.DEFAULT_GOAL := help
SHELL := /bin/bash

# ── M00 note ─────────────────────────────────────────────────────────────────
# The Makefile is the project's interface. Every verb the ROADMAP references
# exists from M00; verbs whose milestone hasn't arrived fail loudly (rule: a
# verb never silently succeeds before it is real).

define not_yet
	@echo "✋ 'make $(1)' arrives in $(2) — see docs/ROADMAP.md" && exit 1
endef

.PHONY: help
help: ## List every verb and its status
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Hermetic (no AWS account needed) ─────────────────────────────────────────

.PHONY: install
install: ## uv sync across packages + create .env from .env.example if missing
	@command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }
	uv sync
	@test -f .env || { cp .env.example .env && echo "created .env — edit AWS_REGION / AWS_PROFILE before deploying"; }

.PHONY: check
check: ## Hermetic gate: lint + unit/contract tests on fixtures + policy + synth
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest -q || { code=$$?; if [ $$code -eq 5 ]; then echo "(no tests collected yet — M00)"; else exit $$code; fi; }
	$(MAKE) --no-print-directory synth
	@echo "✅ make check passed (hermetic)"

.PHONY: synth
synth: ## Synthesise CloudFormation from the CDK app — no AWS account needed
	@# Credentials are cleared deliberately: synth that needs an account is
	@# synth that will fail in CI. The IAM assertions themselves live in
	@# platform/infra/tests/ and run under pytest above.
	AWS_PROFILE= AWS_REGION= cdk synth --quiet

.PHONY: diagrams
diagrams: ## Render docs/diagrams/*.mermaid to SVG
	$(call not_yet,diagrams,M07)

# ── Lambda assets ────────────────────────────────────────────────────────────

GATEWAY_BUILD := build/gateway
MCP_BUILD     := build/mcp-tvmaze
SERVICE_BUILD := build/catalog-agent

# What the Lambda runtime already provides, and therefore what must never be
# vendored: boto3 and botocore. Everything else a service imports has to be in
# SERVICE_DEPS below, or the function dies at import with a bare "Internal
# Server Error" and no line of our code ever runs.
#
# That is not hypothetical — it is what M04's first deployed walkthrough did.
# The asset was plain source, `requests` was declared in the service's
# pyproject and vendored by nothing, and three of five acts failed on it.
# `test_pave_asset.py` now cross-checks this list against the rendered
# service's imports and its declared dependencies, so the next one fails in
# `make check` instead of after a deploy.
#
# OTEL is here rather than in an optional extra because ADR-024 needs it
# present: a tracer that silently degrades to a no-op in the deployed function
# is telemetry the gate cannot tell from telemetry that works.
SERVICE_DEPS  := requests opentelemetry-api opentelemetry-sdk

# CDK's PythonFunction bundles with Docker at synth time, which would put a
# Docker daemon on the critical path of `make check`. Assets are built here
# instead, and `cdk synth` points at plain source when it isn't deploying
# (ADR-007). boto3 is deliberately never vendored — the runtime provides it.
#
# $(1) build dir · $(2) source package · $(3) pip requirements
define build_asset
	rm -rf $(1)
	mkdir -p $(1)
	uv pip install --quiet --target $(1) \
		--python-platform aarch64-manylinux2014 --python-version 3.12 \
		--only-binary=:all: $(3)
	cp -r $(2) $(1)/
	@# Host-built .pyc files are the wrong platform and would ship as dead
	@# weight; dropping them also keeps the asset hash stable across machines.
	find $(1) -name '__pycache__' -type d -prune -exec rm -rf {} +
	@echo "✅ asset built at $(1)"
endef

.PHONY: build-gateway
build-gateway: ## Vendor the gateway's runtime deps into build/ — no Docker
	$(call build_asset,$(GATEWAY_BUILD),platform/gateway/agentpave_gateway,pydantic pyyaml)

