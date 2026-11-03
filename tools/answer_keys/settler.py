"""The settler and the adversarial pass: fresh Claude requests that decide the disputed items and recheck the calls.

A *settling request* carries several disputed items of one map. For each, it shows a zoomed crop with the item
boxed in red, a wider crop around it, and the readings that disagree, and asks for the item's value in the
truth format's letters, with a reason. Each request is fresh: it carries none of the earlier work beyond the
readings themselves. Cells settle before edges, because an edge beside a cell settled as rock needs no
question. Every settled call is written to `settle/calls.json`.

The *adversarial pass* sends second fresh requests, with the same model, effort, and crops, that try to prove
the calls wrong: each item comes with the key's value, and the request asks for the value the picture
supports and whether it differs. It covers every settled call and the agreed edges in caves, at diagonals,
and around keys. A challenged call is settled once more, in a fresh settling request that carries the
adversary's reading with the others, and that result is final. The pass's results are in
`adversary/calls.json`, and the final values in `final.json`.
"""

import base64
import json
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from osrforge.tracing.truth import CELL_LETTERS, DOOR_LETTERS, parse_cell, parse_edge

from .batches import BatchStore
from .keyspec import MapContext
from .reader import MODEL, READ_LETTERS, answer_text_of, parse_answer_text, retry_params
from .readings import Items, edge_cells, edge_key, load_items
from .tiles import render_item, render_rect

__all__ = ["collect_pass", "final_values", "settle_items", "submit_pass"]

EFFORT = "high"
MAX_TOKENS = 20000
ITEMS_PER_REQUEST = 8
ZOOM_PX, ZOOM_REACH = 128, 1.5
WIDE_PX, WIDE_REACH = 48, 4.5
ADVERSARY_CAP = 1500
"""The most agreed items the adversarial pass rechecks on one map, drawn by a fixed seed when there are more."""

SETTLE_SYSTEM = (
    "You settle disputed readings of printed tabletop dungeon maps for a program that rebuilds each level on a "
    "square grid of cells. Each request lists several items of one map. For each item you get two crops: a "
    "zoomed crop with the item boxed in red, and a wider crop around it with the same box, both with red ticks "
    "and grid numbers in white gutters on all four sides (a tick's number is the column or row of the cell that "
    "starts at it). You also get what earlier readers said about the item. The readers disagree, so at least one "
    "is wrong; decide from the picture, not from the readers. Answer only through the structured output.\n\n"
    "Cells. A cell is floor (.) when at least half of it is floor a party can stand on: a room, a corridor, a "
    "cave, or the ground of a yard. It is rock (#) otherwise: solid rock, the earth of a hill, the page's blank "
    "background, a void, or a shaft. It is water (~) when at least half of it is water the map draws as water. "
    "A symbol or a label inside a cell leaves it floor.\n\n"
    "Edges. An edge is one side of a cell. Its letter: o open (nothing but the thin grid line, floor continues), "
    "p printed open line (a dashed, dotted, or dash-dot line, a fence, or an archway a party walks through), w "
    "wall (a heavy line, or the boundary between floor and rock; an edge between floor and water is o unless a "
    "wall line is drawn along the bank, because the water is impassable on its own), n window (a wall "
    "with the map's window symbol), d door (the map's door symbol), l locked door, s secret door. An edge "
    "between floor and rock is w. A thin printed grid line is never a wall. Lines drawn over the map (contours, "
    "trees, tracks, skylights, stream hatching) aren't walls, and neither is the drawing's own frame: where floor "
    "runs up to the frame and continues off the map, the edge on the frame is o. Along a diagonal or curved wall "
    "the edges follow the stair-stepped line that keeps the floor joined edge to edge. A label printed across a "
    "wall or a grid line doesn't change the edge.\n\n"
    "Labels. A label item asks which label is printed with its centre in the boxed cell, or near it. Answer "
    "with the text exactly as printed, an @, and the cell under the centre of the text, as 'text@x,y'; stacked "
    "keys are one label with the texts joined by /. Answer none when no label is printed there.\n\n"
    "Symbols. A symbol item asks whether a map symbol stands in or at the boxed place: stairs, ladder, trapdoor, "
    "hinged_floor, chute, slide, pit, cave_mouth, pillar, statue, pool, steps, bridge, or footing. Answer with "
    "the kind, an @, and the cells it covers joined by ; as 'kind@x,y;x,y', or for a cave mouth the edge as "
    "'cave_mouth@x,y:side'. Add |up or |down after it when the map shows that direction for stairs, a ladder, "
    "a trapdoor, or a chute. Answer none when no symbol stands there.\n\n"
    "Give every item's id exactly as listed, its answer, and a reason under 30 words."
)

