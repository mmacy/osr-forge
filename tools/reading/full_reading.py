"""The full run's reading of a map, as the stage reads it, before resolution.

The reading follows the plan's steps with the screening's settings:

1. **Whole page.** Claude reads each page once (`wholemap`). Its answer gives each region's style, plan view, scale,
   compass, and cut-off edges, the page's legend, each level's outline, seeds, and open ground, and the symbols. A
   region whose style isn't one of the traced styles, or that isn't a plan view, is refused. A page with no legend
   takes the legend of the nearest page of the same dungeon that prints one, the earlier of two equally near, else
   the nearest earlier page that prints one.
2. **Ink.** Each region's floor fractions, edge lines, and the cells the ink leaves unsettled (`ink`), measured with
   the read style. A cell is floor at half floor or more (the half rule).
3. **Clef on edges.** Local Clef-flash answers each edge that touches a cell the half rule makes floor or the ink
   leaves unsettled, and each edge the answer key measures (both cells floor or water), which the calibration is
   fitted on. The question is the read style's answer-key option set. On a real map, an edge the answer-key pass
   exported is asked the exported request, so the pass's answer is reused.
4. **Calibration.** Per read style, the logistic model on Clef's passable and door log-odds and the ink's `width`,
   `heavy`, and `line` (`clef+ink`), fitted on the measured edges of every answer key and synthetic answer of that
   style except the maps cut from an image with the same bytes as the map it is applied to (the map itself, the
   other regions of its page, and a synthetic twin drawn from the same image), so every map is scored held out. When
   the style has no other map, the map's own measured edges are split into spatial folds (`calibrate.fold_of`). An
   edge whose most probable Clef answer is a door kind is that door, whatever the calibration says (doors first).
5. **Uncertain items.** An edge between two cells the half rule makes floor whose calibrated P(passable) lies
   strictly between ε and 1 - ε, unless doors first decides it, and every cell the ink leaves unsettled. The plan's
   other kinds of item (edges that would change which areas connect, bypass and repair items, outline crossings,
   items at a fold, stretch pairings) need the resolution's checks and repairs, which aren't built, so the run sends
   none of them. Claude judges the items in requests of `items_per_request`, one region at a time, in north-then-west
   order. Its answer replaces an item's probabilities, weighted by Claude's accuracy on such items of the style as the
   screening measured it (`screening_accuracy`): an edge is passable with that accuracy when Claude reads it
   passable, and the passable threshold decides, and a cell is floor with that accuracy when Claude reads it floor.
   At the screening's accuracies (0.875 to 1 per style and kind) and the passable thresholds, every weighted reading is
   Claude's own. Without the screening's figures, Claude's answer decides.
6. **Cells and edges.** A cell is the half rule's, or Claude's answer for an item. An edge with floor or water on both
   sides is Claude's answer when it was an item, else the door Clef reads by doors first, else the calibrated
   decision at the read style's passable threshold, else a wall when Clef has no answer. An edge with floor or water
   on one side is a wall, or the door Clef reads there when a door kind beats wall and window. Partly floor cells are
   the cells the ink finds between a quarter and three quarters floor.
7. **Keys from marks.** Every label-sized mark the ink finds (`marks.find_marks`) is asked which key it is: the
   survey's keys of every level whose outline, as the whole-page read gives it, lies within two cells, in chunks of 25
   with `none`, and the letter question when every chunk answers `none`. Each key keeps its most probable mark. A mark
   read as a key or a letter becomes a mark with that text.
8. **Halves.** Claude (`offset_pick`) picks each pair's column offset among the registration's three candidates, or
   none. If it picks a wrong offset for a real pair, every pair stays apart (the halves rule). A pair it joins becomes
   one panel, `<first>+<second>`, with the second half at the picked column offset and the registration's row shift;
   the seam between the halves is open where both sides are floor, and the cells of a column gap are rock. A pair it
   doesn't join stays two panels.

What the run takes from the answer key and nothing else: each region's registration, which the registration
measurement measured.
"""

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from tools.answer_keys.clefpass import STYLE_OPTIONS as KEYSET_OPTIONS
from tools.answer_keys.resolve import reading_of

from osrforge.tracing.truth import (
    EdgeGrids,
    GeometryTruth,
    HalfPair,
    Legend,
    Mark,
    Outline,
    Panel,
    PanelRegion,
    Region,
    Symbol,
    cell_key,
    edge_key,
    parse_cell,
    parse_edge,
)

from .answers import LETTERS as OPTION_LETTERS
from .calibrate import Logistic, fold_of
from .clefask import edge_crop, edge_request
from .dataset import features
from .edges import BLOCKED, EdgeRow, decide, measured_edges
from .full_members import HalvesSpec, Member, RunMap
from .full_settings import FullRunSettings
from .full_store import Sources
from .hosted_exported import checked_requests, exported_requests, for_model
from .ink import INK_STYLES, EdgeInk, MapInk, unsettled_cells
from .items import AskedItem, check_items, items_kind, items_request, letters_of
from .maps import STYLES, ScreenMap, load_rgb
from .marks import (
    FoundMark,
    find_marks,
    key_chunks,
    letter_request,
    mark_crop,
    mark_reading,
    mark_request,
    one_mark_per_key,
)
from .offsets import LETTERS as OFFSET_LETTERS
from .offsets import check_offset, letters_for, offset_kind, offset_request
from .screen_offsets import load_pair
from .screen_wholemap import Page, _page_regions, levels_on
from .wholemap import _region_from, check_whole_map, whole_map_kind, whole_map_request

__all__ = [
    "MapReading",
    "PageRead",
    "bare_edges_cleared",
    "calibrate",
    "clef_edges",
    "key_page_answer",
    "map_letters",
    "member_reading",
    "pick_offsets",
    "read_items",
    "read_marks",
    "read_page",
    "region_key_panel",
    "start_map",
]

type Edge = tuple[tuple[int, int], str]

NEIGHBOUR = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}
CLEF_MODEL = "clef-flash"


