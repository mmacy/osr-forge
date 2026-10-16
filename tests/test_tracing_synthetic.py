"""The synthetic maps: the committed set, its regeneration, its caches, its answers, its features, and its pictures.

The set lives under `tests/assets/synthetic-maps/`. Each test here reads the
committed set, so until the generator chunks land there is nothing to read
and every test fails for that reason.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from osrforge.contracts.stages import LevelContent, MapReading, SurveyIndex
from osrforge.tracing.score import score_trace
from osrforge.tracing.synthetic import CHANGES, FEATURES, SyntheticManifest, generate
from osrforge.tracing.truth import (
    EdgeGrids,
    GeometryTruth,
    Level,
    Panel,
    Region,
    Symbol,
    cell_key,
    parse_cell,
    rotate_cell,
    rotate_side,
)
from tracing_support import TOUCHED_FAMILIES, apply_damage

SYNTHETIC = Path(__file__).parent / "assets" / "synthetic-maps"
SIZE_LIMIT = 8 * 1024 * 1024
JPEG_MAX_DIFFERENCE = 6
JPEG_MEAN_DIFFERENCE = 0.5
STYLES = ("tsr_blue", "black_white", "dark_rock", "cave")

CORE = "chunk: synth-core"
STYLES_CHUNK = "chunk: synth-styles"
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
        STYLES_CHUNK,
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


@dataclass(frozen=True)
class SyntheticMap:
    """One committed map: its directory, manifest, answer, and content caches by (dungeon id, level number)."""

    path: Path
    manifest: SyntheticManifest
    truth: GeometryTruth
    contents: dict[tuple[str, int], LevelContent]


def map_dirs() -> list[Path]:
    if not SYNTHETIC.is_dir():
        return []
    return sorted(path for path in SYNTHETIC.iterdir() if path.is_dir())


def load_map(path: Path) -> SyntheticMap:
    manifest = SyntheticManifest.model_validate_json((path / "synthetic.json").read_text(encoding="utf-8"))
    truth = GeometryTruth.model_validate_json((path / "geometry.json").read_text(encoding="utf-8"))
    contents: dict[tuple[str, int], LevelContent] = {}
    for dungeon in truth.dungeons:
        for level in dungeon.levels:
            cache = path / "stages" / f"areas.{dungeon.id}.{level.number}.json"
            if cache.is_file():
                contents[dungeon.id, level.number] = LevelContent.model_validate_json(cache.read_text(encoding="utf-8"))
    return SyntheticMap(path, manifest, truth, contents)


def loaded_maps() -> list[SyntheticMap]:
    return [load_map(path) for path in map_dirs()]


@pytest.mark.xfail(reason=CORE, strict=True)
def test_set_is_committed():
    assert FEATURE_CHUNK.keys() == set(FEATURES)
    assert map_dirs(), "no synthetic maps are committed"
    for item in loaded_maps():
        assert item.manifest.name == item.path.name
        assert (item.path / "stages" / "survey.json").is_file()
        assert (item.path / "stages" / "mapread.json").is_file()
        for image in item.manifest.images:
            assert (item.path / "images" / image.file).is_file()
        for region in item.truth.regions:
            assert any(image.file == region.image for image in item.manifest.images)


def pixels(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).astype(np.int16)


@pytest.mark.xfail(reason=CORE, strict=True)
def test_regeneration_matches_the_committed_set(tmp_path: Path):
    """A PNG matches by its decoded pixels exactly, a JPEG within the tolerance, and every other file byte for byte."""
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
    """Every level, refused and fallen-back levels included, has a content cache, and the map-reading knob is read."""
    assert map_dirs()
    for item in loaded_maps():
        survey = SurveyIndex.model_validate_json((item.path / "stages" / "survey.json").read_text(encoding="utf-8"))
        assert survey.title == item.manifest.title
        assert [dungeon.id for dungeon in survey.dungeons] == [dungeon.id for dungeon in item.truth.dungeons]
        for dungeon, surveyed in zip(item.truth.dungeons, survey.dungeons, strict=True):
            assert [level.number for level in surveyed.levels] == [level.number for level in dungeon.levels]
            for level, surveyed_level in zip(dungeon.levels, surveyed.levels, strict=True):
                content = item.contents[dungeon.id, level.number]
                assert content.dungeon_id == dungeon.id
                assert content.level_number == level.number
                surveyed_keys = {area.key for area in surveyed_level.areas}
                assert {area.key for area in content.areas} == surveyed_keys
                if level.verdict == "traced":
                    assert set(level.areas) <= surveyed_keys
        reading = MapReading.model_validate_json((item.path / "stages" / "mapread.json").read_text(encoding="utf-8"))
        assert reading.map_reading == "read"


@pytest.mark.xfail(reason=CORE, strict=True)
def test_each_answer_scores_perfect_against_itself():
    assert map_dirs()
    for item in loaded_maps():
        score = score_trace(item.truth, item.truth)
        assert score.after.imperfect_families() == (), item.path.name
        assert score.after_whole.imperfect_families() == (), item.path.name


def reachable_areas(truth: GeometryTruth, dungeon_id: str) -> set[tuple[int, str]]:
    """The areas a walk from the dungeon's entrance reaches across passable edges, doors, transitions, and passages."""
    dungeon = next(candidate for candidate in truth.dungeons if candidate.id == dungeon_id)
    assert dungeon.entrance is not None
    levels = {level.number: level for level in dungeon.levels if level.verdict == "traced"}
    start = (dungeon.entrance.level, parse_cell(dungeon.entrance.cell))
    seen = {start}
    queue = [start]
    while queue:
        number, cell = queue.pop()
        level = levels[number]
        assert level.edges is not None
        for side, step in (("north", (0, -1)), ("east", (1, 0)), ("south", (0, 1)), ("west", (-1, 0))):
            if level.edges.at(cell, side) in "opdls":
                neighbour = (number, (cell[0] + step[0], cell[1] + step[1]))
                if neighbour not in seen and level.cells[neighbour[1][1]][neighbour[1][0]] == ".":
                    seen.add(neighbour)
                    queue.append(neighbour)
        for transition in level.transitions:
            if transition.cell == cell_key(cell) and transition.to_dungeon is None and transition.to_level in levels:
                target = (transition.to_level, parse_cell(transition.to_cell))
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        for passage in level.passages:
            if passage.cell == cell_key(cell):
                target = (passage.to_level, parse_cell(passage.to_cell))
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
    areas: set[tuple[int, str]] = set()
    for number, level in levels.items():
        for key, area in level.areas.items():
            if any((number, parse_cell(cell)) in seen for cell in area.cells):
                areas.add((number, key))
    return areas


