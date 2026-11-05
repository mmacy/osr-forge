"""Send the offset picks over the grid of models and efforts, store each outcome, and score it.

A halves file is JSON: `{"pairs": [...]}`, each with `id`, `first` and `second` (the two halves' images, the one cut
off at its right edge first), `truth` (the letter of the true join, or `none`), `real` (true for a pair from a
printed module rather than a synthetic map), and optionally `first_fit` and
`second_fit`, a registration measurement's result file whose first region's corners stand in for osr-forge's
registration where that leaves a half unregistered. Otherwise each half is registered with
[`register_image`][osrforge.tracing.registration.register_image], taking its largest registered region. The row
shift and the candidates come from osr-forge's alignment of the two grids. `halves.json` beside this module lists the
synthetic pairs. The file for commercial maps stays outside the repository.

Run from the osr-forge checkout, with `OSRFORGE_CLAUDE_LEDGER` set:

    uv run python -m tools.reading.screen_offsets <halves file>... --out <dir> --models ... --efforts ... --cap 160
"""

import argparse
import json
from pathlib import Path

import numpy as np

from osrforge.tracing._registration_halves import align
from osrforge.tracing._registration_ink import ink_image, read_darkness
from osrforge.tracing.registration import _INK, GAP_TOLERANCE, OFFSET_CANDIDATES, register_image

from .claude_run import RequestCap, ask, make_provider
from .maps import load_rgb
from .offsets import HalfPairInput, check_offset, offset_kind, offset_request

__all__ = ["load_pair", "main"]


def _corners(image: Path, fit: Path | None) -> np.ndarray:
    if fit is not None:
        return np.array(json.loads(fit.read_text())["regions"][0]["registration"]["corners"], dtype=np.float64)
    fits = [item for item in register_image(image) if item.registration is not None]
    best = max(fits, key=lambda item: item.registration.columns * item.registration.rows)  # type: ignore[union-attr]
    return np.array(best.registration.corners, dtype=np.float64)  # type: ignore[union-attr]


def load_pair(item: dict[str, str], base: Path) -> tuple[HalfPairInput, tuple[int, ...], str]:
    """A pair's input, its candidate offsets, and its true letter."""

    def resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()

    first, second = resolve(item["first"]), resolve(item["second"])
    first_corners = _corners(first, resolve(item["first_fit"]) if item.get("first_fit") else None)
    second_corners = _corners(second, resolve(item["second_fit"]) if item.get("second_fit") else None)
    alignment = align(
        ink_image(read_darkness(first), _INK).ink,
        first_corners,
        ink_image(read_darkness(second), _INK).ink,
        second_corners,
        OFFSET_CANDIDATES,
        GAP_TOLERANCE,
    )
    pair = HalfPairInput(
        first=load_rgb(first),
        first_corners=first_corners,
        second=load_rgb(second),
        second_corners=second_corners,
        rows=alignment.rows,
        gaps=alignment.gaps,
    )
    return pair, alignment.columns, item["truth"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The offset picks over models and efforts.")
    parser.add_argument("halves", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=["claude-sonnet-5-5"])
    parser.add_argument("--efforts", nargs="+", default=["low", "medium", "high"])
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--cap", type=int, default=160)
    parser.add_argument("--tag", default="", help="a suffix that keeps a repeated trial's outcomes apart")
    parser.add_argument("--render", type=Path, default=None, help="write the join pictures here and send nothing")
    args = parser.parse_args(argv)
    pairs = []
    for path in args.halves:
        for item in json.loads(path.read_text())["pairs"]:
            if not args.only or item["id"] in args.only:
                pairs.append((item["id"], *load_pair(item, path.parent), bool(item.get("real", False))))
    if args.render is not None:
        from .offsets import LETTERS, join_picture

        args.render.mkdir(parents=True, exist_ok=True)
        for pair_id, pair, columns, truth, _real in pairs:
            print(pair_id, "rows", pair.rows, "columns", columns, "gaps", pair.gaps, "truth", truth)
            for gap, letter in zip(pair.gaps, LETTERS, strict=True):
                join_picture(pair, gap, letter).save(args.render / f"{pair_id}-{letter}.png")
        return 0
    for model in args.models:
        provider = make_provider(model)
        cap = RequestCap(provider.settings.ledger_path, args.cap)
        for effort in args.efforts:
            kind = offset_kind(effort)  # type: ignore[arg-type]
            for pair_id, pair, columns, truth, real in pairs:
                path = args.out / "offset" / model / effort / f"{pair_id}{args.tag}.json"
                if not path.exists():
                    outcome = ask(provider, offset_request(kind, pair), check_offset, cap)
                    record = outcome.to_json() | {
                        "columns": list(columns),
                        "gaps": list(pair.gaps),
                        "truth": truth,
                        "real": real,
                    }
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(record, indent=1) + "\n")
                stored = json.loads(path.read_text())
                choice = stored["answer"]["choice"] if stored["answer"] else None
                print(
                    f"{model} {effort} {pair_id}: chose {choice}, truth {truth}, "
                    f"{'right' if choice == truth else 'WRONG'}, ${stored['cost_usd']:.4f}",
                    flush=True,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
