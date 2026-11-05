"""The edges Clef's answers are measured on, and the reading a decision rule makes of them.

The measured edges of a map are the key's edges between two cells that are both floor or water in the key: the edges
whose letter the cells don't decide. An edge with rock on one side is blocked whatever is drawn there, because rock
is never walked onto, so the stage reads it as a wall unless the reading names a door there. On a 5-foot panel the
edges are the 5-foot segments, and the scorer converts the reading as it converts the key.

A reading made from answers is the key's panel with each measured edge's letter replaced by the decision's letter,
scored with [`score_panel`][tools.reading.traces.score_panel]. Its cells are the key's, so it measures the edge
reading alone.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from osrforge.tracing.truth import PASSABLE_LETTERS, Panel, parse_cell

from .answers import Answers, passable_probability
from .traces import patched_panel

__all__ = ["BLOCKED", "EdgeRow", "decide", "measured_edges", "reading_from", "rows_for"]

BLOCKED = "wndls"
"""The blocked letters, in the order a tie between them breaks."""

type Edge = tuple[tuple[int, int], str]

_NEIGHBOUR = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


@dataclass(frozen=True)
class EdgeRow:
    """One measured edge with the key's letter and Clef's answer."""

    map: str
    style: str
    edge: Edge
    key: str
    """The key's letter."""

    cave: bool
    """A cave cell lies on either side."""

    partial: bool
    """A partly floor cell lies on either side."""

    probabilities: dict[str, float] | None
    """Clef's probabilities by truth letter, or `None` when Clef wasn't asked."""

    @property
    def passable(self) -> bool:
        """The key reads the edge as passable."""
        return self.key in PASSABLE_LETTERS


def measured_edges(panel: Panel) -> list[Edge]:
    """The key's edges between two floor or water cells, each named once."""
    occupied = {(x, y) for y, row in enumerate(panel.cells) for x, letter in enumerate(row) if letter in ".~"}
    out = []
    for (x, y), side in panel.edges.positions():
        dx, dy = _NEIGHBOUR[side]
        if (x, y) in occupied and (x + dx, y + dy) in occupied:
            out.append(((x, y), side))
    return out


def rows_for(map_id: str, style: str, panel: Panel, answers: Answers) -> list[EdgeRow]:
    """The measured edges of one map with their answers.

    Args:
        map_id: The map's id.
        style: The map's style.
        panel: The key's panel.
        answers: Clef's answers on the key's grid.

    Returns:
        One row per measured edge.
    """
    cave = {parse_cell(text) for text in panel.cave}
    partial = {parse_cell(text) for text in panel.partial}
    rows = []
    for edge in measured_edges(panel):
        (x, y), side = edge
        dx, dy = _NEIGHBOUR[side]
        sides = {(x, y), (x + dx, y + dy)}
        rows.append(
            EdgeRow(
                map=map_id,
                style=style,
                edge=edge,
                key=panel.edges.at(*edge),
                cave=bool(sides & cave),
                partial=bool(sides & partial),
                probabilities=answers.edges.get(edge),
            )
        )
    return rows


def decide(probabilities: dict[str, float], passable: float, threshold: float) -> str:
    """The letter a passable threshold gives an edge.

    Args:
        probabilities: The probabilities by letter. Only their order among the blocked letters, and between `o`
            and `p`, is used.
        passable: The probability that the edge is passable, raw or calibrated.
        threshold: The style's passable threshold.

    Returns:
        `o` or `p`, whichever is more probable, when `passable` reaches the threshold, and otherwise the most probable
        blocked letter.
    """
    if passable >= threshold:
        return "p" if probabilities.get("p", 0.0) > probabilities.get("o", 0.0) else "o"
    return max(BLOCKED, key=lambda letter: (probabilities.get(letter, 0.0), -BLOCKED.index(letter)))


def reading_from(panel: Panel, rows: Iterable[EdgeRow], letter: Callable[[EdgeRow], str | None] | None = None) -> Panel:
    """The key's panel with each answered row's edge read by a rule.

    Args:
        panel: The key's panel.
        rows: The map's rows.
        letter: The rule: a row's letter, or `None` to keep the key's. By default the raw rule, passable when the
            raw passable probability is at least one half.

    Returns:
        The reading.
    """

    def raw(row: EdgeRow) -> str | None:
        if row.probabilities is None:
            return None
        return decide(row.probabilities, passable_probability(row.probabilities), 0.5)

    rule = letter or raw
    letters = {}
    for row in rows:
        value = rule(row)
        if value is not None:
            letters[row.edge] = value
    return patched_panel(panel, letters)