ADVERSARY_SYSTEM = (
    "You check the settled readings of printed tabletop dungeon maps and try to prove them wrong. Each request "
    "lists several items of one map with the value an answer key records for each. For each item you get two "
    "crops: a zoomed crop with the item boxed in red, and a wider crop around it with the same box, both with "
    "red ticks and grid numbers in white gutters on all four sides (a tick's number is the column or row of the "
    "cell that starts at it). Look for any evidence in the picture that the recorded value is wrong, and answer "
    "with the value the picture supports best, which may be the recorded one. Answer only through the structured "
    "output.\n\n"
    "Cells. A cell is floor (.) when at least half of it is floor a party can stand on: a room, a corridor, a "
    "cave, or the ground of a yard. It is rock (#) otherwise: solid rock, the earth of a hill, the page's blank "
    "background, a void, or a shaft. It is water (~) when at least half of it is water the map draws as water. "
    "A symbol or a label inside a cell leaves it floor.\n\n"
    "Edges. An edge is one side of a cell. Its letter: o open (nothing but the thin grid line, floor continues), "
    "p printed open line (a dashed, dotted, or dash-dot line, a fence, or an archway a party walks through), w "
    "wall (a heavy line, or the boundary between floor and rock; an edge between floor and water is o unless a "
    "wall line is drawn along the bank, because the water is impassable on its own), n window (a wall "
    "with the map's window symbol), d door (the map's door symbol), l locked door, s secret door. An edge "
    "between floor and rock is w. A thin printed grid line is never a wall. Lines drawn over the map (contours, "
    "trees, tracks, skylights, stream hatching) aren't walls, and neither is the drawing's own frame: where floor "
    "runs up to the frame and continues off the map, the edge on the frame is o. Along a diagonal or curved wall "
    "the edges follow the stair-stepped line that keeps the floor joined edge to edge. A label printed across a "
    "wall or a grid line doesn't change the edge.\n\n"
    "Labels are answered as 'text@x,y' (the text exactly as printed, and the cell under its centre) or none. "
    "Symbols are answered as 'kind@x,y;x,y', or 'cave_mouth@x,y:side', with |up or |down when the map shows a "
    "direction, or none. A cave_mouth stands on the edge where the cave's passage opens into the ground outside, "
    "the gap in the heavy cave-wall line; an entrance letter or arrow printed at or beside the gap marks it and "
    "isn't the mouth's place, and the cells it's printed on are floor.\n\n"
    "Give every item's id exactly as listed, challenged true when your value differs from the recorded one, "
    "your value, and a reason under 30 words."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "answer": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "answer", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

ADVERSARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "challenged": {"type": "boolean"},
                    "answer": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "challenged", "answer", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

LETTER_WORDS = {
    "o": "open",
    "p": "printed open line",
    "w": "wall",
    "n": "window",
    "d": "door",
    "l": "locked door",
    "s": "secret door",
    ".": "floor",
    "#": "rock",
    "~": "water",
}


def _item_place(item_id: str) -> tuple[tuple[int, int], str | None]:
    """The cell and side a settling item's crops are drawn around."""
    kind, _, rest = item_id.partition(":")
    if kind == "c":
        return parse_cell(rest), None
    if kind in ("e", "b"):
        return parse_edge(rest)
    # m:<cell>, s:<cell>, o:<cell>, and g:<cell> name the cell the item is about.
    return parse_cell(rest.split("|")[0]), None


def crops_for(ctx: MapContext, item_id: str, rect: tuple[int, int, int, int] | None = None) -> tuple[Path, Path]:
    """Render, or find, the zoomed and wide crops of an item: a cell, an edge, or a rectangle of cells."""
    directory = ctx.key.directory / "crops"
    stem = item_id.replace(":", "_").replace(",", "-").replace("|", "_").replace("/", "_")
    zoom = directory / f"{stem}_zoom.png"
    wide = directory / f"{stem}_wide.png"
    if rect is not None:
        if not zoom.exists():
            render_rect(ctx.rgb, ctx.fit, rect, zoom, 64)
        if not wide.exists():
            render_rect(ctx.rgb, ctx.fit, (rect[0] - 3, rect[1] - 3, rect[2] + 6, rect[3] + 6), wide, 32)
        return zoom, wide
    cell, side = _item_place(item_id)
    if not zoom.exists():
        render_item(ctx.rgb, ctx.fit, cell, side, zoom, ZOOM_PX, ZOOM_REACH)
    if not wide.exists():
        render_item(ctx.rgb, ctx.fit, cell, side, wide, WIDE_PX, WIDE_REACH)
    return zoom, wide


def _describe_edge(name: str) -> str:
    (x, y), side = parse_edge(name)
    other = {"north": (x, y - 1), "south": (x, y + 1), "west": (x - 1, y), "east": (x + 1, y)}[side]
    return f"the {side} side of cell {x},{y}, between it and cell {other[0]},{other[1]}"


def _readings_text(readings: Sequence[tuple[str, object]]) -> str:
    return "; ".join(f"{reader} read {value}" for reader, value in readings)


def settle_items(items: Items, cells_settled: dict[str, str] | None) -> list[dict[str, Any]]:
    """The disputed items to settle in this round, each with its id, question text, and readings.

    The first round (no cells settled yet) settles the cells, the labels, and the symbols. The second settles
    the edges, with the settled cells applied: an edge with rock on one side and floor on the other is a wall
    without a question, unless a reader read a door, and an edge with rock on both sides needs nothing.

    Args:
        items: The table.
        cells_settled: The settled cell letters by name, or `None` in the first round.

    Returns:
        The items, each `{"id", "text", "readings", "kind"}`.
    """
    out: list[dict[str, Any]] = []
    if cells_settled is None:
        for name in items.disputed("cells"):
            entry = items.cells[name]
            readings = [("the classifier", LETTER_WORDS[entry["classifier"]])]
            readings += [("Claude's window read", LETTER_WORDS[letter]) for letter in entry["claude"]]
            if "clef" in entry:
                readings.append(
                    (
                        "Clef",
                        _clef_text(entry["clef"]),
                    )
                )
            out.append(
                {
                    "id": f"c:{name}",
                    "kind": "cell",
                    "text": f"cell {name}: is it floor, rock, or water? Readings: {_readings_text(readings)}.",
                    "readings": readings,
                }
            )
        for index, mark in enumerate(items.marks):
            if mark["status"] != "disputed":
                continue
            cells = mark["reading1"] or mark["reading2"]
            readings = [
                (
                    "the first label read",
                    f"{mark['text']}@{','.join(mark['reading1'])}" if mark["reading1"] else "no such label",
                ),
                (
                    "the second label read",
                    f"{mark['text']}@{','.join(mark['reading2'])}" if mark["reading2"] else "no such label",
                ),
            ]
            out.append(
                {
                    "id": f"m:{cells[0]}|{index}",
                    "kind": "mark",
                    "text": f"label near cell {cells[0]}: which label, if any, is printed with its centre in or "
                    f"next to the boxed cell, and in which cell is its centre? Readings: {_readings_text(readings)}.",
                    "readings": readings,
                }
            )
        for index, symbol in enumerate(items.symbols):
            if symbol["status"] != "disputed":
                continue
            item = _symbol_item(items, index)
            if item is not None:
                out.append(item)
        return out
    for name in items.disputed("edges"):
        entry = items.edges[name]
        key = edge_key(name)
        a, b = edge_cells(key)
        sides = [cells_settled.get(f"{c[0]},{c[1]}", "#") for c in (a, b)]
        letters = {entry["classifier"], *entry["claude"]}
        if "clef" in entry:
            letters.add(entry["clef"]["choice"])
        if all(side == "#" for side in sides):
            continue
        if "#" in sides and not letters & set(DOOR_LETTERS + "n"):
            # Floor against rock is a wall without a question, unless a reader saw a door or a window symbol.
            continue
        readings = [("the classifier", LETTER_WORDS.get(entry["classifier"], entry["classifier"]))]
        readings += [("Claude's window read", LETTER_WORDS.get(letter, letter)) for letter in entry["claude"]]
        if "clef" in entry:
            readings.append(
                (
                    "Clef",
                    f"{LETTER_WORDS.get(entry['clef']['choice'], entry['clef']['choice'])} (p={entry['clef']['p']})",
                )
            )
        out.append(
            {
                "id": f"e:{name}",
                "kind": "edge",
                "text": f"edge {name}, {_describe_edge(name)}: which letter? Readings: {_readings_text(readings)}.",
                "readings": readings,
            }
        )
    return out


def _clef_text(clef: dict[str, Any]) -> str:
    return f"{LETTER_WORDS.get(clef['choice'], clef['choice'])} (p={clef['p']})"


def outline_items(ctx: MapContext, items: Items, final: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The outline round's items: floor just outside every outline, and each open-ground rectangle."""
    from .letters import final_cells, final_edges, outline_state

    log: dict[str, Any] = {}
    cells = final_cells(items, final, log)
    edges = final_edges(items, cells, final, log)
    state = outline_state(ctx, cells, edges, final)
    out: list[dict[str, Any]] = []
    options = [*state.levels, "outside"]
    for crossing in state.crossings:
        nx, ny = crossing["beyond"]
        x, y = crossing["cell"]
        readings = [("the whole-map read", f"no level's outline covers cell {nx},{ny}")]
        out.append(
            {
                "id": f"o:{nx},{ny}|{crossing['level']}",
                "kind": "outline",
                "options": options,
                "text": f"cell {nx},{ny} (boxed) is floor that no level's outline covers, and an open edge joins it "
                "to "
                f"cell {x},{y}, which lies in the outline of level {crossing['level']}. Which level's floor is the "
                f"boxed cell part of? Answer with exactly one of: {', '.join(options)}. Readings: "
                f"{_readings_text(readings)}.",
                "readings": readings,
            }
        )
    for name in water_bank_edges(cells, edges, final):
        readings = [("the key so far", "wall")]
        out.append(
            {
                "id": f"b:{name}",
                "kind": "edge",
                "text": f"edge {name}, {_describe_edge(name)}, between floor and water: an edge between floor and "
                f"water is o unless a wall line is drawn along the bank, because the water is impassable on its own. "
                f"Which "
                f"letter? Readings: {_readings_text(readings)}.",
                "readings": readings,
            }
        )
    for item in state.open_ground_items:
        rect = item["rect"]
        readings = [("the whole-map read", f"open ground of level {item['level']}")]
        out.append(
            {
                "id": f"g:{rect[0]},{rect[1]}|{item['level']}|{item['index']}",
                "kind": "outline",
                "options": ["open_ground", "outside"],
                "rect": list(rect),
                "text": f"the boxed rectangle of cells, {rect[2]} wide and {rect[3]} high from cell "
                f"{rect[0]},{rect[1]}, was read as open ground of level {item['level']}: ground the party walks on "
                f"inside the level, such as a yard, a courtyard, or the ground between the level's buildings. Is it "
                f"open ground of that "
                f"level, or the outside beyond the level's walls or cave mouth? Answer open_ground or outside. "
                f"Readings: {_readings_text(readings)}.",
                "readings": readings,
            }
        )
    return out


def water_bank_edges(cells: list[list[str]], edges: Any, final: dict[str, dict[str, Any]]) -> list[str]:
    """The edges between a floor cell and a water cell that the key reads as a wall and the settler hasn't been
    asked about as a bank: the readings called the bank a wall before the prompts said the water is impassable
    on its own."""
    width, height = len(cells[0]), len(cells)
    out = []
    for y in range(height):
        for x in range(width):
            if cells[y][x] != ".":
                continue
            for side, (dx, dy) in (("east", (1, 0)), ("south", (0, 1)), ("west", (-1, 0)), ("north", (0, -1))):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < width and 0 <= ny < height) or cells[ny][nx] != "~":
                    continue
                if edges.at((x, y), side) != "w":
                    continue
                name = f"{x},{y}:{side}"
                canonical = f"{nx},{ny}:{ {'east': 'west', 'west': 'east', 'south': 'north', 'north': 'south'}[side] }"
                if side in ("east", "south"):
                    name, canonical = canonical, name
                out.append(name)
    return sorted(set(out))


def _symbol_item(items: Items, index: int) -> dict[str, Any] | None:
    """The settling item for a symbol of the table, by its index."""
    if not 0 <= index < len(items.symbols):
        return None
    symbol = items.symbols[index]
    one = symbol["reading1"] or symbol["reading2"]
    if one is None:
        return None
    place = one["cells"][0] if one["cells"] else parse_edge(one["edge"])[0]
    place_text = place if isinstance(place, str) else f"{place[0]},{place[1]}"
    readings = [
        ("the first symbol read", _symbol_text(symbol["reading1"])),
        ("the second symbol read", _symbol_text(symbol["reading2"])),
    ]
    return {
        "id": f"s:{place_text}|{index}",
        "kind": "symbol",
        "text": f"symbol at or beside cell {place_text}: which symbol, if any, stands there, and which "
        f"cells does it cover? Readings: {_readings_text(readings)}.",
        "readings": readings,
    }


def _symbol_text(symbol: dict[str, Any] | None) -> str:
    if symbol is None:
        return "no such symbol"
    place = ";".join(symbol["cells"]) if symbol["cells"] else symbol["edge"]
    direction = f"|{symbol['direction']}" if symbol.get("direction") else ""
    return f"{symbol['kind']}@{place}{direction}"


def _request(
    ctx: MapContext, batch: list[dict[str, Any]], system: str, schema: dict[str, Any], adversary: bool
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": f"This map: {ctx.key.style_note}\n\nThe items:"}]
    for item in batch:
        zoom, wide = crops_for(ctx, item["id"], tuple(item["rect"]) if item.get("rect") else None)
        content.append({"type": "text", "text": f"Item {item['id']}, zoomed crop:"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(zoom.read_bytes()).decode(),
                },
            }
        )
        content.append({"type": "text", "text": f"Item {item['id']}, wider crop:"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(wide.read_bytes()).decode(),
                },
            }
        )
        if adversary:
            content.append(
                {
                    "type": "text",
                    "text": f"Item {item['id']}: {item['text']} The answer key records {item['recorded']}. "
                    "Try to prove that wrong.",
                }
            )
        else:
            content.append({"type": "text", "text": f"Item {item['id']}: {item['text']}"})
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": EFFORT},
    }


