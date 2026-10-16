"""The reference resolution script on every committed synthetic map: each exact reading resolves to its exact answer."""

from pathlib import Path

import pytest
from tools.answer_keys.resolve import differences, load_caches, reading_of, resolve, strip_flags

from osrforge.tracing.truth import canonical_form, load_geometry_truth

MAPS = Path(__file__).resolve().parents[3] / "tests" / "assets" / "synthetic-maps"
MAP_DIRS = sorted(path for path in MAPS.iterdir() if (path / "geometry.json").is_file())

OPEN_CASES = {
    "greyrock-country": "open case 1: the conventions give no text for a fallback level's reason",
    "lost-maps": "open case 1: the conventions give no text for a fallback level's reason",
}
"""Maps whose answers differ from the script's result only where the conventions are silent, by case number in
`~/.re-pod/map-plan/osr-suite/resolver/cases.md`."""


def _param(path: Path):
    reason = OPEN_CASES.get(path.name)
    marks = [pytest.mark.xfail(reason=reason, strict=True)] if reason else []
    return pytest.param(path, id=path.name, marks=marks)


@pytest.mark.parametrize("directory", [_param(path) for path in MAP_DIRS])
def test_reading_resolves_to_the_answer(directory):
    answer = load_geometry_truth(directory / "geometry.json")
    survey, contents, mapread = load_caches(directory)
    result = resolve(answer, survey, contents, mapread)
    assert differences(answer, result) == []
    assert strip_flags(canonical_form(answer)) == strip_flags(result)


@pytest.mark.parametrize("directory", MAP_DIRS, ids=[path.name for path in MAP_DIRS])
def test_an_open_case_differs_only_where_the_conventions_are_silent(directory):
    if directory.name not in OPEN_CASES:
        pytest.skip("not an open case")
    answer = load_geometry_truth(directory / "geometry.json")
    survey, contents, mapread = load_caches(directory)
    result = resolve(answer, survey, contents, mapread)
    lines = differences(answer, result)
    assert lines and all(" reason: " in line for line in lines)


@pytest.mark.parametrize("directory", MAP_DIRS, ids=[path.name for path in MAP_DIRS])
def test_the_reading_carries_no_resolution(directory):
    answer = load_geometry_truth(directory / "geometry.json")
    reading = reading_of(answer)
    assert all(level.verdict == "fallback" for dungeon in reading.dungeons for level in dungeon.levels)
    assert all(dungeon.entrance is None for dungeon in reading.dungeons)
    assert (reading.pages, reading.regions, reading.panels, reading.half_pairs) == (
        answer.pages,
        answer.regions,
        answer.panels,
        answer.half_pairs,
    )


def test_a_different_answer_is_reported():
    first, second = MAP_DIRS[0], MAP_DIRS[1]
    answer = load_geometry_truth(first / "geometry.json")
    other = load_geometry_truth(second / "geometry.json")
    assert differences(answer, other) != []
