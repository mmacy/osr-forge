"""The key's reading layer: the settled cells, edges, labels, symbols, and outlines of a map, as geometry truth.

`assemble` builds one map's `Region` and `Panel` from the table of readings and the final values, with the
outlines from the whole-map read, and writes `<map>/reading.json`: a geometry truth document whose levels are
shells for the resolver to fill. `combine` joins the region documents of one corpus member, resolves them with
the member's caches through `resolve.py`, and writes the key, with its provenance beside it.

The panel's letters come from the readers that agreed, or from the settler where they disagreed, with these
derivations after settling: an edge touching no floor or water is `-`; an edge between floor and rock is `w`
unless the settler put a door there; a cell no window read falls back to the classifier's letter, and the log
counts those.
"""

import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from osrforge.tracing.truth import (
    SYMBOL_KINDS,
    Compass,
    Dungeon,
    EdgeGrids,
    GeometryTruth,
    Legend,
    Level,
    Mark,
    Outline,
    Page,
    PageImage,
    Panel,
    PanelRegion,
    Region,
    Scale,
    Symbol,
    canonical_form,
    parse_cell,
    parse_edge,
    save_geometry_truth,
)

from .keyspec import KeySpec, MapContext
from .letters import final_cells, final_edges, outline_state
from .marks import wholemap_answer
from .readings import Items, classifier_reading, load_items
from .settler import final_values

__all__ = ["assemble", "combine", "panel_letters", "rects_of"]


def _parse_value_mark(value: str) -> tuple[str, str] | None:
    if value.strip().lower() == "none":
        return None
    text, _at, cell = value.rpartition("@")
    parse_cell(cell)
    return text, cell


def _parse_value_symbol(value: str) -> dict[str, Any] | None:
    if value.strip().lower() == "none":
        return None
    body, _, direction = value.partition("|")
    kind, _at, place = body.partition("@")
    if kind not in SYMBOL_KINDS:
        return None
    if kind == "cave_mouth":
        parse_edge(place)
        return {"kind": kind, "cells": [], "edge": place, "direction": None}
    cells = sorted({text for text in place.split(";") if text}, key=lambda t: parse_cell(t)[::-1])
    return {"kind": kind, "cells": cells, "edge": None, "direction": direction or None}


def _final_marks(
    items: Items, final: dict[str, dict[str, Any]], cells: list[list[str]], log: dict[str, Any]
) -> list[Mark]:
    marks: list[Mark] = []
    dropped = 0
    for index, mark in enumerate(items.marks):
        if mark["status"] == "agreed":
            text, cell = mark["text"], mark["reading1"][0]
        else:
            cells_named = mark["reading1"] or mark["reading2"]
            call = final.get(f"m:{cells_named[0]}|{index}")
            if call is None:
                dropped += 1
                continue
            parsed = _parse_value_mark(call["value"])
            if parsed is None:
                continue
            text, cell = parsed
        x, y = parse_cell(cell)
        if not (0 <= x < items.width and 0 <= y < items.height):
            dropped += 1
            continue
        edge = mark.get("on_door")
        if edge is not None:
            try:
                edge_cell, _side = parse_edge(edge)
            except ValueError:
                edge = None
            else:
                if f"{edge_cell[0]},{edge_cell[1]}" != cell:
                    edge = None
        leader = mark.get("leader_to")
        if leader is not None:
            lx, ly = parse_cell(leader)
            if not (0 <= lx < items.width and 0 <= ly < items.height):
                leader = None
            elif cells[ly][lx] == "#":
                # A leader line ends on floor: a read end on rock moves to the nearest floor cell within one cell.
                snapped = _nearest_floor(cells, (lx, ly))
                log.setdefault("leaders_snapped", []).append(f"{text}: {leader} -> {snapped}")
                leader = snapped
        marks.append(Mark(text=text, cell=cell, edge=edge, leader_to=leader))
    log["marks_dropped"] = dropped
    return _unique_marks(marks)


def _nearest_floor(cells: list[list[str]], cell: tuple[int, int]) -> str | None:
    """The nearest floor cell within one cell of a cell, in north-then-west order on a tie, or `None`."""
    x, y = cell
    candidates = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            nx, ny = x + dx, y + dy
            if 0 <= ny < len(cells) and 0 <= nx < len(cells[0]) and cells[ny][nx] != "#":
                candidates.append((max(abs(dx), abs(dy)), ny, nx))
    if not candidates:
        return None
    _distance, ny, nx = min(candidates)
    return f"{nx},{ny}"


