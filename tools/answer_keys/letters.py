"""The settled letters of a map and the outlines over them: what the assembly and the outline settling share.

`final_cells` and `final_edges` turn the readings table and the final values into the panel's letters.
`outline_state` lays the whole-map read's outlines over those letters, applies the outline calls the settler
made, and finds what still needs settling: floor just outside every outline that an open edge joins to a
level's floor (a border that crosses floor), and each open-ground rectangle, which the settler confirms as
open ground of its level or sends to the outside.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from osrforge.tracing.truth import EdgeGrids, parse_cell, parse_edge

from .keyspec import MapContext
from .marks import wholemap_answer
from .readings import Items, edge_cells, edge_key

__all__ = ["OutlineState", "final_cells", "final_edges", "level_id", "outline_state"]

EXPANSION_CAP = 40
"""The most cells one outline call adds to a level's outline: the floor joined to the settled cell across passable
edges, up to the first wall, door, cave mouth, or printed line."""


def level_id(dungeon: str, number: int) -> str:
    """A level's name in outline calls: `<dungeon>/<number>`."""
    return f"{dungeon}/{number}"


def final_cells(items: Items, final: dict[str, dict[str, Any]], log: dict[str, Any]) -> list[list[str]]:
    """The panel's cell letters: the settler's where it decided, else Claude's, else the classifier's."""
    cells = [["#"] * items.width for _ in range(items.height)]
    unread = 0
    for name, entry in items.cells.items():
        x, y = parse_cell(name)
        call = final.get(f"c:{name}")
        if call is not None:
            letter = call["value"]
        elif entry["claude"]:
            letter = entry["claude"][0]
        else:
            letter = entry["classifier"]
            unread += 1
        cells[y][x] = letter
    log["cells_from_classifier_alone"] = unread
    return cells


def final_edges(
    items: Items, cells: list[list[str]], final: dict[str, dict[str, Any]], log: dict[str, Any]
) -> EdgeGrids:
    """The panel's edge letters, with the derivations after settling: `-` where no floor touches, a wall between
    floor and rock unless the settler put a door there."""
    width, height = items.width, items.height
    vertical = [["-"] * (width + 1) for _ in range(height)]
    horizontal = [["-"] * width for _ in range(height + 1)]

    def occupied(cell: tuple[int, int]) -> bool:
        x, y = cell
        return 0 <= x < width and 0 <= y < height and cells[y][x] != "#"

    derived = 0
    unread = 0
    # A call on an edge the table lacks (both readings left it blank, beside a cell a later call made floor).
    extra = {
        name[2:]: {"classifier": "-", "claude": []}
        for name in final
        if name.startswith("e:") and name[2:] not in items.edges
    }
    for name, entry in [*items.edges.items(), *extra.items()]:
        key = edge_key(name)
        a, b = edge_cells(key)
        occupied_a, occupied_b = occupied(a), occupied(b)
        if not (occupied_a or occupied_b):
            continue
        call = final.get(f"e:{name}")
        if call is not None:
            letter = call["value"]
        elif entry["claude"]:
            letter = entry["claude"][0]
        elif entry["classifier"] not in "-?":
            letter = entry["classifier"]
            unread += 1
        else:
            letter = "w"
            unread += 1
        if letter == "-":
            letter = "w"
        if (occupied_a != occupied_b) and letter not in "dlsn":
            # Between floor and rock an edge is a wall, unless a reader saw a door or a window symbol on it.
            if letter != "w":
                derived += 1
            letter = "w"
        axis, first, second = key
        if axis == "v":
            vertical[second][first] = letter
        else:
            horizontal[second][first] = letter
    for y in range(height):
        for x in range(width + 1):
            if vertical[y][x] == "-" and (occupied((x - 1, y)) or occupied((x, y))):
                vertical[y][x] = "w"
                derived += 1
    for y in range(height + 1):
        for x in range(width):
            if horizontal[y][x] == "-" and (occupied((x, y - 1)) or occupied((x, y))):
                horizontal[y][x] = "w"
                derived += 1
    log["edges_derived_as_walls"] = derived
    log["edges_from_classifier_alone"] = unread
    return EdgeGrids(
        vertical=tuple("".join(row) for row in vertical), horizontal=tuple("".join(row) for row in horizontal)
    )


