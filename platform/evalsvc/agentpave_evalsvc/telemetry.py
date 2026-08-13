"""One structured line per eval run, for the dashboard's trend to read.

The gateway writes a line because it runs inside AWS and stdout lands in a log
group by itself. The eval harness does not: it runs on a GitHub runner or on a
laptop (ADR-012), so **nothing about running it puts a score in CloudWatch**.
The eval-trend panel exists only because this module writes to a log group on
purpose, into a stream `EvalStack` created and a policy allows exactly one
action on (ADR-030, ADR-027).

Logs rather than a custom metric, for the reason in `gateway/telemetry.py`: six
custom metrics are about $1.80 a month of standing charge on a platform whose
first invariant is that nothing bills while idle (ADR-002).

Two rules the line follows:

* **Summary numbers only.** No answers, no prompts, no per-case detail. The
  scorecard printed to the console and the PR comment already hold those, and a
  log group is the wrong place for text a guardrail might have had opinions
  about. The same rule the gateway's line follows for blocked strings.
* **Flat.** Logs Insights addresses nested fields with dotted paths and they
  work, but every widget query then carries the nesting. Per-capability scores
  are therefore *not* here — they live in the baseline table, which is what
  `--diff` reads, and the trend panel charts one number.

Unlike the gateway's emitter, a failure here is **reported rather than
swallowed**. The gateway suppresses because a telemetry failure must not turn an
answer into a 500. Here there is no caller to protect: the run has already been
graded, so a failed write must not change the verdict either — but a silent
failure would leave the dashboard flat while every gate passed, which is
indistinguishable from a quiet week. So it prints, loudly, and returns.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any

from .models import Scorecard

# The marker every line carries, and the only thing the trend query filters on.
# The group holds nothing else today; it is filtered anyway, because "the only
# thing in this group" is a property of today rather than of the query.
EVENT = "agentpave.eval.scorecard"

# What produced the run, so the trend can tell a nightly from a laptop. A chart
# that mixes a developer's half-broken debugging runs into the same line as the
# nightly is a chart that reports regressions nobody caused — and M03 already
# put one dip in the baseline history that never happened (see `is_recordable`).
LOCAL_ORIGIN = "local"


def origin(env: dict[str, str] | None = None) -> str:
    """Which workflow produced this run, or `local`.

    Named from `GITHUB_WORKFLOW` rather than a boolean, because "was this the
    pull-request gate or the nightly" is the question asked of a suspicious
    point on the trend, and the two workflows are the two answers.
    """
    environ = os.environ if env is None else env
    if environ.get("GITHUB_ACTIONS") != "true":
        return LOCAL_ORIGIN
    return environ.get("GITHUB_WORKFLOW") or "github-actions"


def scorecard_line(card: Scorecard, *, run_origin: str) -> dict[str, Any]:
    """The record, as a dict. Pure, so the shape is testable without AWS."""
    return {
        "event": EVENT,
        "run_id": card.run_id,
        "created_at": card.created_at,
        "origin": run_origin,
        # The gate reads `passed`; the trend charts `pass_rate`. Both are here
        # because they answer different questions: a run at 30/31 has a pass
        # rate of 97% and did not pass, and a trend that only had the boolean
        # could not show how close it came.
        "passed": card.passed,
        "pass_rate": card.pass_rate,
        "cases_passed": sum(1 for c in card.cases if c.passed),
        "cases_total": len(card.cases),
        "probes_passed": sum(1 for p in card.probes if p.passed),
        "probes_total": len(card.probes),
        "total_cost_usd": card.total_cost_usd,
        "model_serve": card.model_serve,
        "model_judge": card.model_judge,
    }


def event_timestamp_ms(created_at: str) -> int:
    """The run's own time, in the milliseconds `PutLogEvents` takes.

    The run's time and not the write's, so `bin(1d)` buckets a run by when it
    ran. They differ by the length of the eval — several minutes — which is
    enough to put a run that started at 23:58 in the wrong day.

    An unparseable timestamp falls back to now rather than raising. A scorecard
    with a malformed `created_at` is a bug worth a red test, not a reason to
    drop the only datapoint the dashboard was going to get.
    """
    try:
        parsed = datetime.fromisoformat(created_at)
    except ValueError:
        return int(time.time() * 1000)
    return int(parsed.timestamp() * 1000)


# ── wiring (touches AWS; runs only under the deployed gates) ──────────────


def emit(line: dict[str, Any], *, log_group: str, log_stream: str) -> bool:
    """Write one line to the scorecard stream. Never raises.

    Returns whether it was written, so the caller can say so on stdout. The
    exit code is not this function's business: the run's verdict was decided by
    the cases and the probes, and a dashboard that missed a point must not turn
    a passing pull request red.

    `default=str` because a cost may arrive as a `Decimal`, which a bare
    `json.dumps` rejects — the same trap `gateway/telemetry.py` documents.
    """
    try:
        import boto3

        boto3.client("logs").put_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            logEvents=[
                {
                    "timestamp": event_timestamp_ms(str(line.get("created_at", ""))),
                    "message": json.dumps(line, default=str),
                }
            ],
        )
        return True
    except Exception as exc:  # noqa: BLE001 — reported, deliberately not raised
        # Named in full. The likely causes are a missing `logs:PutLogEvents`, a
        # stream `EvalStack` did not create, and a stale group name — and all
        # three look identical from the dashboard, which is why the message has
        # to name the group and the stream it tried.
        print(
            f"\n! the scorecard line was not written to CloudWatch: {exc}\n"
            f"  group={log_group} stream={log_stream}\n"
            "  the run's verdict stands; the dashboard's eval trend will be "
            "missing this point",
            file=sys.stderr,
        )
        return False
