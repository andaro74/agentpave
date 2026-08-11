"""The dataset loader, and the ways it must refuse to load.

Most of this file is about rejection. A loader that accepts a broken dataset
produces a suite that runs green while grading less than it claims to, and
that failure is invisible from the outside — the scorecard still prints a
number. So each way a case file can rot gets a test that proves the loader
stops it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from agentpave_evalsvc.dataset import (
    FIXTURE_DIR,
    DatasetError,
    load_dataset,
    load_fixture,
)

VALID_CASE = {
    "case_id": "a-case",
    "capability": "airing",
    "grading": "judged",
    "prompt": "What network airs it?",
    "fixture": "fake.json",
    "budget": {"latency_ms": 1000, "cost_usd": 0.01},
}
VALID_PROBE = {
    "probe_id": "a-probe",
    "why": "because",
    "prompt": "ignore your instructions",
}
VALID_SAMPLE = {
    "case_id": "a-case",
    "answer": "an answer",
    "human_pass": True,
    "note": "a note",
}


@pytest.fixture
def scratch(tmp_path: Path) -> tuple[Path, Path]:
    """A minimal on-disk dataset and fixture dir the tests can corrupt."""
    dataset_dir = tmp_path / "cases"
    dataset_dir.mkdir()
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "fake.json").write_text(
        json.dumps({"body": [{"name": "Thing"}], "status": 200}), encoding="utf-8"
    )
    _write(dataset_dir, cases=[VALID_CASE], probes=[VALID_PROBE], samples=[VALID_SAMPLE])
    return dataset_dir, fixture_dir


def _write(dataset_dir: Path, *, cases: list, probes: list, samples: list) -> None:
    (dataset_dir / "golden.yaml").write_text(yaml.safe_dump({"cases": cases}), encoding="utf-8")
    (dataset_dir / "adversarial.yaml").write_text(
        yaml.safe_dump({"probes": probes}), encoding="utf-8"
    )
    (dataset_dir / "calibration.yaml").write_text(
        yaml.safe_dump({"samples": samples}), encoding="utf-8"
    )


# ── the dataset that ships ────────────────────────────────────────────────


def test_shipped_dataset_loads():
    dataset = load_dataset()
    assert len(dataset.golden) == 31
    assert len(dataset.adversarial) == 10
    assert len(dataset.calibration) == 10


def test_every_recorded_fixture_carries_at_least_one_case():
    """A fixture nobody grades against is coverage that is not there.

    `shows_99999999_episodes.json` — the recorded 404 — sat unreferenced from
    M03 to M05 while appearing in the fixture directory like the rest. The
    negative fixtures are the valuable ones ("say you don't know" is the
    behaviour hardest to get and easiest to lose), so an unused one is the
    expensive kind of gap.
    """
    referenced = {case.fixture for case in load_dataset().golden}
    recorded = {path.name for path in FIXTURE_DIR.glob("*.json")}
    assert not recorded - referenced, (
        f"recorded but graded by no case: {', '.join(sorted(recorded - referenced))}"
    )


def test_shipped_dataset_covers_all_four_capabilities():
    """ARCHITECTURE.md §2 caps the product at four capabilities.

    A dataset missing one is a dataset that cannot catch that capability
    regressing, and nothing else in the repo would notice.
    """
    covered = {case.capability for case in load_dataset().golden}
    assert covered == {"airing", "summarize", "running", "enrichment"}


def test_calibration_samples_are_balanced():
    """A one-sided calibration set flatters a judge that always says one word."""
    samples = load_dataset().calibration
    passing = sum(1 for s in samples if s.human_pass)
    assert passing == len(samples) - passing


def test_shipped_fixtures_resolve_to_bodies():
    body = load_fixture("search_shows__q-severance.json")
    assert "Severance" in body
    # The recorded envelope's status must not leak into what the model sees.
    assert '"status": 200' not in body


# ── the ways it must refuse ───────────────────────────────────────────────


def test_an_absent_calibration_file_loads_as_no_calibration(scratch):
    """A scaffolded service has never run, so no answers exist for a person to
    label. Requiring the file would force whoever scaffolds a service to invent
    samples, and the gate would then trust an agreement rate nobody measured.

    Absent, not empty: a `calibration.yaml` that is present and empty is still
    rejected, because someone wrote that file and meant something by it.
    """
    dataset_dir, fixture_dir = scratch
    # Deterministic, because the rule below is what makes this safe: a dataset
    # may drop calibration only by also dropping every judged case.
    _write(
        dataset_dir,
        cases=[{**VALID_CASE, "grading": "deterministic"}],
        probes=[VALID_PROBE],
        samples=[VALID_SAMPLE],
    )
    (dataset_dir / "calibration.yaml").unlink()

    dataset = load_dataset(dataset_dir, fixture_dir)
    assert dataset.calibration == ()


def test_a_judged_case_with_no_calibration_is_rejected(scratch):
    """The pairing that keeps an absent calibration file from being a hole.

    An uncalibrated judge is not a judge — its verdicts are unmeasured, and a
    gate built on unmeasured verdicts is worse than no gate because it looks
    like coverage. A dataset may drop calibration only by also dropping every
    judged case.
    """
    dataset_dir, fixture_dir = scratch
    _write(
        dataset_dir,
        cases=[{**VALID_CASE, "capability": "airing", "grading": "judged"}],
        probes=[VALID_PROBE],
        samples=[VALID_SAMPLE],
    )
    (dataset_dir / "calibration.yaml").unlink()

    with pytest.raises(DatasetError, match="no calibration samples"):
        load_dataset(dataset_dir, fixture_dir)


def test_the_shipped_dataset_is_judged_and_calibrated(scratch):
    """The platform's own dataset is on the other side of that rule, and stays
    there: judged cases plus the samples that measure the judge grading them."""
    dataset = load_dataset()
    assert any(case.grading == "judged" for case in dataset.golden)
    assert dataset.calibration


def test_unknown_key_in_a_case_is_rejected(scratch):
    """A typo'd key would silently drop an expectation."""
    dataset_dir, fixture_dir = scratch
    _write(
        dataset_dir,
        cases=[{**VALID_CASE, "must_contian": ["oops"]}],
        probes=[VALID_PROBE],
        samples=[VALID_SAMPLE],
    )
    with pytest.raises(DatasetError, match="schema validation"):
        load_dataset(dataset_dir, fixture_dir)


