"""The second reading of labels and symbols, and the whole-map read of outlines, legend, and compass.

The window read (`reader.py`) is the first reading of every label and symbol. The second comes from this
module's *marks pass*: windows of the same size laid half a window across and half a window down from the
first tiling, so every seam of the first tiling runs through the middle of a marks window, read at medium
effort with a prompt that asks for labels and symbols alone. A label the two readings place in different
cells, or that one reading has and the other lacks, is a disagreement for the settler.

The *whole-map read* shows the region once, downscaled with ticks every few cells, with the levels the survey
draws on it and their keys, and asks for each level's outline rectangles, seed cells, and open-ground
rectangles, the legend's entries from the symbol vocabulary, the compass, and the scale. Its answer is
checked against the settled reading afterwards: an outline border that crosses floor goes to the settler.
"""

import base64
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from osrforge.tracing.truth import SYMBOL_KINDS, parse_cell, parse_edge

from .batches import BatchStore
from .keyspec import MapContext
from .reader import MODEL, answer_text_of, normalize_addresses, normalize_edge, parse_answer_text, retry_params
from .tiles import GUTTER, TICK_COLOUR, Window, _font, render_window, window_starts

__all__ = ["collect_marks", "collect_wholemap", "marks_windows", "submit_marks", "submit_wholemap"]

MARKS_EFFORT = "medium"
MARKS_MAX_TOKENS = 8000
WHOLEMAP_EFFORT = "high"
WHOLEMAP_MAX_TOKENS = 16000
WHOLEMAP_LONG_EDGE = 1568

MARKS_SYSTEM = (
    "You read printed tabletop dungeon maps for a program that rebuilds each level on a square grid of cells. "
    "Every request shows a crop of one map, magnified, and names a window of cells inside it. Your job in this "
    "request is only the labels and the symbols printed inside the window. Answer only through the structured "
    "output. Don't describe the adventure.\n\n"
    "The grid. Red tick marks with red numbers are drawn in white gutters along all four sides of the crop. Each "
    "tick marks a grid line, and its number is the column (along the top and the bottom) or row (down the left "
    "and the right) of the cell that starts at that line. The last tick in each direction is the window's far "
    "edge and has no number. The "
    "crop shows one cell of map outside the window on every side; a label or symbol whose centre is in that "
    "margin belongs to another window, so leave it out.\n\n"
    "Labels. Report every label printed inside the window in marks: a room key (a number such as 12, or a number "
    "with a letter such as 6a), a letter on its own (a, b), a lair letter (A, B), or a word (rubble, stream). "
    "Give its text exactly as printed and the cell under the centre of its text, by absolute column and row as "
    "'x,y'. When two keys are printed stacked one above the other in one place, report them as one mark with "
    "the texts joined by / from top to bottom. When a label sits on a door, give that door's edge in on_door as "
    "'x,y:side' (the cell and the side of it the door is on). When a leader line runs from the label to a cell, "
    "give that cell in leader_to.\n\n"
    "Symbols. Report every map symbol inside the window in symbols, with its kind from this list: stairs, ladder, "
    "trapdoor, hinged_floor, chute, slide, pit, cave_mouth, pillar, statue, pool, steps, bridge, footing. A "
    "cave_mouth stands on the edge where a cave opens to the outside, given as 'x,y:side', and has no cells; "
    "every other kind covers one or more cells. For stairs, a ladder, a trapdoor, or a chute give direction up "
    "or down when the map shows one, else null. A legend entry that isn't in the list, such as a trap or an "
    "alcove, isn't a symbol here.\n\n"
    "Keep notes under 60 words."
)

MARKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "marks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "cell": {"type": "string"},
                    "on_door": {"type": ["string", "null"]},
                    "leader_to": {"type": ["string", "null"]},
                },
                "required": ["text", "cell", "on_door", "leader_to"],
                "additionalProperties": False,
            },
        },
        "symbols": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "cells": {"type": "array", "items": {"type": "string"}},
                    "edge": {"type": ["string", "null"]},
                    "direction": {"type": ["string", "null"]},
                },
                "required": ["kind", "cells", "edge", "direction"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["marks", "symbols", "notes"],
    "additionalProperties": False,
}


