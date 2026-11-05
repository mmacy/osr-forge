"""Grid registration: the fit of each region's grid, the gate, and the alignment of two halves.

Every number here comes from the registration measurement's reference fit, run in a scratch copy over the
committed Chaotic Caves, Monkey Isle, and minimod images, the synthetic maps, and perturbed copies of pages 47
and 38. Each threshold has the measured figures beside it.
"""

import functools
import json
import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image, ImageFilter

from osrforge.tracing.registration import (
    COVERAGE_MINIMUM,
    EXPLAINED_MINIMUM,
    GAP_TOLERANCE,
    LINE_TOLERANCE,
    OFFSET_CANDIDATES,
    GateFigures,
    HalfAlignment,
    RegionFit,
    align_halves,
    register_image,
)
from osrforge.tracing.truth import GeometryTruth, Region, Registration

ASSETS = Path(__file__).parent / "assets"
CHAOTIC_CAVES = ASSETS / "chaotic-caves" / "maps"
MONKEY_ISLE = ASSETS / "monkey-isle" / "maps"
MINIMOD = ASSETS / "minimod" / "pages"
SYNTHETIC = ASSETS / "synthetic-maps"

PITCH_TOLERANCE = 0.005
"""The fitted pitch is within this share of the measured one on a real map. The reference fit's pitches match
the pilot's to 0.01 px on the Palace maps and page 38."""

SYNTHETIC_PITCH_TOLERANCE = 0.02
"""On a synthetic map, whose pitch bows by up to 3 px across the drawing, the fit's mean pitch is within this
share of the true mean. The reference fit gives 26.13 by 25.59 px on cave-entrance against a true 26 by 26."""

GRID_TOLERANCE = 1
"""Lines per side by which a fitted grid may differ from the measured extent on a real map, or exceed the true
grid on a synthetic one."""

CORNER_TOLERANCE = LINE_TOLERANCE
"""Cells. A true corner that touches floor maps this close to a fitted corner. The reference fit gives 0.014 to
0.073 on five synthetic maps and 0.13 to 0.14 on crypt at its drift ridge of 10 median peaks per pixel of bend,
which fails. A build from this contract gives 0.099 on crypt at a ridge of 3 and 0.059 at a ridge of 1. The ridge
is the implementation's to set."""

LINE_COUNT_FLOOR = 0.8
"""The least share of the reference fit's detected lines a fit must find on a real map, so a detector that sees
only the lines it can fit can't pass the share by seeing fewer."""


class RealMap:
    """A committed real map image and what the reference fit measured on it."""

    def __init__(self, path: Path, regions: list[tuple[tuple[int, int], tuple[float, float], int]]) -> None:
        self.path = path
        self.regions = regions
        """Per region in page order: `(columns, rows)`, `(x pitch, y pitch)` in pixels, and detected lines."""


REAL_MAPS = [
    RealMap(CHAOTIC_CAVES / "p021.png", [((37, 26), (44.13, 44.12), 501), ((37, 23), (44.13, 44.11), 486)]),
    RealMap(CHAOTIC_CAVES / "p038.png", [((56, 80), (30.65, 30.63), 1483)]),
    RealMap(CHAOTIC_CAVES / "p046.png", [((32, 37), (60.85, 60.84), 660)]),
    RealMap(CHAOTIC_CAVES / "p047.png", [((16, 16), (75.00, 75.00), 136)]),
    RealMap(MONKEY_ISLE / "p044.png", [((41, 47), (42.86, 42.87), 1011)]),
]
"""Page 21 holds Halfling Home at 5 feet and the Cave of Horrors at 10 feet, drawn at one pitch and split by a
frame line. Page 38 is the ten lairs: it has no frame, and its drawing runs off the image on every side,
with the legend to the right of its last vertical line at x 1719. Its grid takes each cell at least half inside
the image, which is 56 by 80 cells. Page 46
is the manor's ground floor: its printed horizontal lines run from y 303 to y 2554, which is 37 rows, and its
vertical lines reach one row higher, to about y 243, below the legend, so the tolerance of one line accepts a
fit that takes that row. Page 47 is the manor's upper floor, and Monkey Isle page 44 Dragon Harbor, which the
stage refuses for its scale after the reading but which registers. The grids and the detected lines are the
reference fit's over rectangles drawn by the rule in `RegionFit`, and the detected lines count the rejected ones:
1480 and 3 on page 38, 634 and 26 on page 46, 1004 and 7 on page 44."""


def registered(fits: tuple[RegionFit, ...]) -> list[RegionFit]:
    return [fit for fit in fits if fit.verdict == "registered"]


def grid_of(fit: RegionFit) -> tuple[int, int]:
    assert fit.registration is not None
    return fit.registration.columns, fit.registration.rows


def check_gate(fit: RegionFit) -> None:
    assert fit.gate is not None
    assert fit.gate.explained_share >= EXPLAINED_MINIMUM
    assert fit.gate.index_jumps == 0
    assert fit.gate.coverage >= COVERAGE_MINIMUM
    assert fit.gate.passes


def load_rgb(path: Path) -> npt.NDArray[np.uint8]:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def save_rgb(path: Path, rgb: npt.NDArray[np.uint8]) -> Path:
    Image.fromarray(rgb).save(path)
    return path


def shift_columns(rgb: npt.NDArray[np.uint8], dy: npt.NDArray[np.float64]) -> npt.NDArray[np.uint8]:
    """Move each column `x` down by `dy[x]` pixels, bilinearly, filling with white."""
    height, width = rgb.shape[:2]
    out = np.full_like(rgb, 255)
    ys = np.arange(height)[:, None] - dy[None, :]
    y0 = np.floor(ys).astype(int)
    frac = (ys - y0).astype(np.float32)
    xs = np.broadcast_to(np.arange(width)[None, :], (height, width))
    valid = (y0 >= 0) & (y0 + 1 < height)
    y0c = np.clip(y0, 0, height - 2)
    for channel in range(3):
        plane = rgb[..., channel].astype(np.float32)
        value = plane[y0c, xs] * (1 - frac) + plane[y0c + 1, xs] * frac
        out[..., channel] = np.where(valid, np.round(value), 255).astype(np.uint8)
    return out


