"""The passage rule applied to a settled reading: the cells a drawn passage keeps.

`docs/tracing-conventions.md`, Cells and edges: a passage drawn as floor that joins two floor regions of a level,
which the half-floor rule leaves apart, stays joined when it's at least a sixth of a cell wide at its narrowest. Each
passage is judged on its own, so a second passage between the same two regions stays joined too. It keeps a chain of
cells along its course, each sharing an open edge with the next: where it covers one cell across its course, that
cell; where it covers two cells side by side across its course, or turns across a corner, the one with more floor,
the first in page order on a tie. A door drawn across a corner, with floor drawn through the corner, stands on the
step cell's edge with whichever of the two floor cells comes first in page order.

`level_chains` finds, for one level, each passage between two of its floor regions (components across passable
edges) with no other region on the way, widest first, and the chain of cells it keeps. A passage's body is the drawn
floor its course runs through: the samples within its clearance of the centre line, the path through the passable
samples that keeps furthest from their edges. The cells the body covers are the chain's candidates, and the chain is
the shortest four-connected run of them from one region to the other, and of runs equally short, the one with the
most floor. A step whose two candidates have nearly equal floor is marked close, for the settler. Once a passage is
found, its body is closed to the next, which must run elsewhere.
"""

import heapq
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np

from .frame import frame_of, to_registered, to_work
from .keyspec import MapContext
from .passages import clearance, fine_floor, widest_path

__all__ = [
    "CLOSE",
    "NARROWEST",
    "Chain",
    "apply_passages",
    "corner_doors",
    "floor_shares",
    "level_chains",
    "level_regions",
    "passage_calls",
]

NARROWEST = 1 / 6
"""The narrowest a passage may be, as a share of the pitch, and still join two regions."""

CLOSE = 0.1
"""Two step cells whose floor shares differ by less than this are a close call."""

COVER = 1 / 12
"""The least share of a cell a passage's body must cover for the cell to count as one it covers."""

PASSAGES = 4
"""The most passages looked for between one pair of regions."""

_SIDES = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}


@dataclass
class Chain:
    """The cells one passage keeps between two regions of a level."""

    regions: tuple[int, int]
    """The two regions' indexes in `level_chains`' region list."""

    width: float
    """The passage's narrowest width, as a share of the pitch."""

    cells: list[tuple[int, int]]
    """The chain, in order: a cell of the first region, the kept cells, and a cell of the second region."""

    shares: dict[tuple[int, int], float] = field(default_factory=dict)
    """Each kept cell's share drawn as floor."""

    close: list[tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=list)
    """Each kept cell whose alternative at that step has nearly as much floor, as (kept, alternative)."""

    doors: dict[str, str] = field(default_factory=dict)
    """Edges of the chain that are a door drawn across a corner, as `x,y:side` to the door's letter."""


def floor_shares(ctx: MapContext, window: tuple[int, int, int, int], res: int = 12) -> np.ndarray:
    """Each cell's share drawn as floor (paper, and lines across paper) over a window of cells."""
    _x0, _y0, width, height = window
    mask = fine_floor(ctx, window, res)
    return mask.reshape(height, res, width, res).mean(axis=(1, 3))


def _centre_line(allowed: np.ndarray, sources: np.ndarray, targets: np.ndarray) -> list[tuple[int, int]]:
    """The path from the sources to the targets through allowed samples that keeps furthest from their edges: a
    step onto a sample costs the inverse of its clearance."""
    depth = clearance(allowed)
    cost = np.where(allowed, 1.0 / np.maximum(depth, 1.0), np.inf)
    best = np.full(allowed.shape, np.inf)
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    heap = []
    for y, x in zip(*np.nonzero(sources & allowed), strict=True):
        best[y, x] = 0.0
        heap.append((0.0, int(y), int(x)))
    heapq.heapify(heap)
    end = None
    while heap:
        spent, y, x = heapq.heappop(heap)
        if spent > best[y, x]:
            continue
        if targets[y, x]:
            end = (y, x)
            break
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < allowed.shape[0] and 0 <= nx < allowed.shape[1] and allowed[ny, nx]:
                total = spent + cost[ny, nx]
                if total < best[ny, nx]:
                    best[ny, nx] = total
                    parent[(ny, nx)] = (y, x)
                    heapq.heappush(heap, (total, ny, nx))
    path = []
    while end is not None:
        path.append(end)
        end = parent.get(end)
    return path[::-1]


