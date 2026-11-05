"""Clef's answers, on the key's grid: the answer-key pass's stored answers and the measurement's own.

An answers file is JSON lines, one answer per line, with at least `item` (`"x,y:side"` for an edge, `"x,y"` for a
cell), `kind` (`edge` or `cell`), and `probabilities` (option id to probability). The answer-key pass wrote them on
the answer key's working grid, so loading moves each item by the map's frame onto the key's registered grid and
drops items outside it. The measurement's own stores (`store.py`) write them on the key's grid already.

An edge's option ids are mapped to the truth format's letters with `LETTERS`, so answers to different option sets
compare: `open` and `printed_open_line` are the passable letters `o` and `p`.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from osrforge.tracing.truth import parse_cell, parse_edge

__all__ = ["LETTERS", "Answers", "load_answers", "passable_probability"]

LETTERS: dict[str, str] = {
    "open": "o",
    "printed_open_line": "p",
    "wall": "w",
    "window": "n",
    "door": "d",
    "locked_door": "l",
    "secret_door": "s",
}
"""Each edge option id's truth letter."""

type Edge = tuple[tuple[int, int], str]


@dataclass(frozen=True)
class Answers:
    """One map's answers on the key's grid."""

    edges: dict[Edge, dict[str, float]]
    """Each edge's probabilities by truth letter, named by the cell and side `EdgeGrids.positions` names it by."""

    cells: dict[tuple[int, int], dict[str, float]]
    """Each cell's probabilities by option id."""


def _canonical(cell: tuple[int, int], side: str, width: int, height: int) -> Edge:
    """The name `EdgeGrids.positions` gives an edge: from its west or north cell, except on the east and south frame."""
    x, y = cell
    if side == "east" and x + 1 < width:
        return (x + 1, y), "west"
    if side == "south" and y + 1 < height:
        return (x, y + 1), "north"
    return cell, side


def load_answers(path: Path, frame: tuple[int, int] | None, width: int, height: int) -> Answers:
    """Read an answers file onto the key's grid.

    Args:
        path: The JSON-lines file.
        frame: Where the key's grid starts on the file's grid, or `None` when they are one.
        width: The key's grid width.
        height: The key's grid height.

    Returns:
        The answers inside the key's grid. A later line for the same item replaces an earlier one.
    """
    dx, dy = frame or (0, 0)
    edges: dict[Edge, dict[str, float]] = {}
    cells: dict[tuple[int, int], dict[str, float]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        answer = json.loads(line)
        if answer["kind"] == "edge":
            (x, y), side = parse_edge(answer["item"])
            x, y = x - dx, y - dy
            if not (0 <= x < width and 0 <= y < height):
                continue
            probabilities = {LETTERS[option]: p for option, p in answer["probabilities"].items()}
            edges[_canonical((x, y), side, width, height)] = probabilities
        elif answer["kind"] == "cell":
            x, y = parse_cell(answer["item"])
            x, y = x - dx, y - dy
            if 0 <= x < width and 0 <= y < height:
                cells[(x, y)] = dict(answer["probabilities"])
    return Answers(edges=edges, cells=cells)


def passable_probability(probabilities: dict[str, float]) -> float:
    """The probability that an edge is passable: open plus printed open line."""
    return probabilities.get("o", 0.0) + probabilities.get("p", 0.0)
