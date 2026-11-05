"""The gridded regions of a map image: drawings, insets, their grids, their frames, and each region's fit.

A *drawing* is a connected patch of ink and fill: a framed map with
everything inside its frame, a title, a legend's sample, or a stray mark. A
drawing printed above or beside another on one image and split from it by a
frame line that spans both is cut along that line into two. Each drawing's
pitch is found from its own thin runs, so two drawings at different pitches
get a pitch each. An *inset*, a framed drawing printed inside another's frame
and ringed by paper, is taken out of it and is a drawing of its own.

A drawing is a *region* when it has a printed grid: enough tiles, one pitch
on a side, that hold thin runs on both axes, over at least a few distinct
lines per axis on one lattice. A warped grid is still a grid, so it is a
region, and its fit fails the gate.

A region's grid is fitted in up to three passes. The first reads only the
drawing's tiles on the grid, and finds the frame along the fitted lines. A
frame is four sides around the printed lines, each covered by ink and fill
from one perpendicular side to the other: a drawn line, the edge of a fill,
or, where no frame is drawn, the outermost grid lines printed across the
whole drawing. The sides are looked for on lines a pixel apart, so a frame
drawn off the grid is found, and the paper between a drawn frame and the
drawing inside it doesn't matter. Of the frames that close, the outermost is
the drawing's, and the grid takes the lines on and inside it. Where no frame
closes, each axis's frame is the outermost lines in one unbroken run of grid
lines that ink and fill cover along their length, and where no line is
covered on an axis, the outermost lines the fit explains are the grid's
extent there. Following the fitted lines, the search follows a scan's skew,
bow, and stretch. The second pass reads all of the drawing's thin runs inside
the frame. The third adds points on a filled frame's edges, which hold the
drift correction to the frame at the far corners where no line is printed.
On a side where the drawing reaches the image edge, the grid takes each
further line that comes within half a pitch of the image edge, inside or
outside it, along the whole side: the image edge cuts the grid there. A line
more than half a pitch inside the image that isn't framed or printed ends the
grid instead. The region's rectangle is its grid's extent, clipped to the
image.
"""

import itertools
import math
from dataclasses import dataclass

import numpy as np