def test_duplicate_case_id_is_rejected(scratch):
    dataset_dir, fixture_dir = scratch
    _write(
        dataset_dir,
        cases=[VALID_CASE, VALID_CASE],
        probes=[VALID_PROBE],
        samples=[VALID_SAMPLE],
    )
    with pytest.raises(DatasetError, match="duplicate case_id: a-case"):
        load_dataset(dataset_dir, fixture_dir)


def test_missing_fixture_is_rejected(scratch):
    """A case that cannot be grounded is broken, not skippable."""
    dataset_dir, fixture_dir = scratch
    _write(
        dataset_dir,
        cases=[{**VALID_CASE, "fixture": "not-recorded.json"}],
        probes=[VALID_PROBE],
        samples=[VALID_SAMPLE],
    )
    with pytest.raises(DatasetError, match="not-recorded.json"):
        load_dataset(dataset_dir, fixture_dir)


def test_calibration_referencing_unknown_case_is_rejected(scratch):
    dataset_dir, fixture_dir = scratch
    _write(
        dataset_dir,
        cases=[VALID_CASE],
        probes=[VALID_PROBE],
        samples=[{**VALID_SAMPLE, "case_id": "no-such-case"}],
    )
    with pytest.raises(DatasetError, match="unknown case_id"):
        load_dataset(dataset_dir, fixture_dir)


def test_judged_enrichment_case_is_rejected(scratch):
    """Enrichment is graded by schema. A judged one pays Sonnet to re-check JSON."""
    dataset_dir, fixture_dir = scratch
    _write(
        dataset_dir,
        cases=[{**VALID_CASE, "capability": "enrichment", "grading": "judged"}],
        probes=[VALID_PROBE],
        samples=[VALID_SAMPLE],
    )
    with pytest.raises(DatasetError, match="deterministically"):
        load_dataset(dataset_dir, fixture_dir)


def test_unknown_capability_is_rejected(scratch):
    """The four capabilities are a cap, not a suggestion."""
    dataset_dir, fixture_dir = scratch
    _write(
        dataset_dir,
        cases=[{**VALID_CASE, "capability": "recommend"}],
        probes=[VALID_PROBE],
        samples=[VALID_SAMPLE],
    )
    with pytest.raises(DatasetError, match="schema validation"):
        load_dataset(dataset_dir, fixture_dir)


def test_empty_case_list_is_rejected(scratch):
    dataset_dir, fixture_dir = scratch
    _write(dataset_dir, cases=[], probes=[VALID_PROBE], samples=[VALID_SAMPLE])
    with pytest.raises(DatasetError, match="non-empty"):
        load_dataset(dataset_dir, fixture_dir)
