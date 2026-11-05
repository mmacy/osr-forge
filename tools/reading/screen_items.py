"""Choose the uncertain items, send them over the grid of models and efforts, store each outcome, and score it.

The items are what the stage would send Claude: edges between two floor cells whose held-out calibrated P(passable)
(Clef's answer and the ink, `clef+ink`) lies between 0.05 and 0.95, and cells whose ink floor fraction lies between
a quarter and three quarters. Each region gets `--edges` edges and `--cells` cells, drawn with a fixed seed and
shuffled, and the first `--take` of them go to every model and effort. Clef's answers are the answer-key pass's
(`<work>/clef/answers.jsonl`).

Run from the osr-forge checkout, with `OSRFORGE_CLAUDE_LEDGER` set:

    uv run python -m tools.reading.screen_items <map file>... --pages <pages file>... --out <dir> \\
        --models ... --efforts ... --cap 160 [--per-request N] [--only <map id>]

An outcome already stored isn't asked again. The items are written to `<dir>/items/items.json` once and read back.
"""

import argparse
import json
import random
from pathlib import Path

import numpy as np

from .calibrate import fold_of, held_out
from .claude_run import RequestCap, ask, make_provider
from .dataset import features, map_rows
from .items import AskedItem, check_items, items_kind, items_request, letters_of
from .maps import ScreenMap, key_panel, key_region, load_maps, load_rgb
from .screen_wholemap import load_pages

__all__ = ["choose_items", "main"]


def choose_items(maps: list[ScreenMap], edges: int, cells: int, seed: int = 7) -> dict[str, list[AskedItem]]:
    """The uncertain items of each map."""
    rng = random.Random(seed)
    data = {screen.id: map_rows(screen, screen.work / "clef" / "answers.jsonl") for screen in maps if screen.work}
    chosen: dict[str, list[AskedItem]] = {}
    for style in sorted({screen.style for screen in maps}):
        ids = [screen.id for screen in maps if screen.style == style and screen.id in data]
        rows, ink = [], []
        for map_id in ids:
            for row, measure in zip(data[map_id].rows, data[map_id].ink, strict=True):
                if row.probabilities:
                    rows.append(row)
                    ink.append(measure)
        y = np.array([row.passable for row in rows], dtype=float)
        p = held_out(features(rows, ink, "clef+ink"), y, [fold_of(row.map, row.edge[0], ids) for row in rows])
        for map_id in ids:
            pool = [
                AskedItem(map=map_id, cell=row.edge[0], side=row.edge[1], key=row.key, clef=round(float(q), 4))
                for row, q in zip(rows, p, strict=True)
                if row.map == map_id and 0.05 < q < 0.95
            ]
            picked = rng.sample(pool, min(edges, len(pool)))
            panel = key_panel(next(screen for screen in maps if screen.id == map_id))
            floors = data[map_id].floors
            band = [(x, y_) for y_ in range(panel.height) for x in range(panel.width) if 0.25 < floors[y_, x] < 0.75]
            for cell in rng.sample(band, min(cells, len(band))):
                picked.append(
                    AskedItem(
                        map=map_id,
                        cell=cell,
                        side=None,
                        key=panel.cells[cell[1]][cell[0]],
                        clef=round(float(floors[cell[1], cell[0]]), 4),
                    )
                )
            rng.shuffle(picked)
            chosen[map_id] = picked
    return chosen


def _dump(chosen: dict[str, list[AskedItem]]) -> dict[str, list[dict[str, object]]]:
    return {
        map_id: [{"cell": list(i.cell), "side": i.side, "key": i.key, "clef": i.clef} for i in items]
        for map_id, items in chosen.items()
    }


def _load(document: dict[str, list[dict[str, object]]]) -> dict[str, list[AskedItem]]:
    return {
        map_id: [
            AskedItem(map=map_id, cell=tuple(i["cell"]), side=i["side"], key=i["key"], clef=i["clef"])  # type: ignore[arg-type]
            for i in items
        ]
        for map_id, items in document.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claude's item judgments over models and efforts.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--pages", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=["claude-sonnet-5-5"])
    parser.add_argument("--efforts", nargs="+", default=["low", "medium", "high"])
    parser.add_argument("--edges", type=int, default=24)
    parser.add_argument("--cells", type=int, default=6)
    parser.add_argument("--take", type=int, default=10, help="ask the first N items of each map")
    parser.add_argument("--per-request", type=int, default=10)
    parser.add_argument("--tag", default="", help="a suffix that keeps a variant's outcomes apart")
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--cap", type=int, default=160)
    args = parser.parse_args(argv)
    maps = [screen for path in args.map_files for screen in load_maps(path)]
    pages = {region: page for path in args.pages for page in load_pages(path) for _label, region in page.regions}
    store = args.out / "items" / "items.json"
    if not store.exists():
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(_dump(choose_items(maps, args.edges, args.cells)), indent=1) + "\n")
    chosen = _load(json.loads(store.read_text()))
    for model in args.models:
        provider = make_provider(model)
        cap = RequestCap(provider.settings.ledger_path, args.cap)
        for effort in args.efforts:
            kind = items_kind(effort)  # type: ignore[arg-type]
            for screen in maps:
                if screen.id not in chosen or (args.only and screen.id not in args.only):
                    continue
                items = chosen[screen.id][: args.take]
                page = pages[screen.id]
                for start in range(0, len(items), args.per_request):
                    part = items[start : start + args.per_request]
                    name = f"{screen.id}.{start // args.per_request}{args.tag}"
                    path = args.out / "items" / model / effort / f"{name}.json"
                    if not path.exists():
                        request = items_request(
                            kind,
                            load_rgb(page.image),
                            load_rgb(screen.image),
                            key_region(screen).registration,  # type: ignore[arg-type]
                            screen.style,
                            part,
                        )
                        outcome = ask(provider, request, lambda answer, part=part: check_items(answer, part), cap)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps(outcome.to_json(), indent=1) + "\n")
                    stored = json.loads(path.read_text())
                    if stored["answer"] is None:
                        print(f"{model} {effort} {name}: malformed after the retry", flush=True)
                        continue
                    letters = letters_of(stored["answer"])
                    right = sum(
                        (letters[n] in "op") == (item.key in "op")
                        if item.side
                        else (letters[n] == ".") == (item.key == ".")
                        for n, item in enumerate(part, start=1)
                    )
                    print(
                        f"{model} {effort} {name}: {right}/{len(part)} right, attempts {len(stored['attempts'])}, "
                        f"${stored['cost_usd']:.4f}",
                        flush=True,
                    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
