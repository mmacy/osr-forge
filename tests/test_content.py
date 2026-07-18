import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import ScriptedProvider, fabricate_workdir
from osrforge.content import ContentBatch, build_batch_request, content, plan_content_batches
from osrforge.contracts.run import Stage, StageStatus
from osrforge.contracts.stages import LevelContent, SurveyArea, SurveyDungeon, SurveyIndex, SurveyLevel, TownInfo
from osrforge.errors import ProviderError
from osrforge.pages import page_request_parts
from osrforge.providers.base import ModelRequest, TextPart
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, write_json_artifact


def make_area(key: str, pages: tuple[int, ...], label: str | None = None) -> SurveyArea:
    return SurveyArea(key=key, name=f"Area {key}", source_label=label, kind="room", source_pages=pages)


def make_index(levels: dict[int, tuple[SurveyArea, ...]], map_pages: tuple[int, ...] = ()) -> SurveyIndex:
    return SurveyIndex(
        title="Test Module",
        hooks=(),
        town=TownInfo(name="", description=""),
        dungeons=(
            SurveyDungeon(
                id="lair",
                name="The Lair",
                levels=tuple(
                    SurveyLevel(number=number, map_pages=map_pages, areas=areas) for number, areas in levels.items()
                ),
            ),
        ),
        monster_names=(),
    )


def key_enum(schema: dict) -> list[str]:
    return schema["properties"]["areas"]["items"]["properties"]["key"]["enum"]


def header_text(request: ModelRequest) -> str:
    part = request.parts[0]
    assert isinstance(part, TextPart)
    return part.text


def area_payload(key: str, **overrides) -> dict:
    payload = {
        "key": key,
        "description": f"The description of area {key}.",
        "encounters": [],
        "trap": None,
        "treasure": [],
        "features": [],
        "connections": [],
        "source_pages": [1],
        "confidence": 0.9,
    }
    return payload | overrides


def batch_payload(*keys: str, **overrides) -> dict:
    return {"areas": [area_payload(key, **overrides) for key in keys]}


def prepare_content_workdir(
    root: Path, index: SurveyIndex, page_count: int, settings: ConversionSettings | None = None
) -> Workdir:
    workdir = fabricate_workdir(root, page_count, settings)
    run = workdir.read_run()
    completed = StageStatus(
        status="completed",
        started_at=datetime(2026, 7, 9, 12, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 9, 12, 2, 0, tzinfo=UTC),
    )
    workdir.write_run(run.with_stage(Stage.SURVEY, completed))
    workdir.stages_dir.mkdir()
    write_json_artifact(workdir.survey_json, index)
    return workdir


class TestBatchPlanning:
    def test_single_window_when_pages_fit(self):
        index = make_index({1: (make_area("1", (3, 4)), make_area("2", (5,)))})
        (plan,) = plan_content_batches(index, batch_pages=8)
        (batch,) = plan.batches
        assert batch.pages == (3, 4, 5)
        assert [area.key for area in batch.areas] == ["1", "2"]
        assert batch.tag == "content.lair.1.b01"

    def test_windows_share_exactly_one_page(self):
        areas = tuple(make_area(str(page), (page,)) for page in range(1, 11))
        index = make_index({1: areas})
        (plan,) = plan_content_batches(index, batch_pages=8)
        first, second = plan.batches
        assert first.pages == (1, 2, 3, 4, 5, 6, 7, 8)
        assert second.pages == (8, 9, 10)
        assert set(first.pages) & set(second.pages) == {8}
        assert second.tag == "content.lair.1.b02"

    def test_sparse_page_sets_window_like_dense_ones(self):
        areas = (make_area("1", (3,)), make_area("2", (7,)), make_area("3", (20,)), make_area("4", (21,)))
        index = make_index({1: areas})
        (plan,) = plan_content_batches(index, batch_pages=3)
        first, second = plan.batches
        assert first.pages == (3, 7, 20)
        assert second.pages == (20, 21)
        assert [area.key for area in second.areas] == ["4"]

    def test_boundary_page_area_goes_to_earliest_batch(self):
        areas = tuple(make_area(str(page), (page,)) for page in range(1, 10))
        index = make_index({1: areas})
        (plan,) = plan_content_batches(index, batch_pages=8)
        first, second = plan.batches
        # Area "8" first appears on the shared boundary page 8 — earliest wins.
        assert [area.key for area in first.areas] == ["1", "2", "3", "4", "5", "6", "7", "8"]
        assert [area.key for area in second.areas] == ["9"]

    def test_map_pages_ride_every_batch_after_batch_pages(self):
        areas = (make_area("1", (2,)), make_area("2", (5,)))
        index = make_index({1: areas}, map_pages=(5, 9))
        (plan,) = plan_content_batches(index, batch_pages=8)
        (batch,) = plan.batches
        assert batch.pages == (2, 5)
        assert batch.map_pages == (5, 9)
        # Part order: batch pages, then map pages not already included.
        assert batch.part_pages == (2, 5, 9)

    def test_pageless_areas_ride_the_last_batch(self):
        areas = (make_area("1", (1,)), make_area("2", (5,)), make_area("3", (9,)), make_area("lost", ()))
        index = make_index({1: areas})
        (plan,) = plan_content_batches(index, batch_pages=2)
        first, second = plan.batches
        assert [area.key for area in first.areas] == ["1", "2"]
        assert [area.key for area in second.areas] == ["3", "lost"]

    def test_all_pageless_with_map_pages_gets_single_map_batch(self):
        areas = (make_area("1", ()), make_area("2", ()))
        index = make_index({1: areas}, map_pages=(4,))
        (plan,) = plan_content_batches(index, batch_pages=8)
        (batch,) = plan.batches
        assert batch.pages == ()
        assert batch.part_pages == (4,)
        assert [area.key for area in batch.areas] == ["1", "2"]

    def test_level_with_no_pages_at_all_plans_no_batches(self):
        index = make_index({1: (make_area("1", ()),)})
        (plan,) = plan_content_batches(index, batch_pages=8)
        assert plan.batches == ()

    def test_level_with_no_areas_plans_no_batches(self):
        index = make_index({1: ()})
        (plan,) = plan_content_batches(index, batch_pages=8)
        assert plan.batches == ()

    def test_empty_windows_are_dropped_and_batches_renumbered(self):
        # Area "a" spans pages 1 and 9; area "b" is on page 2. Windows over
        # {1, 2, 9} at width 2 are (1,2) and (2,9) — the second window owns no
        # area's first page, so it is dropped.
        areas = (make_area("a", (1, 9)), make_area("b", (2,)))
        index = make_index({1: areas})
        (plan,) = plan_content_batches(index, batch_pages=2)
        (batch,) = plan.batches
        assert batch.pages == (1, 2)
        assert batch.number == 1

    def test_levels_planned_in_survey_order(self):
        index = make_index({1: (make_area("1", (1,)),), 2: (make_area("1", (2,)),)})
        plans = plan_content_batches(index, batch_pages=8)
        assert [(plan.dungeon_id, plan.level_number) for plan in plans] == [("lair", 1), ("lair", 2)]

    def test_batch_pages_below_floor_is_misuse(self):
        index = make_index({1: (make_area("1", (1,)),)})
        for bad in (1, 0, -3):
            with pytest.raises(ValueError, match="at least 2"):
                plan_content_batches(index, batch_pages=bad)