def stretch_rows(rgb: npt.NDArray[np.uint8], factor_at_bottom: float) -> npt.NDArray[np.uint8]:
    """Resample so the pitch grows linearly from 1 at the top to `factor_at_bottom` at the bottom."""
    height = rgb.shape[0]
    out = np.full_like(rgb, 255)
    y = np.arange(height, dtype=np.float64)
    stretched = y + (factor_at_bottom - 1) * y * y / (2 * height)
    source = np.interp(np.arange(height), stretched, y)
    y0 = np.floor(source).astype(int)
    frac = (source - y0).astype(np.float32)[:, None]
    valid = (y0 + 1 < height)[:, None]
    y0c = np.clip(y0, 0, height - 2)
    for channel in range(3):
        plane = rgb[..., channel].astype(np.float32)
        value = plane[y0c] * (1 - frac) + plane[y0c + 1] * frac
        out[..., channel] = np.where(valid, np.round(value), 255).astype(np.uint8)
    return out


def test_gate_figures_model():
    gate = GateFigures(
        detected_lines=100,
        rejected_lines=4,
        explained_share=0.96,
        index_jumps=0,
        counted_cells=50,
        covered_cells=45,
        coverage=0.9,
    )
    assert gate.passes
    assert not gate.model_copy(update={"explained_share": 0.89}).passes
    assert not gate.model_copy(update={"index_jumps": 1}).passes
    assert not gate.model_copy(update={"coverage": 0.69}).passes
    with pytest.raises(ValueError):
        GateFigures.model_validate(gate.model_dump() | {"rejected_lines": 101})
    with pytest.raises(ValueError):
        GateFigures.model_validate(gate.model_dump() | {"covered_cells": 51})


def test_region_fit_model():
    gate = GateFigures(
        detected_lines=10,
        rejected_lines=0,
        explained_share=1.0,
        index_jumps=0,
        counted_cells=4,
        covered_cells=4,
        coverage=1.0,
    )
    registration = Registration(corners=(((0.0, 0.0), (10.0, 0.0)), ((0.0, 10.0), (10.0, 10.0))))
    fit = RegionFit(rect=(0, 0, 10, 10), pitch=(10.0, 10.0), verdict="registered", gate=gate, registration=registration)
    assert fit.reason is None
    RegionFit(rect=(0, 0, 10, 10), verdict="unregistered", reason="only 2 grid lines found")
    failing = gate.model_copy(update={"coverage": 0.5})
    RegionFit(rect=(0, 0, 10, 10), pitch=(10.0, 10.0), verdict="unregistered", reason="coverage 0.5", gate=failing)
    with pytest.raises(ValueError):
        RegionFit(rect=(0, 0, 10, 10), pitch=(10.0, 10.0), verdict="registered", gate=gate)
    with pytest.raises(ValueError):
        RegionFit(rect=(0, 0, 10, 10), verdict="unregistered", reason="x", registration=registration)
    with pytest.raises(ValueError):
        RegionFit(
            rect=(0, 0, 10, 10), pitch=(10.0, 10.0), verdict="registered", gate=failing, registration=registration
        )
    with pytest.raises(ValueError):
        RegionFit(rect=(0, 0, 10, 10), pitch=(10.0, 10.0), verdict="unregistered", reason="x", gate=gate)
    with pytest.raises(ValueError):
        RegionFit(rect=(0, 0, 10, 10), verdict="registered", gate=gate, registration=registration)
    with pytest.raises(ValueError):
        RegionFit(rect=(10, 0, 10, 10), verdict="unregistered", reason="x")


def test_half_alignment_model():
    alignment = HalfAlignment(rows=0, columns=(0, 1, 2), gaps=(-0.02, 0.98, 1.98))
    assert alignment.rows == 0
    assert len(alignment.columns) == OFFSET_CANDIDATES
    with pytest.raises(ValueError):
        HalfAlignment(rows=0, columns=(0, 2, 3), gaps=(0.0, 1.0, 2.0))
    with pytest.raises(ValueError):
        HalfAlignment(rows=0, columns=(0, 1, 2), gaps=(0.0, 2.0, 1.0))
    with pytest.raises(ValueError):
        HalfAlignment(rows=0, columns=(0, 1, 2), gaps=(-GAP_TOLERANCE - 0.01, 1.0, 2.0))
    with pytest.raises(ValueError):
        HalfAlignment(rows=0, columns=(0, 1), gaps=(0.0, 1.0))


@pytest.mark.parametrize("real", REAL_MAPS, ids=lambda real: real.path.name)
def test_each_committed_real_map_registers_as_measured(real: RealMap):
    fits = register_image(real.path)
    assert len(fits) == len(real.regions), [fit.rect for fit in fits]
    assert [fit.rect[1] for fit in fits] == sorted(fit.rect[1] for fit in fits)
    for fit, (grid, pitch, lines) in zip(fits, real.regions, strict=True):
        assert fit.verdict == "registered", fit.reason
        check_gate(fit)
        assert fit.gate is not None
        assert fit.pitch is not None
        columns, rows = grid_of(fit)
        assert abs(columns - grid[0]) <= GRID_TOLERANCE and abs(rows - grid[1]) <= GRID_TOLERANCE, (columns, rows)
        assert abs(fit.pitch[0] - pitch[0]) <= PITCH_TOLERANCE * pitch[0], fit.pitch
        assert abs(fit.pitch[1] - pitch[1]) <= PITCH_TOLERANCE * pitch[1], fit.pitch
        assert fit.gate.detected_lines >= LINE_COUNT_FLOOR * lines, fit.gate


def test_a_gridless_map_yields_no_region():
    """minimod's page 4 is a raster map with no grid: the reference fit's coarse pitch there is 8.5 px."""
    assert register_image(MINIMOD / "0004.png") == ()


def test_the_same_image_registers_the_same_way_twice():
    path = CHAOTIC_CAVES / "p047.png"
    first = register_image(path)
    second = register_image(path)
    assert [fit.model_dump_json() for fit in first] == [fit.model_dump_json() for fit in second]


