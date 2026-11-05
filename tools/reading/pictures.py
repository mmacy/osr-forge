"""The pictures Claude's requests carry: a page at the size the API reads, and a region's crop with its grid ticked.

A region crop is the region's registered grid with half a cell of map around it, resampled square through the
registration, in white gutters on all four sides. Every grid line has a tick in the gutters, and every few lines
(five on a large grid, two on a small one) a number: a column's number sits just right of its tick along the top and
the bottom, and a row's just below its tick down the left and the right, so the number names the cell that starts at
that line.
"""

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from osrforge.tracing.truth import Registration

from .clefask import _grid_points, _resample

__all__ = ["MAX_EDGE", "MAX_PIXELS", "fit_for_api", "png", "region_crop"]

MAX_EDGE = 2576
"""The longest edge, in pixels, the API reads an image at."""

MAX_PIXELS = 3_750_000
"""The most pixels the API reads an image at."""

TICK = (200, 0, 0)
GUTTER = 34


def png(image: Image.Image) -> bytes:
    """An image as PNG bytes."""
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def fit_for_api(image: Image.Image) -> Image.Image:
    """The image scaled down, proportions kept, to the size the API reads it at, so nothing is lost in transit."""
    width, height = image.size
    scale = min(1.0, MAX_EDGE / max(width, height), (MAX_PIXELS / (width * height)) ** 0.5)
    if scale >= 1.0:
        return image
    return image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)


def region_crop(rgb: np.ndarray, registration: Registration, title: str) -> Image.Image:
    """A region's grid with numbered ticks in white gutters, at the largest size the API reads.

    Args:
        rgb: The page image.
        registration: The region's grid.
        title: A label written in the top-left corner of the gutter, like `R1`.

    Returns:
        The picture.
    """
    columns, rows = registration.columns, registration.rows
    corners = np.array(registration.corners, dtype=np.float64)
    pitch = float(np.mean(np.diff(corners[..., 0], axis=1)) + np.mean(np.diff(corners[..., 1], axis=0))) / 2
    margin = 0.5
    span_x, span_y = columns + 2 * margin, rows + 2 * margin
    cell_px = pitch
    budget = min(
        (MAX_EDGE - 2 * GUTTER) / max(span_x, span_y), ((MAX_PIXELS) ** 0.5 - 2 * GUTTER) / (span_x * span_y) ** 0.5
    )
    cell_px = min(cell_px * 2.0, budget)
    width, height = round(span_x * cell_px), round(span_y * cell_px)
    gx = -margin + (np.arange(width) + 0.5) / cell_px
    gy = -margin + (np.arange(height) + 0.5) / cell_px
    px, py = _grid_points(registration, gx[None, :] * np.ones(height)[:, None], gy[:, None] * np.ones(width)[None, :])
    body = Image.fromarray(_resample(rgb, px, py))
    image = Image.new("RGB", (width + 2 * GUTTER, height + 2 * GUTTER), (255, 255, 255))
    image.paste(body, (GUTTER, GUTTER))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=15)
    step = 5 if max(columns, rows) > 24 else 2

    def at(i: float) -> float:
        return GUTTER + (i + margin) * cell_px

    for i in range(columns + 1):
        x = at(i)
        long = i % step == 0
        for top, bottom in (
            (GUTTER - (12 if long else 6), GUTTER),
            (GUTTER + height, GUTTER + height + (12 if long else 6)),
        ):
            draw.line([(x, top), (x, bottom)], fill=TICK, width=2 if long else 1)
        if long and i < columns:
            draw.text((x + 2, 2), str(i), fill=TICK, font=font)
            draw.text((x + 2, GUTTER + height + 14), str(i), fill=TICK, font=font)
    for j in range(rows + 1):
        y = at(j)
        long = j % step == 0
        for left, right in (
            (GUTTER - (12 if long else 6), GUTTER),
            (GUTTER + width, GUTTER + width + (12 if long else 6)),
        ):
            draw.line([(left, y), (right, y)], fill=TICK, width=2 if long else 1)
        if long and j < rows:
            draw.text((1, y + 1), str(j), fill=TICK, font=font)
            draw.text((GUTTER + width + 2, y + 1), str(j), fill=TICK, font=font)
    draw.text((2, GUTTER + height + 14 if columns < 3 else 2), "", fill=TICK, font=font)
    draw.rectangle([(0, 0), (GUTTER - 2, GUTTER - 2)], fill=(255, 255, 255))
    draw.text((2, 8), title, fill=(0, 0, 160), font=ImageFont.load_default(size=16))
    return image
