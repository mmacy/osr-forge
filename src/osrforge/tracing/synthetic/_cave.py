"""Gridded caves: floor shaped by curves, whose cells are read from how much of each is floor.

A cave's floor is the union of capsules, each the points within a radius of
a segment, drawn over the grid with its walls off the grid lines. The picture
leaves exactly that union as paper and inks the rock around it, so a cell's
share of floor is the paper a reader sees in it. The reading follows Cells
and edges in `docs/tracing-conventions.md`: a cell is floor when at least half
of it is floor, a cell between a quarter and three quarters floor is partly
floor, and a cell the wall passes through, partly floor and partly rock, is a
cave cell. The share is counted at `SAMPLES` by `SAMPLES` points per cell, and
a cell whose share lies near 0, a quarter, a half, three quarters, or 1
without reaching it is refused, so the reading doesn't hang on how finely the
floor is counted.

A capsule that leaves cells under half floor along its course, and that
joins two floor regions the half-floor rule leaves apart, is a passage: it
keeps a chain of cells along its course, as Cells and edges says. Its course
is its segment. Wherever it covers two cells side by side across the course,
and wherever the chain turns across a corner, the chain takes the cell with
more floor, and a choice between cells that aren't both floor is refused when
it's a close call. A door drawn across a corner stands on the edge Cells and
edges gives it. The plan hides that edge and draws the door across the
passage at the corner, which must have floor drawn through it.
"""

import itertools
import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import numpy.typing as npt

from osrforge.tracing.synthetic._plan import STEP, Cell, Ornament, Plan, Sketch, canonical
from osrforge.tracing.truth import cell_key

SAMPLES = 64
"""Sample points per cell along each axis when counting a cell's floor."""

MARGIN = 0.03
"""How far a cell's share of floor must stay from 0, a quarter, a half, three quarters, and 1, unless it is 0 or 1.

Where a passage's chain chooses between two cells, the one it takes must also have at least this much more floor than
the other, unless both are floor already.
"""

NARROWEST = 1.0 / 6.0
"""The narrowest passage, in cells, that stays joined."""

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


def _share(share: FloatArray, cell: Cell) -> float:
    x, y = cell
    height, width = share.shape
    return float(share[y, x]) if 0 <= x < width and 0 <= y < height else 0.0


def _page(cell: Cell) -> tuple[int, int]:
    return cell[1], cell[0]


def course(cave: Ornament, share: FloatArray) -> tuple[list[Cell], list[tuple[Cell, Cell]]]:
    """The chain of cells a capsule keeps along its course, each sharing an edge with the next.

    The course is the capsule's segment. A course that runs at least as far
    across as down is taken a column at a time, and any other a row at a time.
    In each, of the cells the capsule covers, the chain takes the one with
    more floor, the first in page order on a tie: the one cell it covers, or
    of two side by side across the course the one with more floor. Where the
    chain moves on across the corner where cells meet, it steps through the
    one of the corner's other two cells with more floor, the first in page
    order on a tie. Cells off the grid are left out.

    Args:
        cave: The capsule, with two different end points.
        share: Each cell's share of floor, as [`shares`][osrforge.tracing.synthetic._cave.shares] counts it.

    Returns:
        The cells, first to last, and each choice the chain made as the cell it took and the best it passed over.

    Raises:
        ValueError: If the chain would skip a row or a column.
    """
    height, width = share.shape
    cover = shares((cave,), width, height)
    (ax, ay), (bx, by) = cave.points[0], cave.points[-1]
    across = abs(bx - ax) >= abs(by - ay)
    first, last = (ax, bx) if across else (ay, by)
    ahead = 1 if last > first else -1
    lines, size = (width, height) if across else (height, width)

    def at(line: int, place: int) -> Cell:
        return (line, place) if across else (place, line)

    choices: list[tuple[Cell, Cell]] = []
    picks: list[Cell] = []
    for line in range(math.floor(first + 1e-9 * ahead), math.floor(last - 1e-9 * ahead) + ahead, ahead):
        if not 0 <= line < lines:
            continue
        covered = sorted((at(line, place) for place in range(size) if _share(cover, at(line, place)) > 0.0), key=_page)
        if not covered:
            continue
        ranked = sorted(covered, key=lambda cell: -_share(share, cell))
        picks.append(ranked[0])
        if len(ranked) > 1:
            choices.append((ranked[0], ranked[1]))
    cells = picks[:1]
    for before, after in itertools.pairwise(picks):
        if abs(before[0] - after[0]) == 1 and abs(before[1] - after[1]) == 1:
            options = sorted([(after[0], before[1]), (before[0], after[1])], key=_page)
            ranked = sorted(options, key=lambda cell: -_share(share, cell))
            cells.append(ranked[0])
            choices.append((ranked[0], ranked[1]))
        elif abs(before[0] - after[0]) + abs(before[1] - after[1]) != 1:
            raise ValueError(f"the course from {cave.points[0]} to {cave.points[-1]} skips from {before} to {after}")
        cells.append(after)
    return cells, choices


