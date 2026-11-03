"""The fit of one region's grid: the lines of each axis, their drift, the grid's corners, and the gate's figures.

Each axis is fitted on its own from the thin runs of ink that run across it:
vertical runs give the x positions of the vertical lines, and horizontal runs
the y positions of the horizontal lines. An axis's fit has three stages:

1. The *affine* stage projects every run pixel onto the axis after removing
   a shear, takes the pitch from the projection's autocorrelation, and fits
   an origin and a pitch to the projection's peaks by least squares. Lines
   printed at the fractions of the pitch divide it, so a scan that loses
   every other faint line can't register at twice the printed pitch.
2. The *bands* cut the region across the axis into strips about three
   pitches wide. Each peak of a band's projection that stands out is one
   detected line, indexed to the nearest fitted line or rejected.
   Neighbouring lines that all lie a multiple of the same number of indices
   apart multiply the pitch.
3. The *drift* stage fits a degree-2 polynomial in the line index and the
   position along the other axis to the indexed band peaks, and to points on
   a filled frame's edges when the region has them: the affine lines plus a
   smooth, low-order bend for scan drift. Indexing and the bend are refined
   together until the indexed peaks stop changing.

A grid's corner `(i, j)` is where vertical line `i` meets horizontal line `j`.
"""

import itertools
import math
from dataclasses import dataclass, field

import numpy as np

from osrforge.tracing._registration_ink import BoolArray, FloatArray, max_filter_bool

PITCH_RANGE = (8.0, 150.0)
"""Pixels. The least and greatest grid pitch considered."""

PEAK_WINDOW = 0.35
"""Pitches. A peak is the highest point of its profile within this distance on each side."""

PEAK_CLEARANCE = 0.15
"""Pitches. A peak stands out when its profile falls to at most `PEAK_ISOLATION` of its height beyond this distance
on each side, out to `PEAK_WINDOW`. A grid line is a narrow ridge with paper beside it; a comb of stair treads or
hatching is many ridges close together, so none of its ridges stands out."""

PEAK_ISOLATION = 0.5
"""The most a profile may rise, as a share of a peak's height, between `PEAK_CLEARANCE` and `PEAK_WINDOW` of it."""

PEAK_FLOOR = 0.1
"""A band's peak weaker than this share of the band's median peak is a stroke, not a line.

A line one cell long in a band three pitches wide is about a third as strong
as one across it. At 0.3 a scan of gatehouse's west half that the tests make
misses its corners by 0.13 cells; from 0 to 0.2 the tests give the same
results.
"""

PHASE_TOLERANCE = 0.15
"""Pitches. A line this close to a place on a lattice lies on it."""

FINER_STRENGTH = 0.5
"""A peak at a fraction of the pitch counts as a finer grid's line when it is at least this share of the lower
quartile of the pitch's own lines. With no such rule, a scan of old-stair's lower page, with its stairs and pool, has
its pitch divided by 3 and misses; from 0.25 to 0.75 the tests give the same results."""

MULTIPLE_SHARE = 0.9
"""The share of neighbouring band peaks that must lie a multiple of `n` indices apart for the pitch to be multiplied
by `n`. A median of the gaps, as the reference fit took, multiplied scattered-spread's pitch by 4 on a scan: bands
that cross only hatched rock hold just the frame's two lines, 12 indices apart."""

PEAK_LEAST = 0.3
"""Pitches. A band's peak is at least this strong: a grid line across one cell of a band, a pitch of run, gives
about half a pitch after smoothing. At 0.2 two faint strokes in the shrine's grotto, joined into one run through a
grid line, count as a line and the grotto fails the gate; at 0.4 a scan of gatehouse's west half that the tests make
misses its corners by 0.13 cells."""

OUTLIER_TOLERANCE = 0.2
"""Pitches. A line farther than this from the fit is left out of the next round of least squares."""

DRIFT_RIDGE = 0.1
"""The ridge on the drift's bend, in median peaks per pixel of bend at the region's edge.

The bend (the terms in index times position, index squared, and position
squared) costs as much as this many median-strength peaks lying one pixel off
for each pixel it moves a line at the region's edge. The synthetic TSR maps
bow their pitch by 0.6 px either side of the mean, up to 3.6 px of bend, and
the scans the tests make bow the grid by up to 0.3 of a pitch. With the
frame's anchors, from no ridge to 1 the tests give the same results, and
the worst floor corner of the unperturbed synthetic maps is 0.051 cells. At 3
one synthetic map's grid comes out a column short and three of the scans go
wrong; at 10 crypt's worst corner is 0.101, past the tolerance of 0.1.
"""

