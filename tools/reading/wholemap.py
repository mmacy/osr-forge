"""The whole-page read (step 3 of Reading the map), as the screening asks it, and its score against the keys.

One request per page. Its question carries the page at the size the API reads, then each registered region as a
crop with its grid ticked (`R1`, `R2`, ...), then the levels the survey maps on the page with their keys. The answer
gives the following:

- each region's style, whether it's a plan view, its scale, and its compass
- the page's legend
- each level's outline, seed cells, and open ground
- the stairs, ladders, trapdoors, chutes, and other symbols
- the image edges that cut a region off where the map goes on past the image

The schema fixes the shape, and `check` tests what it can't: that every region and level is answered once, and that
every cell lies on its region's grid.

The score compares the answer with the key: the region verdicts through the scorer's verdicts family, and the
outlines, seeds, open ground, legend entries, and symbols directly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, get_args

import numpy as np
from PIL import Image

from osrforge.providers.base import ImagePart, TextPart
from osrforge.providers.reading import Effort, ReadingRequest, RequestKind
from osrforge.tracing.score import score_trace
from osrforge.tracing.truth import (
    SYMBOL_KINDS,
    Compass,
    GeometryTruth,
    Legend,
    Panel,
    Region,
    Scale,
    Style,
    parse_cell,
    parse_edge,
)

from .pictures import fit_for_api, png, region_crop

__all__ = [
    "WHOLE_MAP_SCHEMA",
    "WHOLE_MAP_SYSTEM",
    "PageRegion",
    "check_whole_map",
    "score_whole_map",
    "whole_map_kind",
    "whole_map_request",
]

STYLE_NAMES: tuple[str, ...] = get_args(Style)
SIDES = ("top", "right", "bottom", "left")
CELL_SYMBOLS = tuple(kind for kind in SYMBOL_KINDS if kind not in ("cave_mouth", "locked_door"))

WHOLE_MAP_SYSTEM = (
    "You read printed tabletop adventure maps for a program that traces each dungeon level from its printed map onto "
    "a square grid. Answer only through the structured output. Don't describe the adventure.\n\n"
    "Each request shows one page of the module whole, then each gridded region the program found on the page as its "
    "own picture, named R1, R2, and so on. A region picture shows the region's grid with half a cell of map around "
    "it, in white gutters. Red ticks in the gutters mark every grid line, and every few lines a red number names the "
    "column (along the top and the bottom) or the row (down the left and the right) of the cell that starts at that "
    "line. Give every cell as [x, y]: x is the column, counted from 0 at the left of the region's grid, and y is the "
    "row, counted from 0 at the top. Count from the numbered ticks; don't estimate from the picture's size.\n\n"
    "Styles. Give each region one style. tsr_blue: blue ink on white or cream paper, rock solid blue or blue-hatched, "
    "floor white with a thin blue grid. black_white: black line art on white paper, rooms with heavy black walls, "
    "rock drawn solid black, grey, hatched, or left blank. dark_rock: white passages and caves cut out of a dark "
    "fill, mid grey to black, the walls being the border of the fill. cave: white caves on a light grey or textured "
    "ground with freehand walls drawn off the grid, often with contour or elevation lines; its rock is pale, clearly "
    "lighter than mid grey. hex: a hex grid. isometric: a drawing in perspective. side_view: a cross-section seen "
    "from the side. outdoor: a wilderness or regional map. town: a town or village map of streets and buildings. "
    "no_grid: a plan with no square grid. A region is a plan view when it is drawn from straight above.\n\n"
    "Scale and compass. For each region give the scale statement exactly as printed and the feet per square, or "
    "null when none is printed; a scale printed beside a region on the same page applies to it. Say whether the "
    "page prints a compass that applies to the region and which page side its north arrow points to: top, right, "
    "bottom, or left; give its angle in degrees clockwise from the top of the page only when it doesn't point along "
    "a page side.\n\n"
    "Legend. If the page prints a legend (a table that explains the map's symbols), list the entries of the "
    "vocabulary it explains. The vocabulary: stairs, ladder, trapdoor, hinged_floor, chute, slide, pit, cave_mouth, "
    "pillar, statue, pool, steps, bridge, footing, locked_door. Stairs or steps between levels count as stairs; "
    "steps inside a level that lead nowhere else count as steps; a trap door counts as trapdoor; a cave entrance as "
    "cave_mouth; a locked door as locked_door; steps, footings, or bars on a bank of water as footing. Leave out "
    "entries outside the vocabulary.\n\n"
    "Levels. The request lists the levels the survey maps on this page, each with the keys its text gives. For each "
    "level, give the region it's drawn on and its outline: one or more rectangles [x, y, width, height] in grid "
    "cells whose union covers every floor cell of the level and as little as possible of any other level. Where two "
    "levels' floors meet, the border between their outlines follows a wall, a door, a cave mouth, or a printed line, "
    "never open floor. A rectangle may reach over rock or blank page. Give two or three seed cells well inside the "
    "level's floor, and the rectangles of open ground inside the outline: yards, courtyards, and the ground between "
    "separate buildings that the party walks on. Give an empty list when there is none.\n\n"
    "Symbols. List every stair, ladder, trapdoor, hinged floor, chute, slide, pit, pillar, statue, pool, steps, "
    "bridge, and footing drawn on a region, with the cells it covers, and every cave mouth (an opening the legend or "
    "the drawing marks as a cave entrance) with the cell inside it and the side of that cell it opens through "
    "(side none for every other symbol). Give "
    "the direction a stair, ladder, trapdoor, or chute goes, up or down, only when the map shows one with an arrow, a "
    "word, or a letter; otherwise none.\n\n"
    "Cut off. For each region, list the image edges that cut its drawing off, where the map goes on past the image "
    "onto a facing page. An edge where floor only runs to the drawing's own frame or border doesn't count.\n\n"
    "Keep notes under 60 words."
)

_CELL = {"type": "array", "items": {"type": "integer"}}
_RECT = {"type": "array", "items": {"type": "integer"}}

WHOLE_MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "regions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "region": {"type": "string"},
                    "style": {"type": "string", "enum": list(STYLE_NAMES)},
                    "plan_view": {"type": "boolean"},
                    "scale_text": {"type": ["string", "null"]},
                    "feet_per_square": {"type": ["integer", "null"]},
                    "compass_printed": {"type": "boolean"},
                    "north": {"type": "string", "enum": list(SIDES)},
                    "compass_angle": {"type": ["number", "null"]},
                    "cut_off": {"type": "array", "items": {"type": "string", "enum": list(SIDES)}},
                },
                "required": [
                    "region",
                    "style",
                    "plan_view",
                    "scale_text",
                    "feet_per_square",
                    "compass_printed",
                    "north",
                    "compass_angle",
                    "cut_off",
                ],
                "additionalProperties": False,
            },
        },
        "legend_printed": {"type": "boolean"},
        "legend_symbols": {"type": "array", "items": {"type": "string", "enum": list(SYMBOL_KINDS)}},
        "levels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dungeon": {"type": "string"},
                    "level": {"type": "integer"},
                    "region": {"type": "string"},
                    "rects": {"type": "array", "items": _RECT},
                    "seeds": {"type": "array", "items": _CELL},
                    "open_ground": {"type": "array", "items": _RECT},
                },
                "required": ["dungeon", "level", "region", "rects", "seeds", "open_ground"],
                "additionalProperties": False,
            },
        },
        "symbols": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "region": {"type": "string"},
                    "kind": {"type": "string", "enum": [*CELL_SYMBOLS, "cave_mouth"]},
                    "cells": {"type": "array", "items": _CELL},
                    "side": {"type": "string", "enum": ["north", "east", "south", "west", "none"]},
                    "direction": {"type": "string", "enum": ["up", "down", "none"]},
                },
                "required": ["region", "kind", "cells", "side", "direction"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["regions", "legend_printed", "legend_symbols", "levels", "symbols", "notes"],
    "additionalProperties": False,
}

WHOLE_MAP_MAX_TOKENS = 32000


def whole_map_kind(effort: Effort, model: str | None = None) -> RequestKind:
    """The whole-page read at one effort, on a model or the provider's default."""
    return RequestKind(
        name="reading-wholemap",
        system=WHOLE_MAP_SYSTEM,
        schema=WHOLE_MAP_SCHEMA,
        effort=effort,
        max_tokens=WHOLE_MAP_MAX_TOKENS,
        model=model,
    )


