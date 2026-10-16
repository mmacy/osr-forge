"""Read a map's cells and edges from the ink alone, on a fitted grid.

`classify` measures every cell and every grid edge of a region and decides
each one's truth-format letter with a confidence. The rules are the same for
every style, and each style sets the thresholds they use (see `styles.py`
and the README).
"""

from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from .gridfit import GridFit, run_lengths
from .ink import Materials, sample
from .styles import Style

SIDE_SAMPLES = 24
"""Positions sampled along an edge."""


@dataclass(frozen=True)
class CellFeatures:
    """Per-cell measurements, each an array of shape `(rows, columns)`."""

    floor: np.ndarray
    """The share of the cell's interior that is floor, among floor and fill."""

    paper: np.ndarray
    """The share that is bare paper."""

    fill: np.ndarray
    """The share that is fill."""

    ink: np.ndarray
    """The share that is ink."""

    stair: np.ndarray
    """0 for no stair, 1 for a stair that runs east and west, 2 for north and south."""


@dataclass(frozen=True)
class EdgeFeatures:
    """Per-edge measurements for one orientation, each an array of shape `(lines, cells_along)`.

    For vertical edges the shape is `(columns + 1, rows)`, for horizontal `(rows + 1, columns)`.
    """

    wall: np.ndarray
    """Ink across the line, in pixels, the median along the edge's middle."""

    cover: np.ndarray
    """The share of the edge's length with dark ink on the line."""

    line: np.ndarray
    """The share of the edge's length with a printed line of any weight on it."""

    fill: np.ndarray
    """The share of the edge's length where fill lies on the line."""

    offset_wall: np.ndarray
    """The strongest parallel ink line within 0.4 cells of the edge, in pixels of ink."""

    offset_at: np.ndarray
    """That line's offset from the edge in pixels, signed."""

    snapped: np.ndarray
    """Whether the edge's symbols were read on that parallel line instead of its own."""

    gap: np.ndarray
    """The longest run of paper along the line, as a share of the edge, 0 when ink covers it."""

    gap_centre: np.ndarray
    """Where that run's centre lies along the edge, 0 to 1."""

    gap_ends: np.ndarray
    """Whether ink lies on the line beyond both ends of the run."""

    ends_cover: np.ndarray
    """The share of the line outside the run that is dark: the wall the symbol sits on."""

    across: np.ndarray
    """At the run's centre, the paper reach across the line to the first ink, in pixels: the lesser of the two sides."""

    across_far: np.ndarray
    """The greater of the two sides."""

    across_shape: np.ndarray
    """The paper reach a third of the way toward the run's ends, as a share of `across`: 1 for a box, less for a
    circle."""

    box_sides: np.ndarray
    """How many sides of the line a box outline lies on: 2 for a box that straddles the wall, 1 for one hung on it."""

    box: np.ndarray
    """Whether a door's box was found: an outline on both sides of the line, closed at both ends."""

    inside_ink: np.ndarray
    """The share of dark ink inside the run's box, where a secret door's S is printed."""

    bars: np.ndarray
    """Whether ink crosses the line at both ends of the run, reaching out past the wall: an archway's posts."""

    glyph: np.ndarray
    """The longest run along the edge with ink close beside the line on both sides, as a share of the edge."""

    glyph_clean: np.ndarray
    """The share of the edge outside that run with nothing beside the line."""

    glyph_line: np.ndarray
    """The share of the edge outside that run with dark ink on the line: the wall the glyph sits on."""

    run_touch: np.ndarray
    """Whether the longest dark run on the line reaches an end of the edge, as a wall does and a label doesn't."""

    dashes: np.ndarray
    """How many dark runs lie on the line."""

    dash_length: np.ndarray
    """The median dark run's length, as a share of the edge."""

    dash_width: np.ndarray
    """The median ink width across the line over the dark runs, in pixels: thin for dashes, thick for text."""

    line_at: np.ndarray
    """Where the printed line was read, in pixels from the fitted line: east or south positive."""