def check_answer(answer: dict[str, Any], expected: list[dict[str, Any]], adversary: bool) -> list[str]:
    """What is malformed in a settling or adversarial answer."""
    faults: list[str] = []
    by_id = {item["id"]: item for item in expected}
    seen = set()
    for row in answer.get("items", []):
        item_id = row.get("id")
        if item_id not in by_id:
            faults.append(f"item {item_id!r} wasn't asked")
            continue
        seen.add(item_id)
        value = (row.get("answer") or "").strip()
        kind = by_id[item_id]["kind"]
        if (kind == "cell" and value not in CELL_LETTERS) or (kind == "edge" and value not in READ_LETTERS):
            faults.append(
                f"item {item_id} needs one letter from {'. # ~' if kind == 'cell' else 'o p w n d l s'}, not {value!r}"
            )
        elif kind == "mark" and value != "none":
            text, at, cell = value.rpartition("@")
            if not at or not text:
                faults.append(f"item {item_id} needs 'text@x,y' or none, not {value!r}")
            else:
                try:
                    parse_cell(cell)
                except ValueError:
                    faults.append(f"item {item_id} needs 'text@x,y' or none, not {value!r}")
        elif kind == "symbol" and value != "none":
            body, _, direction = value.partition("|")
            kind_name, at, place = body.partition("@")
            if not at or direction not in ("", "up", "down"):
                faults.append(
                    f"item {item_id} needs 'kind@x,y;x,y' or 'cave_mouth@x,y:side', with |up or |down, or none, "
                    f"not {value!r}"
                )
                continue
            try:
                if kind_name == "cave_mouth":
                    parse_edge(place)
                else:
                    for text in place.split(";"):
                        parse_cell(text)
            except ValueError:
                faults.append(f"item {item_id} names a place that isn't an address: {value!r}")
        if kind == "outline" and value not in by_id[item_id].get("options", []):
            faults.append(f"item {item_id} needs one of {by_id[item_id].get('options')}, not {value!r}")
        if adversary and not isinstance(row.get("challenged"), bool):
            faults.append(f"item {item_id} needs challenged true or false")
    for item_id in by_id:
        if item_id not in seen:
            faults.append(f"item {item_id} has no answer")
    return faults


