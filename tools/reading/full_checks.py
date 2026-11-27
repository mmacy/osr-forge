"""The structural checks of "When part of a trace fails", run on a resolved document, level by level.

Each dungeon is walked from its entrance with the scorer's reachability walk (`walk` in `osrforge.tracing.score`),
across passable edges and doors and along the dungeon's own transitions and passages, over its traced levels. The
checks find, per level:

- **unreachable areas**: areas none of whose cells the walk reaches. An area synthesized on rock, which has no floor
  to reach, isn't checked.
- **cut-off floor**: each connected piece of the level's floor that the walk doesn't reach and that holds no area's
  cell, its cells joined across passable edges and doors. Floor in an unreachable area counts with that area.
- **doors without floor**: door edges (`d`, `l`, `s`) with neither floor (or water) nor the outside on one side. A
  side is the outside when it lies off the level's grid or off its panels, or on a panel cell that is floor or water
  and that no level of the document owns.
- **bypasses**: locked doors (`l` edges and the level's prose-locked doors) whose two sides the walk still reaches
  with that door closed, when it reaches both with it open.
- **disputes**: joins between two areas of the level that the prose (a passage, door, or secret door connection in
  the content cache) or the map-reading cache states and that the walk, kept to the level, doesn't make: no floor
  cell of the second area is reached from the floor cells of the first.

A dungeon with no entrance on a traced level can't be walked, and each of its traced levels is marked so.

A level *needs no repair* when it has no unreachable area, no cut-off floor, and no door without floor, and its
dungeon can be walked: those are the faults a repair changes. A bypass or a dispute changes no geometry, so it is
reported and doesn't count.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from osrforge.tracing.fivefoot import convert_five_foot
from osrforge.tracing.score import walk
from osrforge.tracing.truth import (
    DOOR_LETTERS,
    EdgeGrids,
    GeometryTruth,
    Level,
    Panel,
    cell_key,
    edge_key,
    parse_cell,
    parse_edge,
    rotate_cell,
)

__all__ = ["LevelChecks", "check_document"]

_NEIGHBOUR = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}
_WALKABLE = "opdls"


@dataclass
class LevelChecks:
    """One level's faults."""

    dungeon: str
    level: int
    walked: bool = True
    """The dungeon has an entrance on a traced level, so the walk ran."""

    unreachable: list[str] = field(default_factory=list)
    cut_off: list[list[str]] = field(default_factory=list)
    """Each cut-off piece's cells."""

    doors_without_floor: list[str] = field(default_factory=list)
    bypasses: list[str] = field(default_factory=list)
    disputes: list[str] = field(default_factory=list)

    @property
    def needs_no_repair(self) -> bool:
        """No fault a repair changes, and a walkable dungeon."""
        return self.walked and not self.unreachable and not self.cut_off and not self.doors_without_floor

    def to_json(self) -> dict[str, Any]:
        """The checks as JSON."""
        return {
            "walked": self.walked,
            "unreachable": self.unreachable,
            "cut_off": [len(piece) for piece in self.cut_off],
            "doors_without_floor": self.doors_without_floor,
            "bypasses": self.bypasses,
            "disputes": self.disputes,
            "needs_no_repair": self.needs_no_repair,
        }


def _letter(edges: EdgeGrids, cell: tuple[int, int], side: str) -> str:
    x, y = cell
    try:
        if side == "west":
            return edges.vertical[y][x]
        if side == "east":
            return edges.vertical[y][x + 1]
        if side == "north":
            return edges.horizontal[y][x]
        return edges.horizontal[y + 1][x]
    except IndexError:
        return "-"


def _with_letter(level: Level, cell: tuple[int, int], side: str, letter: str) -> Level:
    assert level.edges is not None
    vertical = [list(row) for row in level.edges.vertical]
    horizontal = [list(row) for row in level.edges.horizontal]
    x, y = cell
    if side == "west":
        vertical[y][x] = letter
    elif side == "east":
        vertical[y][x + 1] = letter
    elif side == "north":
        horizontal[y][x] = letter
    else:
        horizontal[y + 1][x] = letter
    edges = EdgeGrids(vertical=tuple(map("".join, vertical)), horizontal=tuple(map("".join, horizontal)))
    return level.model_copy(update={"edges": edges})


def _converted(panel: Panel) -> Panel:
    return convert_five_foot(panel).panel if panel.scale_feet == 5 else panel


def _placements(document: GeometryTruth) -> dict[tuple[str, int], dict[tuple[int, int], tuple[str, tuple[int, int]]]]:
    """Each traced level's cells mapped to the panel cell they come from."""
    panels = {panel.id: _converted(panel) for panel in document.panels}
    out: dict[tuple[str, int], dict[tuple[int, int], tuple[str, tuple[int, int]]]] = {}
    for dungeon in document.dungeons:
        for level in dungeon.levels:
            if level.verdict != "traced":
                continue
            mapping: dict[tuple[int, int], tuple[str, tuple[int, int]]] = {}
            for placement in level.panels:
                panel = panels.get(placement.panel)
                if panel is None:
                    continue
                size = (panel.width, panel.height)
                for y in range(panel.height):
                    for x in range(panel.width):
                        turned = rotate_cell((x, y), size, placement.rotation)
                        mapping[(placement.origin[0] + turned[0], placement.origin[1] + turned[1])] = (panel.id, (x, y))
            out[(dungeon.id, level.number)] = mapping
    return out


def _floor(level: Level) -> set[tuple[int, int]]:
    return {(x, y) for y, row in enumerate(level.cells) for x, letter in enumerate(row) if letter == "."}


