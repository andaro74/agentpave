#!/usr/bin/env python3
"""CDK entrypoint.

Synthesis is environment-agnostic on purpose: `make check` must produce a
template with no AWS account and no network (standing rule 4), so account and
region stay as CloudFormation pseudo-parameters until deploy time.
"""

import os
from pathlib import Path

import aws_cdk as cdk
from agentpave_infra.stacks.ci_stack import CiStack
from agentpave_infra.stacks.eval_stack import EvalStack
from agentpave_infra.stacks.gateway_stack import GatewayStack
from agentpave_infra.stacks.mcp_tvmaze_stack import McpTvmazeStack
from agentpave_infra.stacks.service_stack import UNWIRED_HOST, ServiceStack

REPO_ROOT = Path(__file__).resolve().parents[2]

# Each asset root must *contain* its handler's package so the handler path
# resolves. Synth defaults to the source tree (no build step, so the hermetic
# gate stays fast); `make deploy-dev` points these at built directories that
# also carry the runtime dependencies (ADR-007).
DEFAULT_GATEWAY_ASSET = REPO_ROOT / "platform" / "gateway"
DEFAULT_MCP_ASSET = REPO_ROOT / "platform" / "mcp-tvmaze"
DEFAULT_SERVICE_ASSET = REPO_ROOT / "services" / "catalog-agent"

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

# The identity GitHub Actions assumes. Not stage-suffixed: an OIDC provider is
# an account-level singleton, and a second stage would fail to deploy on the
# duplicate rather than get its own. The repository is a variable so a fork
# does not inherit trust for someone else's repo by copying this file.
CiStack(
    app,
    "AgentPave-Ci",
    repository=os.environ.get("AGENTPAVE_GITHUB_REPO", "andaro74/agentpave"),
    # GitHub issues the `sub` claim with immutable numeric ids, so the trust
    # policy needs them. Read them with:
    #   gh api repos/<owner>/<name> --jq '{repo_id: .id, owner_id: .owner.id}'
    owner_id=int(os.environ.get("AGENTPAVE_GITHUB_OWNER_ID", "3157440")),
    repository_id=int(os.environ.get("AGENTPAVE_GITHUB_REPO_ID", "1327317546")),
    description="AgentPave CI: the role GitHub Actions assumes, no long-lived keys",
)

# The scaffolded sample service. Its URLs are wired from the other stacks'
# outputs at deploy time rather than imported as cross-stack references: a
# hard reference would make the gateway undeployable while a service that
# depends on it exists, and M04's walkthrough tears services down routinely.
#
# Which means these defaults are load-bearing in one direction only. They exist
# so that `cdk synth` works with no AWS account; they are *not* a deployable
# configuration, and `make deploy-dev` resolves the real URLs from the platform
# stacks' outputs before it deploys this one.
ServiceStack(
    app,
    f"AgentPave-Service-CatalogAgent-{STAGE}",
    asset_path=os.environ.get("AGENTPAVE_SERVICE_ASSET", str(DEFAULT_SERVICE_ASSET)),
    service_name="catalog-agent",
    package="agentpave_catalog_agent",
    gateway_url=os.environ.get("AGENTPAVE_GATEWAY_URL", f"https://{UNWIRED_HOST}/"),
    mcp_url=os.environ.get("AGENTPAVE_MCP_URL", f"https://{UNWIRED_HOST}/mcp"),
    description="AgentPave catalog-agent: the scaffolded sample service",
)

app.synth()
