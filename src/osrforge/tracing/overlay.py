"""The overlay renderer: a panel's reading drawn over its map image.

The overlay is how a person judges a trace: the walls, doors, cells, marks,
and area keys the stage read, drawn in colour over the scan they came from,
through the region's registration. It is the minimal form of the previews
that the tracing stage's later chunks build on.
"""

import math

from PIL import Image, ImageDraw, ImageFont

from osrforge.tracing.truth import (
    GeometryTruth,
    LevelPanel,
    Panel,
    PanelRegion,
    Region,
    Registration,
    parse_cell,
    rotate_side,
    rotated_size,
)

__all__ = ["OVERLAY_COLORS", "render_overlay"]

OVERLAY_COLORS: dict[str, tuple[int, int, int]] = {
    "floor": (255, 230, 120),
    "water": (120, 180, 255),
    "wall": (220, 30, 30),
    "window": (30, 90, 220),
    "printed_line": (120, 120, 120),
    "door": (30, 160, 60),
    "locked": (150, 30, 160),
    "secret": (240, 140, 20),
    "label": (0, 0, 0),
    "entrance": (0, 120, 0),
    "passage": (200, 0, 120),
    "transition": (0, 90, 200),
}
"""The colour of each thing the overlay draws, as RGB."""

_TINT_ALPHA = 90
"""The opacity of a cell's tint, out of 255."""

_EDGE_COLORS: dict[str, str] = {
    "w": "wall",
    "n": "window",
    "p": "printed_line",
    "d": "door",
    "l": "locked",
    "s": "secret",
}
"""The overlay colour name of each edge letter that is drawn. Open edges and unread edges are left bare."""

_DOOR_LETTERS = "dls"
_SIDE_ENDS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "north": ((0, 0), (1, 0)),
    "south": ((0, 1), (1, 1)),
    "west": ((0, 0), (0, 1)),
    "east": ((1, 0), (1, 1)),
}
"""The two corners of each side of a cell, as offsets from the cell's north-west corner."""

_NEIGHBOUR: dict[str, tuple[int, int]] = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}

Point = tuple[float, float]
Rgba = tuple[int, int, int, int]


def render_overlay(image: Image.Image, truth: GeometryTruth, region_id: str) -> Image.Image:
    """Draw the reading of a region's panel over the region's image.

    Every floor and water cell gets a translucent tint. Every wall, window,
    printed open line, door, locked door, and secret door is a line along its
    edge in its colour from `OVERLAY_COLORS`, at least 2 pixels wide, and a
    door's line is interrupted at the edge's middle by a short bar across it.
    Each mark's text is drawn at its cell's centre, and each area key of a
    level that uses the panel is drawn at the area's first cell, mapped back
    onto the panel through the panel's placement and turn. Text is drawn
    without antialiasing, so its pixels are exactly the label colour. The entrance
    is a circle on its cell, each transition a triangle, and each passage a
    bar on its side. Everything is positioned through the region's
    registration, so the overlay lies on the printed map.

    Args:
        image: The region's image. It isn't changed.
        truth: The document with the region, its panel, and the levels that use it.
        region_id: The region to draw.

    Returns:
        A new image of the same size and mode with the overlay drawn. The same
        inputs always give the same pixels.

    Raises:
        KeyError: If no region, or no panel, has that id.
        ValueError: If the region isn't registered.
    """
    region = truth.region(region_id)
    panel = next((candidate for candidate in truth.panels if _part_of(candidate, region_id) is not None), None)
    if panel is None:
        raise KeyError(region_id)
    if region.registration is None:
        raise ValueError(f"region {region_id!r} isn't registered, so there is no grid to draw on")
    part = _part_of(panel, region_id)
    assert part is not None
    canvas = _Canvas(image.size, region, region.registration, panel, part)
    canvas.tint()
    canvas.edges()
    canvas.marks()
    for dungeon in truth.dungeons:
        for level in dungeon.levels:
            if level.verdict != "traced" or panel.scale_feet == 5:
                continue
            for placement in level.panels:
                if placement.panel != panel.id:
                    continue
                for key, area in level.areas.items():
                    first = min((parse_cell(text) for text in area.cells), key=lambda cell: (cell[1], cell[0]))
                    cell = _to_panel(placement, panel, first)
                    if cell is not None:
                        canvas.label(cell, key)
                if dungeon.entrance is not None and dungeon.entrance.level == level.number:
                    cell = _to_panel(placement, panel, parse_cell(dungeon.entrance.cell))
                    if cell is not None:
                        canvas.entrance(cell)
                for transition in level.transitions:
                    cell = _to_panel(placement, panel, parse_cell(transition.cell))
                    if cell is not None:
                        canvas.transition(cell)
                for passage in level.passages:
                    cell = _to_panel(placement, panel, parse_cell(passage.cell))
                    if cell is not None:
                        side = rotate_side(passage.side, (360 - placement.rotation) % 360)
                        canvas.passage(cell, side)
    base = image.convert("RGBA")
    return Image.alpha_composite(base, canvas.layer).convert(image.mode)


