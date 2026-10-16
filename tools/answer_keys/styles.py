"""The thresholds each map style reads with.

A style names the rules' settings. The levels that separate a map's ink,
paper, and fill are per map, in its map spec, because two maps of one style
print their fill at different grays.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    """One style's thresholds."""

    name: str
    wall_min: float
    """Pixels of ink across an edge line at which the edge is blocked."""

    wall_scale: float
    """Pixels of ink over which the blocked probability goes from 0.27 to 0.73 around `wall_min`."""

    cover_min: float = 0.8
    """The share of an edge's length that a wall's ink covers on the line. Less, and the ink is text or a symbol."""

    door_length: tuple[float, float] = (0.2, 0.8)
    """The share of an edge a door's paper gap spans, least and most."""

    door_across: tuple[float, float] = (0.12, 0.7)
    """A door box's reach across the line, in cells, least and most."""

    hatch_runs: int = 6
    """How many parallel bars a cell needs across its width to be a stair."""

    thick_walls: bool = True
    """Whether walls are bands wider than 3 px, so a thin line through a door's box isn't the wall."""

    box_sides_min: float = 0.6
    """The share of a door gap's length that the box's outline covers, on the side with less."""

    circle_shape: float = 0.8
    """Below this ratio of the reach near the gap's ends to the reach at its centre, the symbol is a circle: a
    window."""

    secret_inside: float = 0.08
    """The share of ink inside a door's box at which the door is marked: secret or locked, by `inside_letter`."""

    inside_letter: str = "s"
    """What a mark inside a door's box means: `s` for an S (secret), `l` for a dot (locked)."""

    glyph_length: tuple[float, float] = (0.2, 0.92)
    """The share of an edge a secret door's glyph straddles, least and most."""

    glyph_reach: float = 0.4
    """How far a secret door's glyph may reach from the line, in cells. Ink reaching farther is a crossing wall."""

    archways: bool = False
    """Whether the style prints archways: a wall gap with no box, barred at its ends."""

    dashed_lines: bool = True
    """Whether the style prints dashed partitions, which are printed open lines."""

    floor_needs_grid: bool = False
    """Whether a floor cell needs printed grid lines on three of its sides, for a map whose outside is paper."""

    cave: bool = False
    """Whether the style draws its walls off the grid, so partly floor cells are cave cells."""

    snap_offset_walls: bool = False
    """Whether a wall drawn parallel to an edge within half a cell counts on that edge."""


STYLES: dict[str, Style] = {
    "tsr_blue": Style(
        name="tsr_blue",
        wall_min=1.8,
        wall_scale=0.5,
        archways=True,
        circle_shape=0.5,
        dashed_lines=False,
        thick_walls=False,
        hatch_runs=4,
        glyph_reach=0.35,
    ),
    "tsr_blue_white": Style(
        name="tsr_blue_white",
        wall_min=1.8,
        wall_scale=0.5,
        archways=True,
        floor_needs_grid=True,
        circle_shape=0.5,
        dashed_lines=False,
        thick_walls=False,
    ),
    "black_white": Style(name="black_white", wall_min=3.5, wall_scale=0.7, snap_offset_walls=True),
    "cave": Style(
        name="cave", wall_min=3.5, wall_scale=0.7, cave=True, snap_offset_walls=True, inside_letter="l", glyph_reach=0.5
    ),
    "dark_rock": Style(name="dark_rock", wall_min=3.5, wall_scale=0.7, cave=True),
}
"""The styles by name. `tsr_blue_white` is TSR blue with paper outside the building, like Palace level 2."""
