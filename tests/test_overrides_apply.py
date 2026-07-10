"""Overrides application semantics against a synthetic draft.

The synthetic module is shaped so the spec's own § Overrides example addresses
it verbatim (`barrow/1/7`, a hobgoblin chieftain, a stuck east door) — phase 0
proved the example parses; these tests prove it takes effect. Unit tests drive
`plan_overrides`/`apply_level_overrides`/`build_draft` directly; the end-to-end
and purity tests go through a fabricated workdir and `assemble`.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from osrlib.crawl.dungeon import EdgeKind

from osrforge.assemble import DraftResult, assemble, build_draft, render_previews
from osrforge.contracts.overrides import Overrides
from osrforge.contracts.report import AreaReport
from osrforge.contracts.run import RunMeta, Stage, StageStatus
from osrforge.contracts.stages import (
    AreaConnection,
    AreaContent,
    AreaEncounter,
    LevelContent,
    MonsterResolution,
    MonsterResolutions,
    SurveyArea,
    SurveyDungeon,
    SurveyIndex,
    SurveyLevel,
    TownInfo,
)
from osrforge.errors import OverrideError
from osrforge.geometry import synthesize_geometry
from osrforge.overrides import apply_level_overrides, apply_monster_overrides, plan_overrides
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, write_json_artifact

SPEC_EXAMPLE = """
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


def synthetic_index(title: str = "The Example Barrow", town_name: str = "Riverton") -> SurveyIndex:
    return SurveyIndex(
        title=title,
        hooks=("Rumors speak of a barrow.",),
        town=TownInfo(name=town_name, description="A river town."),
        dungeons=(
            SurveyDungeon(
                id="barrow",
                name="The Barrow",
                levels=(
                    SurveyLevel(
                        number=1,
                        map_pages=(2,),
                        areas=(
                            SurveyArea(key="1", name="Guard Post", kind="room", source_pages=(3,)),
                            SurveyArea(key="7", name="Hall", kind="room", source_pages=(3,)),
                            SurveyArea(key="8", name="Cell", kind="room", source_pages=(4,)),
                        ),
                    ),
                ),
            ),
        ),
        monster_names=("hobgoblin chieftain", "goblin"),
    )


def synthetic_levels() -> tuple[LevelContent, ...]:
    return (
        LevelContent(
            dungeon_id="barrow",
            level_number=1,
            areas=(
                AreaContent(
                    key="1",
                    description="A guard post.",
                    encounters=(AreaEncounter(monster="Hobgoblin Chieftain", count_fixed=1),),
                    treasure=(),
                    features=(),
                    connections=(AreaConnection(to_key="7", direction="east"),),
                    source_pages=(3,),
                    confidence=0.9,
                ),
                AreaContent(
                    key="7",
                    description="A great hall.",
                    encounters=(),
                    trap="A pit trap before the altar.",
                    treasure=("100 gp", "an amulet worth 50 gp", "treasure type A", "3 potions for each visitor"),
                    features=("An altar of black stone",),
                    connections=(AreaConnection(to_key="8", direction="east"),),
                    source_pages=(3,),
                    confidence=0.62,
                ),
                AreaContent(
                    key="8",
                    description="A cramped cell.",
                    encounters=(AreaEncounter(monster="goblin", count_dice="1d6"),),
                    treasure=("weird glowing dust",),
                    features=(),
                    connections=(AreaConnection(to_key="99", direction="north"),),
                    source_pages=(4,),
                    confidence=0.8,
                ),
            ),
        ),
    )


def synthetic_resolutions() -> MonsterResolutions:
    return MonsterResolutions(
        resolutions={
            "goblin": MonsterResolution(template_id="goblin", method="exact"),
            "hobgoblin chieftain": MonsterResolution(template_id=None, method="unresolved"),
        }
    )


def parse_overrides(text: str) -> Overrides:
    return Overrides.model_validate(yaml.safe_load(text))


