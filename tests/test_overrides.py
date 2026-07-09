from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from osrforge.contracts.overrides import (
    AreaGeometryOverride,
    AreaOverride,
    GeometryOverride,
    ModuleOverride,
    MonsterOverride,
    Overrides,
    TownOverride,
    load_overrides,
)

# The spec's own overrides example (docs/spec.md § Overrides), verbatim —
# keeping the spec and the models honest against each other.
SPEC_OVERRIDES_EXAMPLE = """
monsters:
  "hobgoblin chieftain":
    template_id: hobgoblin
    reason: No SRD template for the named chieftain; base hobgoblin is the closest match.

areas:
  barrow/1/7:
    description: |
      Corrected text copied from p. 14 — extraction merged rooms 7 and 8.
    reason: Extraction merged two rooms.

geometry:
  barrow/1:
    areas:
      "7":
        cells: [[4, 2], [5, 2], [4, 3], [5, 3]]
    edges:
      "5,2:east": { kind: door, door: { stuck: true } }
    reason: Match the printed map; room 7 is 20' x 20' with a stuck east door.
"""


def test_spec_overrides_example_parses_verbatim():
    overrides = Overrides.model_validate(yaml.safe_load(SPEC_OVERRIDES_EXAMPLE))
    assert overrides.monsters["hobgoblin chieftain"].template_id == "hobgoblin"
    assert "barrow/1/7" in overrides.areas
    geometry = overrides.geometry["barrow/1"]
    assert geometry.areas["7"].cells == ((4, 2), (5, 2), (4, 3), (5, 3))
    edge = geometry.edges["5,2:east"]
    assert edge.kind == "door"
    assert edge.door is not None and edge.door.stuck is True


def test_overrides_round_trip():
    overrides = Overrides.model_validate(yaml.safe_load(SPEC_OVERRIDES_EXAMPLE))
    assert Overrides.model_validate(overrides.model_dump(mode="json", exclude_unset=True)) == overrides


def test_load_overrides_missing_file_is_empty(tmp_path: Path):
    overrides = load_overrides(tmp_path / "overrides.yaml")
    assert overrides == Overrides()


def test_load_overrides_empty_file_is_empty(tmp_path: Path):
    path = tmp_path / "overrides.yaml"
    path.write_text("", encoding="utf-8")
    assert load_overrides(path) == Overrides()


def test_load_overrides_reads_yaml(tmp_path: Path):
    path = tmp_path / "overrides.yaml"
    path.write_text(SPEC_OVERRIDES_EXAMPLE, encoding="utf-8")
    overrides = load_overrides(path)
    assert overrides.monsters["hobgoblin chieftain"].reason.startswith("No SRD template")


@pytest.mark.parametrize("reason", ["", None])
def test_reason_required_and_non_empty_on_every_kind(reason: str | None):
    entries: list[dict[str, object]] = [
        {"template_id": "hobgoblin"},
        {"description": "text"},
        {"areas": {"7": {"cells": [[0, 0]]}}},
        {"name": "Newtown"},
        {"name": "New Name"},
    ]
    models = [MonsterOverride, AreaOverride, GeometryOverride, TownOverride, ModuleOverride]
    for model, entry in zip(models, entries, strict=True):
        if reason is not None:
            entry["reason"] = reason
        with pytest.raises(ValidationError):
            model.model_validate(entry)


def test_unknown_top_level_key_rejected():
    data = yaml.safe_load(SPEC_OVERRIDES_EXAMPLE)
    data["treasures"] = {}
    with pytest.raises(ValidationError):
        Overrides.model_validate(data)


def test_unknown_per_entry_key_rejected():
    data = yaml.safe_load(SPEC_OVERRIDES_EXAMPLE)
    data["areas"]["barrow/1/7"]["descriptino"] = "typo"
    with pytest.raises(ValidationError):
        Overrides.model_validate(data)


def test_area_keys_must_be_area_addresses():
    with pytest.raises(ValidationError):
        Overrides.model_validate({"areas": {"barrow/1": {"reason": "not an area address"}}})


def test_geometry_keys_must_be_level_addresses():
    with pytest.raises(ValidationError):
        Overrides.model_validate({"geometry": {"barrow/1/7": {"reason": "not a level address"}}})


@pytest.mark.parametrize("key", ["5,2:up", "5;2:east", "east", "5,2", "-1,2:east", "05,2:east", "٥,2:east"])  # noqa: RUF001
def test_geometry_edge_key_grammar_rejects(key: str):
    with pytest.raises(ValidationError):
        GeometryOverride.model_validate(
            {"edges": {key: {"kind": "open"}}, "reason": "test"},
        )


@pytest.mark.parametrize("direction", ["north", "south", "east", "west"])
def test_geometry_edge_key_accepts_all_four_directions(direction: str):
    override = GeometryOverride.model_validate({"edges": {f"5,2:{direction}": {"kind": "open"}}, "reason": "test"})
    assert f"5,2:{direction}" in override.edges


def test_osrlib_rejects_bad_payload_at_load_time():
    # A door edge without a door spec violates osrlib's own Edge validator —
    # embedding osrlib models directly means this fails at overrides load.
    with pytest.raises(ValidationError):
        GeometryOverride.model_validate({"edges": {"5,2:east": {"kind": "door"}}, "reason": "test"})


def test_geometry_area_cells_required_non_empty():
    with pytest.raises(ValidationError):
        AreaGeometryOverride.model_validate({"cells": []})


def test_absent_vs_null_distinction_survives_dump_cycle():
    absent = AreaOverride.model_validate({"reason": "leave the trap alone"})
    cleared = AreaOverride.model_validate({"trap": None, "reason": "remove the trap"})

    assert "trap" not in absent.model_fields_set
    assert "trap" in cleared.model_fields_set
    assert cleared.trap is None

    absent_again = AreaOverride.model_validate(absent.model_dump(mode="json", exclude_unset=True))
    cleared_again = AreaOverride.model_validate(cleared.model_dump(mode="json", exclude_unset=True))
    assert "trap" not in absent_again.model_fields_set
    assert "trap" in cleared_again.model_fields_set


def test_area_override_embeds_osrlib_encounter():
    override = AreaOverride.model_validate(
        {
            "encounter": {"monsters": [{"template_id": "goblin", "count_fixed": 3}]},
            "reason": "extraction missed the goblins",
        }
    )
    assert override.encounter is not None
    assert override.encounter.monsters[0].template_id == "goblin"

    with pytest.raises(ValidationError):
        # count_dice and count_fixed together violate osrlib's KeyedMonster validator.
        AreaOverride.model_validate(
            {
                "encounter": {"monsters": [{"template_id": "goblin", "count_fixed": 3, "count_dice": "1d6"}]},
                "reason": "bad payload",
            }
        )
