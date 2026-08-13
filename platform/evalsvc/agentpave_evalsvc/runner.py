"""The deployed run: wiring only.

Everything in this module touches AWS and runs only under `make eval` /
`make eval-adversarial`. It holds no grading logic — it resolves endpoints,
signs requests, and hands `(status, body)` pairs to the pure harness.

Two lessons from M02's deployed gate are enforced here rather than remembered:

1. **Requests are signed.** The gateway's Function URL is `AWS_IAM`; an
   unsigned request gets a 403 on everything, and a suite that treats 403 as
   "the call failed as expected" reports green against a wall (ADR-010).
2. **A missing endpoint is a hard stop.** If the stack output cannot be
   resolved, this exits rather than running the suite against an empty URL.
   `make conformance` learned that one the expensive way.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentpave_gateway.routing import SHADOW_CANDIDATE_FEATURE

from . import adversarial as adversarial_mod
from . import baseline as baseline_mod
from . import calibration as calibration_mod
from . import pr_comment as pr_comment_mod
from . import scorecard as scorecard_mod
from . import shadow as shadow_mod
from . import telemetry as telemetry_mod
from .harness import EVAL_TEMPERATURE, SERVICE_ID, Caller, capped_source, run
from .judge import JUDGE_FEATURE, JUDGE_SYSTEM, build_judge_content, parse_verdict
from .models import Baseline, CalibrationSample, Dataset, JudgeVerdict


def _stack_outputs(stack_name: str) -> dict[str, str]:
    import boto3

    stacks = boto3.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def _optional_stack_outputs(stack_name: str) -> dict[str, str]:
    """Stack outputs, or an empty mapping if the stack is not there.

    The eval stack is resolved on every run now, because it holds the log group
    the dashboard charts as well as the baseline table. It must not become a
    precondition for grading: `pave eval` with no flags printed a scorecard on a
    deployment with no eval stack before M05, and a `ValidationError` from
    `DescribeStacks` would have quietly made the dashboard's plumbing a
    requirement of the thing it observes.

    The callers that genuinely need the table still fail closed on the absence —
    see the `✋ no baseline table` branches below.
    """
    try:
        return _stack_outputs(stack_name)
    except Exception:  # noqa: BLE001 — a missing stack is a legitimate answer here
        return {}


def _signed_caller(url: str) -> Caller:
    """A `Caller` that POSTs SigV4-signed requests to the gateway."""
    import boto3
    import requests
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    session = boto3.Session()

    def call(
        *,
        feature_id: str,
        prompt: str,
        system: str | None = None,
        classification: str = "internal",
        max_tokens: int = 512,
        temperature: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body: dict[str, Any] = {
            "service_id": SERVICE_ID,
            "feature_id": feature_id,
            "prompt": prompt,
            "classification": classification,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if system:
            body["system"] = system
        payload = json.dumps(body)
        request = AWSRequest(
            method="POST",
            url=url,
            data=payload,
            headers={"content-type": "application/json"},
        )
        SigV4Auth(session.get_credentials(), "lambda", session.region_name).add_auth(request)
        response = requests.post(url, data=payload, headers=dict(request.headers), timeout=120)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"raw": response.text[:500]}

    return call


def _live_scorer(call: Caller, dataset: Dataset):
    """Score a calibration sample with the deployed judge.

    The sample's recorded answer is graded against its own case's fixture, so
    calibration measures the judge on exactly the material it will see during
    a run — not on a paraphrase of it.
    """
    from .dataset import load_fixture

    cases = {c.case_id: c for c in dataset.golden}

    def score(sample: CalibrationSample) -> JudgeVerdict:
        case = cases[sample.case_id]
        status, body = call(
            feature_id=JUDGE_FEATURE,
            prompt=build_judge_content(
                case, capped_source(load_fixture(case.fixture)), sample.answer
            ),
            system=JUDGE_SYSTEM,
            max_tokens=512,
            temperature=EVAL_TEMPERATURE,
        )
        if status != 200 or body.get("refused") is True or "completion" not in body:
            blocked_by = body.get("blocked_by") or ()
            named = f" — filters: {', '.join(str(f) for f in blocked_by)}" if blocked_by else ""
            raise RuntimeError(f"judge unavailable during calibration: {str(body)[:300]}{named}")
        return parse_verdict(body["completion"])

    return score


def run_shadow(
    dataset: Dataset,
    *,
    stack_name: str,
    vary_model: bool = True,
    candidate_system: str | None = None,
) -> int:
    """`pave shadow-eval`: the golden set twice, incumbent then candidate.

    Two arms, one process, one dataset, one judge. Running them minutes apart
    against the same deployment is what makes the comparison mean anything —
    the alternative is comparing today's candidate to a bar recorded on
    Tuesday, which is `pave eval --diff` and answers a different question.

    Adversarial probes are deliberately not run. They grade the platform's
    controls, which are identical on both arms by construction, so running them
    twice would double the bill to compare a thing that cannot differ.
    """
    outputs = _stack_outputs(stack_name)
    url = outputs.get("FunctionUrl")
    if not url:
        print("✋ no deployed gateway URL — run 'make deploy-dev' first")
        return 1

    call = _signed_caller(url)
    incumbent_model = outputs.get("ModelServe", "unknown")
    # The capable model serves the judge and the shadow candidate alike, so the
    # gateway's `ModelJudge` output names it. Read through the routing table
    # rather than guessed: the candidate is a *feature*, and this output is
    # what that feature resolves to (ADR-036).
    candidate_model = outputs.get("ModelJudge", "unknown") if vary_model else incumbent_model

    print("shadow eval runs the golden set twice — roughly double an eval run's cost.\n")

    # Calibration once, before either arm. A judge that failed to agree with a
    # person grades both arms into noise, and two noisy runs still produce a
    # tidy-looking delta (fail closed).
    report = calibration_mod.calibrate(dataset.calibration, _live_scorer(call, dataset))
    print(calibration_mod.render(report))
    if not calibration_mod.meets_floor(report):
        print("\n✋ judge failed calibration — refusing to compare anything it graded")
        return 1
    print()

    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    stamp = int(time.time())

    incumbent = run(
        dataset,
        call,
        run_id=f"shadow-incumbent-{stamp}-{uuid.uuid4().hex[:6]}",
        created_at=created_at,
        model_serve=incumbent_model,
        model_judge=outputs.get("ModelJudge", "unknown"),
        include_adversarial=False,
    )
    print(scorecard_mod.render(incumbent))
    print()

    candidate = run(
        dataset,
        shadow_mod.candidate_caller(
            call,
            feature_id=SHADOW_CANDIDATE_FEATURE if vary_model else None,
            system=candidate_system,
        ),
        run_id=f"shadow-candidate-{stamp}-{uuid.uuid4().hex[:6]}",
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        model_serve=candidate_model,
        model_judge=outputs.get("ModelJudge", "unknown"),
        include_adversarial=False,
    )
    print(scorecard_mod.render(candidate))
    print()

    result = shadow_mod.compare(incumbent, candidate, prompt_changed=candidate_system is not None)
    print(shadow_mod.render(result))

    # No baseline is written and no scorecard line is emitted. A shadow run is a
    # question, not a measurement of the platform: its candidate arm was served
    # by a model the platform does not serve on, and charting that in the eval
    # trend would put a number on the graph that no deployed configuration
    # produced (ADR-030's rule, applied to a run that does not belong there).
    return 0 if result.shippable else 1


def run_deployed(
    dataset: Dataset,
    *,
    stack_name: str,
    eval_stack_name: str,
    show_diff: bool = False,
    save_baseline: bool = False,
    adversarial_only: bool = False,
    pr_comment_path: str | None = None,
) -> int:
    """`pave eval` against the deployed stack. Returns a process exit code."""
    outputs = _stack_outputs(stack_name)
    url = outputs.get("FunctionUrl")
    if not url:
        print("✋ no deployed gateway URL — run 'make deploy-dev' first")
        return 1

    call = _signed_caller(url)
    run_id = f"eval-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    created_at = datetime.now(UTC).isoformat(timespec="seconds")

    if adversarial_only:
        from .harness import run_probe

        results = tuple(run_probe(probe, call) for probe in dataset.adversarial)
        rendered, passed = adversarial_mod.report(results)
        print(rendered)
        # No scorecard line from this path, and that is not an oversight: there
        # is no scorecard. L5 grades the platform's controls and produces no
        # pass rate or cost for the trend to chart. Its refusals are already in
        # the gateway's own lines, which is where the guardrail-interventions
        # panel reads them from.
        return 0 if passed else 1

    # Calibration runs before the scorecard is trusted. A judge that failed to
    # agree with a person is not asked to grade 30 cases — its verdicts would
    # be reported as quality signal while being noise (fail closed).
    report = calibration_mod.calibrate(dataset.calibration, _live_scorer(call, dataset))
    print(calibration_mod.render(report))
    if not calibration_mod.meets_floor(report):
        print("\n✋ judge failed calibration — refusing to grade with it")
        return 1
    print()

    card = run(
        dataset,
        call,
        run_id=run_id,
        created_at=created_at,
        model_serve=outputs.get("ModelServe", "unknown"),
        model_judge=outputs.get("ModelJudge", "unknown"),
    )
    print(scorecard_mod.render(card))
    print()
    rendered, probes_passed = adversarial_mod.report(card.probes)
    print(rendered)

    eval_outputs = _optional_stack_outputs(eval_stack_name)
    table_name = eval_outputs.get("BaselineTableName")

    # The dashboard's only source. Written on every graded run — passing or
    # failing — because a trend that dropped its failures would chart a platform
    # that never regressed (ADR-030).
    log_group = eval_outputs.get("ScorecardLogGroupName")
    log_stream = eval_outputs.get("ScorecardLogStreamName")
    if log_group and log_stream:
        line = telemetry_mod.scorecard_line(card, run_origin=telemetry_mod.origin())
        if telemetry_mod.emit(line, log_group=log_group, log_stream=log_stream):
            print(f"\nwrote the scorecard line to {log_group}")
    else:
        # Loud, because the symptom is a flat chart next to a green gate, and
        # nothing else in the output would hint at the cause.
        print(
            "\n! the eval stack published no scorecard log group — this run is "
            "graded but the dashboard's eval trend will not see it"
        )

    if show_diff:
        if not table_name:
            print("\n✋ no baseline table — is the eval stack deployed?")
            return 1
        previous = baseline_mod.latest_baseline(table_name)
        print()
        if previous is None:
            print("score diff: no baseline recorded yet — nothing to compare against")
        else:
            print(baseline_mod.render(baseline_mod.diff(card, previous)))

    if pr_comment_path:
        if not table_name:
            print("\n✋ no baseline table — is the eval stack deployed?")
            return 1
        # UTF-8 pinned, not left to the platform. The comment carries ✅, ❌,
        # `—` and `Δ`, and on a Windows runner defaulting to cp1252 the write
        # dies partway through — the same UnicodeEncodeError `_force_utf8_output`
        # exists for, and the one `curate.py` hit on its first run. A gate whose
        # comment fails to write is a gate that blocks with no explanation.
        Path(pr_comment_path).write_text(
            pr_comment_mod.render(card, baseline_mod.latest_baseline(table_name)),
            encoding="utf-8",
        )
        print(f"\nwrote pull-request comment to {pr_comment_path}")

    if save_baseline:
        if not table_name:
            print("\n✋ no baseline table — is the eval stack deployed?")
            return 1
        if baseline_mod.is_recordable(card):
            baseline_mod.put_baseline(table_name, Baseline.from_scorecard(card))
            print(f"\nrecorded baseline {run_id}")
        else:
            # Refusing to write is the point: a failing run must not become the
            # standard the next run is measured against (see `is_recordable`).
            print(
                f"\nnot recording a baseline: {run_id} failed, and a failing "
                "run must not become the bar"
            )

    print(f"\n{scorecard_mod.summary_line(card)}")
    return 0 if (card.passed and probes_passed) else 1
