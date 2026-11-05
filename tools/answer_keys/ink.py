"""Ink, paper, and fill masks of a map image, and sampling on a fitted grid.

Each map style draws its materials at its own gray levels: ink (walls, doors,
symbols, text), paper (floor, and open ground where the map draws it), and
fill (the gray or blue that stands for rock, or for water on a map whose
legend says so). `Levels` records a map's levels, `materials` turns the image
into masks, and `sample` reads any image at the pixel positions a grid fit
gives.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Levels:
    """The gray levels that separate a map's materials.

    `channel` is the image channel the levels apply to: `gray` for the mean of
    the three channels, `red` for the red channel, which blue ink darkens most.
    """

    channel: str = "gray"
    ink_max: float = 80.0
    """At or below this level a pixel is full ink."""

    dark_max: float = 80.0
    """At or below this level a pixel is dark enough to be part of a door's box or a glyph, but not a grid line."""

    wall_max: float | None = None
    """At or below this level a pixel is dark enough to be part of a wall. `None` means `dark_max`."""

    paper_min: float = 225.0
    """At or above this level a pixel is paper."""

    fill: tuple[float, float] | None = None
    """The band of levels that is the fill, or `None` when the fill is the ink itself (TSR blue)."""

    hatched: bool = False
    """Whether the map draws rock as diagonal hatching, which then counts as fill."""


def channel_image(rgb: np.ndarray, channel: str) -> np.ndarray:
    """The float image of one channel.

    Args:
        rgb: The image as an array of shape `(height, width, 3)`.
        channel: `gray` or `red`.

    Returns:
        A float32 array of shape `(height, width)`.
    """
    if channel == "red":
        return rgb[..., 0].astype(np.float32)
    return rgb.astype(np.float32).mean(axis=2)


def box_max(mask: np.ndarray, radius: int) -> np.ndarray:
    """Dilate a boolean image with a square of side `2 * radius + 1`."""
    out = mask.copy()
    for axis in (0, 1):
        acc = out.copy()
        for shift in range(1, radius + 1):
            acc |= np.roll(out, shift, axis=axis)
            acc |= np.roll(out, -shift, axis=axis)
        out = acc
    return out


def box_min(mask: np.ndarray, radius: int) -> np.ndarray:
    """Erode a boolean image with a square of side `2 * radius + 1`."""
    return ~box_max(~mask, radius)


def close(mask: np.ndarray, radius: int) -> np.ndarray:
    """Fill gaps narrower than the window: dilate, then erode."""
    return box_min(box_max(mask, radius), radius)


def _axis_runs(mask: np.ndarray, axis: int) -> np.ndarray:
    """The length of the run of true values each true pixel lies in, along one axis (0 down, 1 across)."""
    m = mask if axis == 1 else mask.T
    height, width = m.shape
    index = np.broadcast_to(np.arange(width)[None, :], m.shape)
    pad = np.zeros((height, 1), dtype=bool)
    starts = m & ~np.concatenate([pad, m[:, :-1]], axis=1)
    ends = m & ~np.concatenate([m[:, 1:], pad], axis=1)
    run_start = np.maximum.accumulate(np.where(starts, index, 0), axis=1)
    run_end = np.minimum.accumulate(np.where(ends, index, width)[:, ::-1], axis=1)[:, ::-1]
    length = np.where(m, run_end - run_start + 1, 0)
    return length if axis == 1 else length.T


def diagonal_runs(mask: np.ndarray) -> np.ndarray:
    """The length of the longest diagonal run of true values through each true pixel.

    A diagonal run goes down-right or down-left one pixel at a time. The image is sheared so each diagonal
    becomes a column, the runs are measured down the columns, and the result is sheared back.

    Args:
        mask: A boolean image.

    Returns:
        An integer image: the longer of the two diagonal run lengths at each true pixel, 0 elsewhere.
    """
    height, width = mask.shape
    rows = np.arange(height)[:, None]
    columns = np.arange(width)[None, :]
    best = np.zeros(mask.shape, dtype=np.int64)
    for sign in (1, -1):
        sheared = np.zeros((height, width + height), dtype=bool)
        target = columns + (rows if sign == 1 else height - 1 - rows)
        sheared[rows, target] = mask
        runs = _axis_runs(sheared, 0)
        best = np.maximum(best, runs[rows, target])
    return best