def _chain(
    cover: dict[tuple[int, int], float],
    one: set[tuple[int, int]],
    other: set[tuple[int, int]],
    barred: set[tuple[int, int]],
    share: Any,
) -> list[tuple[int, int]]:
    """The cells a passage keeps, from a cell of `one` to a cell of `other`: the shortest four-connected run through
    the cells its body covers, and of runs equally short the one with the most floor, so where the passage covers two
    cells side by side or turns across a corner the cell with more floor is kept. Ties go to the run first in page
    order.

    `cover` gives each cell the share of it the passage's body covers: a cell outside both regions is a candidate
    when the share is at least `COVER`, and a region's cell is an end of the run when the body touches it at all."""
    covered = {cell for cell, part in cover.items() if part >= COVER}
    touched = {cell for cell, part in cover.items() if part > 0}
    ends_too = touched & (one | other)
    # Where the passage turns across a corner, both step cells are candidates, however little of either it covers.
    steps = {
        step
        for x, y in covered | ends_too
        for dx, dy in ((1, 1), (1, -1))
        if (x + dx, y + dy) in covered | ends_too
        for step in ((x + dx, y), (x, y + dy))
    }
    candidates = {cell for cell in covered | steps if cell not in one and cell not in other} - barred

    def beside(cell: tuple[int, int], cells: set[tuple[int, int]]) -> bool:
        return any((cell[0] + dx, cell[1] + dy) in cells for dx, dy in _SIDES)

    ends_of_other = {cell for cell in touched & other if beside(cell, candidates | (touched & one))}
    starts = {cell for cell in touched & one if beside(cell, candidates | ends_of_other)}
    ends = {cell for cell in ends_of_other if beside(cell, candidates | starts)}
    nodes = candidates | starts | ends
    # Length first, then floor: a kept cell costs one step plus a small charge for the floor it lacks, and the page
    # order of the run breaks what remains.
    best: dict[tuple[int, int], tuple[float, tuple]] = {}
    heap: list[tuple[float, tuple, tuple[int, int]]] = []
    for first in starts:
        best[first] = (0.0, ((first[1], first[0]),))
        heap.append((0.0, ((first[1], first[0]),), first))
    heapq.heapify(heap)
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    end = None
    while heap:
        spent, order, cell = heapq.heappop(heap)
        if best[cell] < (spent, order):
            continue
        if cell in ends:
            end = cell
            break
        for dx, dy in _SIDES:
            nxt = (cell[0] + dx, cell[1] + dy)
            if nxt not in nodes or nxt in starts:
                continue
            total = spent + 1.0 + 0.001 * (1.0 - share(nxt))
            key = (total, (*order, (nxt[1], nxt[0])))
            if nxt not in best or key < best[nxt]:
                best[nxt] = key
                parent[nxt] = cell
                heapq.heappush(heap, (total, key[1], nxt))
    if end is None:
        return []
    chain = [end]
    while chain[-1] not in starts:
        chain.append(parent[chain[-1]])
    return chain[::-1]