@dataclass(frozen=True)
class PageRegion:
    """One registered region of a page, as the request names it."""

    label: str
    region_id: str
    columns: int
    rows: int


def whole_map_request(
    kind: RequestKind,
    page: np.ndarray,
    regions: Sequence[tuple[PageRegion, np.ndarray, Any]],
    levels: Sequence[tuple[str, int, Sequence[str]]],
) -> ReadingRequest:
    """The request for one page.

    Args:
        kind: The kind, at its effort.
        page: The page image.
        regions: Each region: its name, the page image it's cut from, and its registration.
        levels: Each level the survey maps on the page: dungeon id, level number, and its keys as printed.

    Returns:
        The request. The system prompt is the shared part, and the page and the regions are the question.
    """
    question: list[TextPart | ImagePart] = [
        TextPart(text="The page:"),
        ImagePart(png=png(fit_for_api(Image.fromarray(page)))),
    ]
    for region, rgb, registration in regions:
        question.append(TextPart(text=f"Region {region.label}: {region.columns} columns by {region.rows} rows."))
        question.append(ImagePart(png=png(fit_for_api(region_crop(rgb, registration, region.label)))))
    lines = [
        f"- dungeon {dungeon!r}, level {number}: keys {', '.join(keys) if keys else '(none)'}"
        for dungeon, number, keys in levels
    ]
    question.append(TextPart(text="The levels the survey maps on this page, in survey order:\n" + "\n".join(lines)))
    return ReadingRequest(kind=kind, shared=(), question=tuple(question))


