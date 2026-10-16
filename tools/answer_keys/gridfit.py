"""Fit a printed map's square grid with numpy alone.

The fit finds the thin grid lines of a drawing, fits one affine grid over the
whole region (separate x and y pitch, a shear on each axis), and reports how
far each detected line sits from the fitted grid. `fit_grid` is the entry
point. `GridFit.corner` turns a grid index into a pixel position, and
`GridFit.registration` writes the fit as a truth-format `Registration`.
"""

from dataclasses import dataclass

import numpy as np

from osrforge.tracing.truth import Registration

LINE_WIDTH_MAX = 4
"""Pixels. A grid line is at most this wide across; a wall is wider."""

LINE_RUN_MIN = 12
"""Pixels. A grid line candidate lies in a run of ink at least this long along the line."""


@dataclass(frozen=True)
class AxisFit:
    """One axis of the grid: the lines that run across the other axis."""

    origin: float
    """The position of line 0, in pixels along this axis, at the other axis's zero."""

    pitch: float
    """Pixels between consecutive lines."""

    shear: float
    """How far a line's position moves per pixel along the other axis."""

    positions: np.ndarray
    """Each detected line's sheared position."""

    strengths: np.ndarray
    """Each detected line's peak height in the projection."""

    indices: np.ndarray
    """The grid index the fit gives each detected line."""

    residuals: np.ndarray
    """Each detected line's distance from the fitted grid, in pixels."""

    kept: np.ndarray
    """Which detected lines the final least-squares fit used."""

    @property
    def rms_px(self) -> float:
        """Root mean square residual over the kept lines, in pixels."""
        return float(np.sqrt(np.mean(self.residuals[self.kept] ** 2))) if self.kept.any() else float("nan")

    @property
    def max_px(self) -> float:
        """The largest residual over every detected line, in pixels."""
        return float(np.abs(self.residuals).max()) if len(self.residuals) else float("nan")

    @property
    def within_tenth(self) -> float:
        """The share of detected lines within a tenth of a cell of the fit."""
        return float(np.mean(np.abs(self.residuals) < 0.1 * self.pitch)) if len(self.residuals) else float("nan")

    def summary(self) -> dict[str, float | int]:
        """The fit's figures for a report."""
        return {
            "pitch": round(self.pitch, 4),
            "shear": round(self.shear, 6),
            "origin": round(self.origin, 3),
            "lines": len(self.positions),
            "kept": int(self.kept.sum()),
            "rms_px": round(self.rms_px, 3),
            "max_px": round(self.max_px, 3),
            "rms_cells": round(self.rms_px / self.pitch, 4),
            "max_cells": round(self.max_px / self.pitch, 4),
            "within_tenth": round(self.within_tenth, 4),
        }


