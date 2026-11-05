"""Claude's judgments of uncertain items (step 8 of Reading the map), as the screening asks them.

One request carries several items of one page. Its shared part is the page at the size the API reads, after the
system prompt, so the requests about one page share it from the cache. Each item is two crops: the small crop Clef
was asked about (an edge's 2 by 2 cells, a cell's 3 by 3 cells, with the red box), and a wider crop of 7 by 7 cells
around it with the same item boxed. Claude answers each item with one reading: an edge letter for an edge, a cell
letter for a cell. The schema fixes the shape; `check_items` tests that each item is answered once, with a reading
of its own kind.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from osrforge.providers.base import ImagePart, TextPart
from osrforge.providers.reading import Effort, ReadingRequest, RequestKind
from osrforge.tracing.truth import Registration

from .clefask import _render, cell_crop, edge_crop
from .pictures import fit_for_api, png

__all__ = [
    "CELL_VALUES",
    "EDGE_VALUES",
    "ITEMS_SCHEMA",
    "ITEMS_SYSTEM",
    "AskedItem",
    "check_items",
    "items_kind",
    "items_request",
    "wide_crop",
]

EDGE_VALUES = {
    "open": "o",
    "printed_open_line": "p",
    "wall": "w",
    "window": "n",
    "door": "d",
    "locked_door": "l",
    "secret_door": "s",
}
CELL_VALUES = {"floor": ".", "rock": "#", "water": "~"}

STYLE_WORDS = {
    "tsr_blue": "blue ink on white paper: floor is white with a thin blue grid, rock is solid blue",
    "black_white": "black line art on white paper: floor is white with a thin grid, rooms have heavy black walls, "
    "and rock or water is solid black, grey, or hatched",
    "dark_rock": "white passages and caves cut out of a dark grey fill, with freehand walls off the grid",
    "cave": "white caves on pale grey rock, with freehand walls off the grid and contour lines that aren't walls",
}

ITEMS_SYSTEM = (
    "You judge readings of printed tabletop dungeon maps that an automatic reader was unsure of, for a program that "
    "rebuilds each level on a square grid of cells. Answer only through the structured output.\n\n"
    "Each request shows one map page whole, then several numbered items on it. Each item is two crops of the map: a "
    "close crop with the item inside a red box, and a wider crop around it with the same item boxed in red. An edge "
    "item asks what is drawn on one side of a grid cell: the grid line through the middle of its red box, between "
    "the two cells on either side. A cell item asks what one grid cell holds: the square inside its red box.\n\n"
    "Edges. open: floor continues across the edge, with nothing along it but the thin printed grid line. "
    "printed_open_line: a dashed, dotted, or dash-dot line, a fence, or an archway (a gap in a wall barred by two "
    "short lines) along the edge, which a party walks through. wall: a heavy line runs along the edge, or the edge "
    "is the border between floor and rock. window: a wall with the map's window symbol. door: the map's door "
    "symbol. locked_door: a door with the map's locked-door mark. secret_door: the map's secret-door mark, usually "
    "an S on the wall. A thin grid line is never a wall. Lines drawn over the map (contours and elevation lines, "
    "trees, tracks, skylights, stream hatching) aren't walls, and neither is the drawing's own frame where floor "
    "runs off the map. Between floor and water the edge is open unless a wall line runs along the bank. Along a "
    "diagonal or curved wall the edges follow the stair-stepped line that keeps the floor joined edge to edge. The "
    "top end of a stairway opens onto the floor it meets, whatever line is drawn across it, unless a door is "
    "drawn there. A label printed across a line doesn't change the edge.\n\n"
    "Cells. floor: at least half of the cell is floor a party can stand on (a room, corridor, cave, or yard); a "
    "symbol or a label on it, such as a pillar, a statue, a pool, or steps, leaves it floor. rock: solid rock, the "
    "earth of a hill, a void, a shaft, or the blank page outside the map. water: at least half of it is water the map "
    "draws as water. Where two floor cells of a diagonal passage touch only at a corner, the one of the two cells "
    "beside both that has more floor is floor, even under half. A passage drawn at least a sixth of a cell wide "
    "keeps the cells along its course floor.\n\n"
    "Answer every item once, by its number, with one reading of its kind."
)

ITEMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "integer"},
                    "value": {"type": "string", "enum": [*EDGE_VALUES, *CELL_VALUES]},
                },
                "required": ["item", "value"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}

ITEMS_MAX_TOKENS = 32000


def items_kind(effort: Effort) -> RequestKind:
    """The item judgments at one effort."""
    return RequestKind(
        name="reading-items", system=ITEMS_SYSTEM, schema=ITEMS_SCHEMA, effort=effort, max_tokens=ITEMS_MAX_TOKENS
    )


@dataclass(frozen=True)
class AskedItem:
    """One uncertain item: an edge (`side` set) or a cell, with the key's reading."""

    map: str
    cell: tuple[int, int]
    side: str | None
    key: str
    """The key's letter."""

    clef: float | None = None
    """The calibrated probability the selection used: P(passable) for an edge, the ink's floor fraction for a cell."""

    @property
    def kind(self) -> str:
        """`edge` or `cell`."""
        return "cell" if self.side is None else "edge"