ANCHOR_WEIGHT = 0.5
"""Each frame anchor weighs this share of the median band peak's weight in the drift fit. The anchors lie 0.3 to
1.8 px outside the synthetic maps' true outer lines, so they guide the bend where no line is printed without
outweighing the lines. At 0.1 two of the scans the tests make miss their bowed corners; from 0.25 to 1 the tests
give the same results."""

DRIFT_MINIMUM_PEAKS = 8
"""The fewest indexed band peaks the drift stage needs; with fewer, the affine stage stands."""

SHEAR_LIMIT = 0.02
"""The largest shear considered, in pixels across per pixel along."""


@dataclass(frozen=True)
class Settings:
    """The gate's thresholds, as the public module states them."""

    line_tolerance: float
    reject_tolerance: float
    coverage_tolerance: float
    coverage_line_share: float


# Profiles, pitch, and peaks.


def sheared_profile(along: FloatArray, other: FloatArray, shear: float, lo: float, length: int) -> FloatArray:
    """Project line pixels onto one axis after removing a shear."""
    position = np.round(along - shear * other - lo).astype(np.int64)
    position = position[(position >= 0) & (position < length)]
    return np.bincount(position, minlength=length).astype(np.float64)


def best_shear(along: FloatArray, other: FloatArray, lo: float, length: int) -> float:
    """The shear that makes the projection of line pixels sharpest, by its sum of squares."""
    best = (-1.0, 0.0)
    for shear in np.linspace(-SHEAR_LIMIT, SHEAR_LIMIT, 81):
        profile = sheared_profile(along, other, float(shear), lo, length)
        score = float((profile**2).sum())
        if score > best[0]:
            best = (score, float(shear))
    coarse = best[1]
    for shear in np.linspace(coarse - 0.001, coarse + 0.001, 41):
        profile = sheared_profile(along, other, float(shear), lo, length)
        score = float((profile**2).sum())
        if score > best[0]:
            best = (score, float(shear))
    return best[1]


def autocorrelation(profile: FloatArray) -> FloatArray:
    """The autocorrelation of a profile about its mean."""
    centred = profile - profile.mean()
    spectrum = np.fft.rfft(centred, n=2 * len(centred))
    return np.fft.irfft(spectrum * np.conj(spectrum))[: len(centred)]


def estimate_pitch(profile: FloatArray) -> float | None:
    """The grid pitch: the first strong autocorrelation lag in `PITCH_RANGE`, refined on far lags.

    The lag that wins is the first local maximum at least half as high as the
    highest in the range, so a doubled pitch, every other line heavier,
    doesn't win over the printed one.

    Returns:
        The pitch in pixels, or `None` when the profile is too short or flat.
    """
    if len(profile) < 2 * PITCH_RANGE[0] + 4:
        return None
    auto = autocorrelation(profile)
    lo, hi = int(PITCH_RANGE[0]), min(math.ceil(PITCH_RANGE[1]) + 1, len(auto) - 2)
    if hi - lo < 3:
        return None
    window = auto[lo:hi]
    top = float(window.max())
    if top <= 0:
        return None
    pitch = float(lo + int(np.argmax(window)))
    for k in range(lo + 1, hi - 1):
        if auto[k] >= auto[k - 1] and auto[k] >= auto[k + 1] and auto[k] >= 0.5 * top:
            pitch = float(k)
            break
    return refine_pitch(auto, pitch)


def refine_pitch(auto: FloatArray, pitch: float) -> float:
    """Refine a pitch on the autocorrelation's peaks at 3 to 36 pitches."""
    for n in (3, 6, 12, 24, 36):
        centre = round(n * pitch)
        if centre + 3 >= len(auto):
            break
        peak = centre - 3 + int(np.argmax(auto[centre - 3 : centre + 4]))
        if abs(peak / n - pitch) < 0.05 * pitch:
            pitch = peak / n
    return pitch