@dataclass
class PageRead:
    """One page's whole-page read."""

    page: Page
    answer: dict[str, Any] | None
    """The answer, or `None` when it is missing or malformed after the retry."""

    stand_in: bool = False
    """The answer is the answer key's, standing in for a request a dry run doesn't send."""

    label_of: dict[str, str] = field(default_factory=dict)
    """Each region label's region id."""


@dataclass
class MapReading:
    """One region's reading, built up step by step."""

    run_map: RunMap
    region: Region
    """The region as read: the answer key's registration with the whole-page read's style, scale, and compass."""

    key: Panel
    """The answer key's panel, for its grid's size."""

    style: str | None
    """The read style, or `None` when the region is refused or unread."""

    outlines: list[Outline] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    floors: np.ndarray | None = None
    unsettled: set[tuple[int, int]] = field(default_factory=set)
    ink: MapInk | None = None
    edge_ink: dict[Edge, EdgeInk] = field(default_factory=dict)
    clef: dict[Edge, dict[str, float]] = field(default_factory=dict)
    """Clef's probabilities by truth letter, per edge."""

    passable: dict[Edge, float] = field(default_factory=dict)
    """The calibrated P(passable), per edge Clef answered: 0 where doors first decides."""

    items: list[AskedItem] = field(default_factory=list)
    judged: dict[tuple[tuple[int, int], str | None], str] = field(default_factory=dict)
    """Claude's letter per item, by `(cell, side)`, `side` `None` for a cell."""

    cells: list[list[str]] = field(default_factory=list)
    letters: dict[Edge, str] = field(default_factory=dict)
    marks: list[Mark] = field(default_factory=list)
    unjudged: list[AskedItem] = field(default_factory=list)
    """Items Claude left without an answer: missing in the run, or malformed after the retry."""

    unanswered: list[Edge] = field(default_factory=list)
    """Edges between floor or water that became walls because Clef had no answer for them."""

    @property
    def screen(self) -> ScreenMap:
        """The map with the read style, for the ink and the questions."""
        base = self.run_map.screen
        return ScreenMap(base.id, self.style or base.style, base.key, base.image, base.work, base.frame, None)


# 1. The whole page.


def key_page_answer(page: Page, key: GeometryTruth) -> dict[str, Any]:
    """The whole-page answer the answer key gives, in the request's shape.

    It stands in for the whole-page read in a dry run, and it is the fake answer of an offline run on fake answers.
    Outlines on a joined panel are cut to each half, in the half's own cells, and a half that holds none of the
    level's seeds gets its first floor cell inside the outline as one. The request answers each level once, on one
    region, so a level drawn on two regions of a page is given on the first one only, as Claude has to give it.
    """
    regions, levels, symbols = [], [], []
    legend = Legend(printed=False)
    for label, region_id in page.regions:
        region = key.region(region_id)
        legend = region.legend if region.legend.printed else legend
        regions.append(
            {
                "region": label,
                "style": region.style,
                "plan_view": True,
                "scale_text": f"1 square = {region.scale.feet} feet" if region.scale.printed else None,
                "feet_per_square": region.scale.feet if region.scale.printed else None,
                "compass_printed": region.compass.printed,
                "north": region.compass.north,
                "compass_angle": None,
                "cut_off": list(region.cut_off),
            }
        )
        panel = next((p for p in key.panels if any(part.region == region_id for part in p.regions)), None)
        if panel is None:
            continue
        origin = next(part.origin for part in panel.regions if part.region == region_id)
        ox, oy, width, height = origin[0], origin[1], region.columns, region.rows

        def inside(cell: tuple[int, int], ox: int = ox, oy: int = oy, width: int = width, height: int = height) -> bool:
            return ox <= cell[0] < ox + width and oy <= cell[1] < oy + height

        for outline in panel.outlines:
            rects = [rect for rect in (_cut(r, ox, oy, width, height) for r in outline.rects) if rect]
            seeds = [[c[0] - ox, c[1] - oy] for c in map(parse_cell, outline.seeds) if inside(c)]
            if rects and not seeds:
                # A level drawn across both halves keeps its seeds on one; the other half's seed is its first floor.
                seeds = [
                    [x - ox, y - oy]
                    for x0, y0, w, h in ((r[0] + ox, r[1] + oy, r[2], r[3]) for r in rects)
                    for y in range(y0, y0 + h)
                    for x in range(x0, x0 + w)
                    if panel.cells[y][x] == "."
                ][:1]
            answered = {(item["dungeon"], item["level"]) for item in levels}
            if rects and seeds and (outline.dungeon, outline.level) not in answered:
                levels.append(
                    {
                        "dungeon": outline.dungeon,
                        "level": outline.level,
                        "region": label,
                        "rects": rects,
                        "seeds": seeds,
                        "open_ground": [
                            rect for rect in (_cut(r, ox, oy, width, height) for r in outline.open_ground) if rect
                        ],
                    }
                )
        for symbol in panel.symbols:
            if symbol.kind == "locked_door":
                continue
            if symbol.edge is not None:
                cell, side = parse_edge(symbol.edge)
                cells, side_name = [cell], side
            else:
                cells, side_name = [parse_cell(c) for c in symbol.cells], "none"
            mine = [[c[0] - ox, c[1] - oy] for c in cells if inside(c)]
            if mine:
                symbols.append(
                    {
                        "region": label,
                        "kind": symbol.kind,
                        "cells": mine,
                        "side": side_name,
                        "direction": symbol.direction or "none",
                    }
                )
    return {
        "regions": regions,
        "legend_printed": legend.printed,
        "legend_symbols": list(legend.symbols),
        "levels": levels,
        "symbols": symbols,
        "notes": "from the answer key",
    }


def _cut(rect: Sequence[int], ox: int, oy: int, width: int, height: int) -> list[int] | None:
    """A panel rectangle cut to one region of the panel, in the region's cells, or `None` when nothing is left."""
    x, y, w, h = rect
    left, top = max(x, ox), max(y, oy)
    right, bottom = min(x + w, ox + width), min(y + h, oy + height)
    if right <= left or bottom <= top:
        return None
    return [left - ox, top - oy, right - left, bottom - top]