@dataclass
class OutlineState:
    """The outlines over a map's settled letters."""

    levels: list[str]
    owned: dict[str, set[tuple[int, int]]]
    seeds: dict[str, list[str]]
    open_ground: dict[str, list[tuple[int, int, int, int]]]
    crossings: list[dict[str, Any]] = field(default_factory=list)
    """Each `{"level", "cell", "side", "beyond"}`: an open edge from a level's outlined floor to floor that no
    outline holds, and that no outline call has placed."""

    open_ground_items: list[dict[str, Any]] = field(default_factory=list)
    """Each `{"level", "index", "rect"}`: an open-ground rectangle no outline call has confirmed or refused."""


_SIDES = (("east", (1, 0)), ("south", (0, 1)), ("west", (-1, 0)), ("north", (0, -1)))


def _rect_cells(rect: list[int], width: int, height: int) -> set[tuple[int, int]]:
    x, y, w, h = rect
    return {(cx, cy) for cy in range(max(0, y), min(height, y + h)) for cx in range(max(0, x), min(width, x + w))}


def extend_path(path: list[tuple[int, int]], width: int, height: int) -> list[tuple[int, int]]:
    """A slope path carried on in a straight line at each end to the panel's edge, so a line the reader stopped
    short of the edge still closes the outline."""
    if len(path) < 2:
        return list(path)
    out = list(path)
    for end, before in ((path[0], path[1]), (path[-1], path[-2])):
        dx, dy = end[0] - before[0], end[1] - before[1]
        x, y = end
        while True:
            x, y = x + dx, y + dy
            if not (0 <= x < width and 0 <= y < height):
                break
            out.append((x, y))
    return out


def slope_side(
    owned: set[tuple[int, int]],
    seeds: list[tuple[int, int]],
    paths: list[list[tuple[int, int]]],
    joined: Callable[[tuple[int, int], tuple[int, int]], bool] | None = None,
) -> set[tuple[int, int]]:
    """The cells of an outline on its seeds' side of its slopes: the cells joined to a seed by sides without
    stepping onto a slope cell (the level's ground), plus each slope cell that `joined` says an edge joins to the
    ground, or every slope cell when `joined` is `None`. A slope cell beyond a wall from the ground, such as the line
    running outside a cave wall, isn't the level's. An outline whose seeds lie on no cell of it, or with no slope,
    is returned whole."""
    blocked = {cell for path in paths for cell in path}
    starts = [seed for seed in seeds if seed in owned and seed not in blocked]
    if not paths or not starts:
        return set(owned)
    found = set(starts)
    queue = deque(starts)
    while queue:
        x, y = queue.popleft()
        for _side, (dx, dy) in _SIDES:
            nxt = (x + dx, y + dy)
            if nxt in owned and nxt not in found and nxt not in blocked:
                found.add(nxt)
                queue.append(nxt)
    line = blocked & owned
    if joined is not None:
        line = {
            (x, y)
            for x, y in line
            if any((x + dx, y + dy) in found and joined((x, y), (x + dx, y + dy)) for _side, (dx, dy) in _SIDES)
        }
    return found | line


def _expand(
    start: tuple[int, int],
    cells: list[list[str]],
    edges: EdgeGrids,
    taken: set[tuple[int, int]],
    blocked: set[tuple[int, int]] = frozenset(),
) -> set:
    """The floor joined to a cell across passable edges, up to the cap, that no outline of the level holds yet and
    that lies on no slope."""
    width, height = len(cells[0]), len(cells)
    found = {start}
    queue = deque([start])
    while queue and len(found) < EXPANSION_CAP:
        x, y = queue.popleft()
        for side, (dx, dy) in _SIDES:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height) or (nx, ny) in found or (nx, ny) in taken:
                continue
            if (nx, ny) in blocked:
                continue
            if cells[ny][nx] == "#" or edges.at((x, y), side) not in "op":
                continue
            found.add((nx, ny))
            queue.append((nx, ny))
    return found