@dataclass(frozen=True)
class Reading:
    """The classifier's reading of one region."""

    cells: np.ndarray
    """Cell letters, shape `(rows, columns)`, dtype `<U1`."""

    cell_confidence: np.ndarray
    vertical: np.ndarray
    """Edge letters on vertical lines, shape `(rows, columns + 1)`."""

    vertical_confidence: np.ndarray
    horizontal: np.ndarray
    """Edge letters on horizontal lines, shape `(rows + 1, columns)`."""

    horizontal_confidence: np.ndarray
    partial: np.ndarray
    """Cells between a quarter and three quarters floor, shape `(rows, columns)`."""

    cave: np.ndarray
    """Cells whose floor boundary is drawn off the grid, shape `(rows, columns)`."""

    cell_features: CellFeatures
    vertical_features: EdgeFeatures
    horizontal_features: EdgeFeatures


def _runs(mask: np.ndarray) -> np.ndarray:
    """Count the runs of true values along the last axis."""
    starts = mask & ~np.concatenate([np.zeros((*mask.shape[:-1], 1), dtype=bool), mask[..., :-1]], axis=-1)
    return starts.sum(axis=-1)


def _run_lengths(row: np.ndarray) -> list[int]:
    """The lengths of the runs of true values."""
    lengths = []
    k = 0
    n = len(row)
    while k < n:
        if row[k]:
            start = k
            while k < n and row[k]:
                k += 1
            lengths.append(k - start)
        else:
            k += 1
    return lengths


def _longest_run(row: np.ndarray) -> tuple[int, int, int]:
    """The longest run of true values: `(length, start, end)`, the end exclusive."""
    best = (0, 0, 0)
    k = 0
    n = len(row)
    while k < n:
        if row[k]:
            start = k
            while k < n and row[k]:
                k += 1
            if k - start > best[0]:
                best = (k - start, start, k)
        else:
            k += 1
    return best


def cell_features(materials: Materials, fit: GridFit, hatch_runs: int) -> CellFeatures:
    """Measure every cell of the grid."""
    columns, rows = fit.columns, fit.rows
    inner = np.linspace(0.15, 0.85, 8)
    u, v = np.meshgrid(inner, inner)
    i = np.arange(columns)[None, :, None, None] + u[None, None]
    j = np.arange(rows)[:, None, None, None] + v[None, None]
    x, y = fit.corners(i, j)
    closed = sample(materials.floor, x, y).mean(axis=(2, 3))
    paper = sample(materials.paper, x, y).mean(axis=(2, 3))
    fill = sample(materials.fill, x, y).mean(axis=(2, 3))
    ink = sample(materials.ink, x, y).mean(axis=(2, 3))
    floor = closed / np.maximum(closed + fill, 1e-6)
    floor = np.where(closed + fill < 0.2, closed, floor)
    fine = np.linspace(0.12, 0.88, 14)
    uf, vf = np.meshgrid(fine, fine)
    i = np.arange(columns)[None, :, None, None] + uf[None, None]
    j = np.arange(rows)[:, None, None, None] + vf[None, None]
    x, y = fit.corners(i, j)
    dark = sample(materials.inkness, x, y) > 0.4
    runs_x = np.percentile(_runs(dark), 75, axis=2)
    runs_y = np.percentile(_runs(np.swapaxes(dark, 2, 3)), 75, axis=2)
    dark_share = dark.mean(axis=(2, 3))
    stair = np.zeros((rows, columns), dtype=np.int8)
    hatched = (closed > 0.3) & (dark_share < 0.6)
    stair[hatched & (runs_x >= hatch_runs) & (runs_y <= 2)] = 1
    stair[hatched & (runs_y >= hatch_runs) & (runs_x <= 2)] = 2
    return CellFeatures(floor=floor, paper=paper, fill=fill, ink=ink, stair=stair)


EXTRA = 6
"""Positions sampled beyond each end of an edge, so a symbol that straddles the edge's end is still found."""


