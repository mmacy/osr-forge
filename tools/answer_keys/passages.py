"""The white passages a drawing gives between two areas, measured below the scale of a cell.

A prose join that a key doesn't make is either floor or an opening the reading lost, a passage the drawing gives
narrower than the half rule keeps, or a connection the drawing doesn't show. `passage_between` tells these apart
from the pixels: it samples the map's passable pixels (paper, and lines drawn across paper) at `RES` points per cell
over a window around the two areas, and finds the widest white path from one area to the other. Its narrowest point,
as a share of the grid's pitch, is the passage's width, and the median along the path outside both areas is its
typical width.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .ink import sample
from .keyspec import MapContext

__all__ = ["NOT_A_PASSAGE", "RES", "Passage", "clearance", "passage_between", "widest_path"]

RES = 12
"""Samples per cell along each axis."""

NOT_A_PASSAGE = 0.2
"""A white path narrower than this share of the pitch is a gap between strokes or a line over rock, not a passage."""

_MATERIALS: dict[str, Any] = {}


@dataclass
class Passage:
    """The widest white path between two areas of a level."""

    width: float | None
    """The path's narrowest width as a share of the pitch, or `None` when no white path joins the areas."""

    narrowest: tuple[float, float] | None = None
    """Where the narrowest point lies, in cells."""

    median: float | None = None
    """The path's median width outside both areas, as a share of the pitch."""

    rock_cells: list[Any] = field(default_factory=list)
    """The cells outside both areas that the path crosses, in order, each with the key's status of it."""

    walls: list[str] = field(default_factory=list)
    """The edges between two floor cells that the key blocks and the path crosses, as `x,y:side` with the letter."""


def _passable(ctx: MapContext) -> np.ndarray:
    """Pixels a passage may cross: paper, or ink darker than the map's fill (a grid, contour, tree, or stream line
    drawn across a passage), closed by two pixels so the antialiased fringe of a line doesn't split a passage. Fill
    is rock. A line drawn over rock reads as a passage one or two samples wide, under `NOT_A_PASSAGE`."""
    from .ink import channel_image, close

    key = ctx.key.id
    if key not in _MATERIALS:
        levels = ctx.spec.levels
        gray = channel_image(ctx.rgb, levels.channel)
        paper = gray >= levels.paper_min
        lines = gray < levels.fill[0] if levels.fill is not None else gray <= levels.ink_max
        _MATERIALS[key] = close(paper | lines, 2)
    return _MATERIALS[key]


def fine_floor(ctx: MapContext, window: tuple[int, int, int, int], res: int = RES) -> np.ndarray:
    """Where a passage may run, sampled at `res` points per cell over a window of cells (see `_passable`)."""
    passable = _passable(ctx)
    x0, y0, width, height = window
    across = x0 + (np.arange(width * res) + 0.5) / res
    down = y0 + (np.arange(height * res) + 0.5) / res
    gx, gy = np.meshgrid(across, down)
    px, py = ctx.fit.corners(gx, gy)
    return sample(passable, px, py) > 0.5


def clearance(mask: np.ndarray, limit: int = 48) -> np.ndarray:
    """The Euclidean distance, in samples, from each true sample to the nearest false one, capped at `limit`.

    A passage `2d - 1` samples wide has clearance `d` along its middle, whatever its direction. The window's own edge
    doesn't bound a passage.
    """
    height, width = mask.shape
    far = float(limit + 1)
    across = np.where(mask, far, 0.0)
    for x in range(1, width):
        across[:, x] = np.minimum(across[:, x], across[:, x - 1] + 1)
    for x in range(width - 2, -1, -1):
        across[:, x] = np.minimum(across[:, x], across[:, x + 1] + 1)
    squared = across**2
    best = squared.copy()
    for dy in range(1, min(limit + 1, height)):
        best[dy:] = np.minimum(best[dy:], squared[:-dy] + dy * dy)
        best[:-dy] = np.minimum(best[:-dy], squared[dy:] + dy * dy)
    return np.minimum(np.sqrt(best), far) * mask


def _flood(allowed: np.ndarray, sources: np.ndarray) -> np.ndarray:
    """Every allowed sample a 4-connected walk from the sources reaches."""
    reached = sources & allowed
    while True:
        grown = reached.copy()
        grown[1:, :] |= reached[:-1, :]
        grown[:-1, :] |= reached[1:, :]
        grown[:, 1:] |= reached[:, :-1]
        grown[:, :-1] |= reached[:, 1:]
        grown &= allowed
        if (grown == reached).all():
            return reached
        reached = grown


