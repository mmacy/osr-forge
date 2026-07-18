"""The monsters stage: normalization, the four tiers, the LLM request, the stat-block pass, and stage choreography."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from osrlib.data import load_monsters

from conftest import ScriptedProvider, fabricate_workdir
from osrforge.contracts.run import Stage, StageStatus
from osrforge.contracts.stages import LevelContent, MonsterResolutions, RawStatBlock, StatBlocks, SurveyIndex
from osrforge.monsters import (
    MONSTER_ALIASES,
    STATBLOCK_PAGE_CAP,
    build_monsters_request,
    build_statblock_request,
    deterministic_resolutions,
    llm_candidates,
    monsters,
    normalize_monster_name,
    statblock_page_plan,
    statblock_tag,
)
from osrforge.providers.base import ImagePart, TextPart
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, write_json_artifact

CATALOG = load_monsters()
THRESHOLD = 0.85


class PoisonedProvider:
    """Raises on any use — pins that a fully deterministic resolution makes no model call."""

    def generate(self, request):
        raise AssertionError(f"unexpected model call: {request.tag!r}")


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Zombies", "zombies"),
        ("  Giant   Centipedes ", "giant centipedes"),
        ("HOBGOBLIN Chief", "hobgoblin chief"),
        ("hobgoblin chieftain", "hobgoblin chieftain"),
    ],
)
def test_normalization(raw: str, normalized: str):
    assert normalize_monster_name(raw) == normalized


class TestExactTier:
    @pytest.mark.parametrize(
        ("name", "template_id"),
        [
            # The real catalog quirks the match forms exist for:
            ("giant centipedes", "centipede_giant"),  # comma inversion + plural
            ("owlbear", "owl_bear"),  # squashed compound
            ("stirges", "stirge"),  # plural
            ("giant rats", "giant_rat"),
            ("goblin", "goblin"),
            ("skeleton", "skeleton"),
            ("sea snake", "sea_snake"),
        ],
    )
    def test_catalog_quirks(self, name: str, template_id: str):
        resolutions = deterministic_resolutions([name], CATALOG, THRESHOLD)
        assert resolutions[name].method == "exact"
        assert resolutions[name].template_id == template_id

    def test_no_plain_wolf_in_the_catalog(self):
        # The alias table earns its keep: "wolf" has no exact match.
        resolutions = deterministic_resolutions(["wolf"], CATALOG, THRESHOLD)
        assert resolutions["wolf"].method == "alias"
        assert resolutions["wolf"].template_id == "normal_wolf"

    def test_catalog_has_no_match_form_collisions(self):
        # deterministic_resolutions' exact tier assumes a unique hit; pin it.
        from osrforge.monsters import _catalog_forms

        collisions = {form: ids for form, ids in _catalog_forms(CATALOG).items() if len(ids) > 1}
        assert collisions == {}


class TestAliasTier:
    def test_every_alias_targets_a_real_template(self):
        for template_id in MONSTER_ALIASES.values():
            CATALOG.get(template_id)

    def test_exact_beats_alias(self, monkeypatch: pytest.MonkeyPatch):
        # An alias entry shadowed by an exact match never fires.
        monkeypatch.setitem(MONSTER_ALIASES, "goblin", "hobgoblin")
        resolutions = deterministic_resolutions(["goblin"], CATALOG, THRESHOLD)
        assert resolutions["goblin"].method == "exact"
        assert resolutions["goblin"].template_id == "goblin"

    def test_alias_beats_fuzzy(self):
        # "lizard men" would fuzzy-miss (0.74 best); the alias resolves it.
        resolutions = deterministic_resolutions(["lizard men"], CATALOG, THRESHOLD)
        assert resolutions["lizard men"].method == "alias"
        assert resolutions["lizard men"].template_id == "lizard_man"


class TestFuzzyTier:
    def test_accepts_the_pinned_true_matches(self):
        resolutions = deterministic_resolutions(["normal man", "yellow mold"], CATALOG, THRESHOLD)
        assert resolutions["normal man"].method == "fuzzy"
        assert resolutions["normal man"].template_id == "normal_human"
        assert resolutions["yellow mold"].method == "fuzzy"
        assert resolutions["yellow mold"].template_id == "yellow_mould"

    def test_rejects_the_pinned_false_neighbours(self):
        resolutions = deterministic_resolutions(["giant bee", "gray jelly"], CATALOG, THRESHOLD)
        assert "giant bee" not in resolutions
        assert "gray jelly" not in resolutions

    def test_threshold_boundary_is_inclusive(self):
        # normal man → Normal Human scores 0.909…; a threshold exactly at the
        # score accepts, just above rejects.
        from difflib import SequenceMatcher

        score = SequenceMatcher(None, "normal man", "normal human").ratio()
        accepted = deterministic_resolutions(["normal man"], CATALOG, score)
        assert accepted["normal man"].method == "fuzzy"
        rejected = deterministic_resolutions(["normal man"], CATALOG, min(score + 0.001, 1.0))
        assert "normal man" not in rejected

    def test_tie_goes_to_the_llm_tier(self):
        # "veteran" scores 0.933 against veteran_1, veteran_2, and veteran_3
        # alike — over threshold but not unique, so no coin flip: LLM tier.
        resolutions = deterministic_resolutions(["veteran"], CATALOG, THRESHOLD)
        assert "veteran" not in resolutions


class TestLlmRequest:
    def test_candidates_ordered_by_score_then_id_and_cut_after(self):
        candidates = llm_candidates("gray jelly", CATALOG, 8)
        assert len(candidates) == 8
        ids = [template_id for template_id, _ in candidates]
        assert ids[0] == "ochre_jelly"  # the best fuzzy neighbour
        assert len(set(ids)) == 8

    def test_top_k_of_one(self):
        candidates = llm_candidates("gray jelly", CATALOG, 1)
        assert [template_id for template_id, _ in candidates] == ["ochre_jelly"]

    def test_schema_shape_per_name_enum_plus_null(self):
        request = build_monsters_request(
            [
                ("gray jelly", (("ochre_jelly", "Ochre Jelly"), ("grey_ooze", "Grey Ooze"))),
                ("bandit leader", (("bandit", "Bandit"),)),
            ]
        )
        assert request.tag == "monsters"
        schema = request.schema
        assert schema["required"] == ["bandit leader", "gray jelly"]  # sorted-name order
        properties = cast(dict[str, Any], schema["properties"])
        jelly = cast(dict[str, Any], properties["gray jelly"])
        assert cast(dict[str, Any], jelly["properties"])["template_id"] == {
            "type": ["string", "null"],
            "enum": ["ochre_jelly", "grey_ooze", None],
        }
        assert jelly["required"] == ["template_id"]
        assert jelly["additionalProperties"] is False
        assert schema["additionalProperties"] is False

    def test_prompt_lists_names_and_candidates_in_schema_order(self):
        request = build_monsters_request(
            [
                ("gray jelly", (("ochre_jelly", "Ochre Jelly"),)),
                ("bandit leader", (("bandit", "Bandit"),)),
            ]
        )
        (part,) = request.parts
        assert isinstance(part, TextPart)
        assert part.text.index('"bandit leader"') < part.text.index('"gray jelly"')
        assert "bandit (Bandit)" in part.text

    def test_request_is_text_only_and_pure(self):
        pairs = [("gray jelly", (("ochre_jelly", "Ochre Jelly"),))]
        first = build_monsters_request(pairs)
        second = build_monsters_request(pairs)
        assert first.fingerprint() == second.fingerprint()
        assert all(isinstance(part, TextPart) for part in first.parts)

    def test_empty_request_is_misuse(self):
        with pytest.raises(ValueError, match="at least one"):
            build_monsters_request([])


def stage_workdir(
    root: Path,
    encounters_by_area: dict[str, list[str]],
    page_count: int = 1,
    settings: ConversionSettings | None = None,
    source_pages_by_area: dict[str, list[int]] | None = None,
) -> Workdir:
    """A workdir with survey + content completed and one level whose areas carry the given monster names."""
    workdir = fabricate_workdir(root, page_count=page_count, settings=settings)
    run = workdir.read_run()
    for stage in (Stage.SURVEY, Stage.CONTENT):
        run = run.with_stage(stage, StageStatus(status="completed"))
    workdir.write_run(run)
    workdir.stages_dir.mkdir(parents=True, exist_ok=True)
    pages_by_area = source_pages_by_area if source_pages_by_area is not None else {}
    survey = {
        "schema_version": 1,
        "title": "Mod",
        "hooks": [],
        "town": {"name": "Town", "description": ""},
        "dungeons": [
            {
                "id": "lair",
                "name": "Lair",
                "levels": [
                    {
                        "number": 1,
                        "map_pages": [],
                        "areas": [
                            {
                                "key": key,
                                "name": key,
                                "source_label": None,
                                "kind": "room",
                                "source_pages": pages_by_area.get(key, []),
                            }
                            for key in encounters_by_area
                        ],
                    }
                ],
            }
        ],
        "monster_names": [],
    }
    write_json_artifact(workdir.survey_json, SurveyIndex.model_validate(survey))
    level = {
        "schema_version": 1,
        "dungeon_id": "lair",
        "level_number": 1,
        "areas": [
            {
                "key": key,
                "description": "",
                "encounters": [
                    {"monster": monster, "count_fixed": 1, "count_dice": None, "count_note": None} for monster in names
                ],
                "trap": None,
                "treasure": [],
                "features": [],
                "connections": [],
                "source_pages": pages_by_area.get(key, []),
                "confidence": 0.9,
            }
            for key, names in encounters_by_area.items()
        ],
    }
    write_json_artifact(workdir.areas_json("lair", 1), LevelContent.model_validate(level))
    return workdir


class TestMonstersStage:
    def test_deterministic_population_makes_no_model_call(self, tmp_path: Path):
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": ["Goblin", "goblin", "stirges"]})
        result = monsters(workdir, PoisonedProvider())
        assert set(result.resolutions) == {"goblin", "stirges"}
        cached = MonsterResolutions.model_validate(json.loads(workdir.monsters_json.read_text(encoding="utf-8")))
        assert cached == result
        run = workdir.read_run()
        status = run.stages[Stage.MONSTERS]
        assert status.status == "completed"
        assert status.usage is not None and status.usage.input_tokens == 0

    def test_cache_keys_are_sorted(self, tmp_path: Path):
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": ["stirges", "goblin", "Bandit"]})
        monsters(workdir, PoisonedProvider())
        cached = json.loads(workdir.monsters_json.read_text(encoding="utf-8"))
        assert list(cached["resolutions"]) == sorted(cached["resolutions"])

    def test_llm_tier_answers_land_in_the_cache(self, tmp_path: Path):
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": ["goblin", "gray jelly", "hobgoblin chieftain"]})
        provider = ScriptedProvider(
            [{"gray jelly": {"template_id": None}, "hobgoblin chieftain": {"template_id": "hobgoblin"}}]
        )
        result = monsters(workdir, provider)
        (request,) = provider.requests
        assert request.tag == "monsters"
        assert request.schema["required"] == ["gray jelly", "hobgoblin chieftain"]
        assert result.resolutions["gray jelly"].method == "unresolved"
        assert result.resolutions["gray jelly"].template_id is None
        assert result.resolutions["hobgoblin chieftain"].method == "llm"
        assert result.resolutions["hobgoblin chieftain"].template_id == "hobgoblin"
        run = workdir.read_run()
        status = run.stages[Stage.MONSTERS]
        assert status.usage is not None and status.usage.input_tokens == 100
        assert run.model_id == "stub-model-1"

    def test_empty_population_writes_an_empty_cache(self, tmp_path: Path):
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": []})
        result = monsters(workdir, PoisonedProvider())
        assert result.resolutions == {}
        assert workdir.monsters_json.is_file()
        assert workdir.read_run().stages[Stage.MONSTERS].status == "completed"

    def test_requires_completed_content(self, tmp_path: Path):
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": []})
        run = workdir.read_run()
        workdir.write_run(run.with_stage(Stage.CONTENT, StageStatus(status="running")))
        with pytest.raises(ValueError, match="content"):
            monsters(workdir, PoisonedProvider())

    def test_requires_every_level_cache(self, tmp_path: Path):
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": []})
        workdir.areas_json("lair", 1).unlink()
        with pytest.raises(ValueError, match="content cache is missing"):
            monsters(workdir, PoisonedProvider())

    def test_provider_failure_marks_stage_failed(self, tmp_path: Path):
        from osrforge.errors import ProviderError

        workdir = stage_workdir(tmp_path / "mod.forge", {"1": ["gray jelly"]})
        with pytest.raises(ProviderError):
            monsters(workdir, ScriptedProvider([ProviderError("rate limited")]))
        run = workdir.read_run()
        assert run.stages[Stage.MONSTERS].status == "failed"
        assert not workdir.monsters_json.exists()


def test_empty_names_are_excluded_from_the_population(tmp_path: Path):
    workdir = stage_workdir(tmp_path / "mod.forge", {"1": ["", "  ", "goblin"]})
    result = monsters(workdir, PoisonedProvider())
    assert set(result.resolutions) == {"goblin"}


def level_content(areas: dict[str, tuple[list[str], list[int]]]) -> LevelContent:
    """One fabricated level: area key → (monster names, source pages)."""
    return LevelContent.model_validate(
        {
            "schema_version": 1,
            "dungeon_id": "lair",
            "level_number": 1,
            "areas": [
                {
                    "key": key,
                    "description": "",
                    "encounters": [
                        {"monster": monster, "count_fixed": 1, "count_dice": None, "count_note": None}
                        for monster in names
                    ],
                    "trap": None,
                    "treasure": [],
                    "features": [],
                    "connections": [],
                    "source_pages": pages,
                    "confidence": 0.9,
                }
                for key, (names, pages) in areas.items()
            ],
        }
    )


class TestStatblockTag:
    @pytest.mark.parametrize(
        ("name", "tag"),
        [
            ("orc chief", "statblock.orc-chief"),
            ("human magic-user 4", "statblock.human-magic-user-4"),
            ("tentacle worm", "statblock.tentacle-worm"),
            ("½", "statblock.unnamed"),
        ],
    )
    def test_slugging(self, name: str, tag: str):
        assert statblock_tag(name) == tag


class TestStatblockPagePlan:
    def test_encounter_pages_then_ascending_text_hits(self):
        levels = [level_content({"1": (["tentacle worm"], [7, 3])})]
        texts = {1: "nothing", 5: "the Tentacle   Worm's lair", 2: "a tentacle worm again"}
        assert statblock_page_plan("tentacle worm", levels, texts) == (3, 7, 2, 5)

    def test_text_hit_already_an_encounter_page_is_not_repeated(self):
        levels = [level_content({"1": (["orc war leader"], [4])})]
        texts = {4: "the orc war leader", 9: "orc war leader again"}
        assert statblock_page_plan("orc war leader", levels, texts) == (4, 9)

    def test_cap_prefers_encounter_pages(self):
        levels = [level_content({"1": (["gnoll"], [1, 2, 3, 4, 5, 6, 7])})]
        texts = {page: "a gnoll" for page in range(8, 15)}
        plan = statblock_page_plan("gnoll", levels, texts)
        assert len(plan) == STATBLOCK_PAGE_CAP
        assert plan == (1, 2, 3, 4, 5, 6, 7, 8)

    def test_empty_text_layer_degrades_to_encounter_pages(self):
        levels = [level_content({"1": (["gnoll"], [2, 5])})]
        texts = {page: "" for page in range(1, 6)}
        assert statblock_page_plan("gnoll", levels, texts) == (2, 5)

    def test_name_absent_everywhere_plans_nothing(self):
        levels = [level_content({"1": (["gnoll"], [])})]
        texts = {1: "no monsters here"}
        assert statblock_page_plan("gnoll", levels, texts) == ()

    def test_only_the_named_monsters_encounters_contribute_pages(self):
        levels = [level_content({"1": (["gnoll"], [2]), "2": (["orc"], [9])})]
        assert statblock_page_plan("gnoll", levels, {}) == (2,)


def statblock_answer(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "found": True,
        "ac": "5 [14]",
        "ac_notation": "dual",
        "thac0": None,
        "hit_dice": "3+1",
        "class_level": None,
        "hp": 14,
        "attacks": ["1 bite (1d6)"],
        "movement": "120' (40')",
        "saves": "D12 W13 P14 B15 S16",
        "morale": 8,
        "alignment": "C",
        "xp": None,
        "number_appearing": "1d6 (2d6)",
        "special": ["paralysing touch"],
        "confidence": 0.9,
        "source_pages": [2],
    }
    return {**base, **overrides}


class TestStatblockRequest:
    def test_schema_covers_every_block_field_and_the_found_marker(self):
        request = build_statblock_request("tentacle worm", (TextPart(text="[page 2]\nworm"), ImagePart(png=b"png")))
        assert request.tag == "statblock.tentacle-worm"
        schema = request.schema
        properties = cast(dict[str, Any], schema["properties"])
        assert set(cast(list[str], schema["required"])) == set(properties)
        assert "found" in properties
        assert cast(dict[str, Any], properties["ac_notation"])["enum"] == ["descending", "ascending", "dual", None]
        assert schema["additionalProperties"] is False

    def test_header_names_the_creature_and_parts_follow(self):
        parts = (TextPart(text="[page 2]\nworm"), ImagePart(png=b"png"))
        request = build_statblock_request("tentacle worm", parts)
        header = request.parts[0]
        assert isinstance(header, TextPart)
        assert '"tentacle worm"' in header.text
        assert request.parts[1:] == parts

    def test_request_is_pure(self):
        parts = (TextPart(text="[page 2]\nworm"), ImagePart(png=b"png"))
        assert (
            build_statblock_request("worm", parts).fingerprint() == build_statblock_request("worm", parts).fingerprint()
        )

    def test_empty_parts_is_misuse(self):
        with pytest.raises(ValueError, match="at least one page part"):
            build_statblock_request("worm", ())


def read_statblocks(workdir: Workdir) -> StatBlocks:
    return StatBlocks.model_validate_json(workdir.statblocks_json.read_text(encoding="utf-8"))


class TestStatblockPass:
    def test_unresolved_name_gets_a_transcription_request_over_its_planned_pages(self, tmp_path: Path):
        workdir = stage_workdir(
            tmp_path / "mod.forge",
            {"1": ["gray jelly"]},
            page_count=3,
            source_pages_by_area={"1": [2]},
        )
        provider = ScriptedProvider([{"gray jelly": {"template_id": None}}, statblock_answer()])
        monsters(workdir, provider)
        resolution_request, block_request = provider.requests
        assert resolution_request.tag == "monsters"
        assert block_request.tag == "statblock.gray-jelly"
        texts = [part.text for part in block_request.parts if isinstance(part, TextPart)]
        assert any(text.startswith("[page 2]") for text in texts)
        assert any(isinstance(part, ImagePart) for part in block_request.parts)
        cache = read_statblocks(workdir)
        assert cache.custom_monsters == "emit"
        expected = {key: value for key, value in statblock_answer().items() if key != "found"}
        assert cache.blocks == {"gray jelly": RawStatBlock.model_validate(expected)}

    def test_found_false_caches_an_explicit_absent_marker(self, tmp_path: Path):
        workdir = stage_workdir(
            tmp_path / "mod.forge", {"1": ["gray jelly"]}, page_count=2, source_pages_by_area={"1": [1]}
        )
        provider = ScriptedProvider([{"gray jelly": {"template_id": None}}, {**statblock_answer(), "found": False}])
        monsters(workdir, provider)
        cache = json.loads(workdir.statblocks_json.read_text(encoding="utf-8"))
        assert cache["blocks"] == {"gray jelly": None}

    def test_empty_page_plan_writes_an_absent_marker_without_a_model_call(self, tmp_path: Path):
        # No source pages and no text-layer hit: the pass has nothing to send.
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": ["gray jelly"]})
        provider = ScriptedProvider([{"gray jelly": {"template_id": None}}])
        monsters(workdir, provider)
        assert len(provider.requests) == 1
        cache = json.loads(workdir.statblocks_json.read_text(encoding="utf-8"))
        assert cache["blocks"] == {"gray jelly": None}

    def test_text_layer_hits_widen_the_page_set(self, tmp_path: Path):
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": ["gray jelly"]}, page_count=3)
        workdir.page_txt(3).write_text("Appendix: the Gray Jelly, a bespoke horror.\n", encoding="utf-8")
        provider = ScriptedProvider([{"gray jelly": {"template_id": None}}, statblock_answer()])
        monsters(workdir, provider)
        block_request = provider.requests[1]
        texts = [part.text for part in block_request.parts if isinstance(part, TextPart)]
        assert any(text.startswith("[page 3]") for text in texts)

    def test_off_skips_the_pass_and_writes_the_echo_with_empty_blocks(self, tmp_path: Path):
        workdir = stage_workdir(
            tmp_path / "mod.forge",
            {"1": ["gray jelly"]},
            page_count=2,
            settings=ConversionSettings(custom_monsters="off"),
            source_pages_by_area={"1": [1]},
        )
        provider = ScriptedProvider([{"gray jelly": {"template_id": None}}])
        monsters(workdir, provider)
        assert len(provider.requests) == 1  # the resolution tier only
        cache = read_statblocks(workdir)
        assert cache.custom_monsters == "off"
        assert cache.blocks == {}

    def test_every_unresolved_name_gets_an_entry(self, tmp_path: Path):
        workdir = stage_workdir(
            tmp_path / "mod.forge",
            {"1": ["gray jelly"], "2": ["tentacle worm"]},
            page_count=2,
            source_pages_by_area={"2": [2]},
        )
        provider = ScriptedProvider(
            [
                {"gray jelly": {"template_id": None}, "tentacle worm": {"template_id": None}},
                statblock_answer(source_pages=[2]),
            ]
        )
        monsters(workdir, provider)
        cache = read_statblocks(workdir)
        # "gray jelly" planned no pages (absent marker); "tentacle worm" transcribed.
        assert list(cache.blocks) == ["gray jelly", "tentacle worm"]
        assert cache.blocks["gray jelly"] is None
        assert cache.blocks["tentacle worm"] is not None

    def test_fully_resolved_population_still_writes_the_cache(self, tmp_path: Path):
        workdir = stage_workdir(tmp_path / "mod.forge", {"1": ["goblin"]})
        monsters(workdir, PoisonedProvider())
        cache = read_statblocks(workdir)
        assert cache.custom_monsters == "emit"
        assert cache.blocks == {}

    def test_resolved_names_never_reach_the_pass(self, tmp_path: Path):
        # The LLM tier resolves the name, so the pass has no population.
        workdir = stage_workdir(
            tmp_path / "mod.forge", {"1": ["hobgoblin chieftain"]}, page_count=2, source_pages_by_area={"1": [1]}
        )
        provider = ScriptedProvider([{"hobgoblin chieftain": {"template_id": "hobgoblin"}}])
        monsters(workdir, provider)
        assert len(provider.requests) == 1
        assert read_statblocks(workdir).blocks == {}
