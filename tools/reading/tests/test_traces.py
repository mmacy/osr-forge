"""The trace builder and the bar on the committed Chaotic Caves key, offline."""

from pathlib import Path

import pytest
from tools.reading.bar import key_blocked
from tools.reading.maps import key_document, key_panel, load_maps
from tools.reading.traces import patched_panel, score_panel, tally, universe

from osrforge.tracing.fivefoot import convert_five_foot
from osrforge.tracing.score import EdgeTally

TOOL = Path(__file__).resolve().parent.parent
MAPS = {screen.id: screen for screen in load_maps(TOOL / "maps.json")}


@pytest.mark.parametrize("map_id", sorted(MAPS))
def test_the_key_scores_perfect_against_itself_over_its_own_universe(map_id: str) -> None:
    screen = MAPS[map_id]
    panel = key_panel(screen)
    every, whole = score_panel(key_document(screen.key), panel)
    converted = convert_five_foot(panel).panel if panel.scale_feet == 5 else panel
    assert every.edges.all.total == len(universe(converted)) > 0
    assert whole.edges.all.total == len(universe(converted, whole=True))
    assert every.edges.all.right == every.edges.all.total
    assert every.floor.perfect and every.doors.perfect


def test_one_opened_wall_is_one_wall_read_as_open() -> None:
    screen = MAPS["jn1-p047"]
    panel = key_panel(screen)
    wall = next(edge for edge in universe(panel) if panel.edges.at(*edge) == "w")
    every, _whole = score_panel(key_document(screen.key), patched_panel(panel, {wall: "o"}))
    assert every.edges.all.walls_read_as_open == 1
    assert every.edges.all.open_read_as_blocked == 0
    assert every.edges.all.right == every.edges.all.total - 1


def test_one_closed_opening_is_one_open_edge_read_as_blocked() -> None:
    screen = MAPS["jn1-p021-cave"]
    panel = key_panel(screen)
    opening = next(edge for edge in universe(panel) if panel.edges.at(*edge) == "o")
    every, _whole = score_panel(key_document(screen.key), patched_panel(panel, {opening: "w"}))
    assert (every.edges.all.walls_read_as_open, every.edges.all.open_read_as_blocked) == (0, 1)


def test_key_blocked_counts_the_converted_universe_of_a_five_foot_panel() -> None:
    screen = MAPS["jn1-p021-halfling"]
    converted = convert_five_foot(key_panel(screen)).panel
    every, _whole = score_panel(key_document(screen.key), key_panel(screen))
    assert key_blocked(screen, whole=False) == sum(
        converted.edges.at(*edge) not in "op" for edge in universe(converted)
    )
    assert key_blocked(screen, whole=False) < every.edges.all.total


def test_tally_sums_each_count() -> None:
    first = EdgeTally(total=10, right=8, walls_read_as_open=1, open_read_as_blocked=1)
    second = EdgeTally(total=5, right=5, walls_read_as_open=0, open_read_as_blocked=0)
    assert tally([first, second]) == EdgeTally(total=15, right=13, walls_read_as_open=1, open_read_as_blocked=1)
