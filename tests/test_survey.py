import copy
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
    SURVEY_SCHEMA,
    SURVEY_SYSTEM,
    build_chunked_survey_request,
    build_survey_request,
    canonical_slug,
    filter_index_to_pages,
    merge_survey_answers,
    normalize_survey,
    survey,
    survey_windows,
)


def raw_area(key: str, name: str = "Somewhere", pages: list[int] | None = None, kind: str = "room") -> dict:
    return {"key": key, "name": name, "source_pages": pages if pages is not None else [3], "kind": kind}


def raw_survey(dungeons: list[dict] | None = None, **overrides) -> dict:
    payload = {
        "title": "The Chaotic Caves",
        "description": "An introductory adventure in the caves.",
        "hooks": ["Rumors of treasure in the caves"],
        "town": {"name": "", "description": "A trade town.", "services": []},
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

    def test_description_and_town_services_carry_through(self):
        payload = raw_survey(
            description="An adventure for levels 1-3.",
            town={"name": "Riverton", "description": "A trade town.", "services": ["The Gilded Goat inn"]},
        )
        index = normalize_survey(payload, page_count=48)
        assert index.description == "An adventure for levels 1-3."
        assert index.town.services == ("The Gilded Goat inn",)

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


def test_success_clears_the_monsters_and_statblock_caches_too(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=2)
    workdir.stages_dir.mkdir()
    stale_monsters = workdir.monsters_json
    stale_monsters.write_text("{}", encoding="utf-8")
    stale_blocks = workdir.statblocks_json
    stale_blocks.write_text("{}", encoding="utf-8")
    survey(workdir, ScriptedProvider([raw_survey()]))
    assert not stale_monsters.exists()
    assert not stale_blocks.exists()


class TestSurveyWindows:
    def test_contiguous_disjoint_windows(self):
        assert survey_windows(5, 2) == ((1, 2), (3, 4), (5, 5))
        assert survey_windows(4, 2) == ((1, 2), (3, 4))
        assert survey_windows(1, 150) == ((1, 1),)

    def test_at_the_chunk_size_is_one_window(self):
        assert survey_windows(150, 150) == ((1, 150),)
        assert survey_windows(151, 150) == ((1, 150), (151, 151))


def one_dungeon(name: str, areas: list[dict], number: int = 1, map_pages: list[int] | None = None) -> dict:
    return {"name": name, "levels": [{"number": number, "map_pages": map_pages or [], "areas": areas}]}


class TestMergeSurveyAnswers:
    def test_boundary_split_dungeon_rejoins_on_slug(self):
        first = raw_survey([one_dungeon("The Crypt", [raw_area("1", "Hall", [2])], map_pages=[2])])
        second = raw_survey([one_dungeon("THE CRYPT!", [raw_area("2", "Vault", [3])], map_pages=[3, 2])])
        merged = merge_survey_answers([first, second])
        (dungeon,) = merged["dungeons"]
        assert dungeon["name"] == "The Crypt"  # first occurrence wins the scalar
        (level,) = dungeon["levels"]
        assert level["map_pages"] == [2, 3]  # union, first-seen order
        assert [area["key"] for area in level["areas"]] == ["1", "2"]

    def test_area_join_first_wins_scalars_and_pages_union(self):
        first = raw_survey([one_dungeon("Lair", [raw_area("5", "Door to Jail", [2], "room")])])
        second_area = {"key": "5.", "name": "Jail", "source_pages": [3, 2], "kind": "cave"}
        second = raw_survey([one_dungeon("Lair", [second_area])])
        merged = merge_survey_answers([first, second])
        (area,) = merged["dungeons"][0]["levels"][0]["areas"]
        assert area["key"] == "5"
        assert area["name"] == "Door to Jail"
        assert area["kind"] == "room"
        assert area["source_pages"] == [2, 3]

    def test_levels_join_on_number_and_new_levels_append(self):
        first = raw_survey([one_dungeon("Lair", [raw_area("1")])])
        second = raw_survey(
            [
                {
                    "name": "Lair",
                    "levels": [
                        {"number": 1, "map_pages": [], "areas": [raw_area("2")]},
                        {"number": 2, "map_pages": [], "areas": [raw_area("1")]},
                    ],
                }
            ]
        )
        merged = merge_survey_answers([first, second])
        (dungeon,) = merged["dungeons"]
        assert [level["number"] for level in dungeon["levels"]] == [1, 2]
        assert [area["key"] for area in dungeon["levels"][0]["areas"]] == ["1", "2"]
        assert [area["key"] for area in dungeon["levels"][1]["areas"]] == ["1"]

    def test_title_and_town_take_the_first_nonempty_in_window_order(self):
        first = raw_survey(title="", description="", town={"name": "", "description": "", "services": []})
        second = raw_survey(
            title="The Caves",
            description="",
            town={"name": "", "description": "A trade town.", "services": ["an inn"]},
        )
        third = raw_survey(
            title="Other Title",
            description="",
            town={"name": "Riverton", "description": "", "services": ["a temple"]},
        )
        merged = merge_survey_answers([first, second, third])
        assert merged["title"] == "The Caves"
        # The town joins as a unit: the first entry with a non-empty name wins
        # whole, its services riding with it.
        assert merged["town"] == {"name": "Riverton", "description": "", "services": ["a temple"]}

    def test_town_falls_back_to_first_description_then_empty(self):
        described = raw_survey(town={"name": "", "description": "A trade town.", "services": ["a general store"]})
        empty = raw_survey(town={"name": "", "description": "", "services": []})
        assert merge_survey_answers([empty, described])["town"] == {
            "name": "",
            "description": "A trade town.",
            "services": ["a general store"],
        }
        assert merge_survey_answers([empty, empty])["town"] == {"name": "", "description": "", "services": []}

    def test_module_description_takes_the_first_nonempty_in_window_order(self):
        first = raw_survey(description="")
        second = raw_survey(description="An adventure for levels 1-3.")
        third = raw_survey(description="Another pitch.")
        merged = merge_survey_answers([first, second, third])
        assert merged["description"] == "An adventure for levels 1-3."

    def test_hooks_dedup_and_monster_names_union_in_first_seen_order(self):
        first = raw_survey(hooks=["A rumor", "A job"], monster_names=["orc", "wolf"])
        second = raw_survey(hooks=["A job", "A debt"], monster_names=["wolf", "goblin"])
        merged = merge_survey_answers([first, second])
        assert merged["hooks"] == ["A rumor", "A job", "A debt"]
        assert merged["monster_names"] == ["orc", "wolf", "goblin"]

    def test_same_window_duplicate_slugs_survive_verbatim(self):
        # JN1's own committed survey holds two `key: "5"` areas — intra-window
        # multiplicity is what reserve-then-bump exists to preserve.
        only = raw_survey([one_dungeon("Lair", [raw_area("5", "Door to Jail"), raw_area("5", "Jail Area")])])
        merged = merge_survey_answers([only])
        assert [area["name"] for area in merged["dungeons"][0]["levels"][0]["areas"]] == ["Door to Jail", "Jail Area"]

    def test_cross_window_joins_are_occurrence_indexed_and_append_past_count(self):
        first = raw_survey([one_dungeon("Lair", [raw_area("5", "First", [2]), raw_area("5", "Second", [3])])])
        second = raw_survey(
            [one_dungeon("Lair", [raw_area("5", "One", [4]), raw_area("5", "Two", [5]), raw_area("5", "Three", [6])])]
        )
        merged = merge_survey_answers([first, second])
        areas = merged["dungeons"][0]["levels"][0]["areas"]
        # n-th occurrence joins n-th occurrence; the third appends as new.
        assert [area["name"] for area in areas] == ["First", "Second", "Three"]
        assert [area["source_pages"] for area in areas] == [[2, 4], [3, 5], [6]]

    def test_empty_slugs_never_join(self):
        first = raw_survey([one_dungeon("洞窟", [raw_area("!!!", "Punct", [2])])])
        second = raw_survey([one_dungeon("洞窟", [raw_area("!!!", "Punct Again", [3])])])
        merged = merge_survey_answers([first, second])
        assert len(merged["dungeons"]) == 2
        index = normalize_survey(merged, page_count=48)
        assert [dungeon.id for dungeon in index.dungeons] == ["dungeon-1", "dungeon-2"]

    def test_merge_does_not_mutate_its_inputs(self):
        first = raw_survey([one_dungeon("Lair", [raw_area("5", "First", [2])])])
        second = raw_survey([one_dungeon("Lair", [raw_area("5", "Second", [3])])])
        snapshots = copy.deepcopy([first, second])
        merge_survey_answers([first, second])
        assert [first, second] == snapshots

    def test_merged_raw_takes_reserve_then_bump_in_one_normalize_pass(self):
        # A per-window normalize would have uniqued window two's keys before the
        # join and the bumped key could never re-join; the raw-level merge keeps
        # both `5`s and one normalize pass bumps the appended one.
        first = raw_survey([one_dungeon("Lair", [raw_area("5", "First", [2])])])
        second = raw_survey([one_dungeon("Lair", [raw_area("5", "One", [3]), raw_area("5", "Two", [4])])])
        index = normalize_survey(merge_survey_answers([first, second]), page_count=48)
        assert [area.key for area in index.dungeons[0].levels[0].areas] == ["5", "5-2"]


class TestChunkedSurvey:
    def test_chunked_run_windows_merge_and_bookkeeping(self, tmp_path: Path):
        settings = ConversionSettings(survey_max_pages=2)
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=5, settings=settings)
        workdir.stages_dir.mkdir()
        stale = workdir.areas_json("old-dungeon", 1)
        stale.write_text("{}", encoding="utf-8")
        first = raw_survey(
            [one_dungeon("The Caves", [raw_area("5", "Door to Jail", [2])], map_pages=[1])],
            title="",
            hooks=["A rumor"],
            monster_names=["orc"],
        )
        second = raw_survey(
            [one_dungeon("The Caves", [raw_area("5", "Jail", [3]), raw_area("5", "Second Jail", [4])])],
            hooks=["A rumor", "A job"],
            monster_names=["wolf"],
        )
        third = raw_survey([], title="", hooks=[], monster_names=["goblin"])
        provider = ScriptedProvider([first, second, third])
        index = survey(workdir, provider)

        assert len(provider.requests) == 3
        markers = [
            [part.text.split("\n")[0] for part in request.parts if isinstance(part, TextPart)]
            for request in provider.requests
        ]
        # Absolute page markers, contiguous disjoint windows.
        assert markers == [["[page 1]", "[page 2]"], ["[page 3]", "[page 4]"], ["[page 5]"]]
        for request, (first_page, last_page) in zip(provider.requests, ((1, 2), (3, 4), (5, 5)), strict=True):
            assert request.tag == "survey"
            assert request.schema == SURVEY_SCHEMA
            assert request.system.startswith(SURVEY_SYSTEM)
            assert f"pages {first_page}-{last_page} of a 5-page module" in request.system

        # Raw-level merge, one normalize pass: the joined `5` plus the appended
        # `5` bumped by reserve-then-bump.
        assert index.title == "The Chaotic Caves"
        (dungeon,) = index.dungeons
        assert dungeon.id == "the-caves"
        assert [area.key for area in dungeon.levels[0].areas] == ["5", "5-2"]
        assert dungeon.levels[0].areas[0].source_pages == (2, 3)
        assert index.hooks == ("A rumor", "A job")
        assert index.monster_names == ("orc", "wolf", "goblin")

        # Usage accumulated across windows; caches cleared once on success.
        run = workdir.read_run()
        usage = run.stages[Stage.SURVEY].usage
        assert usage is not None and usage.input_tokens == 300 and usage.output_tokens == 30
        assert not stale.exists()
        assert workdir.survey_json.is_file()

    def test_distinct_windows_produce_distinct_fingerprints(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=4)
        from osrforge.pages import page_request_parts

        one = build_chunked_survey_request(page_request_parts(workdir, [1, 2]), 1, 2, 4)
        two = build_chunked_survey_request(page_request_parts(workdir, [3, 4]), 3, 4, 4)
        assert one.fingerprint() != two.fingerprint()

    def test_at_the_chunk_size_uses_the_untouched_single_request_path(self, tmp_path: Path):
        settings = ConversionSettings(survey_max_pages=3)
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=3, settings=settings)
        provider = ScriptedProvider([raw_survey()])
        survey(workdir, provider)
        (request,) = provider.requests
        assert request.system == SURVEY_SYSTEM  # no preamble on the single-request path
        rebuilt = build_survey_request(request.parts)
        assert rebuilt.fingerprint() == request.fingerprint()


def test_single_request_path_is_digest_identical_to_the_committed_minimod_fixture(tmp_path: Path):
    """The invariant behind every committed fixture set, named.

    The replay gates prove single-request byte-identity end to end; this
    assertion exists so an edit to `SURVEY_SYSTEM`, `SURVEY_SCHEMA`, or the
    single-request build path fails with a message instead of a mysterious
    fixture miss.
    """
    from osrforge.pages import page_request_parts
    from test_minimod_pipeline import MINIMOD, PAGE_COUNT, minimod_workdir

    workdir = minimod_workdir(tmp_path / "mod.forge")
    request = build_survey_request(page_request_parts(workdir, range(1, PAGE_COUNT + 1)))
    (fixture_path,) = (MINIMOD / "fixtures").glob("survey.*.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert request.fingerprint() == fixture["fingerprint"]
