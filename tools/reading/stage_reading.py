"""The reading the stage would make of a map's windows, before Claude, and the classifier's on the same windows.

Inside the windows, a cell is what the ink says (floor at half floor or more) unless the ink leaves it unsettled
([`unsettled_cells`][tools.reading.ink.unsettled_cells]); then Clef's most probable answer decides, a symbol counting
as floor. An edge with floor or water on both sides takes the calibrated decision at the style's passable threshold;
an edge with floor or water on one side only is a wall, or the door Clef reads there; an edge with neither is `-`.
Outside the windows the panel is the key's, so every error the scorer counts lies inside them, and the classifier's
reading is scored the same way, so the two compare on the same edges.
"""

from collections.abc import Callable, Sequence

import numpy as np

from osrforge.tracing.truth import EdgeGrids, Panel

from .edges import decide
from .windows import Window

__all__ = ["CELL_LETTER", "patched_window_panel", "stage_letters"]

CELL_LETTER = {"floor": ".", "rock": "#", "water": "~"}
"""A cell answer's letter; every symbol counts as floor."""

_NEIGHBOUR = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


def stage_cells(
    key: Panel,
    floors: np.ndarray,
    unsettled: set[tuple[int, int]],
    cell_answers: dict[tuple[int, int], dict[str, float]],
    windows: Sequence[Window],
) -> list[list[str]]:
    """The key's cells with the stage's reading inside the windows."""
    cells = [list(row) for row in key.cells]
    for y in range(key.height):
        for x in range(key.width):
            if not any(window.holds((x, y)) for window in windows):
                continue
            answer = cell_answers.get((x, y)) if (x, y) in unsettled else None
            if answer:
                top = max(answer, key=answer.__getitem__)
                cells[y][x] = CELL_LETTER.get(top, ".")
            else:
                cells[y][x] = "." if floors[y, x] >= 0.5 else "#"
    return cells


def stage_letters(
    key: Panel,
    cells: list[list[str]],
    windows: Sequence[Window],
    probabilities: dict[tuple[tuple[int, int], str], dict[str, float]],
    passable: Callable[[tuple[tuple[int, int], str]], float | None],
    threshold: float,
) -> dict[tuple[tuple[int, int], str], str]:
    """Each window edge's letter.

    Args:
        key: The key's panel, for the grid.
        cells: The stage's cells.
        windows: The windows.
        probabilities: Clef's probabilities by letter, per edge.
        passable: An edge's calibrated P(passable), or `None` when it wasn't asked.
        threshold: The style's passable threshold.

    Returns:
        The letters of the edges named by a cell inside a window.
    """
    letters = {}
    for (x, y), side in key.edges.positions():
        if not any(window.holds((x, y)) for window in windows):
            continue
        dx, dy = _NEIGHBOUR[side]
        here = cells[y][x]
        nx, ny = x + dx, y + dy
        there = cells[ny][nx] if 0 <= nx < key.width and 0 <= ny < key.height else "#"
        occupied = [letter in ".~" for letter in (here, there)]
        edge = ((x, y), side)
        answer = probabilities.get(edge, {})
        if not any(occupied):
            letters[edge] = "-"
        elif all(occupied):
            p = passable(edge)
            letters[edge] = decide(answer, p, threshold) if p is not None else "w"
        else:
            doors = {letter: answer.get(letter, 0.0) for letter in "dls"}
            best = max(doors, key=lambda letter: doors[letter])
            letters[edge] = best if answer and doors[best] > max(answer.get("w", 0.0), answer.get("n", 0.0)) else "w"
    return letters


def patched_window_panel(key: Panel, cells: list[list[str]], letters: dict[tuple[tuple[int, int], str], str]) -> Panel:
    """The key's panel with the stage's cells and edge letters put in."""
    vertical = [list(row) for row in key.edges.vertical]
    horizontal = [list(row) for row in key.edges.horizontal]
    for ((x, y), side), letter in letters.items():
        if side == "west":
            vertical[y][x] = letter
        elif side == "east":
            vertical[y][x + 1] = letter
        elif side == "north":
            horizontal[y][x] = letter
        else:
            horizontal[y + 1][x] = letter
    return key.model_copy(
        update={
            "cells": tuple("".join(row) for row in cells),
            "edges": EdgeGrids(
                vertical=tuple("".join(row) for row in vertical), horizontal=tuple("".join(row) for row in horizontal)
            ),
        }
    )