class TestBatchRequest:
    def make_batch(self) -> ContentBatch:
        return ContentBatch(
            dungeon_id="lair",
            dungeon_name="The Lair",
            level_number=1,
            number=1,
            pages=(1, 2),
            map_pages=(3,),
            areas=(make_area("1", (1,)), make_area("5-2", (2,), label="5")),
        )

    def test_key_enum_is_exactly_the_batch_keys(self):
        batch = self.make_batch()
        request = build_batch_request(batch, ())
        assert key_enum(request.schema) == ["1", "5-2"]
        assert request.tag == "content.lair.1.b01"

    def test_prompt_lines_carry_key_label_name_and_pages(self):
        request = build_batch_request(self.make_batch(), ())
        header = header_text(request)
        assert '- 1 | printed key "1" | Area 1 | pages 1' in header
        assert '- 5-2 | printed key "5" | Area 5-2 | pages 2' in header
        assert "map is on page 3" in header

    def test_pageless_area_named_with_unknown_pages(self):
        batch = ContentBatch(
            dungeon_id="lair",
            dungeon_name="The Lair",
            level_number=1,
            number=1,
            pages=(),
            map_pages=(3,),
            areas=(make_area("lost", ()),),
        )
        request = build_batch_request(batch, ())
        assert "pages unknown" in header_text(request)

    def test_follow_up_names_and_enumerates_only_missing_keys(self):
        batch = self.make_batch()
        missing = (batch.areas[1],)
        request = build_batch_request(batch, (), missing=missing)
        assert request.tag == "content.lair.1.b01.retry"
        assert key_enum(request.schema) == ["5-2"]
        assert "- 1 |" not in header_text(request)
        assert "- 5-2 |" in header_text(request)


