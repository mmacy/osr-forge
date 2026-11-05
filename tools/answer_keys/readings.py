"""Every reading of every item of a map in one table, and the disagreements the settler decides.

An *item* is one cell, one edge, one label, or one symbol. The table (`items.json` in the map's directory)
records each reader's value for it: the classifier's letter, Claude's letter from each window that covered
it, and Clef's choice with its probability. An item is *disputed* when its readers disagree:

- a cell, when Claude's windows differ, or the classifier's letter isn't among Claude's
- an edge, when any two letters differ, or when Clef's choice is passable where the letters are blocked or
  the other way round, or names a different kind of door
- a label, when the two label readings place the same text in different cells, or one has a label the other
  lacks
- a symbol, when the two readings differ in kind or cells, or one lacks it.

Edges are named from their north or west cell (`x,y:west` or `x,y:north`), except the east and south frame
edges, named from the cell inside them. Cells settle first: an edge beside a cell settled as rock is a wall
when its other side is floor, with no question asked, unless a reader read a door there.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from osrforge.tracing.truth import DOOR_LETTERS, PASSABLE_LETTERS, Panel, parse_cell, parse_edge

from .keyspec import MapContext
from .reading import ReadingFile, load_reading
from .windows import parsed_windows

__all__ = [
    "Items",
    "classifier_reading",
    "claude_cells",
    "edge_name",
    "edges_touching",
    "load_items",
    "write_items",
]

CLEF_LETTER = {"open": "o", "wall": "w", "door": "d", "locked_door": "l", "secret_door": "s", "window": "n"}
CLEF_DISAGREEMENT_MIN = 0.5
"""Clef's top probability at which its answer counts as a disagreement with two readers who agree. Under it, Clef
is unsure among its options rather than disagreeing, and the agreed letter stands."""
CLEF_CELL = {"floor": ".", "rock": "#", "water": "~"}


def classifier_reading(ctx: MapContext) -> ReadingFile:
    """The classifier's reading of the map, from the work directory."""
    return load_reading(ctx.key.work / "classifier" / f"{ctx.key.id}.json")


def claude_cells(ctx: MapContext) -> dict[tuple[int, int], set[str]]:
    """Claude's letters for each cell, one per window that read it."""
    out: dict[tuple[int, int], set[str]] = defaultdict(set)
    for window in parsed_windows(ctx):
        for text, letter in window["cells"].items():
            out[parse_cell(text)].add(letter)
    return dict(out)


def claude_edges(ctx: MapContext) -> dict[tuple[str, int, int], set[str]]:
    """Claude's letters for each edge, keyed `(axis, line, index)`, one per window that read it."""
    out: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    for window in parsed_windows(ctx):
        for text, letter in window["edges"].items():
            axis, line, index = text.split(",")
            out[(axis, int(line), int(index))].add(letter)
    return dict(out)


def claude_partial(ctx: MapContext) -> set[tuple[int, int]]:
    """The cells Claude listed as partly floor in any window."""
    return {parse_cell(text) for window in parsed_windows(ctx) for text in window["partial"]}


def edge_name(key: tuple[str, int, int], width: int, height: int) -> str:
    """An edge's name from its canonical key: `x,y:west` or `x,y:north`, or `east`/`south` at the far frame.

    A key is `("v", line, row)` for the vertical line `x = line` beside row `row`, or `("h", column, line)` for
    the horizontal line `y = line` above column `column`.
    """
    axis, first, second = key
    if axis == "v":
        return f"{first},{second}:west" if first < width else f"{first - 1},{second}:east"
    return f"{first},{second}:north" if second < height else f"{first},{second - 1}:south"


def edge_key(name: str) -> tuple[str, int, int]:
    """The canonical key of an edge name."""
    (x, y), side = parse_edge(name)
    if side == "west":
        return ("v", x, y)
    if side == "east":
        return ("v", x + 1, y)
    if side == "north":
        return ("h", x, y)
    return ("h", x, y + 1)


