"""Claude's window reading: the request for one window, the checks on its answer, and the retry.

This is the first of an answer key's two independent readings. Each request shows one tick-marked window crop
(`tiles.render_window`) and asks for every cell and edge in the window in the truth format's letters, with the
labels and symbols printed in it. The answer comes back as JSON that the API checks against `SCHEMA`; `check`
then tests what the schema can't: the number of rows, each row's length, the letters, and that every named
cell lies in the window. A malformed answer is asked again once, through `retry_params`: the original request
followed by the malformed answer and a message that names the faults, so the retry is its own request.

None of this is the tracing stage's code: the stage's prompts and schemas live under `osrforge.tracing`, and
this module shares nothing with them but the letters.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from osrforge.tracing.truth import CELL_LETTERS, SYMBOL_KINDS, parse_cell, parse_edge

from .tiles import Window

__all__ = [
    "MODEL",
    "SCHEMA",
    "SYSTEM",
    "WindowAnswer",
    "check",
    "normalize_addresses",
    "normalize_edge",
    "parse",
    "retry_params",
    "window_params",
]

MODEL = "claude-sonnet-5-5"
EFFORT = "high"
MAX_TOKENS = 24000
"""Room above the longest high-effort window answer the model comparison measured (10,640 output tokens)."""

READ_LETTERS = "opwndls"
"""The edge letters a window answer may use. `-` is never asked for: an edge between two rock cells is `w` in the
answer and becomes `-` when the answer is parsed."""

SYSTEM = (
    "You read printed tabletop dungeon maps for a program that rebuilds each level on a square grid of cells. "
    "Every request shows a crop of one map, magnified, and names a window of cells inside it. Answer only through "
    "the structured output. Don't describe the adventure.\n\n"
    "The grid. Red tick marks with red numbers are drawn in white gutters along all four sides of the crop. Each "
    "tick marks a grid line, and its number is the column (along the top and the bottom) or row (down the left "
    "and the right) of the cell that starts at that line: a column's number sits just right of its tick, and a "
    "row's number sits just below its tick. The last tick in each direction is the window's far edge and has no "
    "number. The crop shows one cell of map outside the window on every side; use that margin only to understand "
    "what's inside the window. The printed map's own grid lines lie on the ticks, so use the printed lines to "
    "find each cell, and the ticks to number them. Count the rows and columns against the ticks on both sides "
    "before you write them, so that no row or column is shifted.\n\n"
    "Cells. A cell is floor (.) when at least half of it is floor a party can stand on: a room, a corridor, a "
    "cave, or the ground of a yard or courtyard. It is rock (#) otherwise: solid rock, the earth of a hill, the "
    "page's blank background, a void, or a shaft. It is water (~) when at least half of it is water the map draws "
    "as water (a moat, a pool, a river). A cell that holds a symbol (stairs, a trapdoor, a pit, a pillar, a "
    "statue, a label) stays floor. A diagonal or curved wall cuts a cell into two parts, and the cell is floor "
    "or rock by which part is larger. List every cell that is between a quarter and three quarters floor, or "
    "that a diagonal or curved wall crosses, in partial, by its absolute coordinates.\n\n"
    "Edges. An edge is one side of a cell: the line between it and its neighbour. Read what the map draws on it, "
    "with one letter: o open (nothing drawn but the thin printed grid line, and floor continues across it), "
    "p printed open line (a dashed, dotted, or dash-dot line, a fence, a portcullis, or an archway, which a party "
    "walks through), w wall (a heavy line, or the boundary between floor and rock, or the boundary between floor "
    "and water), n window (a wall with the map's window symbol), d door (the map's door symbol), l locked door "
    "(a door with the map's locked-door symbol), s secret door (the map's secret-door symbol, usually an S on the "
    "wall). An edge between two rock cells, or between floor and rock, is w. An edge with water on one side and "
    "floor on the other is w. A thin printed grid line is never a wall: a wall is drawn clearly heavier than the "
    "grid. Lines drawn over the map (contours and elevation lines, trees and bushes, tracks, skylights, stream "
    "hatching) aren't walls, so an edge crossed only by such a line is o. The drawing's own frame, the border line "
    "around the whole map, isn't a wall either: where floor runs up to the frame and continues off the map, the "
    "edge on the frame is o.\n\n"
    "Hard cases. Along a diagonal or curved wall the edges between cells follow the stair-stepped line that keeps "
    "the floor joined edge to edge, never only at a corner. A door drawn across two cells' shared edge belongs to "
    "that edge; a door drawn inside a cell belongs to the edge it's nearest. A label printed across a wall or a "
    "grid line doesn't change the edge. A thin line inside a room that doesn't meet walls at both ends is "
    "furniture or decoration, so the edges it crosses are open. Rubble drawn as dots on floor leaves the floor "
    "passable. Stairs drawn across a passage leave the passage's edges open along its length. When ink is "
    "ambiguous, prefer the reading that keeps passages one cell wide joined edge to edge, and name the doubt in "
    "the notes field with the row and column.\n\n"
    "Labels. Report every label printed inside the window in marks: a room key (a number such as 12, or a number "
    "with a letter such as 6a), a letter on its own (a, b), a lair letter (A, B), or a word (rubble, stream). "
    "Give its text exactly as printed and the cell under the centre of its text, by absolute coordinates. When "
    "two keys are printed stacked one above the other in one place, report them as one mark with the texts "
    "joined by / from top to bottom. When a label sits on a door, give that door's edge in on_door. When a leader "
    "line runs from the label to a cell, give that cell in leader_to. A label whose centre is outside the "
    "window belongs to another window: leave it out.\n\n"
    "Symbols. Report every map symbol inside the window in symbols, with its kind from this list: stairs, ladder, "
    "trapdoor, hinged_floor, chute, slide, pit, cave_mouth, pillar, statue, pool, steps, bridge, footing. A "
    "cave_mouth stands on the edge where a cave opens to the outside and has no cells; every other kind covers "
    "one or more cells. For stairs, a ladder, a trapdoor, or a chute give direction up or down when the map shows "
    "one (an arrow, a label such as Down, or the legend's convention), else null. Steps drawn inside a level "
    "between two floors of one level are steps, not stairs. The request names which symbols this map's legend "
    "prints; a legend entry such as a trap or a statue that isn't in the list above isn't a symbol here, and the "
    "cell it's drawn in stays floor.\n\n"
    "Output. cells: one string per row of the window from top to bottom, each one letter per column from left to "
    "right, from . # ~. vertical: one string per row, with one letter per vertical grid line of that row from "
    "left to right, so each string has one more letter than the window has columns: the first letter is the "
    "west side of the row's first cell, and the last is the east side of its last cell. horizontal: one string "
    "per horizontal grid line from top to bottom, so there is one more string than the window has rows, each "
    "with one letter per column: the first string is the north sides of the top row's cells, and the last is the "
    "south sides of the bottom row's. Edge letters come from o p w n d l s. Every string must have exactly the "
    "stated length. Keep notes under 80 words."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cells": {"type": "array", "items": {"type": "string"}, "description": "one string per row, letters . # ~"},
        "vertical": {
            "type": "array",
            "items": {"type": "string"},
            "description": "one string per row, columns + 1 letters from o p w n d l s",
        },
        "horizontal": {
            "type": "array",
            "items": {"type": "string"},
            "description": "rows + 1 strings, columns letters from o p w n d l s",
        },
        "partial": {"type": "array", "items": {"type": "string"}, "description": "cells 'x,y' partly floor"},
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
    "required": ["cells", "vertical", "horizontal", "partial", "marks", "symbols", "notes"],
    "additionalProperties": False,
}


def window_params(crop_png_base64: str, window: Window, style_note: str, model: str = MODEL) -> dict[str, Any]:
    """Build the Messages API parameters for one window read.

    Args:
        crop_png_base64: The window crop, PNG, base64.
        window: The window.
        style_note: The map's style and legend in words, which changes from map to map and so goes in the
            message text, after the image.
        model: The model id.

    Returns:
        The parameters, as `messages.create` or a batch request takes them.
    """
    x1 = window.x0 + window.columns - 1
    y1 = window.y0 + window.rows - 1
    ask = (
        f"This map: {style_note}\n\n"
        f"Read the window of columns {window.x0} to {x1} and rows {window.y0} to {y1}: every cell and every edge "
        f"inside it, with its labels and symbols. cells has {window.rows} strings of {window.columns} letters; "
        f"its first string is row {window.y0}, and each string's first letter is column {window.x0}. vertical has "
        f"{window.rows} strings of {window.columns + 1} letters. horizontal has {window.rows + 1} strings of "
        f"{window.columns} letters. Give partial cells, mark cells, and symbol cells by their absolute column and "
        f"row, as 'x,y'."
    )
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": crop_png_base64}},
                    {"type": "text", "text": ask},
                ],
            }
        ],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": EFFORT},
    }


_SIDE_WORDS = {"n": "north", "e": "east", "s": "south", "w": "west"}
_EDGE_FORMS = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*[:, ]\s*([nesw])(?:orth|ast|outh|est)?\s*$", re.IGNORECASE)


def normalize_edge(text: Any) -> str | None:
    """An edge address written loosely (`4,69 north`, `4,69:n`, `17,25,e`) as `x,y:side`, or `None`."""
    if not isinstance(text, str):
        return None
    match = _EDGE_FORMS.match(text)
    if match is None:
        return None
    return f"{int(match.group(1))},{int(match.group(2))}:{_SIDE_WORDS[match.group(3).lower()]}"


def normalize_addresses(answer: dict[str, Any]) -> None:
    """Rewrite loosely written edge addresses in an answer's marks and symbols to `x,y:side`, in place."""
    for mark in answer.get("marks", []) or []:
        if isinstance(mark, dict) and mark.get("on_door") is not None:
            mark["on_door"] = normalize_edge(mark["on_door"]) or mark["on_door"]
    for symbol in answer.get("symbols", []) or []:
        if isinstance(symbol, dict) and symbol.get("edge") is not None:
            symbol["edge"] = normalize_edge(symbol["edge"]) or symbol["edge"]
            if symbol.get("kind") == "cave_mouth" and symbol.get("cells") == []:
                pass