.PHONY: build-mcp
build-mcp: ## Vendor the MCP server's runtime deps into build/ — no Docker
	@# The registry package ships alongside: the server evaluates Cedar
	@# in-process, so tools.yaml and the policies travel with it.
	$(call build_asset,$(MCP_BUILD),platform/mcp-tvmaze/agentpave_mcp_tvmaze,mcp mangum cedarpy pyyaml)
	cp -r platform/registry/agentpave_registry $(MCP_BUILD)/
	find $(MCP_BUILD) -name '__pycache__' -type d -prune -exec rm -rf {} +

.PHONY: build-service
build-service: ## Vendor the scaffolded service's runtime deps into build/ — no Docker
	$(call build_asset,$(SERVICE_BUILD),services/catalog-agent/agentpave_catalog_agent,$(SERVICE_DEPS))

.PHONY: build
build: build-gateway build-mcp build-service ## Build every Lambda asset

# ── Deployed gates (need AWS; cost real money) ───────────────────────────────

# .env is sourced per-target rather than included globally, so `make check`
# genuinely cannot read it (the note at the top of .env stays true).
WITH_ENV := test -f .env || { echo "✋ no .env — run 'make install' and edit it"; exit 1; }; \
            set -a && . ./.env && set +a &&

.PHONY: bootstrap
bootstrap: ## ⚠️ once per account+region — CDK toolkit stack
	@$(WITH_ENV) cdk bootstrap

# Reads one CloudFormation output. $(1) stack suffix · $(2) output key
define stack_output
$$(aws cloudformation describe-stacks --stack-name "AgentPave-$(1)-$$stage" \
	--query 'Stacks[0].Outputs[?OutputKey==`$(2)`].OutputValue' --output text 2>/dev/null)
endef

.PHONY: deploy-dev
deploy-dev: build ## ⚠️ creates real infrastructure
	@# Every asset variable is set here, and forgetting one is silent: the CDK
	@# app falls back to plain source, which synthesises, passes every IAM
	@# assertion, deploys without complaint, and then 502s at import. That is
	@# how M04's first walkthrough failed.
	@#
	@# Two passes, because the service has to be *told* where the platform is.
	@# The gateway and MCP URLs are Function URLs, so they do not exist until
	@# those stacks are deployed, and the service cannot import them as
	@# cross-stack references (see app.py: a hard reference would pin the
	@# gateway alive for as long as any service exists). So: deploy the
	@# platform, read the URLs it published, then deploy the service knowing
	@# them. Pass two is `--all` so a stack added later is still covered; the
	@# platform stacks are unchanged by then and deploy as no-ops.
	@#
	@# Skipping the wiring is silent in exactly the way the asset variables are:
	@# the service deploys clean and then 502s on a tool call to a host that
	@# does not resolve. That is how M04's *second* walkthrough failed.
	@$(WITH_ENV) set -e; \
	stage=$${AGENTPAVE_STAGE:-dev}; \
	export AGENTPAVE_GATEWAY_ASSET=$(GATEWAY_BUILD) AGENTPAVE_MCP_ASSET=$(MCP_BUILD) \
	       AGENTPAVE_SERVICE_ASSET=$(SERVICE_BUILD); \
	echo "── pass 1: the platform ─────────────────────────────────────────"; \
	cdk deploy --require-approval broadening \
		"AgentPave-Gateway-$$stage" "AgentPave-Mcp-$$stage" "AgentPave-Eval-$$stage"; \
	gateway_url=$(call stack_output,Gateway,FunctionUrl); \
	mcp_url=$(call stack_output,Mcp,McpUrl); \
	for pair in "gateway:$$gateway_url" "mcp:$$mcp_url"; do \
		if [ -z "$${pair#*:}" ] || [ "$${pair#*:}" = "None" ]; then \
			echo "✋ the $${pair%%:*} stack deployed but published no URL — refusing to"; \
			echo "   deploy a service that would 502 on every call"; exit 1; \
		fi; \
	done; \
	echo "── pass 2: services, wired to $$gateway_url ─────────"; \
	AGENTPAVE_GATEWAY_URL="$$gateway_url" AGENTPAVE_MCP_URL="$$mcp_url" \
		cdk deploy --all --require-approval broadening

