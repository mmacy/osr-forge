"""The synthetic maps: the committed set, its regeneration, its caches, its answers, its features, and its pictures.

The set lives under `tests/assets/synthetic-maps/`. Each test here reads the
committed set, so until the generator chunks land there is nothing to read
and every test fails for that reason.
"""

from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from osrforge.contracts.stages import LevelContent, MapReading, SurveyIndex
from osrforge.tracing.score import score_trace
from osrforge.tracing.synthetic import CHANGES, FEATURES, SyntheticManifest, generate
from osrforge.tracing.truth import GeometryTruth, Panel, Region, Symbol, load_geometry_truth, parse_cell
from tracing_support import TOUCHED_FAMILIES, apply_damage

SYNTHETIC = Path(__file__).parent / "assets" / "synthetic-maps"
SIZE_LIMIT = 8 * 1024 * 1024
JPEG_MAX_DIFFERENCE = 6
JPEG_MEAN_DIFFERENCE = 0.5

CORE = "chunk: synth-core"
STYLES = "chunk: synth-styles"
JOINS = "chunk: synth-joins"

FEATURE_CHUNK: dict[str, str] = {
    **dict.fromkeys(
        (
            "walled_courtyard",
            "farmstead_open_ground",
            "tree_house_no_door",
            "moated_house_bridge",
            "key_at_rear_door",
            "keys_on_water",
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
            "stairs_with_direction",
            "stairs_without_direction",
            "two_staircases_one_link",
        ),
        CORE,
    ),
    **dict.fromkeys(
        (
            "five_foot_rooms_two_and_three_wide",
            "rotated_page",
            "no_compass",
            "legend_on_another_page",
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
        ),
        STYLES,
    ),
    **dict.fromkeys(
        (
            "several_levels_one_page",
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
            "two_levels_joined_by_door",
            "two_levels_joined_by_open_floor",
            "levels_meet_at_door",
            "levels_meet_at_locked_secret_door",
            "door_no_vestibule_fits",
            "corridor_bends_across_cut",
            "passage_at_floor_edge",
        ),
        JOINS,
    ),
}
"""Which generator chunk draws each feature."""


def map_dirs() -> list[Path]:
    if not SYNTHETIC.is_dir():
        return []
    return sorted(path for path in SYNTHETIC.iterdir() if path.is_dir())


def load_map(path: Path) -> tuple[SyntheticManifest, GeometryTruth]:
    manifest = SyntheticManifest.model_validate_json((path / "synthetic.json").read_text(encoding="utf-8"))
    return manifest, load_geometry_truth(path / "geometry.json")


def loaded_maps() -> list[tuple[Path, SyntheticManifest, GeometryTruth]]:
    return [(path, *load_map(path)) for path in map_dirs()]


@pytest.mark.xfail(reason=CORE, strict=True)
def test_set_is_committed():
    assert FEATURE_CHUNK.keys() == set(FEATURES)
    assert map_dirs(), "no synthetic maps are committed"
    for path, manifest, truth in loaded_maps():
        assert manifest.name == path.name
        assert (path / "stages" / "survey.json").is_file()
        assert (path / "stages" / "mapread.json").is_file()
        for image in manifest.images:
            assert (path / "images" / image.file).is_file()
        for region in truth.regions:
            assert any(image.file == region.image for image in manifest.images)


def pixels(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).astype(np.int16)


@pytest.mark.xfail(reason=CORE, strict=True)
def test_regeneration_matches_the_committed_set(tmp_path: Path):
    names = generate(tmp_path)
    assert list(names) == [path.name for path in map_dirs()]
    committed = {path.relative_to(SYNTHETIC) for path in SYNTHETIC.rglob("*") if path.is_file()}
    regenerated = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert committed == regenerated
    for relative in sorted(committed):
        fresh, kept = tmp_path / relative, SYNTHETIC / relative
        if relative.suffix.lower() in (".jpg", ".jpeg"):
            difference = np.abs(pixels(fresh) - pixels(kept))
            assert difference.max() <= JPEG_MAX_DIFFERENCE, relative
            assert difference.mean() <= JPEG_MEAN_DIFFERENCE, relative
        elif relative.suffix.lower() == ".png":
            assert np.array_equal(pixels(fresh), pixels(kept)), relative
        else:
            assert fresh.read_bytes() == kept.read_bytes(), relative