def find_peaks(profile: FloatArray, pitch: float, isolated: bool) -> tuple[FloatArray, FloatArray]:
    """The peaks of a profile that stand out, with subpixel centroids.

    A peak is the highest point of the smoothed profile within `PEAK_WINDOW`
    of a pitch on each side, above 0.15 of the profile's 95th percentile. A
    band's peak must also be isolated (see `PEAK_CLEARANCE`), and at least
    `PEAK_FLOOR` of the median peak and `PEAK_LEAST` of a pitch strong.

    Args:
        profile: The projection, one value per pixel.
        pitch: The pitch the peaks are spaced at.
        isolated: Whether to apply the rules for band peaks.

    Returns:
        `(positions, strengths)`, positions in pixels from the profile's start.
    """
    empty = (np.zeros(0), np.zeros(0))
    smooth = np.convolve(profile, np.array([0.25, 0.5, 0.25]), mode="same")
    positive = smooth[smooth > 0]
    if len(positive) == 0:
        return empty
    threshold = 0.15 * float(np.percentile(positive, 95))
    window = max(2, int(pitch * PEAK_WINDOW))
    clearance = max(3, round(pitch * PEAK_CLEARANCE))
    length = len(smooth)
    positions: list[float] = []
    strengths: list[float] = []
    for i in np.nonzero(smooth > threshold)[0]:
        i = int(i)
        if i < 2 or i > length - 3:
            continue
        lo, hi = max(0, i - window), min(length, i + window + 1)
        height = smooth[i]
        if height < smooth[lo:hi].max() or (i > lo and smooth[i - 1] == height):
            continue
        if isolated:
            near, far = slice(lo, max(lo, i - clearance + 1)), slice(min(hi, i + clearance), hi)
            beside = np.concatenate([smooth[near], smooth[far]])
            if len(beside) and float(beside.max()) > PEAK_ISOLATION * height:
                continue
        weights = profile[i - 2 : i + 3]
        positions.append(i + float((weights * np.arange(-2, 3)).sum()) / max(float(weights.sum()), 1e-9))
        strengths.append(float(height))
    position_array, strength_array = np.array(positions), np.array(strengths)
    if isolated and len(strength_array):
        keep = (strength_array >= PEAK_FLOOR * float(np.median(strength_array))) & (
            strength_array >= PEAK_LEAST * pitch
        )
        position_array, strength_array = position_array[keep], strength_array[keep]
    return position_array, strength_array


# The line model of one axis.


@dataclass
class LineModel:
    """Where the lines of one axis lie: line `i` at `position(i, other)` along the axis.

    The position is a polynomial in `u`, the line index, and `v`, the position
    along the other axis, both scaled to -1 to 1 over the region, with the
    terms 1, u, v, uv, u², and v². Each coefficient is in pixels and is the
    term's largest effect at the region's edge. An affine model has only the
    first three.
    """

    coefficients: FloatArray
    index_lo: float
    index_hi: float
    other_lo: float
    other_hi: float

    @classmethod
    def affine(
        cls, origin: float, pitch: float, shear: float, index_range: tuple[float, float], other: tuple[float, float]
    ) -> LineModel:
        """The model with line `i` at `origin + i * pitch + shear * other`."""
        index_lo, index_hi = index_range
        other_lo, other_hi = other
        coefficients = np.array(
            [
                origin + pitch * (index_lo + index_hi) / 2 + shear * (other_lo + other_hi) / 2,
                pitch * (index_hi - index_lo) / 2,
                shear * (other_hi - other_lo) / 2,
                0.0,
                0.0,
                0.0,
            ]
        )
        return cls(coefficients, index_lo, index_hi, other_lo, other_hi)

    def design(self, index: FloatArray, other: FloatArray) -> FloatArray:
        """The design rows `[1, u, v, uv, u², v²]` for line indices at positions along the other axis."""
        u = 2 * (index - self.index_lo) / (self.index_hi - self.index_lo) - 1
        v = 2 * (other - self.other_lo) / (self.other_hi - self.other_lo) - 1
        return np.stack([np.ones_like(u), u, v, u * v, u * u, v * v], axis=-1)

    def position(self, index: FloatArray | float, other: FloatArray | float) -> FloatArray:
        """The position along the axis of line `index` at `other`."""
        index_array, other_array = np.broadcast_arrays(
            np.asarray(index, dtype=np.float64), np.asarray(other, dtype=np.float64)
        )
        return self.design(index_array, other_array) @ self.coefficients

    def slope(self, index: FloatArray | float, other: FloatArray | float) -> FloatArray:
        """The local pitch: the derivative of the position by the index."""
        index_array, other_array = np.broadcast_arrays(
            np.asarray(index, dtype=np.float64), np.asarray(other, dtype=np.float64)
        )
        u = 2 * (index_array - self.index_lo) / (self.index_hi - self.index_lo) - 1
        v = 2 * (other_array - self.other_lo) / (self.other_hi - self.other_lo) - 1
        c = self.coefficients
        return (c[1] + c[3] * v + 2 * c[4] * u) * 2 / (self.index_hi - self.index_lo)

    def index_at(self, along: FloatArray | float, other: FloatArray | float) -> FloatArray:
        """The fractional line index at a position, by Newton's method."""
        along_array, other_array = np.broadcast_arrays(
            np.asarray(along, dtype=np.float64), np.asarray(other, dtype=np.float64)
        )
        mid = (self.index_lo + self.index_hi) / 2
        index = mid + (along_array - self.position(mid, other_array)) / self.slope(mid, other_array)
        for _ in range(4):
            index = index - (self.position(index, other_array) - along_array) / self.slope(index, other_array)
        return index


