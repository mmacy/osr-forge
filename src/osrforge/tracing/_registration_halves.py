"""The alignment of two halves of one map across the fold, from their registrations and the ink along the fold.

The fold is the first half's right image edge and the second half's left
image edge. Image coordinates put each pixel's centre on whole numbers, so
an image `width` pixels wide runs from -0.5 to `width - 0.5`.

The columns come from the registrations alone. The first half's grid reaches
its right edge at a fractional column `edge`, and the second half's line 0
lies `start` pixels right of its left edge. Laid side by side with paper
between them, the halves share one grid when the second half's line 0 falls
on a whole line `k` of the first half's grid, which leaves a gap of
`k - edge - start / pitch` cells of paper at the fold.

The rows come from the grid phase at the fold, for the fraction of a row, and
from the ink within a cell and a half of the fold on each side, for the whole
rows: the two strips' profiles agree best at the right shift.
"""

import math
from dataclasses import dataclass

import numpy as np

from osrforge.tracing._registration_ink import BoolArray, FloatArray

TIE = 0.01
"""Two row shifts tie when their correlations lie within this of each other."""

STRIP_CELLS = 1.5
"""Cells. The ink this close to the fold, measured from the image edge, is what the row shift compares."""

LEAST_OVERLAP = 0.5
"""A row shift is compared only when the two strips overlap over at least this share of the shorter one."""


@dataclass(frozen=True)
class Alignment:
    """The row shift and the column candidates, in plain values."""

    rows: int
    columns: tuple[int, ...]
    gaps: tuple[float, ...]


def _along(xs: FloatArray, values: FloatArray, x: float) -> float:
    """The value at `x` along a row of corners, linear between them and extended from the end cells beyond them."""
    if x <= xs[0]:
        return float(values[0] + (values[1] - values[0]) * (x - xs[0]) / (xs[1] - xs[0]))
    if x >= xs[-1]:
        return float(values[-1] + (values[-1] - values[-2]) * (x - xs[-1]) / (xs[-1] - xs[-2]))
    return float(np.interp(x, xs, values))


def _profile(ink: BoolArray, lo: int, hi: int) -> FloatArray:
    """The share of ink in each row of a strip of columns."""
    return ink[:, max(0, lo) : max(lo + 1, hi)].mean(axis=1)


def _correlation(first: FloatArray, second: FloatArray, shift: int) -> float | None:
    """The correlation of the first profile with the second moved down by `shift` rows, over their overlap."""
    if shift >= 0:
        a, b = first[shift:], second[: max(0, len(second) - shift)]
    else:
        a, b = first[: len(first) + shift], second[-shift:]
    n = min(len(a), len(b))
    if n < LEAST_OVERLAP * min(len(first), len(second)) or n < 2:
        return None
    a, b = a[:n], b[:n]
    spread = float(a.std() * b.std())
    if spread == 0:
        return None
    return float(((a - a.mean()) * (b - b.mean())).mean() / spread)


def align(
    first_ink: BoolArray, first: FloatArray, second_ink: BoolArray, second: FloatArray, candidates: int, slack: float
) -> Alignment:
    """Align two halves.

    Args:
        first_ink: The first half's ink mask.
        first: Its registration's corners, shape `(rows + 1, columns + 1, 2)`.
        second_ink: The second half's ink mask.
        second: Its registration's corners.
        candidates: How many column offsets to return.
        slack: Cells. How far below zero a candidate's gap may be.

    Returns:
        The row shift, and the candidate column offsets past the first half's last column with their gaps.
    """
    width = first_ink.shape[1]
    right_edge, left_edge = width - 0.5, -0.5
    columns_first = first.shape[1] - 1
    # Where the first half's grid reaches its right edge, and where the second half's line 0 lies, row by row.
    edge = float(
        np.mean([_along(row[:, 0], np.arange(columns_first + 1, dtype=np.float64), right_edge) for row in first])
    )
    second_pitch = float(np.mean(second[:, 1, 0] - second[:, 0, 0]))
    start = float(np.mean(second[:, 0, 0])) - left_edge
    least = math.ceil(edge + start / second_pitch - slack)
    ks = range(least, least + candidates)
    gaps = tuple(round(k - edge - start / second_pitch, 4) for k in ks)
    offsets = tuple(k - columns_first for k in ks)
    # The rows: line 0 of each half at the fold, and the pitch there.
    first_y0 = _along(first[0, :, 0], first[0, :, 1], right_edge)
    second_y0 = _along(second[0, :, 0], second[0, :, 1], left_edge)
    first_pitch_y = float(np.mean(np.diff([_along(row[:, 0], row[:, 1], right_edge) for row in first])))
    second_pitch_y = float(np.mean(np.diff([_along(row[:, 0], row[:, 1], left_edge) for row in second])))
    pitch_y = (first_pitch_y + second_pitch_y) / 2
    whole = round((first_y0 - second_y0) / pitch_y)
    phase = first_y0 - second_y0 - whole * pitch_y
    pitch_x = (float(np.mean(first[:, -1, 0] - first[:, -2, 0])) + second_pitch) / 2
    strip = math.ceil(STRIP_CELLS * pitch_x)
    a = _profile(first_ink, width - strip, width)
    b = _profile(second_ink, 0, strip)
    scored: list[tuple[float, int]] = []
    reach = math.ceil(max(len(a), len(b)) / pitch_y) + 1
    for m in range(-reach, reach + 1):
        correlation = _correlation(a, b, round(phase + m * pitch_y))
        if correlation is not None:
            scored.append((correlation, m))
    if not scored:
        best = 0
    else:
        top = max(score for score, _ in scored)
        tied = [m for score, m in scored if score >= top - TIE]
        # On a tie the shift with matching rows nearest in height wins, and then the shift north.
        best = min(tied, key=lambda m: (abs(phase + m * pitch_y), m - whole))
    return Alignment(best - whole, offsets, gaps)