def check_whole_map(
    answer: dict[str, Any], regions: Sequence[PageRegion], levels: Sequence[tuple[str, int, Sequence[str]]]
) -> list[str]:
    """What the schema can't check: each region and level answered once, and every cell on its region's grid."""
    faults: list[str] = []
    by_label = {region.label: region for region in regions}
    seen = [item["region"] for item in answer["regions"]]
    for label in by_label:
        if seen.count(label) != 1:
            faults.append(f"region {label} must be answered exactly once, not {seen.count(label)} times")
    for label in seen:
        if label not in by_label:
            faults.append(f"region {label!r} isn't one the request names")

    def on_grid(label: str, cell: list[int], what: str) -> None:
        region = by_label.get(label)
        if region is None:
            faults.append(f"{what} names region {label!r}, which the request doesn't")
        elif len(cell) != 2 or not (0 <= cell[0] < region.columns and 0 <= cell[1] < region.rows):
            faults.append(f"{what} cell {cell} isn't [x, y] on region {label}'s {region.columns} by {region.rows} grid")

    expected = {(dungeon, number) for dungeon, number, _keys in levels}
    answered = [(item["dungeon"], item["level"]) for item in answer["levels"]]
    for ident in expected:
        if answered.count(ident) != 1:
            faults.append(f"level {ident[0]}/{ident[1]} must be answered exactly once, not {answered.count(ident)}")
    for item in answer["levels"]:
        ident = f"{item['dungeon']}/{item['level']}"
        if (item["dungeon"], item["level"]) not in expected:
            faults.append(f"level {ident} isn't one the request lists")
            continue
        if not item["rects"]:
            faults.append(f"level {ident} has no outline rectangle")
        for rect in [*item["rects"], *item["open_ground"]]:
            if len(rect) != 4 or rect[2] <= 0 or rect[3] <= 0:
                faults.append(f"level {ident} has a rectangle {rect} that isn't [x, y, width, height] with a size")
        if not item["seeds"]:
            faults.append(f"level {ident} has no seed cell")
        for seed in item["seeds"]:
            on_grid(item["region"], seed, f"level {ident} seed")
    for symbol in answer["symbols"]:
        what = f"symbol {symbol['kind']}"
        if not symbol["cells"]:
            faults.append(f"{what} has no cell")
        for cell in symbol["cells"]:
            on_grid(symbol["region"], cell, what)
        if symbol["kind"] == "cave_mouth" and (symbol["side"] == "none" or len(symbol["cells"]) != 1):
            faults.append("a cave mouth gives one cell and the side it opens through")
    return faults


def _region_from(answer: dict[str, Any], key_region: Region, legend: Legend) -> Region:
    item = answer
    feet = item["feet_per_square"]
    return key_region.model_copy(
        update={
            "style": item["style"],
            "scale": Scale(
                feet=feet if feet in (5, 10) else (None if feet else 10), printed=item["scale_text"] is not None
            ),
            "compass": _compass(item),
            "legend": legend,
            "cut_off": tuple(item["cut_off"]),
        }
    )


def _compass(item: dict[str, Any]) -> Compass:
    if not item["compass_printed"]:
        return Compass(north="top", printed=False)
    return Compass(north=item["north"], printed=True)


def _cells_of(rects: Sequence[Sequence[int]]) -> set[tuple[int, int]]:
    return {(x, y) for x0, y0, w, h in rects for x in range(x0, x0 + w) for y in range(y0, y0 + h)}