def _calls_path(ctx: MapContext, stage: str) -> Path:
    return ctx.key.directory / stage / "calls.json"


def _load_calls(ctx: MapContext, stage: str) -> dict[str, dict[str, Any]]:
    path = _calls_path(ctx, stage)
    return json.loads(path.read_text()) if path.exists() else {}


def _save_calls(ctx: MapContext, stage: str, calls: dict[str, dict[str, Any]]) -> None:
    path = _calls_path(ctx, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(calls, indent=1, sort_keys=True) + "\n")


def _add_jobs(ctx: MapContext, store: BatchStore, prefix: str, items: list[dict[str, Any]], adversary: bool) -> int:
    """Add one job per group of items, skipping items that already have a job. Returns the items added."""
    asked = set()
    for custom_id in store.job_ids():
        for item in store.job(custom_id)["meta"]["items"]:
            asked.add(item["id"])
    fresh = [item for item in items if item["id"] not in asked]
    existing = len([c for c in store.job_ids() if c.startswith(prefix)])
    system = ADVERSARY_SYSTEM if adversary else SETTLE_SYSTEM
    schema = ADVERSARY_SCHEMA if adversary else SCHEMA
    for number, start in enumerate(range(0, len(fresh), ITEMS_PER_REQUEST), existing):
        batch = fresh[start : start + ITEMS_PER_REQUEST]
        params = _request(ctx, batch, system, schema, adversary)
        store.add_job(f"{prefix}{number:03d}", params, {"map": ctx.key.id, "items": batch})
    return len(fresh)


