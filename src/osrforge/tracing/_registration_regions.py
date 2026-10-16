"""The gridded regions of a map image: drawings, their grids, their frames, and each region's fit.

A *drawing* is a connected patch of ink and fill: a framed map with
everything inside its frame, a title, a legend's sample, or a stray mark. A
drawing printed above or beside another on one image and split from it by a
frame line that spans both is cut along that line into two. Each drawing's
pitch is found from its own thin runs, so two drawings at different pitches
get a pitch each.

A drawing is a *region* when it has a printed grid: enough tiles, one pitch
on a side, that hold thin runs on both axes, over at least a few distinct
lines per axis on one lattice. A warped grid is still a grid, so it is a
region, and its fit fails the gate.

A region's rectangle is its frame. The frame's sides are the outermost rows
and columns of the drawing that ink and fill cover across the frame: the
lines of a drawn frame, the edges of a filled one, or, where no frame is
drawn, the outermost grid lines printed across the whole drawing. A title or
a legend that joins the drawing covers no such row. Where no row or column
covers the drawing on an axis, the outermost grid lines the fit finds are
the frame on that axis. On a side where the drawing reaches the image edge,
the grid takes one more line when that line lies outside the image by no more
than half a pitch: the image edge cuts the grid there. A line that would lie
inside the image but isn't printed ends the grid instead.
"""

import math
from dataclasses import dataclass

import numpy as np

from osrforge.tracing._registration_fit import (
    AxisData,
    AxisFit,
    Figures,
    GridFit,
    Settings,
    affine_axis,
    coverage_figures,
    estimate_pitch,
    fit_axis,
    line_figures,
)
from osrforge.tracing._registration_ink import (
    LINE_WIDTH,
    RUN_MINIMUM,
    BoolArray,
    FloatArray,
    InkImage,
    IntArray,
    label_components,
    run_lengths,
)

BLOCK = 4
"""Pixels. Drawings are found on blocks this size, so ink this close together is one drawing."""

SEPARATOR_SPAN = 0.97
"""A thick line across at least this share of a drawing, with no fill on either side, splits it in two."""

SEPARATOR_WIDTH = 2 * LINE_WIDTH
"""Pixels. A line that splits two drawings is at least this thick: twice a grid line's greatest width. Chaotic Caves
page 21 splits its two maps with a line 9 px thick and Monkey Isle page 44 its header with one 11 px thick, while
page 38 prints two of its grid lines 5 px thick across the whole map."""

FRAME_COVERAGE = 0.75
"""A row or column can be a side of the frame when ink or fill covers at least this share of the frame's span. At
0.9 the fold halves' frames, whose sides open for a corridor over a frame only 11 cells tall, aren't found."""

FRAME_GAP = 2.0
"""Pitches. A side of the frame has no gap in its ink and fill longer than this: a corridor that runs off the frame
opens a gap a cell or two wide in it, and a row of hatching or of paper between grid lines is mostly gaps."""

MIN_REGION_TILES = 12
"""A drawing needs at least this many gridded tiles to be a region."""

MIN_DISTINCT_LINES = 4
"""A drawing's grid needs at least this many distinct thin lines on each axis to be a region. The fold maps' east
half has one room of 5 by 5 cells, with walls on its outer lines, so it shows 4 thin lines on each axis."""

PHASE_TOLERANCE = 0.15
"""Pitches. A run pixel this close to its tile's lattice line is on the grid."""

FIT_RUN_PITCHES = 0.8
"""The fit's thin runs are at least this many pitches long: a grid line crosses a cell, a stroke of a symbol doesn't."""


type Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class RegionResult:
    """One region's rectangle and fit, in plain values."""

    rect: Rect
    pitch: tuple[float, float] | None = None
    figures: Figures | None = None
    corners: FloatArray | None = None
    reason: str | None = None
    """Why no fit was made, when none was."""


@dataclass(frozen=True)
class Drawing:
    """A patch of ink and fill: its blocks and its pixel bounding box, with any frame line it was split along."""

    blocks: BoolArray
    """The drawing's blocks, over the whole image's block grid."""

    box: Rect
    """`(x0, y0, x1, y1)` in pixels, the end exclusive."""