@pytest.mark.xfail(reason=CORE, strict=True)
def test_damage_sites_follow_their_constraints():
    """Each site stands where `DamageSite` says: every area reachable, edge sites on unturned 10-foot panels, and a
    flipped level neither the entrance level nor one a passage leads to."""
    assert map_dirs()
    for item in loaded_maps():
        truth = item.truth
        for dungeon in truth.dungeons:
            if dungeon.entrance is not None:
                expected = {
                    (level.number, key) for level in dungeon.levels if level.verdict == "traced" for key in level.areas
                }
                assert reachable_areas(truth, dungeon.id) == expected, (item.path.name, dungeon.id)
        for site in item.manifest.damage:
            if site.panel is not None:
                panel = truth.panel(site.panel)
                assert panel.scale_feet == 10, (item.path.name, site)
                for dungeon in truth.dungeons:
                    for level in dungeon.levels:
                        for part in level.panels:
                            if part.panel == site.panel:
                                assert part.rotation == 0, (item.path.name, site)
            if site.change == "refusal_flipped":
                assert site.dungeon is not None and site.level is not None
                dungeon = next(candidate for candidate in truth.dungeons if candidate.id == site.dungeon)
                assert dungeon.entrance is not None and dungeon.entrance.level != site.level, (item.path.name, site)
                for level in dungeon.levels:
                    for passage in level.passages:
                        assert passage.to_level != site.level, (item.path.name, site)


@pytest.mark.xfail(reason=CORE, strict=True)
def test_single_changes_touch_only_their_families():
    assert map_dirs()
    seen: set[str] = set()
    for item in loaded_maps():
        for site in item.manifest.damage:
            seen.add(site.change)
            damaged = apply_damage(item.truth, site)
            score = score_trace(damaged, item.truth)
            assert score.after.imperfect_families() == TOUCHED_FAMILIES[site.change], (item.path.name, site.change)
    assert seen == set(CHANGES)


# Helpers for the feature predicates.


def levels_of(truth: GeometryTruth) -> Iterable[tuple[str, Level]]:
    for dungeon in truth.dungeons:
        for level in dungeon.levels:
            if level.verdict == "traced":
                yield dungeon.id, level


