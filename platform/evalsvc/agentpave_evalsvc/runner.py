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
from typing import Any

from . import adversarial as adversarial_mod
from . import baseline as baseline_mod
from . import calibration as calibration_mod
from . import scorecard as scorecard_mod
from .harness import SERVICE_ID, Caller, run
from .judge import JUDGE_FEATURE, build_judge_prompt, parse_verdict
from .models import Baseline, CalibrationSample, Dataset, JudgeVerdict


def _stack_outputs(stack_name: str) -> dict[str, str]:
    import boto3

    stacks = boto3.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


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
        classification: str = "internal",
        max_tokens: int = 512,
    ) -> tuple[int, dict[str, Any]]:
        payload = json.dumps(
            {
                "service_id": SERVICE_ID,
                "feature_id": feature_id,
                "prompt": prompt,
                "classification": classification,
                "max_tokens": max_tokens,
            }
        )
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
            prompt=build_judge_prompt(case, load_fixture(case.fixture), sample.answer),
            max_tokens=512,
        )
        if status != 200 or body.get("refused") is True or "completion" not in body:
            blocked_by = body.get("blocked_by") or ()
            named = f" — filters: {', '.join(str(f) for f in blocked_by)}" if blocked_by else ""
            raise RuntimeError(f"judge unavailable during calibration: {str(body)[:300]}{named}")
        return parse_verdict(body["completion"])

    return score


def run_deployed(
    dataset: Dataset,
    *,
    stack_name: str,
    eval_stack_name: str,
    show_diff: bool = False,
    save_baseline: bool = False,
    adversarial_only: bool = False,
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

    table_name = (
        _stack_outputs(eval_stack_name).get("BaselineTableName")
        if (show_diff or save_baseline)
        else None
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

    if save_baseline:
        if not table_name:
            print("\n✋ no baseline table — is the eval stack deployed?")
            return 1
        baseline_mod.put_baseline(table_name, Baseline.from_scorecard(card))
        print(f"\nrecorded baseline {run_id}")

    print(f"\n{scorecard_mod.summary_line(card)}")
    return 0 if (card.passed and probes_passed) else 1
