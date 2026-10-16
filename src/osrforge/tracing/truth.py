"""The geometry truth format: what a map shows, and the levels and areas made from it.

One document describes one adventure module's maps. It has two layers. The
*reading* layer is what the map shows: each printed `Region` with its
registration, and each `Panel` with its cells, edges, key marks, outlines, and
symbols. The *resolution* layer is what the rules in
`docs/tracing-conventions.md` make from that reading: each `Level` with its
grid, areas, placed keys, transitions, and passages, and each `Dungeon` with its
entrance. An answer key and a synthetic map's answer fill both layers. A trace
fills both too, and may add `repairs` and `before_repair`.

Cells are addressed as `"x,y"`, with `x` the column from the west edge and `y`
the row from the north edge of the grid, both 0-based, the same addresses the
overrides contract uses. An edge is `"x,y:side"`, the named side of that cell.
Cell and edge readings are letter grids: one string per row, one letter per
cell or edge, so a 50 by 50 level is readable in a text editor and small on
disk.

Examples:
    ```python
    from osrforge.tracing.truth import EdgeGrids, parse_cell

    grids = EdgeGrids(vertical=("wow",), horizontal=("ww", "ww"))
    assert grids.at((0, 0), "east") == "o"
    assert parse_cell("3,4") == (3, 4)
    ```
"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CELL_LETTERS",
    "EDGE_LETTERS",
    "SYMBOL_KINDS",
    "Area",
    "Compass",
    "DamagedEdge",
    "Dungeon",
    "EdgeGrids",
    "EdgeLetter",
    "Entrance",
    "GeometryTruth",
    "HalfPair",
    "Legend",
    "Level",
    "LevelPanel",
    "Mark",
    "Outline",
    "Page",
    "PageImage",
    "PageSide",
    "Panel",
    "PanelRegion",
    "Passage",
    "PlacedKey",
    "Region",
    "Registration",
    "Repair",
    "Rotation",
    "Scale",
    "Side",
    "Style",
    "Symbol",
    "SymbolKind",
    "Transition",
    "cell_key",
    "edge_key",
    "load_geometry_truth",
    "order_key",
    "parse_cell",
    "parse_edge",
    "rotate_cell",
    "rotate_side",
    "rotated_size",
    "rotation_for",
    "save_geometry_truth",
]

Side = Literal["north", "east", "south", "west"]
"""A side of a cell, named for the grid's own directions: north is the top row of the grid."""

PageSide = Literal["top", "right", "bottom", "left"]
"""A side of the printed page, for where a compass points and where an image edge cuts a region off."""

Style = Literal[
    "tsr_blue", "black_white", "dark_rock", "cave", "hex", "isometric", "side_view", "outdoor", "town", "no_grid"
]
"""A map's drawing style.

The first four are plan views the stage traces. The rest are styles it refuses
(`hex`, `isometric`, `side_view`, `outdoor`, `town`) or can't register
(`no_grid`).
"""

EdgeLetter = Literal["o", "p", "w", "n", "d", "l", "s", "-"]
"""One edge reading: open, printed open line, wall, window, door, locked door, secret door, or no reading.

`-` stands where neither side of the edge is floor or water. Every other
position has a letter. Open and printed open line are the two passable
readings.
"""

CELL_LETTERS = ".#~"
"""The cell letters: `.` floor, `#` rock, `~` water."""

EDGE_LETTERS = "opwndls-"
"""The edge letters, in the order `EdgeLetter` lists them."""

PASSABLE_LETTERS = "op"
"""The edge letters a party can walk across."""

DOOR_LETTERS = "dls"
"""The edge letters that are doors: plain, locked, and secret."""

SymbolKind = Literal[
    "stairs",
    "ladder",
    "trapdoor",
    "hinged_floor",
    "chute",
    "slide",
    "pit",
    "cave_mouth",
    "pillar",
    "statue",
    "pool",
    "steps",
    "bridge",
    "footing",
    "locked_door",
]
"""The symbol vocabulary: what a map symbol or a legend entry can be.

`cave_mouth` stands on an edge. `footing` marks steps, footings, or bars drawn
on one bank of water. `locked_door` is a legend entry only: a locked door on
the map is the edge letter `l`.
"""

SYMBOL_KINDS: tuple[str, ...] = get_args(SymbolKind)
"""The `SymbolKind` values, in vocabulary order."""

_SIDES: tuple[str, ...] = get_args(Side)
_OPPOSITE: dict[str, str] = {"north": "south", "south": "north", "east": "west", "west": "east"}


