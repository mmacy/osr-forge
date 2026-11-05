"""Checks of a settled reading: against a synthetic map's exact answer, and against the settled windows.

On a synthetic map, `score_map` wraps the assembled reading as a trace with one trivial level per panel and
scores it with `osrforge.tracing.score.score_trace` against the exact answer, so the floor, edges, and doors
families measure the reading, and it compares the labels, symbols, and outlines directly. On a Chaotic Caves
map it scores the assembled panel against the settled windows in `tools/answer_keys/settled/` with
`score.score_window`.
"""

import json
from pathlib import Path
from typing import Any, cast

from osrforge.tracing.score import score_trace
from osrforge.tracing.truth import (
    Dungeon,
    Entrance,
    GeometryTruth,
    Level,
    LevelPanel,
    Panel,
    load_geometry_truth,
    rotate_cell,
    rotated_size,
    rotation_for,
)

from .keyspec import MapContext
from .score import load_settled, score_window

__all__ = ["score_map", "trivial_trace"]


def trivial_trace(document: GeometryTruth) -> GeometryTruth:
    """A trace whose every level is its first panel turned onto the grid, with no areas, so the reading families score.

    Args:
        document: A reading-layer document with shell levels.

    Returns:
        The document with each dungeon's first level traced from the first panel that outlines it, or from
        the first panel, and an entrance on its first floor cell.
    """
    dungeons = []
    for dungeon in document.dungeons:
        levels = []
        entrance = None
        for level in dungeon.levels:
            panel = next(
                (
                    p
                    for p in document.panels
                    if any(o.dungeon == dungeon.id and o.level == level.number for o in p.outlines)
                ),
                None,
            )
            if panel is None:
                levels.append(level)
                continue
            north = document.north_in_force(panel.regions[0].region)
            rotation = rotation_for(north)
            size = (panel.width, panel.height)
            width, height = rotated_size(size, rotation)
            cells = [["#"] * width for _ in range(height)]
            for y in range(panel.height):
                for x in range(panel.width):
                    tx, ty = rotate_cell((x, y), size, rotation)
                    cells[ty][tx] = panel.cells[y][x]
            if rotation != 0:
                raise NotImplementedError("the trivial trace turns only panels whose north is the top")
            traced = Level(
                number=level.number,
                verdict="traced",
                panels=(LevelPanel(panel=panel.id, origin=(0, 0), rotation=rotation),),
                cells=tuple("".join(row) for row in cells),
                edges=panel.edges,
            )
            levels.append(traced)
            if entrance is None:
                for y in range(height):
                    for x in range(width):
                        if cells[y][x] == "." and entrance is None:
                            entrance = Entrance(level=level.number, cell=f"{x},{y}")
        dungeons.append(Dungeon(id=dungeon.id, name=dungeon.name, entrance=entrance, levels=tuple(levels)))
    return document.model_copy(update={"dungeons": tuple(dungeons)})


def _compare_reading(panel: Panel, answer: Panel) -> dict[str, Any]:
    marks_mine = {(m.text, m.cell) for m in panel.marks}
    marks_answer = {(m.text, m.cell) for m in answer.marks}
    symbols_mine = {(s.kind, s.cells, s.edge, s.direction) for s in panel.symbols}
    symbols_answer = {(s.kind, s.cells, s.edge, s.direction) for s in answer.symbols}
    return {
        "marks_right": len(marks_mine & marks_answer),
        "marks_answer": len(marks_answer),
        "marks_extra": sorted(marks_mine - marks_answer),
        "marks_missing": sorted(marks_answer - marks_mine),
        "symbols_right": len(symbols_mine & symbols_answer),
        "symbols_answer": len(symbols_answer),
        "symbols_extra": sorted(str(s) for s in symbols_mine - symbols_answer),
        "symbols_missing": sorted(str(s) for s in symbols_answer - symbols_mine),
        "partial_mine": len(panel.partial),
        "partial_answer": len(answer.partial),
        "outlines_mine": [o.model_dump(mode="json") for o in panel.outlines],
        "outlines_answer": [o.model_dump(mode="json") for o in answer.outlines],
    }


