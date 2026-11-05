"""A map's registered grid inside the grid its readings were made on.

The readings, the settler's calls, and every store of a map are on its working grid, the grid fitted over the map
spec's `rect`. The key is on the registered grid: the region's rectangle is the framed drawing (`RegionFit` in
`osrforge.tracing.registration`), so a title, a legend, or a compass outside the frame is no part of it. A spec's
`frame` gives the registered grid as `(x, y, columns, rows)` in working cells. The assembly writes the key's panel
on the registered grid (`crop_panel`), samples show registered cells, and what reads the key back to make calls
turns registered cells into working ones (`to_work`).
"""

import re
from typing import Any

from osrforge.tracing.truth import Mark, Outline, Panel, Region, Symbol

from .keyspec import MapContext

__all__ = [
    "crop_panel",
    "crop_region",
    "frame_of",
    "shift_text",
    "to_registered",
    "to_registered_text",
    "to_work",
    "to_work_text",
]

_PAIR = re.compile(r"(?<![\d.])(-?\d+),(-?\d+)(?!\d|\.\d)")


def frame_of(ctx: MapContext) -> tuple[int, int, int, int] | None:
    """The map's registered grid in working cells, or `None` when the two grids are one."""
    frame = getattr(ctx.spec, "frame", None)
    return tuple(frame) if frame else None  # type: ignore[return-value]


def _offset(ctx: MapContext) -> tuple[int, int]:
    frame = frame_of(ctx)
    return (frame[0], frame[1]) if frame else (0, 0)


def to_work(ctx: MapContext, cell: tuple[int, int]) -> tuple[int, int]:
    """A registered cell's working cell."""
    dx, dy = _offset(ctx)
    return (cell[0] + dx, cell[1] + dy)


def to_registered(ctx: MapContext, cell: tuple[int, int]) -> tuple[int, int]:
    """A working cell's registered cell."""
    dx, dy = _offset(ctx)
    return (cell[0] - dx, cell[1] - dy)


def shift_text(text: str, dx: int, dy: int) -> str:
    """Every `x,y` cell address in a call name or value moved by `(dx, dy)`. A group call's rectangle
    (`r:x,y,width,height|level`) moves its corner only."""
    if text.startswith("r:"):
        head, _, rest = text[2:].partition("|")
        x, y, width, height = (int(part) for part in head.split(","))
        return f"r:{x + dx},{y + dy},{width},{height}|{rest}"
    return _PAIR.sub(lambda match: f"{int(match[1]) + dx},{int(match[2]) + dy}", text)


def to_work_text(ctx: MapContext, text: str) -> str:
    """A registered call name or value in working cells."""
    dx, dy = _offset(ctx)
    return shift_text(text, dx, dy) if (dx or dy) else text


def to_registered_text(ctx: MapContext, text: str) -> str:
    """A working call name, value, or reason in registered cells. In free text, `x,y` pairs move, and so do the
    rows a reason names as `row 14`, `rows 14 to 15`, or `y 14.3`."""
    dx, dy = _offset(ctx)
    if not (dx or dy):
        return text
    moved = shift_text(text, -dx, -dy)
    if dy:
        moved = re.sub(r"\b(rows? )(\d+)", lambda m: f"{m[1]}{int(m[2]) - dy}", moved)
        moved = re.sub(r"\b(rows \d+ to )(\d+)", lambda m: f"{m[1]}{int(m[2]) - dy}", moved)
        moved = re.sub(r"\by (\d+(?:\.\d+)?)", lambda m: f"y {_less(m[1], dy)}", moved)
    if dx:
        moved = re.sub(r"\b(columns? )(\d+)", lambda m: f"{m[1]}{int(m[2]) - dx}", moved)
        moved = re.sub(r"\bx (\d+(?:\.\d+)?)", lambda m: f"x {_less(m[1], dx)}", moved)
    return moved


def _less(number: str, by: int) -> str:
    return str(int(number) - by) if "." not in number else f"{float(number) - by:.{len(number.split('.')[1])}f}"


def crop_panel(panel: Panel, frame: tuple[int, int, int, int]) -> Panel:
    """A working panel on its registered grid: the cells, edges, and lists inside the frame, moved to start at 0,0."""
    x0, y0, columns, rows = frame

    def inside(text: str) -> bool:
        x, y = (int(part) for part in text.split(":")[0].split(","))
        return x0 <= x < x0 + columns and y0 <= y < y0 + rows

    def move(text: str) -> str:
        return shift_text(text, -x0, -y0)

    cells = tuple(row[x0 : x0 + columns] for row in panel.cells[y0 : y0 + rows])
    vertical = tuple(row[x0 : x0 + columns + 1] for row in panel.edges.vertical[y0 : y0 + rows])
    horizontal = tuple(row[x0 : x0 + columns] for row in panel.edges.horizontal[y0 : y0 + rows + 1])
    marks = []
    for mark in panel.marks:
        if not inside(mark.cell):
            continue
        marks.append(
            Mark(
                text=mark.text,
                cell=move(mark.cell),
                edge=move(mark.edge) if mark.edge else None,
                leader_to=move(mark.leader_to) if mark.leader_to and inside(mark.leader_to) else None,
            )
        )
    symbols = []
    for symbol in panel.symbols:
        kept = [cell for cell in symbol.cells if inside(cell)]
        if symbol.edge is not None:
            if inside(symbol.edge):
                symbols.append(symbol.model_copy(update={"edge": move(symbol.edge)}))
            continue
        if kept:
            symbols.append(symbol.model_copy(update={"cells": tuple(move(cell) for cell in kept)}))
    outlines = []
    for outline in panel.outlines:
        outlines.append(
            Outline(
                dungeon=outline.dungeon,
                level=outline.level,
                rects=_crop_rects(outline.rects, frame),
                open_ground=_crop_rects(outline.open_ground, frame),
                seeds=tuple(move(seed) for seed in outline.seeds if inside(seed)),
            )
        )
    return panel.model_copy(
        update={
            "cells": cells,
            "edges": panel.edges.model_copy(update={"vertical": vertical, "horizontal": horizontal}),
            "partial": tuple(move(cell) for cell in panel.partial if inside(cell)),
            "cave": tuple(move(cell) for cell in panel.cave if inside(cell)),
            "marks": tuple(marks),
            "symbols": tuple(Symbol.model_validate(symbol.model_dump()) for symbol in symbols),
            "outlines": tuple(outlines),
        }
    )


def _crop_rects(rects: Any, frame: tuple[int, int, int, int]) -> tuple[tuple[int, int, int, int], ...]:
    x0, y0, columns, rows = frame
    out = []
    for x, y, width, height in rects:
        left, top = max(x, x0), max(y, y0)
        right, bottom = min(x + width, x0 + columns), min(y + height, y0 + rows)
        if right > left and bottom > top:
            out.append((left - x0, top - y0, right - left, bottom - top))
    return tuple(out)


def crop_region(region: Region, frame: tuple[int, int, int, int]) -> Region:
    """A working region on its registered grid: its size and its registration's corners inside the frame."""
    x0, y0, columns, rows = frame
    update: dict[str, Any] = {"columns": columns, "rows": rows}
    if region.registration is not None:
        corners = tuple(tuple(row[x0 : x0 + columns + 1]) for row in region.registration.corners[y0 : y0 + rows + 1])
        update["registration"] = region.registration.model_copy(update={"corners": corners})
    return region.model_copy(update=update)