@pytest.mark.xfail(reason=CORE, strict=True)
def test_committed_set_stays_small():
    total = sum(path.stat().st_size for path in SYNTHETIC.rglob("*") if path.is_file())
    assert 0 < total <= SIZE_LIMIT


@pytest.mark.xfail(reason=CORE, strict=True)
def test_caches_load_through_the_stage_models():
    assert map_dirs()
    for path, manifest, truth in loaded_maps():
        survey = SurveyIndex.model_validate_json((path / "stages" / "survey.json").read_text(encoding="utf-8"))
        assert survey.title == manifest.title
        assert [dungeon.id for dungeon in survey.dungeons] == [dungeon.id for dungeon in truth.dungeons]
        for dungeon, surveyed in zip(truth.dungeons, survey.dungeons, strict=True):
            assert [level.number for level in surveyed.levels] == [level.number for level in dungeon.levels]
            for level, surveyed_level in zip(dungeon.levels, surveyed.levels, strict=True):
                content = LevelContent.model_validate_json(
                    (path / "stages" / f"areas.{dungeon.id}.{level.number}.json").read_text(encoding="utf-8")
                )
                assert content.dungeon_id == dungeon.id
                assert content.level_number == level.number
                surveyed_keys = {area.key for area in surveyed_level.areas}
                assert {area.key for area in content.areas} == surveyed_keys
                if level.verdict == "traced":
                    assert set(level.areas) <= surveyed_keys
        reading = MapReading.model_validate_json((path / "stages" / "mapread.json").read_text(encoding="utf-8"))
        assert reading.map_reading == "read"


@pytest.mark.xfail(reason=CORE, strict=True)
def test_each_answer_scores_perfect_against_itself():
    assert map_dirs()
    for path, _manifest, truth in loaded_maps():
        score = score_trace(truth, truth)
        assert score.after.imperfect_families() == (), path.name
        assert score.after_whole.imperfect_families() == (), path.name


@pytest.mark.xfail(reason=CORE, strict=True)
def test_single_changes_touch_only_their_families():
    assert map_dirs()
    seen: set[str] = set()
    for path, manifest, truth in loaded_maps():
        for site in manifest.damage:
            seen.add(site.change)
            damaged = apply_damage(truth, site)
            score = score_trace(damaged, truth)
            assert score.after.imperfect_families() == TOUCHED_FAMILIES[site.change], (path.name, site.change)
    assert seen == set(CHANGES)


def marks(truth: GeometryTruth) -> Iterable[tuple[Panel, str, str]]:
    for panel in truth.panels:
        for mark in panel.marks:
            x, y = parse_cell(mark.cell)
            yield panel, mark.text, panel.cells[y][x]


def letters(truth: GeometryTruth, letter: str) -> bool:
    return any(
        letter in row
        for panel in truth.panels
        for grid in (panel.edges.vertical, panel.edges.horizontal)
        for row in grid
    )


def symbols(truth: GeometryTruth, kind: str) -> list[tuple[Panel, Symbol]]:
    return [(panel, symbol) for panel in truth.panels for symbol in panel.symbols if symbol.kind == kind]


def refused(truth: GeometryTruth, style: str) -> bool:
    return any(region.verdict == "refused" and region.style == style for region in truth.regions)


def cross_level_passages(truth: GeometryTruth) -> bool:
    return any(
        passage.to_level != level.number
        for dungeon in truth.dungeons
        for level in dungeon.levels
        for passage in level.passages
    )


def same_level_transitions(truth: GeometryTruth, kind: str) -> bool:
    return any(
        transition.to_dungeon is None and transition.to_level == level.number and transition.kind == kind
        for dungeon in truth.dungeons
        for level in dungeon.levels
        for transition in level.transitions
    )


def multi_panel_level(truth: GeometryTruth) -> bool:
    return any(len(level.panels) > 1 for dungeon in truth.dungeons for level in dungeon.levels)


