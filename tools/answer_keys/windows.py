"""The window-read stage: crops and jobs for every window of a map, and the parsed answers.

`submit_windows` tiles the map, renders each window, and adds one job per window to the map's `windows` batch
store. `collect_windows` reads the answers back: a well-formed answer is parsed and written to
`parsed/<window>.json`; a malformed one gets one retry job, built by `reader.retry_params`; a window whose
retry is malformed too gets a fresh job (a second attempt from scratch), which the log records, because an
answer key needs every window read.
"""

import base64
import json
from pathlib import Path
from typing import Any

from .batches import BatchStore
from .keyspec import MapContext
from .reader import WindowAnswer, answer_text_of, check, parse, parse_answer_text, retry_params, window_params
from .tiles import Window, render_window, tile

__all__ = ["collect_windows", "parsed_windows", "submit_windows", "window_jobs"]


def window_jobs(ctx: MapContext, store: BatchStore, crops: Path) -> list[str]:
    """Render every window and add its job to the store. Returns the job ids."""
    ids = []
    for window in tile(ctx.fit.columns, ctx.fit.rows):
        crop = crops / f"{window.id}.png"
        if not crop.exists():
            render_window(ctx.rgb, ctx.fit, window, crop)
        custom_id = f"{ctx.key.id}__w__{window.id}"
        data = base64.b64encode(crop.read_bytes()).decode()
        params = window_params(data, window, ctx.key.style_note)
        meta = {"map": ctx.key.id, "window": [window.x0, window.y0, window.columns, window.rows], "attempt": 1}
        store.add_job(custom_id, params, meta)
        ids.append(custom_id)
    return ids


def submit_windows(ctx: MapContext, store: BatchStore, client: Any, ledger: Any) -> str | None:
    """Add the window jobs and send the pending ones."""
    window_jobs(ctx, store, ctx.key.directory / "windows" / "crops")
    return store.submit(client, ledger, f"windows {ctx.key.id}")


def fresh_params(ctx: MapContext, window: Window) -> dict[str, Any]:
    """The request for a fresh attempt at a window: the crop drawn again, so a renderer change reaches it."""
    crop = ctx.key.directory / "windows" / "crops" / f"{window.id}.png"
    render_window(ctx.rgb, ctx.fit, window, crop)
    data = base64.b64encode(crop.read_bytes()).decode()
    return window_params(data, window, ctx.key.style_note)


def _window_of(meta: dict[str, Any]) -> Window:
    x0, y0, columns, rows = meta["window"]
    return Window(x0, y0, columns, rows)


