"""Pictures that aren't plans the stage traces: maps it refuses or can't register, and images no region uses.

Each drawing is named in `DRAWINGS` and fills the map box of a fixed page.
The plan-based pictures (`plan` and `warped`) draw a sheet's own plan: a
draft hidden under a page's map, or a gridded map whose grid is warped so
registration can't fit it. Everything is drawn with the same exact arithmetic
as the traced maps, and no sine is computed: the warp follows Bhaskara's
rational approximation of a sine, scaled so it bends at least as far as a sine
of the same amplitude.
"""

import itertools
import math
from collections.abc import Callable

from osrforge.tracing.synthetic._draw import LOOKS, MARGIN, Pen, chaikin, draw_plan, registration_for
from osrforge.tracing.synthetic._raster import (
    Canvas,
    Point,
    Rgb,
    encode,
    hatching,
    text,
    text_width,
    waves,
)
from osrforge.tracing.synthetic._spec import SheetSpec
from osrforge.tracing.truth import Registration

PAGE = (640, 480)
"""A sheet's page size in pixels."""

BOX = (24.0, 58.0, 616.0, 410.0)
"""The map box on a sheet's page: `(xmin, ymin, xmax, ymax)`."""

WARP = 0.7
"""The warped map's bend, in pitches: more than the 0.6 a registration must refuse."""

_INK: Rgb = (28, 28, 28)
_PAPER: Rgb = (252, 251, 246)


def _unit(index: int) -> float:
    """A number in [0, 1) from an index, by the splitmix64 mixer in Python integers, the same on every platform."""
    mask = (1 << 64) - 1
    z = (index + 0x9E3779B97F4A7C15) & mask
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & mask
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & mask
    return ((z ^ (z >> 31)) >> 11) / float(1 << 53)


def _wave(u: float) -> float:
    """A wave with a period of 1 and an amplitude of 1, bending at least as far as a sine of that amplitude.

    Each half follows Bhaskara's approximation of `sin(pi v)`, which never
    falls more than 0.0017 below it; the result is scaled by 1.002 to cover
    that.
    """
    v = u - math.floor(u)
    half, sign = (v * 2.0, 1.0) if v < 0.5 else (v * 2.0 - 1.0, -1.0)
    product = half * (1.0 - half)
    return sign * 1.002 * 16.0 * product / (5.0 - 4.0 * product)


def _hexagon(canvas: Canvas, centre: Point, radius: float, width: float) -> list[Point]:
    cx, cy = centre
    rise = radius * math.sqrt(3.0) / 2.0
    points = [
        (cx + radius, cy),
        (cx + radius / 2.0, cy + rise),
        (cx - radius / 2.0, cy + rise),
        (cx - radius, cy),
        (cx - radius / 2.0, cy - rise),
        (cx + radius / 2.0, cy - rise),
    ]
    for index in range(6):
        canvas.segment(points[index], points[(index + 1) % 6], width, cap=None)
    return points


def _tree(canvas: Canvas, centre: Point, size: float) -> None:
    cx, cy = centre
    canvas.disc((cx, cy - size * 0.3), size * 0.55)
    canvas.disc((cx, cy - size * 0.3), size * 0.4, "paper")
    canvas.segment((cx, cy + size * 0.2), (cx, cy + size * 0.7), size * 0.18)


def _hill(canvas: Canvas, centre: Point, size: float) -> None:
    cx, cy = centre
    canvas.polyline([(cx - size, cy + size * 0.4), (cx, cy - size * 0.4), (cx + size, cy + size * 0.4)], 1.4)