.PHONY: destroy-dev
destroy-dev: ## tear it all down — nothing should bill after this
	@$(WITH_ENV) cdk destroy --all --force

.PHONY: smoke-gateway
smoke-gateway: ## M01 deployed gate: guarded, metered completion; must-block blocked
	@$(WITH_ENV) uv run python -m agentpave_infra.smoke

.PHONY: conformance
conformance: ## M02 deployed gate: the same contract suite, vs. the deployed MCP tool
	@# Setting AGENTPAVE_MCP_URL is what adds the deployed driver to the suite;
	@# without it the very same tests run against fixtures only. That is the
	@# false pass this target has to prevent: an undeployed stack resolves the
	@# URL to empty, the suite runs green in-process, and nothing says the
	@# deployed gate never ran. So the URL is resolved first and checked.
	@$(WITH_ENV) url=$$(aws cloudformation describe-stacks \
		--stack-name AgentPave-Mcp-$${AGENTPAVE_STAGE:-dev} \
		--query 'Stacks[0].Outputs[?OutputKey==`McpUrl`].OutputValue' \
		--output text 2>/dev/null); \
	if [ -z "$$url" ] || [ "$$url" = "None" ]; then \
		echo "✋ no deployed MCP URL — run 'make deploy-dev' first"; exit 1; \
	fi; \
	echo "conformance target: $$url"; \
	AGENTPAVE_MCP_URL="$$url" uv run pytest platform/mcp-tvmaze/tests/test_mcp_contract.py -v

.PHONY: eval
eval: ## M03 deployed gate: golden-set scorecard (pave eval under the hood)
	@# `pave eval` is the one implementation; this target is a thin wrapper, so
	@# CI and a developer's laptop cannot drift onto different code paths.
	@# --diff and --save-baseline together mean the run is compared against the
	@# previous baseline and then becomes the next one, which is what makes the
	@# comparison meaningful on the second run rather than the third.
	@$(WITH_ENV) uv run pave eval --diff --save-baseline

.PHONY: eval-adversarial
eval-adversarial: ## M03 deployed gate: passes on "blocked or denied+logged", never "the model resisted"
	@$(WITH_ENV) uv run pave eval --adversarial-only

.PHONY: walkthrough
walkthrough: ## M04 deployed gate: Act 1 end to end
	@# Act 1 is scaffold → deploy → a traced, metered, guarded answer. The
	@# scaffold half is already graded hermetically by the render gate and the
	@# drift test; what only a deployed run can show is that the rendered
	@# service works — tool through MCP, model through the gateway, refused by
	@# the platform rather than by the model's manners.
	@#
	@# It waits on CloudWatch log delivery, so it takes a minute or so. That is
	@# ingestion lag, not a hang.
	@$(WITH_ENV) uv run python -m agentpave_infra.walkthrough

.PHONY: seed-baseline
seed-baseline: ## M05 deployed: write the current eval scores as the CI gate baseline
	@# A thin wrapper for the same reason `eval` is one: `pave eval` is the
	@# single implementation, so CI and a laptop cannot drift onto different
	@# code paths.
	@#
	@# `--save-baseline` without `--diff`. Seeding is the deliberate act of
	@# setting the bar a pull request will be blocked against, and it is the one
	@# case where there may be nothing to compare to yet — a fresh stack has no
	@# history, and `--diff` there prints an absence rather than a comparison.
	@#
	@# Only a passing run is recorded (`is_recordable`). M03's teeth
	@# demonstration deliberately broke the service, scored 19/30, and wrote it
	@# down as the baseline; the next run then "improved" on a regression and
	@# the diff reported no problem. That row stays in the history — deleting a
	@# measurement to tidy a chart is the instinct this platform exists to
	@# resist — but a failing run can no longer become the bar.
	@$(WITH_ENV) uv run pave eval --save-baseline