class TestContentStage:
    def test_two_batches_merge_into_one_level_cache(self, tmp_path: Path):
        areas = (make_area("1", (1,)), make_area("2", (2,)), make_area("3", (3,)))
        index = make_index({1: areas})
        settings = ConversionSettings(content_batch_pages=2)
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=3, settings=settings)
        provider = ScriptedProvider([batch_payload("1", "2"), batch_payload("3")])
        (level,) = content(workdir, provider)
        assert [area.key for area in level.areas] == ["1", "2", "3"]
        cached = LevelContent.model_validate(json.loads(workdir.areas_json("lair", 1).read_text(encoding="utf-8")))
        assert cached == level
        run = workdir.read_run()
        status = run.stages[Stage.CONTENT]
        assert status.status == "completed"
        assert status.usage is not None
        assert status.usage.input_tokens == 200  # two responses accumulated
        assert run.provider == "ScriptedProvider"
        # Requests replay the public builder byte-for-byte.
        plans = plan_content_batches(index, settings.content_batch_pages)
        for sent, batch in zip(provider.requests, plans[0].batches, strict=True):
            rebuilt = build_batch_request(batch, page_request_parts(workdir, batch.part_pages))
            assert rebuilt.fingerprint() == sent.fingerprint()

    def test_duplicate_key_in_one_response_first_wins(self, tmp_path: Path):
        index = make_index({1: (make_area("1", (1,)),)})
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=1)
        first = area_payload("1", description="First answer.")
        second = area_payload("1", description="Second answer.")
        provider = ScriptedProvider([{"areas": [first, second]}])
        (level,) = content(workdir, provider)
        assert level.areas[0].description == "First answer."

    def test_missing_keys_get_one_follow_up(self, tmp_path: Path):
        index = make_index({1: (make_area("1", (1,)), make_area("2", (1,)))})
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=1)
        provider = ScriptedProvider([batch_payload("1"), batch_payload("2")])
        (level,) = content(workdir, provider)
        assert [area.key for area in level.areas] == ["1", "2"]
        assert len(provider.requests) == 2
        retry = provider.requests[1]
        assert retry.tag == "content.lair.1.b01.retry"
        assert key_enum(retry.schema) == ["2"]
        # Same parts as the batch — only the leading prompt text differs.
        assert retry.parts[1:] == provider.requests[0].parts[1:]

    def test_keys_still_missing_after_follow_up_are_absent(self, tmp_path: Path):
        index = make_index({1: (make_area("1", (1,)), make_area("2", (1,)))})
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=1)
        provider = ScriptedProvider([batch_payload("1"), {"areas": []}])
        (level,) = content(workdir, provider)
        assert [area.key for area in level.areas] == ["1"]

    def test_page_references_clamped_in_cache(self, tmp_path: Path):
        index = make_index({1: (make_area("1", (1,)),)})
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=2)
        provider = ScriptedProvider([batch_payload("1", source_pages=[2, 99, 2, 0])])
        (level,) = content(workdir, provider)
        assert level.areas[0].source_pages == (2,)

    def test_mid_stage_failure_keeps_finished_levels(self, tmp_path: Path):
        index = make_index({1: (make_area("1", (1,)),), 2: (make_area("1", (2,)),)})
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=2)
        provider = ScriptedProvider([batch_payload("1"), ProviderError("rate limited")])
        with pytest.raises(ProviderError):
            content(workdir, provider)
        assert workdir.areas_json("lair", 1).is_file()
        assert not workdir.areas_json("lair", 2).exists()
        run = workdir.read_run()
        assert run.stages[Stage.CONTENT].status == "failed"
        assert run.stages[Stage.SURVEY].status == "completed"

    def test_stale_caches_cleared_before_extraction(self, tmp_path: Path):
        index = make_index({1: (make_area("1", (1,)),)})
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=1)
        stale = workdir.areas_json("renamed-dungeon", 7)
        stale.write_text("{}", encoding="utf-8")
        content(workdir, ScriptedProvider([batch_payload("1")]))
        assert not stale.exists()
        assert workdir.areas_json("lair", 1).is_file()

    def test_stale_monsters_and_statblock_caches_cleared_upfront(self, tmp_path: Path):
        index = make_index({1: (make_area("1", (1,)),)})
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=1)
        stale = workdir.monsters_json
        stale.write_text("{}", encoding="utf-8")
        stale_blocks = workdir.statblocks_json
        stale_blocks.write_text("{}", encoding="utf-8")
        content(workdir, ScriptedProvider([batch_payload("1")]))
        assert not stale.exists()
        assert not stale_blocks.exists()

    def test_level_with_no_pages_writes_empty_cache_without_model_call(self, tmp_path: Path):
        index = make_index({1: (make_area("1", ()),)})
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=1)
        provider = ScriptedProvider([])
        (level,) = content(workdir, provider)
        assert level.areas == ()
        assert provider.requests == []
        assert workdir.areas_json("lair", 1).is_file()

    def test_cross_batch_duplicate_key_is_a_code_bug(self, tmp_path: Path):
        areas = (make_area("1", (1,)), make_area("2", (2,)), make_area("3", (3,)))
        index = make_index({1: areas})
        settings = ConversionSettings(content_batch_pages=2)
        workdir = prepare_content_workdir(tmp_path / "mod.forge", index, page_count=3, settings=settings)
        # Batch 2 answers a key batch 1 already covered — impossible through a
        # schema-enforcing provider, so it must surface as a code bug.
        provider = ScriptedProvider([batch_payload("1", "2"), batch_payload("1"), {"areas": []}])
        with pytest.raises(AssertionError, match="duplicate area key"):
            content(workdir, provider)

    def test_requires_completed_survey_and_cache(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
        with pytest.raises(ValueError, match="survey"):
            content(workdir, ScriptedProvider([]))
        index = make_index({1: (make_area("1", (1,)),)})
        workdir2 = prepare_content_workdir(tmp_path / "mod2.forge", index, page_count=1)
        workdir2.survey_json.unlink()
        with pytest.raises(ValueError, match="cache"):
            content(workdir2, ScriptedProvider([]))