def built(
    overrides_text: str = "",
    index: SurveyIndex | None = None,
    settings: ConversionSettings | None = None,
) -> DraftResult:
    index = index if index is not None else synthetic_index()
    levels = synthetic_levels()
    overrides = parse_overrides(overrides_text) if overrides_text.strip() else Overrides()
    resolutions = apply_monster_overrides(synthetic_resolutions(), overrides)
    plan = plan_overrides(index, overrides)
    geometries = tuple(
        apply_level_overrides(geometry, plan.levels.get((geometry.dungeon_id, geometry.level_number)))
        for geometry in synthesize_geometry(index, levels)
    )
    return build_draft(index, levels, resolutions, geometries, settings or ConversionSettings(), plan)


def area_spec(draft: DraftResult, key: str):
    level = draft.adventure.dungeons[0].levels[0]
    return next(area for area in level.areas if area.id == key)


def area_report(draft: DraftResult, key: str) -> AreaReport:
    return next(report for report in draft.area_reports if report.id == f"barrow/1/{key}")


def plan_error(overrides_text: str, index: SurveyIndex | None = None) -> str:
    with pytest.raises(OverrideError) as excinfo:
        plan_overrides(index if index is not None else synthetic_index(), parse_overrides(overrides_text))
    return str(excinfo.value)


def synthetic_workdir(root: Path, overrides_text: str | None = None) -> Workdir:
    workdir = Workdir(root)
    workdir.stages_dir.mkdir(parents=True)
    write_json_artifact(workdir.survey_json, synthetic_index())
    for level in synthetic_levels():
        write_json_artifact(workdir.areas_json(level.dungeon_id, level.level_number), level)
    write_json_artifact(workdir.monsters_json, synthetic_resolutions())
    stages = {stage: StageStatus() for stage in Stage}
    for stage in (Stage.PREPROCESS, Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS):
        stages[stage] = StageStatus(
            status="completed",
            started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC),
        )
    workdir.write_run(
        RunMeta(
            source_sha256="00" * 32,
            source_bytes=1,
            page_count=5,
            settings=ConversionSettings(),
            stages=stages,
        )
    )
    if overrides_text is not None:
        workdir.overrides_yaml.write_text(overrides_text, encoding="utf-8")
    return workdir


# --------------------------------------------------------------- the spec example


def test_spec_overrides_example_takes_effect_end_to_end(tmp_path: Path):
    workdir = synthetic_workdir(tmp_path / "mod.forge", SPEC_EXAMPLE)
    result = assemble(workdir.root)
    level = result.adventure.dungeons[0].levels[0]

    hall = next(area for area in level.areas if area.id == "7")
    assert hall.description.startswith("Corrected text copied from p. 14")
    assert hall.cells == ((4, 2), (5, 2), (4, 3), (5, 3))

    # "5,2:east" canonicalizes to the eastern neighbour's west edge.
    assert "5,2:east" not in level.edges
    door_edge = level.edges["6,2:west"]
    assert door_edge.kind is EdgeKind.DOOR
    assert door_edge.door is not None and door_edge.door.stuck is True

    guard_post = next(area for area in level.areas if area.id == "1")
    assert guard_post.encounter is not None
    assert guard_post.encounter.monsters[0].template_id == "hobgoblin"

    assert result.report.monsters.unresolved == ()
    assert result.report.monsters.resolved == 2
    hall_report = next(report for report in result.report.areas if report.id == "barrow/1/7")
    assert hall_report.overridden == ("description", "cells")
    assert not any(flag.startswith("geometry_synthesized") for flag in hall_report.flags)
    guard_report = next(report for report in result.report.areas if report.id == "barrow/1/1")
    assert not any(flag.startswith("monster_unresolved") for flag in guard_report.flags)
    assert result.report.validation.passed


