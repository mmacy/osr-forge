"""Send the whole-page reads over the grid of models and efforts, store each outcome, and score it.

A pages file is JSON: `{"pages": [...]}`, each with `id`, `page` (the page number), `image`, `regions` (`[label,
region id]` pairs in the order the request names them), `key`, and `survey`. Relative paths resolve against the
file's directory. `pages.json` beside this module lists the committed pages; the file for commercial pages stays
outside the repository.

Run from the osr-forge checkout, with `OSRFORGE_CLAUDE_LEDGER` set:

    uv run python -m tools.reading.screen_wholemap <pages file>... --out <dir> --models claude-sonnet-5-5 \\
        --efforts low medium high --cap 160

An outcome already in `<dir>/wholemap/<model>/<effort>/<page id>.json` isn't asked again. `--score` scores the stored
outcomes without sending anything.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .claude_run import RequestCap, ask, make_provider
from .maps import key_document, load_rgb
from .wholemap import PageRegion, check_whole_map, score_whole_map, whole_map_kind, whole_map_request

__all__ = ["Page", "levels_on", "load_pages", "main"]


@dataclass(frozen=True)
class Page:
    """One map page with an answer key."""

    id: str
    number: int
    image: Path
    regions: tuple[tuple[str, str], ...]
    key: Path
    survey: Path


def load_pages(path: Path) -> list[Page]:
    """Read a pages file."""
    base = path.parent

    def resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()

    return [
        Page(
            id=item["id"],
            number=item["page"],
            image=resolve(item["image"]),
            regions=tuple((label, region) for label, region in item["regions"]),
            key=resolve(item["key"]),
            survey=resolve(item["survey"]),
        )
        for item in json.loads(path.read_text())["pages"]
    ]


def levels_on(page: Page) -> list[tuple[str, int, list[str]]]:
    """The levels the survey maps on a page, in survey order, with their keys as printed."""
    survey = json.loads(page.survey.read_text())
    out = []
    for dungeon in survey["dungeons"]:
        for level in dungeon["levels"]:
            if page.number in (level.get("map_pages") or []):
                keys = [area.get("source_label") or area["key"] for area in level["areas"]]
                out.append((dungeon["id"], level["number"], keys))
    return out


def _page_regions(page: Page) -> list[tuple[PageRegion, np.ndarray, Any]]:
    key = key_document(page.key)
    rgb = load_rgb(page.image)
    out = []
    for label, region_id in page.regions:
        region = key.region(region_id)
        assert region.registration is not None
        out.append((PageRegion(label, region_id, region.columns, region.rows), rgb, region.registration))
    return out


def _legend_from_page(page: Page) -> int | None:
    key = key_document(page.key)
    return key.region(page.regions[0][1]).legend.from_page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The whole-page reads over models and efforts.")
    parser.add_argument("pages", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=["claude-sonnet-5-5"])
    parser.add_argument("--efforts", nargs="+", default=["low", "medium", "high"])
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--cap", type=int, default=160)
    parser.add_argument("--score", action="store_true")
    args = parser.parse_args(argv)
    pages = [page for path in args.pages for page in load_pages(path) if not args.only or page.id in args.only]
    for model in args.models:
        provider = None if args.score else make_provider(model)
        cap = RequestCap(provider.settings.ledger_path, args.cap) if provider else None
        for effort in args.efforts:
            kind = whole_map_kind(effort)  # type: ignore[arg-type]
            for page in pages:
                path = args.out / "wholemap" / model / effort / f"{page.id}.json"
                regions = _page_regions(page)
                labels = [region for region, _rgb, _registration in regions]
                levels = levels_on(page)
                if not path.exists():
                    if provider is None or cap is None:
                        continue
                    request = whole_map_request(kind, load_rgb(page.image), regions, levels)
                    outcome = ask(
                        provider,
                        request,
                        lambda answer, labels=labels, levels=levels: check_whole_map(answer, labels, levels),
                        cap,
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(outcome.to_json(), indent=1) + "\n")
                stored = json.loads(path.read_text())
                if stored["answer"] is None:
                    print(f"{model} {effort} {page.id}: malformed after the retry")
                    continue
                score = score_whole_map(stored["answer"], key_document(page.key), labels, _legend_from_page(page))
                path.with_suffix(".score.json").write_text(json.dumps(score, indent=1) + "\n")
                styles = {k: v["style"] for k, v in score["regions"].items()}
                print(
                    f"{model} {effort} {page.id}: ${stored['cost_usd']:.4f}, attempts {len(stored['attempts'])}, "
                    f"styles {styles}, verdicts wrong {score['verdicts']['wrong']}",
                    flush=True,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
