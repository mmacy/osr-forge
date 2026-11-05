"""How the full run scores a trace: every family of a level alone, and a member's reading resolved with salvage.

[`level_documents`][tools.reading.full_score.level_documents] gives a level's own documents, on which `score_trace`
counts that level alone, and [`resolve_member`][tools.reading.full_score.resolve_member] resolves a member's reading
with the reference resolution script, taking out a level that meets an open case so the rest resolve.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Any

from osrforge.tracing.score import ScoreSet
from osrforge.tracing.truth import Entrance, GeometryTruth, Level, Panel, cell_key, parse_cell

from .full_reading import bare_edges_cleared

__all__ = ["entrance_on_level", "level_documents", "resolve_member", "summary"]


def summary(score: ScoreSet) -> dict[str, Any]:
    """A score set's figures, family by family, as counts."""
    areas = score.areas.areas
    doors = {
        kind: {
            "key": getattr(score.doors, kind).key,
            "trace": getattr(score.doors, kind).trace,
            "matched": getattr(score.doors, kind).matched,
        }
        for kind in ("door", "locked", "secret")
    }
    edges = {
        part: {
            "total": tally.total,
            "right": tally.right,
            "walls_read_as_open": tally.walls_read_as_open,
            "open_read_as_blocked": tally.open_read_as_blocked,
        }
        for part, tally in (("all", score.edges.all), ("straight", score.edges.straight), ("cave", score.edges.cave))
    }
    return {
        "floor": {"key": score.floor.key_cells, "trace": score.floor.trace_cells, "both": score.floor.both},
        "areas": {
            "areas": len(areas),
            "perfect": sum(a.key_cells == a.trace_cells == a.both for a in areas),
            "mean_overlap": round(score.areas.mean_overlap, 4) if score.areas.mean_overlap is not None else None,
            "extra": len(score.areas.extra),
        },
        "keys": {"total": score.keys.total, "right": score.keys.right},
        "joins": {
            "key": score.joins.key_joins,
            "matched": score.joins.matched,
            "missing": len(score.joins.missing),
            "extra": len(score.joins.extra),
        },
        "doors": doors,
        "edges": edges,
        "reachability": {"total": score.reachability.total, "right": score.reachability.right},
        **{
            name: {"total": part.total, "right": part.right, "extra": part.extra}
            for name, part in (
                ("entrance", score.entrance),
                ("transitions", score.transitions),
                ("passages", score.passages),
            )
        },
        "halves": {"total": score.halves.total, "right": score.halves.right},
        "verdicts": {
            "regions": score.verdicts.regions,
            "regions_right": score.verdicts.regions_right,
            "levels": score.verdicts.levels,
            "levels_right": score.verdicts.levels_right,
        },
        "repairs": {"edges": score.repairs.edges, "matching": score.repairs.edges_matching},
    }


def _restricted(panel: Panel, rects: Sequence[tuple[int, int, int, int]]) -> Panel:
    inside = {(x, y) for x0, y0, w, h in rects for x in range(x0, x0 + w) for y in range(y0, y0 + h)}
    cells = tuple(
        "".join(letter if (x, y) in inside else "#" for x, letter in enumerate(row))
        for y, row in enumerate(panel.cells)
    )
    keep = {cell_key(cell) for cell in inside}
    return panel.model_copy(
        update={
            "cells": cells,
            "edges": bare_edges_cleared(cells, panel.edges),
            "partial": tuple(c for c in panel.partial if c in keep),
            "cave": tuple(c for c in panel.cave if c in keep),
            "marks": (),
            "symbols": (),
            "outlines": (),
        }
    )


def _within(level: Level, dungeon_id: str) -> Level:
    """A level without its passages to other levels and its transitions into other dungeons.

    The truth format refuses a passage into a level that fell back. A transition into a fallen-back level of the
    same dungeon is allowed, and the scorer compares it by level number.
    """
    return level.model_copy(
        update={
            "passages": tuple(
                p for p in level.passages if p.to_dungeon in (None, dungeon_id) and p.to_level == level.number
            ),
            "transitions": tuple(t for t in level.transitions if t.to_dungeon in (None, dungeon_id)),
        }
    )


def entrance_on_level(key: GeometryTruth, dungeon_id: str, number: int) -> bool:
    """Whether the key's entrance of a dungeon stands on a level."""
    dungeon = next(d for d in key.dungeons if d.id == dungeon_id)
    return dungeon.entrance is not None and dungeon.entrance.level == number