def test_assembly_with_overrides_is_byte_stable_and_previews_show_doors(tmp_path: Path):
    first = synthetic_workdir(tmp_path / "one.forge", SPEC_EXAMPLE)
    second = synthetic_workdir(tmp_path / "two.forge", SPEC_EXAMPLE)
    assemble(first.root)
    assemble(second.root)
    for name in ("adventure.json", "report.json"):
        assert (first.root / name).read_bytes() == (second.root / name).read_bytes()
    first_previews = {path.name: path.read_bytes() for path in sorted(first.previews_dir.iterdir())}
    second_previews = {path.name: path.read_bytes() for path in sorted(second.previews_dir.iterdir())}
    assert first_previews == second_previews
    # The door tick renders — the phase 2 renderer has drawn doors "from birth"
    # for exactly this moment.
    assert "#8b5a2b" in first_previews["barrow.1.svg"].decode("utf-8")


def test_preview_command_matches_assemblys_previews(tmp_path: Path):
    workdir = synthetic_workdir(tmp_path / "mod.forge", SPEC_EXAMPLE)
    assemble(workdir.root)
    assembled = {path.name: path.read_bytes() for path in sorted(workdir.previews_dir.iterdir())}
    for path in workdir.previews_dir.iterdir():
        path.unlink()
    render_previews(workdir.root)
    regenerated = {path.name: path.read_bytes() for path in sorted(workdir.previews_dir.iterdir())}
    assert regenerated == assembled


def test_overrides_error_leaves_run_json_and_artifacts_untouched(tmp_path: Path):
    workdir = synthetic_workdir(tmp_path / "mod.forge")
    assemble(workdir.root)
    run_before = workdir.run_json.read_bytes()
    adventure_before = workdir.adventure_json.read_bytes()
    workdir.overrides_yaml.write_text(
        'areas:\n  barrow/1/99:\n    remove: true\n    reason: "typo\'d address"\n', encoding="utf-8"
    )
    with pytest.raises(OverrideError):
        assemble(workdir.root)
    assert workdir.run_json.read_bytes() == run_before
    assert workdir.adventure_json.read_bytes() == adventure_before


# --------------------------------------------------------------- monsters


def test_monster_override_key_normalizes_like_the_monsters_stage():
    draft = built('monsters:\n  "Hobgoblin  Chieftain":\n    template_id: hobgoblin\n    reason: normalization test\n')
    encounter = area_spec(draft, "1").encounter
    assert encounter is not None and encounter.monsters[0].template_id == "hobgoblin"
    assert draft.unresolved == ()


def test_monster_override_of_a_resolved_name_wins():
    draft = built("monsters:\n  goblin:\n    template_id: orc\n    reason: the module statted them as orcs\n")
    encounter = area_spec(draft, "8").encounter
    assert encounter is not None and encounter.monsters[0].template_id == "orc"


def test_monster_override_suppresses_the_stand_in_and_its_flag():
    baseline = built()
    baseline_flags = area_report(baseline, "1").flags
    assert any(flag.startswith("monster_unresolved:") for flag in baseline_flags)
    draft = built('monsters:\n  "hobgoblin chieftain":\n    template_id: hobgoblin\n    reason: closest match\n')
    assert not any(flag.startswith("monster_unresolved") for flag in area_report(draft, "1").flags)


def test_monster_override_unknown_name_lists_the_unresolved_names():
    overrides = parse_overrides('monsters:\n  "hobgoblin chef":\n    template_id: hobgoblin\n    reason: typo\n')
    with pytest.raises(OverrideError) as excinfo:
        apply_monster_overrides(synthetic_resolutions(), overrides)
    assert "hobgoblin chef" in str(excinfo.value)
    assert "hobgoblin chieftain" in str(excinfo.value)


def test_monster_override_keys_normalizing_together_are_contradictory():
    overrides = parse_overrides(
        "monsters:\n"
        '  "hobgoblin chieftain":\n    template_id: hobgoblin\n    reason: one\n'
        '  "Hobgoblin Chieftain":\n    template_id: orc\n    reason: two\n'
    )
    with pytest.raises(OverrideError, match="normalize"):
        apply_monster_overrides(synthetic_resolutions(), overrides)