def parse_cell(text: str) -> tuple[int, int]:
    """Parse a cell address.

    Args:
        text: The address, `"x,y"`.

    Returns:
        The `(x, y)` pair.

    Raises:
        ValueError: If the text isn't two non-negative integers joined by a comma.
    """
    parts = text.split(",")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"a cell address is 'x,y' with non-negative integers, not {text!r}")
    return int(parts[0]), int(parts[1])


def cell_key(cell: tuple[int, int]) -> str:
    """Format a cell address.

    Args:
        cell: The `(x, y)` pair.

    Returns:
        The address, `"x,y"`.
    """
    return f"{cell[0]},{cell[1]}"


def parse_edge(text: str) -> tuple[tuple[int, int], str]:
    """Parse an edge address.

    Args:
        text: The address, `"x,y:side"`.

    Returns:
        The cell and the side.

    Raises:
        ValueError: If the text isn't a cell address, a colon, and a side name.
    """
    cell_text, separator, side = text.partition(":")
    if not separator or side not in _SIDES:
        raise ValueError(f"an edge address is 'x,y:side' with a side of north, east, south, or west, not {text!r}")
    return parse_cell(cell_text), side


def edge_key(cell: tuple[int, int], side: str) -> str:
    """Format an edge address.

    Args:
        cell: The `(x, y)` pair.
        side: The side of that cell.

    Returns:
        The address, `"x,y:side"`.
    """
    return f"{cell_key(cell)}:{side}"


def order_key(point: tuple[float, float], north: PageSide = "top") -> tuple[float, float]:
    """The sort key that puts points in north-then-west order.

    The item farther north comes first, and of two equally far north, the one
    farther west. On a level's grid north is the top, so the key is `(y, x)`.
    For a point on a panel still in page orientation, `north` is where the
    panel's compass points on the page, and the key gives the order the point
    has once the panel is rotated onto the level: with north at the left edge
    of the page, west is the bottom.

    Args:
        point: A grid point, such as a cell's centre `(x + 0.5, y + 0.5)` or an
            edge's midpoint.
        north: The page side the panel's north points to, `top` on a level grid.

    Returns:
        A pair that sorts ascending into north-then-west order.
    """
    x, y = point
    if north == "top":
        return (y, x)
    if north == "left":
        return (x, -y)
    if north == "bottom":
        return (-y, -x)
    return (-x, y)


Rotation = Literal[0, 90, 180, 270]
"""A quarter turn clockwise, in degrees."""

_ROTATION_FOR: dict[str, int] = {"top": 0, "right": 270, "bottom": 180, "left": 90}
_SIDE_ORDER: tuple[str, ...] = ("north", "east", "south", "west")


def rotation_for(north: PageSide) -> Rotation:
    """The clockwise quarter turn that puts a page side at the top.

    Args:
        north: The page side a panel's north points to.

    Returns:
        0 for `top`, 90 for `left`, 180 for `bottom`, 270 for `right`.
    """
    return _ROTATION_FOR[north]  # type: ignore[return-value]


def rotate_cell(cell: tuple[int, int], size: tuple[int, int], rotation: int) -> tuple[int, int]:
    """Where a panel cell lands after the panel turns clockwise.

    Args:
        cell: The cell `(x, y)` in page orientation.
        size: The panel's `(width, height)` in page orientation.
        rotation: The quarter turn in degrees.

    Returns:
        The cell on the turned panel, whose own cell `(0, 0)` is its north-west corner.
    """
    x, y = cell
    width, height = size
    if rotation == 0:
        return (x, y)
    if rotation == 90:
        return (height - 1 - y, x)
    if rotation == 180:
        return (width - 1 - x, height - 1 - y)
    return (y, width - 1 - x)