def _regions(floor: set[Cell]) -> dict[Cell, int]:
    """Each floor cell's region: floor cells joined edge to edge."""
    region: dict[Cell, int] = {}
    number = 0
    for cell in sorted(floor, key=_page):
        if cell in region:
            continue
        number += 1
        todo = [cell]
        region[cell] = number
        while todo:
            x, y = todo.pop()
            for other in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if other in floor and other not in region:
                    region[other] = number
                    todo.append(other)
    return region


def _passages(caves: Sequence[Ornament], share: FloatArray, floor: set[Cell]) -> list[tuple[Ornament, list[Cell]]]:
    """Each passage and the cells it keeps, for each capsule that leaves cells under half floor along its course.

    Each is judged against the half-floor rule's cells alone.

    Raises:
        ValueError: If such a capsule is narrower than a sixth of a cell, doesn't join two regions the half-floor
            rule leaves apart, runs through a third region, or makes a choice that's a close call.
    """
    region = _regions(floor)
    kept: list[tuple[Ornament, list[Cell]]] = []
    for cave in caves:
        start, end = cave.points[0], cave.points[-1]
        if start == end:
            continue
        cells, choices = course(cave, share)
        if all(cell in floor for cell in cells):
            continue
        if 2.0 * cave.size < NARROWEST:
            raise ValueError(f"the passage from {start} to {end} is narrower than a sixth of a cell")
        ends = (cells[0], cells[-1])
        if not all(cell in floor for cell in ends) or region[ends[0]] == region[ends[1]]:
            raise ValueError(f"the passage from {start} to {end} doesn't join two floor regions left apart")
        for cell in cells:
            third = cell in floor and region[cell] not in (region[ends[0]], region[ends[1]])
            if third and any(other not in cells for other, number in region.items() if number == region[cell]):
                raise ValueError(f"the passage from {start} to {end} runs through a third region at {cell}")
        for taken, passed in choices:
            if {taken, passed} <= floor:
                continue
            if _share(share, taken) - _share(share, passed) < MARGIN:
                raise ValueError(f"the passage from {start} to {end} takes {taken} over {passed} on a close call")
        kept.append((cave, cells))
    return kept


def _corner_door(passages: list[tuple[Ornament, list[Cell]]], corner: Cell) -> tuple[Cell, Cell, Ornament]:
    """Of the two cells meeting at a corner a door is drawn across, the first in page order, the step, and the passage.

    Raises:
        ValueError: If no passage's chain turns across the corner.
    """
    vx, vy = corner
    around = {(vx - 1, vy - 1), (vx, vy - 1), (vx - 1, vy), (vx, vy)}
    for cave, cells in passages:
        for before, step, after in zip(cells, cells[1:], cells[2:], strict=False):
            if {before, step, after} <= around and before[0] != after[0] and before[1] != after[1]:
                return min(before, after, key=_page), step, cave
    raise ValueError(f"no passage turns across the corner {corner} a door is drawn across")