# --------------------------------------------------------------- area field semantics


def test_area_field_absent_null_value_semantics():
    # Absent: every cache-derived value stands.
    draft = built()
    hall = area_spec(draft, "7")
    assert hall.name == "Hall"
    assert hall.description == "A great hall."
    assert hall.trap is not None and hall.trap.effect.manual == "A pit trap before the altar."
    assert hall.treasure is not None and hall.treasure.letters == ("A",)
    assert [feature.id for feature in hall.features] == ["7-f1", "7-treasure"]

    # Value: replaced; null: cleared.
    draft = built(
        "areas:\n"
        "  barrow/1/7:\n"
        "    name: Great Hall\n"
        "    description: null\n"
        "    trap: null\n"
        "    reason: field semantics\n"
    )
    hall = area_spec(draft, "7")
    assert hall.name == "Great Hall"
    assert hall.description == ""
    assert hall.trap is None
    assert area_report(draft, "7").overridden == ("name", "description", "trap")


def test_area_encounter_override_is_verbatim_and_suppresses_flags():
    draft = built(
        "areas:\n"
        "  barrow/1/1:\n"
        "    encounter:\n"
        "      monsters:\n"
        "        - template_id: orc\n"
        "          count_fixed: 3\n"
        "    reason: the chieftain is statted as an orc band\n"
    )
    guard_post = area_spec(draft, "1")
    assert guard_post.encounter is not None
    assert guard_post.encounter.monsters[0].template_id == "orc"
    assert guard_post.encounter.monsters[0].count_fixed == 3
    report = area_report(draft, "1")
    assert report.overridden == ("encounter",)
    assert not any(flag.startswith("monster_unresolved") for flag in report.flags)
    # The overridden area contributes no unresolved names.
    assert draft.unresolved == ()


def test_area_encounter_cleared_builds_no_encounter():
    draft = built("areas:\n  barrow/1/1:\n    encounter: null\n    reason: the room is empty\n")
    assert area_spec(draft, "1").encounter is None
    assert not any(flag.startswith("monster_unresolved") for flag in area_report(draft, "1").flags)


def test_trap_override_payload_is_verbatim():
    draft = built(
        "areas:\n"
        "  barrow/1/7:\n"
        "    trap:\n"
        "      kind: room\n"
        "      trigger: enter\n"
        "      affects: triggerer\n"
        "      effect:\n"
        "        damage_dice: 1d6\n"
        "        fall_feet: 10\n"
        "    reason: the pit is a real pit\n"
    )
    trap = area_spec(draft, "7").trap
    assert trap is not None
    assert trap.effect.damage_dice == "1d6"
    assert trap.effect.fall_feet == 10


# --------------------------------------------------------------- the two-slot treasure grammar


def test_treasure_override_controls_only_the_treasure_slot():
    draft = built("areas:\n  barrow/1/7:\n    treasure: {unguarded: true}\n    reason: replace the letters\n")
    hall = area_spec(draft, "7")
    assert hall.treasure is not None and hall.treasure.unguarded is True
    # The grammar still ran for the features slot: the parsed cache survives...
    assert [feature.id for feature in hall.features] == ["7-f1", "7-treasure"]
    # ...and the leftovers still describe strings the draft couldn't place.
    assert any(flag.startswith("treasure_unparsed:") for flag in area_report(draft, "7").flags)


def test_treasure_cleared_suppresses_the_unguarded_fallback():
    baseline = built()
    cell = area_spec(baseline, "8")
    assert cell.treasure is not None and cell.treasure.unguarded is True  # best-effort fallback fired
    draft = built("areas:\n  barrow/1/8:\n    treasure: null\n    reason: the dust is set dressing\n")
    assert area_spec(draft, "8").treasure is None
    # The grammar ran for the features slot; the leftover string still flags.
    assert any(flag.startswith("treasure_unparsed:") for flag in area_report(draft, "8").flags)