def read_page(page: Page, settings: FullRunSettings, sources: Sources, stand_in: GeometryTruth | None) -> PageRead:
    """Claude's whole-page read of a page.

    Args:
        page: The page.
        settings: The run's settings.
        sources: Where answers come from.
        stand_in: The answer key, whose answer stands in when the store lacks the read and the run may not ask, or
            `None` to leave the page unread then.

    Returns:
        The read.
    """
    kind = whole_map_kind(settings.whole_page.effort, settings.whole_page.model)
    regions = _page_regions(page)
    levels = levels_on(page)
    labels = [region for region, _rgb, _registration in regions]
    request = whole_map_request(kind, load_rgb(page.image), regions, levels)
    outcome = sources.read(request, lambda answer: check_whole_map(answer, labels, levels), page.id, {"page": page.id})
    label_of = {region.label: region.region_id for region in labels}
    if outcome is None:
        if stand_in is None:
            return PageRead(page=page, answer=None, label_of=label_of)
        return PageRead(page=page, answer=key_page_answer(page, stand_in), stand_in=True, label_of=label_of)
    return PageRead(page=page, answer=outcome.get("answer"), label_of=label_of)


def page_legends(reads: Sequence[PageRead]) -> dict[str, Legend]:
    """Each page's legend by the plan's rule, by page id."""
    printed = {read.page.id: read for read in reads if read.answer and read.answer["legend_printed"]}
    dungeons = {read.page.id: {d for d, _n, _k in levels_on(read.page)} for read in reads}
    out: dict[str, Legend] = {}
    for read in reads:
        if read.answer is None:
            continue
        if read.answer["legend_printed"]:
            out[read.page.id] = Legend(printed=True, symbols=tuple(read.answer["legend_symbols"]))
            continue
        number = read.page.number
        same = [printed[p] for p in printed if dungeons[p] & dungeons[read.page.id]]
        pool = same or [printed[p] for p in printed if printed[p].page.number < number]
        if not pool:
            out[read.page.id] = Legend(printed=False)
            continue
        best = min(pool, key=lambda other: (abs(other.page.number - number), other.page.number))
        symbols = tuple(best.answer["legend_symbols"]) if best.answer else ()
        out[read.page.id] = Legend(printed=False, from_page=best.page.number, symbols=symbols)
    return out


def start_map(run_map: RunMap, read: PageRead, legend: Legend | None) -> MapReading:
    """A region's reading after the whole-page read: its region, style, outlines, and symbols."""
    screen = run_map.screen
    key_document_panel = region_key_panel(_key(screen), screen.id)
    key_region = next(r for r in _key(screen).regions if r.id == screen.id)
    if read.answer is None or legend is None:
        return MapReading(run_map=run_map, region=key_region, key=key_document_panel, style=None)
    label = next(label for label, region_id in read.page.regions if region_id == screen.id)
    item = next((entry for entry in read.answer["regions"] if entry["region"] == label), None)
    if item is None:
        return MapReading(run_map=run_map, region=key_region, key=key_document_panel, style=None)
    region = _region_from(item, key_region, legend)
    usable_scale = region.scale.feet is not None
    traced = item["plan_view"] and item["style"] in STYLES and item["style"] in INK_STYLES and usable_scale
    if not traced:
        region = region.model_copy(update={"verdict": "refused"})
    reading = MapReading(
        run_map=run_map, region=region, key=key_document_panel, style=item["style"] if traced else None
    )
    width, height = key_document_panel.width, key_document_panel.height
    for level in read.answer["levels"]:
        if level["region"] != label:
            continue
        seeds = [cell_key((x, y)) for x, y in level["seeds"] if 0 <= x < width and 0 <= y < height]
        rects = tuple(tuple(rect) for rect in level["rects"] if len(rect) == 4 and rect[2] > 0 and rect[3] > 0)
        if seeds and rects:
            reading.outlines.append(
                Outline(
                    dungeon=level["dungeon"],
                    level=level["level"],
                    rects=rects,  # type: ignore[arg-type]
                    seeds=tuple(seeds),
                    open_ground=tuple(tuple(r) for r in level["open_ground"] if len(r) == 4),  # type: ignore[arg-type]
                )
            )
    for symbol in read.answer["symbols"]:
        if symbol["region"] != label:
            continue
        cells = [(x, y) for x, y in symbol["cells"] if 0 <= x < width and 0 <= y < height]
        if not cells:
            continue
        direction = None if symbol["direction"] == "none" else symbol["direction"]
        if symbol["kind"] == "cave_mouth":
            if symbol["side"] in NEIGHBOUR:
                reading.symbols.append(Symbol(kind="cave_mouth", edge=edge_key(cells[0], symbol["side"])))
        else:
            reading.symbols.append(Symbol(kind=symbol["kind"], cells=tuple(map(cell_key, cells)), direction=direction))
    return reading


