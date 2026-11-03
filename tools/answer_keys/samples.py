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


def _reading_value(reader: str, value: str) -> str:
    text = value.split(" (p=")[0]
    return WORD_LETTERS.get(text, text)


def sample_calls(ctx: MapContext, member: str, style: str) -> list[SampleCall]:
    """Every settled call of a map that has a category, as sample calls with crops rendered."""
    final = final_values(ctx)
    calls = []
    for call_id, call in final.items():
        if call.get("settled") is None:
            continue
        kind_code, _, rest = call_id.partition(":")
        kind = {"c": "cell", "e": "edge", "m": "mark", "s": "symbol"}[kind_code]
        if kind == "symbol":
            continue
        item = rest.split("|")[0]
        category = categorize(ctx, item, kind)
        if category is None:
            continue
        zoom, wide = crops_for(ctx, call_id)
        readings = tuple(
            Reading(reader=READER_NAMES[reader], value=_reading_value(reader, value))
            for reader, value in call["readings"]
            if reader in READER_NAMES
        )
        if kind == "mark":
            label = call["value"].rpartition("@")[0] if "@" in call["value"] else call["value"]
            item = label or item
            settled = call["value"].rpartition("@")[2] if "@" in call["value"] else call["value"]
            readings = tuple(
                Reading(reader="claude", value=value.rpartition("@")[2] if "@" in value else value)
                for _reader, value in call["readings"]
            )
        else:
            settled = call["value"]
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
                adversarial=call.get("adversarial"),
                reason=call.get("reason", ""),
            )
        )
    return calls


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
            source = ctx.key.directory / "crops" / getattr(call, attribute)
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