def edge_cells(key: tuple[str, int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    """The two cells beside an edge, the north or west one first."""
    axis, first, second = key
    if axis == "v":
        return (first - 1, second), (first, second)
    return (first, second - 1), (first, second)


def panel_cell(panel: Panel, cell: tuple[int, int]) -> str:
    """A panel's letter at a cell, `#` off the grid."""
    x, y = cell
    if 0 <= x < panel.width and 0 <= y < panel.height:
        return panel.cells[y][x]
    return "#"


def panel_edge(panel: Panel, key: tuple[str, int, int]) -> str:
    """A panel's letter on an edge."""
    axis, first, second = key
    if axis == "v":
        return panel.edges.vertical[second][first]
    return panel.edges.horizontal[second][first]


def edges_touching(panel: Panel, claude: dict[tuple[int, int], set[str]]) -> list[tuple[tuple[int, int], str]]:
    """Every edge with floor or water on either side in the classifier's reading or in any of Claude's.

    Returns:
        `(cell, side)` pairs, each edge once, in row-major order of its name.
    """

    def occupied(cell: tuple[int, int]) -> bool:
        if panel_cell(panel, cell) != "#":
            return True
        return any(letter != "#" for letter in claude.get(cell, ()))

    out = []
    for key in _all_edges(panel.width, panel.height):
        a, b = edge_cells(key)
        if occupied(a) or occupied(b):
            out.append(parse_edge(edge_name(key, panel.width, panel.height)))
    return out


def _on_frame(key: tuple[str, int, int], width: int, height: int) -> bool:
    """Whether an edge lies on the region's outer border."""
    axis, first, second = key
    return (first in (0, width)) if axis == "v" else (second in (0, height))


def _all_edges(width: int, height: int):
    for y in range(height):
        for x in range(width + 1):
            yield ("v", x, y)
    for y in range(height + 1):
        for x in range(width):
            yield ("h", x, y)


def _edge_class(letter: str) -> str:
    if letter in PASSABLE_LETTERS:
        return "passable"
    if letter in DOOR_LETTERS:
        return "door:" + letter
    return "blocked"


@dataclass
class Items:
    """The table of readings."""

    cells: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[str, dict[str, Any]] = field(default_factory=dict)
    marks: list[dict[str, Any]] = field(default_factory=list)
    symbols: list[dict[str, Any]] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)
    cave: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0

    def disputed(self, kind: str) -> list[str]:
        """The ids of the disputed items of one kind, in table order."""
        table = self.cells if kind == "cells" else self.edges
        return [name for name, item in table.items() if item["status"] == "disputed"]


def _normalize_text(text: str) -> str:
    return "".join(text.split()).lower()


def _dedupe_marks(marks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge marks of one text that lie within a cell of each other into one, keeping every cell read."""
    out: list[dict[str, Any]] = []
    for mark in marks:
        cell = parse_cell(mark["cell"])
        for seen in out:
            if _normalize_text(seen["text"]) != _normalize_text(mark["text"]):
                continue
            if any(max(abs(cell[0] - c[0]), abs(cell[1] - c[1])) <= 1 for c in seen["cells"]):
                seen["cells"].append(cell)
                break
        else:
            out.append(
                {
                    "text": mark["text"],
                    "cells": [cell],
                    "on_door": mark.get("on_door"),
                    "leader_to": mark.get("leader_to"),
                }
            )
    return out


def _match_marks(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair the two label readings and say which labels are disputed."""
    used = set()
    out = []
    for mark in first:
        best = None
        for index, other in enumerate(second):
            if index in used or _normalize_text(other["text"]) != _normalize_text(mark["text"]):
                continue
            distance = min(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a in mark["cells"] for b in other["cells"])
            if distance <= 2 and (best is None or distance < best[0]):
                best = (distance, index)
        cells_1 = sorted(set(mark["cells"]))
        if best is None:
            out.append(
                {
                    "text": mark["text"],
                    "reading1": cells_1,
                    "reading2": [],
                    "status": "disputed",
                    "on_door": mark.get("on_door"),
                    "leader_to": mark.get("leader_to"),
                }
            )
            continue
        used.add(best[1])
        other = second[best[1]]
        cells_2 = sorted(set(other["cells"]))
        agreed = len(cells_1) == 1 and cells_1 == cells_2
        out.append(
            {
                "text": mark["text"],
                "reading1": cells_1,
                "reading2": cells_2,
                "status": "agreed" if agreed else "disputed",
                "on_door": mark.get("on_door") or other.get("on_door"),
                "leader_to": mark.get("leader_to") or other.get("leader_to"),
            }
        )
    for index, other in enumerate(second):
        if index not in used:
            out.append(
                {
                    "text": other["text"],
                    "reading1": [],
                    "reading2": sorted(set(other["cells"])),
                    "status": "disputed",
                    "on_door": other.get("on_door"),
                    "leader_to": other.get("leader_to"),
                }
            )
    for item in out:
        item["reading1"] = [f"{x},{y}" for x, y in item["reading1"]]
        item["reading2"] = [f"{x},{y}" for x, y in item["reading2"]]
    return out


def _symbol_key(symbol: dict[str, Any]) -> tuple[str, frozenset[str], str | None]:
    return (symbol["kind"], frozenset(symbol.get("cells") or ()), symbol.get("edge"))


def _match_symbols(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair the two symbol readings: the same kind on overlapping cells (or the same cave-mouth edge) agree."""

    def overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
        if a["kind"] != b["kind"]:
            return False
        if a["kind"] == "cave_mouth":
            (ax, ay), aside = parse_edge(a["edge"])
            (bx, by), bside = parse_edge(b["edge"])
            return aside == bside and max(abs(ax - bx), abs(ay - by)) <= 1
        return bool(set(a["cells"]) & set(b["cells"]))

    used = set()
    out = []
    for symbol in first:
        match = next((i for i, other in enumerate(second) if i not in used and overlaps(symbol, other)), None)
        if match is None:
            out.append({"reading1": symbol, "reading2": None, "status": "disputed"})
            continue
        used.add(match)
        other = second[match]
        same = _symbol_key(symbol) == _symbol_key(other) and symbol.get("direction") == other.get("direction")
        out.append({"reading1": symbol, "reading2": other, "status": "agreed" if same else "disputed"})
    for index, other in enumerate(second):
        if index not in used:
            out.append({"reading1": None, "reading2": other, "status": "disputed"})
    return out


def _dedupe_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = set()
    for symbol in symbols:
        key = _symbol_key(symbol)
        if key in seen:
            continue
        merged = False
        for kept in out:
            if (
                kept["kind"] == symbol["kind"]
                and symbol["kind"] != "cave_mouth"
                and set(kept["cells"]) & set(symbol["cells"])
            ):
                kept["cells"] = sorted(set(kept["cells"]) | set(symbol["cells"]))
                merged = True
                break
        if not merged:
            out.append(
                {
                    "kind": symbol["kind"],
                    "cells": sorted(symbol.get("cells") or []),
                    "edge": symbol.get("edge"),
                    "direction": symbol.get("direction"),
                }
            )
        seen.add(key)
    return out


def build_items(
    ctx: MapContext, clef: dict[str, dict[str, Any]], marks_2: list[dict[str, Any]], symbols_2: list[dict[str, Any]]
) -> Items:
    """Build the table from the classifier's reading, Claude's windows, Clef's answers, and the marks pass."""
    reading = classifier_reading(ctx)
    panel = reading.panel
    cells_1 = claude_cells(ctx)
    edges_1 = claude_edges(ctx)
    items = Items(width=panel.width, height=panel.height)
    for y in range(panel.height):
        for x in range(panel.width):
            name = f"{x},{y}"
            classifier = panel.cells[y][x]
            claude = sorted(cells_1.get((x, y), set()))
            entry: dict[str, Any] = {"classifier": classifier, "claude": claude}
            answer = clef.get(name)
            if answer is not None:
                entry["clef"] = {
                    "choice": CLEF_CELL.get(answer["choice"], answer["choice"]),
                    "p": round(answer["probabilities"][answer["choice"]], 3),
                }
            letters = set(claude) | {classifier}
            if answer is not None:
                letters.add(entry["clef"]["choice"])
            entry["status"] = "disputed" if len(letters) > 1 or not claude else "agreed"
            if not claude:
                entry["status"] = "unread"
            items.cells[name] = entry
    for cell, side in edges_touching(panel, cells_1):
        key = edge_key(f"{cell[0]},{cell[1]}:{side}")
        name = edge_name(key, panel.width, panel.height)
        classifier = panel_edge(panel, key)
        if _on_frame(key, panel.width, panel.height):
            # The classifier reads the drawing's frame line as a wall; whether floor runs off the frame is a
            # question for the readers that see the map, so it abstains here.
            classifier = "?"
        claude = sorted(edges_1.get(key, set()))
        entry = {"classifier": classifier, "claude": claude}
        answer = clef.get(name)
        if answer is not None:
            entry["clef"] = {
                "choice": CLEF_LETTER.get(answer["choice"], answer["choice"]),
                "p": round(answer["probabilities"][answer["choice"]], 3),
            }
        letters = {letter for letter in [classifier, *claude] if letter not in "-?"}
        status = "agreed"
        if not claude:
            status = "unread"
        elif len(letters) > 1 or len(claude) > 1:
            status = "disputed"
        elif answer is not None and letters and entry["clef"]["p"] >= CLEF_DISAGREEMENT_MIN:
            agreed = next(iter(letters))
            clef_letter = entry["clef"]["choice"]
            if _edge_class(clef_letter) != _edge_class(agreed) and not (agreed == "p" and clef_letter == "o"):
                status = "disputed"
        entry["status"] = status
        items.edges[name] = entry
    marks_1 = _dedupe_marks([mark for window in parsed_windows(ctx) for mark in window["marks"]])
    items.marks = _match_marks(marks_1, _dedupe_marks(marks_2))
    symbols_1 = _dedupe_symbols([symbol for window in parsed_windows(ctx) for symbol in window["symbols"]])
    items.symbols = _match_symbols(symbols_1, _dedupe_symbols(symbols_2))
    partial = set(parse_cell(text) for text in panel.partial) | claude_partial(ctx)
    items.partial = sorted((f"{x},{y}" for x, y in partial), key=lambda t: parse_cell(t)[::-1])
    items.cave = list(panel.cave)
    return items


def items_path(ctx: MapContext) -> Path:
    return ctx.key.directory / "items.json"


def write_items(ctx: MapContext) -> dict[str, int]:
    """Build the table and write it. Returns the counts of items and disputes by kind."""
    from .clefpass import clef_answers
    from .marks import parsed_marks

    marks_2 = [mark for window in parsed_marks(ctx) for mark in window["marks"]]
    symbols_2 = [symbol for window in parsed_marks(ctx) for symbol in window["symbols"]]
    items = build_items(ctx, clef_answers(ctx), marks_2, symbols_2)
    document = {
        "width": items.width,
        "height": items.height,
        "cells": items.cells,
        "edges": items.edges,
        "marks": items.marks,
        "symbols": items.symbols,
        "partial": items.partial,
        "cave": items.cave,
    }
    items_path(ctx).write_text(json.dumps(document, indent=1) + "\n")
    counts = {
        "cells": len(items.cells),
        "cells_disputed": len(items.disputed("cells")),
        "cells_unread": sum(1 for item in items.cells.values() if item["status"] == "unread"),
        "edges": len(items.edges),
        "edges_disputed": len(items.disputed("edges")),
        "edges_unread": sum(1 for item in items.edges.values() if item["status"] == "unread"),
        "marks": len(items.marks),
        "marks_disputed": sum(1 for item in items.marks if item["status"] == "disputed"),
        "symbols": len(items.symbols),
        "symbols_disputed": sum(1 for item in items.symbols if item["status"] == "disputed"),
    }
    return counts


def load_items(ctx: MapContext) -> Items:
    """Read the table back."""
    document = json.loads(items_path(ctx).read_text())
    return Items(
        cells=document["cells"],
        edges=document["edges"],
        marks=document["marks"],
        symbols=document["symbols"],
        partial=document["partial"],
        cave=document["cave"],
        width=document["width"],
        height=document["height"],
    )