@dataclass(frozen=True)
class GridFit:
    """An affine grid over a region of an image.

    Vertical line `i` lies at `x = x_axis.origin + i * x_axis.pitch + x_axis.shear * y`, and horizontal line `j` at
    `y = y_axis.origin + j * y_axis.pitch + y_axis.shear * x`. The grid's columns run from line `i0` to line `i1`
    and its rows from line `j0` to line `j1`.
    """

    x_axis: AxisFit
    y_axis: AxisFit
    i0: int
    i1: int
    j0: int
    j1: int

    @property
    def columns(self) -> int:
        """The grid's width in cells."""
        return self.i1 - self.i0

    @property
    def rows(self) -> int:
        """The grid's height in cells."""
        return self.j1 - self.j0

    @property
    def pitch(self) -> float:
        """The mean of the two pitches, in pixels."""
        return (self.x_axis.pitch + self.y_axis.pitch) / 2

    def corner(self, i: float, j: float) -> tuple[float, float]:
        """The pixel position of grid point `(i, j)`, counted from the grid's own line 0.

        Args:
            i: Columns from the grid's west line. Fractions land inside a cell.
            j: Rows from the grid's north line.

        Returns:
            The `(x, y)` pixel position.
        """
        cx, px, sx = self.x_axis.origin, self.x_axis.pitch, self.x_axis.shear
        cy, py, sy = self.y_axis.origin, self.y_axis.pitch, self.y_axis.shear
        a = cx + (i + self.i0) * px
        b = cy + (j + self.j0) * py
        x = (a + sx * b) / (1 - sx * sy)
        y = b + sy * x
        return x, y

    def corners(self, i: np.ndarray, j: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """`corner` over arrays of grid points."""
        cx, px, sx = self.x_axis.origin, self.x_axis.pitch, self.x_axis.shear
        cy, py, sy = self.y_axis.origin, self.y_axis.pitch, self.y_axis.shear
        a = cx + (i + self.i0) * px
        b = cy + (j + self.j0) * py
        x = (a + sx * b) / (1 - sx * sy)
        y = b + sy * x
        return x, y

    def grid_point(self, x: float, y: float) -> tuple[float, float]:
        """The grid coordinates of a pixel, the inverse of `corner`."""
        cx, px, sx = self.x_axis.origin, self.x_axis.pitch, self.x_axis.shear
        cy, py, sy = self.y_axis.origin, self.y_axis.pitch, self.y_axis.shear
        i = (x - cx - sx * y) / px - self.i0
        j = (y - cy - sy * x) / py - self.j0
        return i, j

    def registration(self) -> Registration:
        """The fit as the truth format records it: every grid corner's pixel position."""
        rows = []
        for j in range(self.rows + 1):
            rows.append(tuple(self.corner(i, j) for i in range(self.columns + 1)))
        return Registration(corners=tuple(rows))

    def summary(self) -> dict[str, object]:
        """Both axes' figures and the grid's size, for a report."""
        return {
            "columns": self.columns,
            "rows": self.rows,
            "x": self.x_axis.summary(),
            "y": self.y_axis.summary(),
            "rms_cells": round(max(self.x_axis.rms_px / self.x_axis.pitch, self.y_axis.rms_px / self.y_axis.pitch), 4),
            "max_cells": round(max(self.x_axis.max_px / self.x_axis.pitch, self.y_axis.max_px / self.y_axis.pitch), 4),
        }


def run_lengths(mask: np.ndarray, axis: int) -> np.ndarray:
    """The length of the run of true values that each true pixel lies in, along one axis.

    Args:
        mask: A boolean image.
        axis: 1 for runs along a row, 0 for runs down a column.

    Returns:
        An integer image: the run length at each true pixel, 0 elsewhere.
    """
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


def line_candidates(ink: np.ndarray, width_max: int = LINE_WIDTH_MAX, run_min: int = LINE_RUN_MIN) -> tuple:
    """Split ink into thin vertical runs and thin horizontal runs.

    Args:
        ink: The ink mask.
        width_max: The most pixels a line may span across itself.
        run_min: The fewest pixels a run along the line may have.

    Returns:
        `(vertical, horizontal)`: boolean masks of candidate pixels on vertical lines and on horizontal lines.
    """
    across_x = run_lengths(ink, 1)
    across_y = run_lengths(ink, 0)
    vertical = ink & (across_x <= width_max) & (across_y >= run_min)
    horizontal = ink & (across_y <= width_max) & (across_x >= run_min)
    return vertical, horizontal


def _sheared_profile(along: np.ndarray, other: np.ndarray, shear: float, lo: float, length: int) -> np.ndarray:
    v = np.round(along - shear * other - lo).astype(np.int64)
    v = v[(v >= 0) & (v < length)]
    return np.bincount(v, minlength=length).astype(np.float64)


def best_shear(along: np.ndarray, other: np.ndarray, lo: float, length: int, limit: float = 0.02) -> float:
    """The shear that makes the projection of line pixels sharpest."""
    best = (-1.0, 0.0)
    for shear in np.linspace(-limit, limit, 81):
        profile = _sheared_profile(along, other, float(shear), lo, length)
        score = float((profile**2).sum())
        if score > best[0]:
            best = (score, float(shear))
    coarse = best[1]
    for shear in np.linspace(coarse - 0.001, coarse + 0.001, 41):
        profile = _sheared_profile(along, other, float(shear), lo, length)
        score = float((profile**2).sum())
        if score > best[0]:
            best = (score, float(shear))
    return best[1]


def estimate_pitch(profile: np.ndarray, pitch_min: float, pitch_max: float) -> float:
    """The grid pitch from the profile's autocorrelation, refined on far peaks."""
    centred = profile - profile.mean()
    spectrum = np.fft.rfft(centred, n=2 * len(centred))
    auto = np.fft.irfft(spectrum * np.conj(spectrum))[: len(centred)]
    lo, hi = int(pitch_min), int(np.ceil(pitch_max)) + 1
    pitch = float(lo + np.argmax(auto[lo:hi]))
    for n in (3, 6, 12, 24, 36):
        centre = round(n * pitch)
        if centre + 3 >= len(auto):
            break
        peak = centre - 3 + int(np.argmax(auto[centre - 3 : centre + 4]))
        if abs(peak / n - pitch) < 0.05 * pitch:
            pitch = peak / n
    return pitch


def find_peaks(profile: np.ndarray, pitch: float, lo: float, min_fraction: float = 0.15) -> tuple:
    """Local maxima of the profile that stand out, with subpixel centroids.

    A peak weaker than three tenths of the median peak is dropped: it is a wall or a label that lies off the
    grid, not a grid line.

    Returns:
        `(positions, strengths)` in profile pixels offset by `lo`.
    """
    kernel = np.array([1.0, 2.0, 1.0]) / 4
    smooth = np.convolve(profile, kernel, mode="same")
    window = max(2, int(pitch * 0.35))
    nonzero = smooth[smooth > 0]
    if len(nonzero) == 0:
        return np.zeros(0), np.zeros(0)
    threshold = min_fraction * float(np.percentile(nonzero, 95))
    positions = []
    strengths = []
    for i in range(window, len(smooth) - window):
        if smooth[i] > threshold and smooth[i] == smooth[i - window : i + window + 1].max():
            if smooth[i - 1] == smooth[i] and i - 1 >= window:
                continue
            weights = profile[i - 2 : i + 3]
            centre = i + (weights * np.arange(-2, 3)).sum() / max(weights.sum(), 1e-9)
            positions.append(lo + centre)
            strengths.append(float(smooth[i]))
    positions_array, strengths_array = np.array(positions), np.array(strengths)
    if len(strengths_array):
        keep = strengths_array >= 0.3 * float(np.median(strengths_array))
        positions_array, strengths_array = positions_array[keep], strengths_array[keep]
    return positions_array, strengths_array


def fit_axis(
    along: np.ndarray,
    other: np.ndarray,
    lo: float,
    hi: float,
    pitch_range: tuple[float, float],
    shear_limit: float = 0.02,
) -> AxisFit:
    """Fit the lines of one axis.

    Args:
        along: Candidate pixels' positions along this axis.
        other: The same pixels' positions along the other axis.
        lo: The start of the region on this axis, in pixels.
        hi: Its end.
        pitch_range: The least and greatest pitch to consider, in pixels.
        shear_limit: The largest shear to search.

    Returns:
        The axis fit.
    """
    length = int(hi - lo) + 1
    shear = best_shear(along, other, lo, length, shear_limit)
    profile = _sheared_profile(along, other, shear, lo, length)
    pitch = estimate_pitch(profile, *pitch_range)
    positions, strengths = find_peaks(profile, pitch, lo)
    if len(positions) < 3:
        raise ValueError(f"only {len(positions)} grid lines found; the fit needs at least 3")
    angles = 2 * np.pi * positions / pitch
    phase = float(np.angle((strengths * np.exp(1j * angles)).sum())) * pitch / (2 * np.pi)
    origin = positions.min() + ((phase - positions.min()) % pitch)
    solution = np.array([origin, pitch])
    kept = np.ones(len(positions), dtype=bool)
    for _ in range(6):
        indices = np.round((positions - solution[0]) / solution[1])
        design = np.vstack([np.ones_like(indices), indices]).T
        residuals = positions - design @ solution
        kept = np.abs(residuals) < 0.2 * solution[1]
        if kept.sum() < 3:
            break
        solution, *_ = np.linalg.lstsq(design[kept], positions[kept], rcond=None)
    indices = np.round((positions - solution[0]) / solution[1])
    residuals = positions - (solution[0] + indices * solution[1])
    kept = np.abs(residuals) < 0.2 * solution[1]
    return AxisFit(
        origin=float(solution[0]),
        pitch=float(solution[1]),
        shear=shear,
        positions=positions,
        strengths=strengths,
        indices=indices.astype(int),
        residuals=residuals,
        kept=kept,
    )


def fit_grid(
    ink: np.ndarray,
    rect: tuple[int, int, int, int],
    pitch_range: tuple[float, float] = (8.0, 120.0),
    width_max: int = LINE_WIDTH_MAX,
    run_min: int = LINE_RUN_MIN,
) -> GridFit:
    """Fit the grid of one region.

    Args:
        ink: The ink mask of the whole image, true where the page is inked.
        rect: The region `(x0, y0, x1, y1)` in pixels, the end exclusive. The grid spans it.
        pitch_range: The least and greatest pitch to consider, in pixels.
        width_max: The most pixels a grid line may span across itself.
        run_min: The fewest pixels a grid line candidate's run along the line may have.

    Returns:
        The fit, with its columns and rows covering the rectangle.
    """
    x0, y0, x1, y1 = rect
    region = np.zeros_like(ink)
    region[y0:y1, x0:x1] = ink[y0:y1, x0:x1]
    vertical, horizontal = line_candidates(region, width_max, run_min)
    yv, xv = np.nonzero(vertical)
    yh, xh = np.nonzero(horizontal)
    x_axis = fit_axis(xv.astype(float), yv.astype(float), x0, x1, pitch_range)
    y_axis = fit_axis(yh.astype(float), xh.astype(float), y0, y1, pitch_range)
    ymid = (y0 + y1) / 2
    xmid = (x0 + x1) / 2
    i0 = int(np.round((x0 - x_axis.origin - x_axis.shear * ymid) / x_axis.pitch))
    i1 = int(np.round((x1 - x_axis.origin - x_axis.shear * ymid) / x_axis.pitch))
    j0 = int(np.round((y0 - y_axis.origin - y_axis.shear * xmid) / y_axis.pitch))
    j1 = int(np.round((y1 - y_axis.origin - y_axis.shear * xmid) / y_axis.pitch))
    return GridFit(x_axis=x_axis, y_axis=y_axis, i0=i0, i1=i1, j0=j0, j1=j1)
