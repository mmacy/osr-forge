"""The recheck after the owner's sample: what a `wrong` or `unsure` verdict sends back to the settler.

The owner checks a sample in the sample app, which writes a verdicts file beside it
(`sample.verdicts_path`). A map's key is approved when every call in its sample has a verdict and none is
wrong. A `wrong` verdict sends every call of that category on that map back for a full recheck: a fresh
settling round over all of them, in which the sampled call carries the owner's verdict and note as one more
reading. An `unsure` call goes back to the settler alone, with the owner's note. The recheck's prompt says the
items are checked afresh, because most of them are calls every reader agreed on, and each cell item carries
the pixel count of its floor share and its neighbours', so the half rule is decided by measure and not by eye.

A recheck's value is the call's final value from then on, unless the owner corrects the call: a correction of a
call judged wrong is recorded with `record_corrections` and replaces whatever the recheck found.
`recheck_sample` gathers every call whose value the recheck or a correction changed into a new sample, under
new ids, for the owner to check again.

The recheck's requests go through the map's `recheck` batch store, its calls to `recheck/calls.json`, and the
corrections to `owner/calls.json`.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from osrforge.tracing.truth import CELL_LETTERS, parse_cell, parse_edge

from .batches import BatchStore
from .classify import cell_floor_shares
from .keyspec import MapContext
from .reader import READ_LETTERS
from .readings import load_items
from .run import map_materials
from .sample import SampleCall, SampleSet, Verdicts, verdicts_path
from .samples import categorize, sample_calls
from .settler import (
    RECHECK_SYSTEM,
    SCHEMA,
    _add_jobs,
    _harvest,
    _load_calls,
    _rebuild_item,
    _request,
    _save_calls,
    final_values,
    with_reading,
    write_final,
)

__all__ = [
    "RECHECK_MARK",
    "cell_edge_items",
    "changed_calls",
    "collect_recheck",
    "recheck_items",
    "recheck_sample",
    "record_corrections",
    "sample_call_id",
    "submit_cell_edges",
    "submit_reask",
    "submit_recheck",
]

RECHECK_MARK = "@recheck"
"""The suffix that gives a call a new id in the sample of the calls a recheck changed."""


def sample_call_id(sample_id: str) -> tuple[str, str]:
    """A sample call's panel and call id: `b3-l1:c:4,28` and `b3-l1:c:4,28@recheck` both name `c:4,28`."""
    panel, _, call_id = sample_id.partition(":")
    return panel, call_id.split("@", 1)[0]


def _verdicts(sample_path: Path) -> tuple[SampleSet, Verdicts]:
    sample = SampleSet.model_validate(json.loads(sample_path.read_text()))
    path = verdicts_path(sample_path)
    verdicts = Verdicts.model_validate(json.loads(path.read_text())) if path.exists() else Verdicts()
    return sample, verdicts


_SHARES: dict[str, np.ndarray] = {}


def _shares(ctx: MapContext) -> np.ndarray:
    if ctx.key.id not in _SHARES:
        _SHARES[ctx.key.id] = cell_floor_shares(map_materials(ctx.spec, ctx.rgb, ctx.fit), ctx.fit)
    return _SHARES[ctx.key.id]


def pixel_count_text(shares: np.ndarray, cell: tuple[int, int]) -> str:
    """A cell's measured floor share and its eight neighbours', in words a settling request carries."""
    x, y = cell
    rows, columns = shares.shape

    def percent(cx: int, cy: int) -> str:
        if not (0 <= cx < columns and 0 <= cy < rows):
            return "off the map"
        return f"{round(100 * float(shares[cy, cx]))}"

    block = ", ".join(" ".join(percent(x + dx, y + dy) for dx in (-1, 0, 1)) for dy in (-1, 0, 1))
    return (
        f"{percent(x, y)}% of cell {x},{y} is floor; the percent floor of the 3 by 3 cells around it, row by row "
        f"from the north-west corner {x - 1},{y - 1}: {block}"
    )


