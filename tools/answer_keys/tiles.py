"""Windows of a registered region, and the crops a reader sees: tick-marked windows and boxed items.

A *window* is a block of cells of one region, 12 by 12 unless the region is smaller. `tile` covers a region with
windows laid side by side, the last one in each direction moved back so it ends at the region's edge, so two
windows can overlap by a few cells and read those cells twice.

`render_window` draws a window for Claude to read: the map magnified to a fixed number of pixels per cell, a
margin of map around the window, and red ticks with the grid indices in a white gutter along the top and the
left. Nothing is drawn over the map itself. `render_item` draws a zoomed crop around one cell or edge with the
item boxed in red and the same ticks, for the settler, the adversarial pass, and the owner's sample.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .gridfit import GridFit

__all__ = ["Window", "render_item", "render_rect", "render_window", "tile", "window_starts"]

TICK_COLOUR = (220, 0, 0)
BOX_COLOUR = (255, 0, 0)
GUTTER = 36
"""Pixels of white gutter along the top and the left of a crop, where the ticks and their numbers go."""


@dataclass(frozen=True)
class Window:
    """One window of a region: its first column and row, and its size in cells."""

    x0: int
    y0: int
    columns: int
    rows: int

    @property
    def id(self) -> str:
        """The window's name in file names: `<x0>-<y0>`."""
        return f"{self.x0}-{self.y0}"

    def contains(self, cell: tuple[int, int]) -> bool:
        """Whether a cell lies in the window."""
        x, y = cell
        return self.x0 <= x < self.x0 + self.columns and self.y0 <= y < self.y0 + self.rows


def window_starts(length: int, size: int) -> list[int]:
    """The first indices of the windows that cover a length.

    Windows start every `size` cells; the last starts at `length - size`, so it ends at the edge, when the
    earlier windows don't reach it. A length no longer than `size` is one window at 0.

    Args:
        length: The region's size along one axis, in cells.
        size: The window size.

    Returns:
        The starts, ascending.
    """
    if length <= size:
        return [0]
    starts = list(range(0, length - size, size))
    if starts[-1] != length - size:
        starts.append(length - size)
    return starts


def tile(columns: int, rows: int, size: int = 12) -> list[Window]:
    """Cover a region with windows.

    Args:
        columns: The region's width in cells.
        rows: Its height.
        size: The window size, 12 unless a test asks for another.

    Returns:
        The windows in row-major order.
    """
    width = min(size, columns)
    height = min(size, rows)
    return [Window(x0, y0, width, height) for y0 in window_starts(rows, size) for x0 in window_starts(columns, size)]


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


