"""The AgentPave platform CLI.

`pave` is the developer-facing half of the command vocabulary; `make` is the
CI-facing half. The split is deliberate — `make eval` shells out to `pave eval`
rather than reimplementing it, so there is one code path and not two that drift.
"""

__all__ = ["__version__"]

__version__ = "0.0.1"
