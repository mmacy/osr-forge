"""Settle a window by hand: crop it, expand a sketch into a settled window, and compare it with a reading.

A *sketch* is the short form a person writes from the window crop: the cell
rows, the edges between floor cells that aren't open, and the partly floor
cells. `expand` turns it into a full settled window (see `score.py` for the
format): an edge between two floor cells is open unless the sketch lists it,
an edge between floor and rock is a wall, and an edge between two rock cells
has no reading. `diff` lists where a reading disagrees with a settled window
and writes a zoomed crop of each item, so the person can decide each one and
record the crop's path under `checked`.

Run from the osr-forge checkout:

    uv run python -m tools.answer_keys.settle crop <specs> <map id> <x0,y0> <out.png>
    uv run python -m tools.answer_keys.settle expand <sketch.json> <settled.json>
    uv run python -m tools.answer_keys.settle diff <specs> <readings dir> <settled.json> <crops dir>
    uv run python -m tools.answer_keys.settle from-reading <readings dir> <map id> <x0,y0> <sketch.json>

`from-reading` writes a sketch filled from the classifier's reading, for a person to correct item by item from
zoomed crops. A window settled that way records the corrections under `checked`, and its notes say so.

A sketch file has `map`, `window`, `crop`, `cells`, `blocked` (a list of `["x,y:side", letter]`), `partial`,
`unsure` (items to leave `?`), `beyond` (the letters of cells just outside the window, `{"x,y": letter}`, so the
edges on the window's border can be settled), `outside` (cells that are ground outside every building, which the
scorer can leave out), and `notes`.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from osrforge.tracing.truth import parse_cell, parse_edge

from .crops import render_item, render_window
from .reading import load_reading
from .run import read_map
from .score import load_settled, score_window
from .spec import load_specs


def expand(sketch: dict[str, object]) -> dict[str, object]:
    """Turn a sketch into a settled window.

    Args:
        sketch: The sketch document.

    Returns:
        The settled window document.
    """
    x0, y0, columns, rows = sketch["window"]  # type: ignore[misc]
    cells = list(sketch["cells"])  # type: ignore[arg-type]
    if len(cells) != rows or any(len(row) != columns for row in cells):
        raise ValueError(f"cells must be {rows} rows of {columns} letters")

    beyond = dict(sketch.get("beyond", {}))  # type: ignore[arg-type]

    def occupied(x: int, y: int) -> bool | None:
        i, j = x - x0, y - y0
        if 0 <= i < columns and 0 <= j < rows:
            letter = cells[j][i]
            return None if letter == "?" else letter in ".~"
        letter = beyond.get(f"{x},{y}")
        return None if letter in (None, "?") else letter in ".~"

    blocked: dict[tuple[tuple[int, int], str], str] = {}
    for text, letter in sketch.get("blocked", []):  # type: ignore[union-attr]
        cell, side = parse_edge(text)
        x, y = cell
        if side == "east":
            cell, side = (x + 1, y), "west"
        elif side == "south":
            cell, side = (x, y + 1), "north"
        blocked[(cell, side)] = letter
    unsure = set(sketch.get("unsure", []))  # type: ignore[arg-type]

    def letter_for(cell: tuple[int, int], side: str) -> str:
        x, y = cell
        other = (x - 1, y) if side == "west" else (x, y - 1)
        key = f"{x},{y}:{side}"
        alt = f"{other[0]},{other[1]}:{'east' if side == 'west' else 'south'}"
        if key in unsure or alt in unsure:
            return "?"
        a, b = occupied(x, y), occupied(*other)
        if a is None and b is None:
            return "?"
        if (cell, side) in blocked:
            return blocked[(cell, side)]
        if a is None or b is None:
            return "?"
        if a and b:
            return "o"
        if a or b:
            return "w"
        return "-"

    vertical = ["".join(letter_for((x0 + i, y0 + j), "west") for i in range(columns + 1)) for j in range(rows)]
    horizontal = ["".join(letter_for((x0 + i, y0 + j), "north") for i in range(columns)) for j in range(rows + 1)]
    for (cell, side), _letter in blocked.items():
        x, y = cell
        i, j = x - x0, y - y0
        inside = (side == "west" and 0 <= j < rows and 0 <= i <= columns) or (
            side == "north" and 0 <= i < columns and 0 <= j <= rows
        )
        if not inside:
            raise ValueError(f"blocked edge {x},{y}:{side} lies outside the window")
    for text in sketch.get("partial", []):  # type: ignore[union-attr]
        parse_cell(text)
    return {
        "map": sketch["map"],
        "window": [x0, y0, columns, rows],
        "crop": sketch.get("crop", ""),
        "cells": cells,
        "vertical": vertical,
        "horizontal": horizontal,
        "partial": list(sketch.get("partial", [])),  # type: ignore[arg-type]
        "outside": list(sketch.get("outside", [])),  # type: ignore[arg-type]
        "checked": dict(sketch.get("checked", {})),  # type: ignore[arg-type]
        "notes": sketch.get("notes", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    crop = sub.add_parser("crop", help="write a window crop with the grid drawn on")
    crop.add_argument("specs", type=Path)
    crop.add_argument("map")
    crop.add_argument("window")
    crop.add_argument("out", type=Path)
    crop.add_argument("--cell-px", type=int, default=72)
    crop.add_argument("--size", default="12,12")
    exp = sub.add_parser("expand", help="turn a sketch into a settled window")
    exp.add_argument("sketch", type=Path)
    exp.add_argument("out", type=Path)
    diff = sub.add_parser("diff", help="compare a reading with a settled window and crop each difference")
    diff.add_argument("specs", type=Path)
    diff.add_argument("readings", type=Path)
    diff.add_argument("settled", type=Path)
    diff.add_argument("crops", type=Path)
    diff.add_argument("--cell-px", type=int, default=110)
    fr = sub.add_parser("from-reading", help="write a sketch filled from a reading, to correct by hand")
    fr.add_argument("readings", type=Path)
    fr.add_argument("map")
    fr.add_argument("window")
    fr.add_argument("out", type=Path)
    args = parser.parse_args()
    if args.command == "crop":
        specs = {spec.id: spec for spec in load_specs(args.specs)}
        spec = specs[args.map]
        rgb = np.asarray(Image.open(spec.image).convert("RGB"))
        fit, _reading = read_map(spec, rgb)
        x0, y0 = (int(v) for v in args.window.split(","))
        columns, rows = (int(v) for v in args.size.split(","))
        print(render_window(rgb, fit, x0, y0, columns, rows, args.out, cell_px=args.cell_px))
    elif args.command == "from-reading":
        reading = load_reading(args.readings / f"{args.map}.json")
        x0, y0 = (int(v) for v in args.window.split(","))
        panel = reading.panel
        cells = [panel.cells[y][x0 : x0 + 12] for y in range(y0, y0 + 12)]
        blocked = []
        for y in range(y0, y0 + 12):
            for i in range(x0, x0 + 13):
                letter = panel.edges.vertical[y][i]
                if letter not in "o-":
                    blocked.append([f"{i},{y}:west", letter])
        for j in range(y0, y0 + 13):
            for x in range(x0, x0 + 12):
                letter = panel.edges.horizontal[j][x]
                if letter not in "o-":
                    blocked.append([f"{x},{j}:north", letter])
        partial = [c for c in panel.partial if x0 <= parse_cell(c)[0] < x0 + 12 and y0 <= parse_cell(c)[1] < y0 + 12]
        beyond = {}
        for y in range(y0, y0 + 12):
            for x in (x0 - 1, x0 + 12):
                if 0 <= x < panel.width:
                    beyond[f"{x},{y}"] = panel.cells[y][x]
        for x in range(x0, x0 + 12):
            for y in (y0 - 1, y0 + 12):
                if 0 <= y < panel.height:
                    beyond[f"{x},{y}"] = panel.cells[y][x]
        sketch = {
            "map": args.map,
            "window": [x0, y0, 12, 12],
            "crop": "",
            "cells": cells,
            "blocked": blocked,
            "partial": partial,
            "unsure": [],
            "beyond": beyond,
            "outside": [],
            "checked": {},
            "notes": "Filled from the classifier's reading; corrected by hand from zoomed crops.",
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(sketch, indent=1) + "\n")
        print(args.out)
    elif args.command == "expand":
        settled = expand(json.loads(args.sketch.read_text()))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(settled, indent=1) + "\n")
        print(args.out)
    else:
        specs = {spec.id: spec for spec in load_specs(args.specs)}
        settled = load_settled(args.settled)
        spec = specs[settled.map]
        reading = load_reading(args.readings / f"{settled.map}.json")
        scores = score_window(reading.panel, settled)
        rgb = np.asarray(Image.open(spec.image).convert("RGB"))
        fit, _reading = read_map(spec, rgb)
        for d in scores["all"]["differences"]:
            item = d["item"]
            if ":" in item:
                cell, side = parse_edge(item)
            else:
                cell, side = parse_cell(item), None
            out = args.crops / (item.replace(":", "_").replace(",", "-") + ".png")
            render_item(rgb, fit, cell, side, out, cell_px=args.cell_px, reach=1.5)
            print(f"{item:14s} settled {d['settled']} read {d['read']}  {out}")
        s = scores["all"]
        print(
            f"cells {s['cells']} edges {s['edges']} exact {s['exact']} "
            f"w>o {s['walls_read_open']} o>w {s['open_read_wall']}"
        )


if __name__ == "__main__":
    main()