def marks_windows(columns: int, rows: int, size: int = 12) -> list[Window]:
    """The marks pass's windows: the first tiling moved half a window across and down, plus the first row and
    column of the first tiling, so every cell is covered.

    Args:
        columns: The region's width.
        rows: Its height.
        size: The window size.

    Returns:
        The windows in row-major order.
    """
    half = size // 2
    width = min(size, columns)
    height = min(size, rows)

    def starts(length: int) -> list[int]:
        if length <= size:
            return [0]
        shifted = [start + half for start in window_starts(length - half, size)]
        return sorted({0, *(min(start, length - size) for start in shifted)})

    return [Window(x0, y0, width, height) for y0 in starts(rows) for x0 in starts(columns)]


def marks_params(crop_png_base64: str, window: Window, style_note: str) -> dict[str, Any]:
    """The request parameters for one marks window."""
    x1 = window.x0 + window.columns - 1
    y1 = window.y0 + window.rows - 1
    ask = (
        f"This map: {style_note}\n\n"
        f"The window is columns {window.x0} to {x1} and rows {window.y0} to {y1}. Report every label and symbol "
        f"whose centre lies in it, by absolute column and row."
    )
    return {
        "model": MODEL,
        "max_tokens": MARKS_MAX_TOKENS,
        "system": [{"type": "text", "text": MARKS_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": crop_png_base64}},
                    {"type": "text", "text": ask},
                ],
            }
        ],
        "output_config": {"format": {"type": "json_schema", "schema": MARKS_SCHEMA}, "effort": MARKS_EFFORT},
    }


def check_marks(answer: dict[str, Any], window: Window) -> list[str]:
    """What is malformed in a marks answer, after loosely written edge addresses are rewritten in place."""
    normalize_addresses(answer)
    faults: list[str] = []

    def inside(text: str, what: str) -> None:
        try:
            cell = parse_cell(text)
        except ValueError:
            faults.append(f"{what} names {text!r}, which isn't a cell 'x,y'")
            return
        if not window.contains(cell):
            faults.append(f"{what} names cell {text}, which is outside the window")

    for mark in answer.get("marks", []):
        inside(mark.get("cell", ""), f"mark {mark.get('text')!r}")
        for key in ("on_door", "leader_to"):
            value = mark.get(key)
            if value is None:
                continue
            try:
                parse_edge(value) if key == "on_door" else parse_cell(value)
            except ValueError:
                faults.append(f"mark {mark.get('text')!r} has a {key} that isn't an address: {value!r}")
    for symbol in answer.get("symbols", []):
        kind = symbol.get("kind")
        if kind not in SYMBOL_KINDS:
            faults.append(f"symbol kind {kind!r} isn't in the list")
            continue
        if kind == "cave_mouth":
            if symbol.get("cells") and normalize_edge(symbol.get("edge")) is not None:
                symbol["cells"] = []
            try:
                cell, _side = parse_edge(symbol.get("edge") or "")
            except ValueError:
                faults.append(f"cave_mouth edge {symbol.get('edge')!r} isn't an edge 'x,y:side'")
            else:
                if not window.contains(cell):
                    faults.append(f"cave_mouth edge {symbol.get('edge')} is outside the window")
        else:
            if not symbol.get("cells"):
                faults.append(f"a {kind} needs at least one cell")
            for text in symbol.get("cells", []):
                inside(text, kind)
        if symbol.get("direction") not in (None, "up", "down"):
            faults.append(f"direction {symbol.get('direction')!r} must be up, down, or null")
    return faults


def submit_marks(ctx: MapContext, store: BatchStore, client: Any, ledger: Any) -> str | None:
    """Render the marks windows, add their jobs, and send the pending ones."""
    crops = ctx.key.directory / "marks" / "crops"
    for window in marks_windows(ctx.fit.columns, ctx.fit.rows):
        crop = crops / f"{window.id}.png"
        if not crop.exists():
            render_window(ctx.rgb, ctx.fit, window, crop)
        data = base64.b64encode(crop.read_bytes()).decode()
        meta = {"map": ctx.key.id, "window": [window.x0, window.y0, window.columns, window.rows]}
        store.add_job(f"{ctx.key.id}__m__{window.id}", marks_params(data, window, ctx.key.style_note), meta)
    return store.submit(client, ledger, f"marks {ctx.key.id}")