def _label_edges(call_id: str, call: dict[str, Any]) -> list[str]:
    """The edges around the cells a label call names: its own cell and every cell its readings put the label in."""
    cells = {call_id[2:].split("|")[0]}
    for _reader, value in call.get("readings", []):
        place = str(value).rpartition("@")[2]
        numbers = [part.strip() for part in place.split(",")]
        if "@" in str(value) and len(numbers) % 2 == 0 and all(n.isdigit() for n in numbers):
            cells |= {f"{numbers[i]},{numbers[i + 1]}" for i in range(0, len(numbers), 2)}
    edges: set[str] = set()
    for text in cells:
        x, y = parse_cell(text)
        edges |= {f"{x},{y}:west", f"{x + 1},{y}:west", f"{x},{y}:north", f"{x},{y + 1}:north"}
    return sorted(edges)


def recheck_items(ctx: MapContext, sample_path: Path) -> list[dict[str, Any]]:
    """The calls a sample's verdicts send back for this map, as settling items.

    Args:
        ctx: The map.
        sample_path: The sample file the owner checked.

    Returns:
        Settling items: for each `wrong` verdict, every call of that category on this map, the sampled one with
        the owner's verdict among its readings; for each `unsure` verdict, that call with the owner's note. A note
        on a label also goes with the edges around the label's cells. A cell item carries the pixel count of its
        floor share.
    """
    sample, verdicts = _verdicts(sample_path)
    by_id = {call.id: call for call in sample.calls}
    items = load_items(ctx)
    final = final_values(ctx)
    wanted: dict[str, tuple[str, str]] = {}
    notes: dict[str, str] = {}
    categories_to_recheck: set[str] = set()
    for verdict in verdicts.verdicts:
        call = by_id.get(verdict.call)
        if call is None or call.panel != ctx.key.id or verdict.verdict == "right":
            continue
        if verdict.settled != call.settled:
            # The verdict was given on an earlier value of the call; it doesn't carry over.
            continue
        call_id = sample_call_id(verdict.call)[1]
        wanted[call_id] = (verdict.verdict, verdict.note)
        if verdict.verdict == "wrong":
            categories_to_recheck.add(call.category)
        if call_id.startswith("m:") and verdict.note:
            # A note on a label may say the glyph is part of the drawing, such as an archway's bars: the edges
            # around the cells the label was read in go back with it.
            for edge in _label_edges(call_id, final.get(call_id, {})):
                if f"e:{edge}" in final:
                    notes[f"e:{edge}"] = verdict.note
    if categories_to_recheck:
        for name in final:
            kind_code, _, rest = name.partition(":")
            kind = {"c": "cell", "e": "edge", "m": "mark", "s": "symbol"}.get(kind_code)
            if kind is None:
                continue
            category = categorize(ctx, rest.split("|")[0], kind)
            if category in categories_to_recheck and name not in wanted:
                wanted[name] = ("category", "")
    for name in notes:
        wanted.setdefault(name, ("note", ""))
    out: list[dict[str, Any]] = []
    for name, (why, note) in sorted(wanted.items()):
        item = _rebuild_item(ctx, items, name)
        if item is None:
            continue
        readings = list(item["readings"])
        if item["kind"] == "cell":
            readings.append(("the pixel count", pixel_count_text(_shares(ctx), parse_cell(name[2:]))))
        if why in ("wrong", "unsure"):
            readings.append((f"the owner's verdict ({why})", note or "no note"))
        if name in notes:
            readings.append(("the owner's note on the label read near here", notes[name]))
        text = (
            item["text"].split(" Readings:")[0] + " Readings: " + "; ".join(f"{r} read {v}" for r, v in readings) + "."
        )
        out.append({**item, "readings": readings, "text": text, "why": why})
    return out


def submit_recheck(
    ctx: MapContext, sample_path: Path, client: Any, ledger: Any, limit: int | None = None
) -> tuple[str | None, int]:
    """Add the recheck's jobs for a map and send the pending ones, at most `limit` of them now."""
    store = BatchStore(ctx.key.directory / "recheck")
    items = recheck_items(ctx, sample_path)
    count = _add_jobs(ctx, store, f"{ctx.key.id}__recheck__", items, False, RECHECK_SYSTEM)
    return store.submit(client, ledger, f"recheck {ctx.key.id}", limit), count


