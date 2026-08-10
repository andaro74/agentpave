"""The seed dataset, the seed probes, and the rendered gate.

A scaffolded service arrives with an exam, not just the ability to run one.
That claim is only worth making if the exam loads, grades, and names controls
that can actually fire — so this module checks all three against the real
loader, the real CLI parser, and the real gateway controls, never a copy.

Imports reach across components on purpose. `agentpave_gateway` is not a
dependency of `pave`, and it should not become one; but the question
"does this probe's expected control exist" cannot be answered from inside the
template, and answering it against a reimplementation would prove only that
two copies of an idea agree. The workspace installs every member, so the real
control is available here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from agentpave_evalsvc.dataset import DatasetError, load_dataset
from agentpave_gateway.routing import RoutingTable
from agentpave_gateway.screening import find_encoded_text
from agentpave_pave.cli import TEMPLATE_ROOT, _build_parser
from agentpave_pave.scaffold import render, validate

TEMPLATE = TEMPLATE_ROOT / "agent-tools"


@pytest.fixture
def service(tmp_path: Path) -> Path:
    render(validate("catalog-agent", "internal"), template_root=TEMPLATE, output_root=tmp_path)
    return tmp_path / "catalog-agent"


# ── the seed dataset ──────────────────────────────────────────────────────


def test_the_seed_dataset_loads_through_the_platform_loader(service: Path):
    """Not a schema of its own — the loader the platform grades with.

    A template shipping YAML validated only by a test written beside it can
    drift from the loader indefinitely, and the drift surfaces on the day
    someone runs `pave eval` against a scaffolded service for the first time.
    """
    dataset = load_dataset(service / "eval")

    assert len(dataset.golden) == 5
    assert len(dataset.adversarial) == 4


def test_every_seed_case_is_deterministic(service: Path):
    """A scaffolded service has no calibrated judge, so it grades on what needs
    no judge. The alternative is a suite resting on an agreement rate nobody
    measured, which looks like coverage and is not."""
    dataset = load_dataset(service / "eval")

    assert [c.grading for c in dataset.golden] == ["deterministic"] * 5
    # And the honest half of that: the file is absent, not filled with samples
    # the template author invented on a service that has never run.
    assert not (service / "eval" / "calibration.yaml").exists()


def test_case_ids_carry_the_service_name(service: Path):
    """Case ids are the join key for the baseline diff and M05's dashboards.
    Two services each shipping `airing-channel` produce a trend line that
    silently averages two different questions."""
    dataset = load_dataset(service / "eval")

    assert all(c.case_id.startswith("catalog-agent-") for c in dataset.golden)
    assert all(p.probe_id.startswith("catalog-agent-") for p in dataset.adversarial)


def test_a_judged_case_without_calibration_is_refused(service: Path):
    """The pairing that makes an absent calibration file safe.

    Calibration may be omitted only by a dataset that also omits every judged
    case. Flipping one case to `judged` without labelling anything must fail
    the load, not produce a run graded by an unmeasured judge.
    """
    golden = service / "eval" / "golden.yaml"
    golden.write_text(
        golden.read_text(encoding="utf-8").replace(
            "capability: airing\n    grading: deterministic",
            "capability: airing\n    grading: judged",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(DatasetError, match="no calibration samples"):
        load_dataset(service / "eval")


# ── the probes name controls that can actually fire ───────────────────────


def test_the_encoded_probe_trips_the_gateway_screen(service: Path):
    """ADR-015, checked rather than asserted in a comment.

    M03 shipped a probe whose expected control was not in the request path it
    was sent to; it could never pass, and a deployed run is what noticed. The
    encoded probe's control is the gateway's own screen, and the screen is
    pure — so whether it fires is a hermetic question, answerable here for the
    price of one function call.
    """
    dataset = load_dataset(service / "eval")
    encoded = next(p for p in dataset.adversarial if "encoded" in p.probe_id)

    assert find_encoded_text(encoded.prompt), (
        "the encoded probe carries no base64 the screen recognises — it would "
        "reach the model and be scored `model_complied`"
    )


def test_the_sensitive_probe_is_refused_by_the_routing_table(service: Path):
    """The other reachable control, checked the same way. `sensitive` is turned
    away before a model is chosen (ADR-001), so this probe's pass does not
    depend on any model's behaviour."""
    dataset = load_dataset(service / "eval")
    sensitive = [p for p in dataset.adversarial if p.classification == "sensitive"]
    assert sensitive, "the seed suite must exercise the classification refusal"

    table = RoutingTable(model_fast="fast-model-id", model_capable="capable-model-id")
    for probe in sensitive:
        assert table.route("serve", probe.classification).model_id is None