CROSSING = "opdls"
"""The edge letters across which an outline's border crosses floor: open, a printed open line, and every door."""


def printed_key_cells(ctx: MapContext, cells: list[list[str]], final: dict[str, dict[str, Any]]) -> dict[str, set]:
    """The cells where each level's own keys are printed, from the settled labels and the survey.

    A label is a key of a level when the survey gives that level, among the levels drawn on this map, a key with
    that text; stacked labels count each part. A label that names a key of several of the map's levels counts for
    none, since its print doesn't say which.
    """
    import json

    from .assemble import _final_marks
    from .readings import load_items

    if ctx.key.survey is None:
        return {}
    survey = json.loads(ctx.key.survey.read_text())
    mine = {level_id(dungeon, number) for dungeon, number in ctx.key.levels}
    owners: dict[str, set[str]] = {}
    for dungeon in survey.get("dungeons", []):
        for level in dungeon.get("levels", []):
            name = level_id(dungeon["id"], level["number"])
            if name in mine:
                for area in level.get("areas", []):
                    owners.setdefault(str(area["key"]), set()).add(name)
    out: dict[str, set] = {}
    for mark in _final_marks(load_items(ctx), final, cells, {}):
        for label in {mark.text, *mark.text.split("/")}:
            levels = owners.get(label, set())
            if len(levels) == 1:
                out.setdefault(next(iter(levels)), set()).add(parse_cell(mark.cell))
    return out


def mouth_inner_cells(
    ctx: MapContext, cells: list[list[str]], final: dict[str, dict[str, Any]], owned: dict[str, set]
) -> dict[str, list[tuple[int, int]]]:
    """Each level's cave mouths, as the cell just inside each: the mouth's cell that lies in the level's outline
    while the cell across it doesn't."""
    from .assemble import _final_symbols
    from .readings import load_items

    width, height = len(cells[0]), len(cells)
    out: dict[str, list[tuple[int, int]]] = {}
    for symbol in _final_symbols(load_items(ctx), final, width, height):
        if symbol.kind != "cave_mouth" or symbol.edge is None:
            continue
        (x, y), side = parse_edge(symbol.edge)
        dx, dy = dict(_SIDES)[side]
        for level, cells_of in owned.items():
            for here, there in (((x, y), (x + dx, y + dy)), ((x + dx, y + dy), (x, y))):
                if here in cells_of and there not in cells_of:
                    out.setdefault(level, []).append(here)
    return out