def test_a_bow_registers_through_the_drift_correction(tmp_path: Path):
    """Page 47's columns bowed by 0.3 of its 75 px pitch, 22.5 px at the middle. The reference fit's affine stage
    alone leaves lines 8.4 px off; the drift correction explains 0.975 of the lines within a tenth, with
    coverage 0.875."""
    rgb = load_rgb(CHAOTIC_CAVES / "p047.png")
    width = rgb.shape[1]
    x = np.arange(width) / (width - 1)
    bowed = shift_columns(rgb, 0.3 * 75.0 * 4 * x * (1 - x))
    fits = register_image(save_rgb(tmp_path / "bow.png", bowed))
    assert len(registered(fits)) == 1, [(fit.verdict, fit.reason) for fit in fits]
    fit = registered(fits)[0]
    check_gate(fit)
    columns, rows = grid_of(fit)
    assert abs(columns - 16) <= GRID_TOLERANCE and abs(rows - 16) <= GRID_TOLERANCE


def test_a_stretch_registers_through_the_drift_correction(tmp_path: Path):
    """Page 38 with its pitch growing 4% from top to bottom, a feed-speed drift. The reference fit's affine stage
    explains 0.828 of the lines within a tenth and the drift correction 0.999, with coverage 0.939."""
    rgb = load_rgb(CHAOTIC_CAVES / "p038.png")
    fits = register_image(save_rgb(tmp_path / "stretch.png", stretch_rows(rgb, 1.04)))
    assert len(registered(fits)) == 1, [(fit.verdict, fit.reason) for fit in fits]
    check_gate(registered(fits)[0])


def test_a_half_pitch_shift_fails_registration(tmp_path: Path):
    """Page 47 with its right 45% moved down half a pitch. Two paths fail it. The reference fit first takes
    37.5 px as the pitch, finds neighbouring lines two indices apart, refits at 75 px, rejects 41 of 179 lines,
    and reports a share of 0.703. A fit that keeps the 37.5 px pitch passes the share and the jumps, because every
    printed line lies on that lattice, and fails through the coverage, 0.476 in a build from this contract. Every
    fit that spans the tear is unregistered either way."""
    rgb = load_rgb(CHAOTIC_CAVES / "p047.png")
    width = rgb.shape[1]
    tear = int(0.55 * width)
    dy = np.zeros(width)
    dy[tear:] = 75.0 / 2
    fits = register_image(save_rgb(tmp_path / "shift.png", shift_columns(rgb, dy)))
    spanning = [fit for fit in fits if fit.rect[0] < tear - 75 and fit.rect[2] > tear + 75]
    assert spanning, [fit.rect for fit in fits]
    for fit in spanning:
        assert fit.verdict == "unregistered", fit.gate


def test_a_sine_warp_fails_through_the_coverage(tmp_path: Path):
    """Page 47 warped by a sine of 0.6 of a pitch over two periods across the image. The reference fit registers
    the tiles that survive detection, 15 by 17 cells with a share of 0.987, and only the coverage sees what the
    detector dropped: 0.549 against the 0.857 to 0.996 of the real maps as printed."""
    rgb = load_rgb(CHAOTIC_CAVES / "p047.png")
    width = rgb.shape[1]
    x = np.arange(width) / (width - 1)
    warped = shift_columns(rgb, 0.6 * 75.0 * np.sin(2 * np.pi * 2 * x))
    fits = register_image(save_rgb(tmp_path / "warp.png", warped))
    assert registered(fits) == [], [(fit.rect, fit.gate) for fit in fits]


def test_two_drawings_at_two_pitches_are_two_regions(tmp_path: Path):
    """Page 47 beside a copy of itself scaled by 0.6 with Lanczos, tops aligned, with 100 px of white between
    them: two registered regions in page order, at 75 px on the left and 45 px on the right. The copy's grid
    starts higher on the image, so it comes first."""
    with Image.open(CHAOTIC_CAVES / "p047.png") as page:
        big = page.convert("RGB")
    small = big.resize((round(big.width * 0.6), round(big.height * 0.6)), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (big.width + 100 + small.width, big.height), (255, 255, 255))
    canvas.paste(big, (0, 0))
    canvas.paste(small, (big.width + 100, 0))
    path = tmp_path / "two-pitches.png"
    canvas.save(path)
    fits = register_image(path)
    assert [fit.verdict for fit in fits] == ["registered", "registered"], [(fit.rect, fit.reason) for fit in fits]
    assert [(fit.rect[1], fit.rect[0]) for fit in fits] == sorted((fit.rect[1], fit.rect[0]) for fit in fits)
    left = [fit for fit in fits if fit.rect[2] <= big.width]
    right = [fit for fit in fits if fit.rect[0] >= big.width]
    assert len(left) == 1 and len(right) == 1, [fit.rect for fit in fits]
    for fit, pitch in ((left[0], 75.0), (right[0], 45.0)):
        check_gate(fit)
        assert fit.pitch is not None
        assert abs(fit.pitch[0] - pitch) <= PITCH_TOLERANCE * pitch, fit.pitch
        assert abs(fit.pitch[1] - pitch) <= PITCH_TOLERANCE * pitch, fit.pitch


BAND_INK = np.array([30, 90, 170], dtype=np.uint8)
"""A TSR-style blue, dark enough to read as ink in a line and as fill in a solid."""

BAND_PITCH = 26
BAND_ORIGIN = 100


