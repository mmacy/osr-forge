"""Acceptance tests for the party on the draft: chunk `assemble-party`.

The draft's `Adventure.party` is the survey's `SurveyIndex.party` unless the
`module:` override sets `party`: a mapping replaces it whole and an explicit
`null` clears it. These tests build caches by hand, so they need no model and
no recorded fixture.
"""

import json
from pathlib import Path

import pydantic
import pytest
from osrlib.crawl.adventure import PartySpec

from osrforge.assemble import assemble, resolve_party
from osrforge.contracts.overrides import ModuleOverride, load_overrides
from osrforge.contracts.stages import SurveyIndex
from osrforge.overrides import plan_overrides
from osrforge.workdir import write_json_artifact
from test_overrides_apply import built, parse_overrides, synthetic_index, synthetic_workdir

STUB = pytest.mark.xfail(reason="chunk: assemble-party", raises=NotImplementedError, strict=True)
WIRING = pytest.mark.xfail(reason="chunk: assemble-party", strict=True)

SURVEYED = PartySpec(min_level=1, max_level=3, min_size=6, max_size=8)
CORRECTED = PartySpec(min_level=2, max_level=5, min_size=4)


def index_with_party(party: PartySpec | None = SURVEYED) -> SurveyIndex:
    return synthetic_index().model_copy(update={"party": party})


class TestResolveParty:
    @STUB
    def test_no_override_keeps_the_survey_party(self):
        assert resolve_party(index_with_party(), None) == SURVEYED

    @STUB
    def test_no_override_and_no_survey_party_gives_none(self):
        assert resolve_party(index_with_party(None), None) is None

    @STUB
    def test_an_override_that_leaves_party_unset_keeps_the_survey_party(self):
        override = ModuleOverride(name="The Barrow of Kings", reason="The cover's full title.")
        assert resolve_party(index_with_party(), override) == SURVEYED

    @STUB
    def test_a_mapping_replaces_the_survey_party_whole(self):
        override = ModuleOverride(party=CORRECTED, reason="The introduction on p. 2 says 4+ characters of levels 2-5.")
        assert resolve_party(index_with_party(), override) == CORRECTED

    @STUB
    def test_a_mapping_supplies_a_party_the_survey_missed(self):
        override = ModuleOverride(party=CORRECTED, reason="The back cover states the party.")
        assert resolve_party(index_with_party(None), override) == CORRECTED

    @STUB
    def test_an_explicit_null_clears_the_survey_party(self):
        override = ModuleOverride(party=None, reason="The module states no party; the survey misread a sidebar.")
        assert resolve_party(index_with_party(), override) is None


class TestDraftParty:
    @WIRING
    def test_the_draft_carries_the_survey_party(self):
        assert built(index=index_with_party()).adventure.party == SURVEYED

    @WIRING
    def test_a_yaml_override_replaces_it_and_unstated_sizes_default(self):
        draft = built(
            "module:\n  party: {min_level: 2, max_level: 5, min_size: 4}\n  reason: p. 2 states the party.\n",
            index=index_with_party(),
        )
        assert draft.adventure.party == CORRECTED

    def test_a_yaml_null_leaves_the_draft_without_a_party(self):
        draft = built("module:\n  party: null\n  reason: The module states none.\n", index=index_with_party())
        assert draft.adventure.party is None

    def test_no_party_adds_no_module_flag(self):
        assert built(index=index_with_party(None)).module_flags == ()


class TestPartyOverrideContract:
    def test_a_party_only_module_override_takes_effect(self):
        overrides = parse_overrides("module:\n  party: null\n  reason: The module states none.\n")
        plan = plan_overrides(synthetic_index(), overrides)
        assert plan.module is not None
        assert plan.module.model_fields_set == {"party", "reason"}

    def test_a_reversed_range_fails_at_load(self, tmp_path: Path):
        path = tmp_path / "overrides.yaml"
        path.write_text("module:\n  party: {min_level: 3, max_level: 1}\n  reason: typo\n", encoding="utf-8")
        with pytest.raises(pydantic.ValidationError, match="max_level must not be below min_level"):
            load_overrides(path)


@WIRING
def test_assemble_writes_the_overridden_party_into_adventure_json(tmp_path: Path):
    workdir = synthetic_workdir(
        tmp_path / "mod.forge",
        "module:\n  party: {min_level: 2, max_level: 5, min_size: 4}\n  reason: p. 2 states the party.\n",
    )
    write_json_artifact(workdir.survey_json, index_with_party())
    result = assemble(workdir.root)
    assert result.adventure.party == CORRECTED
    payload = json.loads(workdir.adventure_json.read_text(encoding="utf-8"))["payload"]
    assert payload["party"] == {"min_level": 2, "max_level": 5, "min_size": 4, "max_size": None}
