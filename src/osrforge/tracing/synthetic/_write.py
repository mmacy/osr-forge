"""Writing a synthetic map: its images, its answer, its stage caches, and its manifest.

[`build`][osrforge.tracing.synthetic._write.build] turns a map spec into the
files of one map directory, and [`write_maps`][osrforge.tracing.synthetic._write.write_maps]
writes every map the generator knows.
"""

import shutil
import zlib
from pathlib import Path

from osrforge.contracts.stages import (
    AreaConnection,
    AreaContent,
    Direction,
    LevelContent,
    MapDoor,
    MapEdgeProposal,
    MapLevelReading,
    MapReading,
    SurveyArea,
    SurveyDungeon,
    SurveyIndex,
    SurveyLevel,
    TownInfo,
)
from osrforge.tracing.synthetic import SyntheticImage, SyntheticManifest
from osrforge.tracing.synthetic._draw import draw_page, draw_regions
from osrforge.tracing.synthetic._maps import MAPS
from osrforge.tracing.synthetic._resolve import (
    DesignError,
    Resolved,
    frames_of,
    level_model,
    panel_model,
    resolve_dungeon,
)
from osrforge.tracing.synthetic._sheets import draw_sheet
from osrforge.tracing.synthetic._spec import TEN_FEET, MapSpec, RegionSpec
from osrforge.tracing.truth import (
    Dungeon,
    GeometryTruth,
    Legend,
    Level,
    Page,
    PageImage,
    Region,
    Registration,
    save_geometry_truth,
)
from osrforge.workdir import write_json_artifact

_DIRECTIONS: dict[str, Direction] = {"north": "north", "east": "east", "south": "south", "west": "west"}
_OPPOSITE: dict[str, str] = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _connections(level: Resolved) -> dict[str, list[AreaConnection]]:
    found: dict[str, list[AreaConnection]] = {area.key: [] for area in level.spec.areas}
    for (first, second), edges in level.joins().items():
        letters = {letter for _edge, _side, letter in edges}
        via = "door" if letters & set("dl") else "secret_door" if "s" in letters else "passage"
        side = edges[0][1]
        locked = "l" in letters
        found[first].append(AreaConnection(to_key=second, direction=_DIRECTIONS[side], via=via, door_locked=locked))
        found[second].append(
            AreaConnection(to_key=first, direction=_DIRECTIONS[_OPPOSITE[side]], via=via, door_locked=locked)
        )
    order = {area.key: index for index, area in enumerate(level.spec.areas)}
    for key, connections in found.items():
        connections.sort(key=lambda link: order.get(link.to_key or "", 0))
        area = next(area for area in level.spec.areas if area.key == key)
        connections.extend(area.links)
    return found


def _proposals(level: Resolved) -> tuple[MapEdgeProposal, ...]:
    printed = {item.key for item in level.placed}
    proposals: list[MapEdgeProposal] = []
    for (first, second), edges in level.joins().items():
        if first not in printed or second not in printed:
            continue
        letters = {letter for _edge, _side, letter in edges}
        door: MapDoor = "door" if letters & set("dl") else "secret_door" if "s" in letters else "none"
        proposals.append(MapEdgeProposal(a=first, b=second, door=door))
    return tuple(proposals)


def _check_panel_order(spec: MapSpec, registrations: dict[str, Registration]) -> None:
    """Make sure each level lists its panels in page order, the order Panels and the level's grid places them in.

    Panels go by page, and on one page by the top edge of their frames, highest first, then left to right.
    """
    regions = {region.id: region for region in spec.regions}

    def place(panel_id: str) -> tuple[int, float, float]:
        panel = next(panel for panel in spec.panels if panel.id == panel_id)
        first = regions[panel.regions[0][0]]
        corners = registrations[first.id].corners
        return (first.page, min(point[1] for point in corners[0]), min(row[0][0] for row in corners))

    for level in spec.dungeon.levels:
        places = [place(panel) for panel in level.panels]
        if places != sorted(places):
            raise DesignError(f"level {level.number} doesn't list its panels in page order")