class _Canvas:
    """A magnified crop of the map around a grid rectangle, with a gutter for ticks."""

    def __init__(
        self,
        rgb: np.ndarray,
        fit: GridFit,
        gx0: float,
        gy0: float,
        gx1: float,
        gy1: float,
        cell_px: int,
    ) -> None:
        self.fit = fit
        self.scale_x = cell_px / fit.x_axis.pitch
        self.scale_y = cell_px / fit.y_axis.pitch
        corners = [fit.corner(gx, gy) for gx in (gx0, gx1) for gy in (gy0, gy1)]
        height, width = rgb.shape[:2]
        self.px0 = int(np.floor(min(c[0] for c in corners)))
        self.py0 = int(np.floor(min(c[1] for c in corners)))
        px1 = int(np.ceil(max(c[0] for c in corners)))
        py1 = int(np.ceil(max(c[1] for c in corners)))
        # Pad with white where the crop runs off the image, so a window at the frame keeps its margin.
        padded = np.full((py1 - self.py0, px1 - self.px0, 3), 255, dtype=np.uint8)
        sx0, sy0 = max(self.px0, 0), max(self.py0, 0)
        sx1, sy1 = min(px1, width), min(py1, height)
        if sx1 > sx0 and sy1 > sy0:
            padded[sy0 - self.py0 : sy1 - self.py0, sx0 - self.px0 : sx1 - self.px0] = rgb[sy0:sy1, sx0:sx1]
        crop = Image.fromarray(padded)
        size = (max(1, round(crop.width * self.scale_x)), max(1, round(crop.height * self.scale_y)))
        crop = crop.resize(size, Image.Resampling.LANCZOS)
        self.image = Image.new("RGB", (crop.width + 2 * GUTTER, crop.height + 2 * GUTTER), (255, 255, 255))
        self.image.paste(crop, (GUTTER, GUTTER))
        self.draw = ImageDraw.Draw(self.image)

    def point(self, gx: float, gy: float) -> tuple[float, float]:
        """The canvas position of a grid point."""
        x, y = self.fit.corner(gx, gy)
        return (x - self.px0) * self.scale_x + GUTTER, (y - self.py0) * self.scale_y + GUTTER

    def ticks(self, columns: range, rows: range, label_last: bool) -> None:
        """Draw ticks for grid lines in all four gutters, each numbered by the cell that starts at it.

        A tick's number sits just right of (or below) the tick, top and bottom, left and right, so a reader
        can count the window's rows and columns from either side.
        """
        font = _font(13)
        width, height = self.image.size
        for i in columns:
            x, _ = self.point(i, rows.start)
            self.draw.line([(x, GUTTER - 12), (x, GUTTER + 6)], fill=TICK_COLOUR, width=2)
            self.draw.line([(x, height - GUTTER - 6), (x, height - GUTTER + 12)], fill=TICK_COLOUR, width=2)
            if i < columns.stop - 1 or label_last:
                self.draw.text((x + 2, 2), str(i), fill=TICK_COLOUR, font=font)
                self.draw.text((x + 2, height - GUTTER + 14), str(i), fill=TICK_COLOUR, font=font)
        for j in rows:
            _, y = self.point(columns.start, j)
            self.draw.line([(GUTTER - 12, y), (GUTTER + 6, y)], fill=TICK_COLOUR, width=2)
            self.draw.line([(width - GUTTER - 6, y), (width - GUTTER + 12, y)], fill=TICK_COLOUR, width=2)
            if j < rows.stop - 1 or label_last:
                self.draw.text((2, y + 1), str(j), fill=TICK_COLOUR, font=font)
                self.draw.text((width - GUTTER + 14, y + 1), str(j), fill=TICK_COLOUR, font=font)

    def box(self, gx0: float, gy0: float, gx1: float, gy1: float, width: int = 3) -> None:
        """Draw a red box around a grid rectangle."""
        a = self.point(gx0, gy0)
        b = self.point(gx1, gy1)
        self.draw.rectangle(
            [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])], outline=BOX_COLOUR, width=width
        )

    def save(self, out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(out)
        return out


def render_window(
    rgb: np.ndarray, fit: GridFit, window: Window, out: Path, cell_px: int = 80, margin: float = 1.0
) -> Path:
    """Draw a window for a reader, with the grid's ticks in the gutters and a margin of map around it.

    The ticks mark the window's own grid lines, from its first column to the line after its last column, and the
    same for rows. A tick's number is the index of the cell that starts at it, so the last line of the window
    is unnumbered. The margin is map beyond the window on every side; it has no ticks.

    Args:
        rgb: The map image.
        fit: Its grid fit.
        window: The window.
        out: Where to write the PNG.
        cell_px: Pixels per cell in the crop.
        margin: Cells of map beyond the window on each side.

    Returns:
        The path written.
    """
    canvas = _Canvas(
        rgb,
        fit,
        window.x0 - margin,
        window.y0 - margin,
        window.x0 + window.columns + margin,
        window.y0 + window.rows + margin,
        cell_px,
    )
    canvas.ticks(range(window.x0, window.x0 + window.columns + 1), range(window.y0, window.y0 + window.rows + 1), False)
    return canvas.save(out)


def render_item(
    rgb: np.ndarray,
    fit: GridFit,
    cell: tuple[int, int],
    side: str | None,
    out: Path,
    cell_px: int,
    reach: float,
) -> Path:
    """Draw a zoomed crop around one cell or edge with the item boxed in red and every grid line in view ticked.

    Args:
        rgb: The map image.
        fit: Its grid fit.
        cell: The cell `(x, y)`.
        side: The side of the cell for an edge, or `None` for the cell itself.
        out: Where to write the PNG.
        cell_px: Pixels per cell in the crop.
        reach: Cells shown around the item's centre on each side.

    Returns:
        The path written.
    """
    x, y = cell
    if side is None:
        cx, cy = x + 0.5, y + 0.5
        box = (x + 0.02, y + 0.02, x + 0.98, y + 0.98)
    elif side == "north":
        cx, cy = x + 0.5, float(y)
        box = (x + 0.02, y - 0.3, x + 0.98, y + 0.3)
    elif side == "south":
        cx, cy = x + 0.5, y + 1.0
        box = (x + 0.02, y + 0.7, x + 0.98, y + 1.3)
    elif side == "west":
        cx, cy = float(x), y + 0.5
        box = (x - 0.3, y + 0.02, x + 0.3, y + 0.98)
    else:
        cx, cy = x + 1.0, y + 0.5
        box = (x + 0.7, y + 0.02, x + 1.3, y + 0.98)
    canvas = _Canvas(rgb, fit, cx - reach, cy - reach, cx + reach, cy + reach, cell_px)
    first_column = int(np.ceil(cx - reach))
    last_column = int(np.floor(cx + reach))
    first_row = int(np.ceil(cy - reach))
    last_row = int(np.floor(cy + reach))
    canvas.ticks(range(first_column, last_column + 1), range(first_row, last_row + 1), True)
    canvas.box(*box, width=3 if cell_px >= 96 else 2)
    return canvas.save(out)


def render_rect(rgb: np.ndarray, fit: GridFit, rect: tuple[int, int, int, int], out: Path, cell_px: int) -> Path:
    """Draw a crop around a rectangle of cells with the rectangle boxed in red and the grid lines in view ticked.

    Args:
        rgb: The map image.
        fit: Its grid fit.
        rect: The rectangle `(x, y, width, height)` in cells.
        out: Where to write the PNG.
        cell_px: Pixels per cell in the crop.

    Returns:
        The path written.
    """
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    reach = max(w, h) / 2 + 1.5
    canvas = _Canvas(rgb, fit, cx - reach, cy - reach, cx + reach, cy + reach, cell_px)
    canvas.ticks(
        range(int(np.ceil(cx - reach)), int(np.floor(cx + reach)) + 1),
        range(int(np.ceil(cy - reach)), int(np.floor(cy + reach)) + 1),
        True,
    )
    canvas.box(x + 0.02, y + 0.02, x + w - 0.02, y + h - 0.02, width=3)
    return canvas.save(out)