def framed_drawing(band: int) -> tuple[npt.NDArray[np.uint8], tuple[int, int, int, int]]:
    """A floor plan inside a thin frame, with wide paper margins and, in its southeast, a solid band.

    The floor is 24 by 30 cells at a pitch of 26 px from the lattice origin (100, 100), with grid lines 2 px wide
    and a wall 6 px wide. The frame is a line 2 px wide on lattice lines: 3 pitches west of the floor, 1 pitch
    north of it, on its east wall, and 4 pitches south of its south wall, so it encloses 27 by 35 cells. When
    `band` is above 0, a solid band fills the floor's east `band` columns from row 18 down to the frame's south
    line, and its last 2 rows are a gridded strip walled on its north and west sides. Everything else inside the
    frame is paper. Returns the image and the frame's lines as `(x0, y0, x1, y1)` in pixels.
    """
    columns, rows = 24, 30
    floor_x1 = BAND_ORIGIN + columns * BAND_PITCH
    floor_y1 = BAND_ORIGIN + rows * BAND_PITCH
    frame = (BAND_ORIGIN - 3 * BAND_PITCH, BAND_ORIGIN - BAND_PITCH, floor_x1, floor_y1 + 4 * BAND_PITCH)
    fx0, fy0, fx1, fy1 = frame
    rgb = np.full((fy1 + 120, fx1 + 120, 3), 255, dtype=np.uint8)

    def paint(x0: int, y0: int, x1: int, y1: int) -> None:
        rgb[max(0, y0) : y1, max(0, x0) : x1] = BAND_INK

    for i in range(columns + 1):
        x = BAND_ORIGIN + i * BAND_PITCH
        paint(x - 1, BAND_ORIGIN - 1, x + 1, floor_y1 + 1)
    for j in range(rows + 1):
        y = BAND_ORIGIN + j * BAND_PITCH
        paint(BAND_ORIGIN - 1, y - 1, floor_x1 + 1, y + 1)
    paint(BAND_ORIGIN - 3, BAND_ORIGIN - 3, floor_x1 + 3, BAND_ORIGIN + 3)
    paint(BAND_ORIGIN - 3, floor_y1 - 3, floor_x1 + 3, floor_y1 + 3)
    paint(BAND_ORIGIN - 3, BAND_ORIGIN - 3, BAND_ORIGIN + 3, floor_y1 + 3)
    paint(floor_x1 - 3, BAND_ORIGIN - 3, floor_x1 + 3, floor_y1 + 3)
    if band:
        band_x0 = floor_x1 - band * BAND_PITCH
        paint(band_x0, BAND_ORIGIN + 18 * BAND_PITCH, fx1, fy1)
        strip_y0 = fy1 - 2 * BAND_PITCH
        rgb[strip_y0:fy1, band_x0:fx1] = 255
        for i in range(band + 1):
            x = band_x0 + i * BAND_PITCH
            paint(x - 1, strip_y0 - 1, x + 1, fy1 + 1)
        for j in range(3):
            y = strip_y0 + j * BAND_PITCH
            paint(band_x0 - 1, y - 1, fx1 + 1, y + 1)
        paint(band_x0 - 3, strip_y0 - 3, fx1 + 3, strip_y0 + 3)
        paint(band_x0 - 3, strip_y0 - 3, band_x0 + 3, fy1 + 1)
    paint(fx0 - 1, fy0 - 1, fx1 + 1, fy0 + 1)
    paint(fx0 - 1, fy1 - 1, fx1 + 1, fy1 + 1)
    paint(fx0 - 1, fy0 - 1, fx0 + 1, fy1 + 1)
    paint(fx1 - 1, fy0 - 1, fx1 + 1, fy1 + 1)
    return rgb, frame


FRAMED_VARIANTS = {
    "band": (8, (0, 0), 1.0),
    "no band": (0, (0, 0), 1.0),
    "band, shifted": (8, (7, 11), 1.0),
    "band, scaled": (8, (0, 0), 1.04),
}
"""Each framed drawing: the band's width in columns, a shift `(dx, dy)` in pixels, and a scale."""

FRAME_RECT_TOLERANCE = 3
"""Pixels, before scaling. The rectangle's sides lie this close to the frame's lines."""


@pytest.mark.parametrize("variant", list(FRAMED_VARIANTS))
def test_a_framed_drawing_registers_from_frame_to_frame(tmp_path: Path, variant: str):
    """A thin frame with paper margins wider than two pitches on its west and south bounds the region.

    On the variant with a band, the only columns covered from the frame's north line to its south line are the
    band's, because every column west of it crosses the paper margin below the floor's south wall. The region's
    rectangle is still the framed drawing, as `RegionFit` says, so it runs from the frame's west line to its east
    line and from its north line to its south line, and its grid is 27 by 35 cells.
    """
    band, (dx, dy), scale = FRAMED_VARIANTS[variant]
    rgb, (fx0, fy0, fx1, fy1) = framed_drawing(band)
    image = Image.fromarray(rgb)
    if (dx, dy) != (0, 0):
        shifted = Image.new("RGB", (image.width + dx, image.height + dy), (255, 255, 255))
        shifted.paste(image, (dx, dy))
        image = shifted
    if scale != 1.0:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    path = tmp_path / "framed.png"
    image.save(path)
    fits = registered(register_image(path))
    assert len(fits) == 1, [fit.rect for fit in fits]
    fit = fits[0]
    check_gate(fit)
    expected = [(fx0 + dx) * scale, (fy0 + dy) * scale, (fx1 + dx) * scale, (fy1 + dy) * scale]
    assert all(
        abs(side - want) <= FRAME_RECT_TOLERANCE * scale for side, want in zip(fit.rect, expected, strict=True)
    ), (fit.rect, expected)
    assert grid_of(fit) == (27, 35)
    assert fit.pitch is not None
    for pitch in fit.pitch:
        assert abs(pitch - BAND_PITCH * scale) <= SYNTHETIC_PITCH_TOLERANCE * BAND_PITCH * scale, fit.pitch


def test_a_cell_the_image_edge_cuts_is_part_of_the_grid():
    """Page 38 has no frame, and its drawing runs off the image on every side.

    Its printed horizontal lines run from y 29 to y 2418, and the image is 2450 px tall. The cell above the first
    line is 29 of 30.6 px inside the image, and the cell below the last line is 31 of 30.6, so both are part of the
    grid under `RegionFit`'s rule for a side that the image edge cuts. With the 56 columns between the vertical
    lines at x 0 and x 1719, the grid is 56 by 80.
    """
    fits = registered(register_image(CHAOTIC_CAVES / "p038.png"))
    assert len(fits) == 1, [fit.rect for fit in fits]
    assert grid_of(fits[0]) == (56, 80)


def gridded_room(rgb: npt.NDArray[np.uint8], x0: int, y0: int, columns: int, rows: int) -> None:
    """Draw a walled room of grid cells at `BAND_PITCH`, with grid lines 2 px wide and a wall 6 px wide."""
    x1, y1 = x0 + columns * BAND_PITCH, y0 + rows * BAND_PITCH
    for i in range(columns + 1):
        x = x0 + i * BAND_PITCH
        rgb[y0 - 1 : y1 + 1, x - 1 : x + 1] = BAND_INK
    for j in range(rows + 1):
        y = y0 + j * BAND_PITCH
        rgb[y - 1 : y + 1, x0 - 1 : x1 + 1] = BAND_INK
    for left, top, right, bottom in (
        (x0 - 3, y0 - 3, x1 + 3, y0 + 3),
        (x0 - 3, y1 - 3, x1 + 3, y1 + 3),
        (x0 - 3, y0 - 3, x0 + 3, y1 + 3),
        (x1 - 3, y0 - 3, x1 + 3, y1 + 3),
    ):
        rgb[top:bottom, left:right] = BAND_INK


