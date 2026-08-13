"""Log group names, defined in one place.

The dashboard's Logs Insights widgets need log group **names at synth time**,
and there are only three ways to give them one:

1. A CDK cross-stack reference (`Fn::ImportValue`), which pins the gateway
   alive for as long as the dashboard exists — the exact coupling `app.py`
   refuses for services, and for the same reason.
2. Deploy-time wiring: publish the auto-generated name as an output and pass it
   in as an environment variable, the way the gateway URL reaches a service.
   That is how everything else here is wired, and it is wrong for this one:
   forgetting the variable is **silent**. The dashboard synthesises, passes
   every assertion, deploys clean, and shows four empty widgets. Nothing turns
   red. The same silence cost M04 two walkthroughs (see the notes in the
   Makefile's `deploy-dev`).
3. Deterministic names, here. A wrong name is caught by
   `tests/test_dashboard_stack.py`, which synthesises the producer stacks and
   the dashboard together and asserts the queried groups are the groups that
   actually get created (ADR-031).

The third. It is the only option where a mistake fails in `make check` rather
than as an empty panel nobody can distinguish from a quiet week.

The cost is real and paid once: naming a log group that already exists under a
CDK-generated name replaces it, so the rows M05 verified by hand on 2026-08-12
are gone. The schema they proved is unchanged, and the deployed gate re-runs
`make walkthrough` and `make eval`, which fill both groups again.

The service's log group is deliberately **not** here. Per-service groups are
read by the walkthrough through the stack output it already publishes, and the
dashboard reads tokens and cost from the gateway's line — `service_id` is a
field on it. Renaming the service's group would replace a third deployed group
to no end.
"""

from __future__ import annotations

# One prefix, so every group this platform owns sorts together in the console
# and a single `/agentpave/*` wildcard in an IAM policy covers them.
PREFIX = "/agentpave"


def gateway_log_group(stage: str) -> str:
    """Where the gateway writes one structured line per request (ADR-030)."""
    return f"{PREFIX}/{stage}/gateway"


def eval_log_group(stage: str) -> str:
    """Where `pave eval` writes one scorecard line per run.

    The eval harness runs on a GitHub runner (ADR-012), so its scores reach
    CloudWatch only because it puts them there. Without this group the
    dashboard's eval-trend panel has no source at all.
    """
    return f"{PREFIX}/{stage}/eval"


# The stream inside the eval group. Pre-created by `EvalStack` so the CI role
# needs `logs:PutLogEvents` and nothing else — no `logs:CreateLogStream`, which
# would let a run make its own stream anywhere in the group.
#
# One shared stream is safe: `PutLogEvents` has not required a sequence token
# since 2023, so two runs appending concurrently do not collide.
EVAL_LOG_STREAM = "scorecards"