def _outdoor(canvas: Canvas) -> None:
    left, top, right, bottom = BOX
    coast = chaikin([(left, top + 40), (left + 90, top + 110), (left + 60, top + 210), (left + 120, bottom)])
    for start, end in itertools.pairwise(coast):
        canvas.segment(start, end, 2.0)
        canvas.polygon(
            [(left, start[1]), start, end, (left, end[1])],
            pattern=waves(9.0, 18.0, 2.5, 1.2),
        )
    river = chaikin([(right - 40, top + 30), (right - 160, top + 120), (left + 260, top + 200), (left + 95, top + 250)])
    canvas.polyline(river, 3.0)
    road = chaikin([(right, bottom - 60), (left + 300, bottom - 40), (left + 200, top + 90), (left + 230, top)])
    for index, (start, end) in enumerate(itertools.pairwise(road)):
        if index % 2 == 0:
            canvas.segment(start, end, 1.6, cap=None)
    for index in range(60):
        x = left + 330 + _unit(index) * 220
        y = top + 160 + _unit(index + 500) * 150
        _tree(canvas, (x, y), 6.0)
    for index in range(24):
        x = left + 140 + _unit(index + 900) * 150
        y = top + 20 + _unit(index + 1300) * 90
        _hill(canvas, (x, y), 9.0)
    for name, (x, y) in (("HOLLOWMERE", (left + 250, bottom - 70)), ("GREYROCK", (right - 120, top + 70))):
        canvas.rectangle((x - 5, y - 5, x + 5, y + 5))
        text(canvas, name, (x + 12 + text_width(name, 9.0) / 2.0, y), 9.0, 1.3)


def _hexes(canvas: Canvas) -> None:
    left, top, right, bottom = BOX
    radius = 26.0
    rise = radius * math.sqrt(3.0) / 2.0
    column = 0
    x = left + radius
    while x + radius <= right:
        y = top + rise + (rise if column % 2 else 0.0)
        row = 0
        while y + rise <= bottom:
            points = _hexagon(canvas, (x, y), radius, 1.2)
            kind = int(_unit(column * 31 + row) * 6)
            if kind == 0:
                canvas.polygon(points, pattern=waves(7.0, 14.0, 2.0, 1.2))
            elif kind == 1:
                for offset in ((-8.0, 2.0), (8.0, 2.0), (0.0, -8.0)):
                    _tree(canvas, (x + offset[0], y + offset[1]), 5.0)
            elif kind == 2:
                _hill(canvas, (x - 6, y + 2), 8.0)
                _hill(canvas, (x + 7, y - 4), 8.0)
            row += 1
            y += 2 * rise
        column += 1
        x += radius * 1.5


def _town(canvas: Canvas) -> None:
    left, top, right, bottom = BOX
    canvas.outline((left + 20, top + 10, right - 20, bottom - 10), 6.0)
    blocks = [
        (left + 40, top + 30, left + 170, top + 120),
        (left + 200, top + 30, left + 330, top + 120),
        (left + 360, top + 30, right - 40, top + 120),
        (left + 40, top + 150, left + 170, bottom - 30),
        (left + 360, top + 150, right - 40, bottom - 30),
    ]
    for index, (x0, y0, x1, y1) in enumerate(blocks):
        span = (x1 - x0) / 3.0
        for part in range(3):
            depth = 20.0 + _unit(index * 7 + part) * 30.0
            canvas.rectangle((x0 + part * span + 3, y0 + 3, x0 + (part + 1) * span - 3, y0 + depth))
            canvas.rectangle((x0 + part * span + 3, y1 - depth, x0 + (part + 1) * span - 3, y1 - 3))
    canvas.outline((left + 210, top + 160, left + 320, bottom - 40), 1.5)
    canvas.disc((left + 265, top + 230), 9.0)
    for name, (x, y) in (
        ("INN", (left + 105, top + 75)),
        ("TEMPLE", (left + 265, top + 75)),
        ("SMITH", (right - 110, top + 75)),
    ):
        canvas.rectangle(
            (x - text_width(name, 9.0) / 2.0 - 3, y - 7, x + text_width(name, 9.0) / 2.0 + 3, y + 7), "paper"
        )
        text(canvas, name, (x, y), 9.0, 1.3)
    text(canvas, "MARKET", (left + 265, top + 200), 9.0, 1.3)


