"""Clef's edge option sets compared letter by letter on the screening windows, per style, with no calibration.

A source is a directory of `<map id>.edge.jsonl` stores on the key's grid (`clefask --set <name>`), or
`keyset-stored`, the answer-key pass's own answers. A window counts when every source answered every edge of the
scorer's universe inside it, so a run that stopped partway through a window leaves that window out. Over the
counted windows' edges, for each source, it reports:

- each key letter's recall: the edges whose most probable answer is the key's letter
- the raw passable errors at one half: walls read as open and open edges read as blocked
- what the source answers instead, for the key's walls, windows, doors, locked and secret doors, and printed open
  lines it gets wrong

Run from the osr-forge checkout:

    uv run python -m tools.reading.screen_letters <map file>... --windows <windows file>... \\
        --source <name>=<dir or keyset-stored>... [--json <file>]
"""

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from osrforge.tracing.truth import Panel

from .answers import Answers, Edge, load_answers, passable_probability
from .maps import STYLES, ScreenMap, key_panel, load_maps
from .windows import Window, load_windows, window_edges

__all__ = ["CONFUSED", "answered_windows", "letter_tally", "main", "source_answers"]

CONFUSED = "wndlsp"
"""The key letters whose wrong answers are tallied by what the source answers instead."""


def source_answers(screen: ScreenMap, source: str) -> Answers | None:
    """A source's answers for a map on the key's grid, or `None` when the source has none for it."""
    panel = key_panel(screen)
    if source == "keyset-stored":
        if screen.work is None:
            return None
        return load_answers(screen.work / "clef" / "answers.jsonl", screen.frame, panel.width, panel.height)
    path = Path(source).expanduser() / f"{screen.id}.edge.jsonl"
    return load_answers(path, None, panel.width, panel.height) if path.exists() else None


def answered_windows(panel: Panel, windows: Sequence[Window], answers: Mapping[str, Answers]) -> list[Window]:
    """The windows in which every source answered every universe edge."""
    return [
        window
        for window in windows
        if all(edge in item.edges for edge in window_edges(panel, [window]) for item in answers.values())
    ]


def letter_tally(panel: Panel, answers: Mapping[str, Answers], edges: Sequence[Edge]) -> dict[str, Counter[str]]:
    """Each source's letters over the edges that every source answered.

    Args:
        panel: The key's panel.
        answers: Each source's answers, by name.
        edges: The edges to count, usually the key's universe edges inside the windows.

    Returns:
        For each source, `edges`, `<letter> edges` and `<letter> right` for each key letter, the raw `walls read as
        open` and `open read as blocked` at one half, and `<letter> read as <letter>` for each wrong answer to a key
        letter in [`CONFUSED`][tools.reading.screen_letters.CONFUSED].
    """
    common = [edge for edge in edges if all(edge in item.edges for item in answers.values())]
    out: dict[str, Counter[str]] = {}
    for name, item in answers.items():
        tally: Counter[str] = Counter()
        tally["edges"] = len(common)
        for edge in common:
            key = panel.edges.at(*edge)
            probabilities = item.edges[edge]
            top = max(probabilities, key=probabilities.__getitem__)
            tally[f"{key} edges"] += 1
            tally[f"{key} right"] += top == key
            passable = passable_probability(probabilities) >= 0.5
            tally["walls read as open"] += passable and key not in "op"
            tally["open read as blocked"] += not passable and key in "op"
            if key in CONFUSED and top != key:
                tally[f"{key} read as {top}"] += 1
        out[name] = tally
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clef's edge option sets compared letter by letter, per style.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--windows", type=Path, action="append", required=True)
    parser.add_argument("--source", action="append", required=True, help="name=dir, or name=keyset-stored")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    maps = [screen for path in args.map_files for screen in load_maps(path)]
    windows = [window for path in args.windows for window in load_windows(path)]
    sources = dict(item.split("=", 1) for item in args.source)
    result: dict[str, dict[str, object]] = {}
    for style in STYLES:
        pooled: dict[str, Counter[str]] = {name: Counter() for name in sources}
        counted = []
        for screen in [screen for screen in maps if screen.style == style]:
            mine = [window for window in windows if window.map == screen.id]
            loaded = {name: source_answers(screen, source) for name, source in sources.items()}
            answers = {name: item for name, item in loaded.items() if item is not None}
            if not mine or len(answers) < len(sources):
                continue
            panel = key_panel(screen)
            kept = answered_windows(panel, mine, answers)
            if not kept:
                continue
            counted += [f"{screen.id}:{w.x},{w.y}" for w in kept]
            tallies = letter_tally(panel, answers, window_edges(panel, kept))
            for name, tally in tallies.items():
                pooled[name].update(tally)
        if not counted:
            continue
        figures: dict[str, object] = {"windows": counted}
        for name, tally in pooled.items():
            recall = {
                letter: f"{tally[letter + ' right']}/{tally[letter + ' edges']}"
                for letter in "owdnlsp"
                if tally[letter + " edges"]
            }
            wrong = {key: value for key, value in sorted(tally.items()) if key[1:9] == " read as"}
            figures[name] = {
                "edges": tally["edges"],
                "raw at one half": [tally["walls read as open"], tally["open read as blocked"]],
                "named right": recall,
                "read instead": wrong,
            }
        result[style] = figures
        print(f"{style}: {json.dumps(figures)}", flush=True)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