def build(spec: MapSpec, out_dir: Path) -> None:
    """Write one map's directory.

    Args:
        spec: The map.
        out_dir: The directory holding every map; the map's own directory is replaced.
    """
    target = out_dir / spec.name
    if target.exists():
        shutil.rmtree(target)
    (target / "images").mkdir(parents=True)
    (target / "stages").mkdir()
    registrations: dict[str, Registration] = {}
    images: dict[str, SyntheticImage] = {}
    sheets: dict[str, list[RegionSpec]] = {}
    for region in spec.regions:
        sheets.setdefault(region.image, []).append(region)
    for image, sheet in sheets.items():
        seed = zlib.crc32(image.encode())
        if len(sheet) == 1:
            data, registration, look = draw_page(sheet[0], seed)
            drawn = {sheet[0].id: registration}
        else:
            data, drawn, look = draw_regions(sheet, seed)
        (target / "images" / image).write_bytes(data)
        registrations |= drawn
        images[image] = SyntheticImage(file=image, ink=look.ink, paper=look.paper)
    for picture in spec.sheets:
        if picture.image in images:
            raise ValueError(f"image {picture.image} holds two pictures, which the generator doesn't draw")
        data, ink, paper = draw_sheet(picture)
        (target / "images" / picture.image).write_bytes(data)
        images[picture.image] = SyntheticImage(file=picture.image, ink=ink, paper=paper)
    _check_panel_order(spec, registrations)
    resolved, entrance = resolve_dungeon(spec)
    traced = {level.spec.number: level for level in resolved}
    frames = frames_of(resolved)
    regions = tuple(
        Region(
            id=region.id,
            page=region.page,
            image=region.image,
            style=region.style,
            columns=region.plan.width,
            rows=region.plan.height,
            scale=region.scale or TEN_FEET,
            compass=region.compass,
            legend=Legend(printed=region.legend_from is None, from_page=region.legend_from, symbols=region.legend),
            cut_off=region.cut_off,
            verdict="registered",
            registration=registrations[region.id],
        )
        for region in spec.regions
    ) + tuple(
        Region(
            id=sheet.id,
            page=sheet.page,
            image=sheet.image,
            style=sheet.style,
            columns=sheet.columns,
            rows=sheet.rows,
            scale=sheet.scale,
            compass=sheet.compass,
            legend=sheet.legend,
            verdict=sheet.verdict,
            reason=sheet.reason,
        )
        for sheet in spec.sheets
        if sheet.verdict is not None
    )
    panels = tuple(panel_model(spec, panel) for panel in spec.panels)
    pages: dict[int, list[str]] = {}
    for picture in [*spec.regions, *(sheet for sheet in spec.sheets if sheet.verdict is not None)]:
        pages.setdefault(picture.page, [])
        if picture.image not in pages[picture.page]:
            pages[picture.page].append(picture.image)
    truth = GeometryTruth(
        pages=spec.pages
        or tuple(
            Page(number=number, images=tuple(PageImage(file=file) for file in files))
            for number, files in sorted(pages.items())
        ),
        regions=regions,
        panels=panels,
        half_pairs=spec.half_pairs,
        dungeons=(
            Dungeon(
                id=spec.dungeon.id,
                name=spec.dungeon.name,
                entrance=entrance,
                levels=tuple(
                    level_model(traced[level.number], frames)
                    if level.fallback is None
                    else Level(number=level.number, verdict="fallback", reason=level.fallback)
                    for level in spec.dungeon.levels
                ),
            ),
        ),
    )
    save_geometry_truth(target / "geometry.json", truth)
    page_of = {region.id: region.page for region in spec.regions}
    regions_of = {panel.id: [region for region, _origin in panel.regions] for panel in spec.panels}
    map_pages = {
        level.number: level.pages
        or tuple(sorted({page_of[region] for panel in level.panels for region in regions_of[panel]}))
        for level in spec.dungeon.levels
    }
    survey = SurveyIndex(
        title=spec.title,
        hooks=(),
        town=TownInfo(name="", description=""),
        dungeons=(
            SurveyDungeon(
                id=spec.dungeon.id,
                name=spec.dungeon.name,
                levels=tuple(
                    SurveyLevel(
                        number=level.number,
                        map_pages=map_pages[level.number],
                        areas=tuple(
                            SurveyArea(
                                key=area.key, name=area.name, kind=area.kind, source_pages=map_pages[level.number]
                            )
                            for area in level.areas
                        ),
                    )
                    for level in spec.dungeon.levels
                ),
            ),
        ),
        monster_names=(),
    )
    write_json_artifact(target / "stages" / "survey.json", survey)
    for level in spec.dungeon.levels:
        connections = (
            _connections(traced[level.number])
            if level.number in traced
            else {area.key: list(area.links) for area in level.areas}
        )
        content = LevelContent(
            dungeon_id=spec.dungeon.id,
            level_number=level.number,
            areas=tuple(
                AreaContent(
                    key=area.key,
                    description=area.description,
                    encounters=(),
                    treasure=(),
                    features=(),
                    connections=tuple(connections[area.key]),
                    source_pages=map_pages[level.number],
                    confidence=1.0,
                )
                for area in level.areas
            ),
        )
        write_json_artifact(target / "stages" / f"areas.{spec.dungeon.id}.{level.number}.json", content)
    reading = MapReading(
        map_reading="read",
        levels=tuple(
            MapLevelReading(
                dungeon_id=spec.dungeon.id,
                level_number=level.number,
                map_pages=map_pages[level.number],
                status="read",
                proposals=_proposals(traced[level.number]) if level.number in traced else (),
                entrance_key=spec.entrance_key if any(area.key == spec.entrance_key for area in level.areas) else None,
            )
            for level in spec.dungeon.levels
        ),
    )
    write_json_artifact(target / "stages" / "mapread.json", reading)
    manifest = SyntheticManifest(
        name=spec.name,
        title=spec.title,
        features=spec.features,
        damage=spec.damage,
        images=tuple(images.values()),
    )
    write_json_artifact(target / "synthetic.json", manifest)


README = "\n".join(
    [
        "# Synthetic maps",
        "",
        "Each directory is a map that `osrforge.tracing.synthetic` draws from a known level, so its answer is exact. "
        "A map's directory holds its answer (`geometry.json`), its manifest (`synthetic.json`), its page images "
        "(`images/`), and the survey, content, and map-reading caches the stage would have (`stages/`).",
        "",
        "The generator writes every file here, so don't edit them by hand. Regenerate the set instead:",
        "",
        "```",
        "uv run python -m osrforge.tracing.synthetic tests/assets/synthetic-maps",
        "```",
        "",
        "A test regenerates the set and fails on any difference. The maps are CC0.",
        "",
    ]
)
"""The set's README, written beside the maps."""


def write_maps(out_dir: Path) -> tuple[str, ...]:
    """Write every synthetic map, and the set's README.

    Args:
        out_dir: The directory to write under, created if missing.

    Returns:
        The map names, sorted.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README.md").write_text(README, encoding="utf-8")
    names: list[str] = []
    for make in MAPS:
        spec = make()
        build(spec, out_dir)
        names.append(spec.name)
    return tuple(sorted(names))