def _quarter_mile(canvas: Canvas) -> None:
    left, top, right, bottom = BOX
    pitch = 22.0
    columns, rows = int((right - left) // pitch), int((bottom - top) // pitch)
    for i in range(columns + 1):
        canvas.segment((left + i * pitch, top), (left + i * pitch, top + rows * pitch), 1.0, cap=0.5)
    for j in range(rows + 1):
        canvas.segment((left, top + j * pitch), (left + columns * pitch, top + j * pitch), 1.0, cap=0.5)
    river = chaikin(
        [(left + 30, top), (left + 120, top + 120), (left + 90, top + 220), (left + 200, top + rows * pitch)]
    )
    canvas.polyline(river, 4.0)
    for index in range(40):
        _tree(canvas, (left + 300 + _unit(index + 70) * 230, top + 40 + _unit(index + 170) * 150), 6.0)
    road = chaikin([(left, top + 300), (left + 250, top + 250), (left + columns * pitch, top + 290)])
    canvas.polyline(road, 2.0)
    canvas.rectangle((left + 380, top + 250, left + 400, top + 270))
    text(canvas, "RUIN", (left + 425, top + 260), 9.0, 1.3)


def _side_view(canvas: Canvas) -> None:
    left, top, right, bottom = BOX
    ground = top + 150
    surface = [(left, ground + 10), (left + 150, ground), (left + 420, ground - 6), (right, ground + 12)]
    canvas.polyline(surface, 2.0)
    for start, end in itertools.pairwise(surface):
        canvas.polygon([start, end, (end[0], bottom), (start[0], bottom)], pattern=hatching(10.0, 1.4))
    tower = (left + 220, top + 10, left + 330, ground)
    canvas.rectangle(tower, "paper")
    canvas.outline(tower, 4.0)
    for floor in (top + 60, top + 105):
        canvas.segment((tower[0], floor), (tower[2], floor), 3.0, cap=0.0)
    for x0, y0, x1, y1 in (
        (left + 180, ground + 40, left + 380, ground + 100),
        (left + 120, ground + 150, left + 330, ground + 200),
    ):
        canvas.rectangle((x0, y0, x1, y1), "paper")
        canvas.outline((x0, y0, x1, y1), 4.0)
    stairs = [(left + 262.0 + step * 10.0, ground + 100.0 + step * (50.0 / 6.0)) for step in range(7)]
    canvas.polyline([point for start in stairs for point in (start, (start[0] + 10.0, start[1]))], 2.0)
    canvas.rectangle((left + 250, ground - 2, left + 262, ground + 42), "paper")
    canvas.rectangle((left + 250, ground + 98, left + 262, ground + 152), "paper")
    for name, (x, y) in (("LEVEL 1", (left + 330, ground + 70)), ("LEVEL 2", (left + 275, ground + 175))):
        text(canvas, name, (x, y), 9.0, 1.3)


def _isometric(canvas: Canvas) -> None:
    left, top, _right, _bottom = BOX
    unit = 18.0
    origin = (left + 300.0, top + 60.0)

    def project(x: float, y: float, z: float) -> Point:
        return (origin[0] + (x - y) * unit, origin[1] + (x + y) * unit / 2.0 - z * unit)

    rooms = [(0.0, 0.0, 6.0, 5.0), (6.0, 1.0, 4.0, 3.0), (1.0, 5.0, 4.0, 4.0)]
    height = 2.5
    for x, y, w, h in rooms:
        floor = [project(x, y, 0.0), project(x + w, y, 0.0), project(x + w, y + h, 0.0), project(x, y + h, 0.0)]
        canvas.polygon(floor, "paper")
        back = [project(x, y, 0.0), project(x + w, y, 0.0), project(x + w, y, height), project(x, y, height)]
        side = [project(x, y, 0.0), project(x, y + h, 0.0), project(x, y + h, height), project(x, y, height)]
        canvas.polygon(back, pattern=hatching(6.0, 1.2))
        canvas.polygon(side, pattern=hatching(4.0, 1.2))
        for shape in (floor, back, side):
            for index in range(4):
                canvas.segment(shape[index], shape[(index + 1) % 4], 1.8)
    for index, (x, y, w, h) in enumerate(rooms):
        centre = project(x + w / 2.0, y + h / 2.0, 0.0)
        text(canvas, str(index + 1), centre, 11.0, 1.6)


def _no_grid(canvas: Canvas) -> None:
    left, top, _right, _bottom = BOX
    rooms = [
        (left + 37.0, top + 41.0, left + 171.0, top + 133.0),
        (left + 233.0, top + 22.0, left + 351.0, top + 157.0),
        (left + 409.0, top + 63.0, left + 548.0, top + 141.0),
        (left + 121.0, top + 209.0, left + 287.0, top + 318.0),
        (left + 371.0, top + 223.0, left + 513.0, top + 301.0),
    ]
    corridors = [
        ((left + 171.0, top + 87.0), (left + 233.0, top + 87.0)),
        ((left + 351.0, top + 101.0), (left + 409.0, top + 101.0)),
        ((left + 150.0, top + 133.0), (left + 150.0, top + 209.0)),
        ((left + 287.0, top + 262.0), (left + 371.0, top + 262.0)),
    ]
    for start, end in corridors:
        canvas.segment(start, end, 23.0, cap=0.0)
        canvas.segment(start, end, 15.0, "paper", cap=4.0)
    for x0, y0, x1, y1 in rooms:
        canvas.outline((x0, y0, x1, y1), 4.0)
    for start, end in corridors:
        canvas.segment(start, end, 15.0, "paper", cap=0.0)
    for index, (x0, y0, x1, y1) in enumerate(rooms):
        text(canvas, str(index + 1), ((x0 + x1) / 2.0, (y0 + y1) / 2.0), 12.0, 1.8)


DRAWINGS: dict[str, Callable[[Canvas], None]] = {
    "outdoor": _outdoor,
    "hex": _hexes,
    "town": _town,
    "quarter_mile": _quarter_mile,
    "side_view": _side_view,
    "isometric": _isometric,
    "no_grid": _no_grid,
}
"""The pictures a sheet can show, besides its own plan."""


def _warped(registration: Registration, pitch: float) -> Registration:
    """A registration whose rows wave up and down, and whose columns wave side to side, two periods across."""
    corners = registration.corners
    (x0, y0), (x1, y1) = corners[0][0], corners[-1][-1]
    width, height = x1 - x0, y1 - y0
    return Registration(
        corners=tuple(
            tuple(
                (
                    round(x + WARP * pitch * _wave(2.0 * (y - y0) / height), 3),
                    round(y + WARP * pitch * _wave(2.0 * (x - x0) / width), 3),
                )
                for x, y in row
            )
            for row in corners
        )
    )


def _bent_frame(pen: Pen, width: int, height: int) -> None:
    """Draw the map's frame a cell at a time, so it bends with the warp as a warped scan's frame would."""
    border = [(float(x), 0.0) for x in range(width)] + [(float(width), float(y)) for y in range(height)]
    border += [(float(x), float(height)) for x in range(width, 0, -1)] + [(0.0, float(y)) for y in range(height, 0, -1)]
    for start, end in itertools.pairwise([*border, border[0]]):
        pen.line(start, end, 0.06, cap=0.03)


def draw_sheet(sheet: SheetSpec) -> tuple[bytes, Rgb, Rgb]:
    """Draw a sheet's page.

    Args:
        sheet: The sheet.

    Returns:
        The encoded image, and the ink and paper colours an ink test would use.
    """
    look = LOOKS.get(sheet.style)
    ink, paper = (look.ink, look.paper) if look is not None else (_INK, _PAPER)
    if sheet.plan is not None:
        assert look is not None
        plan = sheet.plan
        probe = registration_for(plan.width, plan.height, look, (MARGIN, MARGIN + 34), (), None)
        width = max(math.ceil(max(point[0] for row in probe.corners for point in row)) + MARGIN * 2, PAGE[0])
        height = math.ceil(max(point[1] for row in probe.corners for point in row)) + 80
        canvas = Canvas(width, height)
        if sheet.drawing == "plan":
            draw_plan(Pen(canvas, probe, look), plan, ())
        else:
            pen = Pen(canvas, _warped(probe, look.pitch), look)
            draw_plan(pen, plan, ("top", "right", "bottom", "left"))
            _bent_frame(pen, plan.width, plan.height)
        bottom = height - 50.0
    else:
        canvas = Canvas(*PAGE)
        DRAWINGS[sheet.drawing](canvas)
        bottom = BOX[3] + 20.0
    text(canvas, sheet.title, (MARGIN + text_width(sheet.title, 13.0) / 2.0, MARGIN + 8.0), 13.0, 1.8)
    if sheet.compass.printed:
        x, y = MARGIN + 16.0, bottom + 30.0
        canvas.segment((x, y + 12.0), (x, y - 5.0), 3.0)
        canvas.polygon([(x, y - 14.0), (x + 5.0, y - 4.0), (x - 5.0, y - 4.0)])
        text(canvas, "N", (x, y - 22.0), 9.0, 1.4)
    if sheet.scale.printed and sheet.scale.text is not None:
        words = sheet.scale.text
        text(canvas, words, (MARGIN + 50.0 + text_width(words, 9.0) / 2.0, bottom + 20.0), 9.0, 1.3)
    pixels = canvas.pixels(paper, look.tint if look is not None else paper, ink)
    return encode(pixels, look.format if look is not None else "png"), ink, paper