def rotate_side(side: str, rotation: int) -> str:
    """Which side of a cell a side becomes after a clockwise quarter turn.

    Args:
        side: A side name.
        rotation: The quarter turn in degrees.

    Returns:
        The side name on the turned panel: north becomes east under 90.
    """
    return _SIDE_ORDER[(_SIDE_ORDER.index(side) + rotation // 90) % 4]


def rotated_size(size: tuple[int, int], rotation: int) -> tuple[int, int]:
    """A panel's `(width, height)` after a clockwise quarter turn.

    Args:
        size: The size in page orientation.
        rotation: The quarter turn in degrees.

    Returns:
        The size, swapped under 90 and 270.
    """
    return size if rotation in (0, 180) else (size[1], size[0])


def _check_rows(rows: tuple[str, ...], letters: str, what: str) -> None:
    widths = {len(row) for row in rows}
    if len(widths) > 1:
        raise ValueError(f"{what} rows differ in length: {sorted(widths)}")
    for row in rows:
        bad = {letter for letter in row if letter not in letters}
        if bad:
            raise ValueError(f"{what} has letters outside {letters!r}: {sorted(bad)}")


class EdgeGrids(BaseModel):
    """The edge readings of a grid, as two letter grids.

    `vertical` has one row per grid row and one letter per grid line that runs
    north to south, so each row is one letter longer than the grid is wide:
    letter `j` of row `y` is the west side of cell `(j, y)`, and the last letter
    is the east frame. `horizontal` has one row per grid line that runs east to
    west, so it has one more row than the grid: letter `x` of row `i` is the
    north side of cell `(x, i)`, and the last row is the south frame.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    vertical: tuple[str, ...]
    """One row per grid row, `width + 1` letters each."""

    horizontal: tuple[str, ...]
    """`height + 1` rows, `width` letters each."""

    @model_validator(mode="after")
    def _shapes_agree(self) -> EdgeGrids:
        _check_rows(self.vertical, EDGE_LETTERS, "vertical edges")
        _check_rows(self.horizontal, EDGE_LETTERS, "horizontal edges")
        if not self.vertical or not self.horizontal:
            raise ValueError("edge grids need at least one row each")
        width = len(self.horizontal[0])
        height = len(self.vertical)
        if len(self.vertical[0]) != width + 1:
            raise ValueError(
                f"vertical rows have {len(self.vertical[0])} letters, but the grid is {width} wide, "
                f"so they need {width + 1}"
            )
        if len(self.horizontal) != height + 1:
            raise ValueError(
                f"horizontal has {len(self.horizontal)} rows, but the grid is {height} high, so it needs {height + 1}"
            )
        return self

    @property
    def width(self) -> int:
        """The grid's width in cells."""
        return len(self.horizontal[0])

    @property
    def height(self) -> int:
        """The grid's height in cells."""
        return len(self.vertical)

    def at(self, cell: tuple[int, int], side: str) -> str:
        """The letter on one side of a cell.

        Args:
            cell: The `(x, y)` pair.
            side: Which side.

        Returns:
            The edge letter.
        """
        x, y = cell
        if side == "west":
            return self.vertical[y][x]
        if side == "east":
            return self.vertical[y][x + 1]
        if side == "north":
            return self.horizontal[y][x]
        return self.horizontal[y + 1][x]

    def with_letter(self, cell: tuple[int, int], side: str, letter: str) -> EdgeGrids:
        """A copy with one edge changed.

        Args:
            cell: The `(x, y)` pair.
            side: Which side of it.
            letter: The new edge letter.

        Returns:
            The new grids. This one is unchanged.
        """
        x, y = cell
        vertical = list(self.vertical)
        horizontal = list(self.horizontal)
        if side in ("west", "east"):
            column = x if side == "west" else x + 1
            row = vertical[y]
            vertical[y] = row[:column] + letter + row[column + 1 :]
        else:
            index = y if side == "north" else y + 1
            row = horizontal[index]
            horizontal[index] = row[:x] + letter + row[x + 1 :]
        return EdgeGrids(vertical=tuple(vertical), horizontal=tuple(horizontal))

    def positions(self) -> Iterable[tuple[tuple[int, int], str]]:
        """Every edge once, as the cell and side that name it.

        Each interior edge is named from its west or north cell. The east and
        south frame edges are named from the cell inside them.

        Yields:
            `(cell, side)` pairs in row-major order.
        """
        for y in range(self.height):
            for x in range(self.width):
                yield (x, y), "north"
                yield (x, y), "west"
                if x == self.width - 1:
                    yield (x, y), "east"
                if y == self.height - 1:
                    yield (x, y), "south"


def _neighbour(cell: tuple[int, int], side: str) -> tuple[int, int]:
    x, y = cell
    return {"north": (x, y - 1), "south": (x, y + 1), "west": (x - 1, y), "east": (x + 1, y)}[side]


def _check_grid(cells: tuple[str, ...], edges: EdgeGrids, what: str) -> None:
    _check_rows(cells, CELL_LETTERS, f"{what} cells")
    if not cells:
        raise ValueError(f"{what} needs at least one row of cells")
    width, height = len(cells[0]), len(cells)
    if edges.width != width or edges.height != height:
        raise ValueError(f"{what} edges are {edges.width} by {edges.height}, but the cells are {width} by {height}")

    def occupied(cell: tuple[int, int]) -> bool:
        x, y = cell
        return 0 <= x < width and 0 <= y < height and cells[y][x] != "#"

    for cell, side in edges.positions():
        letter = edges.at(cell, side)
        read = occupied(cell) or occupied(_neighbour(cell, side))
        if read and letter == "-":
            raise ValueError(f"{what} edge {edge_key(cell, side)} touches floor or water and must have a reading")
        if not read and letter != "-":
            raise ValueError(f"{what} edge {edge_key(cell, side)} touches no floor or water and must be '-'")


def _check_cells_in(cells: Iterable[str], grid: tuple[str, ...], what: str, letters: str = ".~") -> None:
    for text in cells:
        x, y = parse_cell(text)
        if y >= len(grid) or x >= len(grid[0]):
            raise ValueError(f"{what} names cell {text}, which is off the grid")
        if grid[y][x] not in letters:
            raise ValueError(f"{what} names cell {text}, which is {grid[y][x]!r}, not one of {letters!r}")


class PageImage(BaseModel):
    """One image embedded on a page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    """The image file, relative to the document's directory. The same file on two pages is one image printed twice."""

    hidden: bool = False
    """True when a later image on the page covers most of this one, so the page doesn't show it."""


class Page(BaseModel):
    """One page of the module, as the tracing stage's first step sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    """The 1-based page number."""

    images: tuple[PageImage, ...] = ()
    """The embedded images, in drawing order."""

    blanked: bool = False
    """True when the `blank_page_renders` setting blanks this page."""

    spread_with: int | None = None
    """The other page of a two-page spread that one image spans."""


class Registration(BaseModel):
    """Where each grid corner of a region lies on its image.

    `corners` has `rows + 1` rows of `columns + 1` points, each the pixel
    position `[x, y]` of a grid corner, so corner `[j][i]` is the north-west
    corner of cell `(i, j)`. Points record the fit exactly whatever the fit's
    model is: pitch, shear, and drift are all in the corners.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    corners: tuple[tuple[tuple[float, float], ...], ...]
    """The grid corners in pixels, row by row from the north-west."""

    @model_validator(mode="after")
    def _rectangular(self) -> Registration:
        if len(self.corners) < 2 or any(len(row) < 2 for row in self.corners):
            raise ValueError("a registration needs at least two rows of two corners")
        widths = {len(row) for row in self.corners}
        if len(widths) != 1:
            raise ValueError(f"registration rows differ in length: {sorted(widths)}")
        return self

    @property
    def columns(self) -> int:
        """The registered grid's width in cells."""
        return len(self.corners[0]) - 1

    @property
    def rows(self) -> int:
        """The registered grid's height in cells."""
        return len(self.corners) - 1

    def pixel(self, point: tuple[float, float]) -> tuple[float, float]:
        """The image position of a grid point.

        Args:
            point: Grid coordinates `(gx, gy)`, where cell `(i, j)` spans
                `gx` in `[i, i + 1]` and `gy` in `[j, j + 1]`. Points outside the
                grid extend the nearest cell.

        Returns:
            The pixel position, interpolated within the corner cell.
        """
        gx, gy = point
        i = min(max(int(gx // 1), 0), self.columns - 1)
        j = min(max(int(gy // 1), 0), self.rows - 1)
        u, v = gx - i, gy - j
        (x00, y00), (x10, y10) = self.corners[j][i], self.corners[j][i + 1]
        (x01, y01), (x11, y11) = self.corners[j + 1][i], self.corners[j + 1][i + 1]
        x = (1 - u) * (1 - v) * x00 + u * (1 - v) * x10 + (1 - u) * v * x01 + u * v * x11
        y = (1 - u) * (1 - v) * y00 + u * (1 - v) * y10 + (1 - u) * v * y01 + u * v * y11
        return x, y


class Scale(BaseModel):
    """A region's printed scale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feet: int | None
    """Feet per square: 5 or 10 for a scale the stage accepts, `None` for any other."""

    printed: bool
    """True when the map prints its scale. When false, `feet` is the default, 10, and the report flags it."""

    text: str | None = None
    """The printed scale statement, when there is one."""


class Compass(BaseModel):
    """Where north is on the page.

    The level grid takes the map's north: panel placement turns each panel by
    the quarter turn that puts `north` at the top. A printed compass that isn't
    on a quarter turn records its `angle`, and `north` is the nearest quarter
    turn, which the report flags.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    north: PageSide
    """The page side the map's north points to, to the nearest quarter turn. `top` when the map prints no compass."""

    printed: bool
    """True when the map prints a compass. When false, the report flags the default."""

    angle: float | None = None
    """The printed compass's direction in degrees clockwise from the top of the page, when it isn't on a quarter turn:
    0 is the top, 90 the right edge. `None` when the compass points along a page side or the map prints none."""

    @model_validator(mode="after")
    def _north_is_the_nearest_quarter_turn(self) -> Compass:
        if self.angle is None:
            return self
        if not self.printed:
            raise ValueError("a compass angle belongs to a printed compass")
        if not 0 <= self.angle < 360:
            raise ValueError(f"a compass angle is at least 0 and under 360 degrees, not {self.angle}")
        turns, remainder = divmod(self.angle, 90)
        index = int(turns) + (1 if remainder > 45 else 0)
        nearest = ("top", "right", "bottom", "left")[index % 4]
        if self.north != nearest:
            raise ValueError(f"the nearest quarter turn to {self.angle} degrees is {nearest}, not {self.north}")
        return self


class Legend(BaseModel):
    """The legend in force for a region."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printed: bool
    """True when the page prints its own legend."""

    from_page: int | None = None
    """The page whose legend this region uses when it prints none."""

    symbols: tuple[SymbolKind, ...] = ()
    """The vocabulary entries the legend in force explains."""


class Region(BaseModel):
    """One framed drawing on a map image, with its registration and its verdicts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """A name unique within the document."""

    page: int = Field(ge=1)
    """The page the region is on."""

    image: str
    """The image file the region is cut from."""

    style: Style
    """The drawing style."""

    columns: int = Field(ge=0)
    """The region's width in squares of its own scale, 0 when it has no grid."""

    rows: int = Field(ge=0)
    """The region's height in squares of its own scale, 0 when it has no grid."""

    scale: Scale
    compass: Compass
    legend: Legend

    cut_off: tuple[PageSide, ...] = ()
    """The image edges that cut the drawing off."""

    verdict: Literal["registered", "unregistered", "refused"]
    """Whether the grid was fitted, the fit failed, or the style or scale is refused."""

    reason: str | None = None
    """Why the region is unregistered or refused, or `None` when registered."""

    registration: Registration | None = None
    """The fitted grid, present exactly when the verdict is `registered`."""

    @model_validator(mode="after")
    def _verdict_fields_agree(self) -> Region:
        registered = self.verdict == "registered"
        if registered != (self.registration is not None):
            raise ValueError(f"region {self.id!r}: a registration is present exactly when the verdict is registered")
        if registered == (self.reason is not None):
            raise ValueError(f"region {self.id!r}: a reason is present exactly when the verdict isn't registered")
        if self.registration is not None and (self.registration.columns, self.registration.rows) != (
            self.columns,
            self.rows,
        ):
            raise ValueError(f"region {self.id!r}: the registration's grid must be {self.columns} by {self.rows}")
        return self


class PanelRegion(BaseModel):
    """Where a region's grid sits on its panel's grid."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    region: str
    """The region id."""

    origin: tuple[int, int]
    """The panel cell where the region's cell `(0, 0)` sits."""


class Mark(BaseModel):
    """A label-sized mark printed on the map: a key, a letter, or a label."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    """The mark's text as printed. Marks stacked one above the other are one mark."""

    cell: str
    """The cell under the mark's centre."""

    leader_to: str | None = None
    """The cell a leader line from the mark ends on."""


class Outline(BaseModel):
    """One level's outline on a panel, from the whole-map reading."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dungeon: str
    level: int = Field(ge=1)

    rects: tuple[tuple[int, int, int, int], ...] = Field(min_length=1)
    """Rectangles `[x, y, width, height]` in panel cells. Their union is the outline."""

    seeds: tuple[str, ...] = Field(min_length=1)
    """Cells well inside the level, where the partition starts."""

    open_ground: tuple[tuple[int, int, int, int], ...] = ()
    """Rectangles of open ground inside the outline, like yards and courtyards."""


class Symbol(BaseModel):
    """One map symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SymbolKind
    cells: tuple[str, ...] = ()
    """The cells the symbol covers. A cave mouth has none."""

    edge: str | None = None
    """The edge a cave mouth stands on."""

    direction: Literal["up", "down"] | None = None
    """The way the map shows stairs, a ladder, a trapdoor, or a chute going, or `None` when it shows none."""

    @model_validator(mode="after")
    def _placed(self) -> Symbol:
        if self.kind == "cave_mouth":
            if self.edge is None or self.cells:
                raise ValueError("a cave mouth stands on an edge and covers no cells")
            parse_edge(self.edge)
        elif not self.cells or self.edge is not None:
            raise ValueError(f"a {self.kind} covers cells and stands on no edge")
        return self


class Panel(BaseModel):
    """The reading of one panel: a registered region, or two halves joined into one.

    The grid is in the panel's own squares: 5-foot squares on a 5-foot map
    until [`convert_five_foot`][osrforge.tracing.fivefoot.convert_five_foot]
    makes 10-foot cells of them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    regions: tuple[PanelRegion, ...] = Field(min_length=1)
    """The region, or the two joined halves, with their origins on this grid."""

    scale_feet: Literal[5, 10]
    """Feet per square of this grid."""

    cells: tuple[str, ...]
    """One string per row, one cell letter per column."""

    edges: EdgeGrids
    partial: tuple[str, ...] = ()
    """Cells that are partly floor: between a quarter and three quarters, or on a diagonal."""

    cave: tuple[str, ...] = ()
    """Cells whose walls are drawn off the grid. An edge touching one is a cave edge."""

    marks: tuple[Mark, ...] = ()
    outlines: tuple[Outline, ...] = ()
    symbols: tuple[Symbol, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Panel:
        what = f"panel {self.id!r}"
        _check_grid(self.cells, self.edges, what)
        _check_cells_in(self.partial, self.cells, f"{what} partial", CELL_LETTERS)
        _check_cells_in(self.cave, self.cells, f"{what} cave", CELL_LETTERS)
        for mark in self.marks:
            _check_cells_in([mark.cell], self.cells, f"{what} mark {mark.text!r}", CELL_LETTERS)
            if mark.leader_to is not None:
                _check_cells_in([mark.leader_to], self.cells, f"{what} mark {mark.text!r} leader", CELL_LETTERS)
        for symbol in self.symbols:
            _check_cells_in(symbol.cells, self.cells, f"{what} {symbol.kind}", CELL_LETTERS)
            if symbol.edge is not None:
                cell, _side = parse_edge(symbol.edge)
                _check_cells_in([cell_key(cell)], self.cells, f"{what} {symbol.kind}", CELL_LETTERS)
        for outline in self.outlines:
            _check_cells_in(outline.seeds, self.cells, f"{what} outline seeds", CELL_LETTERS)
        return self

    @property
    def width(self) -> int:
        """The grid's width in squares."""
        return len(self.cells[0])

    @property
    def height(self) -> int:
        """The grid's height in squares."""
        return len(self.cells)


class HalfPair(BaseModel):
    """Two regions that are halves of one map, and how the stage handled them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    regions: tuple[str, str]
    """The two region ids, in page order."""

    joined: bool
    """True when the halves became one panel, false when they stayed apart as two panels linked by passages."""

    rows: int
    """The row shift: the second half's row 0 lies this many rows south of the first half's row 0."""

    columns: int | None = None
    """The whole-cell column offset of the second half from the first, when joined."""

    @model_validator(mode="after")
    def _offset_when_joined(self) -> HalfPair:
        if self.joined != (self.columns is not None):
            raise ValueError("a column offset is present exactly when the halves are joined")
        return self


class Area(BaseModel):
    """A keyed area of a level."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cells: tuple[str, ...] = Field(min_length=1)
    synthesized: bool = False
    """True when the key's area was made on rock because the map prints no room for it."""


class PlacedKey(BaseModel):
    """A printed key or letter after placement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    """The text as printed."""

    cell: str
    """The cell the key stands on after any move."""

    area: str
    """The area the label belongs to, by key."""

    moved: bool = False
    """True when the rules moved the key off the cell it's printed on."""


class Transition(BaseModel):
    """Stairs, a trapdoor, or a chute on a cell, and where it leads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell: str
    kind: Literal["stairs_up", "stairs_down", "trapdoor", "chute"]
    to_dungeon: str | None = None
    """The destination dungeon when it isn't this one."""

    to_level: int = Field(ge=1)
    to_cell: str


class Passage(BaseModel):
    """A connection the party walks through: across one side of a cell to a cell elsewhere."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell: str
    side: Side
    """The side the party crosses. It's a wall in the level's edges."""

    to_level: int = Field(ge=1)
    """The destination level of the same dungeon, or this level when the passage links two panels."""

    to_cell: str


class LevelPanel(BaseModel):
    """Where a panel sits on a level's grid, and how it was turned to put its north at the top."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    panel: str
    origin: tuple[int, int]
    """The level cell where the turned panel's north-west cell sits. Panel cell `c` lands on
    `origin + rotate_cell(c, (panel.width, panel.height), rotation)`."""

    rotation: Rotation = 0
    """The clockwise quarter turn that put the panel's north at the top: `rotation_for` of its first region's
    compass."""


class Level(BaseModel):
    """One level after resolution: its grid of 10-foot cells, its areas, and what stands on them.

    A level's cells are floor only where the level owns them. Another level's
    floor and the outside are rock here. The grid has one cell of rock around
    the floor on every side, and the map's north is its top, so osrlib's north
    is the map's north.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    verdict: Literal["traced", "fallback"]
    """Whether the level was traced from its map or fell back to synthesis."""

    reason: str | None = None
    """Why the level fell back, or `None` when traced."""

    panels: tuple[LevelPanel, ...] = ()
    cells: tuple[str, ...] = ()
    edges: EdgeGrids | None = None
    areas: dict[str, Area] = {}
    """Areas by key, the survey's canonical key."""

    keys: tuple[PlacedKey, ...] = ()
    transitions: tuple[Transition, ...] = ()
    passages: tuple[Passage, ...] = ()
    flags: tuple[str, ...] = ()
    """What the report flags on this level, one entry each."""

    @model_validator(mode="after")
    def _consistent(self) -> Level:
        what = f"level {self.number}"
        traced = self.verdict == "traced"
        if traced == (self.reason is not None):
            raise ValueError(f"{what}: a reason is present exactly when the level fell back")
        if not traced:
            if self.panels or self.cells or self.edges or self.areas or self.keys or self.transitions or self.passages:
                raise ValueError(f"{what}: a level that fell back has no grid, areas, keys, transitions, or passages")
            return self
        if not self.panels or self.edges is None:
            raise ValueError(f"{what}: a traced level has panels, cells, and edges")
        _check_grid(self.cells, self.edges, what)
        for key, area in self.areas.items():
            _check_cells_in(area.cells, self.cells, f"{what} area {key!r}")
        for placed in self.keys:
            if placed.area not in self.areas:
                raise ValueError(f"{what}: key {placed.label!r} names area {placed.area!r}, which the level lacks")
            _check_cells_in([placed.cell], self.cells, f"{what} key {placed.label!r}")
        for transition in self.transitions:
            _check_cells_in([transition.cell], self.cells, f"{what} {transition.kind}")
        for passage in self.passages:
            _check_cells_in([passage.cell], self.cells, f"{what} passage")
            if self.edges.at(parse_cell(passage.cell), passage.side) != "w":
                raise ValueError(
                    f"{what}: the side of passage {edge_key(parse_cell(passage.cell), passage.side)} isn't a wall"
                )
        return self

    @property
    def width(self) -> int:
        """The grid's width in cells, 0 for a level that fell back."""
        return len(self.cells[0]) if self.cells else 0

    @property
    def height(self) -> int:
        """The grid's height in cells, 0 for a level that fell back."""
        return len(self.cells)


class Entrance(BaseModel):
    """Where the party arrives from town."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: int = Field(ge=1)
    cell: str


class Dungeon(BaseModel):
    """One dungeon: its levels and its entrance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """The survey's canonical dungeon id."""

    name: str
    entrance: Entrance | None = None
    """The entrance, or `None` when every level fell back."""

    levels: tuple[Level, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Dungeon:
        numbers = [level.number for level in self.levels]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"dungeon {self.id!r}: level numbers repeat: {numbers}")
        by_number = {level.number: level for level in self.levels}
        if self.entrance is not None:
            level = by_number.get(self.entrance.level)
            if level is None or level.verdict != "traced":
                raise ValueError(
                    f"dungeon {self.id!r}: the entrance names level {self.entrance.level}, which isn't traced"
                )
            _check_cells_in([self.entrance.cell], level.cells, f"dungeon {self.id!r} entrance", ".")
        elif any(level.verdict == "traced" for level in self.levels):
            raise ValueError(f"dungeon {self.id!r}: a dungeon with a traced level has an entrance")
        for level in self.levels:
            for passage in level.passages:
                target = by_number.get(passage.to_level)
                if target is None or target.verdict != "traced":
                    raise ValueError(f"dungeon {self.id!r}: a passage on level {level.number} leads to no traced level")
                _check_cells_in([passage.to_cell], target.cells, f"dungeon {self.id!r} passage destination")
            for transition in level.transitions:
                if transition.to_dungeon is not None:
                    continue
                target = by_number.get(transition.to_level)
                if target is None:
                    raise ValueError(
                        f"dungeon {self.id!r}: a {transition.kind} on level {level.number} leads to no level"
                    )
                if target.verdict == "traced":
                    _check_cells_in([transition.to_cell], target.cells, f"dungeon {self.id!r} transition destination")
        return self


class DamagedEdge(BaseModel):
    """One edge a repair changed, with its readings before and after."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    edge: str
    before: EdgeLetter
    after: EdgeLetter


class Repair(BaseModel):
    """One repair the checks made on a level."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dungeon: str
    level: int = Field(ge=1)
    fault: Literal["area_unreachable", "floor_cut_off", "door_without_floor"]
    edges: tuple[DamagedEdge, ...] = ()
    cells: tuple[str, ...] = ()
    """Cells the repair changed: floor made rock, or rock made floor."""

    cost: float = Field(ge=0.0)


class GeometryTruth(BaseModel):
    """One module's maps and the levels made from them.

    An answer key or a synthetic answer has no `repairs` and no
    `before_repair`. A trace has both when the checks repaired anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    pages: tuple[Page, ...] = ()
    regions: tuple[Region, ...] = ()
    panels: tuple[Panel, ...] = ()
    half_pairs: tuple[HalfPair, ...] = ()
    dungeons: tuple[Dungeon, ...] = Field(min_length=1)
    repairs: tuple[Repair, ...] = ()
    before_repair: GeometryTruth | None = None
    """The same document as the rules left it before any repair. A trace fills it when it made repairs."""

    @model_validator(mode="after")
    def _references_resolve(self) -> GeometryTruth:
        region_ids = [region.id for region in self.regions]
        if len(set(region_ids)) != len(region_ids):
            raise ValueError(f"region ids repeat: {region_ids}")
        panel_ids = [panel.id for panel in self.panels]
        if len(set(panel_ids)) != len(panel_ids):
            raise ValueError(f"panel ids repeat: {panel_ids}")
        dungeon_ids = [dungeon.id for dungeon in self.dungeons]
        if len(set(dungeon_ids)) != len(dungeon_ids):
            raise ValueError(f"dungeon ids repeat: {dungeon_ids}")
        regions = set(region_ids)
        levels = {(dungeon.id, level.number) for dungeon in self.dungeons for level in dungeon.levels}
        for panel in self.panels:
            for part in panel.regions:
                if part.region not in regions:
                    raise ValueError(f"panel {panel.id!r} names region {part.region!r}, which the document lacks")
            for outline in panel.outlines:
                if (outline.dungeon, outline.level) not in levels:
                    raise ValueError(
                        f"panel {panel.id!r} outlines level {outline.dungeon}/{outline.level}, which the document lacks"
                    )
        for pair in self.half_pairs:
            for region in pair.regions:
                if region not in regions:
                    raise ValueError(f"a half pair names region {region!r}, which the document lacks")
        regions_by_id = {region.id: region for region in self.regions}
        panels_by_id = {panel.id: panel for panel in self.panels}
        for dungeon in self.dungeons:
            for level in dungeon.levels:
                for part in level.panels:
                    panel = panels_by_id.get(part.panel)
                    if panel is None:
                        raise ValueError(
                            f"level {dungeon.id}/{level.number} names panel {part.panel!r}, which the document lacks"
                        )
                    north = regions_by_id[panel.regions[0].region].compass.north
                    if part.rotation != rotation_for(north):
                        raise ValueError(
                            f"level {dungeon.id}/{level.number} places panel {part.panel!r} turned {part.rotation}, "
                            f"but its north is {north}, which needs {rotation_for(north)}"
                        )
                for transition in level.transitions:
                    if transition.to_dungeon is not None and transition.to_dungeon not in dungeon_ids:
                        raise ValueError(
                            f"a transition on {dungeon.id}/{level.number} leads to dungeon "
                            f"{transition.to_dungeon!r}, which the document lacks"
                        )
        if self.before_repair is not None and (self.before_repair.repairs or self.before_repair.before_repair):
            raise ValueError("the document before repair has no repairs of its own")
        return self

    def region(self, region_id: str) -> Region:
        """The region with an id.

        Args:
            region_id: The id.

        Returns:
            The region.

        Raises:
            KeyError: If no region has that id.
        """
        for region in self.regions:
            if region.id == region_id:
                return region
        raise KeyError(region_id)

    def panel(self, panel_id: str) -> Panel:
        """The panel with an id.

        Args:
            panel_id: The id.

        Returns:
            The panel.

        Raises:
            KeyError: If no panel has that id.
        """
        for panel in self.panels:
            if panel.id == panel_id:
                return panel
        raise KeyError(panel_id)

    def level(self, dungeon_id: str, number: int) -> Level:
        """A level by dungeon id and number.

        Args:
            dungeon_id: The dungeon's id.
            number: The level number.

        Returns:
            The level.

        Raises:
            KeyError: If the document has no such level.
        """
        for dungeon in self.dungeons:
            if dungeon.id == dungeon_id:
                for level in dungeon.levels:
                    if level.number == number:
                        return level
        raise KeyError((dungeon_id, number))


def load_geometry_truth(path: Path) -> GeometryTruth:
    """Load and validate a geometry truth document.

    Args:
        path: The JSON file.

    Returns:
        The document. An unknown key or a reference to a missing region, panel,
        level, or cell fails loudly.
    """
    return GeometryTruth.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_geometry_truth(path: Path, truth: GeometryTruth) -> None:
    """Write a geometry truth document as sorted, indented JSON with a trailing newline.

    The same document always writes the same bytes, so committed answers can be
    compared byte for byte.

    Args:
        path: The JSON file to write.
        truth: The document.
    """
    data = truth.model_dump(mode="json", exclude_none=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