def test_features_override_replaces_the_final_tuple_wholesale():
    draft = built(
        "areas:\n"
        "  barrow/1/7:\n"
        "    features:\n"
        "      - {id: altar, kind: custom, description: A corrected altar}\n"
        "    reason: replace the features\n"
    )
    hall = area_spec(draft, "7")
    # The mapped -f features and the parsed -treasure cache are both gone.
    assert [feature.id for feature in hall.features] == ["altar"]
    # The grammar still ran for the treasure slot.
    assert hall.treasure is not None and hall.treasure.letters == ("A",)
    assert any(flag.startswith("treasure_unparsed:") for flag in area_report(draft, "7").flags)


def test_both_slots_overridden_skips_the_grammar_entirely():
    draft = built("areas:\n  barrow/1/7:\n    treasure: null\n    features: null\n    reason: both slots owned\n")
    hall = area_spec(draft, "7")
    assert hall.treasure is None
    assert hall.features == ()
    assert not any(flag.startswith("treasure_unparsed") for flag in area_report(draft, "7").flags)
    assert area_report(draft, "7").overridden == ("treasure", "features")


# --------------------------------------------------------------- add and remove


ADD_ENTRY = (
    "areas:\n"
    "  barrow/1/9:\n"
    "    name: Hidden Vault\n"
    "    description: A vault the extraction missed.\n"
    "    reason: present on the map, missed by extraction\n"
    "geometry:\n"
    "  barrow/1:\n"
    "    areas:\n"
    '      "9":\n'
    "        cells: [[20, 20], [21, 20]]\n"
    "    reason: place the vault\n"
)


def test_add_appends_after_the_survey_areas():
    draft = built(ADD_ENTRY)
    level = draft.adventure.dungeons[0].levels[0]
    assert [area.id for area in level.areas] == ["1", "7", "8", "9"]
    vault = area_spec(draft, "9")
    assert vault.name == "Hidden Vault"
    assert vault.cells == ((20, 20), (21, 20))
    assert level.width >= 22 and level.height >= 21
    report = area_report(draft, "9")
    assert report.overridden == ("added",)
    assert report.confidence == 1.0
    assert report.source_pages == ()
    assert report.flags == ()
    assert draft.area_reports[-1] is report


def test_add_missing_name_description_or_cells_is_an_error():
    incomplete = (
        "areas:\n"
        "  barrow/1/9:\n"
        "    description: A vault.\n"
        "    reason: no name\n"
        "geometry:\n"
        "  barrow/1:\n"
        '    areas: {"9": {cells: [[20, 20]]}}\n'
        "    reason: place\n"
    )
    assert "add" in plan_error(incomplete)
    no_cells = "areas:\n  barrow/1/9:\n    name: Vault\n    description: A vault.\n    reason: no cells\n"
    assert "cells" in plan_error(no_cells)


def test_remove_skips_the_spec_and_keeps_a_tombstone():
    draft = built("areas:\n  barrow/1/8:\n    remove: true\n    reason: a map-only artifact\n")
    level = draft.adventure.dungeons[0].levels[0]
    assert [area.id for area in level.areas] == ["1", "7"]
    tombstone = area_report(draft, "8")
    assert tombstone.overridden == ("removed",)
    assert tombstone.flags == ()
    assert tombstone.confidence == 0.8
    assert tombstone.source_pages == (4,)
    # Removal deletes content, not floor plan: the cells stay inside the bounds.
    baseline_level = built().adventure.dungeons[0].levels[0]
    assert level.width == baseline_level.width and level.height == baseline_level.height


def test_remove_contradictions_are_errors():
    assert "remove" in plan_error("areas:\n  barrow/1/8:\n    remove: true\n    name: X\n    reason: both\n")
    with_cells = (
        "areas:\n  barrow/1/8:\n    remove: true\n    reason: both\n"
        "geometry:\n  barrow/1:\n"
        '    areas: {"8": {cells: [[9, 9]]}}\n'
        "    reason: cells\n"
    )
    assert "cells" in plan_error(with_cells)
    assert "survey" in plan_error("areas:\n  barrow/1/9:\n    remove: true\n    reason: nothing there\n")


