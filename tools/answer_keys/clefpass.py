"""Clef's answer for each edge, the answer key's third reading, and for each cell the first two readings dispute.

Each request is one question about one crop with a red box on the item, the setup the probe measured best:
an edge crop of 2 by 2 cells at 128 pixels per cell with the box reaching 0.3 of a cell across the edge and
0.4 along it each way, and a cell crop of 3 by 3 cells at 96 pixels per cell with the box on the cell. The
options and their descriptions depend only on the map style. Every request goes through
`osrforge.providers.clef.ClefProvider`, which reserves its estimate on the Clef ledger before sending and
settles it from the usage. The answers are appended to `<map>/clef/answers.jsonl`, one line per item, and a
run skips the items that already have a line, so it can be stopped and started again.

The account id is read from the probe's client script inside this process and never printed. The token comes
from the wrangler login.
"""

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from pydantic import SecretStr

from osrforge.providers.clef import ClefProvider, ClefSettings, cost_usd, wrangler_token
from osrforge.providers.decisions import DecisionRequest

from .keyspec import MapContext
from .readings import classifier_reading, claude_cells, edges_touching

__all__ = ["CLEF_LEDGER", "STYLE_OPTIONS", "clef_items", "edge_crop", "run_clef"]

CLEF_LEDGER = Path.home() / ".re-pod/map-plan/clef-ledger.json"
PROBE_CLIENT = Path.home() / ".re-pod/map-plan/clef/probe/scripts/clef.py"
RED = (228, 20, 20)
EDGE_CELL_PX = 128
CELL_CELL_PX = 96

STYLE_OPTIONS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "tsr_blue": (
        "Crop of a printed dungeon map in blue ink. White squares are floor; solid blue is rock. A red box marks one "
        "cell edge.",
        (
            ("open", "Only a thin blue grid line between two white floor squares"),
            ("wall", "A thick blue line, or the border where white floor meets solid blue rock"),
            ("door", "A small white square with a blue outline sitting across a wall line"),
            ("secret_door", "The letter S printed on a wall line"),
        ),
    ),
    "black_white": (
        "Crop of a printed dungeon map in black ink on white paper. Rooms have heavy black outlines; thin grey lines "
        "are the grid. A red box marks one cell edge.",
        (
            ("open", "Only a thin grid line, or nothing, between two floor squares"),
            ("wall", "A heavy black line, or the edge of a solid black or grey band"),
            ("door", "A small rectangle drawn across a wall line"),
            ("window", "A window symbol across a wall line: a short bar with ticks, or a circle"),
        ),
    ),
    "cave": (
        "Crop of a printed cave map. White is floor; light grey is rock; cave walls are the freehand border between "
        "them. Dark grey straight lines are the grid. A red box marks one cell edge.",
        (
            ("open", "Floor continues across the edge, with only a grid line or nothing drawn on it"),
            ("wall", "The border between white floor and grey rock, or a heavy black wall line"),
            ("door", "A small white rectangle drawn across a wall"),
            ("locked_door", "A small rectangle with a dot inside it, drawn across a wall"),
            ("secret_door", "The letter S drawn on a wall"),
        ),
    ),
    "dark_rock": (
        "Crop of a printed cave map. White is floor; dark grey is rock; cave walls are the freehand border between "
        "them. Thin straight lines are the grid. A red box marks one cell edge.",
        (
            ("open", "White floor continues across the edge"),
            ("wall", "The border between white floor and dark grey rock crosses the edge"),
            ("door", "A door symbol drawn across the edge"),
        ),
    ),
}
"""The state text and the options for an edge question, by truth style."""

CELL_STATE = "Crop of a printed dungeon map. A red box marks one cell of its square grid."
CELL_OPTIONS = (
    ("floor", "Floor a party can stand on: a room, corridor, cave, or yard"),
    ("rock", "Solid rock, the earth of a hill, a solid wall band, or the blank page outside the map"),
    ("water", "Water: a moat, pool, or river drawn as water"),
)


def account_id() -> str:
    """The Cloudflare account id, from the probe's client script."""
    for line in PROBE_CLIENT.read_text().splitlines():
        match = re.match(r'^ACCOUNT\s*=\s*"([0-9a-f]+)"', line)
        if match:
            return match.group(1)
    raise SystemExit(f"no ACCOUNT line in {PROBE_CLIENT}")


def _crop(ctx: MapContext, cx: float, cy: float, span: float, cell_px: int) -> tuple[Image.Image, Any]:
    fit = ctx.fit
    sx, sy = cell_px / fit.x_axis.pitch, cell_px / fit.y_axis.pitch
    corners = [fit.corner(gx, gy) for gx in (cx - span / 2, cx + span / 2) for gy in (cy - span / 2, cy + span / 2)]
    px0 = int(np.floor(min(c[0] for c in corners)))
    py0 = int(np.floor(min(c[1] for c in corners)))
    px1 = int(np.ceil(max(c[0] for c in corners)))
    py1 = int(np.ceil(max(c[1] for c in corners)))
    height, width = ctx.rgb.shape[:2]
    padded = np.full((py1 - py0, px1 - px0, 3), 255, dtype=np.uint8)
    ax0, ay0, ax1, ay1 = max(px0, 0), max(py0, 0), min(px1, width), min(py1, height)
    if ax1 > ax0 and ay1 > ay0:
        padded[ay0 - py0 : ay1 - py0, ax0 - px0 : ax1 - px0] = ctx.rgb[ay0:ay1, ax0:ax1]
    image = Image.fromarray(padded).resize((round((px1 - px0) * sx), round((py1 - py0) * sy)), Image.Resampling.LANCZOS)

    def point(gx: float, gy: float) -> tuple[float, float]:
        x, y = fit.corner(gx, gy)
        return (x - px0) * sx, (y - py0) * sy

    return image, point