def _edge_patches(fit: GridFit, orientation: str, reach: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel positions of the sample patch of every edge of one orientation.

    Returns:
        `(x, y)`: arrays of shape `(lines, cells, SIDE_SAMPLES + 2 * EXTRA, 2 * reach + 1)`. Samples run along the
        edge from a quarter cell before it to a quarter cell after it; offsets across run from `-reach` to `reach`,
        east or south positive.
    """
    along = (np.arange(-EXTRA, SIDE_SAMPLES + EXTRA) + 0.5) / SIDE_SAMPLES
    count = len(along)
    offsets = np.arange(-reach, reach + 1, dtype=np.float64)
    if orientation == "vertical":
        lines = np.arange(fit.columns + 1)
        cells = np.arange(fit.rows)
        i = lines[:, None, None] + np.zeros((1, len(cells), count))
        j = cells[None, :, None] + along[None, None, :]
    else:
        lines = np.arange(fit.rows + 1)
        cells = np.arange(fit.columns)
        i = cells[None, :, None] + along[None, None, :]
        j = lines[:, None, None] + np.zeros((1, len(cells), count))
    x, y = fit.corners(i, j)
    if orientation == "vertical":
        x = x[..., None] + offsets
        y = y[..., None] + np.zeros_like(offsets)
    else:
        x = x[..., None] + np.zeros_like(offsets)
        y = y[..., None] + offsets
    return x, y


def _nearest_line(density: np.ndarray, small: int) -> int:
    """The offset of the printed line nearest the fitted line, from the density of wall ink across it.

    Args:
        density: The share of the edge's length with wall ink, at each offset from `-small` to `small`.
        small: The window's half width.

    Returns:
        The centre of the run of dense offsets nearest 0, or 0 when no offset is dense enough.
    """
    dense = density >= 0.45
    best = 0
    best_distance = None
    k = 0
    n = len(dense)
    while k < n:
        if dense[k]:
            start = k
            while k < n and dense[k]:
                k += 1
            weights = density[start:k]
            centroid = float((weights * np.arange(start, k)).sum() / weights.sum()) - small
            distance = abs(centroid)
            if best_distance is None or distance < best_distance:
                best, best_distance = round(centroid), distance
        else:
            k += 1
    return best


def _thick(dark: np.ndarray) -> np.ndarray:
    """Dark pixels that lie in a run across the line longer than 3 px: a wall band or a box side, not a thin line."""
    across = run_lengths(dark, 1)
    return dark & (across > 3)


def _reach(column: np.ndarray, v0: int, limit: int, skip: int = 2) -> tuple[int, int]:
    """Paper reach from the line to the first dark pixel on each side, ignoring `skip` px either side of the line."""
    lo = column[: max(v0 - skip, 0)][::-1]
    hi = column[v0 + skip + 1 :]
    reach_lo = int(np.argmax(lo)) + skip + 1 if lo.any() else limit
    reach_hi = int(np.argmax(hi)) + skip + 1 if hi.any() else limit
    return min(reach_lo, limit), min(reach_hi, limit)


def _extent(column: np.ndarray, v0: int) -> tuple[int, int]:
    """How far the dark run through `v0` reaches on each side of it, in pixels, 0 when `v0` is paper."""
    if not column[v0]:
        return 0, 0
    lo = 0
    while v0 - lo - 1 >= 0 and column[v0 - lo - 1]:
        lo += 1
    hi = 0
    while v0 + hi + 1 < len(column) and column[v0 + hi + 1]:
        hi += 1
    return lo, hi


def edge_features(materials: Materials, fit: GridFit, style: Style, orientation: str) -> EdgeFeatures:
    """Measure every edge of one orientation."""
    pitch = fit.pitch
    reach = round(0.5 * pitch) + 3
    x, y = _edge_patches(fit, orientation, reach)
    inkness = sample(materials.inkness, x, y)
    dark = sample(materials.dark, x, y) > 0.5
    wall_dark = sample(materials.wall_dark, x, y) > 0.5
    fill = sample(materials.fill, x, y) > 0.5
    centre = reach
    n = SIDE_SAMPLES
    proper = slice(EXTRA, EXTRA + n)
    middle = slice(EXTRA + int(0.15 * n), EXTRA + int(0.85 * n) + 1)
    width = inkness[..., centre - 5 : centre + 6].sum(axis=3)
    wall = np.median(width[:, :, middle], axis=2)
    line = (inkness[:, :, proper, centre - 3 : centre + 4].max(axis=3) > 0.15).mean(axis=2)
    fill_share = fill[:, :, proper, centre - 2 : centre + 3].all(axis=3).mean(axis=2)

    profile = np.median(inkness[:, :, middle, :], axis=2)
    kernel = np.ones(5)
    smooth = np.apply_along_axis(lambda p: np.convolve(p, kernel, mode="same"), 2, profile)
    band = int(0.5 * pitch)
    window = smooth[:, :, centre - band : centre + band + 1].copy()
    window[:, :, band - 3 : band + 4] = -1
    offset_at = (np.argmax(window, axis=2) - band).astype(np.float64)
    offset_wall = window.max(axis=2)
    density = wall_dark.astype(np.float32).mean(axis=2)
    density = np.apply_along_axis(lambda p: np.convolve(p, np.ones(3) / 3, mode="same"), 2, density)
    small = max(3, int(0.2 * pitch))
    local_window = density[:, :, centre - small : centre + small + 1]

    shape = wall.shape
    cover = np.zeros(shape)
    snapped = np.zeros(shape, dtype=bool)
    gap = np.zeros(shape)
    gap_centre = np.zeros(shape)
    gap_ends = np.zeros(shape, dtype=bool)
    ends_cover = np.ones(shape)
    across = np.zeros(shape)
    across_far = np.zeros(shape)
    across_shape = np.ones(shape)
    box_sides = np.zeros(shape)
    inside_ink = np.zeros(shape)
    bars = np.zeros(shape, dtype=bool)
    glyph = np.zeros(shape)
    glyph_clean = np.ones(shape)
    glyph_line = np.zeros(shape)
    run_touch = np.zeros(shape, dtype=bool)
    dashes = np.zeros(shape, dtype=np.int64)
    dash_length = np.zeros(shape)
    dash_width = np.zeros(shape)
    line_at = np.zeros(shape)
    box = np.zeros(shape, dtype=bool)
    tie = 0.5 * pitch - 2
    total = n + 2 * EXTRA
    box_far = int(0.5 * pitch) + 1
    for a in range(shape[0]):
        for b in range(shape[1]):
            v0 = centre + _nearest_line(local_window[a, b], small)
            at_line = wall_dark[a, b, proper, v0 - 3 : v0 + 4].any(axis=1).mean()
            if (
                style.snap_offset_walls
                and at_line < 0.4
                and offset_wall[a, b] >= style.wall_min
                and (abs(offset_at[a, b]) <= tie or offset_at[a, b] > 0)
            ):
                v_try = centre + int(offset_at[a, b])
                v_try = min(max(v_try, 3), 2 * reach - 3)
                there = wall_dark[a, b, proper, v_try - 3 : v_try + 4].any(axis=1)
                t_len, t_start, t_end = _longest_run(there)
                if there.mean() >= 0.45 and t_len > 0 and (t_start == 0 or t_end == n):
                    v0 = v_try
                    snapped[a, b] = True
            v0 = min(max(v0, 3), 2 * reach - 3)
            line_at[a, b] = v0 - centre
            patch = dark[a, b]
            wall_line = wall_dark[a, b, proper, v0 - 3 : v0 + 4].any(axis=1)
            cover[a, b] = wall_line.mean()
            d_len, d_start, d_end = _longest_run(wall_line)
            run_touch[a, b] = d_len > 0 and (d_start == 0 or d_end == n)
            dark_runs = _run_lengths(wall_line)
            paper_runs = _run_lengths(~wall_line)
            dashes[a, b] = len(dark_runs) if sum(1 for run in paper_runs if run >= 2) >= 2 else 0
            dash_length[a, b] = float(np.median(dark_runs)) / n if dark_runs else 0.0
            if wall_line.any():
                dash_width[a, b] = float(np.median(width[a, b, proper][wall_line]))
            limit = min(v0, 2 * reach - v0)

            # A glyph straddling the wall: the ink through the line reaches beyond the wall's own width, on both
            # sides somewhere along one run of the edge, as a secret door's S does.
            extents = np.array([_extent(patch[u], v0) for u in range(total)])
            inked = patch[:, v0]
            if inked.sum() >= 3:
                base_lo = float(np.percentile(extents[inked, 0], 25))
                base_hi = float(np.percentile(extents[inked, 1], 25))
            else:
                base_lo, base_hi = 0.0, 0.0
            bulge_lo = extents[:, 0] >= base_lo + 2
            bulge_hi = extents[:, 1] >= base_hi + 2
            compact = (extents[:, 0] <= style.glyph_reach * pitch) & (extents[:, 1] <= style.glyph_reach * pitch)
            bulge = (bulge_lo | bulge_hi) & compact & ~fill[a, b, :, v0]
            joined = bulge.copy()
            for shift in (1, 2):
                joined[shift:] |= bulge[:-shift] & np.roll(bulge, -shift - 1)[shift:]
                joined[:-shift] |= bulge[shift:] & np.roll(bulge, shift + 1)[:-shift]
            g_len, g_start, g_end = _longest_run(joined)
            if g_len > 0 and g_start > EXTRA and g_end < EXTRA + n:
                has_lo = bulge_lo[g_start:g_end].any()
                has_hi = bulge_hi[g_start:g_end].any()
                glyph[a, b] = g_len / n if has_lo and has_hi else 0.0
                others = np.ones(total, dtype=bool)
                others[g_start:g_end] = False
                others[:EXTRA] = False
                others[EXTRA + n :] = False
                if others.any():
                    glyph_clean[a, b] = float((~bulge)[others].mean())
                    glyph_line[a, b] = float((extents[others].sum(axis=1) > 0).mean())

            # A door's box, from its two short sides: ink across the line much wider than the wall, with a paper
            # interior between them and a long side beyond the interior on at least one side of the line.
            spans = extents.sum(axis=1) + (patch[:, v0] > 0)
            wall_px = float(np.median(spans[spans > 0])) if (spans > 0).any() else 0.0
            threshold = max(wall_px + 4, 0.25 * pitch)
            ends = spans >= threshold
            end_runs = []
            k = 0
            while k < total:
                if ends[k]:
                    start = k
                    while k < total and ends[k]:
                        k += 1
                    end_runs.append((start, k))
                else:
                    k += 1
            found = False
            for (s1, e1), (s2, e2) in pairwise(end_runs):
                interior = slice(e1, s2)
                length = s2 - e1
                mid = (e1 + s2) / 2
                if not (style.door_length[0] * n <= length <= n and EXTRA <= mid < EXTRA + n):
                    continue
                r_lo = int(min(np.median(extents[s1:e1, 0]), np.median(extents[s2:e2, 0])))
                r_hi = int(min(np.median(extents[s1:e1, 1]), np.median(extents[s2:e2, 1])))
                inner = patch[interior]
                side_lo = (
                    inner[:, max(v0 - r_lo - 2, 0) : max(v0 - r_lo + 3, 1)].any(axis=1).mean() if r_lo >= 3 else 0.0
                )
                side_hi = inner[:, v0 + r_hi - 2 : v0 + r_hi + 3].any(axis=1).mean() if r_hi >= 3 else 0.0
                present_lo = side_lo >= 0.6 and r_lo <= box_far
                present_hi = side_hi >= 0.6 and r_hi <= box_far
                if not (present_lo or present_hi):
                    continue
                core = inner[:, v0 - max(r_lo - 3, 0) : v0 + max(r_hi - 2, 1)]
                if core.size and core.mean() > 0.6:
                    continue
                reaches = np.array([_reach(inner[u], v0, limit, skip=0) for u in range(inner.shape[0])])
                in_lo = int(min(reaches[:, 0].max(), r_lo))
                in_hi = int(min(reaches[:, 1].max(), r_hi))
                core = inner[:, v0 - max(in_lo - 1, 0) : v0 + max(in_hi, 1)]
                found = True
                box[a, b] = True
                box_sides[a, b] = float(present_lo) + float(present_hi)
                gap[a, b] = length / n
                gap_centre[a, b] = (mid - EXTRA) / n
                gap_ends[a, b] = True
                across[a, b] = max(r_lo if present_lo else 0, r_hi if present_hi else 0)
                across_far[a, b] = max(r_lo, r_hi)
                across_shape[a, b] = 1.0
                rest = np.ones(total, dtype=bool)
                rest[s1:e2] = False
                rest[:EXTRA] = False
                rest[EXTRA + n :] = False
                ends_cover[a, b] = float((spans[rest] > 0).mean()) if rest.any() else 1.0
                if core.size:
                    inside_ink[a, b] = float(core.mean())
                line_at[a, b] = v0 - centre
                break
            if found:
                continue

            on_mask = _thick(patch) if style.thick_walls else patch
            on_line = on_mask[:, v0 - 1 : v0 + 2].any(axis=1)
            length, start, end = _longest_run(~on_line)
            if length == 0:
                continue
            mid = (start + end) / 2
            if not EXTRA <= mid < EXTRA + n:
                continue
            gap[a, b] = length / n
            gap_centre[a, b] = (mid - EXTRA) / n
            gap_ends[a, b] = start > 0 and end < total
            rest = np.ones(total, dtype=bool)
            rest[start:end] = False
            rest[:EXTRA] = False
            rest[EXTRA + n :] = False
            ends_cover[a, b] = float(on_line[rest].mean()) if rest.any() else 0.0
            reach_lo, reach_hi = _reach(patch[int(mid)], v0, limit)
            present_lo = 3 <= reach_lo <= box_far
            present_hi = 3 <= reach_hi <= box_far
            across[a, b] = min(reach_lo, reach_hi)
            across_far[a, b] = max(reach_lo, reach_hi)
            post = max(3, int(0.12 * pitch))
            before = patch[max(start - 2, 0) : start, v0 - post : v0 + post + 1]
            after = patch[end : min(end + 2, total), v0 - post : v0 + post + 1]
            bars[a, b] = bool(before.size and after.size and before.all(axis=1).any() and after.all(axis=1).any())
            if not (present_lo and present_hi):
                continue
            span = slice(v0 - reach_lo, v0 + reach_hi + 1)
            end_a = patch[max(start - 1, 0), span].mean()
            end_b = patch[min(end, total - 1), span].mean()
            if end_a < 0.6 or end_b < 0.6:
                continue
            box[a, b] = True
            box_sides[a, b] = 2.0
            across[a, b] = max(reach_lo, reach_hi)
            third = max(1, length // 3)
            shapes = []
            for at in (start + third // 2, end - 1 - third // 2):
                r_lo, r_hi = _reach(patch[min(max(at, 0), total - 1)], v0, limit)
                shapes.append(max(r_lo, r_hi))
            across_shape[a, b] = min(shapes) / max(across[a, b], 1)
    return EdgeFeatures(
        wall=wall,
        cover=cover,
        line=line,
        fill=fill_share,
        offset_wall=offset_wall,
        offset_at=offset_at,
        snapped=snapped,
        gap=gap,
        gap_centre=gap_centre,
        gap_ends=gap_ends,
        ends_cover=ends_cover,
        across=across,
        across_far=across_far,
        across_shape=across_shape,
        box_sides=box_sides,
        box=box,
        inside_ink=inside_ink,
        bars=bars,
        glyph=glyph,
        glyph_clean=glyph_clean,
        glyph_line=glyph_line,
        run_touch=run_touch,
        dashes=dashes,
        dash_length=dash_length,
        dash_width=dash_width,
        line_at=line_at,
    )


def _logistic(margin: np.ndarray, scale: float) -> np.ndarray:
    return 1 / (1 + np.exp(-margin / scale))


def decide_edges(
    features: EdgeFeatures,
    occupied_a: np.ndarray,
    occupied_b: np.ndarray,
    stair_open: np.ndarray,
    pitch: float,
    style: Style,
) -> tuple[np.ndarray, np.ndarray]:
    """Decide every edge of one orientation.

    Args:
        features: The orientation's measurements.
        occupied_a: Whether the cell on one side is floor or water, shape `(lines, cells)`; false off the grid.
        occupied_b: The same for the other side.
        stair_open: Whether a stair cell on either side runs across this edge.
        pitch: The grid's mean pitch, in pixels.
        style: The style's thresholds.

    Returns:
        `(letters, confidence)` arrays of the edge shape.
    """
    f = features
    shape = f.wall.shape
    letters = np.full(shape, "-", dtype="<U1")
    confidence = np.ones(shape)
    touches = occupied_a | occupied_b
    both = occupied_a & occupied_b
    letters[touches & ~both] = "w"
    confidence[touches & ~both] = 0.9

    wall = np.where(f.snapped, f.offset_wall, f.wall)
    penalty = np.clip(f.cover / style.cover_min, 0, 1) ** 2
    penalty = np.where((f.cover < 0.95) & ~f.run_touch, 0.0, penalty)
    wall_eff = np.where(f.snapped, wall, wall * penalty)
    blocked_score = np.maximum(wall_eff, f.fill * 11)
    p_blocked = _logistic(blocked_score - style.wall_min, style.wall_scale)

    on_wall = (f.gap >= style.door_length[0]) & (f.gap <= style.door_length[1]) & f.gap_ends
    boxed = on_wall & f.box & (f.across >= style.door_across[0] * pitch) & (f.ends_cover >= 0.3)
    door = boxed & (f.across_shape >= style.circle_shape)
    circle = boxed & ~door
    half_wall = np.maximum(f.wall, 2) / 2
    slit = (
        on_wall
        & ~f.box
        & (f.gap <= 0.6)
        & (f.across <= half_wall + 2)
        & (f.across_far <= half_wall + 4)
        & (f.wall >= style.wall_min)
    )
    window = circle | slit
    marked = door & (f.inside_ink >= style.secret_inside)
    glyph = (
        (p_blocked > 0.5)
        & ~door
        & ~window
        & (f.glyph >= style.glyph_length[0])
        & (f.glyph <= style.glyph_length[1])
        & (f.glyph_clean >= 0.6)
        & (f.glyph_line >= 0.8)
    )
    dashed = (
        style.dashed_lines
        & ~door
        & ~window
        & (f.dashes >= 3)
        & (f.dash_length >= 2 / SIDE_SAMPLES)
        & (f.cover >= 0.25)
        & (f.cover <= 0.72)
        & (f.gap <= 0.4)
        & (f.dash_width <= 5)
    )
    archway = style.archways & on_wall & ~f.box & ~window & (f.gap <= 0.7) & (f.across_far >= 0.4 * pitch) & f.bars

    letters[both] = np.where(p_blocked[both] > 0.5, "w", "o")
    confidence[both] = np.where(p_blocked[both] > 0.5, p_blocked[both], 1 - p_blocked[both])
    for mask, letter, conf in (
        (both & glyph, "s", 0.7),
        (both & dashed, "p", 0.7),
        (both & archway, "p", 0.7),
        (both & window, "n", 0.8),
        (both & door, "d", 0.85),
        (both & marked, style.inside_letter, 0.8),
        (both & stair_open & ~door & ~window & (f.cover < 0.9), "o", 0.75),
    ):
        letters[mask] = letter
        confidence[mask] = conf
    return letters, confidence


def classify(materials: Materials, fit: GridFit, style: Style, fill_letter: str = "#") -> Reading:
    """Read every cell and edge of a region.

    Args:
        materials: The image's masks.
        fit: The region's grid.
        style: The style's thresholds.
        fill_letter: The letter a fill cell gets: `#` for rock, `~` for water.

    Returns:
        The reading.
    """
    cells = cell_features(materials, fit, style.hatch_runs)
    vertical = edge_features(materials, fit, style, "vertical")
    horizontal = edge_features(materials, fit, style, "horizontal")
    rows, columns = cells.floor.shape

    floor = cells.floor >= 0.5
    if style.floor_needs_grid:
        west = vertical.line[:-1, :].T
        east = vertical.line[1:, :].T
        north = horizontal.line[:-1, :]
        south = horizontal.line[1:, :]
        lines = (west > 0.5).astype(int) + (east > 0.5) + (north > 0.5) + (south > 0.5)
        floor &= lines >= 3
    fill_cells = cells.fill >= 0.3 if fill_letter == "~" else np.ones_like(floor)
    cell_letters = np.where(floor, ".", np.where(fill_cells, fill_letter, "#")).astype("<U1")
    cell_confidence = np.clip(0.5 + np.abs(cells.floor - 0.5), 0.5, 1.0)
    if style.floor_needs_grid:
        cell_confidence = np.where(floor | (cells.floor < 0.5), cell_confidence, 0.6)
    partial = (cells.floor >= 0.25) & (cells.floor <= 0.75)
    cave = np.zeros_like(floor)
    if style.cave:
        cave = (cells.floor > 0.08) & (cells.floor < 0.92)

    occupied = floor | (cell_letters == "~")
    padded = np.zeros((rows + 2, columns + 2), dtype=bool)
    padded[1:-1, 1:-1] = occupied
    stair_pad = np.zeros((rows + 2, columns + 2), dtype=np.int8)
    stair_pad[1:-1, 1:-1] = cells.stair

    west_cells = padded[1:-1, :-1].T
    east_cells = padded[1:-1, 1:].T
    stair_v = (stair_pad[1:-1, :-1].T == 1) | (stair_pad[1:-1, 1:].T == 1)
    v_letters, v_conf = decide_edges(vertical, west_cells, east_cells, stair_v, fit.pitch, style)

    north_cells = padded[:-1, 1:-1]
    south_cells = padded[1:, 1:-1]
    stair_h = (stair_pad[:-1, 1:-1] == 2) | (stair_pad[1:, 1:-1] == 2)
    h_letters, h_conf = decide_edges(horizontal, north_cells, south_cells, stair_h, fit.pitch, style)

    return Reading(
        cells=cell_letters,
        cell_confidence=cell_confidence,
        vertical=v_letters.T,
        vertical_confidence=v_conf.T,
        horizontal=h_letters,
        horizontal_confidence=h_conf,
        partial=partial,
        cave=cave,
        cell_features=cells,
        vertical_features=vertical,
        horizontal_features=horizontal,
    )