# --------------------------------------------------------------- geometry


@pytest.mark.parametrize(
    ("override_key", "canonical"),
    [("5,2:east", "6,2:west"), ("5,2:south", "5,3:north"), ("5,2:north", "5,2:north"), ("5,2:west", "5,2:west")],
)
def test_edge_keys_canonicalize_through_osrlib(override_key: str, canonical: str):
    draft = built(
        "geometry:\n"
        "  barrow/1:\n"
        "    edges:\n"
        f'      "{override_key}": {{kind: door, door: {{}}}}\n'
        "    reason: canonicalization table\n"
    )
    level = draft.adventure.dungeons[0].levels[0]
    assert level.edges[canonical].kind is EdgeKind.DOOR


def test_edge_keys_canonicalizing_together_are_an_error():
    colliding = (
        "geometry:\n"
        "  barrow/1:\n"
        "    edges:\n"
        '      "5,2:east": {kind: door, door: {}}\n'
        '      "6,2:west": {kind: wall}\n'
        "    reason: collision\n"
    )
    assert "canonicalize" in plan_error(colliding)


def test_wall_override_seals_a_synthesized_opening():
    baseline = built().adventure.dungeons[0].levels[0]
    assert baseline.edges["1,0:west"].kind is EdgeKind.OPEN  # within-room adjacency of area 1
    draft = built('geometry:\n  barrow/1:\n    edges:\n      "1,0:west": {kind: wall}\n    reason: seal the opening\n')
    assert draft.adventure.dungeons[0].levels[0].edges["1,0:west"].kind is EdgeKind.WALL


def test_entrance_and_transitions_replace_wholesale():
    draft = built(
        "geometry:\n"
        "  barrow/1:\n"
        "    entrance: [1, 1]\n"
        "    transitions:\n"
        "      - {kind: stairs_down, position: [0, 0], to_dungeon_id: barrow, to_level_number: 1,\n"
        "         to_position: [0, 1], to_facing: north}\n"
        "    reason: move the entrance and stairs\n"
    )
    level = draft.adventure.dungeons[0].levels[0]
    assert level.entrance == (1, 1)
    assert len(level.transitions) == 1
    assert level.transitions[0].kind == "stairs_down"


def test_transitions_cleared_to_empty():
    draft = built("geometry:\n  barrow/1:\n    transitions: null\n    reason: no stairs on this level\n")
    assert draft.adventure.dungeons[0].levels[0].transitions == ()


def test_cells_replacement_recomputes_the_bounds():
    draft = built('geometry:\n  barrow/1:\n    areas: {"7": {cells: [[10, 10], [10, 11]]}}\n    reason: bounds\n')
    level = draft.adventure.dungeons[0].levels[0]
    assert area_spec(draft, "7").cells == ((10, 10), (10, 11))
    assert level.width == 11
    assert level.height == 12


@pytest.mark.parametrize(
    "text",
    [
        'geometry:\n  barrow/1:\n    areas: {"7": {cells: [[-1, 0]]}}\n    reason: negative cell\n',
        "geometry:\n  barrow/1:\n    entrance: [-1, 0]\n    reason: negative entrance\n",
        (
            "geometry:\n  barrow/1:\n    transitions:\n"
            "      - {kind: chute, position: [0, 0], to_dungeon_id: barrow, to_level_number: 1,\n"
            "         to_position: [-2, 1], to_facing: north}\n"
            "    reason: negative transition\n"
        ),
    ],
)
def test_negative_coordinates_are_rejected(text: str):
    assert "negative" in plan_error(text)


