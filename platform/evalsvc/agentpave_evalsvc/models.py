"""Data models crossing the eval service's boundaries.

Strict and frozen throughout, for the same reason the gateway's are: a case
file with a typo'd key is a broken case, and a loader that silently ignores it
produces a suite that grades less than it claims to. `extra="forbid"` turns
that into a load-time failure — which is exactly what the hermetic gate's
"dataset schema validates" check is for.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The four capped capabilities from ARCHITECTURE.md §2. Capping the dataset's
# vocabulary here means a case cannot quietly test a fifth capability the
# platform never promised — the cap is enforced, not just documented.
Capability = Literal["airing", "summarize", "running", "enrichment"]

# How a case is graded. `deterministic` cases are graded by code alone;
# `judged` cases additionally go to the LLM judge. Enrichment is deterministic
# because it returns a schema — see ARCHITECTURE.md §2, "structured output
# makes deterministic asserts easy".
Grading = Literal["deterministic", "judged"]

# What the judge scores. Three axes, per ROADMAP.md M03.
JudgeAxis = Literal["groundedness", "completeness", "tone"]

# Why an adversarial probe passed. The suite accepts exactly two outcomes and
# rejects a third that looks like success but is not — see `adversarial.py`.
AdversarialOutcome = Literal["guardrail_blocked", "policy_denied", "model_complied"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Budget(_Strict):
    """Per-case ceilings for the deterministic asserts.

    Both are upper bounds, checked after the fact. They are per-case rather
    than global because enrichment legitimately costs more than a one-line
    airing answer, and a single global ceiling would either be too loose to
    catch a regression or too tight to let enrichment pass.
    """

    latency_ms: int = Field(gt=0, le=60_000)
    cost_usd: float = Field(gt=0.0, le=1.0)


class GoldenCase(_Strict):
    """One graded case in the golden dataset."""

    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    capability: Capability
    grading: Grading
    # The user-visible question. This is what reaches the model.
    prompt: str = Field(min_length=1)
    # The recorded TVMaze fixture whose contents ground the answer. The harness
    # feeds this to the model as the tool result, standing in for the agent
    # that arrives in M04.
    fixture: str = Field(min_length=1)
    budget: Budget
    # Substrings that must appear in the answer, checked case-insensitively.
    # Deliberately not a regex: a golden dataset that needs regexes to express
    # its expectations is testing phrasing, not grounding.
    must_contain: tuple[str, ...] = ()
    # Substrings that must NOT appear. The main use is hallucination bait —
    # a fact absent from the fixture that a model might supply from memory.
    must_not_contain: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _enrichment_is_deterministic(self) -> "GoldenCase":
        # Enrichment's whole reason for existing in this dataset is that a
        # schema can be asserted rather than judged. A judged enrichment case
        # would be paying Sonnet to re-check what `json.loads` already proved.
        if self.capability == "enrichment" and self.grading != "deterministic":
            raise ValueError(
                f"case '{self.case_id}': enrichment is graded deterministically "
                "by schema, never by judge"
            )
        return self


class AdversarialProbe(_Strict):
    """One adversarial probe.

    A probe passes when the *platform* stopped it. Whether the model would
    have resisted on its own is not the question the gate asks — see
    ARCHITECTURE.md invariant 5.
    """

    probe_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    why: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    # When set, the attack text is embedded in the tool response rather than
    # the user prompt — the injection-via-fixture case ROADMAP.md calls for.
    # The named fixture is loaded and the marker substituted at run time; no
    # attack string is ever written into a committed fixture file.
    inject_into_fixture: str | None = None
    # `internal` for guardrail probes, `sensitive` for the classification
    # refusal path. Both are platform controls; neither is model behaviour.
    classification: Literal["internal", "sensitive"] = "internal"


class CalibrationSample(_Strict):
    """One hand-labeled answer, used to measure the judge against a human.

    Calibration needs labeled *answers*, not labeled cases: the question is
    whether the judge agrees with a person about a specific piece of text, and
    the text a live run produces is not known in advance. So these are answers
    captured from real runs and then labeled by hand — which is also what lets
    the calibration run hermetically, since the answers are recorded.
    """

    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    answer: str = Field(min_length=1)
    # The human's verdict: should this answer pass?
    human_pass: bool
    # Why a person decided that. Read by whoever re-labels later; a sample
    # whose label nobody can justify is worse than no sample.
    note: str = Field(min_length=1)


class Dataset(_Strict):
    """A loaded, validated dataset."""

    golden: tuple[GoldenCase, ...]
    adversarial: tuple[AdversarialProbe, ...]
    calibration: tuple[CalibrationSample, ...] = ()


class JudgeVerdict(_Strict):
    """The judge's scores for one answer.

    Scores are 1-5 per axis. `passed` is derived, not judged: the judge rates,
    the harness decides. Keeping the threshold out of the prompt means moving
    the bar is a code change with a test, not a prompt edit nobody reviews.
    """

    groundedness: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    tone: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1, max_length=2000)


class CaseResult(_Strict):
    """What one golden case produced."""

    case_id: str
    capability: Capability
    passed: bool
    # Every deterministic assert that ran, and what it decided.
    assert_failures: tuple[str, ...] = ()
    verdict: JudgeVerdict | None = None
    latency_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    # Set when the case could not be evaluated at all (transport failure,
    # refusal, malformed response). A case that could not be graded fails —
    # standing rule 5, and the M02 defect where transport failure was folded
    # into a passing result.
    error: str | None = None


class ProbeResult(_Strict):
    """What one adversarial probe produced."""

    probe_id: str
    outcome: AdversarialOutcome
    passed: bool
    detail: str


class Scorecard(_Strict):
    """One eval run, in full."""

    run_id: str = Field(min_length=1)
    # ISO-8601. Passed in rather than read from the clock so the scorecard is
    # a pure function of its inputs and the renderer is testable.
    created_at: str = Field(min_length=1)
    model_serve: str
    model_judge: str
    cases: tuple[CaseResult, ...]
    probes: tuple[ProbeResult, ...] = ()

    @property
    def pass_rate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.passed) / len(self.cases)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.cases), 6)

    @property
    def passed(self) -> bool:
        """A run passes only if every case and every probe passed.

        No partial credit. The scorecard reports a pass rate for humans to
        read; the gate reads this.
        """
        return all(c.passed for c in self.cases) and all(p.passed for p in self.probes)

    def score_by_capability(self) -> dict[str, float]:
        """Pass rate per capability, for the score diff to compare."""
        buckets: dict[str, list[bool]] = {}
        for case in self.cases:
            buckets.setdefault(case.capability, []).append(case.passed)
        return {cap: sum(v) / len(v) for cap, v in sorted(buckets.items())}


class CalibrationReport(_Strict):
    """Judge-vs-human agreement on the hand-labeled samples.

    Published rather than merely asserted: ROADMAP.md M03 asks for the
    agreement figure to be reported, on the principle that a judge whose
    agreement is unknown is a judge whose scores mean nothing.
    """

    samples: int = Field(ge=0)
    agreements: int = Field(ge=0)
    # Where judge and human parted, as (case_id, judge_pass, human_pass).
    disagreements: tuple[tuple[str, bool, bool], ...] = ()

    @property
    def agreement_rate(self) -> float:
        if not self.samples:
            return 0.0
        return self.agreements / self.samples


class Baseline(_Strict):
    """A previously recorded scorecard summary, as stored.

    Only the numbers a diff needs are kept. Storing whole scorecards would
    make the table the system of record for run detail, which it is not —
    CloudWatch and the printed scorecard already hold that.
    """

    run_id: str
    created_at: str
    pass_rate: float = Field(ge=0.0, le=1.0)
    total_cost_usd: float = Field(ge=0.0)
    by_capability: dict[str, float]

    @classmethod
    def from_scorecard(cls, card: Scorecard) -> "Baseline":
        return cls(
            run_id=card.run_id,
            created_at=card.created_at,
            pass_rate=card.pass_rate,
            total_cost_usd=card.total_cost_usd,
            by_capability=card.score_by_capability(),
        )


class ScoreDiff(_Strict):
    """The comparison `pave eval --diff` prints."""

    baseline_run_id: str
    pass_rate_delta: float
    cost_delta_usd: float
    # capability -> delta. A capability present in one run and not the other
    # appears with the missing side treated as absent, not as zero — see
    # `baseline.diff` for why that distinction matters.
    by_capability_delta: dict[str, float]
    appeared: tuple[str, ...] = ()
    disappeared: tuple[str, ...] = ()

    @property
    def regressed(self) -> bool:
        """Any drop in overall pass rate, or any capability going backwards.

        A capability that disappeared counts as a regression: the most likely
        cause is cases failing to load, and a diff that reported that as "no
        change" would be the M02 false-pass defect in a new costume.
        """
        return (
            self.pass_rate_delta < 0
            or any(d < 0 for d in self.by_capability_delta.values())
            or bool(self.disappeared)
        )
