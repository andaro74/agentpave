"""The AgentPave eval service.

Structure mirrors what a quality gate has to be able to answer:

- `models`      — everything crossing a boundary, strict and frozen
- `dataset`     — the golden set and the adversarial probes, loaded and validated
- `asserts`     — deterministic checks (schema, latency, cost); no model involved
- `judge`       — LLM-as-judge prompts and verdict parsing, scored via the gateway
- `calibration` — judge-vs-human agreement on the hand-labeled subset
- `adversarial` — probe scoring, which passes only on blocked-or-denied
- `baseline`    — score history and the diff against it
- `harness`     — the run loop that composes the above into a scorecard

Everything except the `_aws` wiring inside `baseline` is pure: it takes data
and returns data. That is what lets `make check` grade the grader on fixtures
with no AWS account (standing rule 4), and it is the reason M02's lesson —
"a gate whose verdict logic is untested can pass for the wrong reason" — does
not repeat here.
"""

__all__ = ["__version__"]

__version__ = "0.0.1"
