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
	@echo "✅ make check passed (hermetic)"

.PHONY: diagrams
diagrams: ## Render docs/diagrams/*.mermaid to SVG
	$(call not_yet,diagrams,M07)

# ── Deployed gates (need AWS; cost real money) ───────────────────────────────

.PHONY: bootstrap
bootstrap: ## ⚠️ once per account+region — CDK toolkit stack
	$(call not_yet,bootstrap,M01)

.PHONY: deploy-dev
deploy-dev: ## ⚠️ creates real infrastructure
	$(call not_yet,deploy-dev,M01)

.PHONY: destroy-dev
destroy-dev: ## tear it all down — nothing should bill after this
	$(call not_yet,destroy-dev,M01)

.PHONY: smoke-gateway
smoke-gateway: ## M01 deployed gate: guarded, metered completion via curl
	$(call not_yet,smoke-gateway,M01)

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
