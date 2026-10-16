"""The synthetic maps: pictures drawn from known levels, so their answers are exact.

Each synthetic map is a directory under `tests/assets/synthetic-maps/` with
the map's answer (`geometry.json`), its manifest (`synthetic.json`), its images
(`images/`), and the survey, content, and map-reading caches the stage would
have (`stages/`). [`generate`][osrforge.tracing.synthetic.generate] writes
every map, and the committed set is what it writes: a test regenerates the set
and fails on any difference. The maps are CC0.

The features a synthetic map can show are the `FEATURES`, each described by
the sentence from the plan it comes from. A map's manifest names the features
it shows and the damage sites a test uses to make single-change copies of its
answer.
"""

import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CHANGES",
    "FEATURES",
    "FEATURE_DESCRIPTIONS",
    "Change",
    "DamageSite",
    "Feature",
    "SyntheticImage",
    "SyntheticManifest",
    "generate",
]

FEATURE_DESCRIPTIONS: dict[str, str] = {
    "walled_courtyard": "a walled courtyard",
    "farmstead_open_ground": "a farmstead of separate buildings on open ground",
    "tree_house_no_door": "a tree house that has no door",
    "moated_house_bridge": "a moated house with a bridge",
    "key_at_rear_door": "a key printed at a house's rear door",
    "keys_on_water": "keys printed on water",
    "two_levels_joined_by_door": "two levels joined by a door",
    "two_levels_joined_by_open_floor": "two levels joined by open floor",
    "key_outside_open_cave_mouth": "a key printed outside an open cave mouth",
    "key_outside_walled_cave_mouth": "a key printed outside a walled cave mouth",
    "level_with_two_cave_mouths": "a level with two cave mouths",
    "corridor_off_frame": "a corridor that runs off the frame",
    "gateway_no_door_between_towers": "a gateway with no door between two towers",
    "wide_open_stairway_only_way_in": "a wide open stairway as the only way in",
    "entrance_chamber_in_cave_mouth_unprinted_key": (
        "an entrance chamber inside a cave mouth whose key the map doesn't print"
    ),
    "open_hall_several_keys": "an open hall with several keys",
    "labelled_one_cell_hall": "a labelled one-cell hall",
    "dashed_circle_in_room": "a dashed circle inside a larger room",
    "jail_cells_row_one_key": "a row of jail cells with one key",
    "key_off_floor": "a key printed off the floor",
    "key_printed_twice": "a key printed twice",
    "keys_stacked": "keys printed stacked one above the other",
    "key_with_leader_line": "a key with a leader line",
    "letter_alone": "a letter on its own",
    "letter_under_two_keys": "a letter named by two keys",
    "letter_suffix": "a letter printed as a suffix",
    "letter_lair_label_outside": "a letter as a lair label on the outside",
    "dash_dot_partition": "a dash-dot partition",
    "windows": "windows",
    "pillars": "pillars",
    "steps_inside_level": "steps inside a level",
    "overlay_contours_tracks_stream": "an overlay of contours, tracks, and stream hatching",
    "five_foot_rooms_two_and_three_wide": "a 5-foot map with rooms two and three squares wide",
    "rotated_page": "a rotated page",
    "no_compass": "a map with no compass",
    "legend_on_another_page": "a legend printed on another page",
    "several_levels_one_page": "several levels on one page",
    "stairs_with_direction": "stairs with a direction mark",
    "stairs_without_direction": "stairs without a direction mark",
    "two_staircases_one_link": "two staircases serving one link",
    "halves_joined_at_offset": "a map printed in two halves, joined at an offset",
    "halves_gap_at_fold": "two halves with a gap at the fold",
    "halves_legend_compass_one_half": "two halves whose legend and compass are on one half only",
    "halves_apart_unequal_stretches": "two halves left apart, with stretches of unequal number and length",
    "halves_apart_door_ends_stretch": "two halves left apart, with a door that ends a stretch at the fold",
    "halves_apart_stretch_without_partner": "two halves left apart, with a stretch left without a partner",
    "panels_two_scales_one_page": "a level drawn as two panels at different scales on one page",
    "panels_two_pages_ladder": "a level drawn as two panels on two pages, joined by a ladder",
    "panels_trapdoor": "two panels joined by a trapdoor",
    "panels_slide": "two panels joined by a slide",
    "inset": "an inset",
    "levels_meet_at_door": "two levels whose areas meet at a door",
    "levels_meet_at_locked_secret_door": "two levels whose areas meet at a locked secret door",
    "door_no_vestibule_fits": "a door where no vestibule fits",
    "corridor_bends_across_cut": "a corridor that bends across a cut",
    "passage_at_floor_edge": "a passage at the edge of a level's floor",
    "image_hidden_under_shown": "an image hidden under the one a page shows",
    "page_spread": "a spread",
    "page_duplicate_image": "a duplicate image",
    "page_blanked": "a blanked page",
    "reject_no_grid": "a map with no grid, which registration must reject",
    "reject_warped": "a warped map, which registration must reject",
    "refuse_hex": "a hex map",
    "refuse_isometric": "an isometric map",
    "refuse_side_view": "a side view",
    "refuse_outdoor": "an outdoor map",
    "refuse_town": "a town map",
    "refuse_quarter_mile": "a map at a quarter mile to the square",
}
"""Each feature id with the plan's words for it."""