def regions_of_pages(truth: GeometryTruth) -> dict[int, list[Region]]:
    pages: dict[int, list[Region]] = {}
    for region in truth.regions:
        pages.setdefault(region.page, []).append(region)
    return pages


def levels_on_page(truth: GeometryTruth) -> bool:
    for regions in regions_of_pages(truth).values():
        ids = {region.id for region in regions}
        levels = {
            (outline.dungeon, outline.level)
            for panel in truth.panels
            if any(part.region in ids for part in panel.regions)
            for outline in panel.outlines
        }
        if len(levels) > 1:
            return True
    return False


def two_scales_on_page(truth: GeometryTruth) -> bool:
    return any(len({region.scale.feet for region in regions}) > 1 for regions in regions_of_pages(truth).values())


PREDICATES: dict[str, Callable[[GeometryTruth], bool]] = {
    "keys_on_water": lambda t: any(letter == "~" for _panel, _text, letter in marks(t)),
    "key_off_floor": lambda t: any(letter == "#" for _panel, _text, letter in marks(t)),
    "key_printed_twice": lambda t: any(
        sum(1 for other in panel.marks if other.text == mark.text) > 1 for panel in t.panels for mark in panel.marks
    ),
    "key_with_leader_line": lambda t: any(mark.leader_to is not None for panel in t.panels for mark in panel.marks),
    "letter_alone": lambda t: any(len(text) == 1 and text.isalpha() for _p, text, _l in marks(t)),
    "letter_suffix": lambda t: any(text[:-1].isdigit() and text[-1].isalpha() for _p, text, _l in marks(t)),
    "windows": lambda t: letters(t, "n"),
    "dash_dot_partition": lambda t: letters(t, "p"),
    "dashed_circle_in_room": lambda t: letters(t, "p"),
    "pillars": lambda t: bool(symbols(t, "pillar")),
    "steps_inside_level": lambda t: bool(symbols(t, "steps")),
    "moated_house_bridge": lambda t: bool(symbols(t, "bridge")),
    "stairs_with_direction": lambda t: any(s.direction is not None for _p, s in symbols(t, "stairs")),
    "stairs_without_direction": lambda t: any(s.direction is None for _p, s in symbols(t, "stairs")),
    "two_staircases_one_link": lambda t: any(
        len(
            [
                x
                for x in level.transitions
                if x.kind == y.kind and x.to_level == y.to_level and x.to_dungeon == y.to_dungeon
            ]
        )
        > 1
        for d in t.dungeons
        for level in d.levels
        for y in level.transitions
    ),
    "key_outside_open_cave_mouth": lambda t: bool(symbols(t, "cave_mouth")),
    "key_outside_walled_cave_mouth": lambda t: bool(symbols(t, "cave_mouth")),
    "level_with_two_cave_mouths": lambda t: any(
        len([s for s in panel.symbols if s.kind == "cave_mouth"]) >= 2 for panel in t.panels
    ),
    "corridor_off_frame": lambda t: any(
        panel.edges.at((x, y), side) == "o"
        for panel in t.panels
        for y in range(panel.height)
        for x in range(panel.width)
        for side in ("north", "south", "east", "west")
        if panel.cells[y][x] == "."
        and (
            (side == "west" and x == 0)
            or (side == "east" and x == panel.width - 1)
            or (side == "north" and y == 0)
            or (side == "south" and y == panel.height - 1)
        )
    ),
    "walled_courtyard": lambda t: any(outline.open_ground for panel in t.panels for outline in panel.outlines),
    "farmstead_open_ground": lambda t: any(outline.open_ground for panel in t.panels for outline in panel.outlines),
    "five_foot_rooms_two_and_three_wide": lambda t: any(panel.scale_feet == 5 for panel in t.panels),
    "rotated_page": lambda t: any(region.compass.north != "top" for region in t.regions),
    "no_compass": lambda t: any(not region.compass.printed for region in t.regions),
    "legend_on_another_page": lambda t: any(region.legend.from_page is not None for region in t.regions),
    "image_hidden_under_shown": lambda t: any(image.hidden for page in t.pages for image in page.images),
    "page_spread": lambda t: any(page.spread_with is not None for page in t.pages),
    "page_duplicate_image": lambda t: (
        len([image.file for page in t.pages for image in page.images])
        > len({image.file for page in t.pages for image in page.images})
    ),
    "page_blanked": lambda t: any(page.blanked for page in t.pages),
    "reject_no_grid": lambda t: any(
        region.verdict == "unregistered" and region.style == "no_grid" for region in t.regions
    ),
    "reject_warped": lambda t: any(
        region.verdict == "unregistered" and region.style != "no_grid" for region in t.regions
    ),
    "refuse_hex": lambda t: refused(t, "hex"),
    "refuse_isometric": lambda t: refused(t, "isometric"),
    "refuse_side_view": lambda t: refused(t, "side_view"),
    "refuse_outdoor": lambda t: refused(t, "outdoor"),
    "refuse_town": lambda t: refused(t, "town"),
    "refuse_quarter_mile": lambda t: any(
        region.verdict == "refused" and region.scale.printed and region.scale.feet is None for region in t.regions
    ),
    "several_levels_one_page": levels_on_page,
    "halves_joined_at_offset": lambda t: any(pair.joined for pair in t.half_pairs),
    "halves_apart_unequal_stretches": lambda t: any(not pair.joined for pair in t.half_pairs),
    "halves_apart_door_ends_stretch": lambda t: any(not pair.joined for pair in t.half_pairs),
    "halves_apart_stretch_without_partner": lambda t: any(not pair.joined for pair in t.half_pairs),
    "halves_legend_compass_one_half": lambda t: any(
        t.region(pair.regions[0]).legend.printed != t.region(pair.regions[1]).legend.printed for pair in t.half_pairs
    ),
    "panels_two_scales_one_page": two_scales_on_page,
    "panels_two_pages_ladder": lambda t: (
        multi_panel_level(t) and (same_level_transitions(t, "stairs_up") or same_level_transitions(t, "stairs_down"))
    ),
    "panels_trapdoor": lambda t: same_level_transitions(t, "trapdoor"),
    "panels_slide": lambda t: same_level_transitions(t, "chute"),
    "inset": multi_panel_level,
    "two_levels_joined_by_door": cross_level_passages,
    "two_levels_joined_by_open_floor": cross_level_passages,
    "levels_meet_at_door": cross_level_passages,
    "levels_meet_at_locked_secret_door": cross_level_passages,
    "door_no_vestibule_fits": cross_level_passages,
    "corridor_bends_across_cut": cross_level_passages,
    "passage_at_floor_edge": cross_level_passages,
}
"""What a map's answer must show for each feature it declares, where the answer can show it."""


