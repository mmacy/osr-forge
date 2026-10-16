"""The 5-foot conversion: a panel read in 5-foot squares becomes 10-foot cells.

The rules are in `docs/tracing-conventions.md` under "5-foot maps". The
conversion picks the block phase that puts the most walls on 10-foot lines,
turns each 2 by 2 block of squares into one cell, keeps narrow runs joined,
and moves each wall or door that lies on a line that isn't a 10-foot line onto
one, recording every move.
"""

from collections.abc import Iterable
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from osrforge.tracing.truth import (
    EdgeGrids,
    Mark,
    Outline,
    Panel,
    Symbol,
    cell_key,
    edge_key,
    order_key,
    parse_cell,
    parse_edge,
)

__all__ = ["FiveFootConversion", "WallMove", "convert_five_foot"]


class WallMove(BaseModel):
    """One run of wall or door that the conversion moved onto a 10-foot line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    axis: Literal["vertical", "horizontal"]
    """Which way the line runs: a vertical line runs north to south."""

    line: int = Field(ge=0)
    """The 5-foot line the run lay on: the grid line index, 0 at the west or north frame."""

    moved_to: int = Field(ge=0)
    """The 10-foot line, as a 5-foot line index, the run moved to."""

    start: int = Field(ge=0)
    """The first square along the line the run covers."""

    end: int = Field(ge=0)
    """The square after the last the run covers."""


class FiveFootConversion(BaseModel):
    """A converted panel with the choices the conversion made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    panel: Panel
    """The panel in 10-foot cells. Its `scale_feet` is 10."""

    phase: tuple[int, int]
    """The block phase `(ox, oy)`: 10-foot lines lie on 5-foot lines `ox + 2k` across and `oy + 2k` down."""

    moves: tuple[WallMove, ...]
    """Every wall or door run moved onto a 10-foot line, in north-then-west order of the runs."""


_BLOCKED = "wndls"
"""The segment letters that block: wall, window, door, locked door, and secret door."""

_PASSABLE = "op"
"""The segment letters a party walks across: open and printed open line."""

_PRECEDENCE = "lsdnwpo"
"""The order in which a 10-foot edge takes the letter of one of its two segments."""

_NO_ROOMS: frozenset[int] = frozenset()

type _Axis = Literal["vertical", "horizontal"]

type _Square = tuple[int, int]
"""A 5-foot square or a 10-foot cell, as `(x, y)`."""

type _Edge = tuple[_Axis, int, int]
"""A 5-foot segment or a 10-foot edge: its axis, the grid line it lies on, and the square or cell along that line."""


class _Run(NamedTuple):
    """A maximal straight sequence of blocked segments between floor squares on a line that isn't a 10-foot line."""

    axis: _Axis
    line: int
    start: int
    end: int

    def midpoint(self) -> tuple[float, float]:
        """The middle of the run as a grid point `(x, y)`."""
        middle = (self.start + self.end) / 2
        return (self.line, middle) if self.axis == "vertical" else (middle, self.line)


def _neighbours(square: _Square) -> tuple[_Square, ...]:
    x, y = square
    return ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1))


def _between(a: _Square, b: _Square) -> _Edge:
    """The segment between two squares that share a side, or the edge between two cells that do."""
    if a[1] == b[1]:
        return ("vertical", max(a[0], b[0]), a[1])
    return ("horizontal", max(a[1], b[1]), a[0])


def _sides(edge: _Edge) -> tuple[_Square, _Square]:
    """The squares or cells west and east of a vertical line, or north and south of a horizontal one."""
    axis, line, index = edge
    if axis == "vertical":
        return (line - 1, index), (line, index)
    return (index, line - 1), (index, line)


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _phase(panel: Panel) -> tuple[int, int]:
    """The block phase that puts the most blocked segments on 10-foot lines, frame lines included."""
    vertical = [sum(row[line] in _BLOCKED for row in panel.edges.vertical) for line in range(panel.width + 1)]
    horizontal = [sum(letter in _BLOCKED for letter in row) for row in panel.edges.horizontal]

    def preference(phase: tuple[int, int]) -> tuple[int, bool, bool]:
        ox, oy = phase
        return (sum(vertical[ox::2]) + sum(horizontal[oy::2]), oy == 0, ox == 0)

    return max(((0, 0), (1, 0), (0, 1), (1, 1)), key=preference)