def outline_state(
    ctx: MapContext, cells: list[list[str]], edges: EdgeGrids, final: dict[str, dict[str, Any]]
) -> OutlineState:
    """Lay the outlines over the settled letters, apply the outline calls, and find what needs settling.

    Args:
        ctx: The map.
        cells: The settled cell letters.
        edges: The settled edge letters.
        final: The final values, whose `o:` calls place a cell in a level's outline and whose `g:` calls confirm
            or refuse an open-ground rectangle.

    Returns:
        The state.
    """
    width, height = len(cells[0]), len(cells)
    whole = wholemap_answer(ctx) or {}
    answers = {level_id(level["dungeon"], level["level"]): level for level in whole.get("levels", [])}
    levels = [level_id(dungeon, number) for dungeon, number in ctx.key.levels]
    owned: dict[str, set[tuple[int, int]]] = {}
    seeds: dict[str, list[str]] = {}
    open_ground: dict[str, list[tuple[int, int, int, int]]] = {}
    for level in levels:
        answer = answers.get(level)
        if answer is None or len(levels) == 1:
            owned[level] = {(x, y) for y in range(height) for x in range(width)}
        else:
            owned[level] = set()
            for rect in answer["rects"]:
                owned[level] |= _rect_cells(rect, width, height)
        seeds[level] = list((answer or {}).get("seeds", []))
        open_ground[level] = [tuple(rect) for rect in (answer or {}).get("open_ground", []) if len(rect) == 4]  # type: ignore[misc]
    # The slope calls: a level's outline ends at the elevation line that bounds its ground. A later call for the
    # same level (`l:x,y|level#2`) replaces an earlier one.
    slopes: dict[str, list[list[tuple[int, int]]]] = {}
    for name, call in sorted(final.items()):
        if name.startswith("l:"):
            from .settler import slope_paths

            level = name[2:].split("|", 1)[1].split("#")[0]
            if level in owned:
                slopes[level] = [extend_path(path, width, height) for path in slope_paths(call["value"])]
    blocked = {cell for paths in slopes.values() for path in paths for cell in path}
    # The calls: a cell placed in a level grows that level's outline across the floor joined to it.
    for name, call in sorted(final.items()):
        if name.startswith("o:"):
            cell = parse_cell(name[2:].split("|")[0])
            value = call["value"].strip()
            if value in owned:
                owned[value] |= _expand(cell, cells, edges, owned[value], blocked)
        elif name.startswith("g:"):
            _coords, level, index = name[2:].split("|")
            if level in open_ground and call["value"].strip() == "outside":
                position = int(index)
                if position < len(open_ground[level]):
                    open_ground[level][position] = (-1, -1, 0, 0)

    # Then the trim: whatever lies beyond a level's slope leaves its outline, however it got there.
    def joined(a: tuple[int, int], b: tuple[int, int]) -> bool:
        side = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}[(b[0] - a[0], b[1] - a[1])]
        return cells[a[1]][a[0]] != "#" and cells[b[1]][b[0]] != "#" and edges.at(a, side) in CROSSING

    for level, paths in slopes.items():
        owned[level] = slope_side(owned[level], [parse_cell(text) for text in seeds[level]], paths, joined)
    # A level's outline always holds the cells where its own keys are printed, except a key printed within two
    # cells of one of the level's cave mouths on its outer side, which the key-placement rules move inside.
    if len(levels) > 1:
        inner = mouth_inner_cells(ctx, cells, final, owned)
        for level, printed in printed_key_cells(ctx, cells, final).items():
            if level not in owned:
                continue
            for cell in printed:
                outside_mouth = cell not in owned[level] and any(
                    max(abs(cell[0] - x), abs(cell[1] - y)) <= 2 for x, y in inner.get(level, [])
                )
                if not outside_mouth:
                    owned[level].add(cell)
    state = OutlineState(levels=levels, owned=owned, seeds=seeds, open_ground=open_ground)
    if len(levels) > 1:
        asked = {name[2:].split("|")[0] for name in final if name.startswith("o:")}
        seen: set[tuple[int, int]] = set()
        for level in levels:
            for x, y in sorted(owned[level], key=lambda c: (c[1], c[0])):
                if cells[y][x] == "#":
                    continue
                for side, (dx, dy) in _SIDES:
                    nx, ny = x + dx, y + dy
                    beyond = (nx, ny)
                    if not (0 <= nx < width and 0 <= ny < height) or beyond in owned[level] or cells[ny][nx] == "#":
                        continue
                    # A border crosses floor across an open edge, a printed open line, or a door of any kind.
                    if edges.at((x, y), side) not in CROSSING or beyond in seen or f"{nx},{ny}" in asked:
                        continue
                    if (x, y) in blocked and beyond not in blocked:
                        # The border follows the slope: floor beyond a slope cell is the outside, not a crossing.
                        continue
                    seen.add(beyond)
                    others = [other for other in levels if other != level and beyond in owned[other]]
                    state.crossings.append(
                        {
                            "level": level,
                            "cell": (x, y),
                            "side": side,
                            "beyond": beyond,
                            "others": others,
                            "letter": edges.at((x, y), side),
                        }
                    )
    for level in levels:
        for index, rect in enumerate(open_ground[level]):
            if rect[2] <= 0:
                continue
            if f"g:{rect[0]},{rect[1]}|{level}|{index}" in final:
                continue
            state.open_ground_items.append({"level": level, "index": index, "rect": rect})
    return state
