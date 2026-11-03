"""The answer-key pipeline, one stage at a time.

Run from the osr-forge checkout, with a spec file (`keys.json` here, or a commercial map's spec outside the repository):

    uv run python -m tools.answer_keys.pipeline <keys.json> <map id>... <stage> [options]

The stages, in the order a map goes through them:

- `windows submit` and `windows collect`: Claude's window reads through the Batches API (`windows.py`).
- `marks submit` and `marks collect`: the second reading of labels and symbols (`marks.py`).
- `wholemap submit` and `wholemap collect`: the whole-map read of outlines, legend, and compass (`marks.py`).
- `clef`: Clef's answer for each edge (`clefpass.py`).
- `disagree`: the table of every item's readings and the disagreements (`readings.py`).
- `settle submit` and `settle collect`: the settler (`settler.py`).
- `adversary submit` and `adversary collect`: the adversarial pass and the re-settling it triggers.
- `assemble`: the key's reading layer, as a geometry truth document (`assemble.py`).
- `sample`: the owner's sample (`samples.py`).
- `score`: the settled reading against a synthetic map's exact answer or the settled windows (`scoring.py`).

`collect` stages take `--wait` to poll until every open batch has ended. Every stage is safe to run again:
jobs and answers that exist are kept.
"""

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

import anthropic

from osrforge.providers.ledger import Ledger

from .batches import LEDGER_PATH, BatchStore, api_key
from .keyspec import KeySpec, context, load_key_specs

__all__ = ["main"]


def client() -> anthropic.Anthropic:
    """An API client with the key from the environment file."""
    return anthropic.Anthropic(api_key=api_key(), max_retries=3)


def ledger() -> Ledger:
    """The shared Claude ledger."""
    return Ledger(LEDGER_PATH, cost_key="cost_usd")


def _wait(collect: Callable[[], dict[str, int]], interval: float, limit: float) -> dict[str, int]:
    """Call `collect` until no batch is open or the time limit passes."""
    started = time.time()
    while True:
        counts = collect()
        if counts.get("open", 0) == 0 or time.time() - started > limit:
            return counts
        print(f"  {counts['open']} batch(es) still running; waiting {interval:.0f}s", flush=True)
        time.sleep(interval)


