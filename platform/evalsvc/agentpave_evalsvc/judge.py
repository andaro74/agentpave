"""LLM-as-judge: prompts, verdict parsing, and the pass threshold.

The judge reaches Bedrock the same way every other component does — through
the gateway, as feature `judge` (ARCHITECTURE.md invariant 1). The eval
service holds no `bedrock:*` permission of its own, which is asserted in
`cdk synth` rather than trusted.

Three deliberate splits:

* The **prompt** rates; the **threshold** decides. `PASS_THRESHOLD` lives here
  in code with a test on it, not in the prompt text. Moving the bar is then a
  reviewed change rather than a prompt edit nobody diffs.
* The judge sees the **fixture**, not just the question. Groundedness is
  unanswerable otherwise: a judge without the source can only rate whether an
  answer sounds right, which is precisely the failure mode being guarded.
* Parsing is **strict**. A judge response that cannot be parsed is an error,
  never a default score. Standing rule 5, and the M02 defect where an
  unreadable result was read as success.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .models import GoldenCase, JudgeVerdict

# The feature id the gateway routes on. It must appear in the gateway's
# CAPABLE_FEATURES or judging silently runs on the serving model — a judge no
# more capable than the thing it grades. `test_evalsvc_judge.py` pins this.
JUDGE_FEATURE = "judge"

# 4 of 5 on every axis. Set high because the axes are not independent in
# practice — an answer that invents a fact tends to score badly on
# groundedness and fine on tone, and a mean would let tone buy the pass.
PASS_THRESHOLD = 4

JUDGE_SYSTEM = """\
You are grading one answer produced by a TV-catalogue assistant.

You are given the SOURCE the assistant was working from, the QUESTION it was
asked, and its ANSWER. Grade only what is in front of you.

Score three axes from 1 to 5:

- groundedness: is every factual claim in the ANSWER supported by the SOURCE?
  An answer that is correct in the world but unsupported by the SOURCE scores
  1-2: unsupported is unsupported, however plausible. An answer that correctly
  reports the SOURCE has no information scores 5.
- completeness: does the ANSWER address what the QUESTION actually asked?
  A true statement that answers a different question scores low.
- tone: is it direct, plain, and free of padding, hedging, and invented
  enthusiasm? Length alone is not tone; a long answer that needed to be long
  scores well.

Reply with a single JSON object and nothing else:

{"groundedness": <1-5>, "completeness": <1-5>, "tone": <1-5>, "rationale": "<one or two sentences>"}
"""

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class JudgeError(Exception):
    """The judge's reply could not be turned into a verdict."""


def build_judge_prompt(case: GoldenCase, source: str, answer: str) -> str:
    """Assemble the judge turn.

    The source is truncated because a 147-entry schedule fixture is mostly
    repetition, and a judge prompt that spends its context on the 140th cable
    news listing grades no better for it. The cap is generous enough that
    every fact the golden cases assert on survives it.
    """
    return (
        f"{JUDGE_SYSTEM}\n"
        f"SOURCE:\n{source[:12000]}\n\n"
        f"QUESTION:\n{case.prompt}\n\n"
        f"ANSWER:\n{answer}\n"
    )


def parse_verdict(reply: str) -> JudgeVerdict:
    """Turn the judge's reply into a verdict, or raise."""
    match = _FENCE.match(reply)
    text = match.group(1) if match else reply.strip()

    # Judges sometimes prefix a sentence before the object despite the
    # instruction. Recovering the outermost braces is worth it; guessing at
    # scores when there are none is not.
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise JudgeError(f"no JSON object in judge reply: {reply[:200]!r}")
        text = text[start : end + 1]

    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judge reply is not valid JSON ({exc.msg}): {reply[:200]!r}") from exc

    try:
        return JudgeVerdict.model_validate(payload)
    except ValidationError as exc:
        raise JudgeError(f"judge reply does not match the verdict schema:\n{exc}") from exc


def verdict_passes(verdict: JudgeVerdict, threshold: int = PASS_THRESHOLD) -> bool:
    """Every axis must clear the bar.

    Deliberately not a mean. Averaging lets a 5 for tone rescue a 2 for
    groundedness, which inverts what this gate is for.
    """
    return (
        verdict.groundedness >= threshold
        and verdict.completeness >= threshold
        and verdict.tone >= threshold
    )


def lint_prompt(prompt: str = JUDGE_SYSTEM) -> list[str]:
    """Static checks on the judge prompt — the hermetic gate's "judge prompts lint".

    Not style policing. Each rule below corresponds to a way the prompt could
    change such that the suite keeps running and quietly stops grading:
    dropping an axis, dropping the JSON contract, or letting the threshold
    drift into the prompt where `verdict_passes` no longer governs it.
    """
    problems = []

    for axis in ("groundedness", "completeness", "tone"):
        if axis not in prompt:
            problems.append(f"judge prompt no longer mentions the {axis!r} axis")

    if "JSON" not in prompt:
        problems.append("judge prompt no longer demands a JSON reply")

    if "rationale" not in prompt:
        problems.append("judge prompt no longer asks for a rationale")

    if "1 to 5" not in prompt:
        problems.append("judge prompt no longer states the 1-5 scale")

    # The prompt must not encode the pass bar. If it does, two thresholds
    # exist and only one of them has a test.
    if re.search(r"\b(pass|fail)\b", prompt, re.IGNORECASE):
        problems.append(
            "judge prompt decides pass/fail; the threshold belongs in "
            "verdict_passes(), where it is tested"
        )

    return problems
