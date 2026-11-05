"""The owner's sample: about 50 settled calls per map style from caves, diagonals, and keys, plus every key placement.

`build_sample` draws the calls of one style from the final values of its maps and writes a sample file in the
format of `sample.py`, with each call's zoomed and wide crops copied beside it. A call's category is where it
was drawn from: `cave` for an item in a cave cell, `diagonal` for an item on or beside a partly floor cell,
`key` for an item within a cell of a label, and `key_placement` for a label the settler placed. A settled call
that is none of those isn't sampled.
"""

import json
import random
import shutil
from pathlib import Path
from typing import Any

from osrforge.tracing.truth import parse_cell

from .frame import frame_of, to_registered_text
from .keyspec import MapContext
from .readings import edge_cells, edge_key, load_items
from .sample import Reader, Reading, SampleCall, SampleSet
from .settler import crops_for, final_values

__all__ = ["build_sample", "categorize", "sample_calls"]

READER_NAMES: dict[str, Reader] = {
    "the classifier": "classifier",
    "Claude's window read": "claude",
    "Clef": "clef",
    "the first label read": "claude",
    "the second label read": "claude",
    "the first symbol read": "claude",
    "the second symbol read": "claude",
    "the adversarial check": "claude",
    "the settler": "claude",
}

WORD_LETTERS = {
    "open": "o",
    "printed open line": "p",
    "wall": "w",
    "window": "n",
    "door": "d",
    "locked door": "l",
    "secret door": "s",
    "floor": ".",
    "rock": "#",
    "water": "~",
}


def categorize(ctx: MapContext, name: str, kind: str) -> str | None:
    """A call's category from where its item lies: cave, diagonal, key, or key_placement."""
    if kind == "mark":
        return "key_placement"
    items = load_items(ctx)
    if kind == "cell":
        cells = [parse_cell(name)]
    elif kind == "edge":
        cells = list(edge_cells(edge_key(name)))
    else:
        return None
    names = {f"{x},{y}" for x, y in cells}
    if names & set(items.cave):
        return "cave"
    if names & set(items.partial):
        return "diagonal"
    near = set()
    for mark in items.marks:
        for text in [*mark["reading1"], *mark["reading2"]]:
            x, y = parse_cell(text)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    near.add(f"{x + dx},{y + dy}")
    if names & near:
        return "key"
    return None


def _current_mark(ctx: MapContext, rest: str) -> bool:
    """Whether a label call `x,y|index` names a disputed label of the current table, as the assembly looks it up."""
    cell, _, index = rest.partition("|")
    marks = load_items(ctx).marks
    if not index.isdigit() or int(index) >= len(marks):
        return False
    mark = marks[int(index)]
    named = mark["reading1"] or mark["reading2"]
    return bool(named) and named[0] == cell


def _mark_cells(value: str) -> str:
    """A label reading's cells, `x,y` or several joined by `; `, from `text@x,y`, `x,y`, or `x,y,x,y`."""
    text = value.rpartition("@")[2] if "@" in value else value
    if text.strip().lower() in ("none", "no such label"):
        return "none"
    parts = [part for part in text.split(",") if part.strip()]
    if len(parts) % 2 == 0 and len(parts) > 2:
        return "; ".join(f"{parts[i]},{parts[i + 1]}" for i in range(0, len(parts), 2))
    return text


def _reading_value(reader: str, value: str) -> str:
    text = value.split(" (p=")[0]
    return WORD_LETTERS.get(text, text)


def sample_calls(
    ctx: MapContext, member: str, style: str, every: bool = False, fallback: str | None = None
) -> list[SampleCall]:
    """Every settled call of a map that has a category, as sample calls with crops rendered.

    `every` takes the calls every reader agreed on too, as a recheck's changes need, and `fallback` is the
    category of a call that has none, such as an edge sent back with the owner's note on a label. A call's reason
    is the one given for the value the key records: the owner's note, the recheck's, the re-settling's, or the
    settler's."""
    final = final_values(ctx)
    calls = []
    for call_id, call in final.items():
        if call.get("settled") is None and not every:
            continue
        kind_code, _, rest = call_id.partition(":")
        kind = {"c": "cell", "e": "edge", "m": "mark"}.get(kind_code)
        if kind is None:
            # Symbols and outline calls have no place in the sample's kinds.
            continue
        item = rest.split("|")[0]
        if kind == "mark" and not _current_mark(ctx, rest):
            # A label call from an earlier table that the assembly no longer applies.
            continue
        category = categorize(ctx, item, kind) or fallback
        if category is None:
            continue
        frame = frame_of(ctx)
        if frame is not None and not _in_frame(call_id, frame):
            # Outside the framed drawing: no part of the key.
            continue
        zoom, wide = _crops(ctx, call_id)
        readings = tuple(
            Reading(reader=READER_NAMES[reader], value=_reading_value(reader, value))
            for reader, value in call["readings"]
            if reader in READER_NAMES
        )
        if kind == "mark":
            label = call["value"].rpartition("@")[0] if "@" in call["value"] else call["value"]
            item = label or item
            settled = _mark_cells(call["value"])
            readings = tuple(Reading(reader="claude", value=_mark_cells(value)) for _reader, value in call["readings"])
        else:
            settled = call["value"]
        adversarial = call.get("adversarial")
        if kind == "mark" and adversarial is not None:
            adversarial = _mark_cells(adversarial)
        reason = (
            call.get("owner_note")
            or call.get("recheck_reason")
            or call.get("resettle_reason")
            or call.get("reason", "")
        )
        if frame is not None:
            # The sample shows the registered grid's cells, the key's own.
            call_id = to_registered_text(ctx, call_id)
            item = to_registered_text(ctx, item) if kind != "mark" else item
            readings = tuple(r.model_copy(update={"value": to_registered_text(ctx, r.value)}) for r in readings)
            settled = to_registered_text(ctx, settled)
            adversarial = to_registered_text(ctx, adversarial) if adversarial is not None else None
            reason = to_registered_text(ctx, reason)
        calls.append(
            SampleCall(
                id=f"{ctx.key.id}:{call_id}",
                member=member,
                map=Path(ctx.spec.image).name,
                panel=ctx.key.id,
                style=style,
                kind="key_placement" if kind == "mark" else kind,  # type: ignore[arg-type]
                category=category,  # type: ignore[arg-type]
                item=item,
                zoom=zoom.name,
                wide=wide.name,
                readings=readings,
                settled=settled,
                adversarial=adversarial,
                reason=reason,
            )
        )
    return calls