def run_stage(key: KeySpec, stage: str, action: str | None, args: argparse.Namespace) -> None:
    """Run one stage for one map."""
    ctx = context(key)
    batch_stage = stage in ("windows", "marks", "wholemap", "settle", "adversary", "recheck")
    api = client() if batch_stage else None
    book = ledger() if batch_stage else Ledger(LEDGER_PATH, cost_key="cost_usd")
    if stage == "windows":
        from .windows import collect_windows, submit_windows

        store = BatchStore(key.directory / "windows")
        if action == "submit":
            batch = submit_windows(ctx, store, api, book)
            print(f"{key.id}: windows batch {batch}, {len(store.job_ids())} jobs")
        else:
            log = key.directory / "windows" / "malformed.jsonl"

            def collect():
                return collect_windows(ctx, store, api, book, log)

            counts = _wait(collect, args.interval, args.limit) if args.wait else collect()
            print(f"{key.id}: windows {counts}")
            if counts["retries"] or counts["fresh"]:
                batch = store.submit(api, book, f"windows {key.id} retries", ids=store.added)
                print(f"{key.id}: retry batch {batch}")
            print(f"{key.id}: windows spent ${store.spent():.4f}")
    elif stage == "marks":
        from .marks import collect_marks, submit_marks

        store = BatchStore(key.directory / "marks")
        if action == "submit":
            batch = submit_marks(ctx, store, api, book)
            print(f"{key.id}: marks batch {batch}, {len(store.job_ids())} jobs")
        else:

            def collect():
                return collect_marks(ctx, store, api, book)

            counts = _wait(collect, args.interval, args.limit) if args.wait else collect()
            print(f"{key.id}: marks {counts}")
            if counts.get("retries"):
                batch = store.submit(api, book, f"marks {key.id} retries", ids=store.added)
                print(f"{key.id}: retry batch {batch}")
            print(f"{key.id}: marks spent ${store.spent():.4f}")
    elif stage == "wholemap":
        from .marks import collect_wholemap, submit_wholemap

        store = BatchStore(key.directory / "wholemap")
        if action == "submit":
            batch = submit_wholemap(ctx, store, api, book)
            print(f"{key.id}: wholemap batch {batch}, {len(store.job_ids())} jobs")
        else:

            def collect():
                return collect_wholemap(ctx, store, api, book)

            counts = _wait(collect, args.interval, args.limit) if args.wait else collect()
            print(f"{key.id}: wholemap {counts}")
            if counts.get("retries"):
                batch = store.submit(api, book, f"wholemap {key.id} retries", ids=store.added)
                print(f"{key.id}: retry batch {batch}")
            print(f"{key.id}: wholemap spent ${store.spent():.4f}")
    elif stage == "clef":
        from .clefpass import export_requests, run_clef

        if action == "export":
            print(f"{key.id}: clef export {export_requests(ctx)}")
        else:
            print(f"{key.id}: clef {run_clef(ctx, args.workers, args.cap)}")
    elif stage == "disagree":
        from .readings import write_items

        print(f"{key.id}: {write_items(ctx)}")
    elif stage in ("settle", "adversary"):
        from .settler import collect_pass, submit_pass

        store = BatchStore(key.directory / stage)
        if action == "submit":
            batch, count = submit_pass(ctx, stage, store, api, book, args.jobs)
            print(f"{key.id}: {stage} batch {batch}, {count} items in {len(store.job_ids())} jobs")
        else:

            def collect():
                return collect_pass(ctx, stage, store, api, book)

            counts = _wait(collect, args.interval, args.limit) if args.wait else collect()
            print(f"{key.id}: {stage} {counts}")
            if counts.get("retries"):
                # A retry may belong to the adversarial store or, for a re-settling, to the resettle store.
                for retry_store in (store, BatchStore(key.directory / "resettle")):
                    batch = retry_store.submit(api, book, f"{stage} {key.id} retries", ids=counts["retry_ids"])
                    if batch is not None:
                        print(f"{key.id}: retry batch {batch}")
            print(f"{key.id}: {stage} spent ${store.spent():.4f}")
    elif stage == "recheck":
        from .recheck import collect_recheck, record_corrections, submit_cell_edges, submit_reask, submit_recheck

        if args.sample is None and action != "collect":
            raise SystemExit("recheck needs --sample, the sample file the owner checked")
        if action == "submit":
            batch, count = submit_recheck(ctx, args.sample, api, book, args.jobs)
            print(f"{key.id}: recheck batch {batch}, {count} items")
        elif action == "edges":
            batch, count = submit_cell_edges(ctx, api, book)
            print(f"{key.id}: cell edges batch {batch}, {count} items")
        elif action == "reask":
            mine = [name.partition(":")[2] for name in args.call if name.partition(":")[0] == key.id]
            if mine:
                batch, count = submit_reask(ctx, mine, api, book)
                print(f"{key.id}: reask batch {batch}, {count} items")
        elif action == "correct":
            mine = {}
            for text in args.set:
                name, _, value = text.rpartition("=")
                map_id, _, call_id = name.partition(":")
                if map_id == key.id:
                    mine[call_id] = value
            print(f"{key.id}: corrections {record_corrections(ctx, args.sample, mine)}")
        else:

            def collect():
                return collect_recheck(ctx, api, book)

            counts = _wait(collect, args.interval, args.limit) if args.wait else collect()
            print(f"{key.id}: recheck {counts}")
            if counts.get("retries"):
                batch = BatchStore(key.directory / "recheck").submit(
                    api, book, f"recheck {key.id} retries", ids=counts["retry_ids"]
                )
                print(f"{key.id}: retry batch {batch}")
    elif stage == "assemble":
        from .assemble import assemble

        print(f"{key.id}: {assemble(ctx, args.out)}")
    elif stage == "score":
        from .scoring import score_map, summary_line

        result = score_map(ctx, args.settled)
        print(f"{key.id}: {summary_line(result)}")
    else:
        raise SystemExit(f"unknown stage {stage}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The answer-key pipeline, one stage at a time.")
    parser.add_argument("keys", type=Path, help="the answer-key spec file")
    parser.add_argument("maps", nargs="+", help="map ids, then the stage and its action")
    parser.add_argument("--wait", action="store_true", help="poll until every open batch has ended")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between polls")
    parser.add_argument("--limit", type=float, default=3600.0, help="the most seconds to wait")
    parser.add_argument(
        "--jobs", type=int, default=None, help="settle or adversary submit: send at most this many jobs now"
    )
    parser.add_argument("--workers", type=int, default=4, help="threads for the Clef pass")
    parser.add_argument("--cap", type=float, default=2.0, help="the most dollars this work may spend on Clef")
    parser.add_argument("--out", type=Path, default=None, help="where assemble or sample writes")
    parser.add_argument("--size", type=int, default=50, help="calls per style in a sample")
    parser.add_argument("--seed", type=int, default=7, help="the sample's random seed")
    parser.add_argument("--settled", type=Path, default=None, help="settled windows to score against")
    parser.add_argument(
        "--stages", type=Path, default=None, help="combine: the directory whose stages/ the resolver reads"
    )
    parser.add_argument("--cases", action="append", default=[], help="combine: a case the key depends on, repeatable")
    parser.add_argument(
        "--verdicts",
        type=Path,
        action="append",
        default=[],
        help="combine: a sample file whose owner's verdicts the provenance counts, repeatable",
    )
    parser.add_argument("--sample", type=Path, default=None, help="recheck: the sample file whose verdicts to act on")
    parser.add_argument(
        "--call",
        action="append",
        default=[],
        help="recheck reask: a call whose answer contradicts its reason, as <map id>:<call id>, repeatable",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        help="recheck correct: the owner's value for a call judged wrong, as <map id>:<call id>=<value>, repeatable",
    )
    args = parser.parse_args(argv)
    words = list(args.maps)
    stages = {
        "windows",
        "marks",
        "wholemap",
        "clef",
        "disagree",
        "settle",
        "adversary",
        "recheck",
        "assemble",
        "sample",
        "score",
        "combine",
    }
    action = None
    if words and words[-1] in ("submit", "collect", "export", "correct", "edges", "reask", "resample"):
        action = words.pop()
    if not words or words[-1] not in stages:
        parser.error("the last words name a stage and, for a batch stage, submit or collect")
    stage = words.pop()
    specs = {spec.id: spec for spec in load_key_specs(args.keys)}
    for map_id in words:
        if map_id not in specs:
            parser.error(f"no map {map_id!r} in {args.keys}")
    if stage == "sample":
        from .samples import build_sample

        if args.out is None:
            parser.error("sample needs --out")
        print(build_sample([context(specs[map_id]) for map_id in words], args.out, args.size, args.seed))
        return 0
    if stage == "recheck" and action == "resample":
        from .recheck import recheck_sample

        if args.out is None:
            parser.error("recheck resample needs --out, the new sample file")
        print(recheck_sample([context(specs[map_id]) for map_id in words], args.out))
        return 0
    if stage == "combine":
        from .assemble import combine

        keys = [specs[map_id] for map_id in words]
        out = args.out or keys[0].key
        print(combine(keys, out, args.stages, args.cases, args.verdicts))
        return 0
    for map_id in words:
        run_stage(specs[map_id], stage, action, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
