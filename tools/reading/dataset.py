"""The measured edges of every map, each with the key's letter, Clef's answer, and the ink.

[`map_rows`][tools.reading.dataset.map_rows] builds one map's rows from an answers file, and
[`features`][tools.reading.dataset.features] turns rows into the calibration's feature matrix.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .answers import Answers, load_answers, passable_probability
from .calibrate import logit
from .edges import EdgeRow, rows_for
from .ink import INK_STYLES, EdgeInk, MapInk
from .maps import ScreenMap, key_panel, key_region, load_rgb

__all__ = ["FEATURE_SETS", "MapRows", "features", "map_ink", "map_rows"]

FEATURE_SETS: tuple[str, ...] = ("clef", "ink", "clef+ink")


@dataclass(frozen=True)
class MapRows:
    """One map's measured edges, with the ink of each and the map's floor fractions."""

    screen: ScreenMap
    rows: list[EdgeRow]
    ink: list[EdgeInk]
    floors: np.ndarray


def map_ink(screen: ScreenMap) -> tuple[MapInk, np.ndarray]:
    """A map's ink measurer and its floor fractions."""
    ink = MapInk(load_rgb(screen.image), key_region(screen).registration, INK_STYLES[screen.style])  # type: ignore[arg-type]
    return ink, ink.floor_fractions()


def map_rows(screen: ScreenMap, answers: Answers | Path | None) -> MapRows:
    """One map's rows.

    Args:
        screen: The map.
        answers: Clef's answers, or an answers file on the answer key's working grid, or `None` for no answers.

    Returns:
        The rows with their ink.
    """
    panel = key_panel(screen)
    if isinstance(answers, Path):
        answers = load_answers(answers, screen.frame, panel.width, panel.height)
    rows = rows_for(screen.id, screen.style, panel, answers or Answers(edges={}, cells={}))
    ink, floors = map_ink(screen)
    return MapRows(screen=screen, rows=rows, ink=[ink.edge(*row.edge, floors) for row in rows], floors=floors)


def features(rows: Sequence[EdgeRow], ink: Sequence[EdgeInk], name: str) -> np.ndarray:
    """The feature matrix of a feature set for answered rows.

    Args:
        rows: The rows. Each must have probabilities when the set uses Clef.
        ink: Each row's ink.
        name: `clef` (Clef's passable and door log-odds), `ink`, or `clef+ink`. The door log-odds is there
            because a door's box interrupts the drawn line, so the ink alone makes a door look open.

    Returns:
        n by k.
    """
    columns = []
    if "clef" in name:
        passable = np.array([passable_probability(row.probabilities or {}) for row in rows])
        door = np.array([sum((row.probabilities or {}).get(letter, 0.0) for letter in "dls") for row in rows])
        columns.append(logit(passable)[:, None])
        columns.append(logit(door)[:, None])
    if "ink" in name:
        columns.append(np.array([item.vector() for item in ink]))
    return np.hstack(columns)
