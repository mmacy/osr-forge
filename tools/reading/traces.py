"""A reading under test as a trace document, scored with the scorer's own code.

The measurement scores a reading of one panel (the classifier's, Clef's, or the stage's) with
[`score_trace`][osrforge.tracing.score.score_trace] on two one-panel documents: the key cut down to that panel, and
the same document with the reading in the panel's place. Each has the panel's region and a placeholder dungeon whose
one level fell back, so no level uses the panel and the scorer's floor, edges, and doors families compare it alone,
with the scorer's alignment, 5-foot conversion, edge universe, and tallies. The resolution families have nothing to
compare.

A reading of part of a panel (a few windows) is scored the same way: the panel is the key's panel with the read
edges' letters put in, so an edge outside the part is right by construction and every error the scorer counts lies
inside the part. The part's own edge count is then the denominator.
"""

from collections.abc import Iterable, Mapping

from osrforge.tracing.score import EdgeTally, ScoreSet, score_trace
from osrforge.tracing.truth import Dungeon, EdgeGrids, GeometryTruth, Level, Panel

__all__ = ["edge_letters", "one_panel", "patched_panel", "score_panel", "tally"]

_PLACEHOLDER = Dungeon(
    id="reading-measurement",
    name="reading measurement",
    levels=(Level(number=1, verdict="fallback", reason="a placeholder: the document compares one panel"),),
)


def one_panel(key: GeometryTruth, panel: Panel) -> GeometryTruth:
    """A document that holds one panel and its regions, with no level that uses it.

    Args:
        key: The answer key, for the panel's regions.
        panel: The key's panel or a reading of it. Its outlines, marks, and symbols are dropped: the outlines name
            the key's levels, the marks and symbols name cells a reading may not have as floor, and none of them
            counts in the floor, edges, or doors families.

    Returns:
        The document.

    Raises:
        KeyError: If the key lacks one of the panel's regions.
    """
    regions = tuple(key.region(part.region) for part in panel.regions)
    bare = Panel.model_validate(panel.model_dump() | {"outlines": (), "marks": (), "symbols": ()})
    return GeometryTruth(regions=regions, panels=(bare,), dungeons=(_PLACEHOLDER,))


def score_panel(key: GeometryTruth, panel: Panel) -> tuple[ScoreSet, ScoreSet]:
    """Score one panel's reading against the key's panel of the same id.

    Args:
        key: The answer key.
        panel: The reading.

    Returns:
        The scorer's score set over every cell, and without the cells the key marks as partly floor.

    Raises:
        KeyError: If the key has no panel with the reading's id.
    """
    score = score_trace(one_panel(key, panel), one_panel(key, key.panel(panel.id)))
    return score.after, score.after_whole


def edge_letters(edges: EdgeGrids) -> dict[tuple[tuple[int, int], str], str]:
    """Every edge's letter, by the cell and side that name it."""
    return {position: edges.at(*position) for position in edges.positions()}


def patched_panel(key_panel: Panel, letters: Mapping[tuple[tuple[int, int], str], str]) -> Panel:
    """The key's panel with some edges' letters replaced.

    Args:
        key_panel: The key's panel.
        letters: The new letter of each edge, by any cell and side that name it.

    Returns:
        The patched panel.
    """
    vertical = [list(row) for row in key_panel.edges.vertical]
    horizontal = [list(row) for row in key_panel.edges.horizontal]
    for ((x, y), side), letter in letters.items():
        if side == "west":
            vertical[y][x] = letter
        elif side == "east":
            vertical[y][x + 1] = letter
        elif side == "north":
            horizontal[y][x] = letter
        else:
            horizontal[y + 1][x] = letter
    edges = EdgeGrids(
        vertical=tuple("".join(row) for row in vertical), horizontal=tuple("".join(row) for row in horizontal)
    )
    return key_panel.model_copy(update={"edges": edges})


def tally(scores: Iterable[EdgeTally]) -> EdgeTally:
    """The sum of several edge tallies."""
    items = list(scores)
    return EdgeTally(
        total=sum(item.total for item in items),
        right=sum(item.right for item in items),
        walls_read_as_open=sum(item.walls_read_as_open for item in items),
        open_read_as_blocked=sum(item.open_read_as_blocked for item in items),
    )