@dataclass(frozen=True)
class Candidate:
    """A drawing with a printed grid, before its fit."""

    drawing: Drawing
    fit_mask: BoolArray
    """The pixels the fit reads: the drawing's tiles on the grid and its filled tiles."""

    pitch: tuple[float, float]


def find_regions(image: InkImage, settings: Settings) -> list[RegionResult]:
    """Find every gridded region of an image and fit it, in page order."""
    vertical = image.vertical_runs(RUN_MINIMUM)
    horizontal = image.horizontal_runs(RUN_MINIMUM)
    structure = image.ink | image.fill
    results: list[RegionResult] = []
    for drawing in find_drawings(image, structure):
        candidate = grid_candidate(image, drawing, vertical, horizontal)
        if candidate is not None:
            results.append(fit_region(image, structure, candidate, settings))
    results.sort(key=lambda result: (result.rect[1], result.rect[0]))
    return results


# Drawings.


def find_drawings(image: InkImage, structure: BoolArray) -> list[Drawing]:
    """The image's drawings: connected blocks of ink and fill, split along frame lines that span them."""
    blocks = _blocks(structure)
    labels, count = label_components(blocks)
    if count == 0:
        return []
    rows, columns = np.nonzero(labels)
    label = labels[rows, columns]
    lo_row = np.full(count + 1, labels.shape[0])
    hi_row = np.full(count + 1, -1)
    lo_column = np.full(count + 1, labels.shape[1])
    hi_column = np.full(count + 1, -1)
    np.minimum.at(lo_row, label, rows)
    np.maximum.at(hi_row, label, rows)
    np.minimum.at(lo_column, label, columns)
    np.maximum.at(hi_column, label, columns)
    smallest = math.ceil(4 * 8 / BLOCK)
    thick_horizontal = image.thick_horizontal()
    thick_vertical = image.thick_vertical()
    drawings: list[Drawing] = []
    for k in range(1, count + 1):
        if hi_row[k] - lo_row[k] + 1 < smallest or hi_column[k] - lo_column[k] + 1 < smallest:
            continue
        drawings.extend(_split(image, labels == k, thick_horizontal, thick_vertical))
    return drawings


def _blocks(mask: BoolArray) -> BoolArray:
    height, width = mask.shape
    rows, columns = math.ceil(height / BLOCK), math.ceil(width / BLOCK)
    padded = np.zeros((rows * BLOCK, columns * BLOCK), dtype=np.bool_)
    padded[:height, :width] = mask
    return padded.reshape(rows, BLOCK, columns, BLOCK).any(axis=(1, 3))


def _box(blocks: BoolArray, height: int, width: int) -> Rect:
    rows, columns = np.nonzero(blocks)
    return (
        int(columns.min()) * BLOCK,
        int(rows.min()) * BLOCK,
        min(width, (int(columns.max()) + 1) * BLOCK),
        min(height, (int(rows.max()) + 1) * BLOCK),
    )


def _runs(mask: BoolArray) -> list[tuple[int, int]]:
    """The runs of true values in a 1-D mask, as `(first, last)` pairs."""
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    step = np.diff(padded)
    starts = np.nonzero(step == 1)[0]
    ends = np.nonzero(step == -1)[0]
    return [(int(first), int(end) - 1) for first, end in zip(starts, ends, strict=True)]


def _separators(image: InkImage, thick: BoolArray, box: Rect, axis: int) -> list[tuple[int, int]]:
    """The frame lines across a drawing's box: thick lines spanning it with no fill just outside them.

    Args:
        image: The image's masks.
        thick: The thick runs along the lines' direction.
        box: The drawing's box.
        axis: 0 for horizontal lines (rows), 1 for vertical lines (columns).
    """
    x0, y0, x1, y1 = box
    window = thick[y0:y1, x0:x1]
    fill = image.fill[y0:y1, x0:x1]
    covered = window.mean(axis=1 - axis) >= SEPARATOR_SPAN
    found: list[tuple[int, int]] = []
    length = covered.shape[0]
    for first, last in _runs(covered):
        if last - first + 1 < SEPARATOR_WIDTH:
            continue
        outside = [k for k in (first - 2, last + 2) if 0 <= k < length]
        if all(float(fill.take(k, axis=axis).mean()) < 0.5 for k in outside):
            offset = y0 if axis == 0 else x0
            found.append((first + offset, last + offset))
    return found