def panel_levels(
    truth: GeometryTruth, panel: Panel
) -> Iterable[tuple[Level, Callable[[tuple[int, int]], tuple[int, int]], int]]:
    """Each traced level that uses a panel, with the map from a panel cell to a level cell and the panel's turn."""
    for _dungeon_id, level in levels_of(truth):
        for part in level.panels:
            if part.panel == panel.id:
                origin, rotation = part.origin, part.rotation

                def to_level(
                    cell: tuple[int, int], origin: tuple[int, int] = origin, rotation: int = rotation
                ) -> tuple[int, int]:
                    turned = rotate_cell(cell, (panel.width, panel.height), rotation)
                    return (turned[0] + origin[0], turned[1] + origin[1])

                yield level, to_level, rotation


def area_of(level: Level, cell: tuple[int, int]) -> str | None:
    key = cell_key(cell)
    return next((name for name, area in level.areas.items() if key in area.cells), None)


def panel_edges(panel: Panel) -> Iterable[tuple[tuple[int, int], str, str]]:
    for cell, side in panel.edges.positions():
        yield cell, side, panel.edges.at(cell, side)


def neighbour(cell: tuple[int, int], side: str) -> tuple[int, int]:
    x, y = cell
    return {"north": (x, y - 1), "south": (x, y + 1), "west": (x - 1, y), "east": (x + 1, y)}[side]


def in_panel(panel: Panel, cell: tuple[int, int]) -> bool:
    return 0 <= cell[0] < panel.width and 0 <= cell[1] < panel.height


def inside_outline(panel: Panel, cell: tuple[int, int]) -> bool:
    return any(
        x <= cell[0] < x + w and y <= cell[1] < y + h for outline in panel.outlines for x, y, w, h in outline.rects
    )


def marks(truth: GeometryTruth) -> Iterable[tuple[Panel, str, str, tuple[int, int]]]:
    for panel in truth.panels:
        for mark in panel.marks:
            cell = parse_cell(mark.cell)
            yield panel, mark.text, panel.cells[cell[1]][cell[0]], cell


def placed_labels(truth: GeometryTruth) -> set[str]:
    return {placed.label for _dungeon_id, level in levels_of(truth) for placed in level.keys}


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


def level_passages(truth: GeometryTruth) -> Iterable[tuple[Level, object]]:
    for _dungeon_id, level in levels_of(truth):
        for passage in level.passages:
            yield level, passage


def cross_level_passages(truth: GeometryTruth) -> bool:
    return any(passage.to_level != level.number for level, passage in level_passages(truth))  # type: ignore[attr-defined]


def passage_sites(truth: GeometryTruth) -> Iterable[tuple[Level, tuple[int, int], str, int]]:
    """Each passage to another level: its level, cell, side, and target level."""
    for _dungeon_id, level in levels_of(truth):
        for passage in level.passages:
            if passage.to_level != level.number:
                yield level, parse_cell(passage.cell), passage.side, passage.to_level


def passage_door_letter(level: Level, cell: tuple[int, int]) -> str:
    """The door letter on a side of a passage cell, or `-` when no side is a door."""
    assert level.edges is not None
    for side in ("north", "east", "south", "west"):
        letter = level.edges.at(cell, side)
        if letter in "dls":
            return letter
    return "-"


def reading_letter_at_passage(truth: GeometryTruth) -> Iterable[str]:
    """For each passage to another level, the panel reading's letter on the passage's side."""
    for level, cell, side, _target in passage_sites(truth):
        for part in level.panels:
            panel = truth.panel(part.panel)
            for candidate_level, to_level, rotation in panel_levels(truth, panel):
                if candidate_level is not level:
                    continue
                for page_cell_y in range(panel.height):
                    for page_cell_x in range(panel.width):
                        page_cell = (page_cell_x, page_cell_y)
                        if to_level(page_cell) == cell:
                            page_side = next(
                                s for s in ("north", "east", "south", "west") if rotate_side(s, rotation) == side
                            )
                            yield panel.edges.at(page_cell, page_side)


def same_level_transitions(truth: GeometryTruth, kind: str) -> bool:
    return any(
        transition.to_dungeon is None and transition.to_level == level.number and transition.kind == kind
        for _dungeon_id, level in levels_of(truth)
        for transition in level.transitions
    )


def multi_panel_level(truth: GeometryTruth) -> list[Level]:
    return [level for _dungeon_id, level in levels_of(truth) if len(level.panels) > 1]


def regions_of_pages(truth: GeometryTruth) -> dict[int, list[Region]]:
    pages: dict[int, list[Region]] = {}
    for region in truth.regions:
        pages.setdefault(region.page, []).append(region)
    return pages