def hatching(ink: np.ndarray, run_min: int = 14, width_max: int = 3, spacing: int = 4) -> np.ndarray:
    """Where the page is hatched: thin diagonal ink lines, with the paper between them.

    Args:
        ink: The ink mask.
        run_min: The fewest pixels a diagonal run needs to count as a hatch line, long enough to pass over the
            diagonal strokes of printed digits.
        width_max: The most pixels a hatch line spans across a row or a column.
        spacing: Half the gap between hatch lines, in pixels: the lines are dilated this far to cover it.

    Returns:
        A boolean image, true on the hatch lines and the paper between them.
    """
    thin = ink & (_axis_runs(ink, 1) <= width_max) & (_axis_runs(ink, 0) <= width_max)
    lines = thin & (diagonal_runs(thin) >= run_min)
    return box_max(lines, spacing)


@dataclass(frozen=True)
class Materials:
    """The masks of one map image."""

    gray: np.ndarray
    """The channel image, float32."""

    ink: np.ndarray
    dark: np.ndarray
    wall_dark: np.ndarray
    """True where a pixel is dark enough to be wall ink."""

    paper: np.ndarray
    fill: np.ndarray
    """True where the page is fill: the fill band, or solid ink for a style whose fill is ink."""

    floor: np.ndarray
    """Paper after a close that fills grid lines, text, and small symbols."""

    inkness: np.ndarray
    """How inked each pixel is, from 0 on paper to 1 at full ink, float32."""


def materials(rgb: np.ndarray, levels: Levels, close_radius: int) -> Materials:
    """Split an image into its materials.

    Args:
        rgb: The image as an array of shape `(height, width, 3)`.
        levels: The map's levels.
        close_radius: Half the window that closes thin lines and small text out of the floor mask, in pixels.

    Returns:
        The masks.
    """
    gray = channel_image(rgb, levels.channel)
    ink = gray <= levels.ink_max
    dark = gray <= levels.dark_max
    wall_dark = gray <= (levels.dark_max if levels.wall_max is None else levels.wall_max)
    paper = gray >= levels.paper_min
    if levels.fill is None:
        fill = box_min(gray < levels.paper_min, 3)
    else:
        lo, hi = levels.fill
        fill = (gray >= lo) & (gray <= hi)
    if levels.hatched:
        hatched = hatching(ink)
        paper = paper & ~hatched
        fill = fill | hatched
    floor = close(paper, close_radius)
    inkness = np.clip((levels.paper_min - gray) / (levels.paper_min - levels.ink_max), 0, 1).astype(np.float32)
    return Materials(
        gray=gray, ink=ink, dark=dark, wall_dark=wall_dark, paper=paper, fill=fill, floor=floor, inkness=inkness
    )


def sample(image: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Read an image at fractional pixel positions by bilinear interpolation.

    Args:
        image: A 2-D array. A boolean array is read as 0 and 1.
        x: Pixel columns, any shape.
        y: Pixel rows, the same shape.

    Returns:
        A float32 array of the same shape. Positions off the image read as the nearest edge pixel.
    """
    img = image.astype(np.float32)
    height, width = img.shape
    xc = np.clip(x, 0, width - 1.001)
    yc = np.clip(y, 0, height - 1.001)
    x0 = np.floor(xc).astype(np.int64)
    y0 = np.floor(yc).astype(np.int64)
    fx = (xc - x0).astype(np.float32)
    fy = (yc - y0).astype(np.float32)
    top = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    bottom = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy
