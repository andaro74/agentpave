"""The policy-to-CloudFormation transform, tested without a synth."""

from agentpave_gateway.guardrail import GuardrailPolicy
from agentpave_infra.guardrail_render import (
    content_policy_config,
    sensitive_information_policy_config,
)

BASE = {
    "version": 1,
    "name": "test-guardrail",
    "description": "fixture policy",
    "blocked_input_message": "blocked",
    "blocked_output_message": "blocked",
    "content_filters": [
        {"type": "PROMPT_ATTACK", "input_strength": "HIGH", "output_strength": "NONE"}
    ],
}


def _policy(**overrides: object) -> GuardrailPolicy:
    return GuardrailPolicy.model_validate(BASE | overrides)


def test_content_filters_render_to_bedrock_key_names() -> None:
    rendered = content_policy_config(_policy())
    assert rendered == {
        "filtersConfig": [
            {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"}
        ]
    }


def test_every_authored_filter_is_rendered() -> None:
    policy = _policy(
        content_filters=[
            {"type": "PROMPT_ATTACK", "input_strength": "HIGH", "output_strength": "NONE"},
            {"type": "HATE", "input_strength": "HIGH", "output_strength": "HIGH"},
            {"type": "VIOLENCE", "input_strength": "LOW", "output_strength": "MEDIUM"},
        ]
    )
    rendered = content_policy_config(policy)
    assert len(rendered["filtersConfig"]) == 3
    assert {f["type"] for f in rendered["filtersConfig"]} == {
        "PROMPT_ATTACK",
        "HATE",
        "VIOLENCE",
    }


def test_pii_entities_render_to_bedrock_key_names() -> None:
    policy = _policy(pii_entities=[{"type": "EMAIL", "action": "BLOCK"}])
    assert sensitive_information_policy_config(policy) == {
        "piiEntitiesConfig": [{"type": "EMAIL", "action": "BLOCK"}]
    }


def test_empty_pii_list_omits_the_block_entirely() -> None:
    # Bedrock rejects an empty piiEntitiesConfig; sending the block hollow
    # would fail the deploy rather than this test.
    assert sensitive_information_policy_config(_policy()) is None
