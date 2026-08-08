#!/usr/bin/env python3
"""CDK entrypoint.

Synthesis is environment-agnostic on purpose: `make check` must produce a
template with no AWS account and no network (standing rule 4), so account and
region stay as CloudFormation pseudo-parameters until deploy time.
"""

import os
from pathlib import Path

import aws_cdk as cdk
from agentpave_infra.stacks.gateway_stack import GatewayStack

REPO_ROOT = Path(__file__).resolve().parents[2]

# The asset root must *contain* `agentpave_gateway/` so the handler path
# resolves. Synth defaults to the source tree (no build step, so the hermetic
# gate stays fast); `make deploy-dev` points this at a built directory that
# also carries the runtime dependencies.
DEFAULT_ASSET_PATH = REPO_ROOT / "platform" / "gateway"

STAGE = os.environ.get("AGENTPAVE_STAGE", "dev")

app = cdk.App()

GatewayStack(
    app,
    f"AgentPave-Gateway-{STAGE}",
    asset_path=os.environ.get("AGENTPAVE_GATEWAY_ASSET", str(DEFAULT_ASSET_PATH)),
    model_serve=os.environ.get(
        "AGENTPAVE_MODEL_SERVE", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    ),
    model_judge=os.environ.get("AGENTPAVE_MODEL_JUDGE", "us.anthropic.claude-sonnet-4-6"),
    description="AgentPave LLM Gateway: routing, guardrails, metering",
)

app.synth()
