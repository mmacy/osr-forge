"""Milestone 1's pictures: three traces drawn over their maps, each beside the same trace with its faults marked.

Milestone 1 looks at three levels, which the settings name (`milestone`): a level of the commercial corpus, the
manor's ground floor, and one lair of Chaotic Caves page 38. Each picture puts two drawings side by side, cut to the
level's part of the region: on the left, the trace as read, drawn by
[`render_overlay`][osrforge.tracing.overlay.render_overlay]; on the right, the same drawing with the faults the
structural checks find (`full_checks`) marked over it. The repair isn't built, so the right-hand drawing stands in for
the repaired trace the plan puts beside the trace before repair: it shows what a repair would act on.

- unreachable areas: their cells filled magenta
- cut-off floor: its cells filled cyan
- doors without floor: a thick black line along the edge
- bypasses: a thick violet line along the locked door
- disputes: a dashed black line between the two areas' first cells

The pictures are written outside the repository, because one of the three levels is from a commercial module.
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from osrforge.tracing.overlay import render_overlay
from osrforge.tracing.truth import GeometryTruth, Level, Panel, parse_cell, parse_edge, rotate_cell

from .full_checks import LevelChecks
from .full_members import Member

__all__ = ["fault_picture", "milestone_pictures"]

UNREACHABLE = (220, 0, 200, 110)
CUT_OFF = (0, 200, 220, 110)
NO_FLOOR = (0, 0, 0, 255)
BYPASS = (130, 0, 200, 255)


def _level_cells(trace: GeometryTruth, level: Level, panel: Panel) -> dict[tuple[int, int], tuple[int, int]]:
    """Each level cell on the panel, mapped to its panel cell."""
    out: dict[tuple[int, int], tuple[int, int]] = {}
    for placement in level.panels:
        if placement.panel != panel.id:
            continue
        size = (panel.width, panel.height)
        for y in range(panel.height):
            for x in range(panel.width):
                turned = rotate_cell((x, y), size, placement.rotation)
                out[(placement.origin[0] + turned[0], placement.origin[1] + turned[1])] = (x, y)
    return out


def fault_picture(
    image: Image.Image, trace: GeometryTruth, region_id: str, dungeon_id: str, number: int, checks: LevelChecks
) -> Image.Image:
    """One level's trace beside the same trace with its faults marked, cut to the level's part of the region.

    Args:
        image: The region's image.
        trace: The trace.
        region_id: The region.
        dungeon_id: The level's dungeon.
        number: The level's number.
        checks: The level's checks.

    Returns:
        The two drawings side by side, with a line of text above that says what is marked.
    """
    overlay = render_overlay(image, trace, region_id).convert("RGBA")
    region = trace.region(region_id)
    assert region.registration is not None
    corners = np.array(region.registration.corners, dtype=np.float64)
    panel = next(p for p in trace.panels if any(part.region == region_id for part in p.regions))
    origin = next(part.origin for part in panel.regions if part.region == region_id)
    level = next(lv for d in trace.dungeons if d.id == dungeon_id for lv in d.levels if lv.number == number)
    to_panel = _level_cells(trace, level, panel) if panel.scale_feet == 10 else {}

    def region_cell(level_cell: tuple[int, int]) -> tuple[int, int] | None:
        cell = to_panel.get(level_cell)
        if cell is None:
            return None
        x, y = cell[0] - origin[0], cell[1] - origin[1]
        return (x, y) if 0 <= x < region.columns and 0 <= y < region.rows else None

    def quad(cell: tuple[int, int]) -> list[tuple[float, float]]:
        x, y = cell
        return [tuple(corners[y][x]), tuple(corners[y][x + 1]), tuple(corners[y + 1][x + 1]), tuple(corners[y + 1][x])]  # type: ignore[misc]

    def side_line(cell: tuple[int, int], side: str) -> list[tuple[float, float]]:
        x, y = cell
        ends = {
            "north": ((x, y), (x + 1, y)),
            "south": ((x, y + 1), (x + 1, y + 1)),
            "west": ((x, y), (x, y + 1)),
            "east": ((x + 1, y), (x + 1, y + 1)),
        }[side]
        return [tuple(corners[j][i]) for i, j in ends]  # type: ignore[misc]

    layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for name in checks.unreachable:
        for text in level.areas[name].cells:
            cell = region_cell(parse_cell(text))
            if cell is not None:
                draw.polygon(quad(cell), fill=UNREACHABLE)
    for piece in checks.cut_off:
        for text in piece:
            cell = region_cell(parse_cell(text))
            if cell is not None:
                draw.polygon(quad(cell), fill=CUT_OFF)
    pitch = float(np.mean(np.diff(corners[..., 0], axis=1)))
    for colour, names in ((NO_FLOOR, checks.doors_without_floor), (BYPASS, checks.bypasses)):
        for text in names:
            level_cell, side = parse_edge(text)
            cell = region_cell(level_cell)
            if cell is not None and panel.scale_feet == 10:
                draw.line(side_line(cell, side), fill=colour, width=max(4, round(pitch / 6)))
    for dispute in checks.disputes:
        ends = []
        for name in dispute.split("-", 1):
            if name in level.areas:
                first = min((parse_cell(t) for t in level.areas[name].cells), key=lambda c: (c[1], c[0]))
                cell = region_cell(first)
                if cell is not None:
                    ends.append(tuple(np.mean(quad(cell), axis=0)))
        if len(ends) == 2:
            _dashed(draw, ends[0], ends[1], max(3, round(pitch / 10)))  # type: ignore[arg-type]
    marked = Image.alpha_composite(overlay, layer)
    own = [(x, y) for y, row in enumerate(level.cells) for x, letter in enumerate(row) if letter in ".~"]
    box = _bounds(corners, [region_cell(c) for c in own], pitch, overlay.size)
    left, right = overlay.crop(box), marked.crop(box)
    width, height = left.size
    banner = 40
    out = Image.new("RGB", (width * 2 + 20, height + banner), "white")
    out.paste(left.convert("RGB"), (0, banner))
    out.paste(right.convert("RGB"), (width + 20, banner))
    text = (
        f"{dungeon_id}/{number}: the trace as read | faults: {len(checks.unreachable)} unreachable areas (magenta), "
        f"{len(checks.cut_off)} cut-off floor (cyan), {len(checks.doors_without_floor)} doors without floor (black), "
        f"{len(checks.bypasses)} bypasses (violet), {len(checks.disputes)} disputes (dashed)"
    )
    ImageDraw.Draw(out).text((8, 10), text, fill=(0, 0, 0), font=ImageFont.load_default(size=18))
    return out


def _dashed(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], width: int) -> None:
    length = float(np.hypot(end[0] - start[0], end[1] - start[1]))
    steps = max(1, int(length // 12))
    for index in range(0, steps, 2):
        a, b = index / steps, min(1.0, (index + 1) / steps)
        draw.line(
            [
                (start[0] + (end[0] - start[0]) * a, start[1] + (end[1] - start[1]) * a),
                (start[0] + (end[0] - start[0]) * b, start[1] + (end[1] - start[1]) * b),
            ],
            fill=(0, 0, 0, 255),
            width=width,
        )


def _bounds(
    corners: np.ndarray, cells: Sequence[tuple[int, int] | None], pitch: float, size: tuple[int, int]
) -> tuple[int, int, int, int]:
    """The pixel box of the level's floor and water on the region, with a cell's margin, or the region's whole grid."""
    cells = [cell for cell in cells if cell is not None]
    if cells:
        points = np.array(
            [corners[y + dy][x + dx] for x, y in cells for dx in (0, 1) for dy in (0, 1)], dtype=np.float64
        )
    else:
        points = corners.reshape(-1, 2)
    x0, y0 = points.min(axis=0) - pitch
    x1, y1 = points.max(axis=0) + pitch
    return (max(0, int(x0)), max(0, int(y0)), min(size[0], int(x1) + 1), min(size[1], int(y1) + 1))