def _pieces(level: Level, cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """The cells joined into pieces across walkable edges."""
    assert level.edges is not None
    left, pieces = set(cells), []
    while left:
        start = min(left, key=lambda cell: (cell[1], cell[0]))
        piece, stack = {start}, [start]
        left.discard(start)
        while stack:
            cell = stack.pop()
            for side, (dx, dy) in _NEIGHBOUR.items():
                other = (cell[0] + dx, cell[1] + dy)
                if other in left and _letter(level.edges, cell, side) in _WALKABLE:
                    left.discard(other)
                    piece.add(other)
                    stack.append(other)
        pieces.append(piece)
    return pieces


def _joins(
    document: GeometryTruth, contents: Mapping[Any, Any], mapread: Any
) -> dict[tuple[str, int], set[tuple[str, str]]]:
    """The area joins the prose and the map-reading cache state, per level."""
    out: dict[tuple[str, int], set[tuple[str, str]]] = {}
    for (dungeon_id, number), content in contents.items():
        for area in content.areas:
            for connection in area.connections:
                if connection.via in ("passage", "door", "secret_door") and connection.to_key:
                    one, other = str(area.key), str(connection.to_key)
                    out.setdefault((dungeon_id, number), set()).add((min(one, other), max(one, other)))
    for reading in getattr(mapread, "levels", ()):
        for proposal in reading.proposals:
            one, other = str(proposal.a), str(proposal.b)
            out.setdefault((reading.dungeon_id, reading.level_number), set()).add((min(one, other), max(one, other)))
    return out


def check_document(document: GeometryTruth, contents: Mapping[Any, Any], mapread: Any) -> list[LevelChecks]:
    """Every traced level's checks.

    Args:
        document: A resolved document: a trace, or an answer key.
        contents: The content caches by `(dungeon id, level number)`.
        mapread: The map-reading cache.

    Returns:
        One entry per traced level, in document order.
    """
    placements = _placements(document)
    panels = {panel.id: _converted(panel) for panel in document.panels}
    owned: set[tuple[str, tuple[int, int]]] = set()
    for dungeon in document.dungeons:
        for level in dungeon.levels:
            mapping = placements.get((dungeon.id, level.number), {})
            for y, row in enumerate(level.cells):
                for x, letter in enumerate(row):
                    if letter in ".~" and (x, y) in mapping:
                        owned.add(mapping[(x, y)])
    joins = _joins(document, contents, mapread)
    out: list[LevelChecks] = []
    for dungeon in document.dungeons:
        levels = {level.number: level for level in dungeon.levels if level.verdict == "traced" and level.edges}
        entrance = dungeon.entrance
        walkable = entrance is not None and entrance.level in levels
        seen = walk(levels, dungeon.id, [(entrance.level, parse_cell(entrance.cell))]) if walkable else set()  # type: ignore[union-attr]
        for number, level in levels.items():
            checks = LevelChecks(dungeon=dungeon.id, level=number, walked=walkable)
            assert level.edges is not None
            reached = {cell for n, cell in seen if n == number}
            area_cells: set[tuple[int, int]] = set()
            for name, area in level.areas.items():
                cells = {parse_cell(text) for text in area.cells}
                area_cells |= cells
                if walkable and not area.synthesized and not cells & reached:
                    checks.unreachable.append(name)
            if walkable:
                for piece in _pieces(level, _floor(level) - reached - area_cells):
                    checks.cut_off.append(sorted(map(cell_key, piece)))
            mapping = placements.get((dungeon.id, number), {})

            def side_ok(cell: tuple[int, int], level: Level = level, mapping: Any = mapping) -> bool:
                x, y = cell
                if not (0 <= y < level.height and 0 <= x < level.width):
                    return True
                if level.cells[y][x] in ".~":
                    return True
                source = mapping.get(cell)
                if source is None:
                    return True
                panel = panels[source[0]]
                px, py = source[1]
                return panel.cells[py][px] in ".~" and source not in owned

            for (x, y), side in level.edges.positions():
                if level.edges.at((x, y), side) not in DOOR_LETTERS:
                    continue
                dx, dy = _NEIGHBOUR[side]
                if not (side_ok((x, y)) and side_ok((x + dx, y + dy))):
                    checks.doors_without_floor.append(edge_key((x, y), side))
            if walkable:
                locked = {
                    edge_key((x, y), side)
                    for (x, y), side in level.edges.positions()
                    if level.edges.at((x, y), side) == "l"
                } | set(level.locked_doors)
                for name in sorted(locked):
                    cell, side = parse_edge(name)
                    dx, dy = _NEIGHBOUR[side]
                    sides = [(number, cell), (number, (cell[0] + dx, cell[1] + dy))]
                    if not all(item in seen for item in sides):
                        continue
                    closed = dict(levels) | {number: _with_letter(level, cell, side, "w")}
                    still = walk(closed, dungeon.id, [(entrance.level, parse_cell(entrance.cell))])  # type: ignore[union-attr]
                    if all(item in still for item in sides):
                        checks.bypasses.append(name)
            for one, other in sorted(joins.get((dungeon.id, number), set())):
                if one not in level.areas or other not in level.areas:
                    continue
                first = {parse_cell(t) for t in level.areas[one].cells} & _floor(level)
                second = {parse_cell(t) for t in level.areas[other].cells} & _floor(level)
                if not first or not second:
                    continue
                kept = walk({number: level}, dungeon.id, [(number, cell) for cell in first])
                if not any((number, cell) in kept for cell in second):
                    checks.disputes.append(f"{one}-{other}")
            out.append(checks)
    return out
