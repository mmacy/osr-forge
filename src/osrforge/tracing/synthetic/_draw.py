"""How the synthetic maps are drawn: each style's look, a region's reading drawn through its registration, and the page.

A style is a [`Look`][osrforge.tracing.synthetic._draw.Look]: its colours,
how it fills rock and water, how the grid's pitch wanders, and how the page is
scanned. [`draw_page`][osrforge.tracing.synthetic._draw.draw_page] draws one
region on its own page image: a title, the framed map, and below it a compass,
the printed scale, and a legend. Every grid point is placed by the region's
registration, so the registration recorded in the answer is exactly where the
lines were drawn.
"""

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from osrforge.tracing.synthetic._cave import inside
from osrforge.tracing.synthetic._plan import Ornament, Plan, neighbour
from osrforge.tracing.synthetic._raster import (
    BoolArray,
    Canvas,
    FloatArray,
    Point,
    Rgb,
    blur,
    encode,
    hatching,
    noise,
    text,
    text_box,
    text_width,
    waves,
)
from osrforge.tracing.synthetic._spec import TEN_FEET, RegionSpec
from osrforge.tracing.truth import PageSide, Registration, parse_cell, parse_edge, rotation_for

type Cell = tuple[int, int]


@dataclass(frozen=True)
class Look:
    """How one style draws and scans a map."""

    ink: Rgb
    paper: Rgb
    tint: Rgb
    """The grid lines' colour, light enough to read as paper."""

    rock: Literal["solid", "hatch"]
    pitch: float
    """The mean distance between grid lines, in pixels."""

    bow: float
    """How far the pitch wanders across the map, in pixels either side of the mean."""

    skew: float
    """How far each row's grid lines shift right per pixel down the page."""

    blur: bool
    grain: int
    """The spread of each of the four terms of the scan's noise, 0 for none."""

    format: Literal["jpeg", "png"]
    wall: float = 0.24
    """A wall's width in cells."""


def _tint(ink: Rgb, paper: Rgb, share: float) -> Rgb:
    return (
        round(paper[0] + (ink[0] - paper[0]) * share),
        round(paper[1] + (ink[1] - paper[1]) * share),
        round(paper[2] + (ink[2] - paper[2]) * share),
    )


_BLUE: Rgb = (46, 100, 172)
_BLUE_PAPER: Rgb = (242, 241, 233)
_BLACK: Rgb = (24, 24, 24)
_WHITE: Rgb = (255, 255, 255)
_DARK: Rgb = (34, 31, 38)
_CAVE_INK: Rgb = (86, 60, 42)
_CAVE_PAPER: Rgb = (249, 243, 228)

LOOKS: dict[str, Look] = {
    "tsr_blue": Look(
        ink=_BLUE,
        paper=_BLUE_PAPER,
        tint=_tint(_BLUE, _BLUE_PAPER, 0.4),
        rock="solid",
        pitch=26.0,
        bow=0.6,
        skew=0.004,
        blur=True,
        grain=3,
        format="jpeg",
    ),
    "black_white": Look(
        ink=_BLACK,
        paper=_WHITE,
        tint=_tint(_BLACK, _WHITE, 0.3),
        rock="hatch",
        pitch=24.0,
        bow=0.0,
        skew=0.0,
        blur=False,
        grain=0,
        format="png",
    ),
    "dark_rock": Look(
        ink=_DARK,
        paper=_WHITE,
        tint=_tint(_DARK, _WHITE, 0.12),
        rock="solid",
        pitch=24.0,
        bow=0.0,
        skew=0.0,
        blur=False,
        grain=0,
        format="png",
        wall=0.32,
    ),
    "cave": Look(
        ink=_CAVE_INK,
        paper=_CAVE_PAPER,
        tint=_tint(_CAVE_INK, _CAVE_PAPER, 0.3),
        rock="solid",
        pitch=24.0,
        bow=0.0,
        skew=0.0,
        blur=False,
        grain=0,
        format="png",
        wall=0.3,
    ),
}
"""The styles this generator draws, by the truth format's style name."""