@pytest.mark.parametrize(
    "feature",
    [pytest.param(name, marks=pytest.mark.xfail(reason=FEATURE_CHUNK[name], strict=True)) for name in FEATURES],
)
def test_feature_coverage(feature: str):
    holders = [(path, truth) for path, manifest, truth in loaded_maps() if feature in manifest.features]
    assert holders, f"no synthetic map declares {feature}"
    predicate = PREDICATES.get(feature)
    if predicate is not None:
        assert any(predicate(truth) for _path, truth in holders), f"no map that declares {feature} shows it"


# The ink test. A window straddles each edge's midpoint: `across` cells across the edge, 0.6 along it. A pixel is ink
# when it's nearer the image's ink colour than its paper colour. The fraction of ink in the window must reach
# `blocked` on a wall or window, `door` on a door of any kind, and stay under `open` on an open edge, in at least
# `share` of each picture's edges of that kind. Edges on the frame, edges beside water, partly-floor cells, marks,
# or symbols, and printed open lines are left out.
INK_RULES: dict[str, dict[str, float]] = {
    "tsr_blue": {"across": 0.5, "blocked": 0.18, "door": 0.18, "open": 0.18, "share": 0.97},
    "black_white": {"across": 0.5, "blocked": 0.18, "door": 0.18, "open": 0.18, "share": 0.97},
    "cave": {"across": 1.0, "blocked": 0.10, "door": 0.10, "open": 0.18, "share": 0.90},
    "dark_rock": {"across": 0.5, "blocked": 0.30, "door": 0.05, "open": 0.05, "share": 0.97},
}
ALONG = 0.6
SAMPLES = 9


