"""The map-reading stage and the reconciled pipeline paths into `report.json` — zero network.

The stage half runs over `ScriptedProvider` with a fabricated survey cache:
request shape, the skip/blank/degenerate/off behaviors, and the cache the
stage writes. The pipeline half fabricates a workdir whose hand-written
`stages/mapread.json` exercises the adopted-door, map-only-edge,
disputed-entrance, and dropped-proposal paths end to end through `assemble`
into `report.json` flags — and pins that `preview` follows the draft over a
workdir whose map cache carries adopted proposals (the second
`synthesize_geometry` call site).
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from osrlib.crawl.dungeon import EdgeKind

from conftest import ScriptedProvider, fabricate_workdir
from osrforge.assemble import assemble, render_previews
from osrforge.contracts.run import RunMeta, Stage, StageStatus
from osrforge.contracts.stages import (
    LevelContent,
    MapEdgeProposal,
    MapLevelReading,
    MapReading,
    MonsterResolutions,
    SurveyIndex,
)
from osrforge.mapread import build_mapread_request, mapread, mapread_schema
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, write_json_artifact

# --------------------------------------------------------------- stage behavior


def survey_index(levels: dict[int, tuple[list[str], list[int]]]) -> SurveyIndex:
    """A one-dungeon index: level number → (area keys, map pages)."""
    return SurveyIndex.model_validate(
        {
            "schema_version": 1,
            "title": "Mod",
            "hooks": [],
            "town": {"name": "Town", "description": ""},
            "dungeons": [
                {
                    "id": "lair",
                    "name": "The Lair",
                    "levels": [
                        {
                            "number": number,
                            "map_pages": map_pages,
                            "areas": [
                                {"key": key, "name": key, "source_label": None, "kind": "room", "source_pages": []}
                                for key in keys
                            ],
                        }
                        for number, (keys, map_pages) in levels.items()
                    ],
                }
            ],
            "monster_names": [],
        }
    )


def stage_workdir(
    root: Path,
    levels: dict[int, tuple[list[str], list[int]]],
    settings: ConversionSettings | None = None,
) -> Workdir:
    workdir = fabricate_workdir(root, page_count=5, settings=settings)
    run = workdir.read_run()
    stages = dict(run.stages)
    for stage in (Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS):
        stages[stage] = StageStatus(
            status="completed",
            started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC),
        )
    workdir.write_run(run.model_copy(update={"stages": stages}))
    workdir.stages_dir.mkdir(parents=True, exist_ok=True)
    write_json_artifact(workdir.survey_json, survey_index(levels))
    return workdir


def answer(adjacencies: list[tuple[str, str, str]], entrance: str | None = None) -> dict[str, object]:
    return {
        "adjacencies": [{"a": a, "b": b, "door": door} for a, b, door in adjacencies],
        "entrance": entrance,
    }


def test_the_request_carries_the_printed_key_list_and_the_map_pages(tmp_path: Path):
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1", "2"], [4])})
    provider = ScriptedProvider([answer([("1", "2", "none")])])
    mapread(workdir, provider)
    (request,) = provider.requests
    assert request.tag == "mapread.lair.1"
    header = request.parts[0]
    assert 'Read the level map for "The Lair", level 1' in getattr(header, "text", "")
    assert "- 1\n- 2" in getattr(header, "text", "")
    # One text+image part pair per sent page.
    assert len(request.parts) == 1 + 2
    assert request.schema == mapread_schema()


def test_the_cache_records_the_reading_and_the_run_records_usage(tmp_path: Path):
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1", "2"], [4])})
    provider = ScriptedProvider([answer([("1", "2", "door")], entrance="1")])
    cache = mapread(workdir, provider)
    assert cache == MapReading.model_validate_json(workdir.mapread_json.read_text(encoding="utf-8"))
    assert cache.map_reading == "read"
    (reading,) = cache.levels
    assert reading.status == "read"
    assert reading.map_pages == (4,)
    assert reading.proposals == (MapEdgeProposal(a="1", b="2", door="door"),)
    assert reading.entrance_key == "1"
    status = workdir.read_run().stages[Stage.MAPREAD]
    assert status.status == "completed"
    assert status.usage is not None and status.usage.input_tokens == 100


def test_keys_cache_as_answered_raw(tmp_path: Path):
    # Endpoint resolution is reconcile's job — a policy fix must never strand
    # the recorded fixture, so the cache keeps the model's own spellings.
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["4a", "5"], [4])})
    provider = ScriptedProvider([answer([("4A", "Room 5", "none")])])
    (reading,) = mapread(workdir, provider).levels
    assert reading.proposals == (MapEdgeProposal(a="4A", b="Room 5", door="none"),)


def test_no_map_pages_records_unread_without_a_request(tmp_path: Path):
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1", "2"], [])})
    provider = ScriptedProvider([])
    (reading,) = mapread(workdir, provider).levels
    assert provider.requests == []
    assert (reading.status, reading.unread_reason) == ("unread", "no_map_pages")
    assert reading.map_pages == ()


def test_fully_blanked_map_pages_record_unread_without_a_request(tmp_path: Path):
    settings = ConversionSettings(blank_page_renders=(4,))
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1", "2"], [4])}, settings=settings)
    provider = ScriptedProvider([])
    (reading,) = mapread(workdir, provider).levels
    assert provider.requests == []
    assert (reading.status, reading.unread_reason) == ("unread", "pages_blanked")


def test_blanked_pages_are_excluded_from_the_sent_set(tmp_path: Path):
    settings = ConversionSettings(blank_page_renders=(4,))
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1", "2"], [3, 4])}, settings=settings)
    provider = ScriptedProvider([answer([("1", "2", "none")])])
    (reading,) = mapread(workdir, provider).levels
    (request,) = provider.requests
    assert len(request.parts) == 1 + 2  # one page sent, not two
    assert reading.map_pages == (3,)
    assert reading.status == "read"


def test_a_level_with_no_keyed_areas_records_unread_without_a_request(tmp_path: Path):
    workdir = stage_workdir(tmp_path / "mod.forge", {1: ([], [4]), 2: (["9"], [5])})
    provider = ScriptedProvider([answer([], entrance="9")])
    first, second = mapread(workdir, provider).levels
    assert (first.status, first.unread_reason) == ("unread", "no_keyed_areas")
    assert second.status == "read"


def test_an_empty_reading_over_a_multi_area_level_records_unread(tmp_path: Path):
    # A degenerate read must not assert map silence against the whole level;
    # the answer stays raw (entrance included) and the status discards it.
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1", "2"], [4])})
    provider = ScriptedProvider([answer([], entrance="2")])
    (reading,) = mapread(workdir, provider).levels
    assert (reading.status, reading.unread_reason) == ("unread", "empty_reading")
    assert reading.entrance_key == "2"
    assert reading.map_pages == (4,)


def test_a_single_area_level_keeps_a_zero_pair_reading_and_its_entrance(tmp_path: Path):
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1"], [4])})
    provider = ScriptedProvider([answer([], entrance="1")])
    (reading,) = mapread(workdir, provider).levels
    assert reading.status == "read"
    assert reading.proposals == ()
    assert reading.entrance_key == "1"


def test_map_reading_off_writes_the_echoed_cache_with_zero_levels(tmp_path: Path):
    settings = ConversionSettings(map_reading="off")
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1", "2"], [4])}, settings=settings)
    provider = ScriptedProvider([])
    cache = mapread(workdir, provider)
    assert provider.requests == []
    assert cache.map_reading == "off"
    assert cache.levels == ()
    assert workdir.read_run().stages[Stage.MAPREAD].status == "completed"


def test_mapread_requires_a_completed_monsters_stage(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
    with pytest.raises(ValueError, match="completed monsters stage"):
        mapread(workdir, ScriptedProvider([]))


def test_mapread_requires_the_survey_cache(tmp_path: Path):
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1"], [4])})
    workdir.survey_json.unlink()
    with pytest.raises(ValueError, match="survey cache is missing"):
        mapread(workdir, ScriptedProvider([]))


def test_the_request_lists_printed_forms_via_source_label(tmp_path: Path):
    workdir = stage_workdir(tmp_path / "mod.forge", {1: (["1", "2"], [4])})
    index = SurveyIndex.model_validate_json(workdir.survey_json.read_text(encoding="utf-8"))
    level = index.dungeons[0].levels[0]
    relabeled = level.model_copy(
        update={"areas": (level.areas[0].model_copy(update={"source_label": "1A"}), level.areas[1])}
    )
    request = build_mapread_request("lair", "The Lair", relabeled, (workdir_parts := _parts(workdir)))
    assert "- 1A\n- 2" in getattr(request.parts[0], "text", "")
    assert len(request.parts) == 1 + len(workdir_parts)


def _parts(workdir: Workdir):
    from osrforge.pages import page_request_parts

    return page_request_parts(workdir, (4,))


def test_contract_rejects_a_reason_on_a_read_level():
    with pytest.raises(ValueError, match="unread_reason"):
        MapLevelReading.model_validate(
            {"dungeon_id": "lair", "level_number": 1, "map_pages": [], "status": "read", "unread_reason": "x"}
        )
    with pytest.raises(ValueError, match="unread_reason"):
        MapLevelReading.model_validate({"dungeon_id": "lair", "level_number": 1, "map_pages": [], "status": "unread"})


# --------------------------------------------------------------- pipeline paths


def synthetic_map_workdir(root: Path, map_cache: MapReading | None, mapread_status: str = "completed") -> Workdir:
    """A fabricated two-level workdir whose caches exercise every reconciliation path.

    Level 1: areas 1, 2, 3; prose states only 1→2 (a passage). Level 2: area
    9, no connections. The map cache is the test's own hand-written input —
    the pipeline half tests assembly's consumption, not the stage.
    """
    workdir = Workdir(root)
    workdir.stages_dir.mkdir(parents=True)
    write_json_artifact(workdir.survey_json, survey_index({1: (["1", "2", "3"], [4]), 2: (["9"], [5])}))
    for number, areas in ((1, ["1", "2", "3"]), (2, ["9"])):
        write_json_artifact(
            workdir.areas_json("lair", number),
            LevelContent.model_validate(
                {
                    "schema_version": 1,
                    "dungeon_id": "lair",
                    "level_number": number,
                    "areas": [
                        {
                            "key": key,
                            "description": "",
                            "encounters": [],
                            "trap": None,
                            "treasure": [],
                            "features": [],
                            "connections": (
                                [{"to_key": "2", "direction": "east"}] if key == "1" and number == 1 else []
                            ),
                            "source_pages": [],
                            "confidence": 0.9,
                        }
                        for key in areas
                    ],
                }
            ),
        )
    write_json_artifact(workdir.monsters_json, MonsterResolutions(resolutions={}))
    if map_cache is not None:
        write_json_artifact(workdir.mapread_json, map_cache)
    stages = {stage: StageStatus() for stage in Stage}
    for stage in (Stage.PREPROCESS, Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS):
        stages[stage] = StageStatus(
            status="completed",
            started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC),
        )
    stages[Stage.MAPREAD] = StageStatus(
        status=mapread_status,  # type: ignore[arg-type]
        started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC) if mapread_status != "pending" else None,
        finished_at=datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC) if mapread_status == "completed" else None,
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
    return workdir


FULL_CACHE = MapReading(
    map_reading="read",
    levels=(
        MapLevelReading(
            dungeon_id="lair",
            level_number=1,
            map_pages=(4,),
            status="read",
            proposals=(
                MapEdgeProposal(a="1", b="2", door="door"),  # adopted onto the prose passage
                MapEdgeProposal(a="2", b="3", door="none"),  # map-only edge
                MapEdgeProposal(a="1", b="x9", door="door"),  # unresolvable endpoint → dropped
            ),
            entrance_key="2",  # disputes the survey-order pick "1"
        ),
        MapLevelReading(dungeon_id="lair", level_number=2, map_pages=(), status="unread", unread_reason="no_map_pages"),
    ),
)


def area_flags(report, address: str) -> tuple[str, ...]:
    return next(area for area in report.areas if area.id == address).flags


def test_every_reconciliation_path_lands_in_report_json(tmp_path: Path):
    workdir = synthetic_map_workdir(tmp_path / "mod.forge", FULL_CACHE)
    result = assemble(workdir.root)
    report = result.report

    # The adopted door: flagged on the survey-order-first endpoint, realized
    # as a door edge in the draft.
    assert "map_disputed:door 1–2 map-read door; prose states no mechanism — adopted" in area_flags(  # noqa: RUF001
        report, "lair/1/1"
    )
    level = result.adventure.dungeons[0].levels[0]
    assert any(edge.kind is EdgeKind.DOOR for edge in level.edges.values())

    # The map-only edge: flagged on its survey-order-first endpoint; area 3
    # joins the graph through it (no synthetic-component flag on 3).
    assert "map_disputed:edge 2–3 map-proposed; prose states no connection — adopted" in area_flags(  # noqa: RUF001
        report, "lair/1/2"
    )
    assert not any("not connected to the entrance" in flag for flag in area_flags(report, "lair/1/3"))

    # The disputed entrance: the map pick wins, flagged on the *selected* area.
    assert "map_disputed:entrance map 2, survey-order 1" in area_flags(report, "lair/1/2")
    area_2 = next(area for area in level.areas if area.id == "2")
    assert level.entrance in area_2.cells

    # The dropped proposal: module scope, level address in the detail.
    assert "map_disputed:lair/1: map names 'x9'; the survey does not" in report.flags

    # The unread level asserts nothing.
    assert not any("map_disputed" in flag for flag in area_flags(report, "lair/2/9"))


def test_preview_follows_the_draft_over_adopted_proposals(tmp_path: Path):
    # The second synthesize_geometry call site: `preview` re-synthesizes with
    # the map cache threaded, so its bytes match assembly's previews exactly.
    workdir = synthetic_map_workdir(tmp_path / "mod.forge", FULL_CACHE)
    assemble(workdir.root)
    assembled = {path.name: path.read_bytes() for path in sorted(workdir.previews_dir.iterdir())}
    written = render_previews(workdir.root)
    regenerated = {path.name: path.read_bytes() for path in sorted(workdir.previews_dir.iterdir())}
    assert regenerated == assembled
    assert [path.name for path in written] == ["lair.1.svg", "lair.2.svg", "index.html"]


def test_a_missing_cache_with_a_pending_stage_assembles_prose_only(tmp_path: Path):
    workdir = synthetic_map_workdir(tmp_path / "mod.forge", None, mapread_status="pending")
    result = assemble(workdir.root)
    assert not any("map_disputed" in flag for area in result.report.areas for flag in area.flags)
    assert not any("map_disputed" in flag for flag in result.report.flags)


def test_a_missing_run_json_entry_assembles_prose_only(tmp_path: Path):
    # The pre-phase-11 workdir shape: no mapread key in run.json at all.
    workdir = synthetic_map_workdir(tmp_path / "mod.forge", None, mapread_status="pending")
    run = workdir.read_run()
    workdir.write_run(
        run.model_copy(update={"stages": {k: v for k, v in run.stages.items() if k is not Stage.MAPREAD}})
    )
    result = assemble(workdir.root)
    assert result.report.validation.passed


@pytest.mark.parametrize("status", ["failed", "running"])
def test_a_failed_or_running_mapread_stage_fails_assembly_loudly(tmp_path: Path, status: str):
    workdir = synthetic_map_workdir(tmp_path / "mod.forge", None, mapread_status=status)
    with pytest.raises(ValueError, match="rerun mapread"):
        assemble(workdir.root)


def test_an_off_cache_reconciles_as_no_proposals(tmp_path: Path):
    workdir = synthetic_map_workdir(tmp_path / "mod.forge", MapReading(map_reading="off", levels=()))
    result = assemble(workdir.root)
    assert not any("map_disputed" in flag for area in result.report.areas for flag in area.flags)