def level_regions(truth: GeometryTruth, level: Level) -> list[Region]:
    return [truth.region(part.region) for lp in level.panels for part in truth.panel(lp.panel).regions]


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
    for level in multi_panel_level(truth):
        regions = level_regions(truth, level)
        if len({region.page for region in regions}) == 1 and len({region.scale.feet for region in regions}) > 1:
            return True
    return False


def two_pages_with_stairs(truth: GeometryTruth) -> bool:
    for level in multi_panel_level(truth):
        if len({region.page for region in level_regions(truth, level)}) > 1 and any(
            transition.to_dungeon is None
            and transition.to_level == level.number
            and transition.kind.startswith("stairs")
            for transition in level.transitions
        ):
            return True
    return False


def inset(truth: GeometryTruth) -> bool:
    for level in multi_panel_level(truth):
        regions = level_regions(truth, level)
        if len({region.page for region in regions}) == 1:
            sizes = sorted(region.columns * region.rows for region in regions)
            if sizes[0] * 4 <= sizes[-1]:
                return True
    return False


def open_components(level: Level) -> list[set[str]]:
    """Groups of areas joined across open edges and printed lines, with no wall or door between them."""
    assert level.edges is not None
    edges = level.edges
    parent: dict[str, str] = {key: key for key in level.areas}

    def find(key: str) -> str:
        while parent[key] != key:
            key = parent[key]
        return key

    for key, area in level.areas.items():
        for cell_text in area.cells:
            cell = parse_cell(cell_text)
            for side in ("east", "south"):
                if edges.at(cell, side) in "op":
                    other = area_of(level, neighbour(cell, side))
                    if other is not None and other != key:
                        parent[find(key)] = find(other)
    groups: dict[str, set[str]] = {}
    for key in level.areas:
        groups.setdefault(find(key), set()).add(key)
    return list(groups.values())


def door_only_parts(level: Level, key: str) -> int:
    """How many parts an area's cells fall into when its cells join only across non-wall, non-door edges."""
    assert level.edges is not None
    cells = {parse_cell(text) for text in level.areas[key].cells}
    seen: set[tuple[int, int]] = set()
    parts = 0
    for start in sorted(cells):
        if start in seen:
            continue
        parts += 1
        queue = [start]
        seen.add(start)
        while queue:
            cell = queue.pop()
            for side in ("north", "east", "south", "west"):
                step = neighbour(cell, side)
                if step in cells and step not in seen and level.edges.at(cell, side) in "op":
                    seen.add(step)
                    queue.append(step)
    return parts


def synthesized_door(truth: GeometryTruth) -> bool:
    """An edge read as a wall on a panel that is a door on the level."""
    for panel in truth.panels:
        for level, to_level, rotation in panel_levels(truth, panel):
            assert level.edges is not None
            for cell, side, letter in panel_edges(panel):
                if letter == "w" and level.edges.at(to_level(cell), rotate_side(side, rotation)) == "d":
                    return True
    return False


def cave_mouth_key(truth: GeometryTruth, mouth_letters: str) -> bool:
    """A mark outside every outline within two cells of a cave mouth whose edge has one of the letters."""
    for panel, _text, _letter, cell in marks(truth):
        if inside_outline(panel, cell):
            continue
        for symbol in panel.symbols:
            if symbol.kind != "cave_mouth" or symbol.edge is None:
                continue
            mouth_cell, side = symbol.edge.split(":")
            mouth = parse_cell(mouth_cell)
            if (
                max(abs(mouth[0] - cell[0]), abs(mouth[1] - cell[1])) <= 2
                and panel.edges.at(mouth, side) in mouth_letters
            ):
                return True
    return False


def frame_opening(truth: GeometryTruth) -> bool:
    for panel in truth.panels:
        for y in range(panel.height):
            for x in range(panel.width):
                if panel.cells[y][x] != ".":
                    continue
                sides = []
                if x == 0:
                    sides.append("west")
                if x == panel.width - 1:
                    sides.append("east")
                if y == 0:
                    sides.append("north")
                if y == panel.height - 1:
                    sides.append("south")
                if any(panel.edges.at((x, y), side) == "o" for side in sides):
                    return True
    return False


def courtyard(truth: GeometryTruth) -> bool:
    """An open-ground rectangle whose cells are corridor and whose boundary is walls and doors."""
    for panel in truth.panels:
        for outline in panel.outlines:
            for x, y, w, h in outline.open_ground:
                cells = [(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)]
                for level, to_level, _rotation in panel_levels(truth, panel):
                    if any(area_of(level, to_level(cell)) is not None for cell in cells):
                        continue
                    bounded = all(
                        panel.edges.at(cell, side) in "wndls"
                        for cell in cells
                        for side in ("north", "east", "south", "west")
                        if neighbour(cell, side) not in cells
                    )
                    if bounded:
                        return True
    return False


