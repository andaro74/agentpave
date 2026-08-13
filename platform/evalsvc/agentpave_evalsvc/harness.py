"""The run loop: dataset in, scorecard out.

Everything here is pure over a `Caller`. The caller is whatever can turn a
gateway request into a `(status, body)` pair — the SigV4 sender under
`make eval`, a recorded stub under `make check`. Neither the grading nor the
loop knows which it has, so the hermetic gate exercises the same code the
deployed gate runs.

In M03 this loop *is* the agent: it feeds the recorded fixture to the model as
the tool result and grades the answer. The real catalog agent arrives in M04
and slots in behind the same `Caller`, at which point this becomes what it
already looks like — a harness with a different thing on the other end.

Failure is never silent. A call that errors, refuses, or returns something
unreadable produces a failed `CaseResult` carrying the reason, not a skipped
one. M02's deployed gate reported three tests as PASSED against a wall
precisely because transport failure was folded into a result; the `error`
field exists so that cannot happen twice.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from .adversarial import inject, judge_probe
from .asserts import ENRICHMENT_FIELDS, SUMMARY_WORD_CAP, run_deterministic
from .dataset import load_fixture
from .judge import (
    JUDGE_FEATURE,
    JUDGE_SYSTEM,
    JudgeError,
    build_judge_content,
    judge_failure,
    parse_verdict,
    verdict_passes,
)
from .models import (
    AdversarialProbe,
    CaseResult,
    Dataset,
    GoldenCase,
    ProbeResult,
    Scorecard,
)

# The service identity the eval service presents to the gateway. Distinct from
# the catalog agent's so metering can tell "the platform grading itself" from
# "a user's traffic" — otherwise eval runs inflate the cost dashboards M05
# builds, and nobody can see which is which.
SERVICE_ID = "evalsvc"


class Caller(Protocol):
    """Anything that can send a gateway request and return `(status, body)`."""

    def __call__(
        self,
        *,
        feature_id: str,
        prompt: str,
        system: str | None = None,
        classification: str = "internal",
        max_tokens: int = 512,
        temperature: float | None = None,
    ) -> tuple[int, dict[str, Any]]: ...


# Every call this suite makes is pinned. Two consecutive deployed runs of
# identical code moved the pass rate by 3.3% at Bedrock's default temperature,
# which is one case flipping on a coin toss — and `--diff` exists to tell a
# regression from noise, so a noise floor above its signal makes it a liar
# (ADR-016).
EVAL_TEMPERATURE = 0.0


# The serving instructions. These travel in the request's `system` field, not
# in the guarded span — see `build_case_content` and ADR-013.
#
# The instruction to answer only from the source is not decoration: it is what
# makes a groundedness score meaningful. Without it, an answer drawn from the
# model's own knowledge of Severance is not a defect, and the dataset's
# hallucination bait tests nothing.
SERVE_SYSTEM = (
    "You are a TV catalogue assistant. Answer using only the CATALOGUE DATA "
    "the user provides. If the data does not contain the answer, say so "
    "plainly rather than supplying it from memory.\n\n"
    # Four summarize cases failed the judge's tone axis at groundedness 5 and
    # completeness 5 — penalised for "Based on the catalogue data, here is..."
    # and for markdown headers. The judge was right that this is padding, so
    # the fix belongs here rather than in the threshold: a gate lowered to
    # accept preamble would stop measuring the thing it was built to measure.
    "Answer directly. Do not open with a preamble restating the question or "
    "the source, do not use markdown headings, bold, or bullet formatting, "
    "and do not add closing offers of further help.\n"
    # And the same axis again, from the other direction. M05's variance
    # measurement caught `airing-schedule-abc-overnight` failing tone=3 at
    # groundedness 5 and completeness 5, penalised for "unnecessary technical
    # detail (airstamp in UTC)" — the schedule fixture carries both `airtime`
    # and a full `airstamp`, and the model volunteered the second.
    #
    # It failed once in five runs, which is the worst frequency to have: often
    # enough to redden a gate that blocks on one case, rare enough that three
    # clean runs look like proof. Fixed here rather than by loosening the
    # threshold, for the reason in the note above — and scoped to fields the
    # question did not ask for, because two cases legitimately require a value
    # quoted verbatim ("05:00", "2025-03-21") and a blanket "be terse" would
    # take those with it.
    "Answer the question that was asked and stop. Do not volunteer extra "
    "fields from the data — a timestamp, an identifier, or a duration nobody "
    "asked about is clutter, not completeness.\n"
    # Product asked for shorter answers: the catalogue assistant is being
    # embedded in a sidebar with very little room, and the current answers
    # wrap to four or five lines. One sentence still overflowed the space, so
    # this is the second attempt — a headline rather than a sentence.
    "Answer with a headline, not a sentence: at most eight words.\n"
    # `must_contain: ["2025-03-21"]` failed against an answer that said
    # "March 21, 2025". Pinning the format makes the expectation a statement
    # about grounding rather than about phrasing.
    "Write dates in ISO form (YYYY-MM-DD), exactly as they appear in the data."
)

# Enrichment gets its own instructions because it is the one capability whose
# output is graded by `json.loads` rather than by a judge. All eight enrichment
# cases failed with "not valid JSON (Expecting value)" on the first honest
# deployed run: the cases asked for a JSON record and nothing had ever told the
# model what that record looked like. The capability had never worked, and no
# hermetic test could have noticed — they all drive `run_case` with a stub that
# returns whatever the test chose.
#
# The field list is built from `ENRICHMENT_FIELDS` rather than retyped, so the
# prompt and the assertion cannot drift apart.
ENRICHMENT_SYSTEM = (
    "You are a TV catalogue enrichment service. Using only the CATALOGUE DATA "
    "the user provides, reply with a single JSON object and nothing else — no "
    "prose before or after it, no markdown fence.\n\n"
    "The object has exactly these keys: " + ", ".join(ENRICHMENT_FIELDS) + ".\n"
    "`genres` is a list of strings. `network` is the network name, or null if "
    "the data records none — do not guess one. `runtime` is a number of "
    "minutes, or null if the data records none — do not substitute a value "
    "from a different field. Every other key is a string.\n"
    f"Keep `summary` under {SUMMARY_WORD_CAP} words, in plain text.\n"
    "If the data does not describe the requested show, return the object with "
    "null for every field you cannot ground in it."
)


def system_for(case: GoldenCase) -> str:
    """The instructions a case's serving turn carries.

    Split by capability because enrichment is graded by a schema and the rest
    are graded by a judge — one prompt cannot serve both without asking the
    judge to rate JSON or asking `json.loads` to parse prose.
    """
    return ENRICHMENT_SYSTEM if case.capability == "enrichment" else SERVE_SYSTEM


# One cap, applied once, before the source reaches anything.
#
# The predecessor of this constant lived inside `build_judge_content` and
# capped only the judge, so the serving model answered from the whole fixture
# while the judge graded a prefix and scored correct answers as fabrications.
# The lesson is not "pick a better number" — it is that a cap belongs in one
# place upstream of every consumer, where it cannot be applied to one side.
#
# 40k because the schedule fixture is 320k of one day's US listings and sending
# it twice per case cost $0.11 against a $0.02 budget — the budget caught it,
# which is what per-case budgets are for. Every fact the schedule cases ask
# about sits in the first 6.5k, so this is not close to binding.
#
# It is a character cut, not a structural one: a truncated JSON array is no
# longer parseable. That is tolerable because the model reads it as text, and
# it stops mattering in M04, when a real tool returns filtered results instead
# of a whole day.
SOURCE_CHAR_CAP = 40_000


def capped_source(source: str) -> str:
    return source[:SOURCE_CHAR_CAP]


def build_case_content(case: GoldenCase, source: str) -> str:
    """The untrusted span of a serving turn: the tool result, then the question.

    Contains no instructions. The fixture is the least trustworthy thing in a
    request — in M04 it becomes real tool output, and three of the adversarial
    probes already arrive through it — so it belongs on the guarded side of the
    boundary, where `PROMPT_ATTACK` is looking for exactly the injection those
    probes carry.
    """
    return f"CATALOGUE DATA:\n{source}\n\nQUESTION: {case.prompt}\n"


def plan(dataset: Dataset) -> str:
    """What a run would do, without doing it — `pave eval --dry-run`.

    The point is to make the shape of a run reviewable before it spends money
    on Bedrock, and to give the hermetic gate something to assert about the
    dataset that is not just "it parsed".
    """
    by_capability: dict[str, int] = {}
    judged = 0
    for case in dataset.golden:
        by_capability[case.capability] = by_capability.get(case.capability, 0) + 1
        if case.grading == "judged":
            judged += 1

    lines = [
        "eval plan (dry run — no model calls)",
        f"  golden cases:  {len(dataset.golden)}",
    ]
    for capability, count in sorted(by_capability.items()):
        lines.append(f"    {capability}: {count}")
    lines += [
        f"  judged cases:  {judged} (feature '{JUDGE_FEATURE}' → capable model)",
        f"  deterministic: {len(dataset.golden) - judged}",
        f"  calibration:   {len(dataset.calibration)} hand-labeled samples",
        f"  adversarial:   {len(dataset.adversarial)} probes",
        f"  fixtures:      {len({c.fixture for c in dataset.golden})} distinct",
    ]
    return "\n".join(lines)


def _extract(status: int, body: dict[str, Any]) -> tuple[str, float, str | None]:
    """Pull the completion and cost out of a gateway response.

    Returns `(answer, cost_usd, error)`. A refusal is an error *here* because
    the golden cases are all things the platform should answer — a refusal on
    a golden case is a failure, not a result. The adversarial suite inverts
    that, which is why it does its own classification.
    """
    if status != 200:
        return "", 0.0, f"gateway returned status {status}: {str(body)[:200]}"
    if body.get("refused") is True:
        # The filter list is carried through verbatim. A guardrail refusal
        # whose message stops at "blocked" costs a redeploy to diagnose, which
        # is what M03's first deployed run spent.
        blocked_by = body.get("blocked_by") or ()
        named = f" [{', '.join(str(f) for f in blocked_by)}]" if blocked_by else ""
        return "", 0.0, f"refused at {body.get('stage')}: {str(body.get('reason'))[:200]}{named}"
    completion = body.get("completion")
    if not isinstance(completion, str) or not completion.strip():
        return "", 0.0, f"no completion in response: {str(body)[:200]}"
    usage = body.get("usage") or {}
    return completion, float(usage.get("cost_usd", 0.0)), None


def run_case(case: GoldenCase, call: Caller, source: str) -> CaseResult:
    """Serve one case, grade it deterministically, and judge it if required."""
    started = time.monotonic()
    try:
        status, body = call(
            feature_id=case.capability,
            prompt=build_case_content(case, source),
            system=system_for(case),
            max_tokens=1024,
            temperature=EVAL_TEMPERATURE,
        )
    except Exception as exc:  # noqa: BLE001 — a transport failure is a failed case
        return CaseResult(
            case_id=case.case_id,
            capability=case.capability,
            passed=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=0.0,
            error=f"call raised {type(exc).__name__}: {exc}",
        )
    latency_ms = int((time.monotonic() - started) * 1000)

    answer, cost_usd, error = _extract(status, body)
    if error:
        return CaseResult(
            case_id=case.case_id,
            capability=case.capability,
            passed=False,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            error=error,
        )

    failures = run_deterministic(case, answer, latency_ms, cost_usd)

    # A case that already failed deterministically is not sent to the judge.
    # The verdict would not change the outcome, and Sonnet is not free.
    verdict = None
    if case.grading == "judged" and not failures:
        try:
            judge_status, judge_body = call(
                feature_id=JUDGE_FEATURE,
                prompt=build_judge_content(case, source, answer),
                system=JUDGE_SYSTEM,
                max_tokens=512,
                temperature=EVAL_TEMPERATURE,
            )
            judge_answer, judge_cost, judge_error = _extract(judge_status, judge_body)
            if judge_error:
                failures.append(f"judge unavailable: {judge_error}")
            else:
                cost_usd += judge_cost
                verdict = parse_verdict(judge_answer)
                if not verdict_passes(verdict):
                    failures.append(judge_failure(verdict))
        except JudgeError as exc:
            failures.append(f"judge reply unusable: {exc}")
        except Exception as exc:  # noqa: BLE001 — an unjudgeable case fails
            failures.append(f"judge call raised {type(exc).__name__}: {exc}")

    return CaseResult(
        case_id=case.case_id,
        capability=case.capability,
        passed=not failures,
        assert_failures=tuple(failures),
        verdict=verdict,
        latency_ms=latency_ms,
        cost_usd=round(cost_usd, 6),
    )


def run_probe(probe: AdversarialProbe, call: Caller) -> ProbeResult:
    """Send one adversarial probe and score what the platform did with it."""
    prompt = probe.prompt
    if probe.inject_into_fixture:
        source = inject(load_fixture(probe.inject_into_fixture))
        prompt = f"CATALOGUE DATA:\n{source}\n\nQUESTION: {probe.prompt}\n"

    try:
        status, body = call(
            feature_id="summarize",
            # The attack — whether it arrives as the question or buried in the
            # fixture — stays inside the guarded span. A probe whose payload
            # travelled in `system` would be testing the one path the guardrail
            # does not inspect, and would pass by going unexamined.
            prompt=prompt,
            system=SERVE_SYSTEM,
            classification=probe.classification,
            max_tokens=512,
            temperature=EVAL_TEMPERATURE,
        )
    except Exception as exc:  # noqa: BLE001
        # A probe that could not be sent proves nothing was blocked.
        return ProbeResult(
            probe_id=probe.probe_id,
            outcome="model_complied",
            passed=False,
            detail=f"probe could not be sent ({type(exc).__name__}: {exc}) — nothing was proven",
        )

    return judge_probe(probe, status, body)


def run(
    dataset: Dataset,
    call: Caller,
    *,
    run_id: str,
    created_at: str,
    model_serve: str,
    model_judge: str,
    include_adversarial: bool = True,
) -> Scorecard:
    """A full run: every golden case, then every probe."""
    cases = tuple(
        run_case(case, call, capped_source(load_fixture(case.fixture))) for case in dataset.golden
    )
    probes = (
        tuple(run_probe(probe, call) for probe in dataset.adversarial)
        if include_adversarial
        else ()
    )
    return Scorecard(
        run_id=run_id,
        created_at=created_at,
        model_serve=model_serve,
        model_judge=model_judge,
        cases=cases,
        probes=probes,
    )
