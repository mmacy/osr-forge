"""The calibration fitted on the real answer keys, applied unchanged to synthetic maps with exact answers.

The synthetic maps' answers are exact and no model made them, so they test what the real keys can't: whether a
calibration fitted on keys that Clef helped build still means what it says on maps Clef never saw. For each style,
the model is fitted on every measured edge of the style's real maps (Clef's answer-key-pass answers and the ink) and
applied to the synthetic maps' measured edges (Clef's answers from `clefask --set keyset`). The figures are the
passable-or-blocked errors at each threshold, the share before the first and fifth wrong answers, the calibration
error, and each letter's recall.

Run from the osr-forge checkout:

    uv run python -m tools.reading.screen_synthetic tools/reading/maps.json <private map file> \\
        --synthetic tools/reading/synthetic-maps.json --answers <dir with <map id>.edge.jsonl> [--json <file>]
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .answers import load_answers, passable_probability
from .calibrate import Logistic, decision_errors, expected_calibration_error, ranked_share
from .dataset import features, map_rows
from .maps import STYLES, key_panel, load_maps
from .screen_clef import THRESHOLDS
from .traces import universe

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real-key calibrations on synthetic maps.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--synthetic", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    real = [screen for path in args.map_files for screen in load_maps(path)]
    synthetic = load_maps(args.synthetic)
    result: dict[str, Any] = {}
    for style in STYLES:
        train_rows, train_ink = [], []
        for screen in [s for s in real if s.style == style]:
            if screen.work is None:
                continue
            data = map_rows(screen, screen.work / "clef" / "answers.jsonl")
            for row, measure in zip(data.rows, data.ink, strict=True):
                if row.probabilities is not None:
                    train_rows.append(row)
                    train_ink.append(measure)
        rows, ink, letters = [], [], Counter()
        maps = []
        for screen in [s for s in synthetic if s.style == style]:
            path = args.answers / f"{screen.id}.edge.jsonl"
            if not path.exists():
                continue
            maps.append(screen.id)
            panel = key_panel(screen)
            answers = load_answers(path, None, panel.width, panel.height)
            data = map_rows(screen, answers)
            for row, measure in zip(data.rows, data.ink, strict=True):
                if row.probabilities is not None:
                    rows.append(row)
                    ink.append(measure)
            for edge in universe(panel):
                probabilities = answers.edges.get(edge)
                if probabilities is None:
                    continue
                key = panel.edges.at(*edge)
                top = max(probabilities, key=probabilities.__getitem__)
                letters[f"{key} edges"] += 1
                letters[f"{key} right"] += top == key
        if not rows or not train_rows:
            continue
        y = np.array([row.passable for row in rows], dtype=float)
        y_train = np.array([row.passable for row in train_rows], dtype=float)
        figures: dict[str, Any] = {
            "maps": maps,
            "measured": len(rows),
            "blocked": int((1 - y).sum()),
            "letters": dict(sorted(letters.items())),
        }
        probabilities = {"raw": np.array([passable_probability(row.probabilities or {}) for row in rows])}
        for name in ("clef", "ink", "clef+ink"):
            model = Logistic().fit(features(train_rows, train_ink, name), y_train)
            probabilities[name] = model.predict(features(rows, ink, name))
        for name, p in probabilities.items():
            figures[name] = {
                "errors": {str(t): decision_errors(y, p, t) for t in THRESHOLDS},
                "ranked_share": ranked_share(y, p, 0.5),
                "ranked_share_5": ranked_share(y, p, 0.5, nth=5),
                "ece": round(expected_calibration_error(y, p), 4),
            }
        result[style] = figures
        line = " | ".join(
            f"{kind}: T.5 {figures[kind]['errors']['0.5']} T.9 {figures[kind]['errors']['0.9']} "
            f"rs {figures[kind]['ranked_share']} ece {figures[kind]['ece']}"
            for kind in ("raw", "clef", "clef+ink")
        )
        print(f"{style:11} maps {maps} n={len(rows)} blocked={figures['blocked']} {line}", flush=True)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