MARGIN = 24
_TITLE_ROOM = 34
_FURNITURE_ROOM = 104


def registration_for(
    columns: int, rows: int, look: Look, origin: Point, cut: Sequence[PageSide], width: int | None
) -> Registration:
    """The grid corners of a region drawn in a look, placed on the page.

    The pitch wanders as a bow: wider in the middle of the map than at its
    edges. A row's corners shift right by `skew` for each pixel the row lies
    below the first. A region cut off at the right edge of its image ends on
    the image's last pixel, and one cut off at the left starts on its first.
    A cut region isn't skewed, so the cut runs along the image's edge.

    Args:
        columns: The region's width in cells.
        rows: The region's height in cells.
        look: The style.
        origin: The north-west corner's pixel position, when no cut moves it.
        cut: The image edges that cut the region off.
        width: The image's width, needed for a cut at the right edge.

    Returns:
        The registration, its corners rounded to thousandths of a pixel.
    """

    def line(count: int, start: float) -> list[float]:
        return [start + look.pitch * i + look.bow * i * (count - i) / count for i in range(count + 1)]

    xs = line(columns, origin[0])
    ys = line(rows, origin[1])
    skew = 0.0 if cut else look.skew
    if "left" in cut:
        xs = [x - xs[0] - 0.5 for x in xs]
    if "right" in cut:
        if width is None:
            raise ValueError("a region cut off at the right needs its image's width")
        xs = [x + (width - 0.5 - xs[-1]) for x in xs]
    return Registration(
        corners=tuple(
            tuple((round(xs[i] + skew * (ys[j] - ys[0]), 3), round(ys[j], 3)) for i in range(columns + 1))
            for j in range(rows + 1)
        )
    )


class Pen:
    """Draws a plan through a registration."""

    def __init__(self, canvas: Canvas, registration: Registration, look: Look) -> None:
        """Bind a canvas, a registration, and a look.

        Args:
            canvas: Where to draw.
            registration: Where each grid point lies.
            look: The style.
        """
        self.canvas = canvas
        self.registration = registration
        self.look = look
        corners = registration.corners
        self.pitch = (corners[0][-1][0] - corners[0][0][0]) / (len(corners[0]) - 1)

    def px(self, point: Point) -> Point:
        """The pixel position of a grid point."""
        return self.registration.pixel(point)

    def quad(self, cell: Cell) -> list[Point]:
        """A cell's four corners in pixels, clockwise from the north-west."""
        x, y = cell
        return [self.px((x, y)), self.px((x + 1, y)), self.px((x + 1, y + 1)), self.px((x, y + 1))]

    def ends(self, cell: Cell, side: str) -> tuple[Point, Point]:
        """An edge's two ends in grid coordinates, west to east or north to south."""
        x, y = cell
        if side == "north":
            return (float(x), float(y)), (x + 1.0, float(y))
        if side == "south":
            return (float(x), y + 1.0), (x + 1.0, y + 1.0)
        if side == "west":
            return (float(x), float(y)), (float(x), y + 1.0)
        return (x + 1.0, float(y)), (x + 1.0, y + 1.0)

    def line(
        self, a: Point, b: Point, width: float, layer: Literal["ink", "tint", "paper"] = "ink", cap: float | None = 0.0
    ) -> None:
        """A line between two grid points, its width in cells."""
        self.canvas.segment(
            self.px(a), self.px(b), width * self.pitch, layer, None if cap is None else cap * self.pitch
        )

    def path(self, points: Sequence[Point], width: float) -> None:
        """A line through grid points with round joins, its width in cells."""
        self.canvas.polyline([self.px(point) for point in points], width * self.pitch)

    def dashes(self, points: Sequence[Point], width: float, pattern: Sequence[float]) -> None:
        """A line through grid points drawn in dashes.

        Args:
            points: The points.
            width: The line's width in cells.
            pattern: Lengths in cells, alternately drawn and skipped.
        """
        index = 0
        remaining = pattern[0]
        for a, b in itertools.pairwise(points):
            length = math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
            done = 0.0
            while done < length:
                step = min(remaining, length - done)
                if index % 2 == 0 and step > 0.0:
                    t0, t1 = done / length, (done + step) / length
                    start = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
                    end = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
                    self.line(start, end, width, cap=None)
                done += step
                remaining -= step
                if remaining <= 1e-9:
                    index += 1
                    remaining = pattern[index % len(pattern)]

    def label(self, words: str, centre: Point, height: float, weight: float, backed: bool) -> None:
        """A label centred on a grid point, sizes in cells, on a blank ground when `backed`."""
        point = self.px(centre)
        if backed:
            self.canvas.rectangle(text_box(words, point, height * self.pitch, 0.08 * self.pitch), "paper")
        text(self.canvas, words, point, height * self.pitch, weight * self.pitch)


