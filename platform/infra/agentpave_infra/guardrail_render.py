"""Render an authored guardrail policy into CfnGuardrail properties.

The gateway component owns the policy *schema*; this module owns its
CloudFormation *shape*. Keeping the split means `agentpave_gateway` never
imports CDK — the Lambda asset stays small, and nothing about the deployment
mechanism leaks into the code that serves requests.

This is a pure transform: policy in, dict out. It is unit-tested directly, so a
mis-rendered property fails without a synth.
"""

from typing import Any

from agentpave_gateway.guardrail import GuardrailPolicy


def content_policy_config(policy: GuardrailPolicy) -> dict[str, Any]:
    return {
        "filtersConfig": [
            {
                "type": f.type,
                "inputStrength": f.input_strength,
                "outputStrength": f.output_strength,
            }
            for f in policy.content_filters
        ]
    }


def sensitive_information_policy_config(policy: GuardrailPolicy) -> dict[str, Any] | None:
    # Bedrock rejects an empty piiEntitiesConfig, so an empty list means the
    # whole block is omitted rather than sent hollow.
    if not policy.pii_entities:
        return None
    return {
        "piiEntitiesConfig": [{"type": e.type, "action": e.action} for e in policy.pii_entities]
    }