def outline(rgb: npt.NDArray[np.uint8], x0: int, y0: int, x1: int, y1: int) -> None:
    """Draw a box's outline 2 px wide, inside `(x0, y0, x1, y1)`."""
    rgb[y0 : y0 + 2, x0:x1] = BAND_INK
    rgb[y1 - 2 : y1, x0:x1] = BAND_INK
    rgb[y0:y1, x0 : x0 + 2] = BAND_INK
    rgb[y0:y1, x1 - 2 : x1] = BAND_INK


def test_a_border_around_a_drawing_and_its_legend_is_no_frame(tmp_path: Path):
    """A 20 by 20 room with a legend box east of it, inside a page border around both.

    A corridor's two walls run from the room's west wall to the border, so the drawing's ink reaches it. The
    border encloses the legend, so it's no frame of the region, and the region is the room, 20 by 20.
    """
    rgb = np.full((800, 1100, 3), 255, dtype=np.uint8)
    gridded_room(rgb, 100, 100, 20, 20)
    outline(rgb, 700, 100, 1000, 500)
    outline(rgb, 40, 40, 1060, 760)
    rgb[300:304, 40:100] = BAND_INK
    rgb[352:356, 40:100] = BAND_INK
    path = save_rgb(tmp_path / "border.png", rgb)
    fits = registered(register_image(path))
    assert len(fits) == 1, [fit.rect for fit in fits]
    assert grid_of(fits[0]) == (20, 20)
    assert all(
        abs(side - want) <= FRAME_RECT_TOLERANCE for side, want in zip(fits[0].rect, (100, 100, 620, 620), strict=True)
    ), fits[0].rect


def test_a_title_above_a_filled_frame_is_no_part_of_the_region():
    """Gatehouse's east half has a title row above its filled frame, and registers as its answer's grid."""
    truth = GeometryTruth.model_validate_json((SYNTHETIC / "gatehouse-apart" / "geometry.json").read_text())
    region = next(region for region in truth.regions if region.image == "gatehouse-east.jpg")
    fits = registered(register_image(SYNTHETIC / "gatehouse-apart" / "images" / "gatehouse-east.jpg"))
    assert len(fits) == 1, [fit.rect for fit in fits]
    assert grid_of(fits[0]) == (region.columns, region.rows)


class SyntheticMap:
    """One committed synthetic map: its directory, truth, and features."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.truth = GeometryTruth.model_validate_json((path / "geometry.json").read_text(encoding="utf-8"))
        manifest = json.loads((path / "synthetic.json").read_text(encoding="utf-8"))
        self.features: set[str] = set(manifest["features"])

    def image(self, region: Region) -> Path:
        return self.path / "images" / region.image


def synthetic_maps() -> list[SyntheticMap]:
    if not SYNTHETIC.is_dir():
        return []
    return [SyntheticMap(path) for path in sorted(SYNTHETIC.iterdir()) if path.is_dir()]


def maps_with(feature: str) -> list[SyntheticMap]:
    return [item for item in synthetic_maps() if feature in item.features]


def floor_corners(truth: GeometryTruth, region: Region) -> set[tuple[int, int]]:
    """The region's grid corners that touch a floor cell of its panel."""
    corners: set[tuple[int, int]] = set()
    for panel in truth.panels:
        for part in panel.regions:
            if part.region != region.id:
                continue
            ox, oy = part.origin
            for rj in range(region.rows):
                for ri in range(region.columns):
                    if panel.cells[rj + oy][ri + ox] == ".":
                        corners |= {(ri + di, rj + dj) for di in (0, 1) for dj in (0, 1)}
    return corners


def grid_point(registration: Registration, pixel: tuple[float, float]) -> tuple[float, float]:
    """The fractional grid coordinates of a pixel: an affine guess from every corner, refined by Newton's method."""
    corners = np.array(registration.corners, dtype=np.float64)
    rows, columns = corners.shape[0], corners.shape[1]
    design = np.column_stack([corners[..., 0].ravel(), corners[..., 1].ravel(), np.ones(rows * columns)])
    ii, jj = np.meshgrid(np.arange(columns, dtype=np.float64), np.arange(rows, dtype=np.float64))
    to_i, *_ = np.linalg.lstsq(design, ii.ravel(), rcond=None)
    to_j, *_ = np.linalg.lstsq(design, jj.ravel(), rcond=None)
    px, py = pixel
    gi = float(to_i[0] * px + to_i[1] * py + to_i[2])
    gj = float(to_j[0] * px + to_j[1] * py + to_j[2])
    step = 1e-3
    for _ in range(12):
        x, y = registration.pixel((gi, gj))
        x_i, y_i = registration.pixel((gi + step, gj))
        x_j, y_j = registration.pixel((gi, gj + step))
        jacobian = np.array([[(x_i - x) / step, (x_j - x) / step], [(y_i - y) / step, (y_j - y) / step]])
        if abs(np.linalg.det(jacobian)) < 1e-9:
            break
        di, dj = np.linalg.solve(jacobian, np.array([px - x, py - y]))
        gi, gj = gi + float(di), gj + float(dj)
        if abs(di) < 1e-6 and abs(dj) < 1e-6:
            break
    return gi, gj


def fit_for(fits: tuple[RegionFit, ...], region: Region) -> RegionFit:
    """The registered fit whose rectangle holds the centre of the region's true grid, and of several the smallest,
    which is an inset's own fit when the inset lies inside another region's frame."""
    assert region.registration is not None
    cx, cy = region.registration.pixel((region.columns / 2, region.rows / 2))
    holding = [fit for fit in registered(fits) if fit.rect[0] <= cx < fit.rect[2] and fit.rect[1] <= cy < fit.rect[3]]
    assert holding, [(fit.rect, fit.verdict, fit.reason) for fit in fits]
    return min(holding, key=lambda fit: (fit.rect[2] - fit.rect[0]) * (fit.rect[3] - fit.rect[1]))


