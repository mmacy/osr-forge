"""Score Clef's edge answers per style: raw, calibrated without each map, alone and with the ink, and its letters.

For each answers source (an option set on a backend), and each style, over the edges the source answered:

- *passable or blocked*, on the measured edges (both cells floor or water in the key): walls read as open and open
  edges read as blocked, raw at one half and calibrated at each threshold; the share before the first wrong answer,
  ranked by confidence; and the calibration error
- *letters*, on every answered edge in the scorer's universe: for each of the key's letters, how many edges Clef's
  most probable option names right

The calibration is [`held_out`][tools.reading.calibrate.held_out] over the style's maps. A source is a directory of
`<map id>.edge.jsonl` stores on the key's grid, or `keyset-stored` (local Clef-flash) or `keyset-hosted-stored`
(hosted Clef), the answer-key pass's own answers.

Run from the osr-forge checkout:

    uv run python -m tools.reading.screen_clef <map file>... --source <name>=<dir or keyset-stored>... \\
        [--windows <windows file>...] [--json <file>]
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .answers import Answers, load_answers, passable_probability
from .calibrate import decision_errors, expected_calibration_error, fold_of, held_out, ranked_share
from .dataset import features, map_rows
from .edges import BLOCKED
from .maps import STYLES, ScreenMap, key_panel, load_maps
from .traces import universe
from .windows import Window, load_windows

__all__ = ["main", "score_source"]

THRESHOLDS = (0.5, 0.7, 0.8, 0.9, 0.95)


def _answers(screen: ScreenMap, source: str) -> Answers | None:
    panel = key_panel(screen)
    if source in ("keyset-stored", "keyset-hosted-stored"):
        name = "answers.jsonl" if source == "keyset-stored" else "answers-hosted.jsonl"
        if screen.work is None or not (screen.work / "clef" / name).exists():
            return None
        return load_answers(screen.work / "clef" / name, screen.frame, panel.width, panel.height)
    path = Path(source).expanduser() / f"{screen.id}.edge.jsonl"
    if not path.exists():
        return None
    return load_answers(path, None, panel.width, panel.height)


def _letter(probabilities: dict[str, float]) -> str:
    return max(probabilities, key=lambda letter: (probabilities[letter], -("op" + BLOCKED).index(letter)))


def score_source(
    maps: list[ScreenMap], source: str, windows: list[Window] | None, keep: dict[str, set[Any]] | None = None
) -> dict[str, Any]:
    """Score one source per style.

    Args:
        maps: The maps.
        source: The answers source.
        windows: Count only edges named by a cell in these windows, or every answered edge when `None`.
        keep: Count only these edges of each map, when given, so two sources compare on the same edges.

    Returns:
        Per style, the figures.
    """
    out: dict[str, Any] = {}
    for style in STYLES:
        rows, ink, letters = [], [], Counter()
        ids = []
        for screen in [screen for screen in maps if screen.style == style]:
            answers = _answers(screen, source)
            if answers is None:
                continue

            def wanted(edge: Any, screen: ScreenMap = screen) -> bool:
                if keep is not None and edge not in keep.get(screen.id, set()):
                    return False
                return windows is None or any(w.map == screen.id and w.holds(edge[0]) for w in windows)

            data = map_rows(screen, answers)
            ids.append(screen.id)
            for row, measure in zip(data.rows, data.ink, strict=True):
                if row.probabilities is not None and wanted(row.edge):
                    rows.append(row)
                    ink.append(measure)
            panel = key_panel(screen)
            for edge in universe(panel):
                probabilities = answers.edges.get(edge)
                if probabilities is None or not wanted(edge):
                    continue
                key = panel.edges.at(*edge)
                letters[f"{key} edges"] += 1
                letters[f"{key} right"] += _letter(probabilities) == key
        if not rows:
            continue
        y = np.array([row.passable for row in rows], dtype=float)
        groups = [fold_of(row.map, row.edge[0], ids) for row in rows]
        raw = np.array([passable_probability(row.probabilities or {}) for row in rows])
        figures: dict[str, Any] = {
            "maps": ids,
            "measured": len(rows),
            "blocked": int((1 - y).sum()),
            "letters": dict(sorted(letters.items())),
        }
        probabilities = {"raw": raw}
        for name in ("clef", "ink", "clef+ink"):
            probabilities[name] = held_out(features(rows, ink, name), y, groups)
        for name, p in probabilities.items():
            figures[name] = {
                "errors": {str(t): decision_errors(y, p, t) for t in THRESHOLDS},
                "ranked_share": ranked_share(y, p, 0.5),
                "ece": round(expected_calibration_error(y, p), 4),
            }
        out[style] = figures
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clef's edge answers per style.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--windows", type=Path, action="append", default=None)
    parser.add_argument("--paired", action="store_true", help="count only edges every source answered")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    maps = [screen for path in args.map_files for screen in load_maps(path)]
    windows = [window for path in args.windows for window in load_windows(path)] if args.windows else None
    sources = dict(item.split("=", 1) for item in args.source)
    keep = None
    if args.paired:
        keep = {}
        for screen in maps:
            sets = []
            for source in sources.values():
                answers = _answers(screen, source)
                sets.append(set(answers.edges) if answers else set())
            keep[screen.id] = set.intersection(*sets) if sets else set()
    result = {name: score_source(maps, source, windows, keep) for name, source in sources.items()}
    for name, styles in result.items():
        for style, f in styles.items():
            line = " | ".join(
                f"{kind}: T.5 {f[kind]['errors']['0.5']} T.9 {f[kind]['errors']['0.9']} rs {f[kind]['ranked_share']}"
                for kind in ("raw", "clef", "clef+ink")
            )
            print(f"{name:12} {style:11} n={f['measured']:5} blocked={f['blocked']:4} {line}")
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