def check(answer: dict[str, Any], window: Window) -> list[str]:
    """List what is malformed in a window answer.

    Loosely written edge addresses (`4,69 north`, `4,69:n`) are rewritten to `x,y:side` first, in place, so a
    reading that differs only in that form isn't asked again.

    Args:
        answer: The parsed JSON answer.
        window: The window it answers.

    Returns:
        One sentence per fault, empty when the answer is well formed.
    """
    normalize_addresses(answer)
    faults: list[str] = []
    cells = answer.get("cells")
    vertical = answer.get("vertical")
    horizontal = answer.get("horizontal")
    w, h = window.columns, window.rows
    for name, rows, count, length, letters in (
        ("cells", cells, h, w, CELL_LETTERS),
        ("vertical", vertical, h, w + 1, READ_LETTERS),
        ("horizontal", horizontal, h + 1, w, READ_LETTERS),
    ):
        if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
            faults.append(f"{name} must be a list of strings")
            continue
        if len(rows) != count:
            faults.append(f"{name} has {len(rows)} strings but needs exactly {count}")
        for index, row in enumerate(rows):
            if len(row) != length:
                faults.append(f"{name} string {index} has {len(row)} letters but needs exactly {length}")
            bad = sorted({letter for letter in row if letter not in letters})
            if bad:
                faults.append(f"{name} string {index} uses letters outside {letters!r}: {bad}")

    def inside(text: str, what: str) -> None:
        try:
            cell = parse_cell(text)
        except ValueError:
            faults.append(f"{what} names {text!r}, which isn't a cell 'x,y'")
            return
        if not window.contains(cell):
            faults.append(f"{what} names cell {text}, which is outside the window")

    for text in answer.get("partial", []):
        inside(text, "partial")
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
            edge = symbol.get("edge")
            if symbol.get("cells") and normalize_edge(edge) is not None:
                symbol["cells"] = []
            if symbol.get("cells"):
                faults.append("a cave_mouth has an edge and no cells")
            try:
                cell, _side = parse_edge(edge or "")
            except ValueError:
                faults.append(f"cave_mouth edge {edge!r} isn't an edge 'x,y:side'")
            else:
                if not window.contains(cell):
                    faults.append(f"cave_mouth edge {edge} is outside the window")
        else:
            if not symbol.get("cells"):
                faults.append(f"a {kind} needs at least one cell")
            for text in symbol.get("cells", []):
                inside(text, kind)
        direction = symbol.get("direction")
        if direction not in (None, "up", "down"):
            faults.append(f"direction {direction!r} must be up, down, or null")
    return faults