WALL = 0.24
WINDOW = 0.32
GRID = 0.05
DOOR = 0.22
DOOR_ACROSS = 0.2


def draw_plan(pen: Pen, plan: Plan, cut: Sequence[PageSide]) -> None:
    """Draw a plan's reading and ornaments.

    Args:
        pen: The pen, bound to the region's registration.
        plan: The region's plan.
        cut: The image edges that cut the region off, where no frame line is drawn.
    """
    look = pen.look
    canvas = pen.canvas
    pitch = pen.pitch
    caves = [ornament for ornament in plan.ornaments if ornament.kind == "cave"]
    for y in range(plan.height):
        for x in range(plan.width):
            letter = plan.cells[y][x]
            if letter == "#" and caves:
                continue
            if letter == "#":
                if look.rock == "solid":
                    canvas.polygon(pen.quad((x, y)))
                else:
                    canvas.polygon(pen.quad((x, y)), pattern=hatching(0.3 * pitch, 0.07 * pitch))
            elif letter == "~":
                canvas.polygon(pen.quad((x, y)), pattern=waves(0.3 * pitch, 0.5 * pitch, 0.08 * pitch, 0.06 * pitch))
    occupied = {(x, y) for y in range(plan.height) for x in range(plan.width) if plan.cells[y][x] != "#"}
    positions = list(plan.edges.positions())
    for cell, side in positions:
        other = neighbour(cell, side)
        if caves or (cell in occupied and other in occupied):
            a, b = pen.ends(cell, side)
            pen.line(a, b, GRID, "tint", cap=GRID / 2.0)
    if caves:
        _cave_floor(pen, caves)
    styles = plan.edge_styles
    for cell, side in positions:
        letter = plan.letter(cell, side)
        a, b = pen.ends(cell, side)
        named = f"{cell[0]},{cell[1]}:{side}"
        style = styles.get(named) or styles.get(_other_name(cell, side))
        if style == "hidden":
            continue
        if letter == "p":
            pattern = (0.16, 0.12, 0.03, 0.12) if style == "dashdot" else (0.2, 0.12)
            pen.dashes([a, b], 0.07, pattern)
        elif letter == "n":
            pen.line(a, b, WINDOW, cap=look.wall / 2.0)
        elif letter in "dls" or (letter == "w" and not caves):
            pen.line(a, b, look.wall, cap=look.wall / 2.0)
    for cell, side in positions:
        letter = plan.letter(cell, side)
        if letter in "nlds":
            _door(pen, cell, side, letter)
    for index in range(len(plan.symbols)):
        _symbol(pen, plan, index)
    for ornament in plan.ornaments:
        _ornament(pen, ornament)
    for mark in plan.marks:
        _mark(pen, plan, mark.text, parse_cell(mark.cell), mark.edge, mark.leader_to)
    corners = [(0.0, 0.0), (float(plan.width), 0.0), (float(plan.width), float(plan.height)), (0.0, float(plan.height))]
    names = ("top", "right", "bottom", "left")
    for index in range(4):
        if names[index] not in cut:
            pen.line(corners[index], corners[(index + 1) % 4], 0.06, cap=0.03)


