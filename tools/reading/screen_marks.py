"""Find the label-sized marks, ask Clef which key each is, and score both against the key's marks.

The marks asked are every found mark within one cell of one of the key's marks, and a fixed-seed sample of the
others, so Clef's `none` is measured too. A mark's key questions offer the survey's keys of the levels whose outline
(the key's) lies within two cells of the mark, in chunks of 25 with `none`; a mark that every chunk answers `none`
gets the letter question (`marks.mark_answer`).

The score matches each key mark to the found mark nearest it, within one cell, by Chebyshev distance and then by the
pixel distance of their centres. A key mark is *found* when it has a match, and *read* when Clef's top answer for the
match is its text: `key:<text>` for a survey key, `letter:<text>` for a single letter, and `none` for any other
label. Found marks with no key mark should be answered `none`.

Run from the osr-forge checkout:

    uv run python -m tools.reading.screen_marks <map file>... --pages <pages file>... --out <dir> [--ask] [--sample N]
"""

import argparse
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np

from osrforge.providers.clef_local import LocalClefProvider, LocalClefSettings
from osrforge.tracing.truth import Registration, parse_cell

from .clefask import AnswerStore, Item, run_items
from .ink import INK_STYLES, MapInk
from .maps import ScreenMap, key_panel, key_region, load_maps, load_rgb
from .marks import LETTERS, FoundMark, find_marks, key_chunks, letter_request, mark_answer, mark_crop, mark_request
from .screen_wholemap import levels_on, load_pages

__all__ = ["main", "nearby_keys"]


def nearby_keys(screen: ScreenMap, cell: tuple[int, int], keys_by_level: dict[tuple[str, int], list[str]]) -> list[str]:
    """The survey keys of every level whose outline in the key lies within two cells of a cell."""
    out: list[str] = []
    for outline in key_panel(screen).outlines:
        near = any(x - 2 <= cell[0] < x + w + 2 and y - 2 <= cell[1] < y + h + 2 for x, y, w, h in outline.rects)
        if near:
            out += keys_by_level.get((outline.dungeon, outline.level), [])
    return out


def _expected(text: str, keys: list[str]) -> str:
    if text in keys:
        return f"key:{text}"
    if text in LETTERS:
        return f"letter:{text}"
    return "none"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Label-sized marks and Clef's keys for them.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--pages", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ask", action="store_true", help="ask local Clef-flash about the marks without an answer")
    parser.add_argument("--sample", type=int, default=15, help="far marks asked per map")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)
    maps = [screen for path in args.map_files for screen in load_maps(path)]
    pages = {region: page for path in args.pages for page in load_pages(path) for _label, region in page.regions}
    provider = LocalClefProvider(LocalClefSettings()) if args.ask else None
    totals: Counter[str] = Counter()
    for screen in maps:
        page = pages[screen.id]
        keys_by_level = {(dungeon, number): keys for dungeon, number, keys in levels_on(page)}
        registration = key_region(screen).registration
        assert registration is not None
        corners = np.array(registration.corners, dtype=np.float64)
        ink = MapInk(load_rgb(screen.image), registration, INK_STYLES[screen.style])
        found_path = args.out / "marks" / f"{screen.id}.found.json"
        if found_path.exists():
            found = [
                FoundMark(box=tuple(m["box"]), cell=tuple(m["cell"]), glyphs=m["glyphs"])
                for m in json.loads(found_path.read_text())
            ]  # type: ignore[arg-type]
        else:
            found = find_marks(ink.darkness, ink.fill, corners)
            found_path.parent.mkdir(parents=True, exist_ok=True)
            found_path.write_text(json.dumps([m.__dict__ for m in found]) + "\n")
        key_marks = [(mark.text, parse_cell(mark.cell)) for mark in key_panel(screen).marks]

        def chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
            return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

        near = [i for i, m in enumerate(found) if any(chebyshev(m.cell, k) <= 1 for _t, k in key_marks)]
        far = [i for i in range(len(found)) if i not in set(near)]
        asked = sorted(near + random.Random(7).sample(far, min(args.sample, len(far))))
        store = AnswerStore(args.out / "marks" / f"{screen.id}.mark.jsonl")
        chunks_of = {i: key_chunks(nearby_keys(screen, found[i].cell, keys_by_level)) for i in asked}

        def stored(store: AnswerStore = store) -> dict[str, dict[str, float]]:
            out: dict[str, dict[str, float]] = {}
            if store.path.exists():
                for line in store.path.read_text().splitlines():
                    record = json.loads(line)
                    out[record["item"]] = record["probabilities"]
            return out

        if provider is not None:
            rgb = load_rgb(screen.image)

            def request_for(
                item: Item,
                rgb: np.ndarray = rgb,
                found: list[FoundMark] = found,
                registration: Registration = registration,
                chunks_of: dict[int, list[list[str]]] = chunks_of,
            ) -> object:
                index, part = item.name.split("/")
                crop = mark_crop(rgb, registration, found[int(index)])
                if part == "L":
                    return letter_request(crop, "clef-flash")
                return mark_request(crop, chunks_of[int(index)][int(part[1:])], "clef-flash")

            key_items = [
                Item(kind="mark", name=f"{i}/k{c}", cell=found[i].cell)
                for i in asked
                for c, chunk in enumerate(chunks_of[i])
                if chunk
            ]
            run_items(provider, store, key_items, request_for, workers=args.workers)  # type: ignore[arg-type]
            done = stored()
            letter_items = [
                Item(kind="mark", name=f"{i}/L", cell=found[i].cell)
                for i in asked
                if mark_answer([done[f"{i}/k{c}"] for c, chunk in enumerate(chunks_of[i]) if chunk], None) == "none"
            ]
            run_items(provider, store, letter_items, request_for, workers=args.workers)  # type: ignore[arg-type]
        done = stored()
        answers: dict[int, str] = {}
        for i in asked:
            names = [f"{i}/k{c}" for c, chunk in enumerate(chunks_of[i]) if chunk]
            if all(name in done for name in names):
                answers[i] = mark_answer([done[name] for name in names], done.get(f"{i}/L"))
        # Match each key mark to its nearest found mark.
        taken: set[int] = set()
        tally: Counter[str] = Counter()
        every_key = [key for keys in keys_by_level.values() for key in keys]
        for text, cell in key_marks:
            candidates = [i for i in asked if chebyshev(found[i].cell, cell) <= 1 and i not in taken]
            kind = "key" if _expected(text, every_key).startswith("key") else ("letter" if text in LETTERS else "other")
            tally[f"{kind} marks"] += 1
            if not candidates:
                continue
            best = min(candidates, key=lambda i: (chebyshev(found[i].cell, cell), i))
            taken.add(best)
            tally[f"{kind} found"] += 1
            if best in answers:
                tally[f"{kind} asked"] += 1
                expected = _expected(text, nearby_keys(screen, found[best].cell, keys_by_level))
                tally[f"{kind} read right"] += answers[best] == expected
        for i in asked:
            if i in taken or i not in answers:
                continue
            tally["extra asked"] += 1
            tally["extra answered none"] += answers[i] == "none"
        tally["found"] += len(found)
        totals.update(tally)
        print(screen.id, dict(tally), flush=True)
    print("all", dict(totals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