def farmstead(truth: GeometryTruth) -> bool:
    """An open-ground rectangle with cells of at least two areas inside it."""
    for panel in truth.panels:
        for outline in panel.outlines:
            for x, y, w, h in outline.open_ground:
                cells = [(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)]
                for level, to_level, _rotation in panel_levels(truth, panel):
                    areas = {area_of(level, to_level(cell)) for cell in cells} - {None}
                    if len(areas) >= 2:
                        return True
    return False


def partition_between_areas(truth: GeometryTruth, same_area: bool) -> bool:
    """A printed open line between two cells of one area, or of two different areas."""
    for panel in truth.panels:
        for level, to_level, _rotation in panel_levels(truth, panel):
            for cell, side, letter in panel_edges(panel):
                other = neighbour(cell, side)
                if letter != "p" or not in_panel(panel, other):
                    continue
                here, there = area_of(level, to_level(cell)), area_of(level, to_level(other))
                if here is not None and there is not None and (here == there) == same_area:
                    return True
    return False


def apart_pairs(truth: GeometryTruth) -> list[tuple[str, str]]:
    return [pair.regions for pair in truth.half_pairs if not pair.joined]


def fold_cells(truth: GeometryTruth, region_id: str) -> list[tuple[Panel, tuple[int, int]]]:
    """A half's floor cells on the column at its fold edge."""
    region = truth.region(region_id)
    panel = next(panel for panel in truth.panels if any(part.region == region_id for part in panel.regions))
    column = panel.width - 1 if "right" in region.cut_off else 0
    return [(panel, (column, y)) for y in range(panel.height) if panel.cells[y][column] == "."]


def passage_cells(truth: GeometryTruth, panel: Panel) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for level, to_level, _rotation in panel_levels(truth, panel):
        level_cells = {parse_cell(passage.cell) for passage in level.passages}
        for y in range(panel.height):
            for x in range(panel.width):
                if to_level((x, y)) in level_cells:
                    cells.add((x, y))
    return cells


def apart_passage_count(truth: GeometryTruth) -> int:
    return sum(
        len(passage_cells(truth, fold_cells(truth, region_id)[0][0]))
        for first, _second in apart_pairs(truth)
        for region_id in (first,)
        if fold_cells(truth, region_id)
    )


def door_ends_stretch(truth: GeometryTruth) -> bool:
    for first, second in apart_pairs(truth):
        for region_id in (first, second):
            cells = fold_cells(truth, region_id)
            if not cells:
                continue
            panel = cells[0][0]
            linked = passage_cells(truth, panel)
            for _panel, cell in cells:
                if cell in linked and any(
                    panel.edges.at(cell, side) in "dls" and neighbour(cell, side) in {c for _p, c in cells}
                    for side in ("north", "south")
                ):
                    return True
    return False


def stretch_without_partner(truth: GeometryTruth) -> bool:
    for first, second in apart_pairs(truth):
        for region_id in (first, second):
            cells = fold_cells(truth, region_id)
            if not cells:
                continue
            panel = cells[0][0]
            linked = passage_cells(truth, panel)
            if linked and any(cell not in linked for _panel, cell in cells):
                return True
    return False


def passage_on_floor_edge(truth: GeometryTruth) -> bool:
    for level, cell, _side, _target in passage_sites(truth):
        floor = [(x, y) for y, row in enumerate(level.cells) for x, letter in enumerate(row) if letter == "."]
        xs, ys = [x for x, _y in floor], [y for _x, y in floor]
        if cell[0] in (min(xs), max(xs)) or cell[1] in (min(ys), max(ys)):
            return True
    return False


def corridor_passage(truth: GeometryTruth) -> bool:
    for level, cell, _side, target in passage_sites(truth):
        if area_of(level, cell) is not None:
            continue
        for passage in level.passages:
            if parse_cell(passage.cell) == cell and passage.to_level == target:
                target_level = next(candidate for _d, candidate in levels_of(truth) if candidate.number == target)
                if area_of(target_level, parse_cell(passage.to_cell)) is None:
                    return True
    return False


