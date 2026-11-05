"""Put the answer-key pass's stored Clef answers onto the keys' grids, as stores the screening's scripts read.

The answer-key pass asked local Clef-flash about every edge that touches floor in either of its readings, with each
style's answer-key option set, and kept the answers on its working grid (`<work>/clef/answers.jsonl`). This writes
each map's answers on the key's registered grid as `<out>/<map id>.edge.jsonl` and `<out>/<map id>.cell.jsonl`, and a
windows file, `<out>/whole-maps.json`, that covers every map whole, so `screen_bar` can score every key edge with the
stage-like reading.

Run from the osr-forge checkout:

    uv run python -m tools.reading.export_stored <map file>... --out <dir>
"""

import argparse
import json
from pathlib import Path

from osrforge.tracing.truth import cell_key, edge_key

from .answers import LETTERS, load_answers
from .maps import key_panel, load_maps

__all__ = ["main"]

_OPTION = {letter: option for option, letter in LETTERS.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The answer-key pass's Clef answers on the keys' grids.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    windows = []
    for screen in [screen for path in args.map_files for screen in load_maps(path)]:
        if screen.work is None:
            continue
        panel = key_panel(screen)
        answers = load_answers(screen.work / "clef" / "answers.jsonl", screen.frame, panel.width, panel.height)
        with (args.out / f"{screen.id}.edge.jsonl").open("w") as handle:
            for (cell, side), probabilities in answers.edges.items():
                record = {
                    "item": edge_key(cell, side),
                    "kind": "edge",
                    "probabilities": {_OPTION[letter]: p for letter, p in probabilities.items()},
                }
                handle.write(json.dumps(record) + "\n")
        with (args.out / f"{screen.id}.cell.jsonl").open("w") as handle:
            for cell, probabilities in answers.cells.items():
                handle.write(
                    json.dumps({"item": cell_key(cell), "kind": "cell", "probabilities": probabilities}) + "\n"
                )
        windows.append([screen.id, 0, 0, panel.width, panel.height])
        print(f"{screen.id}: {len(answers.edges)} edges, {len(answers.cells)} cells", flush=True)
    (args.out / "whole-maps.json").write_text(json.dumps({"windows": windows}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
