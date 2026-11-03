"""The recheck after the owner's sample: what a `wrong` or `unsure` verdict sends back to the settler.

The owner checks a sample in the sample app, which writes a verdicts file beside it
(`sample.verdicts_path`). A map's key is approved when every call in its sample has a verdict and none is
wrong. A `wrong` verdict sends every call of that category on that map back for a full recheck: a fresh
settling round over all of them, in which the sampled call carries the owner's verdict and note as one more
reading. An `unsure` call goes back to the settler alone, with the owner's note. A recheck's value is the call's
final value from then on, and `fresh_sample` draws a new sample of the calls the recheck changed, for the owner
to check again.

The recheck's requests go through the map's `recheck` batch store, and its calls to `recheck/calls.json`.
"""

import json
from pathlib import Path
from typing import Any

from .batches import BatchStore
from .keyspec import MapContext
from .readings import load_items
from .sample import SampleSet, Verdicts, verdicts_path
from .samples import categorize
from .settler import _add_jobs, _harvest, _load_calls, _rebuild_item, _save_calls, final_values, write_final

__all__ = ["collect_recheck", "recheck_items", "submit_recheck"]


def _verdicts(sample_path: Path) -> tuple[SampleSet, Verdicts]:
    sample = SampleSet.model_validate(json.loads(sample_path.read_text()))
    path = verdicts_path(sample_path)
    verdicts = Verdicts.model_validate(json.loads(path.read_text())) if path.exists() else Verdicts()
    return sample, verdicts


def recheck_items(ctx: MapContext, sample_path: Path) -> list[dict[str, Any]]:
    """The calls a sample's verdicts send back for this map, as settling items.

    Args:
        ctx: The map.
        sample_path: The sample file the owner checked.

    Returns:
        Settling items: for each `wrong` verdict, every call of that category on this map, the sampled one with
        the owner's verdict among its readings; for each `unsure` verdict, that call with the owner's note.
    """
    sample, verdicts = _verdicts(sample_path)
    by_id = {call.id: call for call in sample.calls}
    items = load_items(ctx)
    final = final_values(ctx)
    wanted: dict[str, tuple[str, str]] = {}
    categories_to_recheck: set[str] = set()
    for verdict in verdicts.verdicts:
        call = by_id.get(verdict.call)
        if call is None or call.panel != ctx.key.id or verdict.verdict == "right":
            continue
        call_id = verdict.call.split(":", 1)[1]
        wanted[call_id] = (verdict.verdict, verdict.note)
        if verdict.verdict == "wrong":
            categories_to_recheck.add(call.category)
    if categories_to_recheck:
        for name in final:
            kind_code, _, rest = name.partition(":")
            kind = {"c": "cell", "e": "edge", "m": "mark", "s": "symbol"}.get(kind_code)
            if kind is None:
                continue
            category = categorize(ctx, rest.split("|")[0], kind)
            if category in categories_to_recheck and name not in wanted:
                wanted[name] = ("category", "")
    out: list[dict[str, Any]] = []
    for name, (why, note) in sorted(wanted.items()):
        item = _rebuild_item(ctx, items, name)
        if item is None:
            continue
        readings = list(item["readings"])
        if why in ("wrong", "unsure"):
            readings.append((f"the owner's verdict ({why})", note or "no note"))
        text = (
            item["text"].split(" Readings:")[0] + " Readings: " + "; ".join(f"{r} read {v}" for r, v in readings) + "."
        )
        out.append({**item, "readings": readings, "text": text, "why": why})
    return out


def submit_recheck(ctx: MapContext, sample_path: Path, client: Any, ledger: Any) -> tuple[str | None, int]:
    """Add the recheck's jobs for a map and send the pending ones."""
    store = BatchStore(ctx.key.directory / "recheck")
    items = recheck_items(ctx, sample_path)
    count = _add_jobs(ctx, store, f"{ctx.key.id}__recheck__", items, False)
    return store.submit(client, ledger, f"recheck {ctx.key.id}"), count


def collect_recheck(ctx: MapContext, client: Any, ledger: Any) -> dict[str, int]:
    """Collect the recheck's answers into `recheck/calls.json` and rewrite `final.json`."""
    store = BatchStore(ctx.key.directory / "recheck")
    counts = store.collect(client, ledger)
    result = {"calls": 0, "retries": 0, "open": counts["open"], "errored": counts["errored"], "challenged": 0}
    calls = _load_calls(ctx, "recheck")
    _harvest(store, calls, False, result)
    _save_calls(ctx, "recheck", calls)
    write_final(ctx)
    return result


def changed_by_recheck(ctx: MapContext) -> list[str]:
    """The ids of the calls whose recheck value differs from the value they had before."""
    rechecked = _load_calls(ctx, "recheck")
    final = final_values(ctx)
    changed = []
    for name, call in rechecked.items():
        before = final.get(name, {}).get("before_recheck")
        if before is not None and before != call["value"]:
            changed.append(name)
    return changed