def _body(centre: list[tuple[int, int]], depth: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """The samples within each centre-line sample's clearance of it: the drawn floor the course runs through."""
    body = np.zeros(shape, dtype=bool)
    rows, columns = np.mgrid[0 : shape[0], 0 : shape[1]]
    for y, x in centre[:: max(1, len(centre) // 400)] + centre[-1:]:
        radius = float(depth[y, x])
        y0, y1 = max(0, int(y - radius) - 1), min(shape[0], int(y + radius) + 2)
        x0, x1 = max(0, int(x - radius) - 1), min(shape[1], int(x + radius) + 2)
        block = (rows[y0:y1, x0:x1] - y) ** 2 + (columns[y0:y1, x0:x1] - x) ** 2 <= radius**2
        body[y0:y1, x0:x1] |= block
    return body


def level_regions(
    level: set[tuple[int, int]], connectors: set[tuple[int, int]], passable: Any
) -> list[set[tuple[int, int]]]:
    """A level's floor regions on a panel: its cells, joined across passable edges directly or through floor that no
    level owns (`connectors`), each region holding at least one of the level's cells. Floor joined this way isn't left
    apart by the cells, whoever owns it."""
    cells = level | connectors
    regions, seen = [], set()
    for start in sorted(level, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        part, stack = {start}, [start]
        while stack:
            x, y = stack.pop()
            for (dx, dy), side in _SIDES.items():
                nxt = (x + dx, y + dy)
                if nxt in cells and nxt not in part and passable((x, y), side):
                    part.add(nxt)
                    stack.append(nxt)
        seen |= part
        regions.append(part)
    return regions


def level_chains(
    ctx: MapContext,
    regions: list[set[tuple[int, int]]],
    barred: set[tuple[int, int]],
    letter: Any,
    res: int = 24,
    reach: int = 4,
    settled: dict[frozenset, tuple[int, int]] | None = None,
) -> list[Chain]:
    """The chains of cells that passages keep between a level's floor regions.

    Args:
        ctx: The map.
        regions: The level's floor regions on this panel (`level_regions`).
        barred: Cells no passage of this level may cross: other levels' floor.
        letter: The settled letter of an edge, as `letter((x, y), side)`, to find doors drawn across a corner.
        res: Samples per cell along each axis.
        reach: How far apart, in cells, two regions may lie for a passage between them to be looked for.
        settled: Close calls the settler has answered: each pair of cells, to the one with more floor.

    Returns:
        One chain for each passage at least `NARROWEST` wide that joins two regions on its own, up to `PASSAGES`
        between one pair.
    """
    owner = {cell: index for index, region in enumerate(regions) for cell in region}
    winners = set((settled or {}).values())
    losers = {cell for pair in (settled or {}) for cell in pair} - winners
    chains: list[Chain] = []
    for a in range(len(regions)):
        for b in range(a + 1, len(regions)):
            near_a = {
                c for c in regions[a] if any(max(abs(c[0] - d[0]), abs(c[1] - d[1])) <= reach for d in regions[b])
            }
            if not near_a:
                continue
            near_b = {d for d in regions[b] if any(max(abs(c[0] - d[0]), abs(c[1] - d[1])) <= reach for c in near_a)}
            cells = near_a | near_b
            x0 = max(0, min(x for x, _ in cells) - 2)
            y0 = max(0, min(y for _, y in cells) - 2)
            x1 = min(ctx.fit.columns, max(x for x, _ in cells) + 3)
            y1 = min(ctx.fit.rows, max(y for _, y in cells) + 3)
            window = (x0, y0, x1 - x0, y1 - y0)
            mask = fine_floor(ctx, window, res)
            index = np.full((y1 - y0, x1 - x0), -1)
            off = np.zeros((y1 - y0, x1 - x0), dtype=bool)
            for (x, y), number in owner.items():
                if x0 <= x < x1 and y0 <= y < y1:
                    index[y - y0, x - x0] = number
            for x, y in barred:
                if x0 <= x < x1 and y0 <= y < y1:
                    off[y - y0, x - x0] = True
            grow = np.ones((res, res), dtype=bool)
            samples = np.kron(index, grow)
            allowed = mask & ~np.kron(off, grow) & ((samples == -1) | (samples == a) | (samples == b))
            shares_window = floor_shares(ctx, window)

            def share(cell: tuple[int, int], shares_window=shares_window, x0=x0, y0=y0) -> float:
                # A close call the settler answered decides between its two cells.
                if cell in winners:
                    return 1.0
                if cell in losers:
                    return 0.0
                return float(shares_window[cell[1] - y0, cell[0] - x0])

            def drawn(cell: tuple[int, int], shares_window=shares_window, x0=x0, y0=y0) -> float:
                return float(shares_window[cell[1] - y0, cell[0] - x0])

            others = set().union(*(region for number, region in enumerate(regions) if number not in (a, b)))
            for _passage in range(PASSAGES):
                depth, path = widest_path(allowed, allowed & (samples == a), allowed & (samples == b))
                if not path:
                    break
                width = (2 * depth - 1) / res
                if width < NARROWEST:
                    break
                # The course keeps to the widest passage: its centre line runs through samples at least as clear as
                # the passage's narrowest point.
                clear = clearance(allowed)
                wide = allowed & (clear >= depth)
                centre = _centre_line(wide, wide & (samples == a), wide & (samples == b))
                body = _body(centre, clear, allowed.shape) & allowed
                parts = body.reshape(y1 - y0, res, x1 - x0, res).mean(axis=(1, 3))
                cover = {
                    (x0 + int(column), y0 + int(row)): float(parts[row, column])
                    for row, column in zip(*np.nonzero(parts > 0), strict=True)
                }
                covered = {cell for cell, part in cover.items() if part >= COVER}
                # The next passage runs elsewhere: this one's body is closed to it.
                allowed = allowed & ~body
                # A close call the settler answered decides it: the other cell is no step of any course.
                chain = _chain(cover, regions[a], regions[b], barred | others | losers, share)
                if len(chain) < 2:
                    continue
                if any(
                    letter(first, _SIDES[(second[0] - first[0], second[1] - first[1])]) == "n"
                    for first, second in pairwise(chain)
                ):
                    # A window is a wall: the white inside its symbol is no passage.
                    continue
                kept = chain[1:-1]
                close = []
                for cell in kept:
                    for dx, dy in _SIDES:
                        alternative = (cell[0] + dx, cell[1] + dy)
                        if alternative in chain or alternative not in covered:
                            continue
                        if alternative in barred or alternative in others or alternative in regions[a] | regions[b]:
                            continue
                        pair = frozenset((cell, alternative))
                        if abs(drawn(cell) - drawn(alternative)) < CLOSE and pair not in (settled or {}):
                            close.append((cell, alternative))
                chains.append(
                    Chain(
                        regions=(a, b),
                        width=round(width, 2),
                        cells=chain,
                        shares={cell: round(drawn(cell), 2) for cell in kept},
                        close=close,
                        doors=_corner_doors(chain, letter),
                    )
                )
    return chains


def _corner_doors(chain: list[tuple[int, int]], letter: Any) -> dict[str, str]:
    """For each corner the chain turns around a step cell, a door drawn on an edge between the step cell's two
    neighbours in the chain and the other step cell of that corner, moved onto the step cell's edge with the neighbour
    first in page order."""
    doors: dict[str, str] = {}
    for before, step, after in zip(chain, chain[1:], chain[2:], strict=False):
        if before[0] == after[0] or before[1] == after[1]:
            continue
        # The corner's two floor cells are `before` and `after`; its other step cell is the fourth of the square.
        fourth = (before[0] + after[0] - step[0], before[1] + after[1] - step[1])
        found = None
        for cell in (before, after):
            for target in (step, fourth):
                side = _SIDES.get((target[0] - cell[0], target[1] - cell[1]))
                if side is not None and letter(cell, side) in "dls":
                    found = letter(cell, side)
        if found is None:
            continue
        first = min((before, after), key=lambda c: (c[1], c[0]))
        side = _SIDES[(first[0] - step[0], first[1] - step[1])]
        doors[f"{step[0]},{step[1]}:{side}"] = found
    return doors


def corner_doors(
    floor: Any, letter: Any, share: Any, cells: list[tuple[int, int]], through: Any = None
) -> list[tuple[tuple[int, int], dict[str, str], dict[str, str]]]:
    """Each door drawn across the corner where two floor cells meet, with floor drawn through the corner, its step
    cells both rock: the chain through the step cell with more floor, the first in page order on a tie.

    Args:
        floor: Whether a cell is floor, as `floor((x, y))`.
        letter: The settled letter of an edge, as `letter((x, y), side)`.
        share: A cell's share drawn as floor, as `share((x, y))`.
        cells: The cells to look at, each taken as a corner square's north-west cell.
        through: Whether floor is drawn through the corner between two floor cells, as `through(one, other)`;
            every corner when `None`.

    Returns:
        For each such door, the step cell kept, the edges it sets (`x,y:side` to a letter: the door on the step
        cell's edge with the floor cell first in page order, open with the other), and the door letters it moves off
        edges into the other step cell, which become walls.
    """
    found = []
    for x, y in cells:
        square = [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]
        for one, other in ((square[0], square[3]), (square[1], square[2])):
            steps = [cell for cell in square if cell not in (one, other)]
            if not (floor(one) and floor(other)) or any(floor(step) for step in steps):
                continue
            door = None
            moved: dict[str, str] = {}
            for cell in (one, other):
                for step in steps:
                    side = _SIDES[(step[0] - cell[0], step[1] - cell[1])]
                    if letter(cell, side) in "dls":
                        door = letter(cell, side)
                        moved[f"{cell[0]},{cell[1]}:{side}"] = "w"
            if door is None or (through is not None and not through(one, other)):
                continue
            step = max(steps, key=lambda c: (share(c), -c[1], -c[0]))
            first, second = sorted((one, other), key=lambda c: (c[1], c[0]))
            sets = {
                f"{step[0]},{step[1]}:{_SIDES[(first[0] - step[0], first[1] - step[1])]}": door,
                f"{step[0]},{step[1]}:{_SIDES[(second[0] - step[0], second[1] - step[1])]}": "o",
            }
            for name in list(moved):
                cell_text, side = name.split(":")
                cx, cy = (int(part) for part in cell_text.split(","))
                dx, dy = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}[side]
                if (cx + dx, cy + dy) == step:
                    del moved[name]
            found.append((step, sets, moved))
    return found


def passage_calls(resolver: Any, contexts: dict[str, MapContext]) -> dict[str, dict[str, dict[str, Any]]]:
    """The calls the passage rule makes on each map, from the readings as settled without it.

    Each traced level's regions on each panel (`level_regions`) are joined by the chains of the passages between
    them (`level_chains`) and by each door drawn across a corner (`corner_doors`). A kept cell that wasn't floor
    becomes floor, under half floor it is also partial, and each edge along a chain takes its letter. The settler's
    answers to close calls (`k:` recheck calls) decide those steps.
    """
    from osrforge.tracing.truth import GeometryTruth, parse_cell

    from .settler import _load_calls

    world = resolver.world
    out: dict[str, dict[str, dict[str, Any]]] = {map_id: {} for map_id in contexts}
    for level_id, state in resolver.levels.items():
        if state.verdict != "traced":
            continue
        panels = {cell[0] for cells in state.areas.values() for cell in cells}
        for panel in sorted(panels):
            ctx = contexts.get(panel)
            if ctx is None or ctx.spec.scale_feet == 5:
                continue
            reading = GeometryTruth.model_validate_json((ctx.key.directory / "reading.json").read_text()).panels[0]
            # The reading and the resolver are on the registered grid, the map's fit and its calls on the working
            # grid (`frame.py`): the rule works in working cells.
            frame = frame_of(ctx)

            def registered(cell: tuple[int, int], ctx: MapContext = ctx) -> tuple[int, int]:
                return to_registered(ctx, cell)

            def letter(cell: tuple[int, int], side: str, reading: Any = reading, registered: Any = registered) -> str:
                x, y = registered(cell)
                if not (0 <= y <= len(reading.cells) and 0 <= x <= len(reading.cells[0])):
                    return "-"
                return reading.edges.at((x, y), side)

            def floor(cell: tuple[int, int], reading: Any = reading, registered: Any = registered) -> bool:
                x, y = registered(cell)
                return 0 <= y < len(reading.cells) and 0 <= x < len(reading.cells[0]) and reading.cells[y][x] != "#"

            settled: dict[frozenset, tuple[int, int]] = {}
            for name, call in _load_calls(ctx, "recheck").items():
                if name.startswith("k:"):
                    first, second = (parse_cell(text) for text in name[2:].split("|"))
                    settled[frozenset((first, second))] = parse_cell(call["value"])
            mine = {to_work(ctx, (c[1], c[2])) for c, o in resolver.owner.items() if o == level_id and c[0] == panel}
            barred = {
                to_work(ctx, (c[1], c[2]))
                for c, o in resolver.owner.items()
                if o not in (level_id, None) and c[0] == panel
            }
            if frame is not None:
                # No course runs outside the framed drawing.
                barred |= {
                    (x, y)
                    for x in range(ctx.fit.columns)
                    for y in range(ctx.fit.rows)
                    if not (frame[0] <= x < frame[0] + frame[2] and frame[1] <= y < frame[1] + frame[3])
                }
            grid = world.grid((panel, 0, 0))
            connectors = {
                to_work(ctx, (x, y))
                for x in range(grid.width)
                for y in range(grid.height)
                if (panel, x, y) not in resolver.owner and reading.cells[y][x] != "#"
            }
            regions = level_regions(mine, connectors, lambda cell, side, letter=letter: letter(cell, side) in "opdls")
            calls = out[panel]
            shares = floor_shares(ctx, (0, 0, ctx.fit.columns, ctx.fit.rows))
            if len(regions) > 1:
                for chain in level_chains(ctx, regions, barred, letter, settled=settled):
                    reason = f"kept by a passage {chain.width} of a cell wide at its narrowest"
                    for x, y in chain.cells[1:-1]:
                        if not floor((x, y)):
                            calls[f"c:{x},{y}"] = {
                                "value": ".",
                                "kind": "cell",
                                "reason": reason,
                                "partial": bool(shares[y, x] < 0.5),
                            }
                    for first, second in zip(chain.cells, chain.cells[1:], strict=False):
                        side = _SIDES[(second[0] - first[0], second[1] - first[1])]
                        name = f"{first[0]},{first[1]}:{side}"
                        back = {"east": "west", "west": "east", "south": "north", "north": "south"}[side]
                        value = chain.doors.get(name) or chain.doors.get(f"{second[0]},{second[1]}:{back}") or "o"
                        calls[_edge_name(first, side, ctx.fit.columns, ctx.fit.rows)] = {
                            "value": value,
                            "kind": "edge",
                            "reason": reason,
                        }
            squares = sorted({(x + dx, y + dy) for x, y in mine for dx in (0, -1) for dy in (0, -1)})
            for step, sets, walls in corner_doors(
                floor,
                letter,
                lambda c, shares=shares: float(shares[c[1], c[0]]),
                squares,
                lambda one, other, ctx=ctx: _through_corner(ctx, one, other),
            ):
                reason = "a door drawn across the corner where two floor cells meet"
                calls[f"c:{step[0]},{step[1]}"] = {
                    "value": ".",
                    "kind": "cell",
                    "reason": reason,
                    "partial": bool(shares[step[1], step[0]] < 0.5),
                }
                for name, value in {**walls, **sets}.items():
                    cell_text, side = name.split(":")
                    calls[_edge_name(parse_cell(cell_text), side, ctx.fit.columns, ctx.fit.rows)] = {
                        "value": value,
                        "kind": "edge",
                        "reason": reason,
                    }
    return out


def _through_corner(ctx: MapContext, one: tuple[int, int], other: tuple[int, int], res: int = 24) -> bool:
    """Whether floor is drawn through the corner where two cells meet, at least `NARROWEST` wide: paper, with the
    lines drawn across it, a door's among them, inside the two cells' square."""
    from .ink import box_max, box_min, channel_image, close, sample
    from .passages import LINE_REACH

    levels = ctx.spec.levels
    gray = channel_image(ctx.rgb, levels.channel)
    drawn = close(gray >= levels.paper_min, LINE_REACH)
    if levels.fill is not None:
        fill = (gray >= levels.fill[0]) & (gray <= levels.fill[1])
        drawn &= ~box_max(box_min(fill, 2), 2)
    x0, y0 = min(one[0], other[0]), min(one[1], other[1])
    across = x0 + (np.arange(2 * res) + 0.5) / res
    down = y0 + (np.arange(2 * res) + 0.5) / res
    gx, gy = np.meshgrid(across, down)
    px, py = ctx.fit.corners(gx, gy)
    mask = sample(drawn.astype(float), px, py) > 0.5
    first, second = np.zeros((2, 2), dtype=bool), np.zeros((2, 2), dtype=bool)
    first[one[1] - y0, one[0] - x0] = True
    second[other[1] - y0, other[0] - x0] = True
    grow = np.ones((res, res), dtype=bool)
    depth, path = widest_path(mask, np.kron(first, grow) & mask, np.kron(second, grow) & mask)
    return bool(path) and (2 * depth - 1) / res >= NARROWEST


def _edge_name(cell: tuple[int, int], side: str, width: int, height: int) -> str:
    """An edge's call name as the readings table names it."""
    from .readings import edge_key, edge_name

    return f"e:{edge_name(edge_key(f'{cell[0]},{cell[1]}:{side}'), width, height)}"


def apply_passages(keys: list[Any], stages: Any) -> dict[str, dict[str, int]]:
    """Work out the passage rule's calls on a corpus member's maps and write them in each map's `passages` store.

    The rule is worked out from the readings as settled without it: each map is assembled without its passage
    calls, the member is resolved for its levels, and each map is assembled again with the new calls.
    """
    from .assemble import assemble
    from .joins import resolved
    from .keyspec import context
    from .settler import _save_calls, write_final

    contexts = {key.id: context(key) for key in keys}
    for ctx in contexts.values():
        assemble(ctx, passages=False)
    resolver, _contents = resolved(keys, stages)
    calls = passage_calls(resolver, contexts)
    counts = {}
    for map_id, ctx in contexts.items():
        _save_calls(ctx, "passages", calls[map_id])
        write_final(ctx)
        assemble(ctx)
        mine = calls[map_id]
        counts[map_id] = {
            "cells": sum(1 for name in mine if name.startswith("c:")),
            "edges": sum(1 for name in mine if name.startswith("e:")),
        }
    return counts