def unprinted_entrance_key(truth: GeometryTruth) -> bool:
    printed = {text for _panel, text, _letter, _cell in marks(truth)}
    for dungeon in truth.dungeons:
        if dungeon.entrance is None:
            continue
        level = truth.level(dungeon.id, dungeon.entrance.level)
        key = area_of(level, parse_cell(dungeon.entrance.cell))
        if (
            key is not None
            and key not in printed
            and any(symbol.kind == "cave_mouth" for part in level.panels for symbol in truth.panel(part.panel).symbols)
        ):
            return True
    return False


def letter_named_by_two_keys(item: SyntheticMap) -> bool:
    for _panel, text, _letter, _cell in marks(item.truth):
        if not (len(text) == 1 and text.isalpha()):
            continue
        namers = sum(
            1
            for content in item.contents.values()
            for area in content.areas
            if f'"{text.lower()}"' in area.description.lower()
        )
        if namers >= 2:
            return True
    return False


PREDICATES: dict[str, Callable[[SyntheticMap], bool]] = {
    "keys_on_water": lambda m: any(letter == "~" for _p, _t, letter, _c in marks(m.truth)),
    "key_off_floor": lambda m: any(letter == "#" for _p, _t, letter, _c in marks(m.truth)),
    "key_at_rear_door": lambda m: any(mark.edge is not None for panel in m.truth.panels for mark in panel.marks),
    "key_printed_twice": lambda m: any(
        sum(1 for other in panel.marks if other.text == mark.text) > 1
        for panel in m.truth.panels
        for mark in panel.marks
    ),
    "keys_stacked": lambda m: any("/" in text for _p, text, _l, _c in marks(m.truth)),
    "key_with_leader_line": lambda m: any(
        mark.leader_to is not None for panel in m.truth.panels for mark in panel.marks
    ),
    "letter_alone": lambda m: any(len(text) == 1 and text.isalpha() for _p, text, _l, _c in marks(m.truth)),
    "letter_suffix": lambda m: any(text[:-1].isdigit() and text[-1].isalpha() for _p, text, _l, _c in marks(m.truth)),
    "letter_lair_label_outside": lambda m: any(
        len(text) == 1 and text.isalpha() and not inside_outline(panel, cell) and text not in placed_labels(m.truth)
        for panel, text, _l, cell in marks(m.truth)
    ),
    "letter_under_two_keys": letter_named_by_two_keys,
    "windows": lambda m: letters(m.truth, "n"),
    "dash_dot_partition": lambda m: partition_between_areas(m.truth, same_area=False),
    "dashed_circle_in_room": lambda m: partition_between_areas(m.truth, same_area=True),
    "pillars": lambda m: bool(symbols(m.truth, "pillar")),
    "steps_inside_level": lambda m: bool(symbols(m.truth, "steps")),
    "moated_house_bridge": lambda m: any(
        all(panel.cells[parse_cell(c)[1]][parse_cell(c)[0]] == "~" for c in symbol.cells)
        for panel, symbol in symbols(m.truth, "bridge")
    ),
    "stairs_with_direction": lambda m: any(s.direction is not None for _p, s in symbols(m.truth, "stairs")),
    "stairs_without_direction": lambda m: any(s.direction is None for _p, s in symbols(m.truth, "stairs")),
    "two_staircases_one_link": lambda m: any(
        sum(
            1
            for x in level.transitions
            if x.kind == y.kind and x.to_level == y.to_level and x.to_dungeon == y.to_dungeon
        )
        > 1
        for _d, level in levels_of(m.truth)
        for y in level.transitions
    ),
    "key_outside_open_cave_mouth": lambda m: cave_mouth_key(m.truth, "op"),
    "key_outside_walled_cave_mouth": lambda m: cave_mouth_key(m.truth, "wndls"),
    "level_with_two_cave_mouths": lambda m: any(
        len([s for s in panel.symbols if s.kind == "cave_mouth"]) >= 2 for panel in m.truth.panels
    ),
    "corridor_off_frame": lambda m: frame_opening(m.truth),
    "walled_courtyard": lambda m: courtyard(m.truth),
    "farmstead_open_ground": lambda m: farmstead(m.truth),
    "tree_house_no_door": lambda m: synthesized_door(m.truth),
    "labelled_one_cell_hall": lambda m: any(
        len(area.cells) == 1 and any(placed.area == key for placed in level.keys)
        for _d, level in levels_of(m.truth)
        for key, area in level.areas.items()
    ),
    "open_hall_several_keys": lambda m: any(
        len(group) >= 3 for _d, level in levels_of(m.truth) for group in open_components(level)
    ),
    "jail_cells_row_one_key": lambda m: any(
        door_only_parts(level, key) >= 3 and sum(1 for placed in level.keys if placed.area == key) == 1
        for _d, level in levels_of(m.truth)
        for key in level.areas
    ),
    "entrance_chamber_in_cave_mouth_unprinted_key": lambda m: unprinted_entrance_key(m.truth),
    "five_foot_rooms_two_and_three_wide": lambda m: any(panel.scale_feet == 5 for panel in m.truth.panels),
    "rotated_page": lambda m: (
        any(region.compass.north != "top" for region in m.truth.regions)
        and any(part.rotation != 0 for _d, level in levels_of(m.truth) for part in level.panels)
    ),
    "no_compass": lambda m: any(not region.compass.printed for region in m.truth.regions),
    "legend_on_another_page": lambda m: any(region.legend.from_page is not None for region in m.truth.regions),
    "image_hidden_under_shown": lambda m: any(image.hidden for page in m.truth.pages for image in page.images),
    "page_spread": lambda m: any(page.spread_with is not None for page in m.truth.pages),
    "page_duplicate_image": lambda m: (
        len([image.file for page in m.truth.pages for image in page.images])
        > len({image.file for page in m.truth.pages for image in page.images})
    ),
    "page_blanked": lambda m: any(page.blanked for page in m.truth.pages),
    "reject_no_grid": lambda m: any(
        region.verdict == "unregistered" and region.style == "no_grid" for region in m.truth.regions
    ),
    "reject_warped": lambda m: any(
        region.verdict == "unregistered" and region.style != "no_grid" for region in m.truth.regions
    ),
    "refuse_hex": lambda m: refused(m.truth, "hex"),
    "refuse_isometric": lambda m: refused(m.truth, "isometric"),
    "refuse_side_view": lambda m: refused(m.truth, "side_view"),
    "refuse_outdoor": lambda m: refused(m.truth, "outdoor"),
    "refuse_town": lambda m: refused(m.truth, "town"),
    "refuse_quarter_mile": lambda m: any(
        region.verdict == "refused" and region.scale.printed and region.scale.feet is None for region in m.truth.regions
    ),
    "several_levels_one_page": lambda m: levels_on_page(m.truth),
    "halves_joined_at_offset": lambda m: any(pair.joined and pair.columns for pair in m.truth.half_pairs),
    "halves_gap_at_fold": lambda m: any(pair.joined for pair in m.truth.half_pairs),
    "halves_apart_unequal_stretches": lambda m: bool(apart_pairs(m.truth)) and apart_passage_count(m.truth) >= 3,
    "halves_apart_door_ends_stretch": lambda m: door_ends_stretch(m.truth),
    "halves_apart_stretch_without_partner": lambda m: stretch_without_partner(m.truth),
    "halves_legend_compass_one_half": lambda m: any(
        m.truth.region(pair.regions[0]).legend.printed != m.truth.region(pair.regions[1]).legend.printed
        and m.truth.region(pair.regions[0]).compass.printed != m.truth.region(pair.regions[1]).compass.printed
        for pair in m.truth.half_pairs
    ),
    "panels_two_scales_one_page": lambda m: two_scales_on_page(m.truth),
    "panels_two_pages_ladder": lambda m: two_pages_with_stairs(m.truth),
    "panels_trapdoor": lambda m: same_level_transitions(m.truth, "trapdoor"),
    "panels_slide": lambda m: same_level_transitions(m.truth, "chute"),
    "inset": lambda m: inset(m.truth),
    "two_levels_joined_by_door": lambda m: any(
        passage_door_letter(level, cell) == "d" for level, cell, _s, _t in passage_sites(m.truth)
    ),
    "two_levels_joined_by_open_floor": lambda m: any(
        passage_door_letter(level, cell) == "-" for level, cell, _s, _t in passage_sites(m.truth)
    ),
    "levels_meet_at_door": lambda m: any(
        passage_door_letter(level, cell) == "d" for level, cell, _s, _t in passage_sites(m.truth)
    ),
    "levels_meet_at_locked_secret_door": lambda m: any(
        passage_door_letter(level, cell) in "ls" for level, cell, _s, _t in passage_sites(m.truth)
    ),
    "door_no_vestibule_fits": lambda m: any(letter in "dls" for letter in reading_letter_at_passage(m.truth)),
    "corridor_bends_across_cut": lambda m: corridor_passage(m.truth),
    "passage_at_floor_edge": lambda m: passage_on_floor_edge(m.truth),
}
"""What a map's answer must show for each feature it declares, where the answer can show it."""