# --------------------------------------------------------------- addressing and no-ops


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("areas:\n  elsewhere/1/7:\n    name: X\n    reason: bad dungeon\n", "no surveyed level"),
        ("areas:\n  barrow/2/7:\n    name: X\n    reason: bad level\n", "no surveyed level"),
        ("areas:\n  barrow/1/7:\n    reason: nothing set\n", "replaces nothing"),
        ("geometry:\n  barrow/2:\n    entrance: [0, 0]\n    reason: bad level\n", "no surveyed level"),
        ('geometry:\n  barrow/1:\n    areas: {"9": {cells: [[1, 1]]}}\n    reason: no add\n', "unknown area"),
        ("geometry:\n  barrow/1:\n    reason: nothing set\n", "replaces nothing"),
        ("town:\n  reason: nothing set\n", "replaces no fields"),
        ("module:\n  reason: nothing set\n", "replaces no fields"),
    ],
)
def test_entries_that_cannot_take_effect_are_errors(text: str, fragment: str):
    assert fragment in plan_error(text)


# --------------------------------------------------------------- town and module


def test_module_and_town_value_replacement():
    draft = built(
        "module:\n"
        "  name: Corrected Title\n"
        "  description: A corrected blurb.\n"
        "  hooks: [One hook only.]\n"
        "  reason: metadata pass\n"
        "town:\n"
        "  name: Newtown\n"
        "  description: Rebuilt.\n"
        "  services: [inn, smith]\n"
        "  travel_turns: {barrow: 12}\n"
        "  reason: metadata pass\n"
    )
    adventure = draft.adventure
    assert adventure.name == "Corrected Title"
    assert adventure.description == "A corrected blurb."
    assert adventure.hooks == ("One hook only.",)
    assert adventure.town.name == "Newtown"
    assert adventure.town.description == "Rebuilt."
    assert adventure.town.services == ("inn", "smith")
    assert adventure.town.travel_turns == {"barrow": 12}
    assert not any(flag.startswith("low_confidence") for flag in draft.module_flags)


def test_overridden_names_suppress_the_default_flags():
    empty_index = synthetic_index(title="", town_name="")
    baseline = built(index=empty_index)
    assert "low_confidence:module title unstated" in baseline.module_flags
    assert "low_confidence:town name unstated" in baseline.module_flags
    draft = built(
        "module:\n  name: Supplied Title\n  reason: title\ntown:\n  name: Supplied Town\n  reason: town\n",
        index=empty_index,
    )
    assert draft.adventure.name == "Supplied Title"
    assert draft.adventure.town.name == "Supplied Town"
    assert draft.module_flags == ()


def test_cleared_names_return_to_the_default_path():
    draft = built("module:\n  name: null\n  reason: clear it\ntown:\n  name: null\n  reason: clear it\n")
    assert draft.adventure.name == "Untitled module"
    assert draft.adventure.town.name == "Town"
    assert "low_confidence:module title unstated" in draft.module_flags
    assert "low_confidence:town name unstated" in draft.module_flags


# --------------------------------------------------------------- flags describe the build


def test_cells_override_drops_geometry_synthesized_and_marks_cells():
    draft = built('geometry:\n  barrow/1:\n    areas: {"7": {cells: [[4, 2], [5, 2]]}}\n    reason: cells\n')
    report = area_report(draft, "7")
    assert "geometry_synthesized" not in report.flags
    assert report.overridden == ("cells",)
    untouched = area_report(draft, "1")
    assert "geometry_synthesized" in untouched.flags
    assert untouched.overridden == ()


def test_connection_flags_survive_every_override():
    draft = built(
        "areas:\n"
        "  barrow/1/8:\n"
        "    description: Corrected.\n"
        "    encounter: null\n"
        "    treasure: null\n"
        "    features: null\n"
        "    reason: heavy correction\n"
        "geometry:\n"
        '  barrow/1:\n    areas: {"8": {cells: [[15, 15]]}}\n    reason: move it\n'
    )
    report = area_report(draft, "8")
    assert any(flag == "connection_ambiguous:unresolved target 99" for flag in report.flags)
