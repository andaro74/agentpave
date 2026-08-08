"""The guardrail policy lints — this is the "policy file lints" hermetic gate.

Two jobs. First, the committed policy must be valid. Second, each lint rule
must actually reject the thing it claims to reject: a validator with no test
is a comment.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from agentpave_gateway.guardrail import (
    DEFAULT_POLICY_PATH,
    GuardrailPolicy,
    load_policy,
)
from pydantic import ValidationError


def _valid_policy_dict() -> dict[str, Any]:
    return {
        "version": 1,
        "name": "test-guardrail",
        "description": "fixture policy",
        "blocked_input_message": "blocked",
        "blocked_output_message": "blocked",
        "content_filters": [
            {"type": "PROMPT_ATTACK", "input_strength": "HIGH", "output_strength": "NONE"},
            {"type": "HATE", "input_strength": "HIGH", "output_strength": "HIGH"},
        ],
        "pii_entities": [{"type": "EMAIL", "action": "BLOCK"}],
    }


# ── the committed policy ──────────────────────────────────────────────────


def test_committed_policy_is_valid() -> None:
    policy = load_policy()
    assert policy.name == "agentpave-gateway"


def test_committed_policy_guards_against_prompt_attack() -> None:
    # The M03 adversarial gate passes on "guardrail blocked". This is the
    # filter that makes that outcome reachable.
    policy = load_policy()
    assert any(f.type == "PROMPT_ATTACK" for f in policy.content_filters)


def test_committed_policy_does_not_block_person_names() -> None:
    # A catalog agent legitimately handles actor and creator names. Blocking
    # NAME would break the sample use case while looking stricter.
    policy = load_policy()
    blocked = {e.type for e in policy.pii_entities}
    assert "NAME" not in blocked
    assert "ADDRESS" not in blocked


def test_committed_policy_blocks_contact_and_financial_pii() -> None:
    blocked = {e.type for e in load_policy().pii_entities if e.action == "BLOCK"}
    assert {"EMAIL", "PHONE", "CREDIT_DEBIT_CARD_NUMBER", "US_SOCIAL_SECURITY_NUMBER"} <= blocked


def test_committed_policy_refusal_messages_are_attributable() -> None:
    # An adversarial probe "passes" only if the block is traceable to the
    # guardrail rather than to the model deciding to be coy.
    policy = load_policy()
    assert "AgentPave" in policy.blocked_input_message
    assert "AgentPave" in policy.blocked_output_message


def test_policy_file_is_valid_yaml_mapping() -> None:
    raw = yaml.safe_load(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)


# ── the lint rules ────────────────────────────────────────────────────────


def test_prompt_attack_rejects_output_strength() -> None:
    # Bedrock rejects this at CreateGuardrail time. Catching it here turns a
    # failed deploy into a failed unit test.
    policy = _valid_policy_dict()
    policy["content_filters"][0]["output_strength"] = "HIGH"
    with pytest.raises(ValidationError, match="PROMPT_ATTACK requires output_strength NONE"):
        GuardrailPolicy.model_validate(policy)


def test_filter_disabled_on_both_sides_is_rejected() -> None:
    policy = _valid_policy_dict()
    policy["content_filters"][1] = {
        "type": "HATE",
        "input_strength": "NONE",
        "output_strength": "NONE",
    }
    with pytest.raises(ValidationError, match="NONE on both sides"):
        GuardrailPolicy.model_validate(policy)


def test_missing_prompt_attack_filter_is_rejected() -> None:
    policy = _valid_policy_dict()
    policy["content_filters"] = [
        {"type": "HATE", "input_strength": "HIGH", "output_strength": "HIGH"}
    ]
    with pytest.raises(ValidationError, match="must include a PROMPT_ATTACK filter"):
        GuardrailPolicy.model_validate(policy)


def test_duplicate_filter_types_are_rejected() -> None:
    policy = _valid_policy_dict()
    policy["content_filters"].append(
        {"type": "HATE", "input_strength": "LOW", "output_strength": "LOW"}
    )
    with pytest.raises(ValidationError, match="duplicate content filter types"):
        GuardrailPolicy.model_validate(policy)


def test_duplicate_pii_types_are_rejected() -> None:
    policy = _valid_policy_dict()
    policy["pii_entities"].append({"type": "EMAIL", "action": "ANONYMIZE"})
    with pytest.raises(ValidationError, match="duplicate PII entity types"):
        GuardrailPolicy.model_validate(policy)


def test_unknown_filter_type_is_rejected() -> None:
    policy = _valid_policy_dict()
    policy["content_filters"][1]["type"] = "SARCASM"
    with pytest.raises(ValidationError):
        GuardrailPolicy.model_validate(policy)


def test_unknown_top_level_key_is_rejected() -> None:
    # A typo'd or renamed key must not be silently ignored — that would mean a
    # policy that reads stricter than it deploys.
    policy = _valid_policy_dict()
    policy["content_filtres"] = []
    with pytest.raises(ValidationError):
        GuardrailPolicy.model_validate(policy)


def test_load_policy_accepts_an_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(_valid_policy_dict()), encoding="utf-8")
    assert load_policy(path).name == "test-guardrail"
