"""Grid registration: the fit of the printed grid to each region of a map image, and the alignment of two halves.

The tracing stage's second step. A *region* is one framed drawing on a map
image, and *registering* it means fitting the printed grid to it: one fit
over the whole region, with a separate x and y pitch, a shear, and a smooth,
low-order correction for scan drift, written out as the pixel position of
every grid corner (a [`Registration`][osrforge.tracing.truth.Registration]).
A gate with three parts judges the fit, and a region whose fit fails any part
is *unregistered*. Whether a registered region is refused, for a style the
stage can't trace or a scale it can't use, is decided after the whole-map
reading, not here.

Two regions printed as the halves of one map on facing pages get a row
alignment and three candidate column offsets from their two registrations and
the ink along the fold. The reading provider later picks one of the offsets,
or none.

The functions here use numpy and Pillow only, call no model, and give the
same result for the same image on every run.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osrforge.tracing.truth import Registration

__all__ = [
    "BACKGROUND_RADIUS",
    "COVERAGE_LINE_SHARE",
    "COVERAGE_MINIMUM",
    "COVERAGE_TOLERANCE",
    "EXPLAINED_MINIMUM",
    "FILL_DARKNESS",
    "GAP_TOLERANCE",
    "INK_MARGIN",
    "LINE_TOLERANCE",
    "OFFSET_CANDIDATES",
    "REJECT_TOLERANCE",
    "GateFigures",
    "HalfAlignment",
    "RegionFit",
    "align_halves",
    "register_image",
]

LINE_TOLERANCE = 0.1
"""Cells. A detected line is explained when it lies within this distance of the fitted line."""

REJECT_TOLERANCE = 0.25
"""Pitches. A detected line farther than this from every fitted line is rejected as not part of the grid (a wall,
a stroke of a symbol, or a label). It counts against the explained share all the same."""

EXPLAINED_MINIMUM = 0.9
"""The least share of detected lines the fit must explain within `LINE_TOLERANCE`."""

COVERAGE_MINIMUM = 0.7
"""The least share of a region's counted cells that must have ink under the fitted grid lines through them.

Measured with the reference fit of the registration measurement: the committed
real maps give 0.857 to 0.996, the synthetic maps over their true grids 0.815
to 1.0, a copy of Chaotic Caves page 47 bowed by 0.3 of a pitch 0.875, and a
copy of page 38 stretched by 4% 0.939. Copies of page 47 warped by a sine of
0.45 to 0.8 of a pitch over two periods give 0.38 to 0.55, and the sine of 0.6
over three periods 0.526.
"""

COVERAGE_TOLERANCE = 0.15
"""Pitches. Ink within this distance of a fitted grid line counts as lying under it."""

COVERAGE_LINE_SHARE = 0.6
"""A cell is covered when ink lies under at least this share of each of its two fitted lines."""

INK_MARGIN = 35.0
"""How much darker than its local background a pixel must be to count as ink.

The reference fit's value. On the synthetic TSR blue maps the blurred grid
lines are 16 to 47 darker than the paper beside them, so the reference loses
part of every line there, and the value the implementation settles on must
keep the committed maps and the perturbed copies on their sides of the gate.
"""

BACKGROUND_RADIUS = 6
"""Pixels. A pixel's local background is the lightest pixel within this distance of it."""

FILL_DARKNESS = 80.0
"""A pixel whose darkest channel is at most `255 - FILL_DARKNESS` is fill or solid ink, not paper."""

OFFSET_CANDIDATES = 3
"""How many column offsets `align_halves` returns for the reading provider to choose among."""

GAP_TOLERANCE = 0.1
"""Cells. A candidate offset's gap at the fold may be this far below zero, for the fits' error."""


