"""Judge calibration: how often the judge agrees with a person.

The judge scores the hand-labeled answers in `cases/calibration.yaml`; this
module compares its pass/fail to the human's and reports the agreement rate.

Agreement is computed on the *derived* pass/fail, not on the raw 1-5 scores.
A human labeling an answer does not produce a groundedness score, and inventing
one to compare against would be measuring agreement with a number nobody wrote.
Pass/fail is the decision the gate actually makes, so it is the decision worth
calibrating.

The floor is set here rather than left to judgement. A judge below it is not a
judge — its scores are noise, and a gate built on noise is worse than no gate,
because it looks like coverage.
"""

from __future__ import annotations

from collections.abc import Callable

from .judge import verdict_passes
from .models import CalibrationReport, CalibrationSample, JudgeVerdict

# 8 of 10. Below this the judge disagrees with a person often enough that a
# single case's verdict cannot be relied on, and `make eval`'s pass/fail stops
# meaning anything.
MIN_AGREEMENT = 0.8


def calibrate(
    samples: tuple[CalibrationSample, ...],
    score: Callable[[CalibrationSample], JudgeVerdict],
) -> CalibrationReport:
    """Score every labeled sample and report agreement.

    `score` is injected so the hermetic gate can calibrate against recorded
    verdicts with no AWS account, and `make eval` can pass the live judge. The
    comparison logic is identical either way — which is the point, since a
    calibration that only ran deployed would never be regression-tested.
    """
    agreements = 0
    disagreements: list[tuple[str, bool, bool]] = []

    for sample in samples:
        judge_pass = verdict_passes(score(sample))
        if judge_pass == sample.human_pass:
            agreements += 1
        else:
            disagreements.append((sample.case_id, judge_pass, sample.human_pass))

    return CalibrationReport(
        samples=len(samples),
        agreements=agreements,
        disagreements=tuple(disagreements),
    )


def render(report: CalibrationReport, minimum: float = MIN_AGREEMENT) -> str:
    """The published agreement figure, as printed by `make eval`."""
    lines = [
        f"judge calibration: {report.agreements}/{report.samples} "
        f"({report.agreement_rate:.0%} agreement, floor {minimum:.0%})"
    ]
    for case_id, judge_pass, human_pass in report.disagreements:
        lines.append(
            f"  disagreed on {case_id}: "
            f"judge={'pass' if judge_pass else 'fail'}, "
            f"human={'pass' if human_pass else 'fail'}"
        )
    return "\n".join(lines)


def meets_floor(report: CalibrationReport, minimum: float = MIN_AGREEMENT) -> bool:
    """Whether the judge is fit to grade this run.

    Called before the scorecard is trusted. A run whose judge failed
    calibration fails as a whole rather than reporting scores it cannot
    stand behind — fail closed.
    """
    return report.samples > 0 and report.agreement_rate >= minimum
