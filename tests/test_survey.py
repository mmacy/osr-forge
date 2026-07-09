import json
from pathlib import Path

import pytest

from conftest import ScriptedProvider, fabricate_workdir
from osrforge.contracts.report import AreaAddress
from osrforge.contracts.run import Stage
from osrforge.contracts.stages import CANONICAL_SLUG_PATTERN, SurveyIndex
from osrforge.errors import ExtractionError, ProviderError
from osrforge.providers.base import TextPart
from osrforge.settings import ConversionSettings
from osrforge.survey import (
    build_survey_request,
    canonical_slug,
    filter_index_to_pages,
    normalize_survey,
    survey,
)


def raw_area(key: str, name: str = "Somewhere", pages: list[int] | None = None, kind: str = "room") -> dict:
    return {"key": key, "name": name, "source_pages": pages if pages is not None else [3], "kind": kind}


def raw_survey(dungeons: list[dict] | None = None, **overrides) -> dict:
    payload = {
        "title": "The Chaotic Caves",
        "hooks": ["Rumors of treasure in the caves"],
        "town": {"name": "", "description": "A trade town."},
        "dungeons": dungeons
        if dungeons is not None
        else [
            {
                "name": "A. Orc Lair",
                "levels": [{"number": 1, "map_pages": [38], "areas": [raw_area("1", "Entrance", [22], "cave")]}],
            }
        ],
        "monster_names": ["orc"],
    }
    return payload | overrides


class TestCanonicalSlug:
    def test_real_fixture_cases(self):
        assert canonical_slug("A. Orc Lair") == "a-orc-lair"
        assert canonical_slug("4a") == "4a"
        assert canonical_slug("East, North and South Gates") == "east-north-and-south-gates"

    def test_case_and_punctuation(self):
        assert canonical_slug("4A") == "4a"
        assert canonical_slug("Room #7 (upper)") == "room-7-upper"
        assert canonical_slug("  spaced   out  ") == "spaced-out"

    def test_non_ascii_decomposes_or_drops(self):
        assert canonical_slug("Café of Ghoüls") == "cafe-of-ghouls"
        assert canonical_slug("洞窟") == ""

    def test_empty_results(self):
        assert canonical_slug("") == ""
        assert canonical_slug("!!! --- !!!") == ""