from osrforge.tracing._registration_fit import (
    Anchors,
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
    Float32Array,
    FloatArray,
    InkImage,
    IntArray,
    label_components,
    max_filter_bool,
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

FRAME_BAND = 0.2
"""Pitches. Ink or fill this close to a fitted line lies along it, for finding the frame. At 0.1 one more of the
scans the tests make misses; at 0.3 the tests give the same results."""

ANCHOR_REACH = 0.3
"""Pitches. How far to either side of a fitted frame line its filled edge is looked for. From 0.2 to 0.4 the tests
give the same results."""

ANCHOR_TOLERANCE = 0.15
"""Pitches. A filled edge this close to a fitted frame line anchors the drift fit to it, so a frame drawn off the grid
gives no anchors. From 0.1 to 0.25 the tests give the same results."""

ANCHOR_SHARE = 0.5
"""The share of a frame side's places that must show a filled edge for the side to anchor the fit. From 0.3 to 0.7
the tests give the same results."""

FRAME_RUN = 3
"""Two runs of covered lines with one uncovered line between them are one frame's when each holds at least this many
lines. On sunken temple's first page, scanned with a blur of 1 px, one faint line between two runs of 10 goes
uncovered; on a scan of crypt the lines through its legend, below the frame, are covered in runs of 1 and 2, one or
two lines past it, and joined to the frame they add 4 rows. From 2 to 5 the tests give the same results."""

FRAME_COVERAGE = 0.75
"""A fitted line is covered, as a frame's side is, when ink or fill lies along at least this share of it. A
corridor that runs off a frame only 11 cells tall, as on the fold maps, opens a gap of a cell in its side. At 0.85 a
fold half's grid comes out a column short; at 0.65 the tests give the same results."""

FRAME_GAP = 2.0
"""Pitches. A covered line has no gap in its ink and fill longer than this: a corridor that runs off the frame
opens a gap a cell or two wide in it, and a line through paper between grid lines is mostly gaps. At 1 gatehouse's
west half is fitted over a strip 28 px wide and fails the gate, and at 3 Chaotic Caves page 21's lower map takes 20
rows where 23 are printed."""

FRAME_CORNER = 0.2
"""Pitches. A frame's side reaches each perpendicular side to within this distance: its ink and fill leave no longer
gap at either end. It matches `FRAME_BAND`, the distance across the side that its ink may lie from the line. From 0 to 1
the tests give the same results. At 2 the title above gatehouse's east half, a pitch above its fill, closes a frame
with the fill's sides, and the grid takes a row of the title."""

MIN_REGION_TILES = 12
"""A drawing needs at least this many gridded tiles to be a region."""

MIN_INSET_TILES = 4
"""An inset, framed by its ring of paper, needs at least this many gridded tiles to be a region. The shrine's grotto
has 8: its one room holds stairs and a pool. At 8 two of the scans the tests make go wrong, and at 12 the unscanned
grotto is lost."""

MIN_INSET_LINES = 2
"""An inset at its parent's pitch needs at least this many distinct thin lines on each axis, enough to fix the
phase. The grotto's room shows 2 thin rows: its other lines are walls."""

MIN_DISTINCT_LINES = 4
"""A drawing's grid needs at least this many distinct thin lines on each axis to be a region. The fold maps' east
half has one room of 5 by 5 cells, with walls on its outer lines, so it shows 4 thin lines on each axis."""

PHASE_TOLERANCE = 0.15
"""Pitches. A run pixel this close to its tile's lattice line is on the grid."""

INSET_OPENING = 2
"""Pixels. An inset's fill is opened by this much before it is told apart from the fill around it."""

INSET_BAND = 0.3
"""Pitches. The width of the band of paper around an inset. The grotto's ring is 8 to 10 px wide at a pitch of 26.
At 0.45 two of the scans the tests make go wrong; at 0.2 the tests give the same results."""

INSET_PAPER = 0.3
"""The most of an inset's band that fill may cover. At 0.15 the tests give the same results."""

INSET_CROSSING = 0.25
"""The most grid lines that may cross an inset's band, as a share of the one per pitch that cross it where the grid
runs on. Counted as lines, not as pixels, the test doesn't hang on how wide a scan prints its lines: as a share of
the band's pixels, a threshold of 0.05 makes the walled rooms of Chaotic Caves page 47 insets, since its 3 px lines
at a 75 px pitch cover 4% of a band. At 0.1 the unscanned grotto, whose bands would be crossed by about six lines,
is lost; at 0.5 the tests give the same results."""

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

    cover: BoolArray | None = None
    """The blocks whose ink and fill cover the drawing's frame, when they are more than its own: a drawing with an
    inset taken out of it keeps the inset's blocks here, because the inset lies inside its frame."""


@dataclass(frozen=True)
class Candidate:
    """A drawing with a printed grid, before its fit."""

    drawing: Drawing
    mask: BoolArray
    """The drawing's pixels, which the final fit reads inside the frame."""

    tiles: BoolArray
    """The drawing's pixels in its tiles on the grid and the filled tiles beside them, which the first fit reads to
    find the frame: they leave out a title, a legend, and the faint ripples a JPEG leaves beside a fill's edge."""

    cover: BoolArray
    """The pixels whose ink and fill cover the frame."""

    core: Rect
    """The extent of the drawing's tiles on the grid and the filled tiles beside them, where the frame search starts:
    it leaves out a title or a legend that joins the drawing."""

    pitch: tuple[float, float]


def find_regions(image: InkImage, settings: Settings) -> list[RegionResult]:
    """Find every gridded region of an image and fit it, in page order."""
    vertical = image.vertical_runs(RUN_MINIMUM)
    horizontal = image.horizontal_runs(RUN_MINIMUM)
    structure = image.ink | image.fill
    results: list[RegionResult] = []
    for drawing in find_drawings(image, structure):
        candidate = grid_candidate(image, drawing, vertical, horizontal)
        if candidate is None:
            continue
        insets = find_insets(image, drawing, candidate.pitch)
        if insets:
            parent = drawing.blocks.copy()
            for inset in insets:
                parent &= ~inset.blocks
            whole = grid_candidate(
                image, Drawing(parent, _box(parent, image.height, image.width), drawing.blocks), vertical, horizontal
            )
            if whole is not None:
                results.append(fit_region(image, structure, whole, settings))
            for inset in insets:
                # An inset holds too little grid for a pitch of its own to be sure, and is usually drawn at its
                # parent's scale, so the parent's pitch is tried first.
                piece = grid_candidate(
                    image, inset, vertical, horizontal, MIN_INSET_TILES, candidate.pitch, MIN_INSET_LINES
                ) or grid_candidate(image, inset, vertical, horizontal, MIN_INSET_TILES)
                if piece is not None:
                    results.append(fit_region(image, structure, piece, settings))
            continue
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
        # The frame line a drawing was split along stays part of it, on each side.
        for first, last in rows:
            if (last < y0 and y0 - last <= reach) or (first >= y1 and first - y1 < reach):
                band = np.zeros_like(part)
                band[first // BLOCK : last // BLOCK + 1, x0 // BLOCK : math.ceil(x1 / BLOCK)] = True
                part |= band & component
        for first, last in columns:
            if (last < x0 and x0 - last <= reach) or (first >= x1 and first - x1 < reach):
                band = np.zeros_like(part)
                band[y0 // BLOCK : math.ceil(y1 / BLOCK), first // BLOCK : last // BLOCK + 1] = True
                part |= band & component
        drawings.append(Drawing(part, _box(part, height, width)))
    return drawings


# Insets.


def find_insets(image: InkImage, drawing: Drawing, pitch: tuple[float, float]) -> list[Drawing]:
    """The framed insets inside a drawing: bodies of fill that stand alone, ringed by paper the grid doesn't cross.

    An inset printed inside another drawing's frame is a drawing of its own,
    with its own grid, though a label or a scan's speckle joins it to the
    drawing around it. Its body of fill, opened by `INSET_OPENING` so a
    label's thin strokes don't join it to the fill around it, is at least
    three pitches across, and a band `INSET_BAND` of a pitch wide around it
    is paper on all four sides: fill covers less than `INSET_PAPER` of the
    band, and at most `INSET_CROSSING` of the grid lines that would cross it,
    one a pitch, do. Rock inside a cave's floor and a room walled in solid
    ink fail the last test, because the grid's lines run on up to them.

    Returns:
        Each inset as a drawing, its blocks taken from the drawing's.
    """
    height, width = image.height, image.width
    x0, y0, x1, y1 = drawing.box
    mask = _pixels(drawing.blocks, height, width)[y0:y1, x0:x1]
    fill = image.fill[y0:y1, x0:x1] & mask
    opened = max_filter_bool(~max_filter_bool(~fill, INSET_OPENING), INSET_OPENING) & fill
    labels, count = label_components(opened)
    if count < 2:
        return []
    band_x, band_y = max(3, round(INSET_BAND * pitch[0])), max(3, round(INSET_BAND * pitch[1]))
    across = image.vertical_runs(max(RUN_MINIMUM, round(FIT_RUN_PITCHES * pitch[1])))[y0:y1, x0:x1]
    along = image.horizontal_runs(max(RUN_MINIMUM, round(FIT_RUN_PITCHES * pitch[0])))[y0:y1, x0:x1]
    rows, columns = np.nonzero(labels)
    label = labels[rows, columns]
    insets: list[Drawing] = []
    for k in range(1, count + 1):
        mine = label == k
        top, bottom = int(rows[mine].min()), int(rows[mine].max()) + 1
        left, right = int(columns[mine].min()), int(columns[mine].max()) + 1
        if right - left < 3 * pitch[0] or bottom - top < 3 * pitch[1]:
            continue
        if top < band_y or left < band_x or bottom + band_y > y1 - y0 or right + band_x > x1 - x0:
            continue
        # The bands leave out the corners, where a skewed inset's own edges run into them.
        trim_x, trim_y = band_x + LINE_WIDTH, band_y + LINE_WIDTH
        sides = (
            (slice(top - band_y, top), slice(left + trim_x, right - trim_x), across, 0, pitch[0]),
            (slice(bottom, bottom + band_y), slice(left + trim_x, right - trim_x), across, 0, pitch[0]),
            (slice(top + trim_y, bottom - trim_y), slice(left - band_x, left), along, 1, pitch[1]),
            (slice(top + trim_y, bottom - trim_y), slice(right, right + band_x), along, 1, pitch[1]),
        )
        if all(
            float(fill[r, c].mean()) < INSET_PAPER
            and _crossings(runs[r, c], axis) <= INSET_CROSSING * runs[r, c].shape[1 - axis] / side_pitch
            for r, c, runs, axis, side_pitch in sides
        ):
            blocks = np.zeros_like(drawing.blocks)
            blocks[
                (y0 + top) // BLOCK : math.ceil((y0 + bottom) / BLOCK),
                (x0 + left) // BLOCK : math.ceil((x0 + right) / BLOCK),
            ] = True
            blocks &= drawing.blocks
            if blocks.any():
                insets.append(Drawing(blocks, _box(blocks, height, width)))
    return insets


def _crossings(runs: BoolArray, axis: int) -> int:
    """How many separate lines of thin runs cross a band, the band running across `axis`."""
    present = runs.any(axis=axis)
    return int((np.diff(np.concatenate([[False], present]).astype(np.int8)) == 1).sum())


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


def grid_candidate(
    image: InkImage,
    drawing: Drawing,
    vertical: BoolArray,
    horizontal: BoolArray,
    least_tiles: int = MIN_REGION_TILES,
    pitch: tuple[float, float] | None = None,
    least_lines: int = MIN_DISTINCT_LINES,
) -> Candidate | None:
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
    if pitch is None:
        pitch_x = estimate_pitch(v[y0:y1, x0:x1].sum(axis=0).astype(np.float64))
        pitch_y = estimate_pitch(h[y0:y1, x0:x1].sum(axis=1).astype(np.float64))
    else:
        pitch_x, pitch_y = pitch
    if pitch_x is None or pitch_y is None:
        return None
    tile = max(8, round((pitch_x + pitch_y) / 2))
    box = drawing.box
    v_count = _tile_counts(v, box, tile)
    h_count = _tile_counts(h, box, tile)
    filled = _tile_counts(image.fill & mask, box, tile) >= 0.5 * tile * tile
    gridded = (v_count >= 0.3 * tile) & (h_count >= 0.3 * tile)
    if int(gridded.sum()) < least_tiles:
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
    if int((support_x >= tile).sum()) < least_lines or int((support_y >= tile).sum()) < least_lines:
        return None
    kept = (on_grid | filled) & _grow(on_grid, 1)
    rows, columns = np.nonzero(kept)
    core = (
        x0 + int(columns.min()) * tile,
        y0 + int(rows.min()) * tile,
        min(x1, x0 + (int(columns.max()) + 1) * tile),
        min(y1, y0 + (int(rows.max()) + 1) * tile),
    )
    tiles = np.zeros((height, width), dtype=np.bool_)
    tiles[y0:y1, x0:x1] = np.repeat(np.repeat(kept, tile, axis=0), tile, axis=1)[: y1 - y0, : x1 - x0]
    cover = mask if drawing.cover is None else _pixels(drawing.cover, height, width)
    return Candidate(drawing, mask, tiles & mask, cover, core, (ax.pitch, ay.pitch))


# The frame.


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


def _along(fit: AxisFit, indices: IntArray, span: tuple[float, float]) -> tuple[FloatArray, FloatArray]:
    """Points along fitted lines, one per pixel of the span across them: `(along, across)` per line."""
    across = np.arange(math.floor(span[0]), math.ceil(span[1]) + 1, dtype=np.float64)
    along = fit.model.position(indices.astype(np.float64)[:, None], across[None, :])
    return along, np.broadcast_to(across, along.shape)


def _covered_lines(
    fit: AxisFit, near: BoolArray, box: tuple[int, int], span: tuple[float, float], vertical: bool
) -> IntArray:
    """The fitted lines within a drawing's box that ink and fill cover along a span, as frame sides are covered.

    Args:
        fit: The axis's fit.
        near: The structure, grown across the lines by `FRAME_BAND` of a pitch.
        box: The drawing's extent along the axis, the end exclusive.
        span: The extent along the lines that a frame side covers.
        vertical: Whether the lines are vertical.
    """
    middle = (span[0] + span[1]) / 2
    first = math.ceil(float(fit.model.index_at(box[0] - fit.pitch / 2, middle)))
    last = math.floor(float(fit.model.index_at(box[1] + fit.pitch / 2, middle)))
    if last < first:
        return np.zeros(0, dtype=np.int64)
    indices = np.arange(first, last + 1, dtype=np.int64)
    along, across = _along(fit, indices, span)
    height, width = near.shape
    size_along, size_across = (width, height) if vertical else (height, width)
    a = np.round(along).astype(np.int64)
    c = np.round(across).astype(np.int64)
    inside = (a >= 0) & (a < size_along) & (c >= 0) & (c < size_across)
    a, c = np.clip(a, 0, size_along - 1), np.clip(c, 0, size_across - 1)
    hits = (near[c, a] if vertical else near[a, c]) & inside
    longest = run_lengths(~hits, 1).max(axis=1)
    covered = (hits.mean(axis=1) >= FRAME_COVERAGE) & (longest <= FRAME_GAP * fit.pitch)
    return indices[covered]


def _extent(fit: AxisFit, frame: IntArray, settings: Settings) -> tuple[tuple[int, int], bool] | None:
    """The first and last grid lines of an axis, and whether they are a frame's sides.

    They are the frame's sides where one is drawn, and the printed lines elsewhere.
    """
    printed = _printed(fit, settings)
    runs: list[tuple[int, int]] = []
    for index in frame.tolist():
        if runs and index == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], index)
        else:
            runs.append((index, index))
    # One faint line through a large room may go uncovered between two long runs; a legend's lines past the frame
    # make short runs.
    joined: list[tuple[int, int]] = []
    for run in runs:
        if (
            joined
            and run[0] - joined[-1][1] == 2
            and min(run[1] - run[0], joined[-1][1] - joined[-1][0]) + 1 >= FRAME_RUN
        ):
            joined[-1] = (joined[-1][0], run[1])
        else:
            joined.append(run)
    runs = joined
    if printed is not None:
        # A frame encloses the printed lines, and every line inside it is covered, by fill, hatching, or its own
        # ink; a covered line past a gap of uncovered ones lies outside the frame.
        runs.sort(key=lambda run: (-(min(run[1], printed[1]) - max(run[0], printed[0])), -(run[1] - run[0])))
    else:
        runs.sort(key=lambda run: -(run[1] - run[0]))
    if runs and runs[0][1] > runs[0][0]:
        return runs[0], True
    return None if printed is None else (printed, False)


@dataclass(frozen=True)
class Side:
    """A candidate side of a frame.

    It is a run of neighbouring fitted lines, a pixel apart, that ink and fill cover across the other axis's printed
    lines.
    """

    outer: float
    """The run's outermost line index, fractional. It lies `FRAME_BAND` of a pitch past the side's ink."""

    middle: float
    """The run's middle line index, fractional."""

    hits: BoolArray
    """Whether ink or fill lies along any of the run's lines, at each pixel along the other axis from the drawing
    box's start."""


def _sides(
    fit: AxisFit,
    other: AxisFit,
    near: BoolArray,
    box: Rect,
    printed: tuple[int, int],
    other_printed: tuple[int, int],
    vertical: bool,
) -> tuple[list[Side], list[Side]]:
    """The candidate sides of a frame on one axis: those before the printed lines and those after them.

    Lines a pixel apart are tried, not only the grid's, so a frame drawn off the grid is found. A line is covered
    as `_covered_lines` covers one, across the other axis's printed lines, and the covered lines next to each other
    make one side.
    """
    lo, hi = (box[0], box[2]) if vertical else (box[1], box[3])
    other_lo, other_hi = (box[1], box[3]) if vertical else (box[0], box[2])
    middle = (other_lo + other_hi) / 2
    step = 1 / fit.pitch
    first = float(fit.model.index_at(lo - fit.pitch / 2, middle))
    last = float(fit.model.index_at(hi + fit.pitch / 2, middle))
    indices = np.arange(first, last + step / 2, step)
    # A side encloses the printed lines, so only lines outside them can be its outer edge. Those up to half a pitch
    # inside them are tried too, so a side on the outermost printed line is whole.
    indices = indices[(indices <= printed[0] + 0.5) | (indices >= printed[1] - 0.5)]
    if len(indices) == 0:
        return [], []
    across = np.arange(other_lo, other_hi, dtype=np.float64)
    along = fit.model.position(indices[:, None], across[None, :])
    height, width = near.shape
    size_along = width if vertical else height
    a = np.round(along).astype(np.int64)
    c = np.broadcast_to(across.astype(np.int64)[None, :], a.shape)
    inside = (a >= 0) & (a < size_along)
    a = np.clip(a, 0, size_along - 1)
    hits = (near[c, a] if vertical else near[a, c]) & inside
    at = fit.model.position(indices, middle)
    start = other.model.position(float(other_printed[0]), at) - other_lo
    end = other.model.position(float(other_printed[1]), at) - other_lo
    pixel = np.arange(len(across))[None, :]
    window = (pixel >= start[:, None]) & (pixel <= end[:, None])
    length = window.sum(axis=1)
    share = (hits & window).sum(axis=1) / np.maximum(length, 1)
    longest = run_lengths(~hits & window, 1).max(axis=1)
    covered = (length > 0) & (share >= FRAME_COVERAGE) & (longest <= FRAME_GAP * fit.pitch)
    before: list[Side] = []
    after: list[Side] = []
    breaks = np.nonzero(np.diff(indices) > 1.5 * step)[0] + 1
    for part in np.split(np.arange(len(indices)), breaks):
        for run_first, run_last in _runs(covered[part]):
            members = part[run_first : run_last + 1]
            lowest, highest = float(indices[members[0]]), float(indices[members[-1]])
            run_hits = hits[members].any(axis=0)
            run_middle = float(indices[members].mean())
            if lowest <= printed[0]:
                before.append(Side(lowest, run_middle, run_hits))
            if highest >= printed[1]:
                after.append(Side(highest, run_middle, run_hits))
    return before, after


def _closes(side: Side, fit: AxisFit, other: AxisFit, ends: tuple[Side, Side], other_lo: int, middle: float) -> bool:
    """Whether a side's ink and fill run from one perpendicular side to the other, as a frame's side does.

    The side is covered between the two perpendicular sides' middle lines as `_covered_lines` covers a line,
    and no gap at either end is longer than `FRAME_CORNER` of a pitch, so the side meets both of them.
    """
    at = float(fit.model.position(side.middle, middle))
    start = round(float(other.model.position(ends[0].middle, at))) - other_lo
    end = round(float(other.model.position(ends[1].middle, at))) - other_lo
    start, end = max(0, start), min(len(side.hits) - 1, end)
    if end <= start:
        return False
    hits = side.hits[start : end + 1]
    if not hits.any():
        return False
    corner = FRAME_CORNER * fit.pitch
    leading = int(np.argmax(hits))
    trailing = int(np.argmax(hits[::-1]))
    longest = int(run_lengths(~hits[None, :], 1).max())
    return (
        float(hits.mean()) >= FRAME_COVERAGE
        and longest <= FRAME_GAP * fit.pitch
        and leading <= corner
        and trailing <= corner
    )


def _closed_frame(
    x_fit: AxisFit, y_fit: AxisFit, near_x: BoolArray, near_y: BoolArray, box: Rect, settings: Settings
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """The grid lines on and inside the frame around the printed lines, or `None` when no frame closes around them.

    A frame is four sides, each covered from one perpendicular side to the other, that enclose the printed lines
    of both axes. A drawn line, a filled frame's edge, and the outermost grid lines printed across the whole
    drawing all close, wherever they lie on the grid and whatever paper lies between them and the drawing inside.
    Of the frames that close, the outermost is the drawing's. Its grid takes every line on or inside its sides,
    within `FRAME_BAND` of a pitch, so a frame drawn off the grid keeps the lines inside it.

    Returns:
        The first and last vertical lines, and the first and last horizontal lines.
    """
    printed_x, printed_y = _printed(x_fit, settings), _printed(y_fit, settings)
    if printed_x is None or printed_y is None:
        return None
    west, east = _sides(x_fit, y_fit, near_x, box, printed_x, printed_y, True)
    north, south = _sides(y_fit, x_fit, near_y, box, printed_y, printed_x, False)
    middle_x, middle_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    best: tuple[float, tuple[Side, Side, Side, Side]] | None = None
    for w, e, n, s in itertools.product(west, east, north, south):
        if e.outer <= w.outer or s.outer <= n.outer:
            continue
        area = (e.outer - w.outer) * x_fit.pitch * (s.outer - n.outer) * y_fit.pitch
        if best is not None and area <= best[0]:
            continue
        if (
            _closes(w, x_fit, y_fit, (n, s), box[1], middle_y)
            and _closes(e, x_fit, y_fit, (n, s), box[1], middle_y)
            and _closes(n, y_fit, x_fit, (w, e), box[0], middle_x)
            and _closes(s, y_fit, x_fit, (w, e), box[0], middle_x)
        ):
            best = (area, (w, e, n, s))
    if best is None:
        return None
    w, e, n, s = best[1]
    columns = (math.ceil(w.outer - 1e-9), math.floor(e.outer + 1e-9))
    rows = (math.ceil(n.outer - 1e-9), math.floor(s.outer + 1e-9))
    if columns[1] <= columns[0] or rows[1] <= rows[0]:
        return None
    return columns, rows


def _frame_anchors(
    fit: AxisFit,
    extent: tuple[int, int],
    span: tuple[float, float],
    own: BoolArray,
    dark: Float32Array,
    vertical: bool,
) -> Anchors | None:
    """Points on a frame's filled sides: where the fill begins, with paper outside it, along each side's line.

    A filled frame's edge is the grid's outer line, and it runs the length
    of the region where printed lines may not, so it holds the drift
    correction to the frame at the region's far corners. Along each side,
    once a pitch, the structure is read across the fitted line from
    `ANCHOR_REACH` of a pitch outside to as far inside. A place counts where
    paper lies outside and ink or fill runs unbroken from its first pixel
    through the window's inner end. Its point is where the darkness crosses
    halfway from the paper's to the fill's, which a scan's blur leaves in
    place, and it is kept within `ANCHOR_TOLERANCE` of a pitch of the fitted
    line. A side gives points only when at least `ANCHOR_SHARE` of its places
    do; a drawn frame line, already among the fitted lines, and a frame drawn
    off the grid give none.
    """
    height, width = own.shape
    size_along, size_across = (width, height) if vertical else (height, width)
    samples = np.arange(span[0], span[1] + 1e-9, max(1.0, fit.pitch))
    reach = max(2, round(ANCHOR_REACH * fit.pitch))
    offsets = np.arange(reach, -reach - 1, -1, dtype=np.float64)
    others: list[FloatArray] = []
    positions: list[FloatArray] = []
    for line, outward in ((extent[0], -1.0), (extent[1], 1.0)):
        fitted = fit.model.position(float(line), samples)
        across = fitted[:, None] + outward * offsets[None, :]
        a = np.round(across).astype(np.int64)
        c = np.broadcast_to(np.round(samples).astype(np.int64)[:, None], a.shape)
        inside = (a >= 0) & (a < size_along) & (c >= 0) & (c < size_across)
        a, c = np.clip(a, 0, size_along - 1), np.clip(c, 0, size_across - 1)
        hits = (own[c, a] if vertical else own[a, c]) & inside
        level = (dark[c, a] if vertical else dark[a, c]).astype(np.float64)
        first = np.argmax(hits, axis=1)
        rows = np.arange(len(samples))
        through = np.logical_and.accumulate(hits[:, ::-1], axis=1)[:, ::-1][rows, first]
        # The halfway crossing: the paper's darkness is the window's outer end, the fill's its inner end.
        half = (level[:, :1] + level[:, -1:]) / 2
        beyond = np.argmax(level >= half, axis=1)
        before = np.clip(beyond - 1, 0, level.shape[1] - 1)
        low, high = level[rows, before], level[rows, beyond]
        share = np.where(high > low, (half[:, 0] - low) / np.maximum(high - low, 1e-9), 0.0)
        edge = across[rows, before] - outward * share
        good = hits.any(axis=1) & (first > 0) & (beyond > 0) & through & inside.all(axis=1)
        good &= np.abs(edge - fitted) <= ANCHOR_TOLERANCE * fit.pitch
        if len(samples) and good.mean() >= ANCHOR_SHARE:
            others.append(samples[good])
            positions.append(edge[good])
    if not positions:
        return None
    return Anchors(np.concatenate(others), np.concatenate(positions))


def _cut(
    fit: AxisFit, extent: tuple[int, int], span: tuple[float, float], reach: tuple[bool, bool], size: int
) -> tuple[int, int]:
    """Extend an axis's lines to those the image edge cuts.

    On a side where the drawing reaches the image edge, the grid takes the
    next line while that line comes within half a pitch of the image edge,
    inside or outside it, somewhere along the side, so a cell at least half
    inside the image is part of the grid whether or not its outer line shows.
    A line more than half a pitch inside the image along the whole side that
    isn't printed or framed ends the grid.
    """
    lo, hi = extent
    half = fit.pitch / 2
    edges = (-0.5, size - 0.5)
    while reach[0]:
        along, _ = _along(fit, np.array([lo - 1]), span)
        if not (along.min() < edges[0] + half and along.max() >= edges[0] - half):
            break
        lo -= 1
    while reach[1]:
        along, _ = _along(fit, np.array([hi + 1]), span)
        if not (along.max() > edges[1] - half and along.min() <= edges[1] + half):
            break
        hi += 1
    return lo, hi


def _position_span(fit: AxisFit, extent: tuple[int, int], middle: float) -> tuple[float, float]:
    """The pixel extent of an axis's first and last lines, at the middle of the other axis."""
    return float(fit.model.position(float(extent[0]), middle)), float(fit.model.position(float(extent[1]), middle))


# The fit.


def _axis_data(runs: BoolArray, rect: Rect, vertical: bool) -> AxisData:
    x0, y0, x1, y1 = rect
    ys, xs = np.nonzero(runs[y0:y1, x0:x1])
    along, other = (xs + x0, ys + y0) if vertical else (ys + y0, xs + x0)
    if vertical:
        return AxisData(along.astype(np.float64), other.astype(np.float64), x0, x1, y0, y1)
    return AxisData(along.astype(np.float64), other.astype(np.float64), y0, y1, x0, x1)


def fit_region(image: InkImage, structure: BoolArray, candidate: Candidate, settings: Settings) -> RegionResult:
    """Fit the grid of one region, find its frame along the fitted lines, and compute the gate's figures.

    The first fit reads the drawing's tiles on the grid, and the frame is
    found along its lines. The second reads every thin run of the drawing
    inside the frame, so a title or a legend joined to the drawing outside the
    frame doesn't touch it. The third, when the frame has filled sides, adds
    points on their edges to the drift fit.
    """
    height, width = image.height, image.width
    box = candidate.drawing.box
    pitch_x, pitch_y = candidate.pitch
    vertical = image.vertical_runs(max(RUN_MINIMUM, round(FIT_RUN_PITCHES * pitch_y))) & candidate.mask
    horizontal = image.horizontal_runs(max(RUN_MINIMUM, round(FIT_RUN_PITCHES * pitch_x))) & candidate.mask
    own = structure & candidate.cover
    near_x = max_filter_bool(own, max(1, round(FRAME_BAND * pitch_x)), axis=1)
    near_y = max_filter_bool(own, max(1, round(FRAME_BAND * pitch_y)), axis=0)
    window = box
    anchors: list[Anchors | None] = [None, None]
    corners = np.zeros((0, 0, 2))
    grid: GridFit | None = None
    for stage in range(3):
        # The first fit reads the tiles on the grid, to find the frame; the second reads the whole drawing inside
        # the frame; the third adds the frame's own sides, when they are filled edges, to the drift fit.
        reads = candidate.tiles if stage == 0 else candidate.mask
        x_fit = fit_axis(_axis_data(vertical & reads, window, True), pitch_y, settings, pitch_x, anchors[0])
        y_fit = fit_axis(_axis_data(horizontal & reads, window, False), pitch_x, settings, pitch_y, anchors[1])
        if x_fit is None or y_fit is None:
            return RegionResult(window, reason="fewer than 3 grid lines found on an axis")
        core = candidate.core
        span_x, span_y = (float(core[0]), float(core[2])), (float(core[1]), float(core[3]))
        columns = rows = None
        frame = _closed_frame(x_fit, y_fit, near_x, near_y, box, settings)
        if frame is not None:
            columns, rows = (frame[0], True), (frame[1], True)
            span_x = _position_span(x_fit, columns[0], (span_y[0] + span_y[1]) / 2)
            span_y = _position_span(y_fit, rows[0], (span_x[0] + span_x[1]) / 2)
        else:
            for _ in range(2):
                columns = _extent(x_fit, _covered_lines(x_fit, near_x, (box[0], box[2]), span_y, True), settings)
                rows = _extent(y_fit, _covered_lines(y_fit, near_y, (box[1], box[3]), span_x, False), settings)
                if columns is None or rows is None:
                    break
                span_x = _position_span(x_fit, columns[0], (span_y[0] + span_y[1]) / 2)
                span_y = _position_span(y_fit, rows[0], (span_x[0] + span_x[1]) / 2)
        if columns is None or rows is None:
            return RegionResult(window, reason="the fit explains no grid line on an axis")
        if stage == 1:
            anchors = [
                _frame_anchors(x_fit, columns[0], span_y, own, image.dark, True) if columns[1] else None,
                _frame_anchors(y_fit, rows[0], span_x, own, image.dark, False) if rows[1] else None,
            ]
        lines_x = _cut(x_fit, columns[0], span_y, (box[0] == 0, box[2] == width), width)
        lines_y = _cut(y_fit, rows[0], span_x, (box[1] == 0, box[3] == height), height)
        if lines_x[1] <= lines_x[0] or lines_y[1] <= lines_y[0]:
            return RegionResult(window, reason="the fitted grid is less than a cell across")
        grid = GridFit(x_fit, y_fit, lines_x[0], lines_x[1], lines_y[0], lines_y[1])
        corners = grid.corners()
        window = (
            max(box[0], math.floor(float(corners[..., 0].min())) - LINE_WIDTH),
            max(box[1], math.floor(float(corners[..., 1].min())) - LINE_WIDTH),
            min(box[2], math.ceil(float(corners[..., 0].max())) + LINE_WIDTH + 1),
            min(box[3], math.ceil(float(corners[..., 1].max())) + LINE_WIDTH + 1),
        )
        if stage == 1 and anchors == [None, None]:
            break
    assert grid is not None
    # The rectangle is the grid's extent: its frame where one is drawn, its printed lines elsewhere, and the image
    # edge where the edge cuts it.
    rect = (
        max(0, math.floor(float(corners[..., 0].min()) + 0.5)),
        max(0, math.floor(float(corners[..., 1].min()) + 0.5)),
        min(width, math.floor(float(corners[..., 0].max()) + 0.5) + 1),
        min(height, math.floor(float(corners[..., 1].max()) + 0.5) + 1),
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