def widest_path(mask: np.ndarray, sources: np.ndarray, targets: np.ndarray) -> tuple[float, list[tuple[int, int]]]:
    """The largest clearance a white walk from the sources to the targets can keep throughout, and one such walk.

    Returns `(0, [])` when no white walk joins them.
    """
    depth = clearance(mask)
    if not (_flood(mask, sources) & targets).any():
        return 0, []
    levels = np.unique(depth[depth > 0])
    low, high = 0, len(levels) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if (_flood(depth >= levels[middle], sources) & targets).any():
            low = middle
        else:
            high = middle - 1
    low = float(levels[low])
    allowed = depth >= low
    starts = list(zip(*np.nonzero(sources & allowed), strict=True))
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None for start in starts}
    queue = deque(starts)
    end = None
    while queue:
        y, x = queue.popleft()
        if targets[y, x]:
            end = (y, x)
            break
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and allowed[ny, nx] and (ny, nx) not in parent:
                parent[(ny, nx)] = (y, x)
                queue.append((ny, nx))
    path = []
    while end is not None:
        path.append(end)
        end = parent[end]
    return low, path[::-1]


def passage_between(
    ctx: MapContext,
    one: set[tuple[int, int]],
    other: set[tuple[int, int]],
    status: Any,
    edge_letter: Any = None,
    margin: int = 3,
    forbid: set[tuple[int, int]] | None = None,
    res: int = RES,
) -> Passage:
    """The drawing's widest passable path between two areas' cells, with what the key makes of each cell it crosses.

    Args:
        ctx: The map.
        one: The first area's cells.
        other: The second area's cells.
        status: The key's status of a cell, as `status((x, y))`: a word such as rock, outside, or an area's key.
        edge_letter: The key's letter of an edge, as `edge_letter((x, y), side)`, to list the walls the path crosses.
        margin: Cells of window beyond the two areas on each side.
        forbid: Cells the path may not cross, such as other areas', to measure one drawn passage on its own.
        res: Samples per cell along each axis.

    Returns:
        The passage, its `rock_cells` holding every cell the path crosses that isn't in either area, with its
        status.
    """
    cells = one | other
    x0 = max(0, min(x for x, _ in cells) - margin)
    y0 = max(0, min(y for _, y in cells) - margin)
    x1 = min(ctx.fit.columns, max(x for x, _ in cells) + margin + 1)
    y1 = min(ctx.fit.rows, max(y for _, y in cells) + margin + 1)
    mask = fine_floor(ctx, (x0, y0, x1 - x0, y1 - y0), res)
    in_one = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    in_other = np.zeros_like(in_one)
    for x, y in one:
        if x0 <= x < x1 and y0 <= y < y1:
            in_one[y - y0, x - x0] = True
    for x, y in other:
        if x0 <= x < x1 and y0 <= y < y1:
            in_other[y - y0, x - x0] = True
    grow = np.ones((res, res), dtype=bool)
    if forbid:
        blocked = np.zeros_like(in_one)
        for x, y in forbid:
            if x0 <= x < x1 and y0 <= y < y1:
                blocked[y - y0, x - x0] = True
        mask = mask & ~np.kron(blocked, grow)
    _depth, path = widest_path(mask, np.kron(in_one, grow) & mask, np.kron(in_other, grow) & mask)
    if not path:
        return Passage(width=None)
    depths = clearance(mask)
    middle = [
        point
        for point in path
        if not in_one[point[0] // res, point[1] // res] and not in_other[point[0] // res, point[1] // res]
    ]
    sy, sx = min(middle or path, key=lambda point: depths[point])
    crossed: list[Any] = []
    walls: list[str] = []
    previous = None
    for y, x in path:
        cell = (int(x0 + x // res), int(y0 + y // res))
        if cell not in one and cell not in other and (not crossed or crossed[-1][0] != cell):
            crossed.append((cell, status(cell)))
        if previous is not None and previous != cell and edge_letter is not None:
            side = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}[
                (cell[0] - previous[0], cell[1] - previous[1])
            ]
            letter = edge_letter(previous, side)
            if status(previous) != "rock" and status(cell) != "rock" and letter not in "opdls":
                walls.append(f"{previous[0]},{previous[1]}:{side} {letter}")
        previous = cell
    along = [float(depths[point]) for point in (middle or path)]
    narrow = (2 * min(along) - 1) / res
    typical = (2 * float(np.median(along)) - 1) / res
    return Passage(
        width=round(narrow, 2),
        median=round(typical, 2),
        narrowest=(round(float(x0 + (sx + 0.5) / res), 1), round(float(y0 + (sy + 0.5) / res), 1)),
        rock_cells=crossed,
        walls=walls,
    )