def submit_pass(
    ctx: MapContext, stage: str, store: BatchStore, client: Any, ledger: Any, limit: int | None = None
) -> tuple[str | None, int]:
    """Add the stage's jobs and send the pending ones.

    For `settle`: the first call sends the cell, label, and symbol items; once every cell is settled, the next
    call sends the edge items. For `adversary`: the settled calls and the agreed edges in caves, at diagonals,
    and around keys; once the pass has answered, the next call sends the re-settling of the challenged calls.
    `limit` sends at most that many jobs now and leaves the rest pending for a later call, so a large pass can
    go out in parts that each fit under the budget line with their worst case.
    """
    items = load_items(ctx)
    if stage == "settle":
        calls = _load_calls(ctx, "settle")
        cells_done = all(f"c:{name}" in calls for name in items.disputed("cells"))
        round_items = settle_items(items, None)
        if cells_done:
            cells_settled = {name: item["classifier"] for name, item in items.cells.items()}
            for name in items.cells:
                if items.cells[name]["claude"]:
                    cells_settled[name] = items.cells[name]["claude"][0]
            for name, call in calls.items():
                if name.startswith("c:"):
                    cells_settled[name[2:]] = call["value"]
            edge_items = settle_items(items, cells_settled)
            round_items += edge_items
            if all(item["id"] in calls for item in edge_items):
                # The outline round: every cell and edge is settled, so the outlines can be laid over them.
                round_items += outline_items(ctx, items, final_values(ctx))
        count = _add_jobs(ctx, store, f"{ctx.key.id}__settle__", round_items, False)
        return store.submit(client, ledger, f"settle {ctx.key.id}", limit), count
    settled = _load_calls(ctx, "settle")
    adversary = _load_calls(ctx, "adversary")
    pending = [name for name, call in adversary.items() if call["challenged"] and "resettled" not in call]
    if pending:
        resettle = []
        for name in pending:
            base = settled.get(name)
            item = _rebuild_item(ctx, items, name)
            if item is None:
                continue
            readings = [*list(item["readings"]), ("the adversarial check", adversary[name]["value"])]
            if base is not None:
                readings.append(("the settler", base["value"]))
            item = {
                **item,
                "readings": readings,
                "text": item["text"].split(" Readings:")[0] + f" Readings: {_readings_text(readings)}.",
            }
            resettle.append(item)
        resettle_store = BatchStore(ctx.key.directory / "resettle")
        count = _add_jobs(ctx, resettle_store, f"{ctx.key.id}__resettle__", resettle, False)
        return resettle_store.submit(client, ledger, f"resettle {ctx.key.id}", limit), count
    round_items = adversary_items(ctx, items, settled)
    mouths = _mouth_cells(items)
    first = [item for item in round_items if _touches(item["id"], mouths)]
    others = [item for item in round_items if not _touches(item["id"], mouths)]
    # The jobs around cave mouths sort before the rest, so a pass sent in parts asks them first.
    count = _add_jobs(ctx, store, f"{ctx.key.id}__adversary-mouth__", first, True)
    count += _add_jobs(ctx, store, f"{ctx.key.id}__adversary__", others, True)
    return store.submit(client, ledger, f"adversary {ctx.key.id}", limit), count