def _part_of(panel: Panel, region_id: str) -> PanelRegion | None:
    """The panel's placement of a region, or `None` when the panel doesn't hold the region."""
    return next((part for part in panel.regions if part.region == region_id), None)


def _to_panel(placement: LevelPanel, panel: Panel, cell: tuple[int, int]) -> tuple[int, int] | None:
    """The panel cell under a level cell, undoing the panel's origin and turn, or `None` off the panel."""
    tx, ty = cell[0] - placement.origin[0], cell[1] - placement.origin[1]
    width, height = panel.width, panel.height
    turned_width, turned_height = rotated_size((width, height), placement.rotation)
    if not (0 <= tx < turned_width and 0 <= ty < turned_height):
        return None
    if placement.rotation == 90:
        return (ty, height - 1 - tx)
    if placement.rotation == 180:
        return (width - 1 - tx, height - 1 - ty)
    if placement.rotation == 270:
        return (width - 1 - ty, tx)
    return (tx, ty)


class _Canvas:
    """A transparent layer the size of the image, with drawing helpers that work in panel coordinates."""

    def __init__(
        self, size: tuple[int, int], region: Region, registration: Registration, panel: Panel, part: PanelRegion
    ) -> None:
        self.layer = Image.new("RGBA", size, (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.layer)
        self.draw.fontmode = "1"
        self.registration = registration
        self.panel = panel
        self.origin = part.origin
        self.factor = 2 if region.scale.feet == 5 and panel.scale_feet == 10 else 1
        self.columns = -(-registration.columns // self.factor)
        self.rows = -(-registration.rows // self.factor)
        corner_a = registration.corners[0][0]
        corner_b = registration.corners[-1][-1]
        square = math.hypot(corner_b[0] - corner_a[0], corner_b[1] - corner_a[1]) / math.hypot(
            registration.columns, registration.rows
        )
        self.cell_px = square * self.factor
        self.line_width = max(3, round(self.cell_px / 25))
        self.font = ImageFont.load_default(max(10, round(self.cell_px * 0.45)))

    def pixel(self, x: float, y: float) -> Point:
        """The image position of a panel point."""
        return self.registration.pixel(((x - self.origin[0]) * self.factor, (y - self.origin[1]) * self.factor))

    def inside(self, cell: tuple[int, int]) -> bool:
        """Whether a panel cell lies on this region's grid."""
        return 0 <= cell[0] - self.origin[0] < self.columns and 0 <= cell[1] - self.origin[1] < self.rows

    def tint(self) -> None:
        """Tint every floor and water cell of the region."""
        for y, row in enumerate(self.panel.cells):
            for x, letter in enumerate(row):
                if letter not in ".~" or not self.inside((x, y)):
                    continue
                red, green, blue = OVERLAY_COLORS["floor" if letter == "." else "water"]
                corners = [self.pixel(x + dx, y + dy) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
                self.draw.polygon(corners, fill=(red, green, blue, _TINT_ALPHA))

    def edges(self) -> None:
        """Draw every wall, window, printed line, and door."""
        doors: list[tuple[Point, Point, tuple[int, int, int]]] = []
        for (x, y), side in self.panel.edges.positions():
            letter = self.panel.edges.at((x, y), side)
            name = _EDGE_COLORS.get(letter)
            dx, dy = _NEIGHBOUR[side]
            if name is None or not (self.inside((x, y)) or self.inside((x + dx, y + dy))):
                continue
            (ax, ay), (bx, by) = _SIDE_ENDS[side]
            start, end = self.pixel(x + ax, y + ay), self.pixel(x + bx, y + by)
            color = OVERLAY_COLORS[name]
            if letter in _DOOR_LETTERS:
                doors.append((start, end, color))
                self.line(start, _along(start, end, 0.35), color)
                self.line(_along(start, end, 0.65), end, color)
            else:
                self.line(start, end, color)
        for start, end, color in doors:
            self.bar(start, end, color)

    def line(self, start: Point, end: Point, color: tuple[int, int, int], width: int | None = None) -> None:
        """Draw a straight line of at least the line width."""
        self.draw.line(
            [(round(start[0]), round(start[1])), (round(end[0]), round(end[1]))],
            fill=(*color, 255),
            width=width or self.line_width,
        )

    def bar(self, start: Point, end: Point, color: tuple[int, int, int]) -> None:
        """Draw the short bar across the middle of an edge that a door shows."""
        mid = _along(start, end, 0.5)
        length = math.hypot(end[0] - start[0], end[1] - start[1]) or 1.0
        reach = max(4.0, 0.2 * self.cell_px)
        normal = (-(end[1] - start[1]) / length * reach, (end[0] - start[0]) / length * reach)
        self.line((mid[0] - normal[0], mid[1] - normal[1]), (mid[0] + normal[0], mid[1] + normal[1]), color)

    def centre(self, cell: tuple[int, int]) -> Point:
        """The image position of a cell's centre."""
        return self.pixel(cell[0] + 0.5, cell[1] + 0.5)

    def label(self, cell: tuple[int, int], text: str) -> None:
        """Draw text at a cell's centre in the label colour, without antialiasing."""
        if not self.inside(cell):
            return
        x, y = self.centre(cell)
        self.draw.text((round(x), round(y)), text, fill=(*OVERLAY_COLORS["label"], 255), font=self.font, anchor="mm")

    def marks(self) -> None:
        """Draw each mark's text."""
        for mark in self.panel.marks:
            self.label(parse_cell(mark.cell), mark.text)

    def entrance(self, cell: tuple[int, int]) -> None:
        """Draw the entrance as a circle on its cell."""
        if not self.inside(cell):
            return
        x, y = self.centre(cell)
        radius = 0.3 * self.cell_px
        self.draw.ellipse(
            [(round(x - radius), round(y - radius)), (round(x + radius), round(y + radius))],
            fill=(*OVERLAY_COLORS["entrance"], 255),
        )

    def transition(self, cell: tuple[int, int]) -> None:
        """Draw a transition as a triangle on its cell."""
        if not self.inside(cell):
            return
        x, y = self.centre(cell)
        radius = 0.3 * self.cell_px
        corners = [
            (round(x), round(y - radius)),
            (round(x - radius * 0.87), round(y + radius * 0.5)),
            (round(x + radius * 0.87), round(y + radius * 0.5)),
        ]
        self.draw.polygon(corners, fill=(*OVERLAY_COLORS["transition"], 255))

    def passage(self, cell: tuple[int, int], side: str) -> None:
        """Draw a passage as a bar along the side it crosses."""
        if not self.inside(cell):
            return
        (ax, ay), (bx, by) = _SIDE_ENDS[side]
        start, end = self.pixel(cell[0] + ax, cell[1] + ay), self.pixel(cell[0] + bx, cell[1] + by)
        self.line(
            _along(start, end, 0.2),
            _along(start, end, 0.8),
            OVERLAY_COLORS["passage"],
            width=max(5, self.line_width + 2),
        )


def _along(start: Point, end: Point, fraction: float) -> Point:
    """The point a fraction of the way from one end of a segment to the other."""
    return (start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction)
