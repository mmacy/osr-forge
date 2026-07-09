"""The milestone credibility floor — zero network.

The committed JN1 stage caches (tests/assets/chaotic-caves/stages/) are the
machine-checkable evidence behind phase 1's roadmap milestone: one full eval
module extracting with credible per-area output and source pages. The caches
are static committed files, so these assertions are a regression gate on the
evidence, not on the live model. Floors were finalized against the actual
2026-07-09 run (14 dungeons, 137 areas, every area with source pages); see the
phase 1 plan's amendment.
"""

import json
from pathlib import Path

from osrforge.contracts.report import AreaAddress
from osrforge.contracts.stages import LevelContent, SurveyIndex

STAGES_DIR = Path(__file__).parent / "assets" / "chaotic-caves" / "stages"
JN1_PAGE_COUNT = 48
TOWN_SHAPED_IDS = {"town", "town-key"}
MIN_DUNGEONS = 10  # JN1 keys lairs A-J plus standalone sites
MIN_EXTRACTED_AREAS = 100


def load_survey() -> SurveyIndex:
    return SurveyIndex.model_validate_json((STAGES_DIR / "survey.json").read_text(encoding="utf-8"))


def load_levels() -> list[LevelContent]:
    return [
        LevelContent.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(STAGES_DIR.glob("areas.*.json"))
    ]


def test_survey_index_validates_with_addressable_areas():
    index = load_survey()
    assert index.title == "The Chaotic Caves"
    assert len(index.dungeons) >= MIN_DUNGEONS
    assert index.monster_names
    for dungeon in index.dungeons:
        assert dungeon.id not in TOWN_SHAPED_IDS
        for level in dungeon.levels:
            for area in level.areas:
                address = f"{dungeon.id}/{level.number}/{area.key}"
                assert str(AreaAddress.parse(address)) == address
                assert all(1 <= page <= JN1_PAGE_COUNT for page in area.source_pages)


def test_every_extracted_area_meets_the_floor():
    levels = load_levels()
    extracted = [area for level in levels for area in level.areas]
    assert len(extracted) >= MIN_EXTRACTED_AREAS
    for level in levels:
        for area in level.areas:
            assert area.description.strip(), f"{level.dungeon_id}/{level.level_number}/{area.key}"
            assert area.source_pages, f"{level.dungeon_id}/{level.level_number}/{area.key}"
            assert all(1 <= page <= JN1_PAGE_COUNT for page in area.source_pages)
            assert 0.0 <= area.confidence <= 1.0


def test_content_caches_pair_with_the_survey_index():
    index = load_survey()
    survey_levels = {
        (dungeon.id, level.number): {area.key for area in level.areas}
        for dungeon in index.dungeons
        for level in dungeon.levels
    }
    levels = load_levels()
    assert {(level.dungeon_id, level.level_number) for level in levels} == set(survey_levels)
    for level in levels:
        extracted_keys = {area.key for area in level.areas}
        # The survey index is the authority on what exists; content never
        # invents keys beyond it.
        assert extracted_keys <= survey_levels[(level.dungeon_id, level.level_number)]


def test_evidence_fixtures_are_committed_for_every_request():
    evidence = Path(__file__).parent / "assets" / "chaotic-caves" / "fixtures-extract" / "evidence"
    tags = sorted(json.loads(path.read_text(encoding="utf-8"))["tag"] for path in evidence.glob("*.json"))
    assert "survey" in tags
    content_tags = [tag for tag in tags if tag.startswith("content.")]
    # One batch request per level at minimum (the run needed no retries).
    assert len(content_tags) >= 15
