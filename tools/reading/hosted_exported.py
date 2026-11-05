"""Ask a hosted Clef backend the answer-key pass's own exported edge requests, inside the screening windows.

The answer-key pass exported the edge requests local Clef-flash answered (`<work>/clef/requests/requests.jsonl` and
the crops beside it), each with its key computed for the model `clef`. Each request is rebuilt from the exported crop,
state, question, and options and checked against that key before anything is sent, so its pixels and text are the
exported request's byte for byte. Hosted Clef gets the rebuilt request as it is, and hosted Clef-flash gets the
same request with only the model changed. Either way the hosted answers pair with the stored local ones on the same crop
and text.

Run from the osr-forge checkout, with `OSRFORGE_CLEF_ACCOUNT_ID` and `OSRFORGE_CLEF_LEDGER` set and a wrangler login:

    uv run python -m tools.reading.hosted_exported <map file>... --windows <file>... --out <dir> \\
        [--model clef] [--workers 4] [--max-requests N] [--spend 0.2]

Answers go to `<dir>/hosted-<model>/keyset-exported/<map id>.edge.jsonl` on the key's grid.
"""

import argparse
import json
import time
from pathlib import Path

from osrforge.providers.decisions import ClefModel, DecisionProvider, DecisionRequest
from osrforge.tracing.truth import edge_key, parse_edge

from .clefask import AnswerStore, Item, capped_stop, run_items
from .maps import load_maps
from .windows import load_windows

__all__ = ["checked_requests", "exported_requests", "for_model", "main"]


def checked_requests(requests: dict[str, tuple[DecisionRequest, str]], map_id: str) -> dict[str, DecisionRequest]:
    """The rebuilt requests, each checked against the key the answer-key pass exported for it.

    A rebuilt request's key is the hash of its pixels, state, question, options, and model, so an equal key means the
    crop, the text, and the options are the exported request's byte for byte.

    Args:
        requests: Each edge's rebuilt request and exported key, from `exported_requests`.
        map_id: The map, for the error.

    Returns:
        Each edge's request, the model `clef` as exported.

    Raises:
        ValueError: If a rebuilt request's key differs from the exported one.
    """
    for name, (request, key) in requests.items():
        if request.key() != key:
            raise ValueError(f"{map_id} {name}: the rebuilt request's key differs from the exported one")
    return {name: request for name, (request, _key) in requests.items()}


def for_model(request: DecisionRequest, model: ClefModel) -> DecisionRequest:
    """The same request asked of another model: only the model differs."""
    return DecisionRequest(
        tag=request.tag,
        image=request.image,
        state=request.state,
        question=request.question,
        options=request.options,
        model=model,
    )


def exported_requests(work: Path, frame: tuple[int, int] | None) -> dict[str, tuple[DecisionRequest, str]]:
    """Every exported edge request of a map, by its edge on the key's grid, with the exported key."""
    dx, dy = frame or (0, 0)
    folder = work / "clef" / "requests"
    out = {}
    for line in (folder / "requests.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["kind"] != "edge":
            continue
        (x, y), side = parse_edge(record["item"])
        if x < dx or y < dy:
            continue
        request = DecisionRequest(
            tag="edge",
            image=(folder / record["png"]).read_bytes(),
            state=record["state"],
            question=record["question"],
            options=tuple((option, text) for option, text in record["options"]),
            model="clef",
        )
        out[edge_key((x - dx, y - dy), side)] = (request, record["key"])
    return out


def main(argv: list[str] | None = None) -> int:
    from osrforge.providers.clef import ClefProvider, ClefSettings

    parser = argparse.ArgumentParser(description="Hosted Clef on the answer-key pass's exported requests.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--windows", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", choices=("clef", "clef-flash"), default="clef")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--spend", type=float, default=0.2)
    args = parser.parse_args(argv)
    settings = ClefSettings.from_env()
    settings = settings.model_copy(
        update={
            "run_label": f"reading screening keyset-exported {args.model}",
            "ledger_stop_usd": capped_stop(settings.ledger_path, settings.ledger_stop_usd, args.spend),
        }
    )
    provider: DecisionProvider = ClefProvider(settings)
    windows = [window for path in args.windows for window in load_windows(path)]
    budget = args.max_requests
    for screen in [screen for path in args.map_files for screen in load_maps(path)]:
        mine = [window for window in windows if window.map == screen.id]
        if not mine or screen.work is None:
            continue
        requests = exported_requests(screen.work, screen.frame)
        in_windows = {name: pair for name, pair in requests.items() if any(w.holds(parse_edge(name)[0]) for w in mine)}
        try:
            wanted = checked_requests(in_windows, screen.id)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        store = AnswerStore(args.out / f"hosted-{args.model}" / "keyset-exported" / f"{screen.id}.edge.jsonl")
        done = store.answered()
        items = [Item(kind="edge", name=name, cell=parse_edge(name)[0], side=parse_edge(name)[1]) for name in wanted]
        items = [item for item in items if item.name not in done]
        if budget is not None:
            items = items[:budget]

        def request_for(item: Item, wanted: dict[str, DecisionRequest] = wanted) -> DecisionRequest:
            return for_model(wanted[item.name], args.model)

        started = time.monotonic()
        added = run_items(provider, store, items, request_for, workers=args.workers)  # type: ignore[arg-type]
        print(f"{screen.id}: {added} new answers of {len(wanted)} in {time.monotonic() - started:.0f}s", flush=True)
        if budget is not None:
            budget -= added
            if budget <= 0:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