def _rebuild_item(ctx: MapContext, items: Items, name: str) -> dict[str, Any] | None:
    """A settling item for a call id, with its readings from the table."""
    kind = name.split(":")[0]
    if kind == "c":
        for item in settle_items(items, None):
            if item["id"] == name:
                return item
        entry = items.cells.get(name[2:])
        if entry is None:
            return None
        readings = [("the classifier", LETTER_WORDS[entry["classifier"]])] + [
            ("Claude's window read", LETTER_WORDS[letter]) for letter in entry["claude"]
        ]
        return {
            "id": name,
            "kind": "cell",
            "text": f"cell {name[2:]}: is it floor, rock, or water? Readings: {_readings_text(readings)}.",
            "readings": readings,
        }
    if kind in ("e", "b"):
        entry = items.edges.get(name[2:])
        if entry is None:
            return None
        readings = [("the classifier", LETTER_WORDS.get(entry["classifier"], entry["classifier"]))] + [
            ("Claude's window read", LETTER_WORDS.get(letter, letter)) for letter in entry["claude"]
        ]
        if "clef" in entry:
            readings.append(
                (
                    "Clef",
                    f"{LETTER_WORDS.get(entry['clef']['choice'], entry['clef']['choice'])} (p={entry['clef']['p']})",
                )
            )
        return {
            "id": name,
            "kind": "edge",
            "text": f"edge {name[2:]}, {_describe_edge(name[2:])}: which letter? Readings: {_readings_text(readings)}.",
            "readings": readings,
        }
    for item in settle_items(items, None):
        if item["id"] == name:
            return item
    if kind == "s" and "|" in name:
        return _symbol_item(items, int(name.rsplit("|", 1)[1]))
    if kind in ("o", "g"):
        for item in outline_items(ctx, items, {}):
            if item["id"] == name:
                return item
    return None