def collect_recheck(ctx: MapContext, client: Any, ledger: Any) -> dict[str, Any]:
    """Collect the recheck's answers into `recheck/calls.json` and rewrite `final.json`.

    The result lists the retries' job ids under `retry_ids`, so the caller sends those alone.
    """
    store = BatchStore(ctx.key.directory / "recheck")
    counts = store.collect(client, ledger)
    result: dict[str, Any] = {
        "calls": 0,
        "retries": 0,
        "open": counts["open"],
        "errored": counts["errored"],
        "challenged": 0,
        "pending": len(store.pending()),
    }
    calls = _load_calls(ctx, "recheck")
    _harvest(store, calls, False, result)
    _save_calls(ctx, "recheck", calls)
    write_final(ctx)
    result["retry_ids"] = list(store.added)
    return result


def submit_reask(ctx: MapContext, call_ids: list[str], client: Any, ledger: Any) -> tuple[str | None, int]:
    """Ask again, in one fresh recheck request, the recheck calls whose answer contradicts its own stated reason.

    Each item is the one the recheck asked, with the earlier answer and its reason among the readings. The job
    names the jobs it supersedes, so its answers replace theirs however often the store is collected.

    Args:
        ctx: The map.
        call_ids: The calls to ask again, such as `["e:2,7:north"]`.
        client: An `anthropic.Anthropic` client.
        ledger: The Claude ledger.

    Returns:
        The batch id and the number of items asked.
    """
    store = BatchStore(ctx.key.directory / "recheck")
    asked: dict[str, tuple[str, dict[str, Any]]] = {}
    for custom_id in store.job_ids():
        for item in store.job(custom_id)["meta"]["items"]:
            asked[item["id"]] = (store.job(custom_id)["meta"].get("retry_of") or custom_id, item)
    calls = _load_calls(ctx, "recheck")
    items, supersedes = [], set()
    for name in call_ids:
        job_id, item = asked[name]
        call = calls[name]
        items.append(
            with_reading(
                item,
                "an earlier recheck",
                f'{call["value"]}, with the reason "{call["reason"]}", which names another value than its answer',
            )
        )
        supersedes.add(calls[name].get("job", job_id))
    number = sum(1 for custom_id in store.job_ids() if "__recheck__zreask" in custom_id and "__r" not in custom_id[-3:])
    custom_id = f"{ctx.key.id}__recheck__zreask{number:03d}"
    params = _request(ctx, items, RECHECK_SYSTEM, SCHEMA, False)
    store.add_job(custom_id, params, {"map": ctx.key.id, "items": items, "supersedes": sorted(supersedes)})
    return store.submit(client, ledger, f"recheck reask {ctx.key.id}", ids=[custom_id]), len(items)


def cell_edge_items(ctx: MapContext) -> tuple[list[dict[str, Any]], set[str]]:
    """The edges of every cell a recheck or a correction turned from rock into floor, as settling items.

    The recheck asks cells and edges in one round, so its edge answers assume the cells' earlier letters, and an
    edge against a cell that was rock was never settled at all. The settling order applies settled cells before it
    asks edges; this is that step for the cells the recheck changed. Each item states the key's letters for both
    cells, with their pixel counts.

    Returns:
        The items, and the recheck jobs whose answers on those edges the new answers supersede.
    """
    from .letters import final_cells
    from .settler import _describe_edge, _readings_text

    items = load_items(ctx)
    final = final_values(ctx)
    cells = final_cells(items, final, {})
    height, width = len(cells), len(cells[0])
    rechecked = _load_calls(ctx, "recheck")
    shares = _shares(ctx)
    out: list[dict[str, Any]] = []
    supersedes: set[str] = set()
    seen: set[str] = set()
    for name in changed_calls(ctx):
        if not name.startswith("c:") or final[name]["value"] == "#":
            continue
        x, y = parse_cell(name[2:])
        for other, edge in (
            ((x - 1, y), f"{x},{y}:west"),
            ((x + 1, y), f"{x + 1},{y}:west"),
            ((x, y - 1), f"{x},{y}:north"),
            ((x, y + 1), f"{x},{y + 1}:north"),
        ):
            ox, oy = other
            if not (0 <= ox < width and 0 <= oy < height) or cells[oy][ox] == "#" or edge in seen:
                continue
            seen.add(edge)
            item = _rebuild_item(ctx, items, f"e:{edge}") or {
                "id": f"e:{edge}",
                "kind": "edge",
                "text": f"edge {edge}, {_describe_edge(edge)}: which letter? Readings: none.",
                "readings": [],
            }
            for cx, cy in ((x, y), other):
                item = with_reading(
                    item,
                    "the key's cells",
                    f"cell {cx},{cy} is {'floor' if cells[cy][cx] == '.' else 'water'}, "
                    f"{round(100 * float(shares[cy, cx]))}% floor by pixel count",
                )
            earlier = rechecked.get(f"e:{edge}")
            if earlier is not None:
                item = with_reading(
                    item, "an earlier recheck", f"{earlier['value']}, given before the cells were settled"
                )
                supersedes.add(earlier["job"])
            item["text"] = item["text"].split(" Readings:")[0] + f" Readings: {_readings_text(item['readings'])}."
            out.append(item)
    return out, supersedes