def check_against_truth(item: SyntheticMap, region: Region, fit: RegionFit) -> tuple[int, int]:
    """The fit's grid holds the true grid, extends at most `GRID_TOLERANCE` lines beyond it on each side, and puts
    every true corner that touches floor within `CORNER_TOLERANCE` of a fitted corner, at one whole-line offset.

    Returns:
        That offset `(di, dj)`: the true corner `(i, j)` is the fitted corner `(i + di, j + dj)`.
    """
    assert region.registration is not None
    assert fit.registration is not None
    assert fit.pitch is not None
    check_gate(fit)
    true = region.registration
    offsets: set[tuple[int, int]] = set()
    worst = 0.0
    floor = floor_corners(item.truth, region)
    for j, row in enumerate(true.corners):
        for i, corner in enumerate(row):
            gi, gj = grid_point(fit.registration, corner)
            ri, rj = round(gi), round(gj)
            if (i, j) in floor:
                offsets.add((ri - i, rj - j))
                worst = max(worst, abs(gi - ri), abs(gj - rj))
    assert len(offsets) == 1, offsets
    (di, dj) = offsets.pop()
    assert 0 <= di <= GRID_TOLERANCE and 0 <= dj <= GRID_TOLERANCE, (di, dj)
    assert fit.registration.columns - di - region.columns in range(GRID_TOLERANCE + 1), (
        fit.registration.columns,
        region.columns,
        di,
    )
    assert fit.registration.rows - dj - region.rows in range(GRID_TOLERANCE + 1), (
        fit.registration.rows,
        region.rows,
        dj,
    )
    assert worst <= CORNER_TOLERANCE, worst
    true_pitch_x = (true.corners[0][-1][0] - true.corners[0][0][0]) / region.columns
    true_pitch_y = (true.corners[-1][0][1] - true.corners[0][0][1]) / region.rows
    assert abs(fit.pitch[0] - true_pitch_x) <= SYNTHETIC_PITCH_TOLERANCE * true_pitch_x, fit.pitch
    assert abs(fit.pitch[1] - true_pitch_y) <= SYNTHETIC_PITCH_TOLERANCE * true_pitch_y, fit.pitch
    return di, dj


def test_every_synthetic_region_that_should_register_does():
    """Every registered region of the committed set, in every style the generator draws: the TSR blue JPEG maps
    with their bowed pitch, skew, blur, and grain, the clean black and white PNG maps with hatched rock, and the
    dark rock and cave maps. The grid is printed on floor only, so a map's coverage counts its floor cells, its
    frame, and its labels, and the reference fit over synth-core's true grids gives 0.815 (fold-east) to 1.0."""
    items = synthetic_maps()
    assert items, "no synthetic maps are committed"
    for item in items:
        by_image: dict[str, list[Region]] = {}
        for region in item.truth.regions:
            by_image.setdefault(region.image, []).append(region)
        for image, regions in by_image.items():
            fits = register_image(item.path / "images" / image)
            # A region refused after the reading for its style or scale still registers when it has a grid, as
            # greyrock-country's valley at a quarter mile to the square does. Its answer has no corners, so it is
            # checked by its existence alone.
            expected = [region for region in regions if region.verdict == "registered"]
            refused_with_grid = [region for region in regions if region.verdict == "refused" and region.columns > 0]
            assert len(registered(fits)) == len(expected) + len(refused_with_grid), (
                item.path.name,
                image,
                [fit.rect for fit in fits],
            )
            for region in expected:
                check_against_truth(item, region, fit_for(fits, region))


def test_the_gridless_synthetic_map_yields_no_region():
    items = maps_with("reject_no_grid")
    assert items, "no synthetic map shows reject_no_grid"
    for item in items:
        for region in item.truth.regions:
            if region.style == "no_grid":
                assert registered(register_image(item.image(region))) == [], (item.path.name, region.id)


def test_the_warped_synthetic_map_fails_registration():
    """The warped map's answer records its region as unregistered. Its warp is at least the 0.6 of a pitch over
    two periods that the perturbed copy of page 47 tests, where the reference fit's coverage is 0.549."""
    items = maps_with("reject_warped")
    assert items, "no synthetic map shows reject_warped"
    for item in items:
        for region in item.truth.regions:
            if region.verdict == "unregistered" and region.style != "no_grid":
                fits = register_image(item.image(region))
                assert registered(fits) == [], (item.path.name, region.id, [fit.gate for fit in fits])


def halves_of(item: SyntheticMap, pair_index: int) -> tuple[Region, Region]:
    pair = item.truth.half_pairs[pair_index]
    regions = {region.id: region for region in item.truth.regions}
    first, second = regions[pair.regions[0]], regions[pair.regions[1]]
    assert "right" in first.cut_off and "left" in second.cut_off
    return first, second


class Aligned:
    """A half pair's alignment, with the answer's row shift and column offset translated into the fits' numbering.

    A fit that `check_against_truth` accepts may start a line before the true grid and end a line after it, so
    the answer's `rows` and `columns`, which count the true grids' lines, are translated through the two fits'
    offsets: the second fit's row 0 lies `rows + dj1 - dj2` rows south of the first fit's row 0, and the second
    fit's column 0 lies `columns + di1 - di2` columns past the first true grid's last column, which is the first
    fit's column `first.columns + di1`, so the offset counted past the first fit's last column is
    `columns + di1 - di2 + first.columns - first_fit.columns`.
    """

    def __init__(self, item: SyntheticMap, pair_index: int) -> None:
        first, second = halves_of(item, pair_index)
        first_fit = fit_for(register_image(item.image(first)), first)
        second_fit = fit_for(register_image(item.image(second)), second)
        di1, dj1 = check_against_truth(item, first, first_fit)
        di2, dj2 = check_against_truth(item, second, second_fit)
        assert first_fit.registration is not None
        self.alignment = align_halves(item.image(first), first_fit, item.image(second), second_fit)
        self.rows_shift = dj1 - dj2
        self.columns_shift = di1 - di2 + first.columns - first_fit.registration.columns

    def rows(self, true_rows: int) -> int:
        return true_rows + self.rows_shift

    def columns(self, true_columns: int) -> int:
        return true_columns + self.columns_shift


