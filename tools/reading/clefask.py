"""The stage's Clef questions, as the screening asks them: the crops, the options, and a resumable store.

Each request asks one question about one thing, which a red box marks on the crop (step 6 of Reading the map). An
edge crop is 2 by 2 cells magnified to 128 pixels per cell, centred on the edge's midpoint, with the box reaching 0.3
of a cell to each side of the edge and 0.4 of a cell each way along it. A cell crop is 3 by 3 cells at 96 pixels per
cell, with the box on the cell. Crops are resampled through the region's registration, so a sheared or drifting
grid comes out square.

The options and their descriptions depend only on the map style. Two edge option sets are screened:

- `keyset`: the sets the answer-key pass asked (`tools.answer_keys.clefpass.STYLE_OPTIONS`), three to five options
- `seven`: the plan's question, the same seven options in every style (open, printed open line, wall, window, door,
  locked door, secret door), each described in the style's terms

The cell question asks floor, rock, water, or a symbol of the vocabulary that stands on cells.

An [`AnswerStore`][tools.reading.clefask.AnswerStore] is a JSON-lines file of answers on the key's grid, one per
item, which a run appends to and skips on the next run, so a run can stop and start again.
"""

import argparse
import io
import json
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from tools.answer_keys.clefpass import CELL_OPTIONS as KEYSET_CELL_OPTIONS
from tools.answer_keys.clefpass import CELL_STATE as KEYSET_CELL_STATE
from tools.answer_keys.clefpass import STYLE_OPTIONS as KEYSET_OPTIONS

from osrforge.providers.decisions import ClefModel, DecisionProvider, DecisionRequest
from osrforge.tracing.truth import Registration, cell_key, edge_key

__all__ = [
    "CELL_OPTIONS",
    "CELL_QUESTION",
    "EDGE_QUESTION",
    "SEVEN",
    "AnswerStore",
    "Item",
    "cell_crop",
    "cell_request",
    "edge_crop",
    "edge_request",
    "run_items",
]

RED = (228, 20, 20)
EDGE_CELL_PX = 128
CELL_CELL_PX = 96

EDGE_QUESTION = "What is drawn on the cell edge inside the red box?"
CELL_QUESTION = "What is in the grid cell inside the red box?"

_EDGE_STATE = {
    "tsr_blue": (
        "Crop of a printed dungeon map in blue ink on white paper, magnified. Floor is white with thin blue grid "
        "lines; rock is solid blue. A red box marks one edge between two grid cells: the grid line through the middle "
        "of the box."
    ),
    "black_white": (
        "Crop of a printed dungeon map in black ink on white paper, magnified. Floor is white with thin grey or black "
        "grid lines; rooms have heavy black walls; solid black, grey, or hatched areas are rock or water. A red box "
        "marks one edge between two grid cells: the grid line through the middle of the box."
    ),
    "dark_rock": (
        "Crop of a printed cave map, magnified. White passages are floor and dark grey is rock; cave walls are the "
        "freehand border between them, drawn off the grid. Thin straight lines are the grid. A red box marks one edge "
        "between two grid cells: the grid line through the middle of the box."
    ),
    "cave": (
        "Crop of a printed cave map, magnified. White is floor and light grey is rock; cave walls are the freehand "
        "border between them, drawn off the grid. Dark straight lines are the grid; long thin wavy lines are "
        "elevation lines, never walls. A red box marks one edge between two grid cells: the grid line through the "
        "middle of the box."
    ),
}

_WALL = {
    "tsr_blue": "A heavy blue line runs along the edge, or the edge is the border between white floor and solid blue",
    "black_white": "A heavy black line runs along the edge, or the edge is the border of a solid black, grey, or "
    "hatched area",
    "dark_rock": "The border between white floor and dark grey rock runs along the edge",
    "cave": "The border between white floor and grey rock runs along the edge, or a heavy black wall line does",
}
_DOOR = {
    "tsr_blue": "A small white square with a blue outline sitting across a wall line",
    "black_white": "A small rectangle drawn across a wall line",
    "dark_rock": "A small rectangle drawn across a wall",
    "cave": "A small white rectangle drawn across a wall",
}
_WINDOW = {
    "tsr_blue": "A wall line broken by a small white rectangle with a bar along it",
    "black_white": "A window across a wall line: a short bar with a tick at each end, or a small circle",
    "dark_rock": "A window symbol across a wall",
    "cave": "A window symbol across a wall",
}