def submit_cell_edges(ctx: MapContext, client: Any, ledger: Any) -> tuple[str | None, int]:
    """Send `cell_edge_items` in fresh recheck requests that supersede the earlier answers on those edges."""
    store = BatchStore(ctx.key.directory / "recheck")
    items, supersedes = cell_edge_items(ctx)
    asked = {
        item["id"]
        for custom_id in store.job_ids()
        if "__zedges" in custom_id
        for item in store.job(custom_id)["meta"]["items"]
    }
    items = [item for item in items if item["id"] not in asked]
    ids = []
    start = sum(1 for custom_id in store.job_ids() if "__zedges" in custom_id and not custom_id.endswith("__r"))
    for number, first in enumerate(range(0, len(items), 8), start):
        batch = items[first : first + 8]
        custom_id = f"{ctx.key.id}__recheck__zedges{number:03d}"
        params = _request(ctx, batch, RECHECK_SYSTEM, SCHEMA, False)
        store.add_job(custom_id, params, {"map": ctx.key.id, "items": batch, "supersedes": sorted(supersedes)})
        ids.append(custom_id)
    if not ids:
        return None, 0
    return store.submit(client, ledger, f"recheck cell edges {ctx.key.id}", ids=ids), len(items)


def record_corrections(ctx: MapContext, sample_path: Path, values: dict[str, str]) -> dict[str, str]:
    """Record the owner's value for calls the owner judged wrong in a sample.

    Args:
        ctx: The map.
        sample_path: The sample file whose verdicts judged the calls.
        values: The owner's value by call id, such as `{"c:4,28": "."}`.

    Returns:
        The values recorded, by call id.

    Raises:
        ValueError: If a call has no `wrong` verdict on this map in the sample, or the value isn't a letter of the
            call's kind.
    """
    sample, verdicts = _verdicts(sample_path)
    by_id = {call.id: call for call in sample.calls}
    judged = {
        sample_call_id(verdict.call)[1]: verdict
        for verdict in verdicts.verdicts
        if verdict.verdict == "wrong" and by_id.get(verdict.call) and by_id[verdict.call].panel == ctx.key.id
    }
    corrections = _load_calls(ctx, "owner")
    for call_id, value in values.items():
        verdict = judged.get(call_id)
        if verdict is None:
            raise ValueError(f"{ctx.key.id} {call_id}: the sample has no wrong verdict on it")
        kind = {"c": "cell", "e": "edge"}.get(call_id[0])
        if kind is None or value not in (CELL_LETTERS if kind == "cell" else READ_LETTERS):
            raise ValueError(f"{ctx.key.id} {call_id}: {value!r} isn't a {kind or 'cell or edge'} letter")
        corrections[call_id] = {
            "value": value,
            "kind": kind,
            "note": verdict.note,
            "sample": sample_path.name,
            "judged": verdict.settled,
        }
    _save_calls(ctx, "owner", corrections)
    write_final(ctx)
    return {call_id: corrections[call_id]["value"] for call_id in values}