def _collect_checked(store: BatchStore, client: Any, ledger: Any, parsed_dir: Path, checker, name_of) -> dict[str, int]:
    """Collect a store's answers, write the well-formed ones, and queue one retry for each malformed one."""
    counts = store.collect(client, ledger)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    result = {"parsed": 0, "retries": 0, "open": counts["open"], "errored": counts["errored"]}
    for custom_id in store.job_ids():
        answer = store.answer(custom_id)
        if answer is None:
            continue
        job = store.job(custom_id)
        out = parsed_dir / f"{name_of(job['meta'])}.json"
        if out.exists():
            continue
        text = answer_text_of(answer["message"])
        document = parse_answer_text(text)
        faults = ["the answer isn't a JSON object"] if document is None else checker(document, job["meta"])
        if answer.get("stop_reason") in ("max_tokens", "refusal"):
            faults.insert(0, f"the answer stopped with {answer['stop_reason']}")
        if not faults and document is not None:
            out.write_text(json.dumps({**document, "job": custom_id, "cost_usd": answer.get("cost_usd")}, indent=1))
            result["parsed"] += 1
            continue
        if job["meta"].get("retry_of") is not None:
            # The retry failed too: one fresh attempt from the original request, then the window is given up.
            attempt = job["meta"].get("attempt", 1)
            with (store.directory / "failed.jsonl").open("a") as handle:
                handle.write(json.dumps({"job": custom_id, "faults": faults, "attempt": attempt}) + "\n")
            if attempt < 2:
                base_id = job["meta"]["retry_of"]
                fresh_id = f"{base_id}__a2"
                if not (store.jobs / f"{fresh_id}.json").exists():
                    base = store.job(base_id)
                    store.add_job(fresh_id, base["params"], {**base["meta"], "attempt": 2, "retry_of": None})
                    result["retries"] += 1
            continue
        retry_id = f"{custom_id}__r"
        if (store.jobs / f"{retry_id}.json").exists():
            continue
        with (store.directory / "malformed.jsonl").open("a") as handle:
            handle.write(json.dumps({"job": custom_id, "faults": faults, "next": retry_id}) + "\n")
        store.add_job(retry_id, retry_params(job["params"], text, faults), {**job["meta"], "retry_of": custom_id})
        result["retries"] += 1
    return result


def collect_marks(ctx: MapContext, store: BatchStore, client: Any, ledger: Any) -> dict[str, int]:
    """Collect the marks answers into `marks/parsed/<window>.json`."""

    def checker(document: dict[str, Any], meta: dict[str, Any]) -> list[str]:
        x0, y0, columns, rows = meta["window"]
        return check_marks(document, Window(x0, y0, columns, rows))

    def name_of(meta: dict[str, Any]) -> str:
        x0, y0, _columns, _rows = meta["window"]
        return f"{x0}-{y0}"

    return _collect_checked(store, client, ledger, ctx.key.directory / "marks" / "parsed", checker, name_of)


def parsed_marks(ctx: MapContext) -> list[dict[str, Any]]:
    """Every parsed marks window of a map, each with its window."""
    parsed_dir = ctx.key.directory / "marks" / "parsed"
    out = []
    if parsed_dir.exists():
        for path in sorted(parsed_dir.glob("*.json")):
            document = json.loads(path.read_text())
            x0, y0 = (int(v) for v in path.stem.split("-"))
            document["window"] = [x0, y0]
            out.append(document)
    return out


WHOLEMAP_SYSTEM = (
    "You read printed tabletop dungeon maps for a program that traces each dungeon level from its printed map. "
    "Each request shows one map region whole, downscaled, with red tick marks and numbers in a white gutter "
    "along the top and the left: each tick marks a grid line, numbered by the column or row of the cell that "
    "starts at it, every few cells. Answer only through the structured output. Don't describe the adventure.\n\n"
    "Levels and outlines. The request lists the levels the map draws, each with the room keys its text gives. "
    "For each level, give its outline: one or more rectangles of cells, as [x, y, width, height] in grid cells, "
    "whose union covers every floor cell of that level and as little as possible of any other level. Where two "
    "levels' floors meet, draw the border between their outlines along a wall, a door, a cave mouth, or a "
    "printed line, never across open floor. A rectangle may reach past the drawing onto blank page or rock; "
    "that costs nothing. Also give two or three seed cells well inside the level's floor, far from any other "
    "level, and the rectangles of open ground inside the outline: yards, courtyards, and the ground between "
    "separate buildings that the party walks on, as [x, y, width, height]. Give an empty list when the level "
    "has no open ground.\n\n"
    "Legend. If the page prints a legend, list the entries of this vocabulary that the legend explains: stairs, "
    "ladder, trapdoor, hinged_floor, chute, slide, pit, cave_mouth, pillar, statue, pool, steps, bridge, "
    "footing, locked_door. A legend entry for stairs or steps counts as stairs; a trap door counts as "
    "trapdoor; a cave entrance counts as cave_mouth; a locked door counts as locked_door. Entries outside the "
    "vocabulary (a trap, an alcove, trees, grass, contour lines) aren't listed.\n\n"
    "Compass and scale. Say whether the page prints a compass and which page side its north points to (top, "
    "right, bottom, left), and whether it prints a scale statement, with the statement exactly as printed and "
    "the feet per square. Say which edges of the image cut the drawing off, where floor runs off the image's "
    "edge onto a facing page.\n\n"
    "Keep notes under 80 words."
)

WHOLEMAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "levels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dungeon": {"type": "string"},
                    "level": {"type": "integer"},
                    "rects": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
                    "seeds": {"type": "array", "items": {"type": "string"}},
                    "open_ground": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
                },
                "required": ["dungeon", "level", "rects", "seeds", "open_ground"],
                "additionalProperties": False,
            },
        },
        "legend_printed": {"type": "boolean"},
        "legend_symbols": {"type": "array", "items": {"type": "string"}},
        "compass_printed": {"type": "boolean"},
        "north": {"type": "string"},
        "scale_printed": {"type": "boolean"},
        "scale_text": {"type": ["string", "null"]},
        "feet_per_square": {"type": ["integer", "null"]},
        "cut_off": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": [
        "levels",
        "legend_printed",
        "legend_symbols",
        "compass_printed",
        "north",
        "scale_printed",
        "scale_text",
        "feet_per_square",
        "cut_off",
        "notes",
    ],
    "additionalProperties": False,
}


def render_wholemap(ctx: MapContext, out: Path) -> Path:
    """Draw the whole page with the region's grid ticked, downscaled to the long edge the model reads at.

    The whole page goes in, not the region alone, because a page's legend, scale, and compass are printed
    beside the drawing. The ticks mark the region's grid lines, every few numbered.
    """
    fit = ctx.fit
    height, width = ctx.rgb.shape[:2]
    scale = min(1.0, (WHOLEMAP_LONG_EDGE - GUTTER) / max(width, height))
    page = Image.fromarray(ctx.rgb).resize(
        (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS
    )
    image = Image.new("RGB", (page.width + GUTTER, page.height + GUTTER), (255, 255, 255))
    image.paste(page, (GUTTER, GUTTER))
    draw = ImageDraw.Draw(image)
    font = _font(12)
    step = 5 if max(fit.columns, fit.rows) > 30 else 2

    def point(i: float, j: float) -> tuple[float, float]:
        x, y = fit.corner(i, j)
        return x * scale + GUTTER, y * scale + GUTTER

    for i in range(fit.columns + 1):
        x, _ = point(i, 0)
        long = i % step == 0
        draw.line([(x, GUTTER - (12 if long else 5)), (x, GUTTER + 4)], fill=TICK_COLOUR, width=2 if long else 1)
        if long and i < fit.columns:
            draw.text((x + 2, 1), str(i), fill=TICK_COLOUR, font=font)
    for j in range(fit.rows + 1):
        _, y = point(0, j)
        long = j % step == 0
        draw.line([(GUTTER - (12 if long else 5), y), (GUTTER + 4, y)], fill=TICK_COLOUR, width=2 if long else 1)
        if long and j < fit.rows:
            draw.text((1, y + 1), str(j), fill=TICK_COLOUR, font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out


def wholemap_params(ctx: MapContext, crop_png_base64: str, keys_by_level: dict[tuple[str, int], list[str]]) -> dict:
    """The whole-map request for a region."""
    lines = []
    for dungeon, number in ctx.key.levels:
        keys = keys_by_level.get((dungeon, number), [])
        lines.append(f"- dungeon {dungeon!r}, level {number}: keys {', '.join(keys) if keys else '(none printed)'}")
    ask = (
        f"This map: {ctx.key.style_note}\n\n"
        f"The image is the whole page. The gridded region on it is {ctx.fit.columns} columns by {ctx.fit.rows} "
        f"rows of cells; the ticks in the gutter mark its grid lines, numbered every few cells. The legend, the "
        f"scale, and the compass, where the page prints them, lie outside the gridded region. The levels drawn "
        f"on the region, in survey order:\n" + "\n".join(lines)
    )
    return {
        "model": MODEL,
        "max_tokens": WHOLEMAP_MAX_TOKENS,
        "system": [{"type": "text", "text": WHOLEMAP_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": crop_png_base64}},
                    {"type": "text", "text": ask},
                ],
            }
        ],
        "output_config": {"format": {"type": "json_schema", "schema": WHOLEMAP_SCHEMA}, "effort": WHOLEMAP_EFFORT},
    }


def check_wholemap(answer: dict[str, Any], ctx: MapContext) -> list[str]:
    """What is malformed in a whole-map answer."""
    faults: list[str] = []
    expected = {(dungeon, number) for dungeon, number in ctx.key.levels}
    seen = set()
    for level in answer.get("levels", []):
        ident = (level.get("dungeon"), level.get("level"))
        if ident not in expected:
            faults.append(f"level {ident} isn't one the request listed")
            continue
        seen.add(ident)
        if not level.get("rects"):
            faults.append(f"level {ident} has no outline rectangle")
        for rect in [*level.get("rects", []), *level.get("open_ground", [])]:
            if len(rect) != 4 or rect[2] <= 0 or rect[3] <= 0:
                faults.append(f"level {ident} has a rectangle that isn't [x, y, width, height] with a positive size")
        if not level.get("seeds"):
            faults.append(f"level {ident} has no seed cell")
        for text in level.get("seeds", []):
            try:
                x, y = parse_cell(text)
            except ValueError:
                faults.append(f"level {ident} seed {text!r} isn't a cell 'x,y'")
                continue
            if not (0 <= x < ctx.fit.columns and 0 <= y < ctx.fit.rows):
                faults.append(f"level {ident} seed {text} is off the grid")
    for ident in expected - seen:
        faults.append(f"level {ident} is missing")
    for kind in answer.get("legend_symbols", []):
        if kind not in SYMBOL_KINDS:
            faults.append(f"legend symbol {kind!r} isn't in the vocabulary")
    if answer.get("north") not in ("top", "right", "bottom", "left"):
        faults.append("north must be top, right, bottom, or left")
    for side in answer.get("cut_off", []):
        if side not in ("top", "right", "bottom", "left"):
            faults.append(f"cut_off side {side!r} must be top, right, bottom, or left")
    return faults


def keys_by_level(ctx: MapContext) -> dict[tuple[str, int], list[str]]:
    """The survey's keys of each level drawn on the map, as the labels the map prints them with."""
    out: dict[tuple[str, int], list[str]] = {}
    if ctx.key.survey is None:
        return out
    survey = json.loads(ctx.key.survey.read_text())
    for dungeon in survey["dungeons"]:
        for level in dungeon["levels"]:
            labels = [area.get("source_label") or area["key"] for area in level["areas"]]
            out[(dungeon["id"], level["number"])] = labels
    return out


def submit_wholemap(ctx: MapContext, store: BatchStore, client: Any, ledger: Any) -> str | None:
    """Render the whole map and send its one request."""
    crop = ctx.key.directory / "wholemap" / "region.png"
    if not crop.exists():
        render_wholemap(ctx, crop)
    data = base64.b64encode(crop.read_bytes()).decode()
    store.add_job(f"{ctx.key.id}__whole", wholemap_params(ctx, data, keys_by_level(ctx)), {"map": ctx.key.id})
    return store.submit(client, ledger, f"wholemap {ctx.key.id}")


def collect_wholemap(ctx: MapContext, store: BatchStore, client: Any, ledger: Any) -> dict[str, int]:
    """Collect the whole-map answer into `wholemap/parsed/whole.json`."""
    return _collect_checked(
        store,
        client,
        ledger,
        ctx.key.directory / "wholemap" / "parsed",
        lambda document, _meta: check_wholemap(document, ctx),
        lambda _meta: "whole",
    )


def wholemap_answer(ctx: MapContext) -> dict[str, Any] | None:
    """The parsed whole-map answer, or `None`."""
    path = ctx.key.directory / "wholemap" / "parsed" / "whole.json"
    return json.loads(path.read_text()) if path.exists() else None