@pytest.mark.parametrize(
    "feature",
    [pytest.param(name, marks=pytest.mark.xfail(reason=FEATURE_CHUNK[name], strict=True)) for name in FEATURES],
)
def test_feature_coverage(feature: str):
    holders = [item for item in loaded_maps() if feature in item.manifest.features]
    assert holders, f"no synthetic map declares {feature}"
    predicate = PREDICATES.get(feature)
    if predicate is not None:
        assert any(predicate(item) for item in holders), f"no map that declares {feature} shows it"


@pytest.mark.xfail(reason=STYLES_CHUNK, strict=True)
def test_set_has_every_style_and_the_halves_share_their_images():
    """Every style appears, and the joined and apart versions of the halves are drawn from the same two images."""
    items = loaded_maps()
    assert items
    styles = {region.style for item in items for region in item.truth.regions if region.verdict == "registered"}
    assert set(STYLES) <= styles
    joined = [(item, pair) for item in items for pair in item.truth.half_pairs if pair.joined]
    apart = [(item, pair) for item in items for pair in item.truth.half_pairs if not pair.joined]
    assert joined and apart
    joined_images = {
        tuple((item.path / "images" / item.truth.region(region_id).image).read_bytes() for region_id in pair.regions)
        for item, pair in joined
    }
    apart_images = {
        tuple((item.path / "images" / item.truth.region(region_id).image).read_bytes() for region_id in pair.regions)
        for item, pair in apart
    }
    assert joined_images & apart_images