class TestNormalization:
    def test_spike_duplicate_key_case(self):
        # The real fixture hazard: two areas both keyed "5".
        areas = [raw_area("5", "Door to Jail"), raw_area("5", "Jail Area")]
        dungeons = [{"name": "A. Orc Lair", "levels": [{"number": 1, "map_pages": [], "areas": areas}]}]
        index = normalize_survey(raw_survey(dungeons), page_count=48)
        keys = [area.key for area in index.dungeons[0].levels[0].areas]
        assert keys == ["5", "5-2"]
        labels = [area.source_label for area in index.dungeons[0].levels[0].areas]
        assert labels == [None, "5"]

    def test_reserve_then_bump(self):
        # "5", "5", "5-2" must yield 5, 5-3, 5-2 — the second "5" skips 5-2
        # because area three reserved it; the naive suffix-by-duplicate-count
        # reading would emit a duplicate.
        areas = [raw_area("5"), raw_area("5"), raw_area("5-2")]
        dungeons = [{"name": "Lair", "levels": [{"number": 1, "map_pages": [], "areas": areas}]}]
        index = normalize_survey(raw_survey(dungeons), page_count=48)
        assert [area.key for area in index.dungeons[0].levels[0].areas] == ["5", "5-3", "5-2"]

    def test_label_keys_slug_with_label_preserved(self):
        areas = [raw_area("East, North and South Gates", "East, North and South Gates")]
        dungeons = [{"name": "Town", "levels": [{"number": 1, "map_pages": [], "areas": areas}]}]
        index = normalize_survey(raw_survey(dungeons), page_count=48)
        area = index.dungeons[0].levels[0].areas[0]
        assert area.key == "east-north-and-south-gates"
        assert area.source_label == "East, North and South Gates"
        assert area.name == "East, North and South Gates"

    def test_empty_key_falls_back_to_position(self):
        areas = [raw_area("1"), raw_area("!!!"), raw_area("")]
        dungeons = [{"name": "Lair", "levels": [{"number": 1, "map_pages": [], "areas": areas}]}]
        index = normalize_survey(raw_survey(dungeons), page_count=48)
        assert [area.key for area in index.dungeons[0].levels[0].areas] == ["1", "area-2", "area-3"]

    def test_duplicate_dungeon_names_bump(self):
        dungeons = [
            {"name": "Orc Lair", "levels": [{"number": 1, "map_pages": [], "areas": [raw_area("1")]}]},
            {"name": "Orc Lair", "levels": [{"number": 1, "map_pages": [], "areas": [raw_area("1")]}]},
        ]
        index = normalize_survey(raw_survey(dungeons), page_count=48)
        assert [dungeon.id for dungeon in index.dungeons] == ["orc-lair", "orc-lair-2"]
        assert [dungeon.name for dungeon in index.dungeons] == ["Orc Lair", "Orc Lair"]

    def test_empty_dungeon_name_falls_back_to_position(self):
        dungeons = [
            {"name": "", "levels": [{"number": 1, "map_pages": [], "areas": [raw_area("1")]}]},
            {"name": "Lair", "levels": [{"number": 1, "map_pages": [], "areas": [raw_area("1")]}]},
        ]
        index = normalize_survey(raw_survey(dungeons), page_count=48)
        assert index.dungeons[0].id == "dungeon-1"

    def test_invalid_level_numbers_renumbered_in_listed_order(self):
        levels = [
            {"number": 0, "map_pages": [], "areas": [raw_area("1")]},
            {"number": 3, "map_pages": [], "areas": [raw_area("2")]},
        ]
        index = normalize_survey(raw_survey([{"name": "Lair", "levels": levels}]), page_count=48)
        assert [level.number for level in index.dungeons[0].levels] == [1, 2]

    def test_duplicate_level_numbers_renumbered(self):
        levels = [
            {"number": 2, "map_pages": [], "areas": [raw_area("1")]},
            {"number": 2, "map_pages": [], "areas": [raw_area("2")]},
        ]
        index = normalize_survey(raw_survey([{"name": "Lair", "levels": levels}]), page_count=48)
        assert [level.number for level in index.dungeons[0].levels] == [1, 2]

    def test_valid_unique_level_numbers_kept(self):
        levels = [
            {"number": 2, "map_pages": [], "areas": [raw_area("1")]},
            {"number": 5, "map_pages": [], "areas": [raw_area("2")]},
        ]
        index = normalize_survey(raw_survey([{"name": "Lair", "levels": levels}]), page_count=48)
        assert [level.number for level in index.dungeons[0].levels] == [2, 5]

    def test_pages_clamped_deduped_sorted(self):
        areas = [raw_area("1", pages=[40, 2, 99, 2, 0])]
        levels = [{"number": 1, "map_pages": [50, 38, 38, -1], "areas": areas}]
        index = normalize_survey(raw_survey([{"name": "Lair", "levels": levels}]), page_count=48)
        level = index.dungeons[0].levels[0]
        assert level.map_pages == (38,)
        assert level.areas[0].source_pages == (2, 40)

    def test_every_key_matches_slug_grammar_and_round_trips_as_address(self):
        areas = [raw_area("East, North and South Gates"), raw_area("4a"), raw_area("5"), raw_area("5")]
        dungeons = [
            {"name": "A. Orc Lair", "levels": [{"number": 1, "map_pages": [], "areas": areas}]},
            {"name": "Überdorf — Keep!", "levels": [{"number": 1, "map_pages": [], "areas": [raw_area("1")]}]},
        ]
        index = normalize_survey(raw_survey(dungeons), page_count=48)
        for dungeon in index.dungeons:
            assert CANONICAL_SLUG_PATTERN.match(dungeon.id)
            for level in dungeon.levels:
                for area in level.areas:
                    assert CANONICAL_SLUG_PATTERN.match(area.key)
                    address = f"{dungeon.id}/{level.number}/{area.key}"
                    assert str(AreaAddress.parse(address)) == address

    def test_zero_dungeons_raises(self):
        with pytest.raises(ExtractionError, match="no dungeons"):
            normalize_survey(raw_survey([]), page_count=48)

    def test_zero_areas_raises(self):
        dungeons = [{"name": "Lair", "levels": [{"number": 1, "map_pages": [38], "areas": []}]}]
        with pytest.raises(ExtractionError, match="no keyed areas"):
            normalize_survey(raw_survey(dungeons), page_count=48)

    def test_dungeon_with_no_levels_dropped(self):
        dungeons = [
            {"name": "Ghost", "levels": []},
            {"name": "Lair", "levels": [{"number": 1, "map_pages": [], "areas": [raw_area("1")]}]},
        ]
        index = normalize_survey(raw_survey(dungeons), page_count=48)
        assert [dungeon.id for dungeon in index.dungeons] == ["lair"]

    def test_normalization_is_deterministic(self):
        payload = raw_survey()
        assert normalize_survey(payload, 48) == normalize_survey(payload, 48)