def _unique_marks(marks: list[Mark]) -> list[Mark]:
    seen = set()
    out = []
    for mark in marks:
        key = (mark.text, mark.cell)
        if key in seen:
            continue
        seen.add(key)
        out.append(mark)
    return out


def _final_symbols(items: Items, final: dict[str, dict[str, Any]], width: int, height: int) -> list[Symbol]:
    symbols: list[Symbol] = []
    for index, item in enumerate(items.symbols):
        one = item["reading1"] or item["reading2"]
        place = one["cells"][0] if one["cells"] else parse_edge(one["edge"])[0]
        place_text = place if isinstance(place, str) else f"{place[0]},{place[1]}"
        call = final.get(f"s:{place_text}|{index}")
        if call is not None:
            # A settled symbol, or an agreed one the adversarial pass challenged and the settler decided again.
            symbol = _parse_value_symbol(call["value"])
            if symbol is None:
                continue
        elif item["status"] == "agreed":
            symbol = item["reading1"]
        else:
            continue
        inside = all(0 <= parse_cell(c)[0] < width and 0 <= parse_cell(c)[1] < height for c in symbol["cells"])
        if symbol["kind"] == "cave_mouth":
            (x, y), _side = parse_edge(symbol["edge"])
            inside = 0 <= x < width and 0 <= y < height
        if not inside:
            continue
        symbols.append(
            Symbol(
                kind=symbol["kind"],
                cells=tuple(symbol["cells"]),
                edge=symbol.get("edge"),
                direction=symbol.get("direction"),
            )
        )
    unique = {}
    for symbol in symbols:
        unique[(symbol.kind, symbol.cells, symbol.edge)] = symbol
    return list(unique.values())