def _seven(style: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (
        _EDGE_STATE[style],
        (
            ("open", "Floor continues across the edge: nothing lies along it but the thin grid line, or nothing"),
            (
                "printed_open_line",
                "A dashed, dotted, or dash-dot line, a fence, or an archway (a gap in a wall barred by two short "
                "lines) along the edge: a line a party walks through",
            ),
            ("wall", _WALL[style]),
            ("window", _WINDOW[style]),
            ("door", _DOOR[style]),
            ("locked_door", "A door symbol with a dot or a lock mark inside it"),
            ("secret_door", "The letter S on a wall line, or a door symbol with an S in it"),
        ),
    )


SEVEN: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {style: _seven(style) for style in _EDGE_STATE}
"""The plan's seven-option edge question, by style: the state text and the options."""

EDGE_SETS: dict[str, dict[str, tuple[str, tuple[tuple[str, str], ...]]]] = {"keyset": KEYSET_OPTIONS, "seven": SEVEN}
"""The edge option sets the screening compares."""

CELL_STATE = (
    "Crop of a printed dungeon map, magnified. A red box marks one cell of its square grid. Answer for what fills "
    "most of the cell inside the box."
)
CELL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("floor", "Floor a party can stand on: a room, corridor, cave, or yard, with nothing but a label on it"),
    ("rock", "Solid rock, the earth of a hill, a solid wall band, a void, or the blank page outside the map"),
    ("water", "Water: a moat, pool, river, or stream drawn as water"),
    ("stairs", "Stairs or steps: parallel bars across the cell"),
    ("ladder", "A ladder"),
    ("trapdoor", "A trapdoor symbol, often a letter in a circle or a square with a mark"),
    ("pit", "A pit: a box with an X, or a hole"),
    ("pillar", "A pillar or column: a dot or small circle on floor"),
    ("statue", "A statue or another object drawn on floor"),
    ("pool", "A pool or fountain drawn on floor"),
    ("bridge", "A bridge across water or a gap"),
)
"""The cell question's options: floor, rock, water, and the vocabulary's symbols that stand on cells."""

CELL_SETS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "keyset": (KEYSET_CELL_STATE, KEYSET_CELL_OPTIONS),
    "seven": (CELL_STATE, CELL_OPTIONS),
}
"""The cell option sets by edge set name: the answer-key pass's three options, and the stage's."""


@dataclass(frozen=True)
class Item:
    """One thing to ask about: an edge `x,y:side` or a cell `x,y`."""

    kind: str
    name: str
    cell: tuple[int, int]
    side: str | None = None


