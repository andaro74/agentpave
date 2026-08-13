"""Candidate vs. incumbent on the golden set — the canary stand-in.

ARCHITECTURE.md §3 lists "no canary infrastructure" as a deliberate scope cut
with a named replacement: this. A real platform would ship the candidate to a
slice of live traffic and watch. This one has no live traffic, so it runs the
golden set twice — once as the platform serves today, once as it would serve
under the candidate — and compares the two runs case by case.

Everything above the `# ── wiring ──` line in `runner.py` is pure; so is all of
this. The comparison is the part worth testing, and it must be testable without
spending $0.94 to produce its inputs.

**This is not `baseline.diff`, and the difference is the reason the module
exists.** A score diff compares a run against a bar recorded days ago, and asks
"did we regress?". A shadow report compares two runs made minutes apart against
the same dataset with the same judge, and asks a different question: "should we
ship this?" The second question is stricter. A candidate that lifts the overall
pass rate while breaking a case that used to pass is an improvement by mean and
a regression by user, and `baseline.diff` — which compares aggregates — would
report it as a clean win. `ShadowReport.shippable` says no.

Two axes vary, and neither is a model id:

* **Model**, by routing the serving calls to `SHADOW_CANDIDATE_FEATURE`. The
  gateway decides what that feature runs on; the caller never names a model
  (invariant 1, ADR-036).
* **Prompt**, by substituting the serving system prompt.

The judge is fixed on both arms, deliberately and by force — see
`candidate_caller`.
"""

from __future__ import annotations

from typing import Any

from agentpave_gateway.routing import SHADOW_CANDIDATE_FEATURE
from pydantic import BaseModel, ConfigDict

from .harness import Caller
from .judge import JUDGE_FEATURE
from .models import Scorecard


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseOutcome(_Strict):
    """One case, as both arms answered it."""

    case_id: str
    capability: str
    incumbent_passed: bool
    candidate_passed: bool

    @property
    def moved(self) -> bool:
        return self.incumbent_passed != self.candidate_passed

    @property
    def regressed(self) -> bool:
        return self.incumbent_passed and not self.candidate_passed


class ShadowReport(_Strict):
    """What `pave shadow-eval` prints, and what a human decides from."""

    incumbent_run_id: str
    candidate_run_id: str
    # Both arms' serving models, named. A shadow report whose two models are
    # unknown is a table of numbers with no subject.
    incumbent_model_serve: str
    candidate_model_serve: str
    # Whether the candidate's prompt differed from the incumbent's. The prompt
    # itself is not carried: it can be thousands of characters and this object
    # is printed.
    prompt_changed: bool = False

    pass_rate_delta: float
    cost_delta_usd: float
    by_capability_delta: dict[str, float]
    # How many cases each arm actually got a judge verdict for.
    #
    # These exist to keep `cost_delta_usd` from being read as a saving. A case
    # that fails a deterministic assert is never sent to the judge — the verdict
    # could not change the outcome and Sonnet is not free (`harness.run_case`) —
    # so a failing arm skips judge calls and comes out *cheaper*. The judge is
    # handed the whole capped source plus the answer, which on this dataset's
    # large fixtures is the most expensive single call in a case, and the
    # skipped-judge saving can exceed the candidate's extra serving cost
    # outright.
    #
    # That is what the first comparable deployed run did: six regressions and a
    # cost delta of -$0.010189, which reads as a quality-for-price trade and is
    # nothing of the sort. It is the same fact counted twice. Two counts print
    # instead of a caveat because the confound has a size, and a number the
    # reader can subtract beats a sentence asking them to be careful.
    incumbent_judged: int = 0
    candidate_judged: int = 0

    outcomes: tuple[CaseOutcome, ...]
    # Cases present in one arm and not the other. Never empty for a benign
    # reason: both arms run the same dataset in the same process, so a mismatch
    # means one arm failed to load or failed to run cases, and any pass-rate
    # comparison across a different set of cases is arithmetic on two different
    # questions.
    only_incumbent: tuple[str, ...] = ()
    only_candidate: tuple[str, ...] = ()
    # Set when the two arms were graded by different judges, which invalidates
    # the comparison rather than colouring it.
    judge_changed: bool = False
    # Set when a model change was intended and both arms were served by the same
    # model anyway. The likeliest cause is a gateway deployed before the
    # candidate feature existed: routing defaults open on an unknown feature, so
    # the candidate silently gets the incumbent's model and every case ties.
    #
    # This is the failure the first deployed run actually hit, and it is the
    # worst-shaped one available — the report was not merely wrong, it was
    # *reassuring*. "No case regressed, safe to adopt" is exactly what a
    # comparison of a run against itself produces.
    served_identically: bool = False

    @property
    def regressions(self) -> tuple[str, ...]:
        return tuple(o.case_id for o in self.outcomes if o.regressed)

    @property
    def improvements(self) -> tuple[str, ...]:
        return tuple(o.case_id for o in self.outcomes if o.moved and not o.regressed)

    @property
    def judged_evenly(self) -> bool:
        """Whether both arms were charged for the same amount of judging.

        Deliberately *not* folded into `comparable`. Uneven judging does not
        invalidate the pass rates — it is caused by them — and suppressing the
        deltas would hide the regressions that produced it. It qualifies the
        cost line only, so it is reported next to the cost line only.
        """
        return self.incumbent_judged == self.candidate_judged

    @property
    def comparable(self) -> bool:
        """Whether the two runs can be compared at all.

        Kept separate from `shippable` because the two failures call for
        different responses from whoever reads this: an incomparable pair is a
        broken run to re-run, while a comparable pair with regressions is a
        real answer that happens to be "no".
        """
        return not (
            self.judge_changed
            or self.only_incumbent
            or self.only_candidate
            or self.served_identically
        )

    @property
    def shippable(self) -> bool:
        """Whether the candidate is safe to adopt.

        **No case may go backwards.** Not "the mean improved", not "more cases
        improved than regressed" — a single case that used to pass and now
        fails is a user who used to get an answer and now does not, and
        averaging that away is how a platform ships a regression while its own
        dashboard turns green. A candidate with a regression can still be
        adopted; it just cannot be adopted *silently*, which is what a green
        verdict here would license.
        """
        return self.comparable and not self.regressions and self.pass_rate_delta >= 0


