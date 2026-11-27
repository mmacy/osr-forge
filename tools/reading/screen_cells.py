"""The ink's cells on whole maps, and Clef's cell question on the cells the ink leaves unsettled, per style.

The ink figures cover every cell of every map, the cells the key marks as partly floor apart from the rest:

- floor or not right by the half rule (a cell is floor when at least half of it is)
- the cells left unsettled (`ink.unsettled_cells`), and the settled cells that are wrong
- the water cells, and how many of them are left unsettled
- per map, every cell left unsettled by its reason (`ink.unsettled_reasons`): partly floor, ink inside, the grid on
  fewer than two sides, or a tone off the rock's

The Clef figures cover the cells a cell store answered (`clefask --kind cell`, the cells the ink leaves unsettled in
the screening windows):

- floor or not right by Clef's most probable answer, where a symbol on floor is floor, and by the ink's half rule
  on the same cells
- the water cells read as water
- the key's symbol cells named as a symbol, and named as their own kind, by kind
- plain floor called a symbol

Run from the osr-forge checkout:

    uv run python -m tools.reading.screen_cells <map file>... [--synthetic <dir>] [--answers <dir>] [--json <file>]

`--answers` names the directory that holds `<map id>.cell.jsonl`. `--synthetic` adds every registered region of the
synthetic maps under a directory, such as `tests/assets/synthetic-maps`, each with its answer as its key.
"""

import argparse
import json
from collections import Counter
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from osrforge.tracing.truth import Panel, parse_cell

from .answers import load_answers
from .dataset import map_ink
from .full_members import synthetic_members
from .full_reading import region_key_panel
from .ink import unsettled_reasons
from .maps import STYLES, ScreenMap, key_document, load_maps

__all__ = ["SYMBOLS", "clef_tally", "ink_tally", "main"]

SYMBOLS = frozenset({"stairs", "ladder", "trapdoor", "pit", "pillar", "statue", "pool", "bridge"})
"""The cell question's options that name a symbol on floor."""

_STAIRS = frozenset({"stairs", "steps", "ladder", "footing"})
"""The key's symbol kinds that the cell question's `stairs` names: steps of any kind."""


def ink_tally(
    panel: Panel, floors: np.ndarray, unsettled: Collection[tuple[int, int]] | Mapping[tuple[int, int], str]
) -> Counter[str]:
    """The ink's figures on one map's cells.

    Args:
        panel: The key's panel.
        floors: Each cell's floor fraction, indexed by row and then column.
        unsettled: The cells the ink leaves unsettled, or each one with its reason, which then adds
            `unsettled: <reason>` over every cell.

    Returns:
        `<group> cells`, `<group> right`, `<group> unsettled`, and `<group> settled wrong`, where the group is
        `partial` for the cells the key marks as partly floor and `whole` for the rest, and `water cells` and
        `water unsettled`.
    """
    partial = {parse_cell(text) for text in panel.partial}
    tally: Counter[str] = Counter()
    for y in range(panel.height):
        for x in range(panel.width):
            key_floor = panel.cells[y][x] == "."
            read_floor = bool(floors[y, x] >= 0.5)
            group = "partial" if (x, y) in partial else "whole"
            tally[f"{group} cells"] += 1
            tally[f"{group} right"] += key_floor == read_floor
            tally[f"{group} unsettled"] += (x, y) in unsettled
            tally[f"{group} settled wrong"] += (x, y) not in unsettled and key_floor != read_floor
            if panel.cells[y][x] == "~":
                tally["water cells"] += 1
                tally["water unsettled"] += (x, y) in unsettled
            if isinstance(unsettled, Mapping) and (x, y) in unsettled:
                tally[f"unsettled: {unsettled[(x, y)]}"] += 1
    return tally


def clef_tally(
    panel: Panel, answers: Mapping[tuple[int, int], Mapping[str, float]], floors: np.ndarray
) -> Counter[str]:
    """Clef's figures on one map's answered cells.

    Args:
        panel: The key's panel.
        answers: Clef's probabilities by option, for each answered cell.
        floors: Each cell's floor fraction, indexed by row and then column.

    Returns:
        `asked`, `floor or not right, Clef`, `floor or not right, the ink`, `water cells`, `water read as water`,
        `<kind> cells`, `<kind> named a symbol`, and `<kind> named its kind` for each symbol kind on the asked cells,
        `symbol cells` and `symbol cells named a symbol` over every kind, and `plain floor called a symbol`.
    """
    symbols = {parse_cell(cell): symbol.kind for symbol in panel.symbols for cell in symbol.cells}
    tally: Counter[str] = Counter()
    for (x, y), probabilities in answers.items():
        top = max(probabilities, key=probabilities.__getitem__)
        key = panel.cells[y][x]
        tally["asked"] += 1
        tally["floor or not right, Clef"] += (top == "floor" or top in SYMBOLS) == (key == ".")
        tally["floor or not right, the ink"] += bool(floors[y, x] >= 0.5) == (key == ".")
        if key == "~":
            tally["water cells"] += 1
            tally["water read as water"] += top == "water"
        kind = symbols.get((x, y))
        if kind is not None and (kind in SYMBOLS or kind in _STAIRS):
            tally["symbol cells"] += 1
            tally["symbol cells named a symbol"] += top in SYMBOLS
            tally[f"{kind} cells"] += 1
            tally[f"{kind} named a symbol"] += top in SYMBOLS
            tally[f"{kind} named its kind"] += top == kind or (top == "stairs" and kind in _STAIRS)
        elif key == "." and top in SYMBOLS:
            tally["plain floor called a symbol"] += 1
    return tally


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The ink's cells and Clef's cell question, per style.")
    parser.add_argument("map_files", type=Path, nargs="*")
    parser.add_argument("--synthetic", type=Path, default=None, help="a directory of synthetic maps to add")
    parser.add_argument("--answers", type=Path, default=None, help="the directory that holds <map id>.cell.jsonl")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    maps: list[tuple[str, ScreenMap]] = [(screen.id, screen) for path in args.map_files for screen in load_maps(path)]
    if args.synthetic is not None:
        maps += [
            (run_map.uid, run_map.screen) for member in synthetic_members(args.synthetic) for run_map in member.maps
        ]
    result: dict[str, Any] = {}
    for style in STYLES:
        ink: Counter[str] = Counter()
        clef: Counter[str] = Counter()
        per_map: dict[str, dict[str, int]] = {}
        for uid, screen in [(uid, screen) for uid, screen in maps if screen.style == style]:
            panel = region_key_panel(key_document(screen.key), screen.id)
            measure, floors = map_ink(screen)
            cells = [(x, y) for y in range(panel.height) for x in range(panel.width)]
            mine = ink_tally(panel, floors, unsettled_reasons(measure, floors, cells))
            ink.update(mine)
            reasons = {name: count for name, count in sorted(mine.items()) if name.startswith("unsettled: ")}
            per_map[uid] = {"cells": len(cells), "unsettled": sum(reasons.values()), **reasons}
            print(f"{uid} | {style} | {json.dumps(per_map[uid])}", flush=True)
            path = args.answers / f"{screen.id}.cell.jsonl" if args.answers else None
            if path is not None and path.exists():
                answers = load_answers(path, None, panel.width, panel.height).cells
                clef.update(clef_tally(panel, answers, floors))
        if not ink:
            continue
        result[style] = {"ink": dict(sorted(ink.items())), "clef": dict(sorted(clef.items())), "maps": per_map}
        print(f"{style}: ink {result[style]['ink']}", flush=True)
        if clef:
            print(f"{style}: clef {result[style]['clef']}", flush=True)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