@pytest.mark.xfail(reason=CORE, strict=True)
def test_tsr_blue_maps_are_scans():
    """A TSR blue region is a JPEG at about 26 pixels per cell with a pitch that varies across the map and noise on
    its paper: 150 dpi over 10-foot cells of the printed size, JPEG noise, and uneven pitch."""
    items = loaded_maps()
    blue = [
        (item, region)
        for item in items
        for region in item.truth.regions
        if region.style == "tsr_blue" and region.verdict == "registered"
    ]
    assert blue
    for item, region in blue:
        assert region.image.lower().endswith((".jpg", ".jpeg")), (item.path.name, region.id)
        registration = region.registration
        assert registration is not None
        pitches = [
            registration.corners[j][i + 1][0] - registration.corners[j][i][0]
            for j in range(registration.rows + 1)
            for i in range(registration.columns)
        ]
        mean = sum(pitches) / len(pitches)
        assert 20 <= mean <= 32, (item.path.name, region.id, mean)
        assert (max(pitches) - min(pitches)) >= 0.02 * mean, (item.path.name, region.id)
        colors = {image.file: image for image in item.manifest.images}[region.image]
        with Image.open(item.path / "images" / region.image) as image:
            rgb = np.asarray(image.convert("RGB")).astype(np.float64)
        panel = next(panel for panel in item.truth.panels if any(part.region == region.id for part in panel.regions))
        part = next(part for part in panel.regions if part.region == region.id)
        samples: list[float] = []
        for y in range(region.rows):
            for x in range(region.columns):
                if panel.cells[y + part.origin[1]][x + part.origin[0]] != ".":
                    continue
                px, py = registration.pixel((x + 0.5, y + 0.5))
                window = rgb[int(py) - 3 : int(py) + 4, int(px) - 3 : int(px) + 4]
                if window.size:
                    samples.append(float(np.linalg.norm(window - np.array(colors.paper), axis=2).std()))
        assert samples and sum(samples) / len(samples) >= 2.0, (item.path.name, region.id)


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
            excluded = set(panel.partial) | {mark.cell for mark in panel.marks}
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
    for item in loaded_maps():
        colors = {image.file: (image.ink, image.paper) for image in item.manifest.images}
        for region in item.truth.regions:
            if region.verdict != "registered":
                continue
            rules = INK_RULES[region.style]
            ink, paper = colors[region.image]
            with Image.open(item.path / "images" / region.image) as image:
                mask = ink_mask(image, ink, paper)
            outcomes: dict[str, list[bool]] = {"blocked": [], "door": [], "open": []}
            for cell, side, letter in region_edges(item.truth, region):
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
                    assert share >= rules["share"], (item.path.name, region.id, kind, share)


def test_feature_table_is_complete():
    assert set(FEATURE_CHUNK) == set(FEATURES)
    assert set(PREDICATES) == set(FEATURES) - {
        "gateway_no_door_between_towers",
        "wide_open_stairway_only_way_in",
        "overlay_contours_tracks_stream",
    }
    assert set(TOUCHED_FAMILIES) == set(CHANGES)
    assert EdgeGrids(vertical=("ww",), horizontal=("w", "w")).width == 1