def edge_crop(ctx: MapContext, cell: tuple[int, int], side: str) -> bytes:
    """The 2 by 2 cell crop of an edge with the red box, as PNG."""
    x, y = cell
    if side == "west":
        cx, cy, box = float(x), y + 0.5, (x - 0.3, y + 0.1, x + 0.3, y + 0.9)
    elif side == "east":
        cx, cy, box = x + 1.0, y + 0.5, (x + 0.7, y + 0.1, x + 1.3, y + 0.9)
    elif side == "north":
        cx, cy, box = x + 0.5, float(y), (x + 0.1, y - 0.3, x + 0.9, y + 0.3)
    else:
        cx, cy, box = x + 0.5, y + 1.0, (x + 0.1, y + 0.7, x + 0.9, y + 1.3)
    image, point = _crop(ctx, cx, cy, 2.0, EDGE_CELL_PX)
    a, b = point(box[0], box[1]), point(box[2], box[3])
    ImageDraw.Draw(image).rectangle([a, b], outline=RED, width=3)
    return _png(image)


def cell_crop(ctx: MapContext, cell: tuple[int, int]) -> bytes:
    """The 3 by 3 cell crop of a cell with the red box, as PNG."""
    x, y = cell
    image, point = _crop(ctx, x + 0.5, y + 0.5, 3.0, CELL_CELL_PX)
    a, b = point(x - 0.06, y - 0.06), point(x + 1.06, y + 1.06)
    ImageDraw.Draw(image).rectangle([a, b], outline=RED, width=3)
    return _png(image)


def _png(image: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def clef_items(ctx: MapContext) -> list[tuple[str, tuple[int, int], str | None]]:
    """The items to ask about: every edge touching floor or water in either reading, and every disputed cell.

    Returns:
        `(item id, cell, side)` triples; the side is `None` for a cell.
    """
    reading = classifier_reading(ctx)
    claude = claude_cells(ctx)
    items: list[tuple[str, tuple[int, int], str | None]] = []
    for cell, side in edges_touching(reading.panel, claude):
        items.append((f"{cell[0]},{cell[1]}:{side}", cell, side))
    for y in range(reading.panel.height):
        for x in range(reading.panel.width):
            letters = claude.get((x, y), set())
            if letters and (len(letters) > 1 or reading.panel.cells[y][x] not in letters):
                items.append((f"{x},{y}", (x, y), None))
    return items


def run_clef(ctx: MapContext, workers: int, cap: float) -> dict[str, Any]:
    """Ask Clef about every item of a map that has no answer yet, up to a spend cap for this run.

    Args:
        ctx: The map.
        workers: Threads in flight.
        cap: The most dollars this run may spend.

    Returns:
        Counts and the run's spend.
    """
    out = ctx.key.directory / "clef" / "answers.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["item"])
    items = [item for item in clef_items(ctx) if item[0] not in done]
    style = ctx.spec.truth_style
    state, options = STYLE_OPTIONS[style]
    water = ctx.spec.fill_kind == "water"
    cell_options = CELL_OPTIONS if water else CELL_OPTIONS[:2]
    settings = ClefSettings(
        account_id=account_id(),
        token=SecretStr(os.environ.get("OSRFORGE_CLEF_TOKEN", "").strip() or wrangler_token()),
        ledger_path=CLEF_LEDGER,
        run_label=f"answer-keys {ctx.key.id} {date.today().isoformat()}",
    )
    provider = ClefProvider(settings)
    lock = threading.Lock()
    spent = {"usd": 0.0, "answered": 0, "failed": 0, "refused": 0}
    stop = threading.Event()

    def ask(item: tuple[str, tuple[int, int], str | None]) -> None:
        item_id, cell, side = item
        if stop.is_set():
            with lock:
                spent["refused"] += 1
            return
        if side is None:
            request = DecisionRequest(
                tag="cell",
                image=cell_crop(ctx, cell),
                state=CELL_STATE,
                question="What fills the cell inside the red box?",
                options=cell_options,
                model="clef",
            )
        else:
            request = DecisionRequest(
                tag="edge",
                image=edge_crop(ctx, cell, side),
                state=state,
                question="What is drawn on the cell edge inside the red box?",
                options=options,
                model="clef",
            )
        try:
            response = provider.decide(request)
        except Exception as error:
            with lock:
                spent["failed"] += 1
                with (out.parent / "errors.jsonl").open("a") as handle:
                    handle.write(json.dumps({"item": item_id, "error": str(error)[:300]}) + "\n")
            return
        cost = cost_usd("clef", response.input_tokens)
        row = {
            "item": item_id,
            "kind": "cell" if side is None else "edge",
            "probabilities": response.probabilities,
            "choice": response.choice,
            "input_tokens": response.input_tokens,
            "format": response.format,
            "cost_usd": cost,
            "key": request.key(),
        }
        with lock:
            spent["usd"] += cost
            spent["answered"] += 1
            with out.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            if spent["usd"] >= cap:
                stop.set()

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(ask, items))
    return {
        "items": len(items),
        "already": len(done),
        **{k: round(v, 6) if isinstance(v, float) else v for k, v in spent.items()},
    }


def clef_answers(ctx: MapContext) -> dict[str, dict[str, Any]]:
    """Every Clef answer of a map, by item id."""
    out = ctx.key.directory / "clef" / "answers.jsonl"
    answers: dict[str, dict[str, Any]] = {}
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                answers[row["item"]] = row
    return answers
