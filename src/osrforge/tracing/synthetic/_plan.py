"""A synthetic map's drawing plan: the reading it prints, and the ornaments drawn over it.

A [`Sketch`][osrforge.tracing.synthetic._plan.Sketch] is how a map is authored:
rooms and runs of floor, each with a name, water, and the edges that aren't
the default. Two floor cells of the same name are open to each other, two of
different names are walled, floor beside water is open, and floor or water
beside rock or the frame is walled. [`Sketch.build`][osrforge.tracing.synthetic._plan.Sketch.build]
turns it into a [`Plan`][osrforge.tracing.synthetic._plan.Plan], which holds
the exact reading (cells, edges, marks, symbols, and the outline) in the truth
format's letters, plus what the drawing needs and the reading doesn't record.
"""

import itertools
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from osrforge.tracing.truth import EdgeGrids, Mark, Symbol, SymbolKind, cell_key, edge_key, parse_cell, parse_edge

type Cell = tuple[int, int]
type Point = tuple[float, float]

STEP: dict[str, Cell] = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}
"""The offset to the neighbour across each side."""

SIDES: tuple[str, ...] = ("north", "east", "south", "west")


def neighbour(cell: Cell, side: str) -> Cell:
    """The cell across one side of a cell.

    Args:
        cell: The cell.
        side: The side.

    Returns:
        The neighbouring cell, which may be off the grid.
    """
    dx, dy = STEP[side]
    return (cell[0] + dx, cell[1] + dy)


def canonical(cell: Cell, side: str) -> tuple[Cell, str]:
    """An edge named from the cell east or south of it, so both names of an edge agree.

    Args:
        cell: A cell beside the edge.
        side: The edge's side of that cell.

    Returns:
        The cell and side, with the side `north` or `west`.
    """
    if side == "east":
        return (cell[0] + 1, cell[1]), "west"
    if side == "south":
        return (cell[0], cell[1] + 1), "north"
    return cell, side


Decoration = Literal["contour", "track", "stream", "tree", "dashed_circle"]
"""An ornament drawn over the map that the reading doesn't record."""


@dataclass(frozen=True)
class Ornament:
    """One ornament, in grid coordinates.

    A contour, a track, and a stream follow `points`. A tree and a dashed
    circle are centred on `points[0]` with radius `size`.
    """

    kind: Decoration
    points: tuple[Point, ...]
    size: float = 0.0


@dataclass(frozen=True)
class Plan:
    """A panel's exact reading, and how to draw it.

    `edge_styles` names edges drawn other than by their letter's default:
    `dashdot` for a dash-dot partition, `hidden` for edges an ornament draws.
    `stair_runs` gives the direction a stair or steps symbol runs, by its
    first cell, when its cells don't show it.
    """

    width: int
    height: int
    cells: tuple[str, ...]
    edges: EdgeGrids
    marks: tuple[Mark, ...] = ()
    symbols: tuple[Symbol, ...] = ()
    rects: tuple[tuple[int, int, int, int], ...] = ()
    open_ground: tuple[tuple[int, int, int, int], ...] = ()
    seeds: tuple[str, ...] = ()
    edge_styles: dict[str, str] = field(default_factory=dict[str, str])
    stair_runs: dict[str, str] = field(default_factory=dict[str, str])
    ornaments: tuple[Ornament, ...] = ()

    def letter(self, cell: Cell, side: str) -> str:
        """The edge letter on one side of a cell.

        Args:
            cell: A cell of the plan.
            side: The side.

        Returns:
            The letter.
        """
        return self.edges.at(cell, side)

    def cell(self, cell: Cell) -> str:
        """The cell letter, `#` off the grid.

        Args:
            cell: Any cell.

        Returns:
            The letter.
        """
        x, y = cell
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.cells[y][x]
        return "#"

    def columns(self, first: int, stop: int) -> Plan:
        """The plan's columns from `first` up to `stop`, as a plan of their own.

        Marks and symbols move with their cells, and those outside the columns
        are left out. The outline is cut to the columns. A cut edge keeps its
        letter, so floor that runs across the cut runs off the new plan's
        frame.

        Args:
            first: The first column kept.
            stop: The column after the last one kept.

        Returns:
            The narrower plan.

        Raises:
            ValueError: If the plan has ornaments, which aren't cut.
        """
        if self.ornaments:
            raise ValueError("a plan with ornaments can't be cut into columns")
        for symbol in self.symbols:
            if len({first <= parse_cell(text)[0] < stop for text in symbol.cells}) > 1:
                raise ValueError(f"a {symbol.kind} symbol straddles the cut at column {first} or {stop}")

        def keep(text: str) -> bool:
            return first <= parse_cell(text)[0] < stop

        def move(text: str) -> str:
            x, y = parse_cell(text)
            return cell_key((x - first, y))

        def move_edge(text: str) -> str:
            cell, side = parse_edge(text)
            return edge_key((cell[0] - first, cell[1]), side)

        marks = tuple(
            mark.model_copy(
                update={
                    "cell": move(mark.cell),
                    "edge": None if mark.edge is None else move_edge(mark.edge),
                    "leader_to": None if mark.leader_to is None else move(mark.leader_to),
                }
            )
            for mark in self.marks
            if keep(mark.cell)
        )
        symbols = tuple(
            symbol.model_copy(
                update={
                    "cells": tuple(move(text) for text in symbol.cells),
                    "edge": None if symbol.edge is None else move_edge(symbol.edge),
                }
            )
            for symbol in self.symbols
            if (symbol.cells and keep(symbol.cells[0])) or (symbol.edge is not None and keep(symbol.edge.split(":")[0]))
        )

        def cut(rects: tuple[tuple[int, int, int, int], ...]) -> tuple[tuple[int, int, int, int], ...]:
            kept: list[tuple[int, int, int, int]] = []
            for x, y, w, h in rects:
                left, right = max(x, first), min(x + w, stop)
                if left < right:
                    kept.append((left - first, y, right - left, h))
            return tuple(kept)

        width = stop - first
        return Plan(
            width=width,
            height=self.height,
            cells=tuple(row[first:stop] for row in self.cells),
            edges=EdgeGrids(
                vertical=tuple(row[first : stop + 1] for row in self.edges.vertical),
                horizontal=tuple(row[first:stop] for row in self.edges.horizontal),
            ),
            marks=marks,
            symbols=symbols,
            rects=cut(self.rects),
            open_ground=cut(self.open_ground),
            seeds=tuple(move(text) for text in self.seeds if keep(text)),
            edge_styles={move_edge(key): value for key, value in self.edge_styles.items() if keep(key.split(":")[0])},
            stair_runs={move(key): value for key, value in self.stair_runs.items() if keep(key)},
        )