def _grid_points(registration: Registration, gx: np.ndarray, gy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    corners = np.array(registration.corners, dtype=np.float64)
    rows, columns = corners.shape[0] - 1, corners.shape[1] - 1
    i = np.clip(np.floor(gx).astype(int), 0, columns - 1)
    j = np.clip(np.floor(gy).astype(int), 0, rows - 1)
    u, v = (gx - i)[..., None], (gy - j)[..., None]
    point = (
        (1 - u) * (1 - v) * corners[j, i]
        + u * (1 - v) * corners[j, i + 1]
        + (1 - u) * v * corners[j + 1, i]
        + u * v * corners[j + 1, i + 1]
    )
    return point[..., 0], point[..., 1]


def _resample(rgb: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Bilinear samples of the image, white beyond its edges."""
    height, width = rgb.shape[:2]
    x0, y0 = np.floor(px).astype(int), np.floor(py).astype(int)
    fx, fy = (px - x0)[..., None], (py - y0)[..., None]

    def at(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
        values = rgb[np.clip(y, 0, height - 1), np.clip(x, 0, width - 1)].astype(np.float64)
        return np.where(inside[..., None], values, 255.0)

    out = (
        at(x0, y0) * (1 - fx) * (1 - fy)
        + at(x0 + 1, y0) * fx * (1 - fy)
        + at(x0, y0 + 1) * (1 - fx) * fy
        + at(x0 + 1, y0 + 1) * fx * fy
    )
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _render(
    rgb: np.ndarray,
    registration: Registration,
    centre: tuple[float, float],
    span: float,
    cell_px: int,
    box: tuple[float, float, float, float],
) -> bytes:
    size = round(span * cell_px)
    steps = (np.arange(size) + 0.5) / cell_px - span / 2
    gx = centre[0] + steps[None, :] * np.ones(size)[:, None]
    gy = centre[1] + steps[:, None] * np.ones(size)[None, :]
    px, py = _grid_points(registration, gx, gy)
    image = Image.fromarray(_resample(rgb, px, py))

    def point(x: float, y: float) -> tuple[float, float]:
        return (x - centre[0] + span / 2) * cell_px, (y - centre[1] + span / 2) * cell_px

    ImageDraw.Draw(image).rectangle([point(box[0], box[1]), point(box[2], box[3])], outline=RED, width=3)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def edge_crop(rgb: np.ndarray, registration: Registration, cell: tuple[int, int], side: str) -> bytes:
    """The 2 by 2 cell crop of an edge with the red box, as PNG."""
    x, y = cell
    if side in ("west", "east"):
        line = x if side == "west" else x + 1
        centre, box = (float(line), y + 0.5), (line - 0.3, y + 0.1, line + 0.3, y + 0.9)
    else:
        line = y if side == "north" else y + 1
        centre, box = (x + 0.5, float(line)), (x + 0.1, line - 0.3, x + 0.9, line + 0.3)
    return _render(rgb, registration, centre, 2.0, EDGE_CELL_PX, box)


def cell_crop(rgb: np.ndarray, registration: Registration, cell: tuple[int, int]) -> bytes:
    """The 3 by 3 cell crop of a cell with the red box, as PNG."""
    x, y = cell
    return _render(rgb, registration, (x + 0.5, y + 0.5), 3.0, CELL_CELL_PX, (x, y, x + 1.0, y + 1.0))


def edge_request(style: str, option_set: str, crop: bytes, model: ClefModel) -> DecisionRequest:
    """The edge question for one crop."""
    state, options = EDGE_SETS[option_set][style]
    return DecisionRequest(tag="edge", image=crop, state=state, question=EDGE_QUESTION, options=options, model=model)


def cell_request(option_set: str, crop: bytes, model: ClefModel) -> DecisionRequest:
    """The cell question for one crop."""
    state, options = CELL_SETS[option_set]
    return DecisionRequest(tag="cell", image=crop, state=state, question=CELL_QUESTION, options=options, model=model)


class AnswerStore:
    """A JSON-lines file of answers, one per item, appended under a lock."""

    def __init__(self, path: Path) -> None:
        """Open a store; the file need not exist."""
        self.path = path
        self._lock = threading.Lock()

    def answered(self) -> set[str]:
        """The items that have an answer."""
        if not self.path.exists():
            return set()
        return {json.loads(line)["item"] for line in self.path.read_text().splitlines() if line.strip()}

    def append(self, record: dict[str, object]) -> None:
        """Add one answer."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")


class RequestFor:
    """Builds the request for an item of one map."""

    def __init__(self, rgb: np.ndarray, registration: Registration, style: str, option_set: str, model: ClefModel):
        """Bind a map's image and registration, its style, the option set, and the model."""
        self.rgb = rgb
        self.registration = registration
        self.style = style
        self.option_set = option_set
        self.model: ClefModel = model

    def __call__(self, item: Item) -> DecisionRequest:
        """The item's request."""
        if item.kind == "edge":
            assert item.side is not None
            crop = edge_crop(self.rgb, self.registration, item.cell, item.side)
            return edge_request(self.style, self.option_set, crop, self.model)
        return cell_request(self.option_set, cell_crop(self.rgb, self.registration, item.cell), self.model)


def run_items(
    provider: DecisionProvider,
    store: AnswerStore,
    items: Sequence[Item],
    request_for: RequestFor,
    workers: int = 1,
    stop: threading.Event | None = None,
) -> int:
    """Ask every item that has no answer in the store yet.

    Args:
        provider: The decision provider.
        store: Where answers go.
        items: The items.
        request_for: Builds an item's request.
        workers: Requests in flight at once.
        stop: Set to stop after the requests in flight.

    Returns:
        How many answers were added.
    """
    done = store.answered()
    todo = [item for item in items if item.name not in done]
    added = 0
    added_lock = threading.Lock()
    halt = stop or threading.Event()

    def ask(item: Item) -> None:
        nonlocal added
        if halt.is_set():
            return
        request = request_for(item)
        started = time.monotonic()
        try:
            answer = provider.decide(request)
        except Exception:
            halt.set()
            raise
        store.append(
            {
                "item": item.name,
                "kind": item.kind,
                "probabilities": answer.probabilities,
                "choice": answer.choice,
                "model": answer.model_id,
                "input_tokens": answer.input_tokens,
                "format": answer.format,
                "key": request.key(),
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        with added_lock:
            added += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(ask, item) for item in todo]:
            future.result()
    return added


def edge_items(edges: Iterable[tuple[tuple[int, int], str]]) -> list[Item]:
    """Items for edges."""
    return [Item(kind="edge", name=edge_key(cell, side), cell=cell, side=side) for cell, side in edges]


def cell_items(cells: Iterable[tuple[int, int]]) -> list[Item]:
    """Items for cells."""
    return [Item(kind="cell", name=cell_key(cell), cell=cell) for cell in cells]


def main(argv: list[str] | None = None) -> int:
    """Ask the local or hosted Clef about the edges and cells of the screening windows."""
    from osrforge.providers.clef_local import LocalClefProvider, LocalClefSettings

    from .maps import key_panel, key_region, load_maps, load_rgb
    from .windows import load_windows, window_cells, window_edges

    parser = argparse.ArgumentParser(description="Ask Clef about the screening windows.")
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--windows", type=Path, action="append", required=True)
    parser.add_argument("--set", dest="option_set", choices=sorted(EDGE_SETS), required=True)
    parser.add_argument("--backend", choices=("local", "hosted"), default="local")
    parser.add_argument("--model", choices=("clef", "clef-flash"), default="clef-flash")
    parser.add_argument("--kind", choices=("edge", "cell"), default="edge")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--max-requests", type=int, default=None, help="send at most this many requests in all")
    parser.add_argument(
        "--spend", type=float, default=0.40, help="hosted only: the most dollars this run may add to the Clef ledger"
    )
    args = parser.parse_args(argv)
    if args.backend == "local":
        provider: DecisionProvider = LocalClefProvider(LocalClefSettings())
    else:
        from osrforge.providers.clef import ClefProvider, ClefSettings
        from osrforge.providers.ledger import Ledger

        settings = ClefSettings.from_env()
        spent = Ledger(settings.ledger_path, cost_key="usd").spent()
        settings = settings.model_copy(
            update={
                "run_label": f"reading screening {args.option_set} {args.model}",
                "ledger_stop_usd": min(settings.ledger_stop_usd or 8.0, round(spent + args.spend, 6)),
            }
        )
        provider = ClefProvider(settings)
    budget = args.max_requests
    windows = [window for path in args.windows for window in load_windows(path)]
    for screen in [screen for path in args.map_files for screen in load_maps(path)]:
        if args.only and screen.id not in args.only:
            continue
        mine = [window for window in windows if window.map == screen.id]
        if not mine:
            continue
        panel = key_panel(screen)
        if args.kind == "edge":
            items = edge_items(window_edges(panel, mine))
        else:
            from .ink import INK_STYLES, MapInk, unsettled_cells

            registration = key_region(screen).registration
            assert registration is not None
            ink = MapInk(load_rgb(screen.image), registration, INK_STYLES[screen.style])
            items = cell_items(unsettled_cells(ink, ink.floor_fractions(), window_cells(panel, mine)))
        backend = args.backend if args.backend == "local" else f"hosted-{args.model}"
        store = AnswerStore(args.out / backend / args.option_set / f"{screen.id}.{args.kind}.jsonl")
        registration = key_region(screen).registration
        assert registration is not None
        request_for = RequestFor(
            load_rgb(screen.image),
            registration,
            screen.style,
            args.option_set,
            args.model,  # type: ignore[arg-type]
        )
        if budget is not None:
            done = store.answered()
            items = [item for item in items if item.name not in done][:budget]
        started = time.monotonic()
        added = run_items(provider, store, items, request_for, workers=args.workers)
        if budget is not None:
            budget -= added
            if budget <= 0:
                print(f"{screen.id}: the request budget is spent", flush=True)
                break
        print(
            f"{screen.id}: {added} new answers of {len(items)} items in {time.monotonic() - started:.0f}s", flush=True
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