def test_filter_index_to_pages_intersects_every_page_list():
    areas = [raw_area("1", pages=[8, 22]), raw_area("2", pages=[21])]
    levels = [{"number": 1, "map_pages": [38, 39], "areas": areas}]
    index = normalize_survey(raw_survey([{"name": "Lair", "levels": levels}]), page_count=48)
    filtered = filter_index_to_pages(index, [8, 22, 38])
    level = filtered.dungeons[0].levels[0]
    assert level.map_pages == (38,)
    assert level.areas[0].source_pages == (8, 22)
    assert level.areas[1].source_pages == ()


class TestSurveyStage:
    def test_hazard_shaped_data_lands_normalized_in_cache(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=3)
        areas = [raw_area("5", pages=[2]), raw_area("5", pages=[99, 3, 3])]
        dungeons = [
            {"name": "Orc Lair", "levels": [{"number": 0, "map_pages": [2], "areas": areas}]},
            {"name": "Orc Lair", "levels": [{"number": 1, "map_pages": [], "areas": [raw_area("1", pages=[3])]}]},
        ]
        provider = ScriptedProvider([raw_survey(dungeons)])
        index = survey(workdir, provider)
        cached = SurveyIndex.model_validate(json.loads(workdir.survey_json.read_text(encoding="utf-8")))
        assert cached == index
        assert [dungeon.id for dungeon in cached.dungeons] == ["orc-lair", "orc-lair-2"]
        assert [area.key for area in cached.dungeons[0].levels[0].areas] == ["5", "5-2"]
        assert cached.dungeons[0].levels[0].number == 1
        assert cached.dungeons[0].levels[0].areas[1].source_pages == (3,)

    def test_request_covers_all_pages_in_order(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=3)
        provider = ScriptedProvider([raw_survey()])
        survey(workdir, provider)
        (request,) = provider.requests
        assert request.tag == "survey"
        markers = [part.text.split("\n")[0] for part in request.parts if isinstance(part, TextPart)]
        assert markers == ["[page 1]", "[page 2]", "[page 3]"]
        rebuilt = build_survey_request(request.parts)
        assert rebuilt.fingerprint() == request.fingerprint()

    def test_run_json_transitions_and_identity(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=2)
        provider = ScriptedProvider([raw_survey()])
        survey(workdir, provider)
        run = workdir.read_run()
        status = run.stages[Stage.SURVEY]
        assert status.status == "completed"
        assert status.usage is not None and status.usage.input_tokens == 100
        assert run.provider == "ScriptedProvider"
        assert run.model_id == "stub-model-1"

    def test_downstream_caches_cleared_on_success(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=2)
        workdir.stages_dir.mkdir()
        stale = workdir.areas_json("old-dungeon", 1)
        stale.write_text("{}", encoding="utf-8")
        survey(workdir, ScriptedProvider([raw_survey()]))
        assert not stale.exists()
        assert workdir.survey_json.is_file()

    def test_guard_violation_raises_before_any_provider_call(self, tmp_path: Path):
        settings = ConversionSettings(survey_max_pages=2)
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=3, settings=settings)
        provider = ScriptedProvider([])
        with pytest.raises(ExtractionError, match="survey guard"):
            survey(workdir, provider)
        assert provider.requests == []
        # The guard fires before the stage starts: no failed entry, upstream intact.
        run = workdir.read_run()
        assert run.stages[Stage.SURVEY].status == "pending"
        assert run.stages[Stage.PREPROCESS].status == "completed"

    def test_provider_failure_marks_stage_failed_and_keeps_previous_caches(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=2)
        workdir.stages_dir.mkdir()
        previous = workdir.areas_json("old-dungeon", 1)
        previous.write_text("{}", encoding="utf-8")
        with pytest.raises(ProviderError):
            survey(workdir, ScriptedProvider([ProviderError("rate limited")]))
        run = workdir.read_run()
        assert run.stages[Stage.SURVEY].status == "failed"
        assert run.stages[Stage.SURVEY].error == "rate limited"
        assert run.stages[Stage.PREPROCESS].status == "completed"
        # Clear-on-success: the previous consistent cache set survives a failed re-run.
        assert previous.is_file()
        assert not workdir.survey_json.exists()

    def test_requires_completed_preprocess(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
        run = workdir.read_run()
        workdir.write_run(
            run.with_stage(Stage.PREPROCESS, run.stages[Stage.PREPROCESS].model_copy(update={"status": "running"}))
        )
        with pytest.raises(ValueError, match="preprocess"):
            survey(workdir, ScriptedProvider([]))
