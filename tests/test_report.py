import json

import pytest
from pydantic import ValidationError

from osrforge.contracts.report import (
    AreaAddress,
    AreaReport,
    ExtractionReport,
    Flag,
    LevelAddress,
    ModuleInfo,
    MonsterSummary,
    ValidationResult,
    format_flag,
    parse_flag,
)
from osrforge.contracts.run import TokenUsage

# The spec's own report example (docs/spec.md § Extraction report), verbatim —
# keeping the spec and the models honest against each other.
SPEC_REPORT_EXAMPLE = """
{
  "schema_version": 1,
  "osrforge_version": "0.1.0",
  "module": { "title": "The Example Barrow", "pages": 48 },
  "validation": { "passed": false, "errors": ["..."] },
  "areas": [
    {
      "id": "barrow/1/7",
      "source_pages": [14],
      "confidence": 0.62,
      "flags": ["geometry_synthesized", "monster_unresolved:hobgoblin chieftain"],
      "overridden": ["description"]
    }
  ],
  "monsters": { "resolved": 11, "unresolved": ["hobgoblin chieftain"] },
  "usage": { "input_tokens": 412000, "output_tokens": 88000 }
}
"""


def make_report() -> ExtractionReport:
    return ExtractionReport(
        module=ModuleInfo(title="The Example Barrow", pages=48),
        validation=ValidationResult(passed=False, errors=("boom",)),
        areas=(
            AreaReport(
                id="barrow/1/7",
                source_pages=(14,),
                confidence=0.62,
                flags=("geometry_synthesized", "monster_unresolved:hobgoblin chieftain"),
                overridden=("description",),
            ),
        ),
        monsters=MonsterSummary(resolved=11, unresolved=("hobgoblin chieftain",)),
        usage=TokenUsage(input_tokens=412000, output_tokens=88000),
    )


def test_spec_report_example_parses_verbatim():
    report = ExtractionReport.model_validate(json.loads(SPEC_REPORT_EXAMPLE))
    assert report.schema_version == 1
    assert report.areas[0].id == "barrow/1/7"
    assert report.areas[0].flags == ("geometry_synthesized", "monster_unresolved:hobgoblin chieftain")


def test_report_round_trips():
    report = make_report()
    assert ExtractionReport.model_validate(report.model_dump(mode="json")) == report


@pytest.mark.parametrize("value", ["geometry_synthesized", "monster_unresolved:hobgoblin chieftain", "low_confidence"])
def test_flag_grammar_accepts(value: str):
    flag, detail = parse_flag(value)
    assert isinstance(flag, Flag)
    assert format_flag(flag, detail) == value


@pytest.mark.parametrize("value", ["", "bogus_flag", "bogus_flag:detail", "monster_unresolved:", ":detail"])
def test_flag_grammar_rejects(value: str):
    with pytest.raises(ValueError):
        parse_flag(value)


def test_flag_detail_may_contain_colons():
    flag, detail = parse_flag("connection_ambiguous:area 7: north or east")
    assert flag is Flag.CONNECTION_AMBIGUOUS
    assert detail == "area 7: north or east"


def test_format_flag_rejects_empty_detail():
    with pytest.raises(ValueError):
        format_flag(Flag.LOW_CONFIDENCE, "")


def test_report_rejects_unknown_flag_string():
    with pytest.raises(ValidationError):
        AreaReport(id="barrow/1/7", confidence=0.5, flags=("not_a_flag",))


def test_area_address_round_trips():
    address = AreaAddress.parse("barrow/1/7")
    assert address == AreaAddress(dungeon_id="barrow", level_number=1, area_key="7")
    assert str(address) == "barrow/1/7"


def test_level_address_round_trips():
    address = LevelAddress.parse("barrow/2")
    assert address == LevelAddress(dungeon_id="barrow", level_number=2)
    assert str(address) == "barrow/2"


@pytest.mark.parametrize(
    "value",
    [
        "barrow/1",
        "barrow/1/7/8",
        "barrow/one/7",
        "barrow/0/7",
        "/1/7",
        "barrow/1/",
        # Non-canonical digit spellings would alias the same area under two
        # override keys, so only ASCII digits without leading zeros pass.
        "barrow/01/7",
        "barrow/١/7",  # noqa: RUF001 — Arabic-Indic digit, deliberately non-ASCII
    ],
)
def test_area_address_rejects_malformed(value: str):
    with pytest.raises(ValueError):
        AreaAddress.parse(value)


@pytest.mark.parametrize("value", ["barrow/01", "barrow/٢", "barrow/0"])
def test_level_address_rejects_non_canonical_numbers(value: str):
    with pytest.raises(ValueError):
        LevelAddress.parse(value)


def test_address_components_reject_slash():
    with pytest.raises(ValidationError):
        AreaAddress(dungeon_id="bar/row", level_number=1, area_key="7")
    with pytest.raises(ValidationError):
        AreaAddress(dungeon_id="barrow", level_number=1, area_key="7/a")
    with pytest.raises(ValidationError):
        LevelAddress(dungeon_id="bar/row", level_number=1)


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        AreaReport(id="barrow/1/7", confidence=1.2)
    with pytest.raises(ValidationError):
        AreaReport(id="barrow/1/7", confidence=-0.1)


def test_report_rejects_unknown_keys():
    data = json.loads(SPEC_REPORT_EXAMPLE)
    data["surprise"] = True
    with pytest.raises(ValidationError):
        ExtractionReport.model_validate(data)