def retry_params(params: dict[str, Any], answer_text: str, faults: list[str]) -> dict[str, Any]:
    """Build the retry of a malformed answer: the request, the answer, and a message naming the faults.

    Args:
        params: The original request's parameters.
        answer_text: The malformed answer, as the model wrote it.
        faults: What `check` found.

    Returns:
        The retry's parameters.
    """
    message = (
        "Your answer was malformed, so read the window again and answer once more in full. The faults: "
        + " ".join(f"({index}) {fault}." for index, fault in enumerate(faults, 1))
        + " Every string must have exactly the length the request states, and every named cell must lie in the "
        "window."
    )
    return {
        **params,
        "messages": [
            *params["messages"],
            {"role": "assistant", "content": [{"type": "text", "text": answer_text}]},
            {"role": "user", "content": [{"type": "text", "text": message}]},
        ],
    }


@dataclass
class WindowAnswer:
    """A well-formed window answer, parsed into absolute coordinates."""

    window: Window
    cells: dict[tuple[int, int], str] = field(default_factory=dict)
    edges: dict[tuple[str, int, int], str] = field(default_factory=dict)
    """Edges keyed `("v", line, row)` for a vertical line at `x = line` beside row `row`, or `("h", column,
    line)` for a horizontal line at `y = line` above column `column`. Letters are the answer's; a rock-rock edge
    is `-`."""

    partial: set[tuple[int, int]] = field(default_factory=set)
    marks: list[dict[str, Any]] = field(default_factory=list)
    symbols: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""


