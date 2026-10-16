"""The 5-foot conversion: a panel read in 5-foot squares becomes 10-foot cells.

The rules are in `docs/tracing-conventions.md` under "5-foot maps". The
conversion picks the block phase that puts the most walls on 10-foot lines,
turns each 2 by 2 block of squares into one cell, keeps narrow runs joined,
and moves each wall or door that lies on a line that isn't a 10-foot line onto
one, recording every move.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from osrforge.tracing.truth import Panel

__all__ = ["FiveFootConversion", "WallMove", "convert_five_foot"]


class WallMove(BaseModel):
    """One run of wall or door that the conversion moved onto a 10-foot line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    axis: Literal["vertical", "horizontal"]
    """Which way the line runs: a vertical line runs north to south."""

    line: int = Field(ge=0)
    """The 5-foot line the run lay on: the grid line index, 0 at the west or north frame."""

    moved_to: int = Field(ge=0)
    """The 10-foot line, as a 5-foot line index, the run moved to."""

    start: int = Field(ge=0)
    """The first square along the line the run covers."""

    end: int = Field(ge=0)
    """The square after the last the run covers."""


class FiveFootConversion(BaseModel):
    """A converted panel with the choices the conversion made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    panel: Panel
    """The panel in 10-foot cells. Its `scale_feet` is 10."""

    phase: tuple[int, int]
    """The block phase `(ox, oy)`: 10-foot lines lie on 5-foot lines `ox + 2k` across and `oy + 2k` down."""

    moves: tuple[WallMove, ...]
    """Every wall or door run moved onto a 10-foot line, in north-then-west order of the runs."""


def convert_five_foot(panel: Panel) -> FiveFootConversion:
    """Convert a 5-foot panel to 10-foot cells.

    Cell `(X, Y)` of the result covers 5-foot squares `2X - ox` and `2X - ox + 1`
    across and `2Y - oy` and `2Y - oy + 1` down, where `(ox, oy)` is the chosen
    phase. Squares outside the 5-foot grid count as rock. The result is
    `ceil((width + ox) / 2)` cells wide and `ceil((height + oy) / 2)` high.

    Args:
        panel: A panel whose `scale_feet` is 5.

    Returns:
        The converted panel, the phase, and the moves.

    Raises:
        ValueError: If the panel isn't a 5-foot panel.
    """
    raise NotImplementedError("chunk: five-foot")
