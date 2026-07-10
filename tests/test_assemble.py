"""Assembly: content mapping, the unresolved fallback, flags, the report, and stage choreography."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from osrlib.core.tables import MonsterEncounterEntry
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, KeyedEncounter, KeyedMonster, LevelSpec
from osrlib.data import load_encounter_tables
from osrlib.versioning import check_document

from conftest import fabricate_workdir
from osrforge.assemble import _run_validation, assemble, build_draft, parse_treasure
from osrforge.contracts.run import Stage, StageStatus, TokenUsage
from osrforge.contracts.stages import LevelContent, MonsterResolution, MonsterResolutions, SurveyIndex
from osrforge.geometry import synthesize_geometry
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, write_json_artifact

TABLES = load_encounter_tables()


def make_index(
    areas: list[str], title: str = "The Example Barrow", town_name: str = "Threshold", level_number: int = 1
) -> SurveyIndex:
    return SurveyIndex.model_validate(
        {
            "schema_version": 1,
            "title": title,
            "hooks": ["A rumor."],
            "town": {"name": town_name, "description": "A town."},
            "dungeons": [
                {
                    "id": "lair",
                    "name": "The Lair",
                    "levels": [
                        {
                            "number": level_number,
                            "map_pages": [],
                            "areas": [
                                {
                                    "key": key,
                                    "name": f"Area {key}",
                                    "source_label": None,
                                    "kind": "room",
                                    "source_pages": [7],
                                }
                                for key in areas
                            ],
                        }
                    ],
                }
            ],
            "monster_names": [],
        }
    )


def make_area(key: str, **overrides: Any) -> dict[str, Any]:
    area: dict[str, Any] = {
        "key": key,
        "description": "A bare room.",
        "encounters": [],
        "trap": None,
        "treasure": [],
        "features": [],
        "connections": [],
        "source_pages": [7],
        "confidence": 0.9,
    }
    area.update(overrides)
    return area


def make_level(areas: list[dict[str, Any]], level_number: int = 1) -> LevelContent:
    return LevelContent.model_validate(
        {"schema_version": 1, "dungeon_id": "lair", "level_number": level_number, "areas": areas}
    )


def encounter(monster: str, fixed: int | None = None, dice: str | None = None, note: str | None = None):
    return {"monster": monster, "count_fixed": fixed, "count_dice": dice, "count_note": note}


def resolutions_for(**names: str | None) -> MonsterResolutions:
    resolutions = {}
    for name, template_id in names.items():
        key = name.replace("_", " ")
        if template_id is None:
            resolutions[key] = MonsterResolution(template_id=None, method="unresolved")
        else:
            resolutions[key] = MonsterResolution(template_id=template_id, method="exact")
    return MonsterResolutions(resolutions=resolutions)


def draft(
    areas: list[dict[str, Any]],
    resolutions: MonsterResolutions | None = None,
    fallback: str = "best-effort",
    level_number: int = 1,
    survey_keys: list[str] | None = None,
    **index_overrides: Any,
):
    keys = survey_keys if survey_keys is not None else [area["key"] for area in areas]
    index = make_index(keys, level_number=level_number, **index_overrides)
    levels = (make_level(areas, level_number=level_number),)
    geometries = synthesize_geometry(index, levels)
    settings = ConversionSettings(unresolved_fallback=fallback)  # type: ignore[arg-type]
    return build_draft(index, levels, resolutions or MonsterResolutions(resolutions={}), geometries, settings)


def area_spec(result, key: str) -> AreaSpec:
    (dungeon,) = result.adventure.dungeons
    (level,) = dungeon.levels
    return next(area for area in level.areas if area.id == key)


def area_report(result, key: str):
    return next(report for report in result.area_reports if report.id.endswith(f"/{key}"))


def keyed_monsters(result, key: str) -> tuple[KeyedMonster, ...]:
    keyed_encounter = area_spec(result, key).encounter
    assert keyed_encounter is not None
    return keyed_encounter.monsters


class TestCounts:
    def test_dice_preferred_when_both_set(self):
        result = draft(
            [make_area("1", encounters=[encounter("goblin", fixed=6, dice="2d4")])], resolutions_for(goblin="goblin")
        )
        (keyed,) = keyed_monsters(result, "1")
        assert keyed.count_dice == "2d4"
        assert keyed.count_fixed is None

    def test_unparseable_dice_demoted_with_flag(self):
        # Unreachable through the extraction schema's DICE_PATTERN — hand-built
        # cache, defense in depth.
        result = draft(
            [make_area("1", encounters=[encounter("goblin", fixed=3, dice="2d7")])], resolutions_for(goblin="goblin")
        )
        (keyed,) = keyed_monsters(result, "1")
        assert keyed.count_dice is None
        assert keyed.count_fixed == 3
        assert "low_confidence:unparseable count for goblin" in area_report(result, "1").flags

    def test_unparseable_dice_without_fixed_falls_to_the_neither_rule(self):
        result = draft(
            [make_area("1", encounters=[encounter("goblin", dice="1d6+1000000")])], resolutions_for(goblin="goblin")
        )
        (keyed,) = keyed_monsters(result, "1")
        assert keyed.count_fixed == 1
        flags = area_report(result, "1").flags
        assert "low_confidence:unparseable count for goblin" in flags
        assert "low_confidence:count unstated for goblin" in flags

    def test_neither_set_defaults_to_one_with_flag(self):
        result = draft(
            [make_area("1", encounters=[encounter("goblin", note="a warband")])], resolutions_for(goblin="goblin")
        )
        (keyed,) = keyed_monsters(result, "1")
        assert keyed.count_fixed == 1
        assert "low_confidence:count unstated for goblin" in area_report(result, "1").flags

    def test_fixed_only(self):
        result = draft([make_area("1", encounters=[encounter("goblin", fixed=4)])], resolutions_for(goblin="goblin"))
        (keyed,) = keyed_monsters(result, "1")
        assert keyed.count_fixed == 4


class TestTreasureGrammar:
    @pytest.mark.parametrize(
        ("text", "field", "expected"),
        [
            # The real minimod cache strings:
            ("120 sp", "coins", {"sp": 120}),
            ("silver locket worth 25 gp", "valuable", ("jewellery", "silver locket", 25)),
            ("pot of 200 gp", "coins", {"gp": 200}),
            ("potion of healing", "unparsed", "potion of healing"),
            # The real JN1 cache string:
            ("Each orc has 1d6 sp.", "unparsed", "Each orc has 1d6 sp."),
            # Grammar coverage:
            ("2d6 gp per guard", "unparsed", "2d6 gp per guard"),
            ("a gem worth 100 gp", "valuable", ("gem", "gem", 100)),
            ("The large diamond worth 500 gp", "valuable", ("jewellery", "large diamond", 500)),
            ("30 gp and 200 sp", "coins", {"gp": 30, "sp": 200}),
            ("3 chests holding 50 gp", "unparsed", "3 chests holding 50 gp"),
            ("treasure type A", "letters", "A"),
            ("Treasure Type c.", "letters", "C"),
            ("a map to the hoard", "unparsed", "a map to the hoard"),
        ],
    )
    def test_grammar_table(self, text: str, field: str, expected: Any):
        parsed = parse_treasure((text,))
        if field == "coins":
            for denomination, amount in expected.items():
                assert getattr(parsed.coins, denomination) == amount
            assert not parsed.unparsed and not parsed.valuables and not parsed.letters
        elif field == "valuable":
            kind, name, value = expected
            (valuable,) = parsed.valuables
            assert (valuable.kind, valuable.name, valuable.value_gp) == (kind, name, value)
        elif field == "letters":
            assert parsed.letters == (expected,)
        else:
            assert parsed.unparsed == (expected,)

    def test_parsed_pieces_aggregate_into_one_cache_feature(self):
        result = draft([make_area("3", treasure=["120 sp", "silver locket worth 25 gp"])])
        spec = area_spec(result, "3")
        cache = next(feature for feature in spec.features if feature.id == "3-treasure")
        assert cache.kind == "treasure_cache"
        assert cache.coins.sp == 120
        assert cache.valuables[0].name == "silver locket"
        assert spec.treasure is None

    def test_letters_produce_area_treasure(self):
        result = draft([make_area("1", treasure=["treasure type B"])])
        spec = area_spec(result, "1")
        assert spec.treasure is not None
        assert spec.treasure.letters == ("B",)
        assert spec.treasure.unguarded is False

    def test_unparsed_under_best_effort_rolls_unguarded(self):
        result = draft([make_area("6", treasure=["potion of healing"])])
        spec = area_spec(result, "6")
        assert spec.treasure is not None
        assert spec.treasure.unguarded is True
        assert "treasure_unparsed:potion of healing" in area_report(result, "6").flags

    def test_letters_win_over_the_unguarded_fallback(self):
        result = draft([make_area("1", treasure=["treasure type A", "potion of healing"])])
        spec = area_spec(result, "1")
        assert spec.treasure is not None
        assert spec.treasure.letters == ("A",)
        assert spec.treasure.unguarded is False
        assert "treasure_unparsed:potion of healing" in area_report(result, "1").flags

    def test_omit_never_emits_unguarded(self):
        result = draft([make_area("6", treasure=["potion of healing"])], fallback="omit")
        spec = area_spec(result, "6")
        assert spec.treasure is None
        assert "treasure_unparsed:potion of healing" in area_report(result, "6").flags


class TestTrapsAndFeatures:
    def test_trap_maps_to_manual_room_trap(self):
        result = draft([make_area("1", trap="A poisoned needle in the lock.")])
        trap = area_spec(result, "1").trap
        assert trap is not None
        assert trap.kind == "room"
        assert trap.trigger == "enter"
        assert trap.affects == "triggerer"
        assert trap.effect.manual == "A poisoned needle in the lock."

    def test_features_map_to_custom_specs_with_stable_ids(self):
        result = draft([make_area("2", features=["an altar", "a lever"])])
        features = area_spec(result, "2").features
        assert [(feature.id, feature.kind, feature.description) for feature in features] == [
            ("2-f1", "custom", "an altar"),
            ("2-f2", "custom", "a lever"),
        ]
        assert all(feature.cell is None for feature in features)


class TestUnresolvedFallback:
    def test_best_effort_substitutes_a_flagged_level_band_stand_in(self):
        result = draft(
            [make_area("1", encounters=[encounter("gray jelly", fixed=1)])], resolutions_for(**{"gray_jelly": None})
        )
        (keyed,) = keyed_monsters(result, "1")
        entries = [row.entry for row in TABLES.for_level(1).rows if isinstance(row.entry, MonsterEncounterEntry)]
        index = int.from_bytes(hashlib.sha256(b"gray jelly").digest()[:8], "big") % len(entries)
        expected = entries[index].monster_ids[0]
        assert keyed.template_id == expected
        assert f"monster_unresolved:gray jelly → {expected}" in area_report(result, "1").flags
        assert result.unresolved == ("gray jelly",)

    def test_stand_in_keeps_the_extracted_count(self):
        result = draft(
            [make_area("1", encounters=[encounter("gray jelly", dice="2d4")])], resolutions_for(**{"gray_jelly": None})
        )
        (keyed,) = keyed_monsters(result, "1")
        assert keyed.count_dice == "2d4"

    def test_stand_in_band_follows_the_level_number(self):
        # A level ≥ 8 lands in the open 8+ band.
        result = draft(
            [make_area("1", encounters=[encounter("gray jelly", fixed=1)])],
            resolutions_for(**{"gray_jelly": None}),
            level_number=9,
        )
        (keyed,) = keyed_monsters(result, "1")
        entries = [row.entry for row in TABLES.for_level(9).rows if isinstance(row.entry, MonsterEncounterEntry)]
        index = int.from_bytes(hashlib.sha256(b"gray jelly").digest()[:8], "big") % len(entries)
        assert keyed.template_id == entries[index].monster_ids[0]
        assert TABLES.for_level(9).max_level is None

    def test_npc_party_rows_are_skipped(self):
        # Every candidate the picker can return is a monster entry; the level-3
        # table has an npc_party row, so the modulus is 19, not 20.
        table = TABLES.for_level(3)
        entries = [row.entry for row in table.rows if isinstance(row.entry, MonsterEncounterEntry)]
        assert len(entries) == 19
        assert len(table.rows) == 20

    def test_packed_pool_takes_the_first_id(self):
        # A stand-in landing on a packed-variant row (Veteran over
        # veteran_1..3) takes the pool's first id — in the shipped data,
        # each pool's lowest variant.
        from osrforge.assemble import _stand_in_template

        table = TABLES.for_level(2)
        entries = [row.entry for row in table.rows if isinstance(row.entry, MonsterEncounterEntry)]
        packed = next(index for index, entry in enumerate(entries) if len(entry.monster_ids) > 1)
        name = next(
            f"name-{salt}"
            for salt in range(10_000)
            if int.from_bytes(hashlib.sha256(f"name-{salt}".encode()).digest()[:8], "big") % len(entries) == packed
        )
        assert _stand_in_template(name, table) == entries[packed].monster_ids[0]
        assert entries[packed].monster_ids[0] == "veteran_1"

    def test_hash_pick_is_stable_for_a_fixed_name(self):
        first = draft(
            [make_area("1", encounters=[encounter("gray jelly", fixed=1)])], resolutions_for(**{"gray_jelly": None})
        )
        second = draft(
            [make_area("1", encounters=[encounter("gray jelly", fixed=1)])], resolutions_for(**{"gray_jelly": None})
        )
        assert area_spec(first, "1").encounter == area_spec(second, "1").encounter

    def test_omit_drops_the_monster_and_flags_without_the_arrow(self):
        result = draft(
            [make_area("1", encounters=[encounter("gray jelly", fixed=1), encounter("goblin", fixed=2)])],
            resolutions_for(goblin="goblin", **{"gray_jelly": None}),
            fallback="omit",
        )
        (keyed,) = keyed_monsters(result, "1")
        assert keyed.template_id == "goblin"
        assert "monster_unresolved:gray jelly" in area_report(result, "1").flags

    def test_omit_with_every_encounter_unresolved_yields_no_encounter(self):
        result = draft(
            [make_area("1", encounters=[encounter("gray jelly", fixed=1)])],
            resolutions_for(**{"gray_jelly": None}),
            fallback="omit",
        )
        assert area_spec(result, "1").encounter is None
        assert result.unresolved == ("gray jelly",)

    def test_missing_resolution_is_a_stale_cache(self):
        with pytest.raises(ValueError, match="stale"):
            draft([make_area("1", encounters=[encounter("goblin", fixed=1)])])


class TestPlaceholdersAndMerge:
    def test_missing_area_becomes_a_placeholder(self):
        result = draft([make_area("1")], survey_keys=["1", "2"])
        placeholder = area_spec(result, "2")
        assert placeholder.description == ""
        assert placeholder.encounter is None and placeholder.trap is None
        assert placeholder.features == () and placeholder.treasure is None
        report = area_report(result, "2")
        assert report.confidence == 0.0
        assert "low_confidence:not extracted" in report.flags
        assert report.source_pages == (7,)  # the survey's pages

    def test_multiple_encounters_merge_into_one_keyed_encounter(self):
        result = draft(
            [make_area("1", encounters=[encounter("goblin", fixed=2), encounter("hobgoblin", fixed=1)])],
            resolutions_for(goblin="goblin", hobgoblin="hobgoblin"),
        )
        spec = area_spec(result, "1")
        assert isinstance(spec.encounter, KeyedEncounter)
        assert [keyed.template_id for keyed in keyed_monsters(result, "1")] == ["goblin", "hobgoblin"]


class TestFlags:
    def test_pinned_emission_order(self):
        result = draft(
            [
                make_area(
                    "1",
                    encounters=[encounter("goblin"), encounter("gray jelly", fixed=1)],
                    treasure=["potion of healing"],
                    connections=[{"to_key": "nowhere", "direction": "east"}, {"to_key": "2", "direction": "unknown"}],
                ),
                make_area("2"),
            ],
            resolutions_for(goblin="goblin", **{"gray_jelly": None}),
        )
        flags = area_report(result, "1").flags
        assert flags[0] == "geometry_synthesized"
        prefixes = [flag.split(":")[0] for flag in flags]
        assert prefixes == [
            "geometry_synthesized",
            "low_confidence",
            "monster_unresolved",
            "connection_ambiguous",
            "connection_ambiguous",
            "treasure_unparsed",
        ]
        assert "connection_ambiguous:unresolved target nowhere" in flags
        assert "connection_ambiguous:unknown direction to 2" in flags

    def test_every_area_carries_geometry_synthesized(self):
        result = draft([make_area("1")], survey_keys=["1", "2"])
        for report in result.area_reports:
            assert report.flags[0] == "geometry_synthesized"

    def test_disconnected_component_flag_detail(self):
        result = draft([make_area("1"), make_area("2")])
        flags = area_report(result, "2").flags
        assert "connection_ambiguous:not connected to the entrance in the extracted graph" in flags


class TestModuleDefaults:
    def test_defaulted_title_and_town_are_flagged(self):
        result = draft([make_area("1")], title="", town_name="")
        assert result.adventure.name == "Untitled module"
        assert result.adventure.town.name == "Town"
        assert result.module_flags == (
            "low_confidence:module title unstated",
            "low_confidence:town name unstated",
        )

    def test_stated_names_pass_through_unflagged(self):
        result = draft([make_area("1")])
        assert result.adventure.name == "The Example Barrow"
        assert result.adventure.town.name == "Threshold"
        assert result.module_flags == ()
        assert result.adventure.description == ""
        assert result.adventure.hooks == ("A rumor.",)


def test_validation_result_maps_findings_with_the_header_stripped():
    broken = Adventure(
        name="Broken",
        town=TownSpec(name="Town"),
        dungeons=(
            DungeonSpec(
                id="lair",
                levels=(
                    LevelSpec(
                        number=1,
                        width=2,
                        height=2,
                        entrance=(0, 0),
                        areas=(
                            AreaSpec(
                                id="1",
                                cells=((0, 0),),
                                encounter=KeyedEncounter(
                                    monsters=(KeyedMonster(template_id="no_such_monster", count_fixed=1),)
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    result = _run_validation(broken)
    assert result.passed is False
    assert len(result.errors) == 1
    assert "no_such_monster" in result.errors[0]
    assert not result.errors[0].startswith("adventure validation failed")


def assembled_workdir(root: Path, fallback: str = "best-effort") -> Workdir:
    settings = ConversionSettings(unresolved_fallback=fallback)  # type: ignore[arg-type]
    workdir = fabricate_workdir(root, page_count=3, settings=settings)
    run = workdir.read_run()
    run = run.with_stage(
        Stage.SURVEY, StageStatus(status="completed", usage=TokenUsage(input_tokens=100, output_tokens=10))
    )
    run = run.with_stage(
        Stage.CONTENT, StageStatus(status="completed", usage=TokenUsage(input_tokens=200, output_tokens=20))
    )
    run = run.with_stage(
        Stage.MONSTERS, StageStatus(status="completed", usage=TokenUsage(input_tokens=30, output_tokens=3))
    )
    workdir.write_run(run)
    workdir.stages_dir.mkdir(parents=True, exist_ok=True)
    write_json_artifact(workdir.survey_json, make_index(["1"], title="", town_name=""))
    write_json_artifact(
        workdir.areas_json("lair", 1),
        make_level([make_area("1", encounters=[encounter("goblin", fixed=2)])]),
    )
    write_json_artifact(workdir.monsters_json, resolutions_for(goblin="goblin"))
    return workdir


class TestAssembleStage:
    def test_artifacts_report_and_run_json(self, tmp_path: Path):
        workdir = assembled_workdir(tmp_path / "mod.forge")
        result = assemble(workdir.root)
        assert result.report.validation.passed is True
        assert result.report.usage == TokenUsage(input_tokens=330, output_tokens=33)
        assert result.report.module.title == ""  # the report records facts; defaults live in the adventure
        assert result.report.module.pages == 3
        assert result.report.monsters.resolved == 1
        assert result.report.flags == (
            "low_confidence:module title unstated",
            "low_confidence:town name unstated",
        )
        run = workdir.read_run()
        assert run.stages[Stage.GEOMETRY].status == "completed"
        assert run.stages[Stage.ASSEMBLE].status == "completed"
        assert run.stages[Stage.ASSEMBLE].usage == TokenUsage()
        assert run.provider is None  # no provider identity change from deterministic stages
        document = json.loads(workdir.adventure_json.read_text(encoding="utf-8"))
        payload = check_document(document, "adventure")
        assert Adventure.model_validate(payload) == result.adventure
        assert workdir.report_json.is_file()
        assert workdir.preview_svg("lair", 1).is_file()

    def test_requires_completed_monsters(self, tmp_path: Path):
        workdir = assembled_workdir(tmp_path / "mod.forge")
        run = workdir.read_run()
        workdir.write_run(run.with_stage(Stage.MONSTERS, StageStatus(status="pending")))
        with pytest.raises(ValueError, match="monsters"):
            assemble(workdir.root)

    def test_stale_monsters_cache_is_rejected_before_any_tracking(self, tmp_path: Path):
        workdir = assembled_workdir(tmp_path / "mod.forge")
        write_json_artifact(workdir.monsters_json, MonsterResolutions(resolutions={}))
        with pytest.raises(ValueError, match="stale"):
            assemble(workdir.root)
        run = workdir.read_run()
        assert run.stages[Stage.GEOMETRY].status == "pending"
        assert run.stages[Stage.ASSEMBLE].status == "pending"

    def test_missing_level_cache_is_misuse(self, tmp_path: Path):
        workdir = assembled_workdir(tmp_path / "mod.forge")
        workdir.areas_json("lair", 1).unlink()
        with pytest.raises(ValueError, match="content cache is missing"):
            assemble(workdir.root)