def bare_edges_cleared(cells: Sequence[str], edges: EdgeGrids) -> EdgeGrids:
    """The edges with every edge that touches no floor or water made `-`, as the truth format requires."""
    width, height = len(cells[0]), len(cells)

    def occupied(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height and cells[y][x] in ".~"

    vertical = [
        "".join(letter if occupied(x - 1, y) or occupied(x, y) else "-" for x, letter in enumerate(row))
        for y, row in enumerate(edges.vertical)
    ]
    horizontal = [
        "".join(letter if occupied(x, y - 1) or occupied(x, y) else "-" for x, letter in enumerate(row))
        for y, row in enumerate(edges.horizontal)
    ]
    return EdgeGrids(vertical=tuple(vertical), horizontal=tuple(horizontal))


def region_key_panel(key: GeometryTruth, region_id: str) -> Panel:
    """The answer key's panel for one region: the region's own panel, or its part of a joined panel.

    The part of a joined panel is cut at the region's origin to the region's columns and rows. Its marks inside the
    region come with it, and its outlines and symbols are left out: the run measures the grid, the cells, and the
    edges on it, and a fake answer reads the marks.
    """
    own = next((panel for panel in key.panels if panel.id == region_id), None)
    if own is not None:
        return own
    panel = next(p for p in key.panels if any(part.region == region_id for part in p.regions))
    ox, oy = next(part.origin for part in panel.regions if part.region == region_id)
    region = key.region(region_id)
    width, height = region.columns, region.rows
    cells = tuple(row[ox : ox + width] for row in panel.cells[oy : oy + height])
    edges = EdgeGrids(
        vertical=tuple(row[ox : ox + width + 1] for row in panel.edges.vertical[oy : oy + height]),
        horizontal=tuple(row[ox : ox + width] for row in panel.edges.horizontal[oy : oy + height + 1]),
    )

    def mine(texts: Sequence[str]) -> tuple[str, ...]:
        cells_in = [parse_cell(text) for text in texts]
        return tuple(cell_key((x - ox, y - oy)) for x, y in cells_in if ox <= x < ox + width and oy <= y < oy + height)

    marks = []
    for mark in panel.marks:
        x, y = parse_cell(mark.cell)
        if ox <= x < ox + width and oy <= y < oy + height:
            marks.append(Mark(text=mark.text, cell=cell_key((x - ox, y - oy))))
    return Panel(
        id=region_id,
        regions=(PanelRegion(region=region_id, origin=(0, 0)),),
        scale_feet=panel.scale_feet,
        cells=cells,
        edges=bare_edges_cleared(cells, edges),
        partial=mine(panel.partial),
        cave=mine(panel.cave),
        marks=tuple(marks),
    )


def _key(screen: ScreenMap) -> GeometryTruth:
    from .maps import key_document

    return key_document(screen.key)


# 2 and 3. The ink and Clef on edges.


def measure_ink(reading: MapReading) -> None:
    """The region's ink, floor fractions, and unsettled cells, measured with the read style."""
    assert reading.style is not None and reading.region.registration is not None
    screen = reading.screen
    reading.ink = MapInk(load_rgb(screen.image), reading.region.registration, INK_STYLES[reading.style])
    reading.floors = reading.ink.floor_fractions()
    cells = [(x, y) for y in range(reading.key.height) for x in range(reading.key.width)]
    reading.unsettled = set(unsettled_cells(reading.ink, reading.floors, cells))


def asked_edges(reading: MapReading) -> list[Edge]:
    """The edges Clef is asked: those touching a half-rule floor or unsettled cell, and the key's measured edges."""
    assert reading.floors is not None
    candidates = {
        (x, y)
        for y in range(reading.key.height)
        for x in range(reading.key.width)
        if reading.floors[y, x] >= 0.5 or (x, y) in reading.unsettled
    }
    wanted = set(measured_edges(reading.key))
    for (x, y), side in reading.key.edges.positions():
        dx, dy = NEIGHBOUR[side]
        if (x, y) in candidates or (x + dx, y + dy) in candidates:
            wanted.add(((x, y), side))
    return sorted(wanted, key=lambda edge: (edge[0][1], edge[0][0], edge[1]))


def clef_edges(reading: MapReading, sources: Sources, workers: int = 1) -> None:
    """Clef's answer for every asked edge, from the store or the run's Clef, as truth-letter probabilities."""
    assert reading.style is not None and reading.region.registration is not None and reading.floors is not None
    screen = reading.run_map.screen
    exported: dict[str, Any] = {}
    if screen.work is not None and (screen.work / "clef" / "requests" / "requests.jsonl").exists():
        exported = checked_requests(exported_requests(screen.work, screen.frame), screen.id)
    state, options = KEYSET_OPTIONS[reading.style]
    rgb = load_rgb(screen.image)
    edges = asked_edges(reading)
    asks = []
    for edge in edges:
        name = edge_key(*edge)
        stored_request = exported.get(name)
        same = stored_request is not None and (stored_request.state, stored_request.options) == (state, tuple(options))
        if stored_request is not None and same:
            request = for_model(stored_request, CLEF_MODEL)
        else:
            request = edge_request(
                reading.style, "keyset", edge_crop(rgb, reading.region.registration, *edge), CLEF_MODEL
            )
        asks.append((request, reading.run_map.uid, name, "clef-edge", {"edge": [list(edge[0]), edge[1]]}))
    assert reading.ink is not None
    for edge, answer in zip(edges, sources.decide_many(asks, workers), strict=True):
        if answer is not None:
            reading.clef[edge] = {OPTION_LETTERS[option]: p for option, p in answer.items() if option in OPTION_LETTERS}
        reading.edge_ink[edge] = reading.ink.edge(*edge, reading.floors)


# 4. The calibration.


def _door_top(probabilities: dict[str, float]) -> str | None:
    top = max(probabilities, key=probabilities.__getitem__)
    return top if top in "dls" else None


def image_digest(path: Path) -> str:
    """The sha256 of an image file's bytes: maps cut from the same image share it."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calibrate(readings: Sequence[MapReading]) -> dict[str, Any]:
    """Each reading's calibrated P(passable), per edge Clef answered, held out by image.

    A map's calibration leaves out every map cut from an image with the same bytes as its own: the map itself, the
    other regions of its page, and a synthetic twin drawn from the same image, whose picture and answer are the
    map's own.

    Args:
        readings: Every map's reading, Clef's answers in.

    Returns:
        The training edges per style, and for each map the maps its calibration left out, for the report.
    """
    digests = {reading.run_map.uid: image_digest(reading.run_map.screen.image) for reading in readings}
    training: dict[str, list[tuple[str, EdgeRow, EdgeInk]]] = {}
    for reading in readings:
        if reading.floors is None:
            continue
        style = reading.run_map.screen.style
        for edge in measured_edges(reading.key):
            probabilities = reading.clef.get(edge)
            if probabilities is None or edge not in reading.edge_ink:
                continue
            row = EdgeRow(reading.run_map.uid, style, edge, reading.key.edges.at(*edge), False, False, probabilities)
            training.setdefault(style, []).append((reading.run_map.uid, row, reading.edge_ink[edge]))
    left_out: dict[str, list[str]] = {}
    for reading in readings:
        if reading.style is None or not reading.clef:
            continue
        pool = training.get(reading.style, [])
        mine = digests[reading.run_map.uid]
        others = [(row, ink) for uid, row, ink in pool if digests[uid] != mine]
        left_out[reading.run_map.uid] = sorted(uid for uid in digests if digests[uid] == mine)
        edges = sorted(reading.clef)
        rows = [EdgeRow(reading.run_map.uid, reading.style, e, "-", False, False, reading.clef[e]) for e in edges]
        x = features(rows, [reading.edge_ink[e] for e in edges], "clef+ink")
        if others and len({row.passable for row, _ink in others}) == 2:
            model = Logistic().fit(
                features([row for row, _ in others], [ink for _, ink in others], "clef+ink"),
                np.array([row.passable for row, _ in others], dtype=float),
            )
            p = model.predict(x)
        else:
            p = _own_folds(reading, edges, x)
        for edge, q in zip(edges, p, strict=True):
            reading.passable[edge] = 0.0 if _door_top(reading.clef[edge]) else float(q)
    return {
        "training_edges": {style: len(rows) for style, rows in training.items()},
        "left_out": {uid: uids for uid, uids in left_out.items() if len(uids) > 1},
    }


def _own_folds(reading: MapReading, edges: list[Edge], x: np.ndarray) -> np.ndarray:
    """P(passable) from spatial folds of the map's own measured edges, for a style with no other map."""
    measured = {edge for edge in measured_edges(reading.key) if edge in reading.clef}
    y = np.array([reading.key.edges.at(*edge) in "op" for edge in edges], dtype=float)
    groups = np.array([fold_of(reading.run_map.uid, edge[0], [reading.run_map.uid]) for edge in edges])
    train_mask = np.array([edge in measured for edge in edges])
    out = np.full(len(edges), 0.5)
    for group in sorted(set(groups.tolist())):
        train = train_mask & (groups != group)
        if train.sum() and len(set(y[train].tolist())) == 2:
            out[groups == group] = Logistic().fit(x[train], y[train]).predict(x[groups == group])
    return out


# 5. The uncertain items.


def choose_items(reading: MapReading, epsilon: float) -> None:
    """The region's uncertain items, in north-then-west order: band edges between floor, and unsettled cells."""
    assert reading.floors is not None
    floor = {(x, y) for y in range(reading.key.height) for x in range(reading.key.width) if reading.floors[y, x] >= 0.5}
    items: list[tuple[tuple[int, int, int], AskedItem]] = []
    for edge, p in reading.passable.items():
        (x, y), side = edge
        dx, dy = NEIGHBOUR[side]
        if (x, y) in floor and (x + dx, y + dy) in floor and epsilon < p < 1 - epsilon:
            items.append(((y, x, 1), AskedItem(reading.run_map.uid, (x, y), side, "", round(p, 4))))
    for x, y in reading.unsettled:
        items.append(
            ((y, x, 0), AskedItem(reading.run_map.uid, (x, y), None, "", round(float(reading.floors[y, x]), 4)))
        )
    reading.items = [item for _order, item in sorted(items, key=lambda pair: (pair[0], pair[1].side or ""))]


def read_items(reading: MapReading, page: Page, settings: FullRunSettings, sources: Sources) -> int:
    """Claude's judgments of the region's items, in requests of `items_per_request`.

    Returns:
        How many requests had no answer: missing in a dry run, or malformed after the retry.
    """
    assert reading.style is not None and reading.region.registration is not None
    kind = items_kind(settings.items.effort, settings.items.model)
    per = settings.items_per_request
    unanswered = 0
    page_rgb, rgb = load_rgb(page.image), load_rgb(reading.run_map.screen.image)
    for start in range(0, len(reading.items), per):
        part = reading.items[start : start + per]
        request = items_request(kind, page_rgb, rgb, reading.region.registration, reading.style, part)
        about = {"map": reading.run_map.uid, "items": [[list(i.cell), i.side] for i in part]}
        outcome = sources.read(request, lambda answer, part=part: check_items(answer, part), reading.run_map.uid, about)
        if outcome is None or outcome.get("answer") is None:
            unanswered += 1
            continue
        letters = letters_of(outcome["answer"])
        for number, item in enumerate(part, start=1):
            reading.judged[(item.cell, item.side)] = letters[number]
    return unanswered


def screening_accuracy(
    screening: Path, model: str, effort: str, per_request: int, styles: dict[str, str]
) -> dict[str, dict[str, list[int]]]:
    """Claude's accuracy on the screening's uncertain items at one setting, per style and kind.

    The screening asked each real map's chosen items (`<screening>/claude/items/items.json`, each with the key's
    letter) in requests of `per_request` items (`<map>.<n>k<per_request>.json`, or `<map>.<n>.json` for requests of
    10). An edge is right when Claude reads it passable or blocked as the key does, and a cell when Claude reads it
    floor or not as the key does.

    Args:
        screening: The screening's results directory.
        model: The items' model.
        effort: The items' effort.
        per_request: The items in a request.
        styles: Each real map's style.

    Returns:
        Per style, `[right, judged]` for `edge` and for `cell`.
    """
    root = screening / "claude" / "items"
    chosen = json.loads((root / "items.json").read_text()) if (root / "items.json").exists() else {}
    tag = "" if per_request == 10 else f"k{per_request}"
    out: dict[str, dict[str, list[int]]] = {}
    for map_id, items in chosen.items():
        style = styles.get(map_id)
        if style is None:
            continue
        for path in sorted((root / model / effort).glob(f"{map_id}.*.json")):
            part = path.name[len(map_id) + 1 : -len(".json")]
            if not part[:1].isdigit() or part.lstrip("0123456789") != tag:
                continue
            outcome = json.loads(path.read_text())
            if outcome.get("answer") is None:
                continue
            start = int(part.split("k")[0]) * per_request
            letters = letters_of(outcome["answer"])
            for number, item in enumerate(items[start : start + per_request], start=1):
                read = letters.get(number)
                if read is None:
                    continue
                kind = "cell" if item["side"] is None else "edge"
                right = (
                    (read in "op") == (item["key"] in "op") if kind == "edge" else (read == ".") == (item["key"] == ".")
                )
                tally = out.setdefault(style, {}).setdefault(kind, [0, 0])
                tally[0] += right
                tally[1] += 1
    return out


# 6. Cells and edges.


def weighted_cell(letter: str, accuracy: float | None) -> str:
    """A cell item's reading from Claude's letter weighted by Claude's accuracy on cells of the style.

    P(floor) is the accuracy when Claude reads floor, and one less the accuracy when it doesn't, and the cell is floor
    at one half or more. With no accuracy, Claude's letter decides.
    """
    if accuracy is None:
        return letter
    floor = accuracy if letter == "." else 1.0 - accuracy
    if floor >= 0.5:
        return "."
    return letter if letter != "." else "#"


def weighted_edge(letter: str, accuracy: float | None, clef: dict[str, float], threshold: float) -> str:
    """An edge item's reading from Claude's letter weighted by Claude's accuracy on edges of the style.

    P(passable) is the accuracy when Claude reads the edge passable, and one less the accuracy when it doesn't, and
    the style's passable threshold decides. Claude's letter stands when the decision agrees with it. Otherwise the
    edge takes Clef's most probable letter of the decided kind (`edges.decide`). With no accuracy, Claude's letter
    decides.
    """
    if accuracy is None:
        return letter
    passable = accuracy if letter in "op" else 1.0 - accuracy
    agrees = (passable >= threshold) == (letter in "op")
    return letter if agrees else decide(clef, passable, threshold)


def map_letters(reading: MapReading, threshold: float, accuracy: dict[str, float] | None = None) -> None:
    """The region's cells and edge letters, Claude's judgments in, each weighted by Claude's accuracy.

    Args:
        reading: The region's reading.
        threshold: The read style's passable threshold.
        accuracy: Claude's measured accuracy on the style's `edge` and `cell` items, or `None` to let Claude decide.

    The reading records the items Claude left unjudged and the edges between floor that became walls for want of a
    Clef answer.
    """
    assert reading.floors is not None
    accuracy = accuracy or {}
    width, height = reading.key.width, reading.key.height
    cells = [["." if reading.floors[y, x] >= 0.5 else "#" for x in range(width)] for y in range(height)]
    for (cell, side), letter in reading.judged.items():
        if side is None:
            cells[cell[1]][cell[0]] = weighted_cell(letter, accuracy.get("cell"))
    reading.unjudged = [item for item in reading.items if (item.cell, item.side) not in reading.judged]
    reading.unanswered = []
    letters: dict[Edge, str] = {}
    for (x, y), side in reading.key.edges.positions():
        edge = ((x, y), side)
        dx, dy = NEIGHBOUR[side]
        nx, ny = x + dx, y + dy
        here = cells[y][x]
        there = cells[ny][nx] if 0 <= nx < width and 0 <= ny < height else "#"
        occupied = [letter in ".~" for letter in (here, there)]
        answer = reading.clef.get(edge, {})
        if not any(occupied):
            letters[edge] = "-"
        elif all(occupied):
            if (edge[0], side) in reading.judged:
                letters[edge] = weighted_edge(reading.judged[(edge[0], side)], accuracy.get("edge"), answer, threshold)
            elif answer and _door_top(answer):
                letters[edge] = _door_top(answer)  # type: ignore[assignment]
            elif edge in reading.passable:
                letters[edge] = decide(answer, reading.passable[edge], threshold)
            else:
                letters[edge] = "w"
                reading.unanswered.append(edge)
        else:
            doors = {letter: answer.get(letter, 0.0) for letter in "dls"}
            best = max(doors, key=lambda letter: (doors[letter], -BLOCKED.index(letter)))
            beaten = max(answer.get("w", 0.0), answer.get("n", 0.0))
            letters[edge] = best if answer and doors[best] > beaten else "w"
    reading.cells = cells
    reading.letters = letters


# 7. Keys from marks.


def _nearby_keys(outlines: Sequence[Outline], cell: tuple[int, int], keys_by_level: dict[tuple[str, int], list[str]]):  # type: ignore[no-untyped-def]
    out: list[str] = []
    for outline in outlines:
        if any(x - 2 <= cell[0] < x + w + 2 and y - 2 <= cell[1] < y + h + 2 for x, y, w, h in outline.rects):
            out += keys_by_level.get((outline.dungeon, outline.level), [])
    return out


def read_marks(reading: MapReading, page: Page, sources: Sources, workers: int = 1) -> int:
    """The region's keys from its label-sized marks, one mark per key.

    Returns:
        How many marks had no answer: a chunk or the letter question missing in a dry run.
    """
    assert reading.ink is not None and reading.region.registration is not None
    keys_by_level = {(dungeon, number): keys for dungeon, number, keys in levels_on(page)}
    corners = np.array(reading.region.registration.corners, dtype=np.float64)
    found: list[FoundMark] = find_marks(reading.ink.darkness, reading.ink.fill, corners)
    rgb = load_rgb(reading.run_map.screen.image)
    uid = reading.run_map.uid
    marks = [
        (index, mark)
        for index, mark in enumerate(found)
        if 0 <= mark.cell[0] < reading.key.width and 0 <= mark.cell[1] < reading.key.height
    ]
    crops = {index: mark_crop(rgb, reading.region.registration, mark) for index, mark in marks}
    asks, owners = [], []
    for index, mark in marks:
        chunks = [chunk for chunk in key_chunks(_nearby_keys(reading.outlines, mark.cell, keys_by_level)) if chunk]
        about = {"cell": list(mark.cell)}
        for number, chunk in enumerate(chunks):
            asks.append((mark_request(crops[index], chunk, CLEF_MODEL), uid, f"{index}/k{number}", "clef-mark", about))
            owners.append(index)
    answers: dict[int, list[dict[str, float] | None]] = {index: [] for index, _mark in marks}
    for index, answer in zip(owners, sources.decide_many(asks, workers), strict=True):
        answers[index].append(answer)
    unanswered = sum(any(answer is None for answer in got) for got in answers.values())
    complete = {index: [a for a in got if a is not None] for index, got in answers.items() if None not in got}
    letters = [index for index, got in complete.items() if mark_reading(got, None)[0] == "none"]
    letter_asks = [
        (letter_request(crops[index], CLEF_MODEL), uid, f"{index}/L", "clef-mark", {"cell": list(found[index].cell)})
        for index in letters
    ]
    letter_answers = dict(zip(letters, sources.decide_many(letter_asks, workers), strict=True))
    readings: dict[int, tuple[str, float]] = {}
    for index, got in complete.items():
        if index in letter_answers and letter_answers[index] is None:
            unanswered += 1
            continue
        readings[index] = mark_reading(got, letter_answers.get(index))
    kept = one_mark_per_key(readings)
    reading.marks = [
        Mark(text=answer.split(":", 1)[1], cell=cell_key(found[index].cell))
        for index, answer in sorted(kept.items())
        if answer.startswith(("key:", "letter:"))
    ]
    return unanswered


# 8. Halves.


@dataclass(frozen=True)
class Pick:
    """One pair's offset pick."""

    spec: HalvesSpec
    choice: str | None
    """The letter Claude picked, `none`, or `None` when unanswered."""

    truth: str
    columns: tuple[int, ...]
    rows: int

    @property
    def wrong(self) -> bool:
        """Claude picked an offset other than the true one."""
        return self.choice in OFFSET_LETTERS and self.choice != self.truth

    @property
    def joined_columns(self) -> int | None:
        """The picked column offset, or `None` for none or no answer."""
        return self.columns[OFFSET_LETTERS.index(self.choice)] if self.choice in OFFSET_LETTERS else None


def pick_offsets(specs: Sequence[HalvesSpec], settings: FullRunSettings, sources: Sources) -> list[Pick]:
    """Claude's offset pick for each pair of halves."""
    kind = offset_kind(settings.offset_pick.effort, settings.offset_pick.model)
    picks = []
    for spec in specs:
        item = {"first": str(spec.first), "second": str(spec.second), "truth": spec.truth or "none"}
        if spec.first_fit is not None:
            item["first_fit"] = str(spec.first_fit)
        if spec.second_fit is not None:
            item["second_fit"] = str(spec.second_fit)
        pair, columns, truth = load_pair(item, spec.first.parent)
        if spec.truth is None:
            truth = letters_for(columns, spec.joined_columns)
        outcome = sources.read(offset_request(kind, pair), check_offset, spec.id, {"pair": spec.id, "truth": truth})
        choice = outcome["answer"]["choice"] if outcome and outcome.get("answer") else None
        picks.append(Pick(spec=spec, choice=choice, truth=truth, columns=tuple(columns), rows=pair.rows))
    return picks


# The panels.


def _edge_grids(width: int, height: int, letters: dict[Edge, str]) -> EdgeGrids:
    vertical = [["-"] * (width + 1) for _ in range(height)]
    horizontal = [["-"] * width for _ in range(height + 1)]
    for ((x, y), side), letter in letters.items():
        if side == "west":
            vertical[y][x] = letter
        elif side == "east":
            vertical[y][x + 1] = letter
        elif side == "north":
            horizontal[y][x] = letter
        else:
            horizontal[y + 1][x] = letter
    return EdgeGrids(
        vertical=tuple("".join(row) for row in vertical), horizontal=tuple("".join(row) for row in horizontal)
    )


def _partial(reading: MapReading) -> tuple[str, ...]:
    assert reading.floors is not None
    return tuple(
        cell_key((x, y))
        for y in range(reading.key.height)
        for x in range(reading.key.width)
        if 0.25 < reading.floors[y, x] < 0.75
    )


def region_panel(reading: MapReading) -> Panel:
    """One region's panel as read."""
    feet = reading.region.scale.feet or 10
    return Panel(
        id=reading.run_map.screen.id,
        regions=(PanelRegion(region=reading.run_map.screen.id, origin=(0, 0)),),
        scale_feet=feet if feet in (5, 10) else 10,  # type: ignore[arg-type]
        cells=tuple("".join(row) for row in reading.cells),
        edges=_edge_grids(reading.key.width, reading.key.height, reading.letters),
        partial=_partial(reading),
        marks=tuple(reading.marks),
        outlines=tuple(reading.outlines),
        symbols=tuple(reading.symbols),
    )


def joined_panel(first: MapReading, second: MapReading, columns: int, rows: int) -> Panel:
    """Two halves joined into one panel at a column offset and a row shift."""
    w1, h1, w2, h2 = first.key.width, first.key.height, second.key.width, second.key.height
    oy1, oy2 = max(0, -rows), max(0, rows)
    ox2 = w1 + columns
    width, height = ox2 + w2, max(oy1 + h1, oy2 + h2)
    cells = [["#"] * width for _ in range(height)]
    for reading, (ox, oy) in ((first, (0, oy1)), (second, (ox2, oy2))):
        for y, row in enumerate(reading.cells):
            for x, letter in enumerate(row):
                cells[oy + y][ox + x] = letter
    letters: dict[Edge, str] = {}
    for reading, (ox, oy) in ((first, (0, oy1)), (second, (ox2, oy2))):
        for ((x, y), side), letter in reading.letters.items():
            letters[((x + ox, y + oy), side)] = letter
    # The grid's edges, named the way `EdgeGrids.positions` names them, read again where the seam or a gap is.
    joined: dict[Edge, str] = {}
    for y in range(height):
        for x in range(width + 1):
            here = cells[y][x] if x < width else "#"
            west = cells[y][x - 1] if x > 0 else "#"
            name = ((x, y), "west") if x < width else ((x - 1, y), "east")
            seam = w1 <= x <= ox2
            if seam:
                both = here in ".~" and west in ".~"
                joined[name] = "o" if both else ("w" if (here in ".~" or west in ".~") else "-")
            else:
                joined[name] = _shifted(letters, name, (x, y), "west", cells, width, height)
    for y in range(height + 1):
        for x in range(width):
            name = ((x, y), "north") if y < height else ((x, y - 1), "south")
            joined[name] = _shifted(letters, name, (x, y), "north", cells, width, height)

    def shift(outline: Outline, ox: int, oy: int) -> Outline:
        return outline.model_copy(
            update={
                "rects": tuple((x + ox, y + oy, w, h) for x, y, w, h in outline.rects),
                "seeds": tuple(cell_key((c[0] + ox, c[1] + oy)) for c in map(parse_cell, outline.seeds)),
                "open_ground": tuple((x + ox, y + oy, w, h) for x, y, w, h in outline.open_ground),
            }
        )

    def shift_symbol(symbol: Symbol, ox: int, oy: int) -> Symbol:
        if symbol.edge is not None:
            cell, side = parse_edge(symbol.edge)
            return symbol.model_copy(update={"edge": edge_key((cell[0] + ox, cell[1] + oy), side)})
        return symbol.model_copy(
            update={"cells": tuple(cell_key((c[0] + ox, c[1] + oy)) for c in map(parse_cell, symbol.cells))}
        )

    def shift_mark(mark: Mark, ox: int, oy: int) -> Mark:
        c = parse_cell(mark.cell)
        return mark.model_copy(update={"cell": cell_key((c[0] + ox, c[1] + oy))})

    placed = ((first, 0, oy1), (second, ox2, oy2))
    feet = first.region.scale.feet or 10
    return Panel(
        id=f"{first.run_map.screen.id}+{second.run_map.screen.id}",
        regions=(
            PanelRegion(region=first.run_map.screen.id, origin=(0, oy1)),
            PanelRegion(region=second.run_map.screen.id, origin=(ox2, oy2)),
        ),
        scale_feet=feet if feet in (5, 10) else 10,  # type: ignore[arg-type]
        cells=tuple("".join(row) for row in cells),
        edges=_edge_grids(width, height, joined),
        partial=tuple(cell_key((c[0] + ox, c[1] + oy)) for r, ox, oy in placed for c in map(parse_cell, _partial(r))),
        marks=tuple(shift_mark(m, ox, oy) for r, ox, oy in placed for m in r.marks),
        outlines=tuple(shift(o, ox, oy) for r, ox, oy in placed for o in r.outlines),
        symbols=tuple(shift_symbol(s, ox, oy) for r, ox, oy in placed for s in r.symbols),
    )


def _shifted(
    letters: dict[Edge, str],
    name: Edge,
    cell: tuple[int, int],
    side: str,
    cells: list[list[str]],
    width: int,
    height: int,
) -> str:
    """An edge's letter from a half's reading, or from the cells where no half reads it."""
    x, y = cell
    for candidate in (name, (cell, side)):
        if candidate in letters:
            return letters[candidate]
    dx, dy = NEIGHBOUR[side]
    sides = [(x, y), (x + dx, y + dy)]
    occupied = [0 <= cx < width and 0 <= cy < height and cells[cy][cx] in ".~" for cx, cy in sides]
    return "w" if any(occupied) else "-"


def member_reading(
    member: Member, readings: dict[str, MapReading], picks: Sequence[Pick], halves_apart: bool
) -> GeometryTruth:
    """The member's reading document: the answer key's pages and dungeons, each level a shell, with the regions,
    panels, and half pairs read, for the reference resolution script.

    Args:
        member: The member.
        readings: Each of its maps' readings, by region id.
        picks: The offset picks of its half pairs.
        halves_apart: The halves rule keeps every pair apart.

    Returns:
        The document, validated.
    """
    key = member.document()
    regions = []
    for region in key.regions:
        reading = readings.get(region.id)
        regions.append(reading.region if reading is not None else region)
    panels: list[Panel] = []
    pairs: list[HalfPair] = []
    paired: set[str] = set()
    for pick in picks:
        if pick.spec.regions is None:
            continue
        first, second = (readings.get(region) for region in pick.spec.regions)
        if first is None or second is None or first.style is None or second.style is None:
            continue
        columns = None if halves_apart else pick.joined_columns
        if columns is not None:
            panels.append(joined_panel(first, second, columns, pick.rows))
            paired.update(pick.spec.regions)
        pairs.append(HalfPair(regions=pick.spec.regions, joined=columns is not None, rows=pick.rows, columns=columns))
    for region_id, reading in readings.items():
        if region_id in paired or reading.style is None:
            continue
        panels.append(region_panel(reading))
    document = reading_of(key).model_copy(
        update={"regions": tuple(regions), "panels": tuple(panels), "half_pairs": tuple(pairs)}
    )
    return GeometryTruth.model_validate(document.model_dump())


def unanswered_edges(reading: MapReading) -> int:
    """Edges between two half-rule floor cells that Clef didn't answer: possible items a dry run can't count."""
    assert reading.floors is not None
    floor = {(x, y) for y in range(reading.key.height) for x in range(reading.key.width) if reading.floors[y, x] >= 0.5}
    count = 0
    for (x, y), side in reading.key.edges.positions():
        dx, dy = NEIGHBOUR[side]
        if (x, y) in floor and (x + dx, y + dy) in floor and ((x, y), side) not in reading.clef:
            count += 1
    return count


def ceil_div(a: int, b: int) -> int:
    """`a / b` rounded up."""
    return math.ceil(a / b) if a else 0