def adversary_items(ctx: MapContext, items: Items, settled: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every settled call, plus the agreed edges in caves, at diagonals, and around keys."""
    out: list[dict[str, Any]] = []
    for name, call in settled.items():
        item = _rebuild_item(ctx, items, name)
        if item is None:
            continue
        out.append(
            {
                **item,
                "recorded": _recorded_text(item["kind"], call["value"]),
                "category": call.get("category", "settled"),
            }
        )
    cave = set(items.cave)
    partial = set(items.partial)
    mouths = _mouth_cells(items)
    near_keys = set()
    for mark in items.marks:
        for text in [*mark["reading1"], *mark["reading2"]]:
            x, y = parse_cell(text)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    near_keys.add(f"{x + dx},{y + dy}")
    agreed = []
    for name, entry in items.edges.items():
        if entry["status"] != "agreed" or f"e:{name}" in settled:
            continue
        a, b = edge_cells(edge_key(name))
        names = {f"{a[0]},{a[1]}", f"{b[0]},{b[1]}"}
        category = None
        if names & mouths or names & cave:
            category = "cave"
        elif names & partial:
            category = "diagonal"
        elif names & near_keys:
            category = "key"
        if category is None:
            continue
        letter = entry["claude"][0]
        if letter == "-":
            continue
        item = _rebuild_item(ctx, items, f"e:{name}")
        if item is not None:
            agreed.append({**item, "recorded": _recorded_text("edge", letter), "category": category})
    # Agreed cells beside a label or on a diagonal: where a key lands and where the half rule is judged.
    for name, entry in items.cells.items():
        if entry["status"] != "agreed" or f"c:{name}" in settled or not entry["claude"]:
            continue
        category = None
        if name in mouths:
            category = "cave"
        elif name in partial:
            category = "diagonal"
        elif name in near_keys:
            category = "key"
        if category is None:
            continue
        item = _rebuild_item(ctx, items, f"c:{name}")
        if item is not None:
            agreed.append({**item, "recorded": _recorded_text("cell", entry["claude"][0]), "category": category})
    # Agreed cave mouths: both readers may have put the mouth on the entrance arrow instead of the gap.
    for index, symbol in enumerate(items.symbols):
        one = symbol["reading1"]
        if symbol["status"] != "agreed" or not one or one.get("kind") != "cave_mouth" or not one.get("edge"):
            continue
        item = _symbol_item(items, index)
        if item is not None and item["id"] not in settled:
            agreed.append({**item, "recorded": _symbol_text(one), "category": "cave"})
    # The calls around a cave mouth are always checked: an entrance letter or arrow printed in the mouth reads as
    # ink, and a mouth read as a wall with rock beyond it closes the lair.
    must = [item for item in agreed if _touches(item["id"], mouths)]
    rest = [item for item in agreed if not _touches(item["id"], mouths)]
    if len(rest) > ADVERSARY_CAP:
        rng = random.Random(7)
        rest = rng.sample(rest, ADVERSARY_CAP)
    return out + must + rest


def _mouth_cells(items: Items) -> set[str]:
    """The cells within one cell of either side of every cave mouth a reading placed."""
    cells: set[str] = set()
    for symbol in items.symbols:
        for reading in (symbol.get("reading1"), symbol.get("reading2")):
            if not reading or reading.get("kind") != "cave_mouth" or not reading.get("edge"):
                continue
            for x, y in edge_cells(edge_key(reading["edge"])):
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        cells.add(f"{x + dx},{y + dy}")
    return cells


def _touches(item_id: str, cells: set[str]) -> bool:
    """Whether a cell or edge item lies on one of the cells."""
    if item_id.startswith("c:"):
        return item_id[2:] in cells
    if item_id.startswith("e:"):
        a, b = edge_cells(edge_key(item_id[2:]))
        return bool({f"{a[0]},{a[1]}", f"{b[0]},{b[1]}"} & cells)
    if item_id.startswith("s:"):
        return item_id[2:].split("|")[0] in cells
    return False


def _recorded_text(kind: str, value: str) -> str:
    if kind in ("cell", "edge"):
        return f"{value} ({LETTER_WORDS.get(value, value)})"
    return value


def collect_pass(ctx: MapContext, stage: str, store: BatchStore, client: Any, ledger: Any) -> dict[str, Any]:
    """Collect a stage's answers into its calls file, queueing one retry for each malformed answer.

    The result counts the calls, retries, open batches, errors, and challenges, and lists the retries' job ids
    under `retry_ids`, so the caller sends those alone and not every job the store still holds.
    """
    adversary = stage == "adversary"
    counts = store.collect(client, ledger)
    result: dict[str, Any] = {
        "calls": 0,
        "retries": 0,
        "open": counts["open"],
        "errored": counts["errored"],
        "challenged": 0,
    }
    calls = _load_calls(ctx, stage)
    _harvest(store, calls, adversary, result)
    if adversary:
        resettle_store = BatchStore(ctx.key.directory / "resettle")
        if resettle_store.job_ids():
            more = resettle_store.collect(client, ledger)
            result["open"] += more["open"]
            resettled = _load_calls(ctx, "resettle")
            _harvest(resettle_store, resettled, False, result)
            _save_calls(ctx, "resettle", resettled)
            for name, call in resettled.items():
                if name in calls:
                    calls[name]["resettled"] = call["value"]
        result["challenged"] = sum(1 for call in calls.values() if call.get("challenged"))
        result["retry_ids"] = [*store.added, *resettle_store.added]
    result.setdefault("retry_ids", list(store.added))
    _save_calls(ctx, stage, calls)
    if stage == "settle":
        for call in calls.values():
            call.setdefault("category", "settled")
    write_final(ctx)
    return result


def _harvest(store: BatchStore, calls: dict[str, dict[str, Any]], adversary: bool, result: dict[str, Any]) -> None:
    for custom_id in store.job_ids():
        answer = store.answer(custom_id)
        if answer is None:
            continue
        job = store.job(custom_id)
        expected = job["meta"]["items"]
        if all(
            item["id"] in calls and calls[item["id"]].get("job") in (custom_id, job["meta"].get("retry_of"))
            for item in expected
        ):
            continue
        text = answer_text_of(answer["message"])
        document = parse_answer_text(text)
        faults = ["the answer isn't a JSON object"] if document is None else check_answer(document, expected, adversary)
        if answer.get("stop_reason") in ("max_tokens", "refusal"):
            faults.insert(0, f"the answer stopped with {answer['stop_reason']}")
        if not faults and document is not None:
            by_id = {item["id"]: item for item in expected}
            for row in document["items"]:
                item = by_id[row["id"]]
                call = {
                    "value": row["answer"].strip(),
                    "reason": row["reason"],
                    "job": custom_id,
                    "kind": item["kind"],
                    "readings": item["readings"],
                }
                if adversary:
                    # A challenge that names the recorded value isn't one.
                    recorded = item["recorded"].split(" ")[0]
                    call["challenged"] = bool(row["challenged"]) and row["answer"].strip() != recorded
                    call["recorded"] = item["recorded"]
                    call["category"] = item.get("category", "settled")
                calls[row["id"]] = call
                result["calls"] += 1
            continue
        if job["meta"].get("retry_of") is not None:
            with (store.directory / "failed.jsonl").open("a") as handle:
                handle.write(json.dumps({"job": custom_id, "faults": faults}) + "\n")
            continue
        retry_id = f"{custom_id}__r"
        if (store.jobs / f"{retry_id}.json").exists():
            continue
        with (store.directory / "malformed.jsonl").open("a") as handle:
            handle.write(json.dumps({"job": custom_id, "faults": faults, "next": retry_id}) + "\n")
        store.add_job(retry_id, retry_params(job["params"], text, faults), {**job["meta"], "retry_of": custom_id})
        result["retries"] += 1


def final_values(ctx: MapContext) -> dict[str, dict[str, Any]]:
    """Every call's final value: the owner's recheck where there is one, else the re-settled value where the
    adversary's challenge was re-settled, else the settler's, with the adversary's value beside it."""
    settled = _load_calls(ctx, "settle")
    adversary = _load_calls(ctx, "adversary")
    resettled = _load_calls(ctx, "resettle")
    rechecked = _load_calls(ctx, "recheck")
    out: dict[str, dict[str, Any]] = {}
    for name, call in settled.items():
        final = dict(call)
        final["settled"] = call["value"]
        if name in adversary:
            final["adversarial"] = adversary[name]["value"]
            final["challenged"] = adversary[name]["challenged"]
        if name in resettled:
            final["value"] = resettled[name]["value"]
            final["resettle_reason"] = resettled[name]["reason"]
        out[name] = final
    for name, call in adversary.items():
        if name in out:
            continue
        final = {
            "kind": call["kind"],
            "readings": call["readings"],
            "value": call["recorded"].split(" ")[0],
            "settled": None,
            "adversarial": call["value"],
            "challenged": call["challenged"],
            "reason": "",
            "category": call.get("category", "agreed"),
        }
        if name in resettled:
            final["value"] = resettled[name]["value"]
            final["reason"] = resettled[name]["reason"]
        out[name] = final
    # A bank call (`b:`) answers an edge between floor and water asked again under the bank rule: it replaces the
    # edge's value.
    for name, call in list(out.items()):
        if name.startswith("b:"):
            edge_name = "e:" + name[2:]
            target = out.get(edge_name)
            if target is None:
                target = {
                    "kind": "edge",
                    "readings": call["readings"],
                    "settled": None,
                    "reason": "",
                    "category": "bank",
                }
                out[edge_name] = target
            target["before_bank"] = target.get("value")
            target["value"] = call["value"]
            target["bank"] = True
            target["reason"] = call.get("reason", "")
    # The owner's recheck has the last word: its value replaces the call's, and the earlier value is kept beside it.
    for name, call in rechecked.items():
        final = out.get(name)
        if final is None:
            final = {
                "kind": call["kind"],
                "readings": call["readings"],
                "settled": None,
                "reason": "",
                "category": "recheck",
            }
            out[name] = final
        final["before_recheck"] = final.get("value")
        final["value"] = call["value"]
        final["recheck_reason"] = call["reason"]
    return out


def write_final(ctx: MapContext) -> Path:
    """Write `final.json`."""
    path = ctx.key.directory / "final.json"
    path.write_text(json.dumps(final_values(ctx), indent=1, sort_keys=True) + "\n")
    return path