def score_whole_map(
    answer: dict[str, Any],
    key: GeometryTruth,
    regions: Sequence[PageRegion],
    legend_from_page: int | None,
) -> dict[str, Any]:
    """Score one page's answer against the key.

    Args:
        answer: The parsed answer.
        key: The answer key.
        regions: The regions the request named.
        legend_from_page: The page whose legend the stage's rule gives this page when the answer says it prints none.

    Returns:
        The figures, in three parts. Per region, the scorer's verdict figures (style, scale, compass, legend) and the
        cut-off edges. Per level, the outline's floor overlap, the seeds on the level's floor, and the open ground's
        overlap. Per region again, the symbols found.
    """
    out: dict[str, Any] = {"regions": {}, "levels": {}, "symbols": {}}
    legend = Legend(
        printed=answer["legend_printed"],
        from_page=None if answer["legend_printed"] else legend_from_page,
        symbols=tuple(answer["legend_symbols"]),
    )
    by_label = {item["region"]: item for item in answer["regions"]}
    traced: list[Region] = []
    for region in regions:
        key_region = key.region(region.region_id)
        mine = _region_from(by_label[region.label], key_region, legend)
        traced.append(mine)
        out["regions"][region.region_id] = {
            "style": [mine.style, key_region.style],
            "plan_view": by_label[region.label]["plan_view"],
            "feet": [mine.scale.feet, key_region.scale.feet],
            "scale_printed": [mine.scale.printed, key_region.scale.printed],
            "north": [mine.compass.north, key_region.compass.north],
            "compass_printed": [mine.compass.printed, key_region.compass.printed],
            "legend_printed": [mine.legend.printed, key_region.legend.printed],
            "legend_from_page": [mine.legend.from_page, key_region.legend.from_page],
            "legend_symbols": [sorted(mine.legend.symbols), sorted(key_region.legend.symbols)],
            "cut_off": [sorted(mine.cut_off), sorted(key_region.cut_off)],
        }
    trace = key.model_copy(update={"regions": tuple(next((r for r in traced if r.id == k.id), k) for k in key.regions)})
    verdicts = score_trace(trace, key).after.verdicts
    out["verdicts"] = verdicts.model_dump()
    label_of = {region.label: region.region_id for region in regions}
    for item in answer["levels"]:
        region_id = label_of.get(item["region"])
        ident = f"{item['dungeon']}/{item['level']}"
        panel = _panel_of(key, region_id) if region_id else None
        outline = next(
            (o for o in (panel.outlines if panel else ()) if (o.dungeon, o.level) == (item["dungeon"], item["level"])),
            None,
        )
        if panel is None or outline is None:
            out["levels"][ident] = {"region_right": False}
            continue
        floor = {(x, y) for y, row in enumerate(panel.cells) for x, letter in enumerate(row) if letter in ".~"}
        key_cells = _cells_of(outline.rects) & floor
        read_cells = _cells_of(item["rects"]) & floor
        key_ground = _cells_of(outline.open_ground) & floor
        read_ground = _cells_of(item["open_ground"]) & floor
        seeds = [tuple(seed) for seed in item["seeds"]]
        out["levels"][ident] = {
            "region_right": True,
            "outline_iou": len(key_cells & read_cells) / max(1, len(key_cells | read_cells)),
            "outline_recall": len(key_cells & read_cells) / max(1, len(key_cells)),
            "seeds": len(seeds),
            "seeds_in_level": sum(seed in key_cells for seed in seeds),
            "open_ground_key": len(key_ground),
            "open_ground_iou": (len(key_ground & read_ground) / max(1, len(key_ground | read_ground)))
            if key_ground or read_ground
            else None,
        }
    for region in regions:
        panel = _panel_of(key, region.region_id)
        if panel is None:
            continue
        mine = [symbol for symbol in answer["symbols"] if label_of.get(symbol["region"]) == region.region_id]
        out["symbols"][region.region_id] = _symbol_match(panel, mine)
    return out


def _panel_of(key: GeometryTruth, region_id: str) -> Panel | None:
    return next((panel for panel in key.panels if any(part.region == region_id for part in panel.regions)), None)


_FAMILY = {"stairs": "stairs", "ladder": "stairs", "steps": "steps", "trapdoor": "trapdoor", "hinged_floor": "trapdoor"}


def _symbol_match(panel: Panel, mine: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Key symbols found (same kind on a cell within one of the key's, or the same cave-mouth edge), and extras."""
    key_items = []
    for symbol in panel.symbols:
        if symbol.kind == "cave_mouth" and symbol.edge:
            cell, side = parse_edge(symbol.edge)
            key_items.append((symbol.kind, {cell}, side, symbol.direction))
        else:
            key_items.append((symbol.kind, {parse_cell(c) for c in symbol.cells}, None, symbol.direction))
    used: set[int] = set()
    found = 0
    direction_right = 0
    by_kind: dict[str, list[int]] = {}
    for kind, cells, _side, direction in key_items:
        tally = by_kind.setdefault(kind, [0, 0])
        tally[0] += 1
        for index, item in enumerate(mine):
            if index in used or _FAMILY.get(item["kind"], item["kind"]) != _FAMILY.get(kind, kind):
                continue
            read = {tuple(cell) for cell in item["cells"]}
            if any(max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 1 for a in read for b in cells):
                used.add(index)
                found += 1
                tally[1] += 1
                read_direction = None if item["direction"] == "none" else item["direction"]
                direction_right += read_direction == direction
                break
    return {
        "key": len(key_items),
        "found": found,
        "read": len(mine),
        "extra": len(mine) - len(used),
        "direction_right": direction_right,
        "by_kind": by_kind,
    }