def rects_of(cells: set[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """Cover a set of cells with rectangles: maximal runs along each row, merged down where runs line up."""
    rows: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for x, y in sorted(cells, key=lambda c: (c[1], c[0])):
        runs = rows[y]
        if runs and runs[-1][1] == x:
            runs[-1] = (runs[-1][0], x + 1)
        else:
            runs.append((x, x + 1))
    rects: list[list[int]] = []
    open_runs: dict[tuple[int, int], list[int]] = {}
    for y in sorted(rows):
        next_open: dict[tuple[int, int], list[int]] = {}
        for x0, x1 in rows[y]:
            rect = open_runs.get((x0, x1))
            if rect is not None and rect[1] + rect[3] == y:
                rect[3] += 1
            else:
                rect = [x0, y, x1 - x0, 1]
                rects.append(rect)
            next_open[(x0, x1)] = rect
        open_runs = next_open
    return [tuple(rect) for rect in rects]  # type: ignore[misc]


def _outline_cells(rects: list[list[int]], width: int, height: int) -> set[tuple[int, int]]:
    cells = set()
    for x, y, w, h in rects:
        for cy in range(max(0, y), min(height, y + h)):
            for cx in range(max(0, x), min(width, x + w)):
                cells.add((cx, cy))
    return cells


def _outlines(
    ctx: MapContext,
    cells: list[list[str]],
    edges: EdgeGrids,
    marks: list[Mark],
    final: dict[str, dict[str, Any]],
    log: dict[str, Any],
) -> list[Outline]:
    state = outline_state(ctx, cells, edges, final)
    log["outline_crossings"] = [f"{c['level']} {c['cell'][0]},{c['cell'][1]}:{c['side']}" for c in state.crossings]
    log["open_ground_unsettled"] = len(state.open_ground_items)
    outlines: list[Outline] = []
    for level, cells_of in state.owned.items():
        dungeon, number_text = level.rsplit("/", 1)
        number = int(number_text)
        seeds = [text for text in state.seeds[level] if _is_floor(cells, text) and parse_cell(text) in cells_of]
        if not seeds:
            level_marks = [
                mark.cell for mark in marks if parse_cell(mark.cell) in cells_of and _is_floor(cells, mark.cell)
            ]
            seeds = (
                level_marks[:1]
                or [f"{x},{y}" for x, y in sorted(cells_of, key=lambda c: (c[1], c[0])) if cells[y][x] != "#"][:1]
            )
        if not seeds:
            log.setdefault("levels_without_floor", []).append(level)
            continue
        open_ground = [rect for rect in state.open_ground[level] if rect[2] > 0]
        outlines.append(
            Outline(
                dungeon=dungeon,
                level=number,
                rects=tuple(rects_of(cells_of)),
                seeds=tuple(seeds),
                open_ground=tuple(open_ground),
            )
        )
    return outlines


def _is_floor(cells: list[list[str]], text: str) -> bool:
    x, y = parse_cell(text)
    return 0 <= y < len(cells) and 0 <= x < len(cells[0]) and cells[y][x] != "#"


def _closed_mouths(symbols: list[Symbol], cells: list[list[str]], edges: EdgeGrids) -> list[str]:
    """The cave mouths whose edge isn't passable or whose cells aren't both floor, each with what closes it.

    A cave mouth is an outer opening, so a mouth read as a wall, or with rock on either side, is a reading to
    look at: on a cave map the entrance letter printed in the mouth reads as ink.
    """
    closed: list[str] = []
    for symbol in symbols:
        if symbol.kind != "cave_mouth" or symbol.edge is None:
            continue
        cell, side = parse_edge(symbol.edge)
        letter = edges.at(cell, side)
        x, y = cell
        beyond = {"north": (x, y - 1), "south": (x, y + 1), "west": (x - 1, y), "east": (x + 1, y)}[side]
        faults = []
        if letter not in "op":
            faults.append(f"edge {letter}")
        for name, (cx, cy) in (("inside", cell), ("beyond", beyond)):
            if 0 <= cy < len(cells) and 0 <= cx < len(cells[0]) and cells[cy][cx] == "#":
                faults.append(f"{name} rock")
        if faults:
            closed.append(f"{symbol.edge}: {', '.join(faults)}")
    return closed


def panel_letters(ctx: MapContext) -> tuple[Panel, Region, dict[str, Any]]:
    """Build the map's panel and region from the table, the final values, and the whole-map read."""
    items = load_items(ctx)
    final = final_values(ctx) if (ctx.key.directory / "final.json").exists() else {}
    log: dict[str, Any] = {"settled_calls": len(final)}
    cells = final_cells(items, final, log)
    edges = final_edges(items, cells, final, log)
    marks = _final_marks(items, final, cells, log)
    symbols = _final_symbols(items, final, items.width, items.height)
    log["mouths_closed"] = _closed_mouths(symbols, cells, edges)
    outlines = _outlines(ctx, cells, edges, marks, final, log)
    reading = classifier_reading(ctx)
    whole = wholemap_answer(ctx) or {}
    partial = [text for text in items.partial if _in_grid(text, items.width, items.height)]
    cave = [text for text in items.cave if _in_grid(text, items.width, items.height)]
    panel = Panel(
        id=ctx.key.id,
        regions=(PanelRegion(region=ctx.key.id, origin=(0, 0)),),
        scale_feet=ctx.spec.scale_feet,  # type: ignore[arg-type]
        cells=tuple("".join(row) for row in cells),
        edges=edges,
        partial=tuple(partial),
        cave=tuple(cave),
        marks=tuple(marks),
        outlines=tuple(outlines),
        symbols=tuple(symbols),
    )
    legend_symbols = tuple(kind for kind in whole.get("legend_symbols", []) if kind in SYMBOL_KINDS)
    legend_printed = bool(ctx.spec.legend_printed or (whole.get("legend_printed") and ctx.key.legend_from_page is None))
    region = reading.region.model_copy(
        update={
            "style": ctx.spec.truth_style,
            "scale": Scale(
                feet=ctx.spec.scale_feet,
                printed=ctx.spec.scale_printed,
                text=whole.get("scale_text") if ctx.spec.scale_printed else None,
            ),
            "compass": Compass(north=ctx.spec.north, printed=ctx.spec.compass_printed),  # type: ignore[arg-type]
            "legend": Legend(printed=legend_printed, from_page=ctx.key.legend_from_page, symbols=legend_symbols),
            "cut_off": tuple(side for side in whole.get("cut_off", []) if side in ("top", "right", "bottom", "left")),
        }
    )
    if whole:
        for field_name, mine, theirs in (
            ("north", ctx.spec.north, whole.get("north")),
            ("compass_printed", ctx.spec.compass_printed, whole.get("compass_printed")),
            ("scale_printed", ctx.spec.scale_printed, whole.get("scale_printed")),
            ("feet", ctx.spec.scale_feet, whole.get("feet_per_square")),
        ):
            if mine != theirs:
                log.setdefault("wholemap_disagrees", []).append(f"{field_name}: spec {mine}, whole-map read {theirs}")
    return panel, region, log


def _in_grid(text: str, width: int, height: int) -> bool:
    x, y = parse_cell(text)
    return 0 <= x < width and 0 <= y < height


def _image_relative(ctx: MapContext, document_dir: Path) -> str:
    image = Path(ctx.spec.image).resolve()
    return os.path.relpath(image, document_dir.resolve())


def assemble(ctx: MapContext, out: Path | None = None) -> dict[str, Any]:
    """Write the map's reading document and return the assembly log."""
    panel, region, log = panel_letters(ctx)
    out = out or ctx.key.directory / "reading.json"
    region = region.model_copy(update={"image": _image_relative(ctx, ctx.key.key.parent)})
    page = Page(number=ctx.spec.page, images=(PageImage(file=region.image),))
    dungeons = _shell_dungeons(ctx.key)
    document = GeometryTruth(pages=(page,), regions=(region,), panels=(panel,), dungeons=dungeons)
    save_geometry_truth(out, document)
    log["written"] = str(out)
    (ctx.key.directory / "assemble.log.json").write_text(json.dumps(log, indent=1) + "\n")
    return log


def _shell_dungeons(key: KeySpec) -> tuple[Dungeon, ...]:
    """Every dungeon of the survey as a shell: each level a fallback the resolver fills in."""
    if key.survey is None:
        by_dungeon: dict[str, list[int]] = defaultdict(list)
        for dungeon, number in key.levels:
            by_dungeon[dungeon].append(number)
        return tuple(
            Dungeon(
                id=dungeon,
                name=dungeon,
                levels=tuple(Level(number=n, verdict="fallback", reason="reading only") for n in sorted(numbers)),
            )
            for dungeon, numbers in by_dungeon.items()
        )
    survey = json.loads(key.survey.read_text())
    return tuple(
        Dungeon(
            id=dungeon["id"],
            name=dungeon["name"],
            levels=tuple(
                Level(number=level["number"], verdict="fallback", reason="reading only") for level in dungeon["levels"]
            ),
        )
        for dungeon in survey["dungeons"]
    )


def combine(
    keys: list[KeySpec], out: Path, resolve_with: Path | None, cases: list[str] | None = None
) -> dict[str, Any]:
    """Join the region documents of one member, resolve them, and write the key.

    Args:
        keys: The member's map entries, each with a `reading.json` assembled.
        out: The key's path.
        resolve_with: The directory whose `stages/` holds the member's caches, or `None` to write the reading
            layer alone.

    Returns:
        A log: the regions joined and the resolver's outcome.
    """
    from .resolve import ConventionGap, load_caches, resolve

    pages: dict[int, list[PageImage]] = defaultdict(list)
    regions: list[Region] = []
    panels: list[Panel] = []
    dungeons = _shell_dungeons(keys[0])
    for key in keys:
        document = GeometryTruth.model_validate(json.loads((key.directory / "reading.json").read_text()))
        for page in document.pages:
            for image in page.images:
                if image not in pages[page.number]:
                    pages[page.number].append(image)
        regions.extend(document.regions)
        panels.extend(document.panels)
    reading = GeometryTruth(
        pages=tuple(Page(number=number, images=tuple(images)) for number, images in sorted(pages.items())),
        regions=tuple(regions),
        panels=tuple(panels),
        dungeons=dungeons,
    )
    log: dict[str, Any] = {"regions": [region.id for region in regions]}
    if resolve_with is None:
        save_geometry_truth(out, reading)
        log["resolved"] = False
        log["provenance"] = str(write_provenance(keys, out, cases or [], False))
        return log
    survey, contents, mapread = load_caches(resolve_with)
    try:
        resolved = resolve(reading, survey, contents, mapread)
    except ConventionGap as gap:
        log["open_case"] = str(gap)
        save_geometry_truth(out, reading)
        return log
    save_geometry_truth(out, canonical_form(resolved))
    log["resolved"] = True
    log["provenance"] = str(write_provenance(keys, out, cases or [], True))
    log["levels"] = [
        f"{d.id}/{level.number}: {level.verdict}" + (f" ({level.reason})" if level.reason else "")
        for d in resolved.dungeons
        for level in d.levels
    ]
    return log


def map_figures(key: KeySpec) -> dict[str, Any]:
    """One map's figures for the provenance: its spend by stage and its counts of items, disputes, and calls."""
    from .batches import BatchStore

    figures: dict[str, Any] = {"map": key.id}
    claude = 0.0
    for stage in ("windows", "marks", "wholemap", "settle", "adversary", "resettle"):
        store = BatchStore(key.directory / stage)
        spent = store.spent()
        if spent:
            figures[f"{stage}_usd"] = spent
            claude += spent
    figures["claude_usd"] = round(claude, 6)
    clef = key.directory / "clef" / "answers.jsonl"
    if clef.exists():
        rows = [json.loads(line) for line in clef.read_text().splitlines() if line.strip()]
        figures["clef_requests"] = len(rows)
        figures["clef_usd"] = round(sum(row["cost_usd"] for row in rows), 6)
        readers = sorted({row.get("model", "hosted clef on Workers AI") for row in rows})
        figures["clef_reader"] = ", ".join(readers)
    hosted = key.directory / "clef" / "answers-hosted.jsonl"
    if hosted.exists():
        figures["clef_hosted_record"] = sum(1 for line in hosted.read_text().splitlines() if line.strip())
    items_file = key.directory / "items.json"
    if items_file.exists():
        items = json.loads(items_file.read_text())
        for kind in ("cells", "edges"):
            table = items[kind]
            figures[kind] = len(table)
            figures[f"{kind}_disputed"] = sum(1 for item in table.values() if item["status"] == "disputed")
        figures["marks"] = len(items["marks"])
        figures["marks_disputed"] = sum(1 for item in items["marks"] if item["status"] == "disputed")
        figures["symbols"] = len(items["symbols"])
        figures["symbols_disputed"] = sum(1 for item in items["symbols"] if item["status"] == "disputed")
    final_file = key.directory / "final.json"
    if final_file.exists():
        final = json.loads(final_file.read_text())
        figures["settled_calls"] = sum(1 for call in final.values() if call.get("settled") is not None)
        figures["adversarial_checks"] = sum(1 for call in final.values() if "adversarial" in call)
        figures["challenged"] = sum(1 for call in final.values() if call.get("challenged"))
        figures["changed_by_challenge"] = sum(
            1 for call in final.values() if call.get("challenged") and call.get("value") != call.get("settled")
        )
    return figures


def write_provenance(keys: list[KeySpec], out: Path, cases: list[str], resolved: bool) -> Path:
    """Write `geometry-provenance.yaml` beside a key: what made it, which checks ran, what it cost, and the cases
    it depends on."""
    figures = [map_figures(key) for key in keys]
    claude = round(sum(f["claude_usd"] for f in figures), 4)
    clef = round(sum(f.get("clef_usd", 0.0) for f in figures), 4)
    by_reader: dict[str, list[str]] = {}
    for f in figures:
        if f.get("clef_requests"):
            by_reader.setdefault(f["clef_reader"], []).append(f["map"])
    clef_unread = [f["map"] for f in figures if not f.get("clef_requests")]
    parts = [
        f"{reader} answered the edges of {', '.join(maps)} as a third reading"
        for reader, maps in sorted(by_reader.items())
    ]
    if clef_unread:
        parts.append(
            f"no Clef read {', '.join(clef_unread)}, whose readers were the two alone; its answers can be added later "
            "with one more settling round"
        )
    hosted_record = [f["map"] for f in figures if f.get("clef_hosted_record")]
    if hosted_record:
        parts.append(
            f"hosted Clef's earlier answers on {', '.join(hosted_record)} are kept as a record beside the key's"
        )
    clef_text = "; ".join(parts) + "." if parts else "No Clef reading."
    lines = [
        "# The provenance of geometry.json beside this file. Written by tools/answer_keys/assemble.py.",
        f'authored: "{date.today().isoformat()}"',
        "instrument: >-",
        "  Two independent readings per map: Claude Sonnet 5.5 (claude-sonnet-5-5) at high effort reading 12 by 12",
        "  cell windows with grid ticks through tools/answer_keys/reader.py, which isn't the tracing stage's code,",
        "  and the ink classifier tools/answer_keys/classify.py. " + clef_text,
        "  Each disagreement was settled in a fresh Sonnet request at high effort that carried a zoomed crop, a",
        "  wider crop, and the readings that disagreed. The resolution layer",
        "  "
        + (
            "comes from tools/answer_keys/resolve.py by docs/tracing-conventions.md."
            if resolved
            else "is not filled in."
        ),
        "verified: >-",
        "  An adversarial pass of fresh Sonnet requests at high effort rechecked every settled call and the",
        "  agreed edges in caves, at diagonals, and around keys; a challenged call was settled once more in a",
        "  fresh request with the challenge among its readings, and that value is the key's. Owner sample:",
        "  PENDING, in the sample app (tools/answer_keys/sample_app.py).",
        f"spend_usd: {{claude: {claude:.4f}, clef: {clef:.4f}}}",
        "maps:",
    ]
    for f in figures:
        lines.append(f"  - {json.dumps(f)}")
    lines.append("cases:")
    for case in cases:
        lines.append(f"  - {case}")
    if not cases:
        lines.append("  []")
    path = out.with_name("geometry-provenance.yaml")
    path.write_text("\n".join(lines) + "\n")
    return path