def _cave_floor(pen: Pen, caves: list[Ornament]) -> None:
    """Paint rock everywhere off the caves' floor, so the paper left is exactly the floor the reading counts.

    The rock's edge is the cave's wall, off the grid lines.
    """
    corners = pen.registration.corners
    (left, top), (right, bottom) = corners[0][0], corners[-1][-1]
    pitch = pen.pitch

    def floor(x: FloatArray, y: FloatArray) -> BoolArray:
        return inside(caves, (x - left) / pitch, (y - top) / pitch)

    def rock(x: FloatArray, y: FloatArray) -> BoolArray:
        return ~floor(x, y) & (x >= left) & (x <= right) & (y >= top) & (y <= bottom)

    pen.canvas.paint(rock, (left, top, right, bottom))


def _other_name(cell: Cell, side: str) -> str:
    other = neighbour(cell, side)
    opposite = {"north": "south", "south": "north", "east": "west", "west": "east"}[side]
    return f"{other[0]},{other[1]}:{opposite}"


def _door(pen: Pen, cell: Cell, side: str, letter: str) -> None:
    a, b = pen.ends(cell, side)
    middle = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    canvas = pen.canvas
    horizontal = side in ("north", "south")
    if letter == "n":
        span = 0.32
        start = (middle[0] - span, middle[1]) if horizontal else (middle[0], middle[1] - span)
        end = (middle[0] + span, middle[1]) if horizontal else (middle[0], middle[1] + span)
        pen.line(start, end, 0.06, "paper", cap=0.0)
        return
    if letter == "s":
        box = [(middle[0] + dx * 0.19, middle[1] + dy * 0.19) for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        canvas.polygon([pen.px(point) for point in box], "paper")
        pen.label("S", middle, 0.4, 0.085, backed=False)
        return
    if horizontal:
        corners = [(middle[0] - DOOR, middle[1] - DOOR_ACROSS), (middle[0] + DOOR, middle[1] - DOOR_ACROSS)]
        corners += [(middle[0] + DOOR, middle[1] + DOOR_ACROSS), (middle[0] - DOOR, middle[1] + DOOR_ACROSS)]
    else:
        corners = [(middle[0] - DOOR_ACROSS, middle[1] - DOOR), (middle[0] + DOOR_ACROSS, middle[1] - DOOR)]
        corners += [(middle[0] + DOOR_ACROSS, middle[1] + DOOR), (middle[0] - DOOR_ACROSS, middle[1] + DOOR)]
    pixels = [pen.px(point) for point in corners]
    canvas.polygon(pixels, "paper")
    for index in range(4):
        canvas.segment(pixels[index], pixels[(index + 1) % 4], 0.07 * pen.pitch, cap=0.035 * pen.pitch)
    if letter == "l":
        canvas.disc(pen.px(middle), 0.075 * pen.pitch)


def _run(plan: Plan, cells: list[Cell]) -> str:
    named = plan.stair_runs.get(f"{cells[0][0]},{cells[0][1]}")
    if named is not None:
        return named
    xs = {cell[0] for cell in cells}
    ys = {cell[1] for cell in cells}
    return "ew" if len(xs) > len(ys) else "ns"


def _symbol(pen: Pen, plan: Plan, index: int) -> None:
    symbol = plan.symbols[index]
    cells = [parse_cell(text) for text in symbol.cells]
    if symbol.kind == "cave_mouth":
        assert symbol.edge is not None
        cell, side = parse_edge(symbol.edge)
        a, b = pen.ends(cell, side)
        outward = neighbour(cell, side)
        if plan.cell(outward) == "#" and plan.cell(cell) != "#":
            outward = cell
        direction = (outward[0] - cell[0], outward[1] - cell[1])
        middle = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        along = (b[0] - a[0], b[1] - a[1])
        for toward in (-1.0, 1.0):
            centre = (
                middle[0] + along[0] * 0.9 * toward + direction[0] * 0.4,
                middle[1] + along[1] * 0.9 * toward + direction[1] * 0.4,
            )
            pen.canvas.disc(pen.px(centre), 0.12 * pen.pitch)
        return
    if symbol.kind == "pillar":
        for x, y in cells:
            pen.canvas.disc(pen.px((x + 0.5, y + 0.5)), 0.27 * pen.pitch)
        return
    if symbol.kind == "bridge":
        run = _run(plan, cells)
        for x, y in cells:
            if run == "ns":
                pen.line((x + 0.18, y), (x + 0.18, y + 1.0), 0.06, cap=0.0)
                pen.line((x + 0.82, y), (x + 0.82, y + 1.0), 0.06, cap=0.0)
                for step in range(1, 6):
                    pen.line((x + 0.18, y + step / 6.0), (x + 0.82, y + step / 6.0), 0.04, cap=0.0)
            else:
                pen.line((x, y + 0.18), (x + 1.0, y + 0.18), 0.06, cap=0.0)
                pen.line((x, y + 0.82), (x + 1.0, y + 0.82), 0.06, cap=0.0)
                for step in range(1, 6):
                    pen.line((x + step / 6.0, y + 0.18), (x + step / 6.0, y + 0.82), 0.04, cap=0.0)
        return
    if symbol.kind in ("stairs", "steps", "ladder"):
        run = _run(plan, cells)
        bars = 5 if symbol.kind != "steps" else 3
        reach = 0.42 if symbol.kind != "steps" else 0.26
        for x, y in cells:
            for step in range(bars):
                offset = (step + 0.5) / bars
                if run == "ns":
                    pen.line((x + 0.5 - reach, y + offset), (x + 0.5 + reach, y + offset), 0.05, cap=0.0)
                else:
                    pen.line((x + offset, y + 0.5 - reach), (x + offset, y + 0.5 + reach), 0.05, cap=0.0)
        if symbol.direction is not None:
            xs = [cell[0] for cell in cells]
            ys = [cell[1] for cell in cells]
            centre = ((min(xs) + max(xs) + 1) / 2.0, (min(ys) + max(ys) + 1) / 2.0)
            pen.label("UP" if symbol.direction == "up" else "DN", centre, 0.26, 0.05, backed=True)
        return
    raise ValueError(f"the generator doesn't draw {symbol.kind} symbols")


def chaikin(points: Sequence[Point], rounds: int = 3, closed: bool = False) -> list[Point]:
    """Round a polyline's corners by Chaikin's corner cutting.

    Args:
        points: The polyline.
        rounds: How many times to cut.
        closed: Whether the line closes on itself.

    Returns:
        The smoother polyline.
    """
    current = list(points)
    for _ in range(rounds):
        cut: list[Point] = [] if closed else [current[0]]
        count = len(current) if closed else len(current) - 1
        for index in range(count):
            a, b = current[index], current[(index + 1) % len(current)]
            cut.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            cut.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        if closed:
            cut.append(cut[0])
        else:
            cut.append(current[-1])
        current = cut
    return current


def _normals(points: Sequence[Point]) -> list[Point]:
    normals: list[Point] = []
    for index in range(len(points)):
        a = points[max(index - 1, 0)]
        b = points[min(index + 1, len(points) - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.sqrt(dx * dx + dy * dy) or 1.0
        normals.append((-dy / length, dx / length))
    return normals


def circle(centre: Point, radius: float, steps: int = 24) -> list[Point]:
    """Points around a circle, from the rational parametrization so no sine is computed.

    Args:
        centre: The centre.
        radius: The radius.
        steps: Points per quarter turn.

    Returns:
        The closed loop of points.
    """
    quarter = [((1.0 - t * t) / (1.0 + t * t), 2.0 * t / (1.0 + t * t)) for t in (k / steps for k in range(steps))]
    loop: list[Point] = []
    for sx, sy, swap in ((1.0, 1.0, False), (-1.0, 1.0, True), (-1.0, -1.0, False), (1.0, -1.0, True)):
        for u, v in quarter:
            dx, dy = (v, u) if swap else (u, v)
            loop.append((centre[0] + sx * dx * radius, centre[1] + sy * dy * radius))
    loop.append(loop[0])
    return loop


def _ornament(pen: Pen, ornament: Ornament) -> None:
    if ornament.kind == "cave":
        return
    if ornament.kind == "contour":
        pen.path(chaikin(ornament.points), 0.035)
    elif ornament.kind == "track":
        smooth = chaikin(ornament.points)
        for sign in (-1.0, 1.0):
            shifted = [
                (point[0] + normal[0] * 0.1 * sign, point[1] + normal[1] * 0.1 * sign)
                for point, normal in zip(smooth, _normals(smooth), strict=True)
            ]
            pen.dashes(shifted, 0.035, (0.16, 0.1))
    elif ornament.kind == "stream":
        smooth = chaikin(ornament.points)
        travelled = 0.0
        mark = 0.0
        for index in range(1, len(smooth)):
            a, b = smooth[index - 1], smooth[index]
            length = math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
            travelled += length
            if travelled - mark >= 0.24 and length > 0.0:
                mark = travelled
                nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
                pen.line((b[0] - nx * 0.16, b[1] - ny * 0.16), (b[0] + nx * 0.16, b[1] + ny * 0.16), 0.04, cap=None)
    elif ornament.kind == "tree":
        centre, radius = ornament.points[0], ornament.size
        loop = circle(centre, radius, 6)
        bumpy = [
            (
                centre[0] + (point[0] - centre[0]) * (1.0 if index % 2 == 0 else 0.86),
                centre[1] + (point[1] - centre[1]) * (1.0 if index % 2 == 0 else 0.86),
            )
            for index, point in enumerate(loop[:-1])
        ]
        pen.path(chaikin(bumpy, 2, closed=True), 0.04)
    else:
        pen.dashes(circle(ornament.points[0], ornament.size), 0.07, (0.2, 0.12))


def _mark(pen: Pen, plan: Plan, words: str, cell: Cell, edge: str | None, leader: str | None) -> None:
    x, y = cell
    centre = (x + 0.5, y + 0.5)
    if edge is not None:
        _cell, side = parse_edge(edge)
        dx, dy = {"north": (0.0, -1.0), "south": (0.0, 1.0), "west": (-1.0, 0.0), "east": (1.0, 0.0)}[side]
        centre = (x + 0.5 + dx * 0.12, y + 0.5 + dy * 0.12)
    backed = plan.cell(cell) != "."
    lines = words.split("/")
    if len(lines) == 1:
        pen.label(words, centre, 0.46, 0.085, backed)
    else:
        for index, line in enumerate(lines):
            offset = (index - (len(lines) - 1) / 2.0) * 0.5
            pen.label(line, (centre[0], centre[1] + offset), 0.34, 0.07, backed)
    if leader is not None:
        tx, ty = parse_cell(leader)
        target = (tx + 0.5, ty + 0.5)
        dx, dy = target[0] - centre[0], target[1] - centre[1]
        length = math.sqrt(dx * dx + dy * dy)
        start = (centre[0] + dx / length * 0.35, centre[1] + dy / length * 0.35)
        pen.line(start, target, 0.04, cap=None)
        pen.canvas.disc(pen.px(target), 0.07 * pen.pitch)


# The page around the map.


def _compass(canvas: Canvas, centre: Point, size: float) -> None:
    cx, cy = centre
    canvas.segment((cx, cy + size * 0.5), (cx, cy - size * 0.2), size * 0.08)
    canvas.polygon([(cx, cy - size * 0.55), (cx + size * 0.18, cy - size * 0.15), (cx - size * 0.18, cy - size * 0.15)])
    text(canvas, "N", (cx, cy - size * 0.85), size * 0.3, size * 0.06)


_LEGEND_NAMES: dict[str, str] = {
    "d": "DOOR",
    "s": "SECRET DOOR",
    "l": "LOCKED DOOR",
    "n": "WINDOW",
    "stairs": "STAIRS",
    "steps": "STEPS",
    "pillar": "PILLAR",
    "cave_mouth": "CAVE MOUTH",
    "bridge": "BRIDGE",
}


def _legend_sample(canvas: Canvas, look: Look, origin: Point, entry: str) -> None:
    """Draw one legend entry's sample: two cells side by side showing the entry."""
    size = 20.0
    registration = Registration(
        corners=(
            ((origin[0], origin[1]), (origin[0] + size, origin[1]), (origin[0] + 2 * size, origin[1])),
            (
                (origin[0], origin[1] + size),
                (origin[0] + size, origin[1] + size),
                (origin[0] + 2 * size, origin[1] + size),
            ),
        )
    )
    pen = Pen(canvas, registration, look)
    if entry in ("d", "s", "l", "n"):
        pen.line((1.0, 0.0), (1.0, 1.0), WINDOW if entry == "n" else WALL, cap=WALL / 2.0)
        _door(pen, (0, 0), "east", entry)
    elif entry == "pillar":
        canvas.disc(pen.px((1.0, 0.5)), 0.27 * size)
    elif entry in ("stairs", "steps"):
        for step in range(5 if entry == "stairs" else 3):
            offset = 0.2 + step * (0.6 / (4 if entry == "stairs" else 2))
            reach = 0.42 if entry == "stairs" else 0.26
            pen.line((0.5 + offset, 0.5 - reach), (0.5 + offset, 0.5 + reach), 0.05, cap=0.0)
    elif entry == "cave_mouth":
        canvas.disc(pen.px((0.6, 0.5)), 0.16 * size)
        canvas.disc(pen.px((1.4, 0.5)), 0.16 * size)
    elif entry == "bridge":
        pen.line((0.3, 0.3), (1.7, 0.3), 0.06, cap=0.0)
        pen.line((0.3, 0.7), (1.7, 0.7), 0.06, cap=0.0)
        for step in range(1, 7):
            pen.line((0.3 + step * 0.2, 0.3), (0.3 + step * 0.2, 0.7), 0.04, cap=0.0)
    canvas.outline((origin[0], origin[1], origin[0] + 2 * size, origin[1] + size), 1.0)


def legend_entries(region: RegionSpec) -> list[str]:
    """The legend's entries, in the order they're printed: the door kinds the map shows, then its symbols."""
    letters = "".join(region.plan.edges.vertical) + "".join(region.plan.edges.horizontal)
    entries = [letter for letter in "dsln" if letter in letters or (letter == "l" and "locked_door" in region.legend)]
    if "l" in letters and "locked_door" not in region.legend:
        raise ValueError(f"region {region.id} shows a locked door its legend doesn't explain")
    return entries + [kind for kind in region.legend if kind != "locked_door"]


def page_size(region: RegionSpec, look: Look, plan: Plan) -> tuple[int, int]:
    """The page image's size for one region, drawn with its north at the top.

    Args:
        region: The region.
        look: Its style.
        plan: The region's plan turned to put its north at the top.

    Returns:
        `(width, height)` in pixels.
    """
    probe = registration_for(plan.width, plan.height, look, (MARGIN, MARGIN + _TITLE_ROOM), (), None)
    right = max(point[0] for row in probe.corners for point in row)
    bottom = max(point[1] for row in probe.corners for point in row)
    furniture = MARGIN * 2 + 230 + 150 * max(1, (len(legend_entries(region)) + 1) // 2)
    width = max(math.ceil(right) + MARGIN, furniture)
    if "left" in region.cut_off:
        width -= MARGIN
    return width, math.ceil(bottom) + _FURNITURE_ROOM


def _turn_page(
    pixels: npt.NDArray[np.int32], registration: Registration, rotation: int, plan: Plan
) -> tuple[npt.NDArray[np.int32], Registration]:
    """Turn a page drawn north-up so its north points where the region's compass says.

    The page turns counter-clockwise by `rotation`, the quarter turn that panel
    placement undoes. Each corner of the region's grid in page orientation
    takes the pixel of the north-up corner it turned from.

    Args:
        pixels: The north-up page.
        registration: The north-up grid's corners.
        rotation: The clockwise quarter turn from the page to the north-up drawing.
        plan: The region's plan in page orientation.

    Returns:
        The turned page and the region's registration on it.
    """
    turns = rotation // 90
    height, width = pixels.shape[0], pixels.shape[1]

    def turned(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        size = (width, height)
        for _ in range(turns):
            x, y = y, size[0] - 1 - x
            size = (size[1], size[0])
        return (round(x, 3), round(y, 3))

    def upright(gx: int, gy: int) -> tuple[int, int]:
        if rotation == 90:
            return (plan.height - gy, gx)
        if rotation == 180:
            return (plan.width - gx, plan.height - gy)
        return (gy, plan.width - gx)

    corners = tuple(
        tuple(turned(registration.corners[upright(gx, gy)[1]][upright(gx, gy)[0]]) for gx in range(plan.width + 1))
        for gy in range(plan.height + 1)
    )
    return np.ascontiguousarray(np.rot90(pixels, k=turns)), Registration(corners=corners)


def draw_page(region: RegionSpec, seed: int) -> tuple[bytes, Registration, Look]:
    """Draw one region on its own page image.

    The page is drawn with the region's north at the top, then turned so its
    north points where the region's compass says, title and legend with it.

    Args:
        region: The region.
        seed: The scan noise's seed.

    Returns:
        The encoded image, the region's registration, and its look.
    """
    look = LOOKS[region.style]
    rotation = rotation_for(region.compass.north) if region.compass.printed else 0
    plan = region.plan.turned(rotation)
    width, height = page_size(region, look, plan)
    registration = registration_for(
        plan.width, plan.height, look, (MARGIN, MARGIN + _TITLE_ROOM), region.cut_off, width
    )
    canvas = Canvas(width, height)
    draw_plan(Pen(canvas, registration, look), plan, region.cut_off)
    left = MARGIN if "left" not in region.cut_off else MARGIN + 4
    text(canvas, region.title, (left + text_width(region.title, 13.0) / 2.0, MARGIN + 8.0), 13.0, 1.8)
    bottom = max(point[1] for row in registration.corners for point in row)
    furniture = bottom + 22.0
    if region.compass.printed:
        _compass(canvas, (left + 16.0, furniture + 40.0), 48.0)
    scale = region.scale or TEN_FEET
    if scale.printed and scale.text is not None:
        text(canvas, scale.text, (left + 50.0 + text_width(scale.text, 9.0) / 2.0, furniture + 14.0), 9.0, 1.3)
    entries = legend_entries(region) if region.legend_from is None else []
    for index, entry in enumerate(entries):
        column, row = divmod(index, 2)
        x = left + 50.0 + column * 150.0
        y = furniture + 30.0 + row * 28.0
        _legend_sample(canvas, look, (x, y), entry)
        name = _LEGEND_NAMES[entry]
        text(canvas, name, (x + 48.0 + text_width(name, 8.0) / 2.0, y + 10.0), 8.0, 1.2)
    pixels = canvas.pixels(look.paper, look.tint, look.ink)
    if rotation:
        pixels, registration = _turn_page(pixels, registration, rotation, region.plan)
    if look.blur:
        pixels = blur(pixels)
    if look.grain:
        grain = noise(pixels.shape[1], pixels.shape[0], seed, look.grain)
        pixels = pixels + grain[:, :, None]
    return encode(pixels, look.format), registration, look
