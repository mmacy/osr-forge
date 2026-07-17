import pytest
from pydantic import ValidationError

from osrforge.contracts.stages import (
    CANONICAL_SLUG_PATTERN,
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


def make_area(key: str = "1", **overrides) -> SurveyArea:
    fields = {
        "key": key,
        "name": "Entrance",
        "source_label": None,
        "kind": "cave",
        "source_pages": (22,),
    } | overrides
    return SurveyArea.model_validate(fields)


def make_index() -> SurveyIndex:
    return SurveyIndex(
        title="The Chaotic Caves",
        description="An introductory adventure.",
        hooks=("A rumor of treasure",),
        town=TownInfo(name="", description="A trade town.", services=("The Gilded Goat inn",)),
        dungeons=(
            SurveyDungeon(
                id="a-orc-lair",
                name="A. Orc Lair",
                levels=(SurveyLevel(number=1, map_pages=(38,), areas=(make_area(), make_area(key="5-2"))),),
            ),
        ),
        monster_names=("orc", "wolf"),
    )


def make_area_content(key: str = "1") -> AreaContent:
    return AreaContent(
        key=key,
        description="A gaping entranceway.",
        encounters=(AreaEncounter(monster="orc", count_fixed=6),),
        trap=None,
        treasure=("Each orc has 1d6 sp",),
        features=("A crude wooden barricade",),
        connections=(AreaConnection(to_key="2", direction="north"),),
        source_pages=(22,),
        confidence=0.91,
    )


def make_level_content() -> LevelContent:
    return LevelContent(dungeon_id="a-orc-lair", level_number=1, areas=(make_area_content(),))


def test_survey_index_round_trips():
    index = make_index()
    assert SurveyIndex.model_validate(index.model_dump(mode="json")) == index


def test_level_content_round_trips():
    level = make_level_content()
    assert LevelContent.model_validate(level.model_dump(mode="json")) == level


def test_pre_phase_6_survey_cache_shape_still_loads():
    """A cache written before `description`/`services` existed loads with the defaults."""
    payload = make_index().model_dump(mode="json")
    del payload["description"]
    del payload["town"]["services"]
    index = SurveyIndex.model_validate(payload)
    assert index.description == ""
    assert index.town.services == ()


def test_caches_carry_schema_version_only():
    assert "schema_version" in SurveyIndex.model_fields
    assert "schema_version" in LevelContent.model_fields
    assert "osrforge_version" not in SurveyIndex.model_fields
    assert "osrforge_version" not in LevelContent.model_fields


def test_confidence_bounds():
    payload = make_area_content().model_dump(mode="json")
    for bad in (-0.1, 1.1):
        with pytest.raises(ValidationError):
            AreaContent.model_validate(payload | {"confidence": bad})


def test_count_fixed_floor():
    with pytest.raises(ValidationError):
        AreaEncounter(monster="orc", count_fixed=0)


def test_unknown_keys_rejected():
    for model, payload in (
        (SurveyIndex, make_index().model_dump(mode="json")),
        (LevelContent, make_level_content().model_dump(mode="json")),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload | {"surprise": 1})


def test_models_are_frozen():
    with pytest.raises(ValidationError):
        make_index().title = "renamed"
    with pytest.raises(ValidationError):
        make_level_content().dungeon_id = "other"


def test_non_canonical_ids_rejected():
    for bad in ("A. Orc Lair", "orc/lair", "orc.lair", "4A", "-orc", "orc-", "orc--lair", ""):
        assert not CANONICAL_SLUG_PATTERN.match(bad)
        with pytest.raises(ValidationError):
            SurveyDungeon(id=bad, name="x", levels=(SurveyLevel(number=1, map_pages=(), areas=()),))
        with pytest.raises(ValidationError):
            make_area(key=bad)
        with pytest.raises(ValidationError):
            LevelContent(dungeon_id=bad, level_number=1, areas=())


def test_duplicate_area_keys_rejected_per_level():
    with pytest.raises(ValidationError):
        SurveyLevel(number=1, map_pages=(), areas=(make_area(), make_area()))


def test_duplicate_level_numbers_rejected_per_dungeon():
    level = SurveyLevel(number=1, map_pages=(), areas=(make_area(),))
    with pytest.raises(ValidationError):
        SurveyDungeon(id="barrow", name="Barrow", levels=(level, level))


def test_dungeon_requires_at_least_one_level():
    with pytest.raises(ValidationError):
        SurveyDungeon(id="barrow", name="Barrow", levels=())


def test_encounter_count_fields_are_independent():
    # The cache stores what the model said; osrlib's exactly-one-of mapping is
    # phase 2's job.
    both = AreaEncounter(monster="orc", count_fixed=2, count_dice="2d4", count_note="more at night")
    neither = AreaEncounter(monster="orc")
    assert both.count_fixed == 2 and both.count_dice == "2d4"
    assert neither.count_fixed is None and neither.count_dice is None and neither.count_note is None


class TestMonsterResolutions:
    def make(self) -> MonsterResolutions:
        return MonsterResolutions(
            resolutions={
                "zombie": MonsterResolution(template_id="zombie", method="exact"),
                "gray jelly": MonsterResolution(template_id=None, method="unresolved"),
                "wolf": MonsterResolution(template_id="normal_wolf", method="alias"),
            }
        )

    def test_round_trips(self):
        cache = self.make()
        assert MonsterResolutions.model_validate(cache.model_dump(mode="json")) == cache

    def test_keys_are_sorted_ascending(self):
        cache = self.make()
        assert list(cache.resolutions) == ["gray jelly", "wolf", "zombie"]

    def test_frozen(self):
        cache = self.make()
        with pytest.raises(ValidationError):
            cache.schema_version = 2  # type: ignore[misc]

    def test_unknown_keys_rejected(self):
        with pytest.raises(ValidationError):
            MonsterResolutions.model_validate({"resolutions": {}, "surprise": 1})
        with pytest.raises(ValidationError):
            MonsterResolution.model_validate({"template_id": "orc", "method": "exact", "tier": 1})

    def test_template_id_iff_resolved(self):
        with pytest.raises(ValidationError):
            MonsterResolution(template_id=None, method="exact")
        with pytest.raises(ValidationError):
            MonsterResolution(template_id="orc", method="unresolved")

    def test_carries_schema_version_only(self):
        assert "schema_version" in MonsterResolutions.model_fields
        assert "osrforge_version" not in MonsterResolutions.model_fields
