<!-- agentpave-eval-gate -->
## ❌ Quality gate — 2 checks failed

**L2 eval** blocked this pull request. Golden set **2/4** (50.0%), against baseline `eval-baseline-1`.

| | baseline | this run | Δ |
|---|---|---|---|
| **pass rate** | 100.0% | 50.0% | **-50.0%** |
| airing | 100.0% | 50.0% | **-50.0%** |
| summarize | 100.0% | 100.0% | — |
| enrichment | 100.0% | 0.0% | **-100.0%** |
| cost | $0.4719 | $0.4677 | -0.0042 |

**What failed**

- **`airing-schedule-abc-overnight`** · airing
  - judge: groundedness 5, completeness 5, tone 3
  > includes unnecessary technical detail (airstamp in UTC) that adds little value
- **`enrichment-severance-null-runtime`** · enrichment
  - must_not_contain: '49' present in the answer

**Adversarial** 1/1 probes blocked or denied · **Models** serving `us.anthropic.claude-haiku-4-5-20251001-v1:0`, judge `us.anthropic.claude-sonnet-4-6` · **Run** `eval-run-2`