def score_map(ctx: MapContext, settled: Path | None) -> dict[str, Any]:
    """Score the assembled reading. Writes `<map>/score.json` and returns it."""
    reading_path = ctx.key.directory / "reading.json"
    document = load_geometry_truth(reading_path)
    panel = document.panels[0]
    result: dict[str, Any] = {}
    if ctx.key.answer is not None:
        answer = load_geometry_truth(ctx.key.answer)
        trace = trivial_trace(document)
        score = score_trace(trace, answer)
        for name, score_set in (("after", score.after), ("after_whole", score.after_whole)):
            tallies = {"all": score_set.edges.all, "straight": score_set.edges.straight, "cave": score_set.edges.cave}
            doors = {"door": score_set.doors.door, "locked": score_set.doors.locked, "secret": score_set.doors.secret}
            result[name] = {
                "floor": score_set.floor.model_dump(mode="json") | {"overlap": score_set.floor.overlap},
                "edges": {k: v.model_dump(mode="json") | {"accuracy": v.accuracy} for k, v in tallies.items()},
                "doors": {k: v.model_dump(mode="json") for k, v in doors.items()},
            }
        answer_panel = next(p for p in answer.panels if p.id == panel.id)
        result["reading"] = _compare_reading(panel, answer_panel)
        result["edge_differences"] = _edge_differences(panel, answer_panel)
        result["cell_differences"] = _cell_differences(panel, answer_panel)
    if settled is not None:
        windows = []
        for path in sorted(settled.glob("*.json")):
            window = load_settled(path)
            if window.map != ctx.key.id:
                continue
            scores = cast(dict[str, dict[str, Any]], score_window(panel, window))
            windows.append(
                {
                    "window": path.stem,
                    **{
                        name: {k: v for k, v in scores[name].items() if k != "differences"}
                        for name in ("all", "without_partial")
                    },
                    "differences": scores["all"]["differences"],
                }
            )
        result["settled_windows"] = windows
    (ctx.key.directory / "score.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


def _edge_differences(panel: Panel, answer: Panel) -> list[str]:
    out = []
    if (panel.width, panel.height) != (answer.width, answer.height):
        return [f"grids differ: {panel.width}x{panel.height} against {answer.width}x{answer.height}"]
    for cell, side in answer.edges.positions():
        a = answer.edges.at(cell, side)
        m = panel.edges.at(cell, side)
        if a != m:
            out.append(f"{cell[0]},{cell[1]}:{side} answer {a} mine {m}")
    return out


def _cell_differences(panel: Panel, answer: Panel) -> list[str]:
    out = []
    if (panel.width, panel.height) != (answer.width, answer.height):
        return []
    for y in range(answer.height):
        for x in range(answer.width):
            if panel.cells[y][x] != answer.cells[y][x]:
                out.append(f"{x},{y} answer {answer.cells[y][x]} mine {panel.cells[y][x]}")
    return out


def summary_line(result: dict[str, Any]) -> str:
    """One line of the headline figures."""
    parts = []
    if "after" in result:
        edges = result["after"]["edges"]["all"]
        floor = result["after"]["floor"]
        parts.append(
            f"floor {floor['both']}/{floor['key_cells']} edges {edges['right']}/{edges['total']} "
            f"walls-open {edges['walls_read_as_open']} open-blocked {edges['open_read_as_blocked']}"
        )
        doors = result["after"]["doors"]
        parts.append(
            f"doors d {doors['door']['matched']}/{doors['door']['key']} "
            f"s {doors['secret']['matched']}/{doors['secret']['key']}"
        )
        reading = result["reading"]
        parts.append(
            f"marks {reading['marks_right']}/{reading['marks_answer']} "
            f"symbols {reading['symbols_right']}/{reading['symbols_answer']}"
        )
    for window in result.get("settled_windows", []):
        parts.append(
            f"{window['window']}: cells {window['all']['cells']} edges {window['all']['edges']} "
            f"w>o {window['all']['walls_read_open']}"
        )
    return "; ".join(parts)
