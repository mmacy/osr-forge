"""Crops of a map on its fitted grid, with the grid drawn on and the cells numbered.

`render_window` draws a window of cells for a person to read, and `render_item`
draws a zoomed crop around one cell or edge with the item boxed in red, the
crop a settled call points at.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .gridfit import GridFit

GRID_COLOUR = (40, 140, 255)
BOX_COLOUR = (255, 0, 0)
LABEL_COLOUR = (200, 0, 0)


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def render_window(
    rgb: np.ndarray,
    fit: GridFit,
    x0: int,
    y0: int,
    columns: int,
    rows: int,
    out: Path,
    cell_px: int = 72,
    margin_cells: float = 0.5,
) -> Path:
    """Draw a window of the map at a fixed magnification with the grid and its indices.

    Args:
        rgb: The map image.
        fit: Its grid fit.
        x0: The window's first column.
        y0: Its first row.
        columns: Its width in cells.
        rows: Its height in cells.
        out: Where to write the PNG.
        cell_px: Pixels per cell in the crop.
        margin_cells: Cells of map shown beyond the window on each side.

    Returns:
        The path written.
    """
    scale_x = cell_px / fit.x_axis.pitch
    scale_y = cell_px / fit.y_axis.pitch
    gx0, gy0 = fit.corner(x0 - margin_cells, y0 - margin_cells)
    gx1, gy1 = fit.corner(x0 + columns + margin_cells, y0 + rows + margin_cells)
    px0, py0 = int(np.floor(min(gx0, gx1))), int(np.floor(min(gy0, gy1)))
    px1, py1 = int(np.ceil(max(gx0, gx1))), int(np.ceil(max(gy0, gy1)))
    image = Image.fromarray(rgb.astype(np.uint8))
    crop = image.crop((px0, py0, px1, py1))
    crop = crop.resize((int((px1 - px0) * scale_x), int((py1 - py0) * scale_y)), Image.LANCZOS)
    gutter = 28
    canvas = Image.new("RGB", (crop.width + gutter, crop.height + gutter), (255, 255, 255))
    canvas.paste(crop, (gutter, gutter))
    draw = ImageDraw.Draw(canvas)
    font = _font(14)

    def point(i: float, j: float) -> tuple[float, float]:
        x, y = fit.corner(i, j)
        return (x - px0) * scale_x + gutter, (y - py0) * scale_y + gutter

    for i in range(x0, x0 + columns + 1):
        top = point(i, y0 - margin_cells)
        bottom = point(i, y0 + rows + margin_cells)
        draw.line([top, bottom], fill=GRID_COLOUR, width=1)
        if i < x0 + columns:
            cx = (point(i, y0)[0] + point(i + 1, y0)[0]) / 2
            draw.text((cx - 8, 4), str(i), fill=LABEL_COLOUR, font=font)
    for j in range(y0, y0 + rows + 1):
        left = point(x0 - margin_cells, j)
        right = point(x0 + columns + margin_cells, j)
        draw.line([left, right], fill=GRID_COLOUR, width=1)
        if j < y0 + rows:
            cy = (point(x0, j)[1] + point(x0, j + 1)[1]) / 2
            draw.text((2, cy - 8), str(j), fill=LABEL_COLOUR, font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def render_item(
    rgb: np.ndarray,
    fit: GridFit,
    cell: tuple[int, int],
    side: str | None,
    out: Path,
    cell_px: int = 128,
    reach: float = 1.5,
) -> Path:
    """Draw a zoomed crop around one cell or one edge, with the item boxed in red.

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
        box = ((x, y), (x + 1, y + 1))
    elif side == "north":
        cx, cy = x + 0.5, float(y)
        box = ((x, y - 0.3), (x + 1, y + 0.3))
    elif side == "south":
        cx, cy = x + 0.5, y + 1.0
        box = ((x, y + 0.7), (x + 1, y + 1.3))
    elif side == "west":
        cx, cy = float(x), y + 0.5
        box = ((x - 0.3, y), (x + 0.3, y + 1))
    else:
        cx, cy = x + 1.0, y + 0.5
        box = ((x + 0.7, y), (x + 1.3, y + 1))
    scale_x = cell_px / fit.x_axis.pitch
    scale_y = cell_px / fit.y_axis.pitch
    gx0, gy0 = fit.corner(cx - reach, cy - reach)
    gx1, gy1 = fit.corner(cx + reach, cy + reach)
    px0, py0 = int(np.floor(min(gx0, gx1))), int(np.floor(min(gy0, gy1)))
    px1, py1 = int(np.ceil(max(gx0, gx1))), int(np.ceil(max(gy0, gy1)))
    image = Image.fromarray(rgb.astype(np.uint8))
    crop = image.crop((px0, py0, px1, py1))
    crop = crop.resize((int((px1 - px0) * scale_x), int((py1 - py0) * scale_y)), Image.LANCZOS)
    draw = ImageDraw.Draw(crop)

    def point(i: float, j: float) -> tuple[float, float]:
        gx, gy = fit.corner(i, j)
        return (gx - px0) * scale_x, (gy - py0) * scale_y

    for i in range(int(np.floor(cx - reach)), int(np.ceil(cx + reach)) + 1):
        draw.line([point(i, cy - reach), point(i, cy + reach)], fill=GRID_COLOUR, width=1)
    for j in range(int(np.floor(cy - reach)), int(np.ceil(cy + reach)) + 1):
        draw.line([point(cx - reach, j), point(cx + reach, j)], fill=GRID_COLOUR, width=1)
    (ax, ay), (bx, by) = box
    draw.rectangle([point(ax, ay), point(bx, by)], outline=BOX_COLOUR, width=3)
    font = _font(16)
    label = f"{x},{y}" + (f":{side}" if side else "")
    draw.text((4, 4), label, fill=LABEL_COLOUR, font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out)
    return out


