"""The stage's reading of the screening windows against the classifier's, on the same edges, by the scorer.

For each style and each option set, it gives the following:

- the stage's reading before Claude (`stage_reading`) at each passable threshold, with the calibration fitted without
  each map (`clef` or `clef+ink`), scored with [`score_panel`][tools.reading.traces.score_panel]
- the classifier's reading of the same windows, scored the same way
- the edges whose calibrated P(passable) lies between ε and 1 - ε, the band Claude would judge, for several ε

Run from the osr-forge checkout:

    uv run python -m tools.reading.screen_bar <map file>... --windows <file>... --set <name>=<answers dir>... \\
        --classifier <readings dir> [--json <file>]
"""

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from osrforge.tracing.score import edge_universe

from .answers import load_answers
from .bar import classifier_panel
from .calibrate import Logistic, fold_of
from .dataset import features, map_ink
from .edges import EdgeRow, rows_for
from .ink import unsettled_cells
from .maps import STYLES, ScreenMap, key_document, key_panel, load_maps
from .stage_reading import patched_window_panel, stage_cells, stage_letters
from .traces import score_panel, tally
from .windows import Window, load_windows, window_cells

__all__ = ["main"]

THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
EPSILONS = (0.02, 0.05, 0.1, 0.2)


def _window_universe(screen: ScreenMap, windows: list[Window]) -> int:
    panel = key_panel(screen)
    return sum(any(w.holds(edge[0]) for w in windows) for edge in edge_universe(panel))