def _in_frame(call_id: str, frame: tuple[int, int, int, int]) -> bool:
    """Whether a working call's cell lies in the registered grid."""
    from osrforge.tracing.truth import parse_cell

    x, y = parse_cell(call_id.partition(":")[2].split("|")[0].split(":")[0])
    return frame[0] <= x < frame[0] + frame[2] and frame[1] <= y < frame[1] + frame[3]


def _crops(ctx: MapContext, call_id: str) -> tuple[Path, Path]:
    """A call's crops, on a framed map labelled with the registered grid's cells and kept apart from the working
    crops, whose names they would share."""
    frame = frame_of(ctx)
    if frame is None:
        return crops_for(ctx, call_id)
    from .settler import WIDE_PX, WIDE_REACH, ZOOM_PX, ZOOM_REACH, _item_place
    from .tiles import render_item

    directory = ctx.key.directory / "crops-registered"
    directory.mkdir(parents=True, exist_ok=True)
    name = to_registered_text(ctx, call_id)
    stem = name.replace(":", "_").replace(",", "-").replace("|", "_").replace("/", "_")
    zoom, wide = directory / f"{stem}_zoom.png", directory / f"{stem}_wide.png"
    cell, side = _item_place(call_id)
    offset = (frame[0], frame[1])
    if not zoom.exists():
        render_item(ctx.rgb, ctx.fit, cell, side, zoom, ZOOM_PX, ZOOM_REACH, offset)
    if not wide.exists():
        render_item(ctx.rgb, ctx.fit, cell, side, wide, WIDE_PX, WIDE_REACH, offset)
    return zoom, wide


def build_sample(contexts: list[MapContext], out: Path, size: int, seed: int) -> dict[str, Any]:
    """Draw one style's sample from its maps and write it with its crops.

    Args:
        contexts: The maps of one style.
        out: The sample file to write; crops go beside it.
        size: About how many settled calls to draw from caves, diagonals, and keys, split evenly among the
            categories that have calls and then among the maps.
        seed: The random seed.

    Returns:
        Counts by category.
    """
    style = contexts[0].spec.truth_style
    rng = random.Random(seed)
    pools: dict[str, list[tuple[MapContext, SampleCall]]] = {"cave": [], "diagonal": [], "key": [], "key_placement": []}
    for ctx in contexts:
        for call in sample_calls(ctx, ctx.key.member, style):
            pools[call.category].append((ctx, call))
    chosen: list[tuple[MapContext, SampleCall]] = list(pools["key_placement"])
    drawn_from = [name for name in ("cave", "diagonal", "key") if pools[name]]
    for name in drawn_from:
        share = -(-size // len(drawn_from))
        pool = pools[name]
        chosen.extend(rng.sample(pool, min(share, len(pool))))
    out.parent.mkdir(parents=True, exist_ok=True)
    calls = []
    for ctx, call in chosen:
        for attribute in ("zoom", "wide"):
            folder = "crops-registered" if frame_of(ctx) is not None else "crops"
            source = ctx.key.directory / folder / getattr(call, attribute)
            target = out.parent / f"{ctx.key.id}__{getattr(call, attribute)}"
            if not target.exists():
                shutil.copyfile(source, target)
        calls.append(
            call.model_copy(update={"zoom": f"{ctx.key.id}__{call.zoom}", "wide": f"{ctx.key.id}__{call.wide}"})
        )
    sample = SampleSet(calls=tuple(calls))
    out.write_text(json.dumps(sample.model_dump(mode="json"), indent=1) + "\n")
    return {
        "style": style,
        "written": str(out),
        **{name: sum(1 for c in calls if c.category == name) for name in pools},
        "pool_sizes": {name: len(pool) for name, pool in pools.items()},
    }
