"""The stage's ink measurements, as the screening prototypes them: floor in each cell, and lines on each edge.

This is not the answer-key classifier (`tools.answer_keys`), which gives an answer key one of its two readings. It
shares no code with it, so the keys stay independent of what the stage measures. It uses numpy alone.

Every pixel gets a *darkness* from 0 (paper) to 1 (ink): the style's channel, scaled between the region's paper
level (its 95th percentile) and its ink level (its 2nd percentile). A pixel is *paper* below the style's paper
threshold. The *fill* is what is left of the non-paper pixels after an opening by a square a quarter of a cell wide,
so grid lines, walls, labels, and other strokes drop out and solid areas (rock, water, a void) stay. A cell's floor
fraction is its paper over its paper and fill, inside its inner 76 percent. Strokes count for neither.

An edge is measured on samples along it (from 0.1 to 0.9 of its length) and across it (a quarter of a cell each way):

- `width`: the median, along the edge, of the dark run across the line within a quarter cell, in cells
- `heavy`: the share of positions along the edge where that run is at least `HEAVY` cells wide
- `line`: the share of positions along the edge with dark ink within `NEAR` cells of the line
- `floor_low` and `floor_high`: the two cells' floor fractions, the smaller first
"""

from dataclasses import dataclass
from functools import cached_property

import numpy as np

from osrforge.tracing.truth import Registration

__all__ = ["HEAVY", "INK_STYLES", "NEAR", "EdgeInk", "InkStyle", "MapInk", "unsettled_cells"]

HEAVY = 0.07
"""Cells. A dark run across the line at least this wide is heavier than a printed grid line."""

NEAR = 0.08
"""Cells. Ink this close to the line counts as on it."""

_ALONG = 24
_ACROSS = 25
_CELL_SAMPLES = 16
_INNER = 0.12


@dataclass(frozen=True)
class InkStyle:
    """How one style's ink is read."""

    channel: str
    """`red` for blue ink on paper, `luminance` otherwise."""

    paper: float
    """The darkness below which a pixel is paper."""

    dark: float = 0.5
    """The darkness at or above which a pixel is ink, for edge lines."""

    close: float = 0.0
    """Cells. A closing of the non-paper pixels before the opening, so that hatching becomes solid fill."""

    opening: float = 0.25
    """Cells. The width of the square that opens the non-paper pixels into fill."""


INK_STYLES: dict[str, InkStyle] = {
    "tsr_blue": InkStyle(channel="red", paper=0.35),
    "black_white": InkStyle(channel="luminance", paper=0.06, close=0.06),
    "dark_rock": InkStyle(channel="luminance", paper=0.06),
    "cave": InkStyle(channel="luminance", paper=0.06),
}
"""Each style's settings."""


@dataclass(frozen=True)
class EdgeInk:
    """One edge's ink measurements."""

    width: float
    heavy: float
    line: float
    floor_low: float
    floor_high: float

    def vector(self) -> list[float]:
        """The line measurements the calibration uses, in a fixed order: width (capped at 0.3), heavy, and line.

        The floor fractions stay out: on the edges the calibration sees, both cells are floor, so they vary too
        little between maps to fit a weight that carries from one map to another.
        """
        return [min(self.width, 0.3), self.heavy, self.line]


def _box_sum(mask: np.ndarray, size: int) -> np.ndarray:
    """Each pixel's count of true pixels in the `size` by `size` square centred on it, zero beyond the image."""
    pad = size // 2
    padded = np.pad(mask.astype(np.int32), ((pad + 1, size - pad - 1), (pad + 1, size - pad - 1)))
    table = padded.cumsum(0).cumsum(1)
    height, width = mask.shape
    return (
        table[size : size + height, size : size + width]
        - table[0:height, size : size + width]
        - table[size : size + height, 0:width]
        + table[0:height, 0:width]
    )


def _erode(mask: np.ndarray, size: int) -> np.ndarray:
    return _box_sum(mask, size) == size * size


def _dilate(mask: np.ndarray, size: int) -> np.ndarray:
    return _box_sum(mask, size) > 0