class Sketch:
    """A plan being authored: named floor, water, edges other than the default, marks, and symbols."""

    def __init__(self, width: int, height: int) -> None:
        """Start an empty sketch, all rock.

        Args:
            width: The grid's width in cells.
            height: The grid's height in cells.
        """
        self.width = width
        self.height = height
        self.space: dict[Cell, str] = {}
        self.overrides: dict[tuple[Cell, str], str] = {}
        self.marks: list[Mark] = []
        self.symbols: list[Symbol] = []
        self.rects: list[tuple[int, int, int, int]] = []
        self.open_ground: list[tuple[int, int, int, int]] = []
        self.seeds: list[str] = []
        self.edge_styles: dict[str, str] = {}
        self.stair_runs: dict[str, str] = {}
        self.ornaments: list[Ornament] = []

    def _put(self, cell: Cell, name: str) -> None:
        x, y = cell
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(f"cell {cell} is off the {self.width} by {self.height} grid")
        self.space[cell] = name

    def room(self, name: str, x: int, y: int, width: int, height: int) -> Sketch:
        """Make a rectangle of floor with one name.

        Args:
            name: The floor's name; floor of one name is open within itself.
            x: The west column.
            y: The north row.
            width: Columns.
            height: Rows.

        Returns:
            This sketch.
        """
        for cy in range(y, y + height):
            for cx in range(x, x + width):
                self._put((cx, cy), name)
        return self

    def run(self, name: str, *points: Cell) -> Sketch:
        """Make floor along straight steps from point to point, each step along a row or a column.

        Args:
            name: The floor's name.
            *points: The cells the run turns at, first to last.

        Returns:
            This sketch.

        Raises:
            ValueError: If two consecutive points aren't on one row or column.
        """
        for (x0, y0), (x1, y1) in itertools.pairwise(points):
            if x0 != x1 and y0 != y1:
                raise ValueError(f"a run steps along a row or a column, not from {(x0, y0)} to {(x1, y1)}")
            for cx in range(min(x0, x1), max(x0, x1) + 1):
                for cy in range(min(y0, y1), max(y0, y1) + 1):
                    self._put((cx, cy), name)
        if len(points) == 1:
            self._put(points[0], name)
        return self

    def water(self, x: int, y: int, width: int, height: int) -> Sketch:
        """Make a rectangle of water.

        Args:
            x: The west column.
            y: The north row.
            width: Columns.
            height: Rows.

        Returns:
            This sketch.
        """
        return self.room("~", x, y, width, height)

    def rock(self, *cells: Cell) -> Sketch:
        """Turn cells back to rock.

        Args:
            *cells: The cells.

        Returns:
            This sketch.
        """
        for cell in cells:
            self.space.pop(cell, None)
        return self

    def edge(self, cell: Cell, side: str, letter: str, style: str | None = None) -> Sketch:
        """Set one edge's letter.

        Args:
            cell: A cell beside the edge.
            side: The edge's side of that cell.
            letter: The edge letter.
            style: How the edge is drawn, when not by its letter's default.

        Returns:
            This sketch.
        """
        self.overrides[canonical(cell, side)] = letter
        if style is not None:
            named = canonical(cell, side)
            self.edge_styles[edge_key(named[0], named[1])] = style
        return self

    def edges(self, letter: str, *places: tuple[Cell, str], style: str | None = None) -> Sketch:
        """Set several edges to one letter.

        Args:
            letter: The edge letter.
            *places: Each edge as a cell beside it and its side.
            style: How the edges are drawn, when not by their letter's default.

        Returns:
            This sketch.
        """
        for cell, side in places:
            self.edge(cell, side, letter, style)
        return self

    def mark(self, text: str, cell: Cell, *, edge: str | None = None, leader_to: Cell | None = None) -> Sketch:
        """Print a label.

        Args:
            text: The label, with stacked labels joined by `/`.
            cell: The cell under the label's centre.
            edge: The door side of `cell` the label sits on, when it sits on a door.
            leader_to: The cell a leader line from the label ends on.

        Returns:
            This sketch.
        """
        self.marks.append(
            Mark(
                text=text,
                cell=cell_key(cell),
                edge=None if edge is None else edge_key(cell, edge),
                leader_to=None if leader_to is None else cell_key(leader_to),
            )
        )
        return self

    def symbol(
        self,
        kind: SymbolKind,
        *cells: Cell,
        edge: tuple[Cell, str] | None = None,
        direction: Literal["up", "down"] | None = None,
        run: str | None = None,
    ) -> Sketch:
        """Draw a map symbol.

        Args:
            kind: The symbol.
            *cells: The cells it covers; none for a cave mouth.
            edge: The edge a cave mouth stands on.
            direction: The way stairs, a ladder, a trapdoor, or a chute goes.
            run: For stairs or steps, `ns` or `ew`, the way the flight runs, when the cells don't show it.

        Returns:
            This sketch.
        """
        self.symbols.append(
            Symbol(
                kind=kind,
                cells=tuple(cell_key(cell) for cell in cells),
                edge=None if edge is None else edge_key(edge[0], edge[1]),
                direction=direction,
            )
        )
        if run is not None:
            self.stair_runs[cell_key(cells[0])] = run
        return self

    def outline(self, *rects: tuple[int, int, int, int], seeds: Iterable[Cell] = ()) -> Sketch:
        """Set the level's outline.

        Args:
            *rects: Rectangles `(x, y, width, height)` whose union is the outline.
            seeds: Cells well inside the level.

        Returns:
            This sketch.
        """
        self.rects.extend(rects)
        self.seeds.extend(cell_key(cell) for cell in seeds)
        return self

    def ground(self, *rects: tuple[int, int, int, int]) -> Sketch:
        """Add rectangles of open ground.

        Args:
            *rects: Rectangles `(x, y, width, height)`.

        Returns:
            This sketch.
        """
        self.open_ground.extend(rects)
        return self

    def ornament(self, kind: Decoration, *points: Point, size: float = 0.0) -> Sketch:
        """Draw an ornament the reading doesn't record.

        Args:
            kind: The ornament.
            *points: Its points in grid coordinates.
            size: A tree's or a circle's radius in cells.

        Returns:
            This sketch.
        """
        self.ornaments.append(Ornament(kind=kind, points=points, size=size))
        return self

    def _letter(self, cell: Cell, side: str) -> str:
        named = canonical(cell, side)
        if named in self.overrides:
            return self.overrides[named]
        here, there = self.space.get(cell), self.space.get(neighbour(cell, side))
        if here is None and there is None:
            return "-"
        if here is None or there is None:
            return "w"
        if here == "~" or there == "~" or here == there:
            return "o"
        return "w"

    def build(self) -> Plan:
        """The plan the sketch describes.

        Returns:
            The plan.

        Raises:
            ValueError: If an edge set by hand touches no floor or water.
        """
        for (cell, side), letter in self.overrides.items():
            if cell not in self.space and neighbour(cell, side) not in self.space and letter != "-":
                raise ValueError(f"edge {edge_key(cell, side)} touches no floor or water but is set to {letter!r}")
        cells = tuple(
            "".join(
                "#" if (x, y) not in self.space else "~" if self.space[(x, y)] == "~" else "."
                for x in range(self.width)
            )
            for y in range(self.height)
        )
        vertical = tuple(
            "".join(self._letter((x, y), "west") for x in range(self.width)) + self._letter((self.width - 1, y), "east")
            for y in range(self.height)
        )
        horizontal = (
            *tuple("".join(self._letter((x, y), "north") for x in range(self.width)) for y in range(self.height)),
            "".join(self._letter((x, self.height - 1), "south") for x in range(self.width)),
        )
        return Plan(
            width=self.width,
            height=self.height,
            cells=cells,
            edges=EdgeGrids(vertical=vertical, horizontal=horizontal),
            marks=tuple(self.marks),
            symbols=tuple(self.symbols),
            rects=tuple(self.rects),
            open_ground=tuple(self.open_ground),
            seeds=tuple(self.seeds),
            edge_styles=dict(self.edge_styles),
            stair_runs=dict(self.stair_runs),
            ornaments=tuple(self.ornaments),
        )