Feature = Literal[
    "walled_courtyard",
    "farmstead_open_ground",
    "tree_house_no_door",
    "moated_house_bridge",
    "key_at_rear_door",
    "keys_on_water",
    "two_levels_joined_by_door",
    "two_levels_joined_by_open_floor",
    "key_outside_open_cave_mouth",
    "key_outside_walled_cave_mouth",
    "level_with_two_cave_mouths",
    "corridor_off_frame",
    "gateway_no_door_between_towers",
    "wide_open_stairway_only_way_in",
    "entrance_chamber_in_cave_mouth_unprinted_key",
    "open_hall_several_keys",
    "labelled_one_cell_hall",
    "dashed_circle_in_room",
    "jail_cells_row_one_key",
    "key_off_floor",
    "key_printed_twice",
    "keys_stacked",
    "key_with_leader_line",
    "letter_alone",
    "letter_under_two_keys",
    "letter_suffix",
    "letter_lair_label_outside",
    "dash_dot_partition",
    "windows",
    "pillars",
    "steps_inside_level",
    "overlay_contours_tracks_stream",
    "five_foot_rooms_two_and_three_wide",
    "rotated_page",
    "no_compass",
    "legend_on_another_page",
    "several_levels_one_page",
    "stairs_with_direction",
    "stairs_without_direction",
    "two_staircases_one_link",
    "halves_joined_at_offset",
    "halves_gap_at_fold",
    "halves_legend_compass_one_half",
    "halves_apart_unequal_stretches",
    "halves_apart_door_ends_stretch",
    "halves_apart_stretch_without_partner",
    "panels_two_scales_one_page",
    "panels_two_pages_ladder",
    "panels_trapdoor",
    "panels_slide",
    "inset",
    "levels_meet_at_door",
    "levels_meet_at_locked_secret_door",
    "door_no_vestibule_fits",
    "corridor_bends_across_cut",
    "passage_at_floor_edge",
    "image_hidden_under_shown",
    "page_spread",
    "page_duplicate_image",
    "page_blanked",
    "reject_no_grid",
    "reject_warped",
    "refuse_hex",
    "refuse_isometric",
    "refuse_side_view",
    "refuse_outdoor",
    "refuse_town",
    "refuse_quarter_mile",
]
"""A feature id."""

FEATURES: tuple[str, ...] = tuple(FEATURE_DESCRIPTIONS)
"""Every feature id, in the plan's order."""