def milestone_pictures(
    result: object, members: Sequence[Member], out: Path, levels: Sequence[tuple[str, str, str, int]]
) -> list[Path]:
    """Draw milestone 1's pictures from a run's result.

    Args:
        result: The run's result (`full_run.RunResult`), with its traces and checks.
        members: The run's members, for the region images.
        out: The directory to write them in, outside the repository.
        levels: Each level to draw: its member, region, dungeon, and level.

    Returns:
        The pictures written. A level the run didn't trace is left out.
    """
    out.mkdir(parents=True, exist_ok=True)
    written = []
    images = {(m.id, run_map.screen.id): run_map.screen.image for m in members for run_map in m.maps}
    for member_id, region_id, dungeon_id, number in levels:
        trace = result.traces.get(member_id)  # type: ignore[attr-defined]
        checks = next(
            (c for c in result.checks.get(member_id, []) if (c.dungeon, c.level) == (dungeon_id, number)),  # type: ignore[attr-defined]
            None,
        )
        if trace is None or checks is None or (member_id, region_id) not in images:
            continue
        if not any(any(part.region == region_id for part in p.regions) for p in trace.panels):
            continue
        with Image.open(images[(member_id, region_id)]) as image:
            picture = fault_picture(image.convert("RGB"), trace, region_id, dungeon_id, number, checks)
        path = out / f"milestone-{dungeon_id}-{number}.png"
        picture.save(path)
        written.append(path)
    return written