# The fit of one axis.


@dataclass(frozen=True)
class BandPeak:
    """One detected line: a peak of one band's profile."""

    band: int
    position: float
    """The peak's position along the axis, in pixels, at the band's centre."""

    other: float
    """The band's centre along the other axis, in pixels."""

    strength: float


@dataclass
class AxisFit:
    """The fit of one axis's lines over a region."""

    pitch: float
    """The affine stage's pitch, in pixels."""

    model: LineModel
    bands: int
    peaks: list[BandPeak]
    indices: list[int | None] = field(default_factory=list[int | None])
    """Each peak's line index, or `None` for a rejected peak."""

    residuals: FloatArray = field(default_factory=lambda: np.zeros(0))
    """Each peak's distance from its indexed line, in pixels; NaN for a rejected peak."""


@dataclass(frozen=True)
class Anchors:
    """Points on the frame's sides that the drift fit reads beside the band peaks, outside the gate's figures."""

    other: FloatArray
    """Each point's position along the other axis."""

    position: FloatArray
    """Each point's position along the axis."""


@dataclass(frozen=True)
class AxisData:
    """The line pixels of one axis over a region."""

    along: FloatArray
    """Each pixel's position along the axis."""

    other: FloatArray
    """Each pixel's position along the other axis."""

    lo: float
    hi: float
    """The region's extent along the axis, in pixels, the end exclusive."""

    other_lo: float
    other_hi: float
    """The region's extent along the other axis."""


@dataclass(frozen=True)
class Affine:
    """The affine stage of one axis."""

    origin: float
    pitch: float
    shear: float
    positions: FloatArray
    strengths: FloatArray


def affine_axis(data: AxisData, pitch_hint: float | None = None) -> Affine | None:
    """Fit one axis's affine lines from the global sheared projection of its line pixels.

    The shear is the one that makes the projection sharpest, the pitch comes
    from the projection's autocorrelation (or refines `pitch_hint`), each line
    is a peak of the projection, and origin and pitch are refined by least
    squares with peaks farther than `OUTLIER_TOLERANCE` from the fit left out.

    Returns:
        The affine stage, or `None` when fewer than 3 lines are found, or 2 when the pitch is given.
    """
    length = int(data.hi - data.lo) + 1
    if len(data.along) == 0 or length < 3:
        return None
    shear = best_shear(data.along, data.other, data.lo, length)
    profile = sheared_profile(data.along, data.other, shear, data.lo, length)
    if pitch_hint is None:
        estimate = estimate_pitch(profile)
        if estimate is None:
            return None
        pitch = estimate
    else:
        pitch = refine_pitch(autocorrelation(profile), pitch_hint)
    positions, strengths = find_peaks(profile, pitch, isolated=False)
    if len(positions) < (3 if pitch_hint is None else 2):
        return None
    positions = positions + data.lo
    angles = 2 * np.pi * positions / pitch
    phase = float(np.angle((strengths * np.exp(1j * angles)).sum())) * pitch / (2 * np.pi)
    origin = float(positions.min() + ((phase - positions.min()) % pitch))
    fitted = (origin, pitch)
    for _ in range(6):
        indices = np.round((positions - fitted[0]) / fitted[1]).astype(np.float64)
        design = np.stack([np.ones(len(indices)), indices], axis=1)
        kept = np.abs(positions - design @ np.array(fitted)) < OUTLIER_TOLERANCE * fitted[1]
        if kept.sum() < 3:
            break
        solution = np.linalg.lstsq(design[kept], positions[kept], rcond=None)[0]
        fitted = (float(solution[0]), float(solution[1]))
    return Affine(fitted[0], fitted[1], shear, positions, strengths)