EDGE_COLOURS = {
    "w": (220, 0, 0),
    "n": (0, 170, 220),
    "d": (0, 0, 255),
    "l": (0, 0, 160),
    "s": (200, 0, 200),
    "p": (230, 180, 0),
}


def render_reading(
    rgb: np.ndarray,
    fit: GridFit,
    cells: list[str] | tuple[str, ...],
    vertical: list[str] | tuple[str, ...],
    horizontal: list[str] | tuple[str, ...],
    partial: set[tuple[int, int]],
    x0: int,
    y0: int,
    columns: int,
    rows: int,
    out: Path,
    cell_px: int = 60,
) -> Path:
    """Draw a reading over a window of the map: floor cells tinted, blocked and door edges drawn in colour.

    Args:
        rgb: The map image.
        fit: Its grid fit.
        cells: The panel's cell rows.
        vertical: The panel's vertical edge rows.
        horizontal: The panel's horizontal edge rows.
        partial: The partly floor cells.
        x0: The window's first column.
        y0: Its first row.
        columns: Its width in cells.
        rows: Its height in cells.
        out: Where to write the PNG.
        cell_px: Pixels per cell in the crop.

    Returns:
        The path written.
    """
    path = render_window(rgb, fit, x0, y0, columns, rows, out, cell_px=cell_px, margin_cells=0.5)
    canvas = Image.open(path).convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    scale_x = cell_px / fit.x_axis.pitch
    scale_y = cell_px / fit.y_axis.pitch
    gx0, gy0 = fit.corner(x0 - 0.5, y0 - 0.5)
    gx1, gy1 = fit.corner(x0 + columns + 0.5, y0 + rows + 0.5)
    px0, py0 = int(np.floor(min(gx0, gx1))), int(np.floor(min(gy0, gy1)))
    gutter = 28

    def point(i: float, j: float) -> tuple[float, float]:
        x, y = fit.corner(i, j)
        return (x - px0) * scale_x + gutter, (y - py0) * scale_y + gutter

    for y in range(y0, min(y0 + rows, len(cells))):
        for x in range(x0, min(x0 + columns, len(cells[0]))):
            if x < 0 or y < 0:
                continue
            if cells[y][x] == ".":
                a, b = point(x + 0.08, y + 0.08), point(x + 0.92, y + 0.92)
                draw.rectangle([a, b], fill=(0, 200, 0, 60))
            if (x, y) in partial:
                a, b = point(x + 0.4, y + 0.4), point(x + 0.6, y + 0.6)
                draw.ellipse([a, b], fill=(255, 140, 0, 220))
    for y in range(y0, min(y0 + rows, len(vertical))):
        for i in range(x0, min(x0 + columns + 1, len(vertical[0]))):
            if y < 0 or i < 0:
                continue
            letter = vertical[y][i]
            if letter in EDGE_COLOURS:
                draw.line([point(i, y + 0.05), point(i, y + 0.95)], fill=EDGE_COLOURS[letter] + (230,), width=5)
    for j in range(y0, min(y0 + rows + 1, len(horizontal))):
        for x in range(x0, min(x0 + columns, len(horizontal[0]))):
            if j < 0 or x < 0:
                continue
            letter = horizontal[j][x]
            if letter in EDGE_COLOURS:
                draw.line([point(x + 0.05, j), point(x + 0.95, j)], fill=EDGE_COLOURS[letter] + (230,), width=5)
    Image.alpha_composite(canvas, overlay).convert("RGB").save(out)
    return out