def test_no_probe_expects_a_control_the_gateway_path_lacks(service: Path):
    """The general form of the same rule, as a tripwire.

    Cedar denials are real and are tested — from the MCP path, in the contract
    suite. A probe added here that says `tool`, `cedar` or `escalat` is almost
    certainly the M03 mistake being rebuilt: an authorization control asserted
    from a request path that has no authorization in it.
    """
    text = (service / "eval" / "adversarial.yaml").read_text(encoding="utf-8")
    probes = yaml.safe_load(text)["probes"]

    for probe in probes:
        haystack = f"{probe['probe_id']} {probe['prompt']}".lower()
        for banned in ("cedar", "escalat", "invoke the tool"):
            assert banned not in haystack, (
                f"{probe['probe_id']} looks like a policy probe, but probes are sent to the "
                "gateway, which has no Cedar in its request path (ADR-015)"
            )


# ── the rendered gate ─────────────────────────────────────────────────────


def test_the_rendered_gate_fails_closed(service: Path):
    gate = yaml.safe_load((service / "gate.yml").read_text(encoding="utf-8"))

    assert gate["fail_closed"] is True
    assert gate["service"] == "catalog-agent"
    assert [level["id"] for level in gate["levels"]] == ["L0", "L1", "L2", "L5"]
    assert all(level["blocking"] is True for level in gate["levels"])


def test_the_gate_carries_no_escape_hatch(service: Path):
    """`continue_on_error` in a gate is the gate, undone in one line. If a level
    is too flaky to block, the fix is the level — deleting it makes the lost
    coverage visible, where a skip key makes it invisible on every run."""
    text = (service / "gate.yml").read_text(encoding="utf-8").lower()

    for escape in ("continue_on_error", "continue-on-error", "allow_failure"):
        # The word appears once, in the prose explaining why the key is absent.
        assert f"{escape}:" not in text


def test_every_gate_command_is_a_verb_the_cli_accepts(service: Path):
    """The check that keeps `gate.yml` from being decoration.

    A ladder naming a flag the CLI does not have is a ladder that fails on the
    day CI first runs it. Parsed with the real parser, so a renamed flag turns
    this red in the same commit that renames it.

    That only holds because the parser has `allow_abbrev=False`. With argparse's
    default, `--adversarial` parses as `--adversarial-only` and a `gate.yml`
    naming a flag that does not exist passes this test — which is exactly what
    a mutation showed before the flag was turned off.
    """
    gate = yaml.safe_load((service / "gate.yml").read_text(encoding="utf-8"))
    parser = _build_parser()

    for level in gate["levels"]:
        argv = level["command"].split()
        if argv[0] != "pave":
            continue
        args = parser.parse_args(argv[1:])
        assert args.verb == "eval"
        assert args.dataset == "services/catalog-agent/eval"


def test_the_dataset_the_gate_points_at_is_the_one_that_ships(service: Path):
    """The path in `gate.yml` and the directory the template renders have to be
    the same place. They are written in two files and nothing but this connects
    them."""
    gate = yaml.safe_load((service / "gate.yml").read_text(encoding="utf-8"))
    referenced = {
        Path(level["command"].split("--dataset ")[1].split()[0]).name
        for level in gate["levels"]
        if "--dataset" in level["command"]
    }

    assert referenced == {"eval"}
    assert (service / "eval" / "golden.yaml").exists()
    assert (service / "eval" / "adversarial.yaml").exists()