def _entrance_area(key: GeometryTruth, dungeon_id: str, number: int) -> str | None:
    dungeon = next(d for d in key.dungeons if d.id == dungeon_id)
    level = next(lv for lv in dungeon.levels if lv.number == number)

    def holding(cell: str) -> str | None:
        return next((name for name, area in level.areas.items() if cell in area.cells), None)

    if dungeon.entrance is not None and dungeon.entrance.level == number:
        return holding(dungeon.entrance.cell)
    arrivals = sorted(
        (other.number, item.cell, item.to_cell)
        for other in dungeon.levels
        if other.number != number
        for item in (*other.transitions, *other.passages)
        if item.to_level == number and item.to_dungeon in (None, dungeon_id) and item.to_cell
    )
    for _number, _cell, to_cell in arrivals:
        name = holding(to_cell)
        if name is not None:
            return name
    return next(iter(level.areas), None)


def _stand_in(level: Level, area: str | None) -> Entrance | None:
    """An entrance at the first floor cell of an area of the level, or of its first area, or of the level."""
    floor = [(x, y) for y, row in enumerate(level.cells) for x, letter in enumerate(row) if letter == "."]
    names = [name for name in (area, *level.areas) if name in level.areas]
    for name in names:
        cells = {parse_cell(text) for text in level.areas[name].cells}
        mine = sorted((c for c in floor if c in cells), key=lambda c: (c[1], c[0]))
        if mine:
            return Entrance(level=level.number, cell=cell_key(mine[0]))
    return Entrance(level=level.number, cell=cell_key(floor[0])) if floor else None


def level_documents(
    trace: GeometryTruth, key: GeometryTruth, dungeon_id: str, number: int
) -> tuple[GeometryTruth, GeometryTruth]:
    """A trace and its key with one level traced and every other level fallen back.

    Each document keeps only the panels its level uses, so the reading families count no other level's panel.
    Panels the key's level shares with another traced level of the key are cut, in both documents, to the level's
    outline on that panel in the key, every other cell made rock. A passage to another level and a transition into
    another dungeon are left out, because their levels fall back, so the member's whole score counts them and the
    level's doesn't.

    A dungeon with a traced level needs an entrance on a traced level. A document whose entrance stands on another
    level gets a stand-in on this one, at the first floor cell of the area the key names: the key's area holding its
    entrance when that is on this level, else the key's area where the first transition or passage from another level
    arrives, else the key level's first area. A document without that area uses its own first area. The reachability
    family then walks from the stand-in, and the level's entrance family counts only where the key's entrance stands
    on the level (`entrance_on_level`).
    """
    level = next(lv for d in key.dungeons if d.id == dungeon_id for lv in d.levels if lv.number == number)
    users: Counter[str] = Counter(
        placement.panel
        for d in key.dungeons
        for other in d.levels
        if other.verdict == "traced"
        for placement in other.panels
    )
    cuts: dict[str, list[tuple[int, int, int, int]]] = {}
    for placement in level.panels:
        if users[placement.panel] > 1:
            panel = key.panel(placement.panel)
            cuts[placement.panel] = [
                rect for o in panel.outlines if (o.dungeon, o.level) == (dungeon_id, number) for rect in o.rects
            ]

    area = _entrance_area(key, dungeon_id, number)

    def only(document: GeometryTruth) -> GeometryTruth:
        dungeons = []
        for dungeon in document.dungeons:
            levels = tuple(
                _within(lv, dungeon.id)
                if (dungeon.id, lv.number) == (dungeon_id, number)
                else Level(number=lv.number, verdict="fallback", reason="scored apart")
                for lv in dungeon.levels
            )
            entrance = None
            if dungeon.id == dungeon_id:
                mine = next(lv for lv in levels if lv.number == number)
                entrance = dungeon.entrance
                if mine.verdict == "traced" and (entrance is None or entrance.level != number):
                    entrance = _stand_in(mine, area)
                    if entrance is None:
                        levels = tuple(
                            Level(number=lv.number, verdict="fallback", reason="no floor") if lv is mine else lv
                            for lv in levels
                        )
            dungeons.append(dungeon.model_copy(update={"levels": levels, "entrance": entrance}))
        kept = next(lv for d in document.dungeons if d.id == dungeon_id for lv in d.levels if lv.number == number)
        used = {placement.panel for placement in kept.panels}
        panels = tuple(
            _restricted(panel, cuts[panel.id]) if cuts.get(panel.id) else panel
            for panel in document.panels
            if panel.id in used
        )
        data = document.model_copy(update={"dungeons": tuple(dungeons), "panels": panels}).model_dump()
        return GeometryTruth.model_validate(data)

    return only(trace), only(key)