def test_the_fold_maps_align_their_rows_and_offer_the_true_offset():
    """fold-joined and fold-apart print the same drawing in two halves with no gap: the west half's grid reaches
    its right image edge at index 10.02 and the east half's first line lies at -0.5 px, so the candidates from
    the true grids are 0, 1, and 2 columns with gaps of 0.0, 1.0, and 2.0 cells. The ink along the fold agrees
    best at a row shift of 0 (correlation 0.999, against 0.55 for a shift of one row either way). fold-apart's
    answer keeps the halves apart and records no offset, but its drawing is the same, so its true offset is 0 too."""
    items = [item for item in synthetic_maps() if item.path.name in ("fold-joined", "fold-apart")]
    assert len(items) == 2, [item.path.name for item in items]
    for item in items:
        pair = item.truth.half_pairs[0]
        aligned = Aligned(item, 0)
        alignment = aligned.alignment
        assert alignment.rows == aligned.rows(pair.rows), (item.path.name, alignment, aligned.rows_shift)
        true_offset = pair.columns if pair.columns is not None else 0
        expected = aligned.columns(true_offset)
        assert expected in alignment.columns, (item.path.name, alignment, aligned.columns_shift)
        assert abs(alignment.gaps[alignment.columns.index(expected)]) <= GAP_TOLERANCE, (item.path.name, alignment)


def test_halves_with_a_gap_at_the_fold_offer_the_true_offset():
    """The halves synth-joins draws, joined at an offset with a gap at the fold and left apart with unequal
    stretches: the rows line up as the answer records and the true offset is among the three candidates."""
    items = [
        item
        for item in synthetic_maps()
        if item.truth.half_pairs and item.path.name not in ("fold-joined", "fold-apart")
    ]
    assert items, "no synthetic map beyond the fold maps has a half pair"
    for item in items:
        for index, pair in enumerate(item.truth.half_pairs):
            aligned = Aligned(item, index)
            alignment = aligned.alignment
            assert alignment.rows == aligned.rows(pair.rows), (item.path.name, index, alignment, aligned.rows_shift)
            if pair.columns is not None:
                expected = aligned.columns(pair.columns)
                assert expected in alignment.columns, (item.path.name, index, alignment, aligned.columns_shift)
                assert alignment.gaps[alignment.columns.index(expected)] >= -GAP_TOLERANCE


def test_align_halves_refuses_an_unregistered_half(tmp_path: Path):
    path = CHAOTIC_CAVES / "p047.png"
    fit = registered(register_image(path))[0]
    unregistered = RegionFit(rect=fit.rect, verdict="unregistered", reason="no fit")
    with pytest.raises(ValueError):
        align_halves(path, unregistered, path, fit)
    with pytest.raises(ValueError):
        align_halves(path, fit, path, unregistered)


SCAN_ROUNDS = 2
"""How many perturbed scans each synthetic image with a registered region gets, each from its own seed."""

SCAN_MISS_LIMIT = 0.05
"""The largest share of perturbed regions that may fail to register. A failure is safe, because the level falls back,
but every one loses a level the stage could have traced. The chunk's first build missed 6 of these 64 scans, and 14
more registered wrong. Four of the misses were sunken-temple, whose 1 px grid lines fade below the ink margin once a
resample splits them. Over 200 random scans in the chunk's review, it missed 13.5%."""


@dataclass(frozen=True)
class Scan:
    """A scan's distortions of a drawing, in the ranges a scanned module page shows."""

    bow: float
    """Pitches. Rows bend by this much at the middle when `bow_axis` is 0, and columns when it's 1."""

    bow_axis: int
    stretch: float
    """The share by which the pitch grows from the top of the image to the bottom."""

    scale: float
    rotation: float
    """Degrees, clockwise."""

    blur: float
    """Pixels, the radius of a Gaussian blur."""

    quality: int
    """The JPEG quality the scan is saved at."""

    @classmethod
    def draw(cls, seed: int) -> Scan:
        rng = np.random.default_rng(seed)
        return cls(
            bow=float(rng.uniform(-0.3, 0.3)),
            bow_axis=int(rng.integers(2)),
            stretch=float(rng.uniform(-0.04, 0.04)),
            scale=float(rng.uniform(0.8, 1.25)),
            rotation=float(rng.uniform(-1.0, 1.0)),
            blur=float(rng.uniform(0.0, 1.0)),
            quality=int(rng.integers(70, 96)),
        )


Mapping = Callable[[npt.NDArray[np.float64], npt.NDArray[np.float64]], tuple[npt.NDArray[np.float64], ...]]


def scanned(rgb: npt.NDArray[np.uint8], scan: Scan, pitch: float) -> tuple[Image.Image, Mapping]:
    """The image as the scan distorts it, and the map from a pixel of the drawing to its pixel in the scan."""
    height, width = rgb.shape[:2]
    bow, stretch, scale = scan.bow * pitch, scan.stretch, scan.scale
    turn = math.radians(scan.rotation)
    cos, sin = math.cos(turn), math.sin(turn)
    out_width, out_height = round(width * scale), round(height * scale)
    cx, cy, ox, oy = width / 2, height / 2, out_width / 2, out_height / 2

    def forward(x: npt.NDArray[np.float64], y: npt.NDArray[np.float64]) -> tuple[npt.NDArray[np.float64], ...]:
        if scan.bow_axis == 0:
            y = y + bow * 4 * (x / width) * (1 - x / width)
        else:
            x = x + bow * 4 * (y / height) * (1 - y / height)
        y = y + stretch * y * y / (2 * height)
        dx, dy = (x - cx) * scale, (y - cy) * scale
        return ox + cos * dx - sin * dy, oy + sin * dx + cos * dy

    def inverse(x: npt.NDArray[np.float64], y: npt.NDArray[np.float64]) -> tuple[npt.NDArray[np.float64], ...]:
        dx, dy = x - ox, y - oy
        x = (cos * dx + sin * dy) / scale + cx
        y = (-sin * dx + cos * dy) / scale + cy
        if stretch != 0:
            a = stretch / (2 * height)
            y = (-1 + np.sqrt(np.maximum(1 + 4 * a * y, 0))) / (2 * a)
        if scan.bow_axis == 0:
            y = y - bow * 4 * (x / width) * (1 - x / width)
        else:
            x = x - bow * 4 * (y / height) * (1 - y / height)
        return x, y

    yy, xx = np.mgrid[0:out_height, 0:out_width].astype(np.float64)
    sx, sy = inverse(xx, yy)
    x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
    fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
    inside = (x0 >= 0) & (y0 >= 0) & (x0 + 1 < width) & (y0 + 1 < height)
    x0, y0 = np.clip(x0, 0, width - 2), np.clip(y0, 0, height - 2)
    source = rgb.astype(np.float32)
    value = (
        source[y0, x0] * (1 - fx) * (1 - fy)
        + source[y0, x0 + 1] * fx * (1 - fy)
        + source[y0 + 1, x0] * (1 - fx) * fy
        + source[y0 + 1, x0 + 1] * fx * fy
    )
    value = np.where(inside[..., None], value, 255)
    image = Image.fromarray(np.clip(np.round(value), 0, 255).astype(np.uint8))
    if scan.blur > 0.05:
        image = image.filter(ImageFilter.GaussianBlur(scan.blur))
    return image, forward