def wide_crop(rgb: np.ndarray, registration: Registration, item: AskedItem) -> bytes:
    """A 7 by 7 cell crop around an item with the item boxed in red, at 56 pixels per cell."""
    x, y = item.cell
    if item.side is None:
        centre, box = (x + 0.5, y + 0.5), (x, y, x + 1.0, y + 1.0)
    elif item.side in ("west", "east"):
        line = x if item.side == "west" else x + 1
        centre, box = (float(line), y + 0.5), (line - 0.3, y + 0.1, line + 0.3, y + 0.9)
    else:
        line = y if item.side == "north" else y + 1
        centre, box = (x + 0.5, float(line)), (x + 0.1, line - 0.3, x + 0.9, line + 0.3)
    return _render(rgb, registration, centre, 7.0, 56, box)


def items_request(
    kind: RequestKind,
    page: np.ndarray,
    rgb: np.ndarray,
    registration: Registration,
    style: str,
    items: Sequence[AskedItem],
) -> ReadingRequest:
    """One request about several items of one region of one page.

    Args:
        kind: The kind, at its effort.
        page: The page image, the shared part.
        rgb: The image the region is cut from.
        registration: The region's grid.
        style: The region's style.
        items: The items, numbered from 1 in this order.

    Returns:
        The request.
    """
    shared = ((TextPart(text="The page:"), ImagePart(png=png(fit_for_api(Image.fromarray(page))))),)
    question: list[TextPart | ImagePart] = [TextPart(text=f"The map is drawn in {STYLE_WORDS[style]}.")]
    for number, item in enumerate(items, start=1):
        if item.side is None:
            close = cell_crop(rgb, registration, item.cell)
            what = "cell"
        else:
            close = edge_crop(rgb, registration, item.cell, item.side)
            what = "edge"
        question.append(TextPart(text=f"Item {number}, a {what}:"))
        question.append(ImagePart(png=close))
        question.append(ImagePart(png=wide_crop(rgb, registration, item)))
    question.append(TextPart(text=f"Answer items 1 to {len(items)}."))
    return ReadingRequest(kind=kind, shared=shared, question=tuple(question))


def check_items(answer: dict[str, Any], items: Sequence[AskedItem]) -> list[str]:
    """Each item answered once, with a reading of its own kind."""
    faults: list[str] = []
    numbers = [item["item"] for item in answer["answers"]]
    for number, item in enumerate(items, start=1):
        if numbers.count(number) != 1:
            faults.append(f"item {number} must be answered exactly once, not {numbers.count(number)} times")
            continue
        value = next(entry["value"] for entry in answer["answers"] if entry["item"] == number)
        allowed = EDGE_VALUES if item.kind == "edge" else CELL_VALUES
        if value not in allowed:
            faults.append(f"item {number} is a {item.kind}, so {value!r} isn't a reading of it")
    for number in numbers:
        if not 1 <= number <= len(items):
            faults.append(f"item {number} isn't one of items 1 to {len(items)}")
    return faults


def letters_of(answer: dict[str, Any]) -> dict[int, str]:
    """Each item's reading as a truth letter."""
    out = {}
    for entry in answer["answers"]:
        value = entry["value"]
        out[entry["item"]] = EDGE_VALUES.get(value) or CELL_VALUES[value]
    return out
