"""The reference resolution script on every committed synthetic map: each exact reading resolves to its exact answer."""

from pathlib import Path

import pytest
from tools.answer_keys.resolve import differences, load_caches, reading_of, resolve, strip_flags

from osrforge.tracing.truth import canonical_form, load_geometry_truth

MAPS = Path(__file__).resolve().parents[3] / "tests" / "assets" / "synthetic-maps"
MAP_DIRS = sorted(path for path in MAPS.iterdir() if (path / "geometry.json").is_file())


@pytest.mark.parametrize("directory", MAP_DIRS, ids=[path.name for path in MAP_DIRS])
def test_reading_resolves_to_the_answer(directory):
    answer = load_geometry_truth(directory / "geometry.json")
    survey, contents, mapread = load_caches(directory)
    result = resolve(answer, survey, contents, mapread)
    assert differences(answer, result) == []
    assert strip_flags(canonical_form(answer)) == strip_flags(result)


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