def band_edges(data: AxisData, other_pitch: float) -> FloatArray:
    """The bands across the axis: each at least three pitches of the other axis wide, and at most twelve."""
    span = data.other_hi - data.other_lo
    bands = int(max(1, min(12, span // (3 * other_pitch))))
    return np.linspace(data.other_lo, data.other_hi, bands + 1)


def band_peaks(data: AxisData, model: LineModel, pitch: float, edges: FloatArray) -> list[BandPeak]:
    """The detected lines of every band, at the band's centre.

    Each pixel is carried to the band's centre along the model's lines before
    the band is projected, so a sheared or bent line projects to one narrow
    peak.
    """
    peaks: list[BandPeak] = []
    length = int(data.hi - data.lo) + 1
    for band in range(len(edges) - 1):
        select = (data.other >= edges[band]) & (data.other < edges[band + 1])
        if select.sum() < 10:
            continue
        centre = float((edges[band] + edges[band + 1]) / 2)
        profile = _carried_profile(data.along[select], data.other[select], model, centre, data.lo, length)
        positions, strengths = find_peaks(profile, pitch, isolated=True)
        for position, strength in zip(positions, strengths, strict=True):
            peaks.append(BandPeak(band, float(position) + data.lo, centre, float(strength)))
    return peaks


def _carried_profile(
    along: FloatArray, other: FloatArray, model: LineModel, centre: float, lo: float, length: int
) -> FloatArray:
    """Project pixels onto the axis after carrying each along the model's lines to the band's centre."""
    if len(along) == 0:
        return np.zeros(length)
    index = model.index_at(along, other)
    carried = along + model.position(index, centre) - model.position(index, other)
    return np.bincount(np.clip(np.round(carried - lo).astype(np.int64), 0, length - 1), minlength=length).astype(
        np.float64
    )


def index_peaks(peaks: list[BandPeak], model: LineModel, tolerance: float) -> tuple[list[int | None], FloatArray]:
    """Give each peak the index of the model's nearest line, or `None` when it is farther than `tolerance` pixels."""
    if not peaks:
        return [], np.zeros(0)
    position = np.array([peak.position for peak in peaks])
    other = np.array([peak.other for peak in peaks])
    nearest = np.round(model.index_at(position, other))
    residual = position - model.position(nearest, other)
    indices: list[int | None] = [
        int(n) if abs(r) <= tolerance else None for n, r in zip(nearest, residual, strict=True)
    ]
    return indices, np.where([index is not None for index in indices], residual, np.nan)


def fit_drift(
    peaks: list[BandPeak], indices: list[int | None], start: LineModel, pitch: float, anchors: Anchors | None = None
) -> LineModel:
    """Fit the drift model to the indexed peaks, and to the frame's anchors when there are any.

    Weighted least squares, each peak weighted by the square root of its
    strength and each anchor by the median peak's, with the bend held near
    zero by `DRIFT_RIDGE` and points farther than `OUTLIER_TOLERANCE` from the
    fit dropped over up to six rounds. A term is fitted only when the data can
    tell it apart: two lines for the pitch, three for its drift, two places
    along the other axis for the shear, and three for a bend along it, so a
    small region gets an affine fit and never an extrapolated bend. A term
    left out keeps the starting model's value.
    """
    chosen = [k for k, index in enumerate(indices) if index is not None]
    if len(chosen) < DRIFT_MINIMUM_PEAKS:
        return start
    index = np.array([indices[k] for k in chosen], dtype=np.float64)
    other = np.array([peaks[k].other for k in chosen])
    target = np.array([peaks[k].position for k in chosen])
    weights = np.sqrt(np.array([peaks[k].strength for k in chosen]))
    if anchors is not None and len(anchors.position):
        index = np.concatenate([index, np.round(start.index_at(anchors.position, anchors.other))])
        other = np.concatenate([other, anchors.other])
        target = np.concatenate([target, anchors.position])
        weights = np.concatenate([weights, np.full(len(anchors.position), ANCHOR_WEIGHT * float(np.median(weights)))])
    design = start.design(index, other)
    # A term needs data that can tell it apart: two lines for the pitch, three for its drift, two bands for the
    # shear, and three for a bend along the other axis.
    lines, places = len(np.unique(index)), len(np.unique(other))
    terms = [0]
    if lines >= 2:
        terms.append(1)
    if places >= 2:
        terms.append(2)
    if lines >= 2 and places >= 2:
        terms.append(3)
    if lines >= 3:
        terms.append(4)
    if places >= 3:
        terms.append(5)
    ridge_weight = math.sqrt(DRIFT_RIDGE * float(np.median(weights[: len(chosen)] ** 2)))
    bend = [term for term in terms if term >= 3]
    ridge = np.zeros((len(bend), len(terms)))
    for row, term in enumerate(bend):
        ridge[row, terms.index(term)] = ridge_weight
    fixed = [term for term in range(6) if term not in terms]
    target = target - design[:, fixed] @ start.coefficients[fixed]
    kept = np.ones(len(target), dtype=np.bool_)
    solution = start.coefficients[terms]
    for _ in range(6):
        if kept.sum() < DRIFT_MINIMUM_PEAKS:
            break
        w = weights[kept][:, None]
        a = np.vstack([design[kept][:, terms] * w, ridge])
        b = np.concatenate([target[kept] * w[:, 0], np.zeros(len(bend))])
        solution = np.linalg.lstsq(a, b, rcond=None)[0]
        residual = target - design[:, terms] @ solution
        new_kept = np.abs(residual) < OUTLIER_TOLERANCE * pitch
        if np.array_equal(new_kept, kept):
            break
        kept = new_kept
    coefficients = start.coefficients.copy()
    coefficients[terms] = solution
    return LineModel(coefficients, start.index_lo, start.index_hi, start.other_lo, start.other_hi)


def fit_axis(
    data: AxisData,
    other_pitch: float,
    settings: Settings,
    pitch_hint: float | None = None,
    anchors: Anchors | None = None,
) -> AxisFit | None:
    """Fit one axis: the affine stage, the bands, and the drift.

    A pitch can be a whole multiple or a whole fraction of the printed one,
    and the fit rules out both. Lines printed at every fraction `1/n` of the
    pitch between the fitted lines mean the pitch is `n` times the printed
    one, and the fit divides it (see `_divisor`). Neighbouring band peaks
    that all lie a multiple of `n` indices apart mean the pitch is a
    fraction of the printed one, and the fit multiplies it (see `_multiple`).

    Returns:
        The fit, or `None` when fewer than 3 lines are found.
    """
    affine = affine_axis(data, pitch_hint)
    if affine is None:
        return None
    divisor = _divisor(data, affine)
    if divisor > 1:
        finer = affine_axis(data, affine.pitch / divisor)
        if finer is not None:
            affine = finer
    corrections = 0
    while True:
        fit = _bands_and_drift(data, affine, other_pitch, settings, anchors)
        multiple = _multiple(fit)
        if multiple <= 1 or corrections >= 2 or affine.pitch * multiple > PITCH_RANGE[1] * 1.5:
            return fit
        corrections += 1
        corrected = affine_axis(data, affine.pitch * multiple)
        if corrected is None:
            return fit
        affine = corrected


def _divisor(data: AxisData, affine: Affine) -> int:
    """The `n` of 4, 3, or 2 for which lines are printed at every fraction `1/n` of the pitch, or 1.

    The profile's peaks are found again at a window for the finer pitch, so
    a line a fraction of the pitch from a stronger one isn't hidden by it.
    The finer pitch is real when lines lie within `PHASE_TOLERANCE` of the
    finer lattice at every one of its `n - 1` fractional places, at least 3 of
    them, and at least half as many as lie on the pitch's own lines. A peak at
    a fractional place counts only when it is a line's length: at least
    `FINER_STRENGTH` of the lower quartile of the pitch's own lines, which a
    stair's treads and a pool's waves, at regular spacings, are not.
    """
    length = int(data.hi - data.lo) + 1
    profile = sheared_profile(data.along, data.other, affine.shear, data.lo, length)
    for n in (4, 3, 2):
        finer = affine.pitch / n
        if finer < PITCH_RANGE[0]:
            continue
        positions, strengths = find_peaks(profile, finer, isolated=False)
        place = (positions + data.lo - affine.origin) / finer
        nearest = np.round(place)
        on = np.abs(place - nearest) <= PHASE_TOLERANCE
        residue = np.mod(nearest, n).astype(np.int64)
        lines = on & (residue == 0)
        if not lines.any():
            continue
        # A finer grid's lines are about as long as the pitch's own; a stair's treads and a pool's waves are short.
        long = strengths >= FINER_STRENGTH * float(np.percentile(strengths[lines], 25))
        fractional = on & (residue != 0) & long
        own = int(lines.sum())
        count = int(fractional.sum())
        if len(set(residue[fractional].tolist())) == n - 1 and count >= 3 and count >= own / 2:
            return n
    return 1


def _multiple(fit: AxisFit) -> int:
    """The `n` of 5 to 2 for which neighbouring band peaks lie a multiple of `n` indices apart, or 1.

    At the printed pitch, neighbouring lines lie one index apart wherever
    floor runs on, and further apart only across rock or paper. At a fraction
    of the printed pitch, every pair lies a multiple of the fraction apart.
    """
    gaps: list[int] = []
    for band in range(fit.bands):
        ordered = sorted(
            (peak.position, index)
            for peak, index in zip(fit.peaks, fit.indices, strict=True)
            if peak.band == band and index is not None
        )
        gaps.extend(second[1] - first[1] for first, second in itertools.pairwise(ordered))
    gaps = [gap for gap in gaps if gap > 0]
    if not gaps:
        return 1
    for n in (5, 4, 3, 2):
        if sum(gap % n == 0 for gap in gaps) >= MULTIPLE_SHARE * len(gaps):
            return n
    return 1


def _bands_and_drift(
    data: AxisData, affine: Affine, other_pitch: float, settings: Settings, anchors: Anchors | None
) -> AxisFit:
    index_range = (
        (data.lo - affine.origin - affine.shear * (data.other_lo + data.other_hi) / 2) / affine.pitch,
        (data.hi - affine.origin - affine.shear * (data.other_lo + data.other_hi) / 2) / affine.pitch,
    )
    if index_range[1] - index_range[0] < 1:
        index_range = (index_range[0] - 0.5, index_range[0] + 0.5)
    other_range = (data.other_lo, max(data.other_hi, data.other_lo + 1.0))
    model = LineModel.affine(affine.origin, affine.pitch, affine.shear, index_range, other_range)
    edges = band_edges(data, other_pitch)
    bands = len(edges) - 1
    tolerance = settings.reject_tolerance * affine.pitch
    peaks = band_peaks(data, model, affine.pitch, edges)
    indices, _ = index_peaks(peaks, model, tolerance)
    for _ in range(4):
        model = fit_drift(peaks, indices, model, affine.pitch, anchors)
        peaks = band_peaks(data, model, affine.pitch, edges)
        new_indices, _ = index_peaks(peaks, model, tolerance)
        if new_indices == indices:
            break
        indices = new_indices
    model = fit_drift(peaks, indices, model, affine.pitch, anchors)
    indices, residuals = index_peaks(peaks, model, tolerance)
    return AxisFit(affine.pitch, model, bands, peaks, indices, residuals)


# The grid and its figures.


@dataclass
class GridFit:
    """A region's grid: the two axis fits and the grid's extent in lines."""

    x: AxisFit
    """The vertical lines: x as a function of the line index and y."""

    y: AxisFit
    """The horizontal lines: y as a function of the line index and x."""

    i0: int
    i1: int
    j0: int
    j1: int
    """The first and last vertical and horizontal lines of the grid, in the fits' own numbering."""

    @property
    def columns(self) -> int:
        """The grid's width in cells."""
        return self.i1 - self.i0

    @property
    def rows(self) -> int:
        """The grid's height in cells."""
        return self.j1 - self.j0

    def corners(self) -> FloatArray:
        """Every corner's pixel position, shape `(rows + 1, columns + 1, 2)`, by fixed-point iteration."""
        ii, jj = np.meshgrid(
            np.arange(self.i0, self.i1 + 1, dtype=np.float64), np.arange(self.j0, self.j1 + 1, dtype=np.float64)
        )
        x_middle = (self.y.model.other_lo + self.y.model.other_hi) / 2
        y = self.y.model.position(jj, x_middle)
        x = self.x.model.position(ii, y)
        for _ in range(6):
            y = self.y.model.position(jj, x)
            x = self.x.model.position(ii, y)
        return np.stack([x, y], axis=-1)

    def pitches(self) -> tuple[float, float]:
        """The mean x and y pitch over the grid: the span of its middle row and middle column over their cells."""
        corners = self.corners()
        middle_row = corners[corners.shape[0] // 2]
        middle_column = corners[:, corners.shape[1] // 2]
        x_pitch = float(middle_row[-1, 0] - middle_row[0, 0]) / self.columns
        y_pitch = float(middle_column[-1, 1] - middle_column[0, 1]) / self.rows
        return x_pitch, y_pitch


@dataclass(frozen=True)
class Figures:
    """The gate's figures for one region's fit."""

    detected: int
    rejected: int
    explained: int
    jumps: int
    counted: int
    covered: int


def line_figures(fit: AxisFit, pitch: float, settings: Settings) -> tuple[int, int, int, int]:
    """One axis's detected, rejected, and explained lines, and its index jumps."""
    detected = len(fit.peaks)
    rejected = sum(index is None for index in fit.indices)
    residuals = fit.residuals[~np.isnan(fit.residuals)]
    explained = int((np.abs(residuals) <= settings.line_tolerance * pitch).sum())
    jumps = 0
    for band in range(fit.bands):
        ordered = sorted(
            (peak.position, index)
            for peak, index in zip(fit.peaks, fit.indices, strict=True)
            if peak.band == band and index is not None
        )
        for (first, first_index), (second, second_index) in itertools.pairwise(ordered):
            if second_index - first_index != round((second - first) / pitch):
                jumps += 1
    return detected, rejected, explained, jumps


def coverage_figures(
    grid: GridFit, ink: BoolArray, fill: BoolArray, pitch: tuple[float, float], settings: Settings
) -> tuple[int, int]:
    """The counted and covered cells of a grid.

    A cell is counted when it holds at least three tenths of the mean pitch in
    ink pixels and fill covers less than half of it. A counted cell is covered
    when ink lies within the coverage tolerance of its west line along at
    least the coverage line share of that line's length, and the same of its
    north line.
    """
    height, width = ink.shape
    corners = grid.corners()
    columns, rows = grid.columns, grid.rows
    if columns < 1 or rows < 1:
        return 0, 0
    x_lo = max(0, math.floor(float(corners[..., 0].min())))
    x_hi = min(width, math.ceil(float(corners[..., 0].max())) + 1)
    y_lo = max(0, math.floor(float(corners[..., 1].min())))
    y_hi = min(height, math.ceil(float(corners[..., 1].max())) + 1)
    if x_hi <= x_lo or y_hi <= y_lo:
        return 0, 0
    ys = np.arange(y_lo, y_hi, dtype=np.float64)
    xs = np.arange(x_lo, x_hi, dtype=np.float64)
    column_lines = np.arange(grid.i0, grid.i1 + 1, dtype=np.float64)
    row_lines = np.arange(grid.j0, grid.j1 + 1, dtype=np.float64)
    # Each pixel's column, row by row, and each pixel's row, column by column.
    x_at = grid.x.model.position(column_lines[None, :], ys[:, None])
    y_at = grid.y.model.position(row_lines[None, :], xs[:, None])
    cell_i = np.empty((len(ys), len(xs)), dtype=np.int64)
    for k in range(len(ys)):
        cell_i[k] = np.searchsorted(x_at[k], xs, side="right") - 1
    cell_j = np.empty((len(ys), len(xs)), dtype=np.int64)
    for k in range(len(xs)):
        cell_j[:, k] = np.searchsorted(y_at[k], ys, side="right") - 1
    inside = (cell_i >= 0) & (cell_i < columns) & (cell_j >= 0) & (cell_j < rows)
    cell = (cell_j * columns + cell_i)[inside]
    window = (slice(y_lo, y_hi), slice(x_lo, x_hi))
    count = columns * rows
    pixels = np.bincount(cell, minlength=count)
    ink_pixels = np.bincount(cell, weights=ink[window][inside], minlength=count)
    fill_pixels = np.bincount(cell, weights=fill[window][inside], minlength=count)
    mean_pitch = (pitch[0] + pitch[1]) / 2
    counted = (ink_pixels >= 0.3 * mean_pitch) & (fill_pixels < 0.5 * np.maximum(pixels, 1))
    # The west line of each cell, sampled on every pixel row it crosses, and the north line on every column.
    near_x = max_filter_bool(ink, max(0, round(settings.coverage_tolerance * pitch[0])), axis=1)
    near_y = max_filter_bool(ink, max(0, round(settings.coverage_tolerance * pitch[1])), axis=0)
    west = _line_share(grid, near_x, columns, rows, vertical=True)
    north = _line_share(grid, near_y, columns, rows, vertical=False)
    covered = counted & (west >= settings.coverage_line_share) & (north >= settings.coverage_line_share)
    return int(counted.sum()), int(covered.sum())


def _line_share(grid: GridFit, near: BoolArray, columns: int, rows: int, vertical: bool) -> FloatArray:
    """For each cell, the share of its west (or north) line's samples that have ink near them."""
    height, width = near.shape
    count = columns * rows
    hits = np.zeros(count)
    samples = np.zeros(count)
    if vertical:
        across = np.arange(width, dtype=np.float64)
        top = math.floor(float(grid.y.model.position(float(grid.j0), across).min()))
        bottom = math.ceil(float(grid.y.model.position(float(grid.j1), across).max()))
        ys = np.arange(max(0, top), min(height, bottom + 1), dtype=np.float64)
        for i in range(columns):
            x = grid.x.model.position(float(grid.i0 + i), ys)
            j = np.floor(grid.y.model.index_at(ys, x)).astype(np.int64) - grid.j0
            ok = (j >= 0) & (j < rows) & (x >= -0.5) & (x <= width - 0.5)
            hit = near[ys[ok].astype(np.int64), np.clip(np.round(x[ok]).astype(np.int64), 0, width - 1)]
            cell = j[ok] * columns + i
            hits += np.bincount(cell, weights=hit, minlength=count)
            samples += np.bincount(cell, minlength=count)
    else:
        down = np.arange(height, dtype=np.float64)
        left = math.floor(float(grid.x.model.position(float(grid.i0), down).min()))
        right = math.ceil(float(grid.x.model.position(float(grid.i1), down).max()))
        xs = np.arange(max(0, left), min(width, right + 1), dtype=np.float64)
        for j in range(rows):
            y = grid.y.model.position(float(grid.j0 + j), xs)
            i = np.floor(grid.x.model.index_at(xs, y)).astype(np.int64) - grid.i0
            ok = (i >= 0) & (i < columns) & (y >= -0.5) & (y <= height - 0.5)
            hit = near[np.clip(np.round(y[ok]).astype(np.int64), 0, height - 1), xs[ok].astype(np.int64)]
            cell = j * columns + i[ok]
            hits += np.bincount(cell, weights=hit, minlength=count)
            samples += np.bincount(cell, minlength=count)
    return np.where(samples > 0, hits / np.maximum(samples, 1), 0.0)
