"""Schema and loader for the authored guardrail policy.

Enforcement is Bedrock's: `cdk synth` renders this policy into an
AWS::Bedrock::Guardrail and the gateway passes that guardrail's identifier on
every InvokeModel call. Nothing here runs at request time.

What *does* run here is the lint. The rules below encode Bedrock constraints
that would otherwise only appear as a CreateGuardrail API error partway through
a deploy — a slow, expensive way to learn that `PROMPT_ATTACK` rejects a
non-NONE output strength. `make check` finds it in milliseconds instead.
"""

from collections import Counter
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_POLICY_PATH = Path(__file__).parent / "guardrail_policy.yaml"

FilterType = Literal["SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT", "PROMPT_ATTACK"]
FilterStrength = Literal["NONE", "LOW", "MEDIUM", "HIGH"]
PiiEntityType = Literal[
    "EMAIL",
    "PHONE",
    "NAME",
    "ADDRESS",
    "CREDIT_DEBIT_CARD_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
    "PASSWORD",
    "USERNAME",
]
PiiAction = Literal["BLOCK", "ANONYMIZE"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContentFilter(_Strict):
    type: FilterType
    input_strength: FilterStrength
    output_strength: FilterStrength

    @model_validator(mode="after")
    def _prompt_attack_has_no_output_side(self) -> Self:
        # Bedrock rejects PROMPT_ATTACK with a non-NONE output strength: a
        # prompt attack is by definition something that arrives on the way in.
        if self.type == "PROMPT_ATTACK" and self.output_strength != "NONE":
            raise ValueError(
                "PROMPT_ATTACK requires output_strength NONE; Bedrock rejects any other value"
            )
        return self

    @model_validator(mode="after")
    def _filter_actually_filters(self) -> Self:
        # A filter set to NONE on both sides is a disabled filter wearing the
        # costume of an enabled one. Delete it or turn it on.
        if self.input_strength == "NONE" and self.output_strength == "NONE":
            raise ValueError(
                f"filter {self.type} is NONE on both sides — remove it rather than "
                "leaving a disabled filter in the policy"
            )
        return self


class PiiEntity(_Strict):
    type: PiiEntityType
    action: PiiAction


class GuardrailPolicy(_Strict):
    """The whole authored policy, validated."""

    version: Literal[1]
    name: str = Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    description: str = Field(min_length=1, max_length=200)
    blocked_input_message: str = Field(min_length=1)
    blocked_output_message: str = Field(min_length=1)
    content_filters: list[ContentFilter]
    pii_entities: list[PiiEntity] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_filter_types(self) -> Self:
        duplicates = [t for t, n in Counter(f.type for f in self.content_filters).items() if n > 1]
        if duplicates:
            raise ValueError(f"duplicate content filter types: {sorted(duplicates)}")
        return self

    @model_validator(mode="after")
    def _no_duplicate_pii_types(self) -> Self:
        duplicates = [t for t, n in Counter(e.type for e in self.pii_entities).items() if n > 1]
        if duplicates:
            raise ValueError(f"duplicate PII entity types: {sorted(duplicates)}")
        return self

    @model_validator(mode="after")
    def _guards_against_prompt_attack(self) -> Self:
        # Standing rule 5, applied to the policy itself. The M03 adversarial
        # suite passes only on "guardrail blocked"; a policy with no
        # PROMPT_ATTACK filter cannot honestly produce that outcome, so a
        # policy missing it fails the hermetic gate rather than the demo.
        if not any(f.type == "PROMPT_ATTACK" for f in self.content_filters):
            raise ValueError(
                "policy must include a PROMPT_ATTACK filter — the adversarial gate depends on it"
            )
        return self


def load_policy(path: Path | None = None) -> GuardrailPolicy:
    """Parse and validate the authored policy. Raises on anything malformed."""
    source = path or DEFAULT_POLICY_PATH
    return GuardrailPolicy.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))
