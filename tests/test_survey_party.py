"""Acceptance tests for the survey's party reading: chunk `survey-party`.

The survey answer gains a required, nullable `party` property
([`PARTY_SCHEMA`][osrforge.survey.PARTY_SCHEMA]); windows merge by first
non-null occurrence, taken whole; normalization turns the answer into an
osrlib `PartySpec`, or `None` when the module states none or `PartySpec`
rejects the reading. The last test reads the minimod goldens, whose page 1
states "for characters of level 1", so it passes only after the minimod
fixtures are re-recorded under the new schema.
"""

import json
from pathlib import Path

import jsonschema
import pytest
from osrlib.crawl.adventure import PartySpec

from conftest import ScriptedProvider, fabricate_workdir
from osrforge.contracts.stages import SurveyIndex
from osrforge.survey import PARTY_SCHEMA, SURVEY_SCHEMA, merge_survey_answers, normalize_party, normalize_survey, survey
from test_survey import raw_census, raw_survey

STUB = pytest.mark.xfail(reason="chunk: survey-party", raises=NotImplementedError, strict=True)
WIRING = pytest.mark.xfail(reason="chunk: survey-party", strict=True)

SIX_TO_EIGHT = {"min_level": 1, "max_level": 3, "min_size": 6, "max_size": 8}
LEVEL_TWO_TO_FOUR = {"min_level": 2, "max_level": 4, "min_size": None, "max_size": None}
MINIMOD = Path(__file__).parent / "assets" / "minimod" / "expected"


class TestPartySchema:
    @pytest.mark.parametrize(
        "value",
        [None, SIX_TO_EIGHT, LEVEL_TWO_TO_FOUR, {"min_level": 1, "max_level": 1, "min_size": None, "max_size": 4}],
    )
    def test_accepts_null_and_complete_readings(self, value):
        jsonschema.validate(value, PARTY_SCHEMA)

    @pytest.mark.parametrize(
        "value",
        [
            {"min_level": 0, "max_level": 3, "min_size": None, "max_size": None},
            {"min_level": 1, "max_level": 3, "min_size": 0, "max_size": None},
            {"min_level": 1, "max_level": 3, "min_size": None},
            {"min_level": 1, "max_level": 3, "min_size": None, "max_size": None, "classes": "any"},
            {"min_level": "1", "max_level": 3, "min_size": None, "max_size": None},
        ],
    )
    def test_rejects_out_of_range_missing_and_extra_fields(self, value):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, PARTY_SCHEMA)

    @WIRING
    def test_survey_schema_requires_party(self):
        properties = SURVEY_SCHEMA["properties"]
        assert isinstance(properties, dict)
        assert properties["party"] == PARTY_SCHEMA
        required = SURVEY_SCHEMA["required"]
        assert isinstance(required, list)
        assert "party" in required


class TestNormalizeParty:
    @STUB
    def test_null_means_no_party(self):
        assert normalize_party(None) is None

    @STUB
    def test_a_reading_becomes_a_party_spec(self):
        assert normalize_party(SIX_TO_EIGHT) == PartySpec(min_level=1, max_level=3, min_size=6, max_size=8)
        assert normalize_party(LEVEL_TWO_TO_FOUR) == PartySpec(min_level=2, max_level=4)

    @STUB
    @pytest.mark.parametrize(
        "value",
        [
            {"min_level": 3, "max_level": 1, "min_size": None, "max_size": None},
            {"min_level": 1, "max_level": 3, "min_size": 8, "max_size": 6},
            {"min_level": 0, "max_level": 3, "min_size": None, "max_size": None},
        ],
    )
    def test_a_reading_party_spec_rejects_gives_none(self, value):
        assert normalize_party(value) is None


class TestMergeParty:
    @WIRING
    def test_first_non_null_window_wins_whole(self):
        merged = merge_survey_answers(
            [raw_survey(party=None), raw_survey(party=SIX_TO_EIGHT), raw_survey(party=LEVEL_TWO_TO_FOUR)]
        )
        assert merged["party"] == SIX_TO_EIGHT

    @WIRING
    def test_no_window_stating_a_party_merges_to_null(self):
        merged = merge_survey_answers([raw_survey(party=None), raw_survey(party=None)])
        assert "party" in merged
        assert merged["party"] is None

    @WIRING
    def test_an_answer_without_the_key_counts_as_null(self):
        merged = merge_survey_answers([raw_survey(), raw_survey(party=LEVEL_TWO_TO_FOUR)])
        assert merged["party"] == LEVEL_TWO_TO_FOUR

    @WIRING
    def test_the_merge_does_not_mutate_its_inputs(self):
        later = raw_survey(party=dict(SIX_TO_EIGHT))
        merged = merge_survey_answers([raw_survey(party=None), later])
        merged["party"]["max_size"] = 99
        assert later["party"] == SIX_TO_EIGHT


class TestNormalizeSurveyParty:
    @WIRING
    def test_the_index_carries_the_normalized_party(self):
        index = normalize_survey(raw_survey(party=SIX_TO_EIGHT), page_count=48)
        assert index.party == PartySpec(min_level=1, max_level=3, min_size=6, max_size=8)

    def test_a_null_party_gives_none(self):
        assert normalize_survey(raw_survey(party=None), page_count=48).party is None

    def test_a_rejected_reading_gives_none_and_keeps_the_rest(self):
        index = normalize_survey(
            raw_survey(party={"min_level": 3, "max_level": 1, "min_size": None, "max_size": None}), page_count=48
        )
        assert index.party is None
        assert index.title == "The Chaotic Caves"

    @WIRING
    def test_the_stage_caches_the_party(self, tmp_path: Path):
        workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=2)
        survey(workdir, ScriptedProvider([raw_survey(party=LEVEL_TWO_TO_FOUR), raw_census()]))
        cached = SurveyIndex.model_validate_json(workdir.survey_json.read_text(encoding="utf-8"))
        assert cached.party == PartySpec(min_level=2, max_level=4)


def test_a_cache_without_the_field_loads_with_no_party():
    cached = json.loads((MINIMOD / "survey.json").read_text(encoding="utf-8"))
    cached.pop("party", None)
    assert SurveyIndex.model_validate(cached).party is None


@WIRING
def test_minimod_goldens_carry_its_printed_party():
    # Page 1: "A one-evening dungeon crawl for characters of level 1."
    expected = {"min_level": 1, "max_level": 1, "min_size": None, "max_size": None}
    cached = json.loads((MINIMOD / "survey.json").read_text(encoding="utf-8"))
    assert cached["party"] == expected
    adventure = json.loads((MINIMOD / "adventure.json").read_text(encoding="utf-8"))
    assert adventure["payload"]["party"] == expected