@dataclass(frozen=True)
class ScanResult:
    seed: int | None
    map: str
    image: str
    region: str
    scan: Scan
    outcome: str
    """`right`, `missed` (no registered fit holds the region), or `wrong`."""

    detail: str


def judge(item: SyntheticMap, region: Region, fits: tuple[RegionFit, ...], forward: Mapping) -> tuple[str, str]:
    """Whether the region's registered fit, if any, holds its true grid moved by the scan, as check_against_truth
    asks of an unperturbed map."""
    assert region.registration is not None
    corners = np.array(region.registration.corners, dtype=np.float64)
    xs, ys = forward(corners[..., 0], corners[..., 1])
    true = Registration(
        corners=tuple(
            tuple((float(x), float(y)) for x, y in zip(row_x, row_y, strict=True))
            for row_x, row_y in zip(xs, ys, strict=True)
        )
    )
    cx, cy = true.pixel((region.columns / 2, region.rows / 2))
    holding = [fit for fit in registered(fits) if fit.rect[0] <= cx < fit.rect[2] and fit.rect[1] <= cy < fit.rect[3]]
    if not holding:
        return "missed", str([(fit.rect, fit.verdict, fit.reason) for fit in fits])
    fit = min(holding, key=lambda fit: (fit.rect[2] - fit.rect[0]) * (fit.rect[3] - fit.rect[1]))
    assert fit.registration is not None
    floor = floor_corners(item.truth, region)
    offsets: set[tuple[int, int]] = set()
    worst = 0.0
    for j, row in enumerate(true.corners):
        for i, corner in enumerate(row):
            if (i, j) in floor:
                gi, gj = grid_point(fit.registration, corner)
                ri, rj = round(gi), round(gj)
                offsets.add((ri - i, rj - j))
                worst = max(worst, abs(gi - ri), abs(gj - rj))
    detail = (
        f"grid {grid_of(fit)} against {(region.columns, region.rows)}, offsets {sorted(offsets)}, worst {worst:.3f}"
    )
    if len(offsets) != 1:
        return "wrong", detail
    (di, dj) = offsets.pop()
    holds = (
        0 <= di <= GRID_TOLERANCE
        and 0 <= dj <= GRID_TOLERANCE
        and fit.registration.columns - di - region.columns in range(GRID_TOLERANCE + 1)
        and fit.registration.rows - dj - region.rows in range(GRID_TOLERANCE + 1)
    )
    return ("right" if holds and worst <= CORNER_TOLERANCE else "wrong"), detail


def scan_one(item: SyntheticMap, image: str, regions: list[Region], scan: Scan, seed: int | None) -> list[ScanResult]:
    pitch = min(
        (region.registration.corners[0][-1][0] - region.registration.corners[0][0][0]) / region.columns
        for region in regions
        if region.registration is not None
    )
    picture, forward = scanned(load_rgb(item.path / "images" / image), scan, pitch)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "scan.jpg"
        picture.save(path, quality=scan.quality)
        fits = register_image(path)
    results = []
    for region in regions:
        outcome, detail = judge(item, region, fits, forward)
        results.append(ScanResult(seed, item.path.name, image, region.id, scan, outcome, detail))
    return results


FIXED_SCANS = [
    ("sunken-temple", "sunken-temple-2.png", Scan(0.0, 0, 0.04, 1.0, 0.0, 0.0, 95)),
    ("scattered-pages", "scattered-spread.png", Scan(0.0, 0, -0.04, 1.0, 0.0, 0.0, 95)),
]
"""Scans the chunk's review found registering at twice and four times the printed pitch, half a cell off."""


@functools.cache
def scan_results() -> tuple[ScanResult, ...]:
    """Every synthetic image with a registered region, scanned `SCAN_ROUNDS` times from fixed seeds, plus the fixed
    scans."""
    cases: list[tuple[SyntheticMap, str, list[Region]]] = []
    for item in synthetic_maps():
        by_image: dict[str, list[Region]] = {}
        for region in item.truth.regions:
            if region.verdict == "registered":
                by_image.setdefault(region.image, []).append(region)
        cases.extend((item, image, regions) for image, regions in sorted(by_image.items()))
    results: list[ScanResult] = []
    for round_ in range(SCAN_ROUNDS):
        for index, (item, image, regions) in enumerate(cases):
            seed = round_ * len(cases) + index
            results.extend(scan_one(item, image, regions, Scan.draw(seed), seed))
    by_name = {(item.path.name, image): (item, regions) for item, image, regions in cases}
    for name, image, scan in FIXED_SCANS:
        item, regions = by_name[(name, image)]
        results.extend(scan_one(item, image, regions, scan, None))
    return tuple(results)


def test_a_scanned_map_never_registers_a_wrong_grid():
    """A region that registers holds its true grid, moved by the scan, as an unperturbed map must: a 2 or 4 times
    pitch, a grid cut short, or a bow the drift correction doesn't follow all fail here."""
    wrong = [result for result in scan_results() if result.outcome == "wrong"]
    assert not wrong, "\n".join(f"{r.seed} {r.map}/{r.image}/{r.region} {r.scan}: {r.detail}" for r in wrong)


def test_most_scanned_maps_register():
    results = scan_results()
    missed = [result for result in results if result.outcome == "missed"]
    assert len(missed) <= SCAN_MISS_LIMIT * len(results), "\n".join(
        f"{r.seed} {r.map}/{r.image}/{r.region} {r.scan}: {r.detail}" for r in missed
    )
