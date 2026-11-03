"""The settled letters of a map and the outlines over them: what the assembly and the outline settling share.

`final_cells` and `final_edges` turn the readings table and the final values into the panel's letters.
`outline_state` lays the whole-map read's outlines over those letters, applies the outline calls the settler
made, and finds what still needs settling: floor just outside every outline that an open edge joins to a
level's floor (a border that crosses floor), and each open-ground rectangle, which the settler confirms as
open ground of its level or sends to the outside.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from osrforge.tracing.truth import EdgeGrids, parse_cell

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
    for name, entry in items.edges.items():
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
        if (occupied_a != occupied_b) and letter not in "dls":
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


def _expand(start: tuple[int, int], cells: list[list[str]], edges: EdgeGrids, taken: set[tuple[int, int]]) -> set:
    """The floor joined to a cell across passable edges, up to the cap, that no outline of the level holds yet."""
    width, height = len(cells[0]), len(cells)
    found = {start}
    queue = deque([start])
    while queue and len(found) < EXPANSION_CAP:
        x, y = queue.popleft()
        for side, (dx, dy) in _SIDES:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height) or (nx, ny) in found or (nx, ny) in taken:
                continue
            if cells[ny][nx] == "#" or edges.at((x, y), side) not in "op":
                continue
            found.add((nx, ny))
            queue.append((nx, ny))
    return found


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
    # The calls: a cell placed in a level grows that level's outline across the floor joined to it.
    for name, call in sorted(final.items()):
        if name.startswith("o:"):
            cell = parse_cell(name[2:].split("|")[0])
            value = call["value"].strip()
            if value in owned:
                owned[value] |= _expand(cell, cells, edges, owned[value])
        elif name.startswith("g:"):
            _coords, level, index = name[2:].split("|")
            if level in open_ground and call["value"].strip() == "outside":
                position = int(index)
                if position < len(open_ground[level]):
                    open_ground[level][position] = (-1, -1, 0, 0)
    state = OutlineState(levels=levels, owned=owned, seeds=seeds, open_ground=open_ground)
    if len(levels) > 1:
        any_outline: set[tuple[int, int]] = set().union(*owned.values())
        asked = {name[2:].split("|")[0] for name in final if name.startswith("o:")}
        seen: set[tuple[int, int]] = set()
        for level in levels:
            for x, y in sorted(owned[level], key=lambda c: (c[1], c[0])):
                if cells[y][x] == "#":
                    continue
                for side, (dx, dy) in _SIDES:
                    nx, ny = x + dx, y + dy
                    beyond = (nx, ny)
                    if not (0 <= nx < width and 0 <= ny < height) or beyond in any_outline or cells[ny][nx] == "#":
                        continue
                    if edges.at((x, y), side) not in "op" or beyond in seen or f"{nx},{ny}" in asked:
                        continue
                    seen.add(beyond)
                    state.crossings.append({"level": level, "cell": (x, y), "side": side, "beyond": beyond})
    for level in levels:
        for index, rect in enumerate(open_ground[level]):
            if rect[2] <= 0:
                continue
            if f"g:{rect[0]},{rect[1]}|{level}|{index}" in final:
                continue
            state.open_ground_items.append({"level": level, "index": index, "rect": rect})
    return state
