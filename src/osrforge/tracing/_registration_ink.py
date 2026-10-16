"""The ink of a map image: darkness, ink against the local background, fill, and runs of ink.

Registration reads an image through these masks only. A pixel's *darkness*
is 255 less its darkest channel, so blue ink and black ink both read as dark
and paper reads near 0. A pixel is *ink* when it is darker than the lightest
pixel near it by a margin, so a grid line on grey fill is ink and the inside
of a solid wall isn't. A pixel is *fill* when it is dark outright. A *run* is
a stretch of ink along a row or a column, and a grid line is a run that is
thin across and long along.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image

type FloatArray = npt.NDArray[np.float64]
type Float32Array = npt.NDArray[np.float32]
type BoolArray = npt.NDArray[np.bool_]
type IntArray = npt.NDArray[np.int64]

LINE_WIDTH = 4
"""Pixels. A grid line is at most this wide across; a wall or a fill's rim is wider."""

RUN_MINIMUM = 12
"""Pixels. The shortest run of ink that can be part of a grid line, before any pitch is known."""


@dataclass(frozen=True)
class InkSettings:
    """The three settings that say what ink and fill are."""

    margin: float
    """How much darker than its local background a pixel must be to count as ink."""

    radius: int
    """Pixels. A pixel's local background is the lightest pixel within this distance of it."""

    fill: float
    """A pixel at least this dark is fill or solid ink."""


@dataclass(frozen=True)
class InkImage:
    """The masks of one image that registration reads."""

    dark: Float32Array
    """Each pixel's darkness, 0 to 255."""

    ink: BoolArray
    """True where a pixel is darker than its local background by the margin."""

    fill: BoolArray
    """True where a pixel is at least as dark as the fill setting."""

    across_x: IntArray
    """The length of the run of ink along the row through each ink pixel, 0 off ink."""

    across_y: IntArray
    """The length of the run of ink down the column through each ink pixel, 0 off ink."""

    @property
    def height(self) -> int:
        """The image height in pixels."""
        return int(self.dark.shape[0])

    @property
    def width(self) -> int:
        """The image width in pixels."""
        return int(self.dark.shape[1])

    def vertical_runs(self, run_minimum: int) -> BoolArray:
        """The ink on thin vertical runs at least `run_minimum` long."""
        return self.ink & (self.across_x <= LINE_WIDTH) & (self.across_y >= run_minimum)

    def horizontal_runs(self, run_minimum: int) -> BoolArray:
        """The ink on thin horizontal runs at least `run_minimum` long."""
        return self.ink & (self.across_y <= LINE_WIDTH) & (self.across_x >= run_minimum)

    def thick_horizontal(self) -> BoolArray:
        """The ink on horizontal runs thicker than a grid line: walls, frame lines, and the rims of fills."""
        return self.ink & (self.across_y > LINE_WIDTH) & (self.across_x >= RUN_MINIMUM)

    def thick_vertical(self) -> BoolArray:
        """The ink on vertical runs thicker than a grid line."""
        return self.ink & (self.across_x > LINE_WIDTH) & (self.across_y >= RUN_MINIMUM)


def read_darkness(path: Path) -> Float32Array:
    """Read an image as RGB and return each pixel's darkness.

    Raises:
        OSError: If the image can't be read.
    """
    with Image.open(path) as image:
        rgb = np.asarray(image.convert("RGB"))
    return (255 - rgb.min(axis=2)).astype(np.float32)


def ink_image(dark: Float32Array, settings: InkSettings) -> InkImage:
    """Make the masks of an image from its darkness."""
    background = min_filter(dark, settings.radius)
    ink = (dark - background) >= settings.margin
    fill = dark >= settings.fill
    return InkImage(dark, ink, fill, run_lengths(ink, 1), run_lengths(ink, 0))


def min_filter(image: Float32Array, radius: int) -> Float32Array:
    """The least value within `radius` of each pixel along both axes: a square window, clipped at the image edge."""
    out = image
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius, radius)
        padded = np.pad(out, pad, mode="edge")
        out = sliding_window_view(padded, 2 * radius + 1, axis=axis).min(axis=-1)
    return out


def max_filter_bool(mask: BoolArray, radius: int, axis: int | None = None) -> BoolArray:
    """Dilate a mask by `radius` pixels along one axis, or along both for a square window."""
    out = mask
    for along in (0, 1) if axis is None else (axis,):
        pad = [(0, 0), (0, 0)]
        pad[along] = (radius, radius)
        padded = np.pad(out, pad, mode="constant", constant_values=False)
        out = sliding_window_view(padded, 2 * radius + 1, axis=along).any(axis=-1)
    return out


def run_lengths(mask: BoolArray, axis: int) -> IntArray:
    """The length of the run of true values through each true pixel along one axis, 0 elsewhere.

    Args:
        mask: A boolean image.
        axis: 1 for runs along a row, 0 for runs down a column.
    """
    grid = mask if axis == 1 else mask.T
    height, width = grid.shape
    index = np.broadcast_to(np.arange(width, dtype=np.int64)[None, :], grid.shape)
    edge = np.zeros((height, 1), dtype=np.bool_)
    starts = grid & ~np.concatenate([edge, grid[:, :-1]], axis=1)
    ends = grid & ~np.concatenate([grid[:, 1:], edge], axis=1)
    first = np.maximum.accumulate(np.where(starts, index, 0), axis=1)
    last = np.minimum.accumulate(np.where(ends, index, width)[:, ::-1], axis=1)[:, ::-1]
    length = np.where(grid, last - first + 1, 0).astype(np.int64)
    return length if axis == 1 else length.T


def label_components(mask: BoolArray) -> tuple[IntArray, int]:
    """Label the 8-connected components of a boolean image.

    Components are numbered from 1 in the order of their first pixel, row by
    row from the top, so the labelling is the same on every run.

    Returns:
        The label image, 0 off the mask, and the number of components.
    """
    height, width = mask.shape
    padded = np.zeros((height, width + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    step = np.diff(padded, axis=1)
    starts = np.nonzero(step == 1)
    run_rows: IntArray = starts[0].astype(np.int64)
    run_starts: IntArray = starts[1].astype(np.int64)
    run_ends: IntArray = np.nonzero(step == -1)[1].astype(np.int64)
    count = len(run_rows)
    if count == 0:
        return np.zeros((height, width), dtype=np.int64), 0
    parent = list(range(count))

    def root(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    row_first: IntArray = np.searchsorted(run_rows, np.arange(height + 1, dtype=np.int64)).astype(np.int64)
    for row in range(1, height):
        a0, a1 = int(row_first[row - 1]), int(row_first[row])
        b0, b1 = int(row_first[row]), int(row_first[row + 1])
        if a0 == a1 or b0 == b1:
            continue
        # Runs touch, diagonals included, when each starts no later than the other's last pixel plus one.
        lows = np.searchsorted(run_ends[a0:a1], run_starts[b0:b1], side="left")
        highs = np.searchsorted(run_starts[a0:a1], run_ends[b0:b1], side="right")
        for k in range(b1 - b0):
            for a in range(int(lows[k]), int(highs[k])):
                ra, rb = root(a0 + a), root(b0 + k)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([root(node) for node in range(count)], dtype=np.int64)
    _, numbered = np.unique(roots, return_inverse=True)
    labels = np.zeros((height, width), dtype=np.int64)
    lengths = run_ends - run_starts
    flat = np.repeat(run_rows * width + run_starts, lengths) + (
        np.arange(int(lengths.sum())) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    )
    labels.ravel()[flat] = np.repeat(numbered + 1, lengths)
    return labels, int(numbered.max()) + 1