class GateFigures(BaseModel):
    """The registration gate's figures for one region's fit.

    The *detected lines* are found band by band: the region is cut, along
    each axis in turn, into bands about three pitches wide (at least three
    pitches, and at most twelve bands), and each peak of a band's profile of
    thin ink runs that stands out is one detected line. One printed line that
    crosses twelve bands is twelve detected lines, and a band with no grid in
    it contributes none. A detected line within `REJECT_TOLERANCE` of a pitch
    of a fitted line gets that line's index. The others are *rejected*, and
    stay in the denominator of the explained share.

    The gate passes when `explained_share` is at least `EXPLAINED_MINIMUM`,
    `index_jumps` is 0, and `coverage` is at least `COVERAGE_MINIMUM`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    detected_lines: int = Field(ge=0)
    """Every detected line, indexed or rejected, over both axes."""

    rejected_lines: int = Field(ge=0)
    """The detected lines the fit rejected."""

    explained_share: float = Field(ge=0.0, le=1.0)
    """The share of `detected_lines` that lie within `LINE_TOLERANCE` of the fitted line they're indexed to."""

    index_jumps: int = Field(ge=0)
    """Pairs of neighbouring indexed lines in one band, with no detected line between them, whose index difference
    isn't their distance in pitches rounded to a whole number. A grid with part of its lines shifted by half a
    pitch, or a pitch taken as a fraction of the printed one, shows here or in the explained share."""

    counted_cells: int = Field(ge=0)
    """The cells of the fitted grid that hold ink and aren't fill: a cell with fewer ink pixels than three tenths
    of a pitch is blank, and a cell with `FILL_DARKNESS` or darker over at least half its pixels is fill. Neither
    is counted."""

    covered_cells: int = Field(ge=0)
    """The counted cells whose west and north fitted lines each have ink within `COVERAGE_TOLERANCE` of a pitch
    along at least `COVERAGE_LINE_SHARE` of their length."""

    coverage: float = Field(ge=0.0, le=1.0)
    """`covered_cells` over `counted_cells`, or 0 when no cell is counted."""

    @property
    def passes(self) -> bool:
        """Whether the three parts of the gate pass."""
        return self.explained_share >= EXPLAINED_MINIMUM and self.index_jumps == 0 and self.coverage >= COVERAGE_MINIMUM

    @model_validator(mode="after")
    def _counts_agree(self) -> GateFigures:
        if self.rejected_lines > self.detected_lines:
            raise ValueError("rejected lines are among the detected lines")
        if self.covered_cells > self.counted_cells:
            raise ValueError("covered cells are among the counted cells")
        return self


class RegionFit(BaseModel):
    """One gridded region of a map image, with its fit and the gate's verdict.

    The region's rectangle is the framed drawing. Where a frame is drawn, the
    grid runs from the frame's west line to its east line and from its north
    line to its south line, so a title, a legend, or a compass outside the
    frame is no part of the region, and rock inside the frame is. Where no
    frame is drawn, the grid runs from the first printed grid line to the
    last on each axis. A legend or a label inside the rectangle doesn't move
    the fit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rect: tuple[int, int, int, int]
    """`(x0, y0, x1, y1)` in pixels of the image, the end exclusive."""

    pitch: tuple[float, float] | None = None
    """The fitted x and y pitch in pixels, present whenever a fit was made."""

    verdict: Literal["registered", "unregistered"]
    """Whether the fit passed the gate."""

    reason: str | None = None
    """Why the region is unregistered, or `None` when registered."""

    gate: GateFigures | None = None
    """The gate's figures, present whenever a fit was made. A region with too few lines for a fit has none."""

    registration: Registration | None = None
    """The fitted grid, present exactly when the verdict is `registered`."""

    @model_validator(mode="after")
    def _verdict_fields_agree(self) -> RegionFit:
        registered = self.verdict == "registered"
        if registered != (self.registration is not None):
            raise ValueError("a registration is present exactly when the verdict is registered")
        if registered == (self.reason is not None):
            raise ValueError("a reason is present exactly when the verdict is unregistered")
        if (self.gate is None) != (self.pitch is None):
            raise ValueError("the gate's figures and the pitch are present together")
        if registered and self.gate is None:
            raise ValueError("a registered region has the gate's figures")
        if self.gate is not None and self.gate.passes != registered:
            raise ValueError("the verdict follows the gate's figures")
        x0, y0, x1, y1 = self.rect
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"the rectangle {self.rect} has no area")
        return self


