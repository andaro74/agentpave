#!/usr/bin/env python3
"""CDK entrypoint.

Synthesis is environment-agnostic on purpose: `make check` must produce a
template with no AWS account and no network (standing rule 4), so account and
region stay as CloudFormation pseudo-parameters until deploy time.
"""

import os
from pathlib import Path

import aws_cdk as cdk
from agentpave_infra.stacks.eval_stack import EvalStack
from agentpave_infra.stacks.gateway_stack import GatewayStack
from agentpave_infra.stacks.mcp_tvmaze_stack import McpTvmazeStack

REPO_ROOT = Path(__file__).resolve().parents[2]

# Each asset root must *contain* its handler's package so the handler path
# resolves. Synth defaults to the source tree (no build step, so the hermetic
# gate stays fast); `make deploy-dev` points these at built directories that
# also carry the runtime dependencies (ADR-007).
DEFAULT_GATEWAY_ASSET = REPO_ROOT / "platform" / "gateway"
DEFAULT_MCP_ASSET = REPO_ROOT / "platform" / "mcp-tvmaze"

STAGE = os.environ.get("AGENTPAVE_STAGE", "dev")

app = cdk.App()

GatewayStack(
    app,
    f"AgentPave-Gateway-{STAGE}",
    asset_path=os.environ.get("AGENTPAVE_GATEWAY_ASSET", str(DEFAULT_GATEWAY_ASSET)),
    model_serve=os.environ.get(
        "AGENTPAVE_MODEL_SERVE", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    ),
    model_judge=os.environ.get("AGENTPAVE_MODEL_JUDGE", "us.anthropic.claude-sonnet-4-6"),
    description="AgentPave LLM Gateway: routing, guardrails, metering",
)

McpTvmazeStack(
    app,
    f"AgentPave-Mcp-{STAGE}",
    asset_path=os.environ.get("AGENTPAVE_MCP_ASSET", str(DEFAULT_MCP_ASSET)),
    description="AgentPave tvmaze-catalog MCP server: registry-governed tools",
)

EvalStack(
    app,
    f"AgentPave-Eval-{STAGE}",
    description="AgentPave eval service: baseline score store",
)

app.synth()
