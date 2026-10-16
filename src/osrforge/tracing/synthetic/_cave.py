"""Gridded caves: floor shaped by curves, whose cells are read from how much of each is floor.

A cave's floor is the union of capsules, each the points within a radius of
a segment, drawn over the grid with its walls off the grid lines. The
reading follows Cells and edges in `docs/tracing-conventions.md`: a cell is
floor when at least half of it is floor, a cell between a quarter and three
quarters floor is partly floor, and a cell the wall passes through is a cave
cell. The share of floor is counted at `SAMPLES` by `SAMPLES` points per
cell, and a cell whose share lies near a threshold is refused, so the reading
doesn't hang on how finely the floor is counted.
"""

from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import numpy.typing as npt

from osrforge.tracing.synthetic._plan import Ornament, Plan, Sketch
from osrforge.tracing.truth import cell_key

SAMPLES = 16
"""Sample points per cell along each axis when counting a cell's floor."""

MARGIN = 0.03
"""How far a cell's share of floor must stay from a quarter, a half, and three quarters."""

type FloatArray = npt.NDArray[np.float64]
type BoolArray = npt.NDArray[np.bool_]


def inside(caves: Sequence[Ornament], x: FloatArray, y: FloatArray) -> BoolArray:
    """Which points lie on a cave's floor.

    Args:
        caves: The cave ornaments, each a capsule from its first point to its last.
        x: Grid x coordinates, broadcastable with `y`.
        y: Grid y coordinates.

    Returns:
        True where a point lies within some capsule.
    """
    found = np.zeros(np.broadcast_shapes(x.shape, y.shape), dtype=np.bool_)
    for cave in caves:
        (ax, ay), (bx, by) = cave.points[0], cave.points[-1]
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        if length == 0.0:
            px, py = x - ax, y - ay
        else:
            along = np.clip(((x - ax) * dx + (y - ay) * dy) / length, 0.0, 1.0)
            px, py = x - (ax + along * dx), y - (ay + along * dy)
        found |= px * px + py * py <= cave.size * cave.size
    return found


def shares(caves: Sequence[Ornament], width: int, height: int) -> FloatArray:
    """Each cell's share of floor.

    Args:
        caves: The cave ornaments.
        width: The grid's width in cells.
        height: The grid's height in cells.

    Returns:
        An array `height` by `width` of shares from 0 to 1.
    """
    offsets = (np.arange(SAMPLES, dtype=np.float64) + 0.5) / SAMPLES
    xs = (np.arange(width, dtype=np.float64)[:, np.newaxis] + offsets[np.newaxis, :]).reshape(-1)
    ys = (np.arange(height, dtype=np.float64)[:, np.newaxis] + offsets[np.newaxis, :]).reshape(-1)
    hits = inside(caves, xs[np.newaxis, :], ys[:, np.newaxis])
    counts = hits.reshape(height, SAMPLES, width, SAMPLES).sum(axis=(1, 3))
    return counts.astype(np.float64) / float(SAMPLES * SAMPLES)


def cave_plan(sketch: Sketch, caves: Sequence[Ornament]) -> Plan:
    """A plan whose floor is the caves, with the sketch's marks, symbols, outline, and edges set by hand.

    Floor cells are named alike, so floor beside floor is open and floor
    beside rock is walled, unless the sketch sets the edge. Water isn't
    supported.

    Args:
        sketch: The sketch, holding no floor of its own.
        caves: The cave ornaments that shape the floor.

    Returns:
        The plan, with its partly-floor and cave cells and the caves as ornaments to draw.

    Raises:
        ValueError: If a cell's share of floor lies near a threshold, or two floor cells meet at an edge whose
            middle isn't floor.
    """
    share = shares(caves, sketch.width, sketch.height)
    partial: list[str] = []
    walled: list[str] = []
    for y in range(sketch.height):
        for x in range(sketch.width):
            value = float(share[y, x])
            for threshold in (0.25, 0.5, 0.75):
                if abs(value - threshold) < MARGIN:
                    raise ValueError(f"cell {(x, y)} is {value:.3f} floor, too near {threshold}")
            if value >= 0.5:
                sketch.run("cave", (x, y))
            if 0.25 <= value <= 0.75:
                partial.append(cell_key((x, y)))
            if 0.0 < value < 1.0:
                walled.append(cell_key((x, y)))
    for (x, y), _name in list(sketch.space.items()):
        for dx, dy in ((1, 0), (0, 1)):
            if (x + dx, y + dy) in sketch.space:
                middle = (x + 0.5 + dx * 0.5, y + 0.5 + dy * 0.5)
                point = np.array([middle[0]]), np.array([middle[1]])
                if not bool(inside(caves, point[0], point[1])[0]):
                    raise ValueError(f"floor cells {(x, y)} and {(x + dx, y + dy)} meet where the wall runs")
    for cave in caves:
        sketch.ornaments.append(cave)
    plan = sketch.build()
    return replace(plan, partial=tuple(partial), cave=tuple(walled))