def ink_mask(image: Image.Image, ink: tuple[int, int, int], paper: tuple[int, int, int]) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB")).astype(np.float64)
    to_ink = np.linalg.norm(rgb - np.array(ink, dtype=np.float64), axis=2)
    to_paper = np.linalg.norm(rgb - np.array(paper, dtype=np.float64), axis=2)
    return to_ink < to_paper


def window_fraction(mask: np.ndarray, region: Region, cell: tuple[int, int], side: str, across: float) -> float:
    registration = region.registration
    assert registration is not None
    x, y = cell
    if side == "east":
        centre, along_axis = (x + 1.0, y + 0.5), "y"
    else:
        centre, along_axis = (x + 0.5, y + 1.0), "x"
    hits = 0
    total = 0
    for i in range(SAMPLES):
        for j in range(SAMPLES):
            a = (i / (SAMPLES - 1) - 0.5) * ALONG
            b = (j / (SAMPLES - 1) - 0.5) * across
            point = (centre[0] + a, centre[1] + b) if along_axis == "x" else (centre[0] + b, centre[1] + a)
            px, py = registration.pixel(point)
            column, row = round(px), round(py)
            if 0 <= row < mask.shape[0] and 0 <= column < mask.shape[1]:
                total += 1
                hits += int(mask[row, column])
    return hits / total if total else 0.0


def region_edges(truth: GeometryTruth, region: Region) -> Iterable[tuple[tuple[int, int], str, str]]:
    """Each interior edge of the region's part of its panel: region cell, side (east or south), and letter."""
    for panel in truth.panels:
        for part in panel.regions:
            if part.region != region.id:
                continue
            excluded = set(panel.partial) | {cell for mark in panel.marks for cell in (mark.cell,)}
            excluded |= {cell for symbol in panel.symbols for cell in symbol.cells}
            ox, oy = part.origin
            for ry in range(region.rows):
                for rx in range(region.columns):
                    px, py = rx + ox, ry + oy
                    for side, (nx, ny) in (("east", (rx + 1, ry)), ("south", (rx, ry + 1))):
                        if nx >= region.columns or ny >= region.rows:
                            continue
                        here, there = panel.cells[py][px], panel.cells[ny + oy][nx + ox]
                        if "~" in (here, there) or f"{px},{py}" in excluded or f"{nx + ox},{ny + oy}" in excluded:
                            continue
                        yield (rx, ry), side, panel.edges.at((px, py), side)


@pytest.mark.xfail(reason=CORE, strict=True)
def test_pictures_show_ink_on_walls_and_doors_and_blank_paper_across_open_edges():
    assert map_dirs()
    for path, manifest, truth in loaded_maps():
        colors = {image.file: (image.ink, image.paper) for image in manifest.images}
        for region in truth.regions:
            if region.verdict != "registered":
                continue
            rules = INK_RULES[region.style]
            ink, paper = colors[region.image]
            with Image.open(path / "images" / region.image) as image:
                mask = ink_mask(image, ink, paper)
            outcomes: dict[str, list[bool]] = {"blocked": [], "door": [], "open": []}
            for cell, side, letter in region_edges(truth, region):
                fraction = window_fraction(mask, region, cell, side, rules["across"])
                if letter in "wn":
                    outcomes["blocked"].append(fraction >= rules["blocked"])
                elif letter in "dls":
                    outcomes["door"].append(fraction >= rules["door"])
                elif letter == "o":
                    outcomes["open"].append(fraction <= rules["open"])
            for kind, results in outcomes.items():
                if results:
                    share = sum(results) / len(results)
                    assert share >= rules["share"], (path.name, region.id, kind, share)


def test_feature_table_is_complete():
    assert set(FEATURE_CHUNK) == set(FEATURES)
    assert set(PREDICATES) <= set(FEATURES)
    assert set(TOUCHED_FAMILIES) == set(CHANGES)
