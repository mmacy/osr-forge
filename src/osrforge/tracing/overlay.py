"""The overlay renderer: a panel's reading drawn over its map image.

The overlay is how a person judges a trace: the walls, doors, cells, marks,
and area keys the stage read, drawn in colour over the scan they came from,
through the region's registration. It is the minimal form of the previews
that the tracing stage's later chunks build on.
"""

from PIL import Image

from osrforge.tracing.truth import GeometryTruth

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


def render_overlay(image: Image.Image, truth: GeometryTruth, region_id: str) -> Image.Image:
    """Draw the reading of a region's panel over the region's image.

    Every floor and water cell gets a translucent tint. Every wall, window,
    printed open line, door, locked door, and secret door is a line along its
    edge in its colour from `OVERLAY_COLORS`, at least 2 pixels wide, and a
    door's line is interrupted at the edge's middle by a short bar across it.
    Each mark's text is drawn at its cell's centre, and each area key of a
    level that uses the panel is drawn at the area's first cell, mapped back
    onto the panel through the panel's placement and turn. The entrance
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
    raise NotImplementedError("chunk: overlay")