def _classifier_in_windows(screen: ScreenMap, readings: Path, windows: list[Window]) -> Any:
    key = key_panel(screen)
    reading = classifier_panel(readings, screen)
    cells = [list(row) for row in key.cells]
    letters = {}
    for y in range(key.height):
        for x in range(key.width):
            if any(w.holds((x, y)) for w in windows):
                cells[y][x] = reading.cells[y][x]
    for edge in key.edges.positions():
        if any(w.holds(edge[0]) for w in windows):
            letters[edge] = reading.edges.at(*edge)
    every, whole = score_panel(key_document(screen.key), patched_window_panel(key, cells, letters))
    return every.edges.all, whole.edges.all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The stage's window reading against the classifier's.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--windows", type=Path, action="append", required=True)
    parser.add_argument("--set", dest="sets", action="append", required=True)
    parser.add_argument("--classifier", type=Path, required=True)
    parser.add_argument("--calibration", default="clef+ink")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--dump", type=float, default=None, help="write each map's stage panel at this threshold")
    parser.add_argument("--dump-dir", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    maps = [screen for path in args.map_files for screen in load_maps(path)]
    windows = [window for path in args.windows for window in load_windows(path)]
    result: dict[str, Any] = {}
    for item in args.sets:
        name, directory = item.split("=", 1)
        root = Path(directory).expanduser()
        result[name] = {}
        for style in STYLES:
            screens = [s for s in maps if s.style == style and (root / f"{s.id}.edge.jsonl").exists()]
            if not screens:
                continue
            prepared = []
            rows: list[EdgeRow] = []
            ink_rows = []
            kinds: list[tuple[bool, bool]] = []
            for screen in screens:
                panel = key_panel(screen)
                mine = [w for w in windows if w.map == screen.id]
                answers = load_answers(root / f"{screen.id}.edge.jsonl", None, panel.width, panel.height)
                cell_path = root / f"{screen.id}.cell.jsonl"
                cells_answers = (
                    load_answers(cell_path, None, panel.width, panel.height).cells if cell_path.exists() else {}
                )
                ink, floors = map_ink(screen)
                unsettled = set(unsettled_cells(ink, floors, window_cells(panel, mine)))
                cells = stage_cells(panel, floors, unsettled, cells_answers, mine)
                # The calibration is fitted on the measured edges of the key. It is applied to every edge the
                # stage's cells put between two floor or water cells.
                stage_panel = panel.model_copy(update={"cells": tuple("".join(row) for row in cells)})
                occupied = {(x, y_) for y_, line in enumerate(panel.cells) for x, c in enumerate(line) if c in ".~"}
                for row in rows_for(screen.id, style, stage_panel, answers):
                    if row.probabilities is not None and any(w.holds(row.edge[0]) for w in mine):
                        key_letter = panel.edges.at(*row.edge)
                        rows.append(
                            EdgeRow(row.map, row.style, row.edge, key_letter, row.cave, row.partial, row.probabilities)
                        )
                        ink_rows.append(ink.edge(*row.edge, floors))
                        (x, y_), side = row.edge
                        dx, dy = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}[side]
                        both = (x, y_) in occupied and (x + dx, y_ + dy) in occupied
                        either = (x, y_) in occupied or (x + dx, y_ + dy) in occupied
                        kinds.append((both, either))
                prepared.append((screen, panel, mine, answers, cells))
            y = np.array([row.passable for row in rows], dtype=float)
            ids = [s.id for s in screens]
            # Fitted on the edges the key has between two floor or water cells, each map or fold held out, and
            # applied to every edge the stage's cells put between two.
            x = features(rows, ink_rows, args.calibration)
            groups = np.array([fold_of(r.map, r.edge[0], ids) for r in rows])
            measured = np.array([both for both, _either in kinds])
            in_universe = np.array([either for _both, either in kinds])
            p = np.zeros(len(rows))
            for group in sorted(set(groups.tolist())):
                train = measured & (groups != group)
                p[groups == group] = Logistic().fit(x[train], y[train]).predict(x[groups == group])
            # A door Clef reads is decided by Clef, not the threshold, and isn't sent to Claude.
            door_top = np.array(
                [
                    max(row.probabilities or {"w": 1.0}, key=(row.probabilities or {"w": 1.0}).__getitem__) in "dls"
                    for row in rows
                ]
            )
            p = np.where(door_top, 0.0, p)
            calibrated = {(row.map, row.edge): float(q) for row, q in zip(rows, p, strict=True)}
            figures: dict[str, Any] = {
                "maps": ids,
                "edges": sum(_window_universe(s, m) for s, _p, m, _a, _c in prepared),
            }
            for threshold in THRESHOLDS:
                tallies = []
                for screen, panel, mine, answers, cells in prepared:
                    letters = stage_letters(
                        panel,
                        cells,
                        mine,
                        answers.edges,
                        lambda edge, screen=screen, calibrated=calibrated: calibrated.get((screen.id, edge)),
                        threshold,
                    )
                    trace = patched_window_panel(panel, cells, letters)
                    every, _whole = score_panel(key_document(screen.key), trace)
                    tallies.append(every.edges.all)
                    if args.dump is not None and threshold == args.dump:
                        args.dump_dir.mkdir(parents=True, exist_ok=True)
                        (args.dump_dir / f"{screen.id}.panel.json").write_text(trace.model_dump_json() + "\n")
                total = tally(tallies)
                figures[f"T{threshold}"] = {
                    "walls_read_as_open": total.walls_read_as_open,
                    "open_read_as_blocked": total.open_read_as_blocked,
                }
            classifier = tally(_classifier_in_windows(s, args.classifier, m)[0] for s, _p, m, _a, _c in prepared)
            figures["classifier"] = {
                "walls_read_as_open": classifier.walls_read_as_open,
                "open_read_as_blocked": classifier.open_read_as_blocked,
            }
            figures["band"] = {str(e): int(((p > e) & (p < 1 - e)).sum()) for e in EPSILONS}
            figures["band_in_universe"] = {str(e): int(((p > e) & (p < 1 - e) & in_universe).sum()) for e in EPSILONS}
            maps_of_rows = np.array([row.map for row in rows])
            figures["band_by_map"] = {
                map_id: {
                    str(e): int(((p > e) & (p < 1 - e) & in_universe & (maps_of_rows == map_id)).sum())
                    for e in EPSILONS
                }
                for map_id in ids
            }
            figures["band_errors"] = {}
            for e in EPSILONS:
                inside = (p > e) & (p < 1 - e) & in_universe
                for threshold in THRESHOLDS:
                    passable = p >= threshold
                    figures["band_errors"][f"e{e} T{threshold}"] = {
                        "walls_read_as_open": int((inside & passable & (y == 0)).sum()),
                        "open_read_as_blocked": int((inside & ~passable & (y == 1)).sum()),
                    }
            figures["between_floor_edges"] = len(rows)
            result[name][style] = figures
            line = " ".join(
                f"T{t}:{figures[f'T{t}']['walls_read_as_open']}/{figures[f'T{t}']['open_read_as_blocked']}"
                for t in THRESHOLDS
            )
            print(
                f"{name:8} {style:11} edges {figures['edges']:5} classifier "
                f"{classifier.walls_read_as_open}/{classifier.open_read_as_blocked} | stage {line} "
                f"| band {figures['band']}",
                flush=True,
            )
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