Change = Literal[
    "wall_removed",
    "key_moved",
    "door_kind_changed",
    "secret_door_removed",
    "transition_moved",
    "passage_moved",
    "halves_offset_changed",
    "refusal_flipped",
]
"""One single change a test makes to a map's answer."""

CHANGES: tuple[str, ...] = (
    "wall_removed",
    "key_moved",
    "door_kind_changed",
    "secret_door_removed",
    "transition_moved",
    "passage_moved",
    "halves_offset_changed",
    "refusal_flipped",
)
"""Every change, in the plan's order."""


class DamageSite(BaseModel):
    """Where a test applies one change to a map's answer.

    The site is chosen so the change touches one scorer family and no other.
    A wall is removed between two floor cells of one level, where no passage
    stands, and either both cells are in one area or in corridor, or the two
    areas they belong to already adjoin elsewhere. A key moves to a cell
    outside its area. A door's kind changes from plain to secret. A secret door
    is removed that isn't the only join between two areas and leaves every
    area reachable. A transition or passage moves to another floor cell of its
    level, a passage onto another wall side. A half pair's column offset grows
    by one. A traced level becomes a fallback, and that level isn't the
    dungeon's entrance level and no passage leads to it. A map's answer
    reaches every area, so a removed wall can't change reachability. An edge
    site stands on a 10-foot panel that no level turns, so the same edge is
    changed in the panel and, at the panel's origin, in the level.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    change: Change
    dungeon: str | None = None
    level: int | None = Field(default=None, ge=1)
    panel: str | None = None
    """The panel the edge is on, for the three edge changes. The test changes the edge in the panel's reading and
    in the grid of the level that uses the panel."""

    edge: str | None = None
    """The edge in panel coordinates, for `wall_removed`, `door_kind_changed`, and `secret_door_removed`."""

    label: str | None = None
    """The key's label, for `key_moved`."""

    cell: str | None = None
    """The transition's or passage's cell, for `transition_moved` and `passage_moved`."""

    side: str | None = None
    """The passage's side, for `passage_moved`."""

    to_cell: str | None = None
    """Where the key, transition, or passage moves to."""

    to_side: str | None = None
    """The moved passage's side."""

    pair: int | None = Field(default=None, ge=0)
    """The index into `half_pairs`, for `halves_offset_changed`."""


class SyntheticImage(BaseModel):
    """One image file of a synthetic map, with the colours an ink test needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    """The file under `images/`."""

    ink: tuple[int, int, int]
    """The colour the map's lines and fills are drawn in."""

    paper: tuple[int, int, int]
    """The colour of the blank page."""


class SyntheticManifest(BaseModel):
    """What one synthetic map is for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    """The directory name."""

    title: str
    """The module title the survey cache gives it."""

    features: tuple[Feature, ...]
    """The features this map shows."""

    damage: tuple[DamageSite, ...] = ()
    images: tuple[SyntheticImage, ...] = Field(min_length=1)


def generate(out_dir: Path) -> tuple[str, ...]:
    """Write every synthetic map under a directory.

    Each map gets `<out_dir>/<name>/geometry.json`, `synthetic.json`,
    `images/<file>` for each image, `stages/survey.json`,
    `stages/areas.<dungeon>.<level>.json` for every level, refused and
    fallen-back levels included, and `stages/mapread.json` with its knob
    `read`. Running it twice writes the same bytes for every file except the
    JPEG images, whose decoded pixels are the same.

    Args:
        out_dir: The directory to write under. It's created if missing, and a
            map's directory is replaced if present.

    Returns:
        The map names, sorted.
    """
    raise NotImplementedError("chunk: synth-core")


def main(argv: list[str] | None = None) -> int:
    """Write the synthetic maps: `python -m osrforge.tracing.synthetic <out-dir>`.

    Args:
        argv: The arguments after the module name, or `sys.argv[1:]` when omitted.

    Returns:
        The exit status: 0 when every map was written.
    """
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m osrforge.tracing.synthetic <out-dir>", file=sys.stderr)
        return 2
    names = generate(Path(arguments[0]))
    print("\n".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