def changed_calls(ctx: MapContext) -> list[str]:
    """The ids of the calls whose value a recheck or an owner's correction changed."""
    changed = []
    for name, call in final_values(ctx).items():
        # The value before any of them: the recheck's `before_recheck` when there was one, else the owner's.
        before = call.get("before_recheck", call.get("before_owner"))
        if ("before_recheck" in call or "before_owner" in call) and before != call["value"]:
            changed.append(name)
    return sorted(changed)


def effective_changes(ctx: MapContext, names: list[str]) -> tuple[list[str], list[str]]:
    """Split changed calls into those whose change reaches the assembled cells and edges and those it doesn't.

    An edge call between floor and rock is a wall in the key whatever the call says, so a recheck that changes
    such a call's letter changes nothing the key records.

    Args:
        ctx: The map.
        names: Changed call ids.

    Returns:
        The calls whose change reaches the key, and the rest.
    """
    from .letters import final_cells, final_edges

    items = load_items(ctx)
    after = final_values(ctx)
    before = {name: dict(call) for name, call in after.items()}
    for name in names:
        call = before[name]
        call["value"] = call.get("before_recheck", call.get("before_owner"))
    letters = {}
    for label, final in (("after", after), ("before", before)):
        cells = final_cells(items, final, {})
        letters[label] = (cells, final_edges(items, cells, final, {}))
    reach, inert = [], []
    for name in names:
        kind, _, rest = name.partition(":")
        if kind == "c":
            x, y = parse_cell(rest)
            differs = letters["after"][0][y][x] != letters["before"][0][y][x]
        elif kind == "e":
            cell, side = parse_edge(rest)
            differs = letters["after"][1].at(cell, side) != letters["before"][1].at(cell, side)
        else:
            differs = True
        (reach if differs else inert).append(name)
    return reach, inert


def recheck_sample(contexts: list[MapContext], out: Path) -> dict[str, Any]:
    """Write a sample of every call whose value a recheck or a correction changed in the key, under new ids.

    Each call's id is its first sample id with `RECHECK_MARK` added, so the owner's verdicts on the first sample
    don't carry over, and its reason says what the value was before. A change that doesn't reach the assembled
    cells and edges is listed in the result and left out.

    Args:
        contexts: The maps the recheck covered.
        out: The sample file to write; crops go beside it.

    Returns:
        Counts by map and category, and the file written.
    """
    calls: list[SampleCall] = []
    counts: dict[str, Any] = {}
    for ctx in contexts:
        reach, inert = effective_changes(ctx, changed_calls(ctx))
        changed = set(reach)
        if inert:
            counts[f"{ctx.key.id} changed with no effect on the key"] = inert
        final = final_values(ctx)
        for call in sample_calls(ctx, ctx.key.member, ctx.spec.truth_style, every=True, fallback="key"):
            call_id = sample_call_id(call.id)[1]
            if call_id not in changed:
                continue
            entry = final[call_id]
            before = entry.get("before_recheck", entry.get("before_owner"))
            source = "the owner's correction" if "before_owner" in entry else "the recheck"
            for attribute in ("zoom", "wide"):
                target = out.parent / f"{ctx.key.id}__{getattr(call, attribute)}"
                if not target.exists():
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ctx.key.directory / "crops" / getattr(call, attribute), target)
            calls.append(
                call.model_copy(
                    update={
                        "id": f"{call.id}{RECHECK_MARK}",
                        "zoom": f"{ctx.key.id}__{call.zoom}",
                        "wide": f"{ctx.key.id}__{call.wide}",
                        "reason": f"Changed by {source} from {before}. {call.reason}".strip(),
                    }
                )
            )
            key = f"{ctx.key.id} {call.category}"
            counts[key] = counts.get(key, 0) + 1
    out.write_text(json.dumps(SampleSet(calls=tuple(calls)).model_dump(mode="json"), indent=1) + "\n")
    return {"written": str(out), "calls": len(calls), **counts}
