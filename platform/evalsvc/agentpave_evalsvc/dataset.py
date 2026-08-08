"""Loading and validating the dataset.

The loader is strict on purpose, and its strictness is the hermetic gate's
"dataset schema validates" check. Three failures it refuses to tolerate:

1. An unknown key in a case (pydantic `extra="forbid"`). A typo'd `must_contian`
   would otherwise silently drop an expectation and leave a case that passes
   while asserting nothing.
2. A duplicate `case_id`. Two cases with one id means the scorecard reports a
   count that does not match what ran, and the baseline diff compares rows that
   are not the same row.
3. A `fixture` that does not exist on disk. A case pointing at a missing
   fixture cannot be grounded in anything, so it is a broken case, not a
   skippable one — standing rule 5.

Fixtures are resolved against the MCP server's recorded set rather than a copy
kept here. One recorded response, shared by the contract suite and the eval
suite, is the whole point: if the two drift apart, the eval stops measuring the
tool the platform actually ships.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import AdversarialProbe, CalibrationSample, Dataset, GoldenCase

# The case files live in `cases/`, not `dataset/`: a `dataset` directory
# beside `dataset.py` inside the same package is an import ambiguity waiting
# to happen, and the one that resolves is not the one you meant.
DATASET_DIR = Path(__file__).parent / "cases"

# The MCP server owns the recorded responses; the eval service borrows them.
# parents[2] is `platform/` — this file sits at
# platform/evalsvc/agentpave_evalsvc/dataset.py.
FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "mcp-tvmaze" / "agentpave_mcp_tvmaze" / "fixtures"
)


class DatasetError(Exception):
    """A dataset that cannot be trusted to grade anything."""


def _read_yaml(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise DatasetError(f"dataset file missing: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise DatasetError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict) or key not in loaded:
        raise DatasetError(f"{path.name} must be a mapping with a top-level '{key}' list")
    entries = loaded[key]
    if not isinstance(entries, list) or not entries:
        raise DatasetError(f"{path.name}: '{key}' must be a non-empty list")
    return entries


def _no_duplicates(ids: list[str], what: str) -> None:
    dupes = sorted(i for i, n in Counter(ids).items() if n > 1)
    if dupes:
        raise DatasetError(f"duplicate {what}: {', '.join(dupes)}")


def load_dataset(
    dataset_dir: Path | None = None,
    fixture_dir: Path | None = None,
) -> Dataset:
    """Load, validate, and return the dataset — or raise.

    Both directories are injectable so the tests can point at a deliberately
    broken dataset. That is not a convenience: the hermetic gate has to prove
    the loader *rejects* bad input, and it can only do that against a bad
    dataset that does not ship.
    """
    dataset_dir = dataset_dir or DATASET_DIR
    fixture_dir = fixture_dir or FIXTURE_DIR

    try:
        golden = tuple(
            GoldenCase.model_validate(entry)
            for entry in _read_yaml(dataset_dir / "golden.yaml", "cases")
        )
        adversarial = tuple(
            AdversarialProbe.model_validate(entry)
            for entry in _read_yaml(dataset_dir / "adversarial.yaml", "probes")
        )
        calibration = tuple(
            CalibrationSample.model_validate(entry)
            for entry in _read_yaml(dataset_dir / "calibration.yaml", "samples")
        )
    except ValidationError as exc:
        raise DatasetError(f"dataset failed schema validation:\n{exc}") from exc

    _no_duplicates([c.case_id for c in golden], "case_id")
    _no_duplicates([p.probe_id for p in adversarial], "probe_id")

    known = {c.case_id for c in golden}
    unknown = sorted({s.case_id for s in calibration} - known)
    if unknown:
        raise DatasetError(f"calibration references unknown case_id(s): {', '.join(unknown)}")

    referenced = {c.fixture for c in golden} | {
        p.inject_into_fixture for p in adversarial if p.inject_into_fixture
    }
    missing = sorted(name for name in referenced if not (fixture_dir / name).exists())
    if missing:
        raise DatasetError(
            f"cases reference fixtures that do not exist in {fixture_dir}: {', '.join(missing)}"
        )

    return Dataset(golden=golden, adversarial=adversarial, calibration=calibration)


def load_fixture(name: str, fixture_dir: Path | None = None) -> str:
    """Return a fixture's response body, as the text a tool result would carry.

    Only `body` is passed through. The recorded envelope also holds the HTTP
    status, which is the MCP server's business and not something the model
    should be shown or graded on.
    """
    fixture_dir = fixture_dir or FIXTURE_DIR
    path = fixture_dir / name
    if not path.exists():
        raise DatasetError(f"fixture not found: {path}")
    recorded = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(recorded.get("body"), ensure_ascii=False)


def curation_rate(dataset_dir: Path | None = None) -> str:
    """Where the published curation rate lives.

    Deliberately not computed. ARCHITECTURE.md §6 asks for the rate at which a
    human edited Claude's drafts, and nothing in the repo can observe that — it
    is a fact about the authoring session, recorded by the person who did it.
    A function that derived a number from the files would be inventing one.
    """
    return "recorded by hand in docs/VALIDATION.md (M03 hermetic row)"