def _split(image: InkImage, component: BoolArray, thick_h: BoolArray, thick_v: BoolArray) -> list[Drawing]:
    height, width = image.height, image.width
    box = _box(component, height, width)
    rows = _separators(image, thick_h, box, axis=0)
    columns = _separators(image, thick_v, box, axis=1)
    if not rows and not columns:
        return [Drawing(component, box)]
    cut = component.copy()
    for first, last in rows:
        cut[first // BLOCK : last // BLOCK + 1, :] = False
    for first, last in columns:
        cut[:, first // BLOCK : last // BLOCK + 1] = False
    labels, count = label_components(cut)
    drawings: list[Drawing] = []
    reach = 3 * BLOCK
    for k in range(1, count + 1):
        part = labels == k
        x0, y0, x1, y1 = _box(part, height, width)
        # The frame line a drawing was split along stays part of its box, on each side.
        for first, last in rows:
            if last < y0 and y0 - last <= reach:
                y0 = first
            if first >= y1 and first - y1 < reach:
                y1 = last + 1
        for first, last in columns:
            if last < x0 and x0 - last <= reach:
                x0 = first
            if first >= x1 and first - x1 < reach:
                x1 = last + 1
        drawings.append(Drawing(part, (x0, y0, x1, y1)))
    return drawings


# The grid test.


def _pixels(blocks: BoolArray, height: int, width: int) -> BoolArray:
    return np.repeat(np.repeat(blocks, BLOCK, axis=0), BLOCK, axis=1)[:height, :width]


def _tile_counts(mask: BoolArray, box: Rect, tile: int) -> IntArray:
    x0, y0, x1, y1 = box
    window = mask[y0:y1, x0:x1]
    rows, columns = math.ceil((y1 - y0) / tile), math.ceil((x1 - x0) / tile)
    padded = np.zeros((rows * tile, columns * tile), dtype=np.int64)
    padded[: y1 - y0, : x1 - x0] = window
    return padded.reshape(rows, tile, columns, tile).sum(axis=(1, 3))


def _grow(tiles: BoolArray, radius: int) -> BoolArray:
    out = tiles.copy()
    height, width = tiles.shape
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            shifted = np.zeros_like(tiles)
            shifted[max(0, dy) : height + min(0, dy), max(0, dx) : width + min(0, dx)] = tiles[
                max(0, -dy) : height + min(0, -dy), max(0, -dx) : width + min(0, -dx)
            ]
            out |= shifted
    return out


def _circular_offsets(phase: FloatArray, group: IntArray, groups: int) -> FloatArray:
    """The circular mean of each group's phases, in -0.5 to 0.5, and 0 for an empty group."""
    angle = 2 * np.pi * phase
    cos = np.bincount(group, weights=np.cos(angle), minlength=groups)
    sin = np.bincount(group, weights=np.sin(angle), minlength=groups)
    return np.arctan2(sin, cos) / (2 * np.pi)


def grid_candidate(image: InkImage, drawing: Drawing, vertical: BoolArray, horizontal: BoolArray) -> Candidate | None:
    """Test a drawing for a printed grid, and find its pitch and the pixels its fit reads.

    The drawing is tiled at its own pitch. A tile is *gridded* when it holds
    thin vertical and thin horizontal runs, three tenths of a tile's length of
    each, and the drawing needs `MIN_REGION_TILES` of them. Each axis gets an
    affine fit from the drawing's runs. A run pixel is *on the grid* when it
    lies within `PHASE_TOLERANCE` of the lattice, after the offset of its tile
    row (for vertical lines) or tile column (for horizontal lines), and the
    drawing needs `MIN_DISTINCT_LINES` lattice lines per axis with a tile's
    length of such pixels. The offset lets a smooth drift pass. A tile is on
    the grid when six tenths of its run pixels on each axis are, which fails a
    legend's boxes, off phase against the grid lines beside them. The fit
    reads the tiles on the grid and the filled tiles beside them.

    Returns:
        The candidate, or `None` when the drawing has no printed grid.
    """
    height, width = image.height, image.width
    mask = _pixels(drawing.blocks, height, width)
    x0, y0, x1, y1 = drawing.box
    v = vertical & mask
    h = horizontal & mask
    if int(v.sum()) < MIN_DISTINCT_LINES * RUN_MINIMUM or int(h.sum()) < MIN_DISTINCT_LINES * RUN_MINIMUM:
        return None
    pitch_x = estimate_pitch(v[y0:y1, x0:x1].sum(axis=0).astype(np.float64))
    pitch_y = estimate_pitch(h[y0:y1, x0:x1].sum(axis=1).astype(np.float64))
    if pitch_x is None or pitch_y is None:
        return None
    tile = max(8, round((pitch_x + pitch_y) / 2))
    box = drawing.box
    v_count = _tile_counts(v, box, tile)
    h_count = _tile_counts(h, box, tile)
    filled = _tile_counts(image.fill & mask, box, tile) >= 0.5 * tile * tile
    gridded = (v_count >= 0.3 * tile) & (h_count >= 0.3 * tile)
    if int(gridded.sum()) < MIN_REGION_TILES:
        return None
    yv, xv = np.nonzero(v)
    yh, xh = np.nonzero(h)
    ax = affine_axis(AxisData(xv.astype(np.float64), yv.astype(np.float64), x0, x1, y0, y1), pitch_x)
    ay = affine_axis(AxisData(yh.astype(np.float64), xh.astype(np.float64), y0, y1, x0, x1), pitch_y)
    if ax is None or ay is None:
        return None
    tile_rows, tile_columns = gridded.shape
    phase_v = (xv - ax.shear * yv - ax.origin) / ax.pitch
    phase_h = (yh - ay.shear * xh - ay.origin) / ay.pitch
    row_v = (yv - y0) // tile
    column_h = (xh - x0) // tile
    wrapped_v = phase_v - _circular_offsets(phase_v, row_v, tile_rows)[row_v]
    wrapped_h = phase_h - _circular_offsets(phase_h, column_h, tile_columns)[column_h]
    on_v = np.abs(wrapped_v - np.round(wrapped_v)) <= PHASE_TOLERANCE
    on_h = np.abs(wrapped_h - np.round(wrapped_h)) <= PHASE_TOLERANCE
    tile_v = row_v * tile_columns + (xv - x0) // tile
    tile_h = ((yh - y0) // tile) * tile_columns + column_h
    count = tile_rows * tile_columns
    v_total = np.bincount(tile_v, minlength=count).reshape(gridded.shape)
    v_on = np.bincount(tile_v[on_v], minlength=count).reshape(gridded.shape)
    h_total = np.bincount(tile_h, minlength=count).reshape(gridded.shape)
    h_on = np.bincount(tile_h[on_h], minlength=count).reshape(gridded.shape)
    on_grid = (v_on >= 0.6 * v_total) & (v_total >= 0.3 * tile) & (h_on >= 0.6 * h_total) & (h_total >= 0.3 * tile)
    if not on_grid.any():
        return None
    # Distinct lines: lattice lines with a cell's length of run pixels on the grid, anywhere in the drawing.
    support_x = np.unique(np.round(phase_v[on_v]).astype(np.int64), return_counts=True)[1]
    support_y = np.unique(np.round(phase_h[on_h]).astype(np.int64), return_counts=True)[1]
    if int((support_x >= tile).sum()) < MIN_DISTINCT_LINES or int((support_y >= tile).sum()) < MIN_DISTINCT_LINES:
        return None
    kept = (on_grid | filled) & _grow(on_grid, 1)
    fit_mask = np.zeros((height, width), dtype=np.bool_)
    tiles_up = np.repeat(np.repeat(kept, tile, axis=0), tile, axis=1)
    fit_mask[y0:y1, x0:x1] = tiles_up[: y1 - y0, : x1 - x0]
    fit_mask &= mask
    return Candidate(drawing, fit_mask, (ax.pitch, ay.pitch))


# The frame.


def frame_sides(structure: BoolArray, box: Rect, core: Rect, pitch: tuple[float, float]) -> tuple[float | None, ...]:
    """The frame's sides `(left, top, right, bottom)` in pixels, each `None` where no frame covers that axis.

    The rows of the drawing's box that ink and fill cover across the frame's
    width, and the columns covered across its height, are found in turn, each
    time over the span the other found, until the spans settle. The first
    spans are the grid's core: the tiles the fit reads, which leave out a
    title or a legend that joins the drawing. The outermost covered rows and
    columns are the frame. An axis whose covered rows lie less than two
    pitches apart has no frame.
    """
    x0, y0, x1, y1 = box
    window = structure[y0:y1, x0:x1]
    span_x, span_y = (core[0] - x0, core[2] - x0), (core[1] - y0, core[3] - y0)
    rows = columns = np.zeros(0, dtype=np.int64)
    for _ in range(4):
        rows = _covered(window[:, span_x[0] : span_x[1]], FRAME_GAP * pitch[0])
        columns = _covered(window[span_y[0] : span_y[1], :].T, FRAME_GAP * pitch[1])
        new_x = (int(columns[0]), int(columns[-1]) + 1) if _spans(columns, pitch[0]) else (0, x1 - x0)
        new_y = (int(rows[0]), int(rows[-1]) + 1) if _spans(rows, pitch[1]) else (0, y1 - y0)
        if (new_x, new_y) == (span_x, span_y):
            break
        span_x, span_y = new_x, new_y
    left = right = top = bottom = None
    if _spans(columns, pitch[0]):
        left, right = float(columns[0] + x0), float(columns[-1] + x0)
    if _spans(rows, pitch[1]):
        top, bottom = float(rows[0] + y0), float(rows[-1] + y0)
    return left, top, right, bottom


def _covered(lines: BoolArray, gap: float) -> IntArray:
    """The indices of the rows of `lines` that ink and fill cover enough to be a side of a frame."""
    if lines.shape[1] == 0:
        return np.zeros(0, dtype=np.int64)
    longest = run_lengths(~lines, 1).max(axis=1)
    return np.nonzero((lines.mean(axis=1) >= FRAME_COVERAGE) & (longest <= gap))[0]


def _spans(lines: IntArray, pitch: float) -> bool:
    return len(lines) >= 2 and int(lines[-1] - lines[0]) >= 2 * pitch


def _printed(fit: AxisFit, settings: Settings) -> tuple[int, int] | None:
    """The first and last lines of an axis that the fit explains a band peak on."""
    explained = [
        index
        for index, residual in zip(fit.indices, fit.residuals, strict=True)
        if index is not None and abs(residual) <= settings.line_tolerance * fit.pitch
    ]
    if not explained:
        return None
    return min(explained), max(explained)


# The fit.


def _axis_data(runs: BoolArray, rect: Rect, vertical: bool) -> AxisData:
    x0, y0, x1, y1 = rect
    window = np.zeros_like(runs)
    window[y0:y1, x0:x1] = runs[y0:y1, x0:x1]
    ys, xs = np.nonzero(window)
    if vertical:
        return AxisData(xs.astype(np.float64), ys.astype(np.float64), x0, x1, y0, y1)
    return AxisData(ys.astype(np.float64), xs.astype(np.float64), y0, y1, x0, x1)


def _extent(
    fit: AxisFit,
    first: float | None,
    last: float | None,
    middle: float,
    reach: tuple[bool, bool],
    size: int,
    settings: Settings,
) -> tuple[int, int] | None:
    """The first and last grid lines of one axis.

    Args:
        fit: The axis's fit.
        first: The frame's near side, or `None` for the first printed line.
        last: The frame's far side, or `None` for the last printed line.
        middle: The middle of the region along the other axis, where the lines are read.
        reach: Whether the drawing reaches the image's near and far edges on this axis.
        size: The image's size along this axis.
        settings: The gate's settings.

    Returns:
        The first and last line indices, or `None` when no line is printed.
    """
    printed = _printed(fit, settings)
    if first is not None:
        lo = round(float(fit.model.index_at(first, middle)))
    elif printed is not None:
        lo = printed[0]
    else:
        return None
    if last is not None:
        hi = round(float(fit.model.index_at(last, middle)))
    elif printed is not None:
        hi = printed[1]
    else:
        return None
    # The image edge cuts the grid when the next line lies outside the image, by no more than half a pitch.
    half = fit.pitch / 2
    if reach[0]:
        before = float(fit.model.position(float(lo - 1), middle))
        if -0.5 - half <= before < -0.5:
            lo -= 1
    if reach[1]:
        after = float(fit.model.position(float(hi + 1), middle))
        if size - 0.5 < after <= size - 0.5 + half:
            hi += 1
    return lo, hi


def fit_region(image: InkImage, structure: BoolArray, candidate: Candidate, settings: Settings) -> RegionResult:
    """Fit the grid of one region and compute the gate's figures."""
    height, width = image.height, image.width
    box = candidate.drawing.box
    pitch_x, pitch_y = candidate.pitch
    rows, columns = np.nonzero(candidate.fit_mask[box[1] : box[3], box[0] : box[2]])
    core = (
        box[0] + int(columns.min()),
        box[1] + int(rows.min()),
        box[0] + int(columns.max()) + 1,
        box[1] + int(rows.max()) + 1,
    )
    left, top, right, bottom = frame_sides(structure, box, core, candidate.pitch)
    window = (
        box[0] if left is None else int(left),
        box[1] if top is None else int(top),
        box[2] if right is None else int(right) + 1,
        box[3] if bottom is None else int(bottom) + 1,
    )
    vertical = image.vertical_runs(max(RUN_MINIMUM, round(FIT_RUN_PITCHES * pitch_y))) & candidate.fit_mask
    horizontal = image.horizontal_runs(max(RUN_MINIMUM, round(FIT_RUN_PITCHES * pitch_x))) & candidate.fit_mask
    x_fit = fit_axis(_axis_data(vertical, window, True), pitch_y, settings, pitch_x)
    y_fit = fit_axis(_axis_data(horizontal, window, False), pitch_x, settings, pitch_y)
    if x_fit is None or y_fit is None:
        return RegionResult(window, reason="fewer than 3 grid lines found on an axis")
    x_middle, y_middle = (window[0] + window[2]) / 2, (window[1] + window[3]) / 2
    columns = _extent(x_fit, left, right, y_middle, (box[0] == 0, box[2] == width), width, settings)
    rows = _extent(y_fit, top, bottom, x_middle, (box[1] == 0, box[3] == height), height, settings)
    if columns is None or rows is None:
        return RegionResult(window, reason="the fit explains no grid line on an axis")
    if columns[1] <= columns[0] or rows[1] <= rows[0]:
        return RegionResult(window, reason="the fitted grid is less than a cell across")
    grid = GridFit(x_fit, y_fit, columns[0], columns[1], rows[0], rows[1])
    corners = grid.corners()
    # The rectangle is the frame where one is drawn and the grid elsewhere, reaching any line the image edge cuts.
    xs, ys = corners[..., 0], corners[..., 1]
    near = (float(xs.min()), float(ys.min()))
    far = (float(xs.max()), float(ys.max()))
    rect = (
        max(0, math.floor(min(near[0], near[0] if left is None else left) + 0.5)),
        max(0, math.floor(min(near[1], near[1] if top is None else top) + 0.5)),
        min(width, math.floor(max(far[0], far[0] if right is None else right) + 0.5) + 1),
        min(height, math.floor(max(far[1], far[1] if bottom is None else bottom) + 0.5) + 1),
    )
    pitch = grid.pitches()
    figures = _figures(grid, image, pitch, settings)
    return RegionResult(rect, pitch, figures, corners)


def _figures(grid: GridFit, image: InkImage, pitch: tuple[float, float], settings: Settings) -> Figures:
    totals = [0, 0, 0, 0]
    for fit, axis_pitch in ((grid.x, pitch[0]), (grid.y, pitch[1])):
        for k, value in enumerate(line_figures(fit, axis_pitch, settings)):
            totals[k] += value
    counted, covered = coverage_figures(grid, image.ink, image.fill, pitch, settings)
    return Figures(totals[0], totals[1], totals[2], totals[3], counted, covered)