def candidate_caller(
    call: Caller,
    *,
    feature_id: str | None = SHADOW_CANDIDATE_FEATURE,
    system: str | None = None,
) -> Caller:
    """Wrap a `Caller` so serving calls run as the candidate would.

    A wrapper rather than parameters threaded through `harness.run`: the
    harness already decides a feature and a system prompt per case, and every
    way of overriding that from the inside meant a second code path through the
    thing being measured. A shadow run has to exercise the *same* harness the
    graded run does, or it is measuring its own instrumentation.

    **Judge calls pass through untouched, and that is load-bearing.** The
    harness reaches the judge through this same caller. Rewriting its feature
    would move the judge onto the candidate's model, and substituting its system
    prompt would replace the rubric — either way both arms would be scored by
    different graders, and the resulting deltas would measure the judge rather
    than the candidate. That failure is invisible in the output: the numbers
    move, they look like a result, and they mean nothing. So it is a branch with
    a test rather than a note in a docstring.
    """

    candidate_feature = feature_id
    candidate_system = system

    def wrapped(
        *,
        feature_id: str,
        prompt: str,
        system: str | None = None,
        classification: str = "internal",
        max_tokens: int = 512,
        temperature: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if feature_id != JUDGE_FEATURE:
            if candidate_feature is not None:
                feature_id = candidate_feature
            if candidate_system is not None:
                system = candidate_system
        return call(
            feature_id=feature_id,
            prompt=prompt,
            system=system,
            classification=classification,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    return wrapped


def observing_caller(call: Caller, sink: set[str]) -> Caller:
    """Wrap a `Caller` and record which model actually served each request.

    The gateway returns `model_id` on every completion and the harness throws
    it away — `_extract` needs an answer and a cost, not a provenance. So the
    only place the truth is visible is here, at the seam that already holds the
    response body.

    This exists because the first deployed shadow run reported "no case changed
    outcome" and "safe to adopt" while both arms ran on the same model. The
    report named two different models in its header, and that header was built
    from stack outputs and a boolean — configuration describing what *should*
    have happened, printed as though it were a measurement. The routing table
    is deployed code, and a gateway that predates a new feature id routes it to
    the fast model by design (routing rule 2 defaults open). Nothing in the
    output could distinguish that from a genuine tie.

    Judge calls are excluded for the same reason `candidate_caller` leaves them
    alone: the judge is fixed by construction, and folding its model into this
    set would make both arms look like they served on two models each.
    """

    def wrapped(
        *,
        feature_id: str,
        prompt: str,
        system: str | None = None,
        classification: str = "internal",
        max_tokens: int = 512,
        temperature: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        status, body = call(
            feature_id=feature_id,
            prompt=prompt,
            system=system,
            classification=classification,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if feature_id != JUDGE_FEATURE and status == 200:
            served = body.get("model_id")
            if served:
                sink.add(str(served))
        return status, body

    return wrapped


def compare(
    incumbent: Scorecard,
    candidate: Scorecard,
    *,
    prompt_changed: bool = False,
    expect_model_change: bool = False,
) -> ShadowReport:
    """Compare two runs of the same dataset."""
    incumbent_cases = {c.case_id: c for c in incumbent.cases}
    candidate_cases = {c.case_id: c for c in candidate.cases}
    shared = sorted(set(incumbent_cases) & set(candidate_cases))

    outcomes = tuple(
        CaseOutcome(
            case_id=case_id,
            capability=incumbent_cases[case_id].capability,
            incumbent_passed=incumbent_cases[case_id].passed,
            candidate_passed=candidate_cases[case_id].passed,
        )
        for case_id in shared
    )

    before = incumbent.score_by_capability()
    after = candidate.score_by_capability()
    shared_caps = sorted(set(before) & set(after))

    return ShadowReport(
        incumbent_run_id=incumbent.run_id,
        candidate_run_id=candidate.run_id,
        incumbent_model_serve=incumbent.model_serve,
        candidate_model_serve=candidate.model_serve,
        prompt_changed=prompt_changed,
        pass_rate_delta=round(candidate.pass_rate - incumbent.pass_rate, 6),
        cost_delta_usd=round(candidate.total_cost_usd - incumbent.total_cost_usd, 6),
        by_capability_delta={cap: round(after[cap] - before[cap], 6) for cap in shared_caps},
        # A verdict is present exactly when a judge call succeeded: a case that
        # failed a deterministic assert was never sent, and a case whose judge
        # errored records the failure and leaves this None. Both are cases the
        # arm was not billed a judge call for, which is what the count is for.
        incumbent_judged=sum(1 for c in incumbent.cases if c.verdict is not None),
        candidate_judged=sum(1 for c in candidate.cases if c.verdict is not None),
        outcomes=outcomes,
        only_incumbent=tuple(sorted(set(incumbent_cases) - set(candidate_cases))),
        only_candidate=tuple(sorted(set(candidate_cases) - set(incumbent_cases))),
        judge_changed=incumbent.model_judge != candidate.model_judge,
        served_identically=(expect_model_change and incumbent.model_serve == candidate.model_serve),
    )


def render(report: ShadowReport) -> str:
    """The report `pave shadow-eval` prints."""
    arrow = "▲" if report.pass_rate_delta > 0 else "▼" if report.pass_rate_delta < 0 else "="
    lines = [
        "shadow eval — candidate vs. incumbent on the golden set",
        f"  incumbent {report.incumbent_run_id}  serving {report.incumbent_model_serve}",
        f"  candidate {report.candidate_run_id}  serving {report.candidate_model_serve}",
    ]
    if report.prompt_changed:
        lines.append("  the candidate also carries a different serving prompt")
    lines.append("")
    lines.append(f"  {arrow} pass rate {report.pass_rate_delta:+.1%}")
    lines.append(f"    cost {report.cost_delta_usd:+.6f} USD")
    for cap, delta in sorted(report.by_capability_delta.items()):
        lines.append(f"    {cap}: {delta:+.1%}")
    if not report.judged_evenly:
        # Printed next to the cost line it qualifies, and printed as two counts
        # rather than as a warning: the reader can size the confound themselves.
        # Direction is not branched on — an incumbent that skipped judge calls
        # is the same confound pointing the other way.
        lines.append("")
        lines.append(
            f"  ! judge verdicts: incumbent {report.incumbent_judged}, "
            f"candidate {report.candidate_judged}"
        )
        lines.append(
            "    a case that fails a deterministic assert is never judged, so the "
            "arms were not charged for the same work — that cost delta is partly "
            "the failures, not the candidate"
        )
    lines.append("")

    if report.improvements:
        lines.append(f"  improved ({len(report.improvements)}):")
        lines.extend(f"    + {case_id}" for case_id in report.improvements)
    if report.regressions:
        lines.append(f"  REGRESSED ({len(report.regressions)}):")
        lines.extend(f"    - {case_id}" for case_id in report.regressions)
    if not report.improvements and not report.regressions:
        lines.append("  no case changed outcome")
    lines.append("")

    if not report.comparable:
        # Said first and said plainly. An incomparable pair still produces
        # numbers above, and a reader who skips to the verdict must not carry
        # those numbers away as a finding.
        lines.append("✋ these two runs are not comparable, so the deltas above mean nothing")
        if report.served_identically:
            lines.append(
                f"   both arms were served by {report.incumbent_model_serve} — this run "
                "compared the incumbent to itself"
            )
            lines.append(
                "   the routing table is deployed code: a gateway that predates "
                "the candidate feature routes it to the fast model by default. "
                "Run 'make deploy-dev' and try again"
            )
        if report.judge_changed:
            lines.append("   the judge differed between the arms — they were graded by two rubrics")
        for case_id in report.only_incumbent:
            lines.append(f"   {case_id} ran only on the incumbent")
        for case_id in report.only_candidate:
            lines.append(f"   {case_id} ran only on the candidate")
        return "\n".join(lines)

    if report.shippable:
        lines.append("✅ no case regressed — the candidate is safe to adopt")
    else:
        lines.append(
            f"❌ {len(report.regressions)} case(s) regressed — adopt this only deliberately"
        )
    return "\n".join(lines)
