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

# ── Lambda asset ─────────────────────────────────────────────────────────────

BUILD_DIR   := build/gateway
GATEWAY_PKG := platform/gateway/agentpave_gateway

.PHONY: build-gateway
build-gateway: ## Vendor the gateway's runtime deps into build/ — no Docker
	@# CDK's PythonFunction bundles with Docker at synth time, which would put
	@# a Docker daemon on the critical path of `make check`. Instead the asset
	@# is built here and `cdk synth` points at plain source when it isn't.
	@# boto3 is deliberately absent — the Lambda runtime provides it.
	rm -rf $(BUILD_DIR)
	mkdir -p $(BUILD_DIR)
	uv pip install --quiet --target $(BUILD_DIR) \
		--python-platform aarch64-manylinux2014 --python-version 3.12 \
		--only-binary=:all: pydantic pyyaml
	cp -r $(GATEWAY_PKG) $(BUILD_DIR)/
	@# Host-built .pyc files are the wrong platform and would ship as dead
	@# weight; dropping them also keeps the asset hash stable across machines.
	find $(BUILD_DIR) -name '__pycache__' -type d -prune -exec rm -rf {} +
	@echo "✅ gateway asset built at $(BUILD_DIR)"

# ── Deployed gates (need AWS; cost real money) ───────────────────────────────

# .env is sourced per-target rather than included globally, so `make check`
# genuinely cannot read it (the note at the top of .env stays true).
WITH_ENV := test -f .env || { echo "✋ no .env — run 'make install' and edit it"; exit 1; }; \
            set -a && . ./.env && set +a &&

.PHONY: bootstrap
bootstrap: ## ⚠️ once per account+region — CDK toolkit stack
	@$(WITH_ENV) cdk bootstrap

.PHONY: deploy-dev
deploy-dev: build-gateway ## ⚠️ creates real infrastructure
	@$(WITH_ENV) AGENTPAVE_GATEWAY_ASSET=$(BUILD_DIR) \
		cdk deploy --require-approval broadening

.PHONY: destroy-dev
destroy-dev: ## tear it all down — nothing should bill after this
	@$(WITH_ENV) cdk destroy --force

.PHONY: smoke-gateway
smoke-gateway: ## M01 deployed gate: guarded, metered completion; must-block blocked
	@$(WITH_ENV) uv run python -m agentpave_infra.smoke

.PHONY: conformance
conformance: ## M02 deployed gate: contract suite vs. deployed MCP tool
	$(call not_yet,conformance,M02)

.PHONY: eval
eval: ## M03 deployed gate: golden-set scorecard (pave eval under the hood)
	$(call not_yet,eval,M03)

.PHONY: eval-adversarial
eval-adversarial: ## M03 deployed gate: passes on "blocked or denied+logged", never "the model resisted"
	$(call not_yet,eval-adversarial,M03)

.PHONY: walkthrough
walkthrough: ## M04 deployed gate: Act 1 end to end
	$(call not_yet,walkthrough,M04)

.PHONY: seed-baseline
seed-baseline: ## M05: write the current eval scores as the CI gate baseline
	$(call not_yet,seed-baseline,M05)
