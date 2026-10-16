"""Score a reading against settled windows.

A settled window is a JSON file: a 12 by 12 window of one map whose cells and
edges a person settled from zoomed crops. It has these keys:

- `map`: the map spec's id
- `window`: `[x0, y0, columns, rows]` on the reading's grid
- `crop`: the window crop the person read, relative to the settled file
- `cells`: `rows` strings of `columns` cell letters, `?` for a cell left unsettled
- `vertical`: `rows` strings of `columns + 1` edge letters, `?` for an edge left unsettled
- `horizontal`: `rows + 1` strings of `columns` edge letters
- `partial`: the partly floor cells, `"x,y"` each, on the reading's grid
- `outside`: cells of ground outside every building, which the third tally leaves out with the edges between them
- `checked`: each item the person looked at on its own, `"x,y"` or `"x,y:side"`, with the crop's path
- `notes`: free text

The scores follow the plan's scorer: cells right; edges right as passable or
blocked, over the settled edges that touch floor or water; walls read as
open, counted on their own; doors and secret doors found; and the same with
partly floor cells and the edges touching them left out.

Run from the osr-forge checkout:

    uv run python -m tools.answer_keys.score <readings dir> <settled file or dir>...
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from osrforge.tracing.truth import DOOR_LETTERS, PASSABLE_LETTERS, Panel, parse_cell

from .reading import load_reading


@dataclass(frozen=True)
class SettledWindow:
    """One settled window, loaded."""

    map: str
    x0: int
    y0: int
    columns: int
    rows: int
    cells: tuple[str, ...]
    vertical: tuple[str, ...]
    horizontal: tuple[str, ...]
    partial: frozenset[tuple[int, int]]
    outside: frozenset[tuple[int, int]]
    path: Path

    def cell(self, x: int, y: int) -> str:
        """The settled letter of a grid cell, `?` outside the window."""
        i, j = x - self.x0, y - self.y0
        if 0 <= i < self.columns and 0 <= j < self.rows:
            return self.cells[j][i]
        return "?"

    def occupied(self, x: int, y: int) -> bool:
        """Whether a grid cell is settled floor or water."""
        return self.cell(x, y) in ".~"

    def edges(self):
        """Every edge of the window with its settled letter: `(cell, side, letter)`, each edge once."""
        for j in range(self.rows):
            for i in range(self.columns + 1):
                yield (self.x0 + i, self.y0 + j), "west", self.vertical[j][i]
        for j in range(self.rows + 1):
            for i in range(self.columns):
                yield (self.x0 + i, self.y0 + j), "north", self.horizontal[j][i]


def load_settled(path: Path) -> SettledWindow:
    """Read a settled window file."""
    document = json.loads(path.read_text())
    x0, y0, columns, rows = document["window"]
    cells = tuple(document["cells"])
    vertical = tuple(document["vertical"])
    horizontal = tuple(document["horizontal"])
    if len(cells) != rows or any(len(row) != columns for row in cells):
        raise ValueError(f"{path}: cells must be {rows} rows of {columns}")
    if len(vertical) != rows or any(len(row) != columns + 1 for row in vertical):
        raise ValueError(f"{path}: vertical must be {rows} rows of {columns + 1}")
    if len(horizontal) != rows + 1 or any(len(row) != columns for row in horizontal):
        raise ValueError(f"{path}: horizontal must be {rows + 1} rows of {columns}")
    partial = frozenset(parse_cell(text) for text in document.get("partial", []))
    outside = frozenset(parse_cell(text) for text in document.get("outside", []))
    return SettledWindow(
        map=document["map"],
        x0=x0,
        y0=y0,
        columns=columns,
        rows=rows,
        cells=cells,
        vertical=vertical,
        horizontal=horizontal,
        partial=partial,
        outside=outside,
        path=path,
    )


@dataclass
class Tally:
    """Counts over one set of items."""

    cells: int = 0
    cells_right: int = 0
    edges: int = 0
    edges_right: int = 0
    exact: int = 0
    walls_open: int = 0
    open_walls: int = 0
    doors: int = 0
    doors_found: int = 0
    secret: int = 0
    secret_found: int = 0
    false_doors: int = 0
    differences: list[dict[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """The tally as ratios and counts."""

        def ratio(a: int, b: int) -> float | None:
            return round(a / b, 4) if b else None

        return {
            "cells": f"{self.cells_right}/{self.cells}",
            "cells_rate": ratio(self.cells_right, self.cells),
            "edges": f"{self.edges_right}/{self.edges}",
            "edges_rate": ratio(self.edges_right, self.edges),
            "exact": f"{self.exact}/{self.edges}",
            "exact_rate": ratio(self.exact, self.edges),
            "walls_read_open": self.walls_open,
            "open_read_wall": self.open_walls,
            "doors": f"{self.doors_found}/{self.doors}",
            "secret": f"{self.secret_found}/{self.secret}",
            "false_doors": self.false_doors,
        }


def _panel_edge(panel: Panel, cell: tuple[int, int], side: str) -> str:
    x, y = cell
    if side == "west":
        if 0 <= y < panel.height and 0 <= x <= panel.width:
            return panel.edges.vertical[y][x]
    elif 0 <= x < panel.width and 0 <= y <= panel.height:
        return panel.edges.horizontal[y][x]
    return "-"


def _panel_cell(panel: Panel, x: int, y: int) -> str:
    if 0 <= x < panel.width and 0 <= y < panel.height:
        return panel.cells[y][x]
    return "#"


def score_window(panel: Panel, settled: SettledWindow) -> dict[str, object]:
    """Score a panel against one settled window.

    Args:
        panel: The reading's panel.
        settled: The settled window.

    Returns:
        `all`, `without_partial`, and `without_outside` tallies, as `Tally.summary` gives them, with the
        differences listed.
    """
    tallies = {"all": Tally(), "without_partial": Tally(), "without_outside": Tally()}
    for j in range(settled.rows):
        for i in range(settled.columns):
            x, y = settled.x0 + i, settled.y0 + j
            truth = settled.cell(x, y)
            if truth == "?":
                continue
            read = _panel_cell(panel, x, y)
            right = (truth in ".~") == (read in ".~")
            for name, tally in tallies.items():
                if name == "without_partial" and (x, y) in settled.partial:
                    continue
                if name == "without_outside" and (x, y) in settled.outside:
                    continue
                tally.cells += 1
                tally.cells_right += right
                if not right:
                    tally.differences.append({"item": f"{x},{y}", "settled": truth, "read": read})
    for cell, side, truth in settled.edges():
        if truth in "?-":
            continue
        x, y = cell
        other = (x - 1, y) if side == "west" else (x, y - 1)
        if not (settled.occupied(x, y) or settled.occupied(*other)):
            continue
        read = _panel_edge(panel, cell, side)
        truth_passable = truth in PASSABLE_LETTERS
        read_passable = read in PASSABLE_LETTERS
        right = truth_passable == read_passable
        touches_partial = cell in settled.partial or other in settled.partial
        both_outside = cell in settled.outside and other in settled.outside
        for name, tally in tallies.items():
            if name == "without_partial" and touches_partial:
                continue
            if name == "without_outside" and both_outside:
                continue
            tally.edges += 1
            tally.edges_right += right
            tally.exact += truth == read
            if truth == "w" and read_passable:
                tally.walls_open += 1
            if truth_passable and read == "w":
                tally.open_walls += 1
            if truth in DOOR_LETTERS:
                tally.doors += 1
                tally.doors_found += read in DOOR_LETTERS
            if truth == "s":
                tally.secret += 1
                tally.secret_found += read == "s"
            if read in DOOR_LETTERS and truth not in DOOR_LETTERS:
                tally.false_doors += 1
            if truth != read:
                tally.differences.append({"item": f"{x},{y}:{side}", "settled": truth, "read": read})
    return {name: {**tally.summary(), "differences": tally.differences} for name, tally in tallies.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("readings", type=Path, help="the directory of reading files, one per map id")
    parser.add_argument("settled", type=Path, nargs="+", help="settled window files, or directories of them")
    parser.add_argument("--out", type=Path, default=None, help="write the scores as JSON here")
    parser.add_argument("--diffs", action="store_true", help="list every difference")
    args = parser.parse_args()
    files: list[Path] = []
    for item in args.settled:
        files.extend(sorted(item.glob("*.json")) if item.is_dir() else [item])
    results = []
    for path in files:
        settled = load_settled(path)
        reading = load_reading(args.readings / f"{settled.map}.json")
        scores = score_window(reading.panel, settled)
        results.append({"settled": str(path), "map": settled.map, "window": [settled.x0, settled.y0], **scores})
        for name in ("all", "without_partial", "without_outside"):
            s = scores[name]
            print(
                f"{path.stem:28s} {name:16s} cells {s['cells']:8s} edges {s['edges']:8s} ({s['edges_rate']}) "
                f"exact {s['exact']:8s} w>o {s['walls_read_open']:3d} o>w {s['open_read_wall']:3d} "
                f"doors {s['doors']:6s} secret {s['secret']:5s} false {s['false_doors']}"
            )
        if args.diffs:
            for d in scores["all"]["differences"]:
                print(f"    {d['item']:14s} settled {d['settled']} read {d['read']}")
    if args.out:
        args.out.write_text(json.dumps(results, indent=1) + "\n")


if __name__ == "__main__":
    main()
