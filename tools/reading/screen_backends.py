"""Clef backends on identical requests, per style, with no calibration.

A map's sources are whichever of these exist:

- `local Clef-flash`: the answer-key pass's answers, `<work>/clef/answers.jsonl`
- `hosted Clef`: the hosted Clef answers the answer-key pass kept, `<work>/clef/answers-hosted.jsonl`, or the
  answers of `hosted_exported --model clef`, `<dir>/hosted-clef/keyset-exported/<map id>.edge.jsonl`
- `hosted Clef-flash`: the answers of `hosted_exported --model clef-flash`,
  `<dir>/hosted-clef-flash/keyset-exported/<map id>.edge.jsonl`

A map counts when it has at least two sources. Over the scorer's universe edges that every source of a map answered,
it reports how often each pair of sources has the same most probable answer, and each key letter's recall. Over the
measured edges among them (both cells floor or water), it reports each source's raw passable errors at one half, and
the fewest open edges read as blocked at any threshold that reads no wall as open, and at most two walls as open.

Run from the osr-forge checkout:

    uv run python -m tools.reading.screen_backends <map file>... --hosted <dir> [--json <file>]
"""

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from osrforge.tracing.score import edge_universe
from osrforge.tracing.truth import Panel

from .answers import Answers, load_answers, passable_probability
from .edges import measured_edges
from .maps import STYLES, ScreenMap, key_panel, load_maps

__all__ = ["backend_tally", "fewest_open_blocked", "main", "sources_of"]


def sources_of(screen: ScreenMap, hosted: Path) -> dict[str, Answers]:
    """Every backend's answers for a map, on the key's grid."""
    panel = key_panel(screen)
    out: dict[str, Answers] = {}
    if screen.work is not None:
        for name, file in (("local Clef-flash", "answers.jsonl"), ("hosted Clef", "answers-hosted.jsonl")):
            path = screen.work / "clef" / file
            if path.exists():
                out[name] = load_answers(path, screen.frame, panel.width, panel.height)
    for name, folder in (("hosted Clef", "hosted-clef"), ("hosted Clef-flash", "hosted-clef-flash")):
        path = hosted / folder / "keyset-exported" / f"{screen.id}.edge.jsonl"
        if path.exists():
            out[name] = load_answers(path, None, panel.width, panel.height)
    return out


def fewest_open_blocked(passable: np.ndarray, p: np.ndarray, walls_open: int) -> int | None:
    """The fewest open edges read as blocked at any threshold that reads at most `walls_open` walls as open.

    Args:
        passable: Whether each edge is passable in the key.
        p: Each edge's passable probability.
        walls_open: The most walls read as open a threshold may give.

    Returns:
        The count, or `None` when no threshold reads few enough walls as open. A threshold above every probability
        reads every edge as blocked, so it reads no wall as open.
    """
    best = None
    for threshold in np.unique(np.concatenate([p, [1.01]])):
        decision = p >= threshold
        if int((decision & ~passable).sum()) <= walls_open:
            orb = int((~decision & passable).sum())
            best = orb if best is None else min(best, orb)
    return best


def backend_tally(
    panel: Panel, sources: Mapping[str, Answers]
) -> tuple[Counter[str], dict[str, Counter[str]], dict[str, list[tuple[bool, float]]]]:
    """One map's backend figures over the edges every source answered.

    Returns:
        The agreement counts (`<first> = <second>` and `<first> = <second> edges`, names in sorted order), each
        source's `<letter> edges` and `<letter> right`, and each source's measured edges as (passable in the key,
        passable probability).
    """
    names = sorted(sources)
    common = set.intersection(*(set(item.edges) for item in sources.values()))
    agree: Counter[str] = Counter()
    letters: dict[str, Counter[str]] = {name: Counter() for name in names}
    for edge in [edge for edge in edge_universe(panel) if edge in common]:
        key = panel.edges.at(*edge)
        tops = {name: max(sources[name].edges[edge], key=sources[name].edges[edge].__getitem__) for name in names}
        for name in names:
            letters[name][f"{key} edges"] += 1
            letters[name][f"{key} right"] += tops[name] == key
        for index, first in enumerate(names):
            for second in names[index + 1 :]:
                agree[f"{first} = {second}"] += tops[first] == tops[second]
                agree[f"{first} = {second} edges"] += 1
    rows = {
        name: [
            (panel.edges.at(*edge) in "op", passable_probability(sources[name].edges[edge]))
            for edge in measured_edges(panel)
            if edge in common
        ]
        for name in names
    }
    return agree, letters, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clef backends on identical requests, per style.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--hosted", type=Path, required=True, help="the directory hosted_exported wrote to")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    maps = [screen for path in args.map_files for screen in load_maps(path)]
    result: dict[str, dict[str, object]] = {}
    for style in STYLES:
        agree: Counter[str] = Counter()
        letters: dict[str, Counter[str]] = {}
        rows: dict[str, list[tuple[bool, float]]] = {}
        counted = []
        for screen in [screen for screen in maps if screen.style == style]:
            sources = sources_of(screen, args.hosted)
            if len(sources) < 2:
                continue
            counted.append(screen.id)
            map_agree, map_letters, map_rows = backend_tally(key_panel(screen), sources)
            agree.update(map_agree)
            for name in sources:
                letters.setdefault(name, Counter()).update(map_letters[name])
                rows.setdefault(name, []).extend(map_rows[name])
        if not counted:
            continue
        figures: dict[str, object] = {"maps": counted, "agreement": dict(agree)}
        for name, items in rows.items():
            passable = np.array([item[0] for item in items], dtype=bool)
            p = np.array([item[1] for item in items], dtype=float)
            decision = p >= 0.5
            figures[name] = {
                "measured": len(items),
                "blocked": int((~passable).sum()),
                "raw at one half": [int((decision & ~passable).sum()), int((~decision & passable).sum())],
                "fewest open blocked, no wall open": fewest_open_blocked(passable, p, 0),
                "fewest open blocked, at most two walls open": fewest_open_blocked(passable, p, 2),
                "named right": {
                    letter: f"{letters[name][letter + ' right']}/{letters[name][letter + ' edges']}"
                    for letter in "owdnls"
                    if letters[name][letter + " edges"]
                },
            }
        result[style] = figures
        print(f"{style}: {json.dumps(figures)}", flush=True)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