class MapInk:
    """One region's ink, measured on its registration."""

    def __init__(self, rgb: np.ndarray, registration: Registration, style: InkStyle) -> None:
        """Measure a region.

        Args:
            rgb: The map image, height by width by 3.
            registration: The region's grid.
            style: The style's settings.
        """
        self.registration = registration
        self.style = style
        self.corners = np.array(registration.corners, dtype=np.float64)
        values = rgb[..., 0].astype(np.float64) if style.channel == "red" else rgb.astype(np.float64).mean(axis=2)
        xs, ys = self.corners[..., 0], self.corners[..., 1]
        height, width = values.shape
        x0, x1 = int(max(0, np.floor(xs.min()))), int(min(width, np.ceil(xs.max()) + 1))
        y0, y1 = int(max(0, np.floor(ys.min()))), int(min(height, np.ceil(ys.max()) + 1))
        inside = values[y0:y1, x0:x1]
        paper_level, ink_level = np.percentile(inside, 95), np.percentile(inside, 2)
        self.darkness = np.clip((paper_level - values) / max(1.0, paper_level - ink_level), 0.0, 1.0)
        self.pitch = (
            float(np.mean(np.diff(self.corners[..., 0], axis=1)) + np.mean(np.diff(self.corners[..., 1], axis=0))) / 2
        )

    @cached_property
    def fill(self) -> np.ndarray:
        """The solid areas: non-paper pixels opened by a square a quarter of a cell wide."""
        mask = self.darkness >= self.style.paper
        if self.style.close > 0:
            size = max(3, round(self.style.close * self.pitch) | 1)
            mask = _erode(_dilate(mask, size), size)
        size = max(3, round(self.style.opening * self.pitch) | 1)
        return _dilate(_erode(mask, size), size)

    @cached_property
    def paper(self) -> np.ndarray:
        """The paper pixels."""
        return self.darkness < self.style.paper

    def pixels(self, gx: np.ndarray, gy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Grid points' pixel positions, bilinear within each grid cell and extended beyond the grid."""
        rows, columns = self.corners.shape[0] - 1, self.corners.shape[1] - 1
        i = np.clip(np.floor(gx).astype(int), 0, columns - 1)
        j = np.clip(np.floor(gy).astype(int), 0, rows - 1)
        u, v = gx - i, gy - j
        c = self.corners
        p00, p10, p01, p11 = c[j, i], c[j, i + 1], c[j + 1, i], c[j + 1, i + 1]
        point = (
            (1 - u)[..., None] * (1 - v)[..., None] * p00
            + u[..., None] * (1 - v)[..., None] * p10
            + (1 - u)[..., None] * v[..., None] * p01
            + u[..., None] * v[..., None] * p11
        )
        return point[..., 0], point[..., 1]

    def _sample(self, image: np.ndarray, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
        px, py = self.pixels(gx, gy)
        height, width = image.shape
        xi = np.clip(np.rint(px).astype(int), 0, width - 1)
        yi = np.clip(np.rint(py).astype(int), 0, height - 1)
        return image[yi, xi]

    def floor_fractions(self) -> np.ndarray:
        """Every cell's floor fraction, rows by columns. A cell with neither paper nor fill inside counts as floor."""
        rows, columns = self.corners.shape[0] - 1, self.corners.shape[1] - 1
        steps = (np.arange(_CELL_SAMPLES) + 0.5) / _CELL_SAMPLES * (1 - 2 * _INNER) + _INNER
        out = np.ones((rows, columns))
        for y in range(rows):
            gy = y + steps[:, None] * np.ones(_CELL_SAMPLES)[None, :]
            for x in range(columns):
                gx = x + np.ones(_CELL_SAMPLES)[:, None] * steps[None, :]
                paper = self._sample(self.paper, gx, gy).sum()
                fill = self._sample(self.fill, gx, gy).sum()
                if paper + fill:
                    out[y, x] = paper / (paper + fill)
        return out

    def ink_inside(self, cell: tuple[int, int]) -> float:
        """The share of a cell's inner 76 percent that is dark ink and not fill: a label or a symbol drawn in it."""
        x, y = cell
        steps = (np.arange(_CELL_SAMPLES) + 0.5) / _CELL_SAMPLES * (1 - 2 * _INNER) + _INNER
        gx = x + np.ones(_CELL_SAMPLES)[:, None] * steps[None, :]
        gy = y + steps[:, None] * np.ones(_CELL_SAMPLES)[None, :]
        dark = self._sample(self.darkness, gx, gy) >= self.style.dark
        return float((dark & ~self._sample(self.fill, gx, gy)).mean())

    def fill_tone(self, cell: tuple[int, int]) -> float:
        """The median darkness of a cell's fill pixels, or 0 when it has none."""
        x, y = cell
        steps = (np.arange(_CELL_SAMPLES) + 0.5) / _CELL_SAMPLES * (1 - 2 * _INNER) + _INNER
        gx = x + np.ones(_CELL_SAMPLES)[:, None] * steps[None, :]
        gy = y + steps[:, None] * np.ones(_CELL_SAMPLES)[None, :]
        fill = self._sample(self.fill, gx, gy)
        if not fill.any():
            return 0.0
        return float(np.median(self._sample(self.darkness, gx, gy)[fill]))

    def edge(self, cell: tuple[int, int], side: str, floors: np.ndarray) -> EdgeInk:
        """One edge's measurements.

        Args:
            cell: The cell that names the edge.
            side: Its side.
            floors: Every cell's floor fraction, from `floor_fractions`. A cell off the grid counts as no floor.

        Returns:
            The measurements.
        """
        x, y = cell
        along = 0.1 + 0.8 * (np.arange(_ALONG) + 0.5) / _ALONG
        across = np.linspace(-0.25, 0.25, _ACROSS)
        if side in ("west", "east"):
            line = x if side == "west" else x + 1
            gx = line + across[None, :] * np.ones(_ALONG)[:, None]
            gy = y + along[:, None] * np.ones(_ACROSS)[None, :]
            other = (x - 1, y) if side == "west" else (x + 1, y)
        else:
            line = y if side == "north" else y + 1
            gy = line + across[None, :] * np.ones(_ALONG)[:, None]
            gx = x + along[:, None] * np.ones(_ACROSS)[None, :]
            other = (x, y - 1) if side == "north" else (x, y + 1)
        dark = self._sample(self.darkness, gx, gy) >= self.style.dark
        step = 0.5 / (_ACROSS - 1)
        centre = _ACROSS // 2
        near = round(NEAR / step)
        widths = np.array([_run_width(row, centre) for row in dark]) * step
        on_line = dark[:, centre - near : centre + near + 1].any(axis=1)
        rows, columns = floors.shape
        here = floors[y, x]
        there = floors[other[1], other[0]] if 0 <= other[0] < columns and 0 <= other[1] < rows else 0.0
        return EdgeInk(
            width=float(np.median(widths)),
            heavy=float((widths >= HEAVY).mean()),
            line=float(on_line.mean()),
            floor_low=float(min(here, there)),
            floor_high=float(max(here, there)),
        )


def _run_width(row: np.ndarray, centre: int) -> int:
    """The length of the dark run across the line that lies nearest the centre, within the row."""
    dark = np.flatnonzero(row)
    if dark.size == 0:
        return 0
    start = int(dark[np.argmin(np.abs(dark - centre))])
    left = right = start
    while left - 1 >= 0 and row[left - 1]:
        left -= 1
    while right + 1 < row.size and row[right + 1]:
        right += 1
    return right - left + 1


INSIDE = 0.03
"""The share of a floor cell's inside that is ink, at or above which the ink doesn't settle the cell."""

GRIDDED = 0.6
"""The share of a side's length that must carry ink on its line for the side to count as printed."""

TONE_SPREAD = 0.15
"""How far a cell's fill tone may lie from the region's rock tone and still be rock."""


def unsettled_cells(ink: MapInk, floors: np.ndarray, cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The cells the ink doesn't settle, which Clef is asked about.

    A cell is unsettled in any of these cases:

    - It is between a quarter and three quarters floor.
    - It is floor with ink inside it (`INSIDE`), such as a label or a symbol.
    - It is floor with the printed grid on fewer than two of its sides (`GRIDDED`), as bare paper outside a building
      is.
    - It is not floor, and its fill's tone lies more than `TONE_SPREAD` from the region's rock tone, the median tone
      of the region's cells under a quarter floor, as water or a grey void would.

    Args:
        ink: The region's ink.
        floors: Every cell's floor fraction.
        cells: The cells to sort.

    Returns:
        The unsettled cells, in the order given.
    """
    rows, columns = floors.shape
    rocky = [(x, y) for y in range(rows) for x in range(columns) if floors[y, x] <= 0.25]
    tones = [ink.fill_tone(cell) for cell in rocky[:: max(1, len(rocky) // 400)]]
    rock_tone = float(np.median(tones)) if tones else 1.0
    out = []
    for x, y in cells:
        share = floors[y, x]
        if (
            0.25 < share < 0.75
            or (share >= 0.75 and (ink.ink_inside((x, y)) >= INSIDE or _lined_sides(ink, floors, (x, y)) < 2))
            or (share <= 0.25 and abs(ink.fill_tone((x, y)) - rock_tone) > TONE_SPREAD)
        ):
            out.append((x, y))
    return out


def _lined_sides(ink: MapInk, floors: np.ndarray, cell: tuple[int, int]) -> int:
    """How many of a cell's four sides carry ink along their line for at least `GRIDDED` of their length."""
    return sum(ink.edge(cell, side, floors).line >= GRIDDED for side in ("north", "east", "south", "west"))