def collect_windows(ctx: MapContext, store: BatchStore, client: Any, ledger: Any, log: Path) -> dict[str, int]:
    """Collect answers, parse the well-formed ones, and queue a retry or a fresh attempt for the rest.

    Returns:
        Counts: `parsed` windows written now, `retries` queued, `fresh` attempts queued, `open` batches.
    """
    counts = store.collect(client, ledger)
    parsed_dir = ctx.key.directory / "windows" / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    result = {"parsed": 0, "retries": 0, "fresh": 0, "open": counts["open"], "errored": counts["errored"]}
    for custom_id in store.job_ids():
        answer = store.answer(custom_id)
        if answer is None:
            continue
        job = store.job(custom_id)
        window = _window_of(job["meta"])
        out = parsed_dir / f"{window.id}.json"
        if out.exists():
            continue
        text = answer_text_of(answer["message"])
        document = parse_answer_text(text)
        faults = ["the answer isn't a JSON object"] if document is None else check(document, window)
        if answer.get("stop_reason") in ("max_tokens", "refusal"):
            faults.insert(0, f"the answer stopped with {answer['stop_reason']}")
        if not faults and document is not None:
            parsed = parse(document, window)
            shift = shift_against_classifier(ctx, parsed)
            attempt = job["meta"].get("attempt", 1)
            if shift is not None and attempt < 3:
                # The whole window reads better moved by a cell: a miscount of rows or columns, not a reading.
                with log.open("a") as handle:
                    handle.write(json.dumps({"job": custom_id, "shifted": shift, "attempt": attempt}) + "\n")
                fresh_id = f"{ctx.key.id}__w__{window.id}__a{attempt + 1}"
                if not (store.jobs / f"{fresh_id}.json").exists():
                    store.add_job(fresh_id, fresh_params(ctx, window), {**job["meta"], "attempt": attempt + 1})
                    result["fresh"] += 1
                continue
            out.write_text(
                json.dumps(
                    {
                        "window": [window.x0, window.y0, window.columns, window.rows],
                        "job": custom_id,
                        "cells": {f"{x},{y}": letter for (x, y), letter in parsed.cells.items()},
                        "edges": {
                            f"{axis},{line},{index}": letter for (axis, line, index), letter in parsed.edges.items()
                        },
                        "partial": sorted(f"{x},{y}" for x, y in parsed.partial),
                        "marks": parsed.marks,
                        "symbols": parsed.symbols,
                        "notes": parsed.notes,
                        "cost_usd": answer.get("cost_usd"),
                    },
                    indent=1,
                )
            )
            result["parsed"] += 1
            continue
        attempt = job["meta"].get("attempt", 1)
        retried = job["meta"].get("retry_of")
        if retried is None:
            # The retry: the request, the malformed answer, and the faults, as one new request.
            follow_id = f"{custom_id}__r"
            params = retry_params(job["params"], text, faults)
            meta = {**job["meta"], "retry_of": custom_id}
            kind = "retries"
        else:
            if attempt >= 3:
                continue
            follow_id = f"{ctx.key.id}__w__{window.id}__a{attempt + 1}"
            params = fresh_params(ctx, window)
            meta = {**job["meta"], "attempt": attempt + 1, "retry_of": None}
            kind = "fresh"
        if (store.jobs / f"{follow_id}.json").exists():
            continue
        with log.open("a") as handle:
            handle.write(json.dumps({"job": custom_id, "faults": faults, "attempt": attempt, "next": follow_id}) + "\n")
        store.add_job(follow_id, params, meta)
        result[kind] += 1
    return result


SHIFT_MARGIN = 8
"""How many more cells a window must agree on with the classifier at a one-cell offset than in place before the
window counts as shifted."""


def shift_of(parsed: WindowAnswer, cells: tuple[str, ...]) -> list[int] | None:
    """The one-cell offset at which a window's cells agree with another reading's far better than in place.

    Args:
        parsed: The parsed window answer.
        cells: The other reading's cell rows, the classifier's.

    Returns:
        `[dx, dy]` when moving the window by that much raises its agreement by at least `SHIFT_MARGIN` cells,
        else `None`.
    """
    height = len(cells)
    width = len(cells[0]) if cells else 0

    def agreement(dx: int, dy: int) -> int:
        count = 0
        for (x, y), letter in parsed.cells.items():
            x, y = x + dx, y + dy
            if 0 <= x < width and 0 <= y < height and cells[y][x] == letter:
                count += 1
        return count

    in_place = agreement(0, 0)
    best = max(((agreement(dx, dy), dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)), key=lambda t: t[0])
    if (best[1], best[2]) != (0, 0) and best[0] - in_place >= SHIFT_MARGIN:
        return [best[1], best[2]]
    return None


def shift_against_classifier(ctx: MapContext, parsed: WindowAnswer) -> list[int] | None:
    """`shift_of` against the classifier's reading of the map."""
    from .readings import classifier_reading

    return shift_of(parsed, classifier_reading(ctx).panel.cells)


def parsed_windows(ctx: MapContext) -> list[dict[str, Any]]:
    """Every parsed window of a map."""
    parsed_dir = ctx.key.directory / "windows" / "parsed"
    return [json.loads(path.read_text()) for path in sorted(parsed_dir.glob("*.json"))] if parsed_dir.exists() else []