def parse(answer: dict[str, Any], window: Window) -> WindowAnswer:
    """Turn a checked answer into absolute cells and edges.

    Args:
        answer: A window answer `check` found well formed.
        window: Its window.

    Returns:
        The parsed answer. An edge both of whose cells (within the window) are rock, or whose one cell in the
        window is rock when the other lies outside it, is `-`; `parse` doesn't know what lies beyond the window,
        so a window-border edge beside a rock cell becomes `-` here and the merge decides it from the whole map.
    """
    parsed = WindowAnswer(window=window, notes=answer.get("notes", ""))
    for j, row in enumerate(answer["cells"]):
        for i, letter in enumerate(row):
            parsed.cells[(window.x0 + i, window.y0 + j)] = letter

    def occupied(cell: tuple[int, int]) -> bool | None:
        letter = parsed.cells.get(cell)
        return None if letter is None else letter != "#"

    for j, row in enumerate(answer["vertical"]):
        y = window.y0 + j
        for i, letter in enumerate(row):
            x = window.x0 + i
            west, east = occupied((x - 1, y)), occupied((x, y))
            if not (west or east):
                letter = "-"
            parsed.edges[("v", x, y)] = letter
    for j, row in enumerate(answer["horizontal"]):
        y = window.y0 + j
        for i, letter in enumerate(row):
            x = window.x0 + i
            north, south = occupied((x, y - 1)), occupied((x, y))
            if not (north or south):
                letter = "-"
            parsed.edges[("h", x, y)] = letter
    parsed.partial = {parse_cell(text) for text in answer.get("partial", [])}
    parsed.marks = [dict(mark) for mark in answer.get("marks", [])]
    parsed.symbols = [dict(symbol) for symbol in answer.get("symbols", [])]
    return parsed


def answer_text_of(message: dict[str, Any]) -> str:
    """The text of a batch result's message: its text blocks joined."""
    return "".join(block.get("text", "") for block in message.get("content", []) if block.get("type") == "text")


def parse_answer_text(text: str) -> dict[str, Any] | None:
    """Parse an answer's text as JSON, or `None` when it isn't JSON."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