def _named_level(message: str) -> tuple[str, int] | None:
    """The level an open case names, as the reference script writes it: `('<dungeon>', <number>)`."""
    import re

    found = re.search(r"\('([^']+)', (\d+)\)", message)
    return (found.group(1), int(found.group(2))) if found else None


def _without_outline(document: GeometryTruth, level: tuple[str, int]) -> GeometryTruth:
    """The reading with one level's outlines taken out of every panel, so the level falls back."""
    panels = tuple(
        panel.model_copy(update={"outlines": tuple(o for o in panel.outlines if (o.dungeon, o.level) != level)})
        for panel in document.panels
    )
    return document.model_copy(update={"panels": panels})


def resolve_member(document: GeometryTruth, survey: Any, contents: Any, mapread: Any) -> tuple[GeometryTruth, str]:
    """A member's reading resolved, salvaging what the conventions can resolve when they meet an open case.

    The whole reading is resolved first. When the reference script meets a case the conventions give no answer for,
    and the case names a level, that level's outlines are taken out of the reading, so it falls back, and the reading
    is resolved again, until it resolves or a case names no level or a level already taken out. Then each panel is
    resolved on its own, with only its half pairs, and each level takes its resolution from the first panel whose
    reading resolves it. A level no panel resolves falls back. If the salvaged document doesn't validate, every level
    falls back.

    Returns:
        The trace, and how it was made: `resolved`, the levels taken out with their cases, or the open case and what
        was salvaged.
    """
    from tools.answer_keys.resolve import ConventionGap, reading_of, resolve

    taken: list[str] = []
    while True:
        try:
            trace = resolve(document, survey, contents, mapread)
            return trace, "resolved" if not taken else "resolved with levels fallen back: " + "; ".join(taken)
        except ConventionGap as gap:
            whole = str(gap)
        level = _named_level(whole)
        if level is None or any(entry.startswith(f"{level[0]}/{level[1]} ") for entry in taken):
            break
        taken.append(f"{level[0]}/{level[1]} ({whole})")
        document = _without_outline(document, level)
    whole = "; ".join([*taken, whole])
    shells = reading_of(document)
    levels: dict[tuple[str, int], Level] = {}
    entrances: dict[str, Any] = {}
    gaps = []
    for panel in document.panels:
        ids = {part.region for part in panel.regions}
        pairs = tuple(pair for pair in document.half_pairs if set(pair.regions) <= ids)
        solo = document.model_copy(update={"panels": (panel,), "half_pairs": pairs})
        try:
            trace = resolve(GeometryTruth.model_validate(solo.model_dump()), survey, contents, mapread)
        except (ConventionGap, ValueError) as gap:
            gaps.append(f"{panel.id}: {gap}")
            continue
        for dungeon in trace.dungeons:
            for level in dungeon.levels:
                uses = {placement.panel for placement in level.panels}
                if level.verdict == "traced" and panel.id in uses and (dungeon.id, level.number) not in levels:
                    levels[(dungeon.id, level.number)] = level
                    if dungeon.entrance is not None and dungeon.entrance.level == level.number:
                        entrances.setdefault(dungeon.id, dungeon.entrance)
    dungeons = tuple(
        dungeon.model_copy(
            update={
                "levels": tuple(levels.get((dungeon.id, level.number), level) for level in dungeon.levels),
                "entrance": entrances.get(dungeon.id),
            }
        )
        for dungeon in shells.dungeons
    )
    salvaged = f"open case: {whole}; salvaged {len(levels)} levels panel by panel" + (
        f"; open cases: {'; '.join(gaps)}" if gaps else ""
    )
    try:
        merged = GeometryTruth.model_validate(shells.model_copy(update={"dungeons": dungeons}).model_dump())
        return merged, salvaged
    except ValueError as error:
        return shells, f"open case: {whole}; the salvaged levels don't validate ({error}), so every level falls back"
