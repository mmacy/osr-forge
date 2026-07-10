"""Artifact purity: byte-identical re-assembly, no timestamps, and the golden compatibility gate."""

import json
import re
from pathlib import Path

import pytest
from osrlib.crawl.adventure import Adventure, validate_adventure
from osrlib.data import load_equipment, load_monsters
from osrlib.versioning import check_document

from osrforge.assemble import assemble, render_previews
from test_assemble import assembled_workdir

ASSETS = Path(__file__).parent / "assets"

# ISO-8601-shaped content anywhere in a pure artifact is a purity bug — the
# phase 0 pin, live now that report production exists.
ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

GOLDEN_ADVENTURES = sorted(ASSETS.glob("*/expected/adventure.json"))


def pure_artifacts(root: Path) -> dict[str, bytes]:
    files = {
        "adventure.json": (root / "adventure.json").read_bytes(),
        "report.json": (root / "report.json").read_bytes(),
    }
    for path in sorted((root / "previews").iterdir()):
        files[f"previews/{path.name}"] = path.read_bytes()
    return files


def test_two_assemblies_are_byte_identical(tmp_path: Path):
    workdir = assembled_workdir(tmp_path / "mod.forge")
    assemble(workdir.root)
    first = pure_artifacts(workdir.root)
    assemble(workdir.root)
    second = pure_artifacts(workdir.root)
    assert first == second


def test_no_pure_artifact_contains_a_timestamp(tmp_path: Path):
    workdir = assembled_workdir(tmp_path / "mod.forge")
    assemble(workdir.root)
    for name, data in pure_artifacts(workdir.root).items():
        assert not ISO_TIMESTAMP.search(data.decode("utf-8")), f"timestamp-shaped content in {name}"
    # run.json is operational metadata — timestamps are legal there and only there.
    assert ISO_TIMESTAMP.search(workdir.run_json.read_text(encoding="utf-8"))


def test_preview_command_rewrites_previews_only(tmp_path: Path):
    workdir = assembled_workdir(tmp_path / "mod.forge")
    assemble(workdir.root)
    before = pure_artifacts(workdir.root)
    run_json_before = workdir.run_json.read_bytes()
    written = render_previews(workdir.root)
    assert [path.name for path in written] == ["lair.1.svg"]
    assert pure_artifacts(workdir.root) == before  # identical bytes, nothing else touched
    assert workdir.run_json.read_bytes() == run_json_before


def test_goldens_exist_for_every_expected_module():
    # The compatibility gate below runs per discovered golden; pin the
    # discovery so a vanished golden fails loudly instead of silently passing.
    assert ASSETS / "minimod" / "expected" / "adventure.json" in GOLDEN_ADVENTURES


@pytest.mark.parametrize("golden", GOLDEN_ADVENTURES, ids=lambda path: path.parent.parent.name)
def test_golden_adventures_load_against_the_pinned_osrlib(golden: Path):
    # The spec's compatibility gate: an osrlib upgrade that moves the stamped
    # envelope or the Adventure models fails loudly here first.
    document = json.loads(golden.read_text(encoding="utf-8"))
    payload = check_document(document, "adventure")
    adventure = Adventure.model_validate(payload)
    validate_adventure(adventure, load_monsters(), load_equipment())