def cave_plan(sketch: Sketch, caves: Sequence[Ornament], corner_doors: Sequence[Cell] = ()) -> Plan:
    """A plan whose floor is the caves, with the sketch's marks, symbols, outline, and edges set by hand.

    Floor cells are named alike, so floor beside floor is open and floor
    beside rock is walled, unless the sketch sets the edge. Water isn't
    supported. A passage's kept cells are floor, and those under half floor
    are partly floor. A door drawn across a corner stands, as Cells and edges
    says, on the step cell's edge with the corner's floor cell first in page
    order. That edge is hidden, and the door is drawn across the passage at
    the corner, which must have floor drawn through it.

    Args:
        sketch: The sketch, holding no floor of its own.
        caves: The cave ornaments that shape the floor.
        corner_doors: The grid corners, as points with whole coordinates, that a door is drawn across.

    Returns:
        The plan, with its partly-floor and cave cells and the caves as ornaments to draw.

    Raises:
        ValueError: If a cell's share of floor lies near a threshold, 0, or 1 without reaching it, two floor
            cells side by side that aren't consecutive cells of a passage have rock between their centres, or a
            passage or a corner door isn't one these rules settle.
    """
    share = shares(caves, sketch.width, sketch.height)
    floor: set[Cell] = set()
    partial: list[str] = []
    walled: list[str] = []
    for y in range(sketch.height):
        for x in range(sketch.width):
            value = float(share[y, x])
            for threshold in (0.0, 0.25, 0.5, 0.75, 1.0):
                if value != threshold and abs(value - threshold) < MARGIN:
                    raise ValueError(f"cell {(x, y)} is {value:.3f} floor, too near {threshold}")
            if value >= 0.5:
                floor.add((x, y))
    passages = _passages(caves, share, floor)
    kept = {cell for _cave, cells in passages for cell in cells}
    consecutive = {frozenset(pair) for _cave, cells in passages for pair in itertools.pairwise(cells)}
    for y in range(sketch.height):
        for x in range(sketch.width):
            value = float(share[y, x])
            if (x, y) in floor or (x, y) in kept:
                sketch.run("cave", (x, y))
            if 0.25 <= value <= 0.75 or ((x, y) in kept and value < 0.5):
                partial.append(cell_key((x, y)))
            if 0.0 < value < 1.0:
                walled.append(cell_key((x, y)))
    steps = np.arange(SAMPLES + 1, dtype=np.float64) / SAMPLES
    for (x, y), _name in list(sketch.space.items()):
        for dx, dy in ((1, 0), (0, 1)):
            if (x + dx, y + dy) in sketch.space and frozenset(((x, y), (x + dx, y + dy))) not in consecutive:
                xs, ys = x + 0.5 + dx * steps, y + 0.5 + dy * steps
                if not bool(inside(caves, xs, ys).all()):
                    raise ValueError(f"floor cells {(x, y)} and {(x + dx, y + dy)} have rock between their centres")
    for corner in corner_doors:
        first, step, cave = _corner_door(passages, corner)
        point = (float(corner[0]), float(corner[1]))
        if not bool(inside(caves, np.array([point[0]]), np.array([point[1]])).all()):
            raise ValueError(f"the corner door at {corner} has no floor drawn through the corner")
        side = next(side for side, (dx, dy) in STEP.items() if (step[0] + dx, step[1] + dy) == first)
        if canonical(step, side) in sketch.overrides:
            raise ValueError(f"the corner door at {corner} lands on an edge already set")
        sketch.edge(step, side, "d", style="hidden")
        (ax, ay), (bx, by) = cave.points[0], cave.points[-1]
        length = math.hypot(bx - ax, by - ay)
        ahead = (point[0] + (bx - ax) / length, point[1] + (by - ay) / length)
        sketch.ornament("corner_door", point, ahead, size=cave.size)
    for cave in caves:
        sketch.ornaments.append(cave)
    plan = sketch.build()
    return replace(plan, partial=tuple(partial), cave=tuple(walled))