class HalfAlignment(BaseModel):
    """How two halves of one map line up, from their registrations and the ink along the fold.

    The fold is the first half's right image edge and the second half's left
    image edge. The halves share one grid when the second half's first grid
    line sits a whole number of columns from the first half's grid. Each
    candidate column offset is that whole number counted past the first half's
    last column, as `HalfPair.columns` records it, and its gap is the paper
    the two scans would leave between them at the fold, in cells. The
    candidates are the `OFFSET_CANDIDATES` offsets with the smallest gaps that
    aren't below `-GAP_TOLERANCE`, in increasing order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: int
    """The row shift: the second half's row 0 lies this many rows south of the first half's row 0, as
    `HalfPair.rows` records it. The grid phase at the fold gives the fraction of a row, and the whole rows are the
    shift at which the ink along the fold agrees best between the two halves. On a tie, the shift that puts matching
    rows at the nearest heights on their two pages wins, and if that ties too, the shift north wins."""

    columns: tuple[int, ...] = Field(min_length=OFFSET_CANDIDATES, max_length=OFFSET_CANDIDATES)
    """The candidate column offsets, consecutive and increasing."""

    gaps: tuple[float, ...] = Field(min_length=OFFSET_CANDIDATES, max_length=OFFSET_CANDIDATES)
    """Each candidate's gap at the fold in cells, increasing, none below `-GAP_TOLERANCE`."""

    @model_validator(mode="after")
    def _candidates_are_consecutive(self) -> HalfAlignment:
        if any(b - a != 1 for a, b in zip(self.columns, self.columns[1:], strict=False)):
            raise ValueError(f"the candidate offsets are consecutive, not {self.columns}")
        if any(b <= a for a, b in zip(self.gaps, self.gaps[1:], strict=False)):
            raise ValueError(f"the gaps increase with the offsets, not {self.gaps}")
        if self.gaps[0] < -GAP_TOLERANCE:
            raise ValueError(f"the smallest gap is at least {-GAP_TOLERANCE} cells, not {self.gaps[0]}")
        return self


def register_image(image: Path) -> tuple[RegionFit, ...]:
    """Find the gridded regions of a map image and fit the printed grid to each.

    The image is read as RGB. Ink is what's darker than its local background
    by `INK_MARGIN`, so a grid line on grey fill counts and the inside of a
    solid wall doesn't. Each framed drawing with a printed grid is one
    region, found without any rectangle given by hand, and two drawings on
    one image at different pitches are two regions with a fit each. A
    drawing with no grid yields no region, so an image of a gridless map
    yields an empty tuple.

    Each region gets one fit over the whole region: a separate x and y
    pitch, a shear, and a smooth, low-order correction for scan drift, never
    a fit tile by tile. The fit is judged by the three parts of the gate (see
    `GateFigures`), and the region's `registration` holds every corner of the
    fitted grid when the gate passes. A copy of a map bowed by three tenths of
    a pitch, or stretched by 4% from top to bottom, registers, because the
    drift correction follows it. A copy with part of its grid shifted by half
    a pitch, and a copy warped by a sine of 0.6 of a pitch over two periods,
    don't.

    Args:
        image: The image file, as the workdir's `maps/` directory stores it
            or as the test assets commit it.

    Returns:
        The regions in page order: by the top of their rectangles, then by the
        left. The same image gives the same result on every run.

    Raises:
        OSError: If the image can't be read.
    """
    raise NotImplementedError("chunk: registration")


def align_halves(first: Path, first_fit: RegionFit, second: Path, second_fit: RegionFit) -> HalfAlignment:
    """Align the registered halves of one map across the fold.

    The first half is the one cut off at the right edge of its image and the
    second the one cut off at the left edge of its image, in page order. The
    column candidates come from the two registrations alone: where the first
    half's grid reaches its right image edge, and where the second half's
    first grid line lies from its left image edge. The row shift's fraction
    comes from the two grids' phases at the fold, and its whole rows from the
    ink of the first half's last cell and a half against the second half's
    first cell and a half, over every whole-row shift.

    Args:
        first: The first half's image file.
        first_fit: Its registered fit.
        second: The second half's image file.
        second_fit: Its registered fit.

    Returns:
        The row shift and the candidate column offsets with their gaps.

    Raises:
        ValueError: If either fit is unregistered.
        OSError: If an image can't be read.
    """
    raise NotImplementedError("chunk: registration")