class _Grid:
    """A 5-foot reading under one phase, with its segments and its squares' open spaces as the moves leave them."""

    def __init__(self, panel: Panel, phase: tuple[int, int]) -> None:
        self.panel = panel
        self.width = panel.width
        self.height = panel.height
        self.ox, self.oy = phase
        self.columns = (self.width + self.ox + 1) // 2
        self.rows = (self.height + self.oy + 1) // 2
        # Both axes are stored line by line, so a segment is `lines[axis][line][index]`.
        self.lines: dict[_Axis, list[list[str]]] = {
            "vertical": [[row[line] for row in panel.edges.vertical] for line in range(self.width + 1)],
            "horizontal": [list(row) for row in panel.edges.horizontal],
        }
        self.moved: dict[_Edge, int] = {}
        """The 10-foot line each moved segment went to, by the segment's place before the move."""

        self.space: dict[_Square, int] = {}
        """The open space each floor square belongs to, after the moves so far."""

        self.walled: list[bool] = []
        """Whether each open space is a walled room."""

        self._find_spaces()
        self.space_size = [0] * len(self.walled)
        for space in self.space.values():
            self.space_size[space] += 1
        self.cell_rooms: dict[_Square, frozenset[int]] = {}
        """The walled rooms each cell belongs to, after the moves so far."""

        self.room_cells = [0] * len(self.walled)
        """How many cells each walled room has, after the moves so far."""

        for cell in {self.cell_of(square) for square in self.space}:
            rooms = self._rooms_in(cell)
            self.cell_rooms[cell] = rooms
            for room in rooms:
                self.room_cells[room] += 1
        self.narrow_squares: set[_Square] = set()
        self.narrow_edges: set[_Edge] = set()
        """The 10-foot edges between consecutive cells of a narrow run."""

        self._find_narrow_runs()

    def kind(self, square: _Square) -> str:
        """A square's cell letter, rock off the grid."""
        x, y = square
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.panel.cells[y][x]
        return "#"

    def segment(self, segment: _Edge) -> str:
        """A segment's letter, `-` off the grid."""
        axis, line, index = segment
        lines = self.lines[axis]
        if 0 <= line < len(lines) and 0 <= index < len(lines[line]):
            return lines[line][index]
        return "-"

    def cell_of(self, square: _Square) -> _Square:
        """The cell that contains a square."""
        return ((square[0] + self.ox) // 2, (square[1] + self.oy) // 2)

    def squares_of(self, cell: _Square) -> tuple[_Square, ...]:
        """The four squares a cell covers, some of them off the grid at the edges."""
        left, top = 2 * cell[0] - self.ox, 2 * cell[1] - self.oy
        return ((left, top), (left + 1, top), (left, top + 1), (left + 1, top + 1))

    def covered(self, edge: _Edge) -> tuple[_Edge, _Edge]:
        """The two segments a 10-foot edge covers."""
        axis, line, index = edge
        across, along = (self.ox, self.oy) if axis == "vertical" else (self.oy, self.ox)
        first = 2 * index - along
        return (axis, 2 * line - across, first), (axis, 2 * line - across, first + 1)

    def edge_of(self, segment: _Edge) -> _Edge:
        """The 10-foot edge that covers a segment on a 10-foot line."""
        axis, line, index = segment
        across, along = (self.ox, self.oy) if axis == "vertical" else (self.oy, self.ox)
        return (axis, (line + across) // 2, (index + along) // 2)

    def read_letter(self, edge: _Edge) -> str | None:
        """The first letter of a 10-foot edge's read segments in precedence order, `None` when neither is read."""
        read = [letter for letter in map(self.segment, self.covered(edge)) if letter != "-"]
        return min(read, key=_PRECEDENCE.index) if read else None

    def _find_spaces(self) -> None:
        # An open space is a walled room when every side it shares with anything but its own floor is blocked. The
        # rule's open-ground clause excludes nothing here: open ground is floor outside every walled structure, so an
        # enclosed space holds none.
        for y in range(self.height):
            for x in range(self.width):
                if self.kind((x, y)) != "." or (x, y) in self.space:
                    continue
                number = len(self.walled)
                self.space[(x, y)] = number
                stack = [(x, y)]
                enclosed = True
                while stack:
                    square = stack.pop()
                    for neighbour in _neighbours(square):
                        if self.segment(_between(square, neighbour)) in _BLOCKED:
                            continue
                        if self.kind(neighbour) != ".":
                            enclosed = False
                        elif neighbour not in self.space:
                            self.space[neighbour] = number
                            stack.append(neighbour)
                self.walled.append(enclosed)

    def _find_narrow_runs(self) -> None:
        # A narrow run's squares each have at most two passable floor neighbours and lie in no 2 by 2 block of floor
        # joined across passable sides, and consecutive squares are joined by an open side. Three of a block's four
        # inner sides are enough to join all four squares.
        def passable_floor(square: _Square) -> int:
            return sum(
                self.kind(neighbour) == "." and self.segment(_between(square, neighbour)) in _PASSABLE
                for neighbour in _neighbours(square)
            )

        def in_joined_block(square: _Square) -> bool:
            x, y = square
            for left, top in ((x - 1, y - 1), (x, y - 1), (x - 1, y), (x, y)):
                nw, ne, sw, se = (left, top), (left + 1, top), (left, top + 1), (left + 1, top + 1)
                if any(self.kind(corner) != "." for corner in (nw, ne, sw, se)):
                    continue
                inner = ((nw, ne), (sw, se), (nw, sw), (ne, se))
                if sum(self.segment(_between(a, b)) in _PASSABLE for a, b in inner) >= 3:
                    return True
            return False

        candidates = {
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if self.kind((x, y)) == "." and passable_floor((x, y)) <= 2 and not in_joined_block((x, y))
        }
        for square in candidates:
            for neighbour in ((square[0] + 1, square[1]), (square[0], square[1] + 1)):
                if neighbour not in candidates or self.segment(_between(square, neighbour)) != "o":
                    continue
                self.narrow_squares.update((square, neighbour))
                cell, other = self.cell_of(square), self.cell_of(neighbour)
                if cell != other:
                    self.narrow_edges.add(_between(cell, other))

    def runs(self) -> list[_Run]:
        """Every run, in north-then-west order of its midpoint with north at the top of the page."""
        runs: list[_Run] = []
        axes: tuple[tuple[_Axis, int], ...] = (("vertical", self.ox), ("horizontal", self.oy))
        for axis, offset in axes:
            for line, letters in enumerate(self.lines[axis]):
                if (line - offset) % 2 == 0:
                    continue
                start: int | None = None
                for index in range(len(letters) + 1):
                    inside = index < len(letters) and self._separates_floor((axis, line, index))
                    if inside and start is None:
                        start = index
                    elif not inside and start is not None:
                        runs.append(_Run(axis, line, start, index))
                        start = None
        return sorted(runs, key=lambda run: (order_key(run.midpoint()), run.axis, run.line, run.start))

    def _separates_floor(self, segment: _Edge) -> bool:
        low, high = _sides(segment)
        return self.segment(segment) in _BLOCKED and self.kind(low) == "." and self.kind(high) == "."

    def destination(self, run: _Run) -> int:
        """The 10-foot line a run moves to, by the four rules in order.

        Rules 1 and 2 are tests a direction passes or fails, and each decides
        only when one direction passes and the other fails.
        """
        lower, upper = run.line - 1, run.line + 1
        for keeps in (self._keeps_rooms, self._keeps_narrow_runs):
            lower_kept, upper_kept = keeps(run, lower), keeps(run, upper)
            if lower_kept != upper_kept:
                return lower if lower_kept else upper
        lower_part, upper_part = self._part(run, 0), self._part(run, 1)
        if lower_part != upper_part:
            return lower if lower_part > upper_part else upper
        return upper

    def _changes(self, run: _Run, to: int) -> dict[_Square, int]:
        """The squares between a run's line and `to`, each with the open space of the side the run moves away from."""
        changes: dict[_Square, int] = {}
        for index in range(run.start, run.end):
            low, high = _sides((run.axis, run.line, index))
            if to < run.line:
                changes[low] = self.space[high]
            else:
                changes[high] = self.space[low]
        return changes

    def _rooms_in(self, cell: _Square, changes: dict[_Square, int] | None = None) -> frozenset[int]:
        """The walled rooms that have at least two of a cell's four squares, with `changes` applied."""
        counts: dict[int, int] = {}
        for square in self.squares_of(cell):
            space = (changes or {}).get(square, self.space.get(square))
            if space is not None and self.walled[space]:
                counts[space] = counts.get(space, 0) + 1
        return frozenset(space for space, count in counts.items() if count >= 2)

    def _keeps_rooms(self, run: _Run, to: int) -> bool:
        # Rule 1: the move fails when it leaves a walled room that has a cell with none. A room that has no cell
        # before the move doesn't count against either direction.
        changes = self._changes(run, to)
        change: dict[int, int] = {}
        for cell in {self.cell_of(square) for square in changes}:
            before = self.cell_rooms.get(cell, _NO_ROOMS)
            after = self._rooms_in(cell, changes)
            for room in before - after:
                change[room] = change.get(room, 0) - 1
            for room in after - before:
                change[room] = change.get(room, 0) + 1
        return all(self.room_cells[room] + delta > 0 for room, delta in change.items() if self.room_cells[room] > 0)

    def _keeps_narrow_runs(self, run: _Run, to: int) -> bool:
        # Rule 2: the move fails when it blocks a passable edge between consecutive cells of a narrow run. The moved
        # letters are blocked, so every 10-foot edge they land on is blocked after the move.
        for index in range(run.start, run.end):
            edge = self.edge_of((run.axis, to, index))
            if edge in self.narrow_edges:
                letter = self.read_letter(edge)
                if letter is not None and letter in _PASSABLE:
                    return False
        return True

    def _part(self, run: _Run, side: int) -> int:
        """The squares of the open spaces on one side of a run: side 0 is west or north, side 1 east or south."""
        spaces = {self.space[_sides((run.axis, run.line, index))[side]] for index in range(run.start, run.end)}
        return sum(self.space_size[space] for space in spaces)

    def move(self, run: _Run, to: int) -> WallMove:
        """Move a run onto the 10-foot line `to`.

        The squares between the two lines go to the side the run moves away
        from, the run's segments become open, and each segment on the new line
        takes the letter of the moved segment beside it.
        """
        changes = self._changes(run, to)
        cells = {self.cell_of(square) for square in changes}
        before = {cell: self.cell_rooms.get(cell, _NO_ROOMS) for cell in cells}
        for square, space in changes.items():
            self.space_size[self.space[square]] -= 1
            self.space_size[space] += 1
            self.space[square] = space
        for cell in cells:
            after = self._rooms_in(cell)
            for room in before[cell] - after:
                self.room_cells[room] -= 1
            for room in after - before[cell]:
                self.room_cells[room] += 1
            self.cell_rooms[cell] = after
        letters = self.lines[run.axis]
        for index in range(run.start, run.end):
            letters[to][index] = letters[run.line][index]
            letters[run.line][index] = "o"
            self.moved[(run.axis, run.line, index)] = to
        return WallMove(axis=run.axis, line=run.line, moved_to=to, start=run.start, end=run.end)

    def converted_panel(self) -> Panel:
        """The panel in 10-foot cells, with the moves made."""
        panel = self.panel
        partial_squares = {parse_cell(text) for text in panel.partial}
        cave_squares = {parse_cell(text) for text in panel.cave}
        narrow_cells = {self.cell_of(square) for square in self.narrow_squares}
        rows: list[str] = []
        partial: list[str] = []
        cave: list[str] = []
        for y in range(self.rows):
            row = ""
            for x in range(self.columns):
                squares = self.squares_of((x, y))
                kinds = [self.kind(square) for square in squares]
                if (x, y) in narrow_cells or kinds.count(".") >= 2:
                    row += "."
                elif kinds.count("~") >= 2:
                    row += "~"
                else:
                    row += "#"
                if len(set(kinds)) > 1 or not partial_squares.isdisjoint(squares):
                    partial.append(cell_key((x, y)))
                if not cave_squares.isdisjoint(squares):
                    cave.append(cell_key((x, y)))
            rows.append(row)

        def occupied(cell: _Square) -> bool:
            x, y = cell
            return 0 <= x < self.columns and 0 <= y < self.rows and rows[y][x] != "#"

        def letter(edge: _Edge) -> str:
            low, high = _sides(edge)
            if not (occupied(low) or occupied(high)):
                return "-"
            read = self.read_letter(edge)
            result = "w" if read is None else read
            if edge in self.narrow_edges and result not in _PASSABLE:
                result = "o"
            # Two cells are cells of two different walled rooms when each belongs to a room and no room has both.
            low_rooms = self.cell_rooms.get(low, _NO_ROOMS)
            high_rooms = self.cell_rooms.get(high, _NO_ROOMS)
            if result in _PASSABLE and low_rooms and high_rooms and low_rooms.isdisjoint(high_rooms):
                result = "w"
            return result

        vertical = tuple("".join(letter(("vertical", x, y)) for x in range(self.columns + 1)) for y in range(self.rows))
        horizontal = tuple(
            "".join(letter(("horizontal", y, x)) for x in range(self.columns)) for y in range(self.rows + 1)
        )
        return Panel(
            id=panel.id,
            regions=tuple(
                region.model_copy(update={"origin": self.cell_of(region.origin)}) for region in panel.regions
            ),
            scale_feet=10,
            cells=tuple(rows),
            edges=EdgeGrids(vertical=vertical, horizontal=horizontal),
            partial=tuple(partial),
            cave=tuple(cave),
            marks=tuple(self._convert_mark(mark) for mark in panel.marks),
            outlines=tuple(self._convert_outline(outline) for outline in panel.outlines),
            symbols=tuple(self._convert_symbol(symbol) for symbol in panel.symbols),
        )

    def _convert_cell(self, text: str) -> str:
        return cell_key(self.cell_of(parse_cell(text)))

    def _convert_edge(self, text: str) -> str:
        # An edge becomes the same side of the cell that contains its square. When its segment moved, it becomes
        # the side of that cell the segment moved to, so a mark on a door stays on the door.
        (x, y), side = parse_edge(text)
        cell = self.cell_of((x, y))
        if side in ("west", "east"):
            to = self.moved.get(("vertical", x if side == "west" else x + 1, y))
            if to is not None:
                side = "west" if to == 2 * cell[0] - self.ox else "east"
        else:
            to = self.moved.get(("horizontal", y if side == "north" else y + 1, x))
            if to is not None:
                side = "north" if to == 2 * cell[1] - self.oy else "south"
        return edge_key(cell, side)

    def _convert_rect(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        # The smallest rectangle of cells that covers the squares `x` to `x + width` across and `y` to `y + height`
        # down: its far side is the cell boundary at or beyond the rectangle's far side.
        x, y, width, height = rect
        left, top = (x + self.ox) // 2, (y + self.oy) // 2
        right, bottom = (x + width + self.ox + 1) // 2, (y + height + self.oy + 1) // 2
        return (left, top, right - left, bottom - top)

    def _convert_mark(self, mark: Mark) -> Mark:
        return mark.model_copy(
            update={
                "cell": self._convert_cell(mark.cell),
                "edge": None if mark.edge is None else self._convert_edge(mark.edge),
                "leader_to": None if mark.leader_to is None else self._convert_cell(mark.leader_to),
            }
        )

    def _convert_outline(self, outline: Outline) -> Outline:
        return outline.model_copy(
            update={
                "rects": tuple(self._convert_rect(rect) for rect in outline.rects),
                "seeds": _unique(self._convert_cell(seed) for seed in outline.seeds),
                "open_ground": tuple(self._convert_rect(rect) for rect in outline.open_ground),
            }
        )

    def _convert_symbol(self, symbol: Symbol) -> Symbol:
        return symbol.model_copy(
            update={
                "cells": _unique(self._convert_cell(cell) for cell in symbol.cells),
                "edge": None if symbol.edge is None else self._convert_edge(symbol.edge),
            }
        )


def convert_five_foot(panel: Panel) -> FiveFootConversion:
    """Convert a 5-foot panel to 10-foot cells.

    Cell `(X, Y)` of the result covers 5-foot squares `2X - ox` and `2X - ox + 1`
    across and `2Y - oy` and `2Y - oy + 1` down, where `(ox, oy)` is the chosen
    phase. Squares outside the 5-foot grid count as rock. The result is
    `ceil((width + ox) / 2)` cells wide and `ceil((height + oy) / 2)` high.

    The panel alone doesn't say where its compass points, so the runs are put
    in north-then-west order with north at the top of the page. Each run's
    direction is decided in that order, after the moves of the runs before it.
    An edge that a mark or a cave mouth names becomes the same side of the cell
    that contains its square, or, when the segment moved, the side of that cell
    it moved to.

    Args:
        panel: A panel whose `scale_feet` is 5.

    Returns:
        The converted panel, the phase, and the moves.

    Raises:
        ValueError: If the panel isn't a 5-foot panel.
    """
    if panel.scale_feet != 5:
        raise ValueError(f"panel {panel.id!r} is read in {panel.scale_feet}-foot squares, not 5-foot squares")
    phase = _phase(panel)
    grid = _Grid(panel, phase)
    moves: list[WallMove] = []
    for run in grid.runs():
        moves.append(grid.move(run, grid.destination(run)))
    return FiveFootConversion(panel=grid.converted_panel(), phase=phase, moves=tuple(moves))
