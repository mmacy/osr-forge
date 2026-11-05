"""The reading measurement's full run (step 8 of the plan's measurements): every map read, resolved, scored, checked.

It covers every map with a first answer key and every synthetic map (`full_members`), reads each as the stage reads
it with the screening's settings (`full_reading`, `full_settings`), resolves each member's reading into a trace with
the reference resolution script (`tools/answer_keys/resolve.py`), scores the trace against its key with `score_trace`,
and runs the structural checks (`full_checks`). The report gives the following:

- every scorer family per level, over every cell (`after`) and without the partly-floor cells (`after_whole`), with
  cave edges apart from straight ones, and the same per member and pooled by style. A level is scored on documents
  where every other level fell back, so the resolution families count that level alone. Where the key puts several
  levels on one panel (page 38's lairs), both panels are cut to the level's outline in the key, every other cell made
  rock, so the floor, edges, and doors families count that level's cells too. Every calibration a map is read with
  was fitted without that map, so every score is a held-out score.
- the share of levels that need no repair, by the checks, beside the same checks on the answer key
- Claude requests and Claude and Clef dollars per level: a whole-page read's are shared evenly by the levels its page
  lists, and a map's item requests and Clef questions by the levels whose read outline holds their items, in
  proportion to the items. Stored answers count at what they cost when they were asked.

Run from the osr-forge checkout. `R` is the results directory and `P` the commercial specs:

    uv run python -m tools.reading.full_run --settings $R/full-run/settings.json --out $R/full-run \\
        --maps tools/reading/maps.json --maps $P/<module>-maps.json --pages tools/reading/pages.json \\
        --pages $P/<module>-pages.json --keys tools/answer_keys/keys.json --keys <module keys file> \\
        --halves $P/halves.json --synthetic tests/assets/synthetic-maps --screening $R \\
        [--dry-run | --claude none|fake|live --clef none|fake|local|hosted] [--pictures]

`--dry-run` sends nothing: it reads with every stored answer, lets the answer key's whole-page answer stand in where
none is stored, and estimates the requests and dollars each map still needs. `--claude live` needs
`OSRFORGE_CLAUDE_LEDGER`, and is held to the settings' cap on the ledger's `reading-` entries. `--claude-for <member>`
keeps Claude to the named members' maps and pages (and the real pairs), so a run within the cap can take the members in
an order. `--clef hosted` needs `OSRFORGE_CLEF_ACCOUNT_ID`, `OSRFORGE_CLEF_LEDGER`, and a wrangler login, and is held
to the settings' hosted spend.
"""

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from osrforge.tracing.score import ScoreSet, score_trace
from osrforge.tracing.truth import Entrance, GeometryTruth, Level, Panel, cell_key, parse_cell

from .claude_run import RequestCap
from .full_checks import LevelChecks, check_document
from .full_members import HalvesSpec, Member, load_halves, real_members, synthetic_members
from .full_reading import (
    MapReading,
    PageRead,
    Pick,
    bare_edges_cleared,
    calibrate,
    choose_items,
    clef_edges,
    map_letters,
    measure_ink,
    member_reading,
    page_legends,
    pick_offsets,
    read_items,
    read_marks,
    read_page,
    start_map,
    unanswered_edges,
)
from .full_seed import seed_claude, seed_clef_exported, seed_clef_stores
from .full_settings import FullRunSettings, load_settings
from .full_store import ClaudeStore, ClefStore, Sources

__all__ = ["RunResult", "level_documents", "main", "run", "summary"]

HOSTED_FLASH_USD = 0.013398 / 462
"""Hosted Clef-flash's dollars a request, as the screening measured them on 462 requests."""


@dataclass
class RunResult:
    """What a run read and scored."""

    report: dict[str, Any]
    traces: dict[str, GeometryTruth] = field(default_factory=dict)
    readings: dict[str, MapReading] = field(default_factory=dict)
    checks: dict[str, list[LevelChecks]] = field(default_factory=dict)


# Scores.


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


# The run.


def _style_of(key: GeometryTruth, level: Level) -> str:
    for placement in level.panels:
        panel = key.panel(placement.panel)
        return key.region(panel.regions[0].region).style
    return "none"


def _level_of_cell(reading: MapReading, cell: tuple[int, int]) -> tuple[str, int] | None:
    for outline in reading.outlines:
        if any(x <= cell[0] < x + w and y <= cell[1] < y + h for x, y, w, h in outline.rects):
            return outline.dungeon, outline.level
    return None


def _shares(reading: MapReading) -> dict[tuple[str, int] | None, float]:
    """How a map's item and Clef costs split among the levels whose read outline holds its items."""
    counts: Counter[tuple[str, int] | None] = Counter(_level_of_cell(reading, item.cell) for item in reading.items)
    if not counts:
        counts = Counter(_level_of_cell(reading, edge[0]) for edge in reading.clef)
    total = sum(counts.values())
    return {level: count / total for level, count in counts.items()} if total else {}


def run(
    members: Sequence[Member],
    halves: Sequence[HalvesSpec],
    settings: FullRunSettings,
    sources: Sources,
    stand_in: bool,
    workers: int = 1,
) -> RunResult:
    """Read, resolve, score, and check every member.

    Args:
        members: The members.
        halves: The real pairs of halves, which decide the halves rule.
        settings: The run's settings.
        sources: Where answers come from.
        stand_in: Let the answer key's whole-page answer stand in for a read the store lacks (a dry run).
        workers: How many Clef requests may be in flight at once.

    Returns:
        The report and what it was made from.
    """
    from tools.answer_keys.resolve import load_caches

    result = RunResult(report={})
    page_reads: dict[str, PageRead] = {}
    for member in members:
        key = member.document()
        reads = [read_page(page, settings, sources, key if stand_in else None) for page in member.pages]
        page_reads.update({read.page.id: read for read in reads})
        legends = page_legends(reads, key)
        for run_map in member.maps:
            read = page_reads[run_map.page]
            reading = start_map(run_map, read, legends.get(run_map.page))
            result.readings[run_map.uid] = reading
            if reading.style is not None:
                measure_ink(reading)
                clef_edges(reading, sources, workers)
    training = calibrate(list(result.readings.values()))
    pages = {page.id: page for member in members for page in member.pages}
    unanswered: Counter[str] = Counter()
    for uid, reading in result.readings.items():
        if reading.style is None:
            continue
        choose_items(reading, settings.epsilon)
        unanswered[f"{uid} item requests"] += read_items(reading, pages[reading.run_map.page], settings, sources)
        map_letters(reading, settings.thresholds[reading.style])
        unanswered[f"{uid} marks"] += read_marks(reading, pages[reading.run_map.page], sources, workers)
        unanswered[f"{uid} edges between floor"] += unanswered_edges(reading)
    picks = pick_offsets([*halves, *(spec for member in members for spec in member.halves)], settings, sources)
    halves_apart = any(pick.wrong for pick in picks if pick.spec.real)
    levels_report: list[dict[str, Any]] = []
    members_report: dict[str, Any] = {}
    for member in members:
        key = member.document()
        survey, contents, mapread = load_caches(member.stages)
        readings = {r.run_map.screen.id: r for r in result.readings.values() if r.run_map.member == member.id}
        mine = [pick for pick in picks if pick.spec.member == member.id]
        document = member_reading(member, readings, mine, halves_apart)
        trace, resolution = resolve_member(document, survey, contents, mapread)
        result.traces[member.id] = trace
        scores = score_trace(trace, key)
        trace_checks = check_document(trace, contents, mapread)
        key_checks = {(c.dungeon, c.level): c for c in check_document(key, contents, mapread)}
        result.checks[member.id] = trace_checks
        members_report[member.id] = {
            "resolution": resolution,
            "after": summary(scores.after),
            "after_whole": summary(scores.after_whole),
        }
        by_level = {(c.dungeon, c.level): c for c in trace_checks}
        for dungeon in key.dungeons:
            for level in dungeon.levels:
                if level.verdict != "traced":
                    continue
                one_trace, one_key = level_documents(trace, key, dungeon.id, level.number)
                level_score = score_trace(one_trace, one_key)
                after, after_whole = summary(level_score.after), summary(level_score.after_whole)
                if not entrance_on_level(key, dungeon.id, level.number):
                    after["entrance"] = after_whole["entrance"] = None
                checks = by_level.get((dungeon.id, level.number))
                levels_report.append(
                    {
                        "member": member.id,
                        "synthetic": member.synthetic,
                        "dungeon": dungeon.id,
                        "level": level.number,
                        "style": _style_of(key, level),
                        "after": after,
                        "after_whole": after_whole,
                        "checks": checks.to_json() if checks else {"traced": False},
                        "needs_no_repair": bool(checks and checks.needs_no_repair),
                        "key_checks": key_checks[(dungeon.id, level.number)].to_json(),
                        "costs": _level_costs(member, dungeon.id, level.number, result.readings, sources, pages),
                    }
                )
    result.report = {
        "settings": {
            "whole_page": vars(settings.whole_page),
            "items": vars(settings.items) | {"per_request": settings.items_per_request},
            "offset_pick": vars(settings.offset_pick),
            "thresholds": settings.thresholds,
            "epsilon": settings.epsilon,
            "clef": settings.clef_backend,
        },
        "calibration_training_edges": training,
        "halves": [
            {
                "pair": pick.spec.id,
                "real": pick.spec.real,
                "choice": pick.choice,
                "truth": pick.truth,
                "wrong": pick.wrong,
                "same_pictures_as": [other.spec.id for other in picks if other is not pick and _same(other, pick)],
            }
            for pick in picks
        ],
        "halves_apart": halves_apart,
        "stand_in_pages": sorted(page for page, read in page_reads.items() if read.stand_in),
        "unread_pages": sorted(page for page, read in page_reads.items() if read.answer is None),
        "unanswered": {name: count for name, count in sorted(unanswered.items()) if count},
        "members": members_report,
        "levels": levels_report,
        "by_style": _by_style(levels_report),
        "requests": _requests(sources),
    }
    return result


def resolve_member(document: GeometryTruth, survey: Any, contents: Any, mapread: Any) -> tuple[GeometryTruth, str]:
    """A member's reading resolved, salvaging what the conventions can resolve when they meet an open case.

    The whole reading is resolved first. When the reference script meets a case the conventions give no answer for,
    each panel is resolved on its own, with only its half pairs, and each level takes its resolution from the first
    panel whose reading resolves it. A level no panel resolves falls back. If the salvaged document doesn't validate,
    every level falls back.

    Returns:
        The trace, and how it was made: `resolved`, or the open case and what was salvaged.
    """
    from tools.answer_keys.resolve import ConventionGap, reading_of, resolve

    try:
        return resolve(document, survey, contents, mapread), "resolved"
    except ConventionGap as gap:
        whole = str(gap)
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


def _same(one: Pick, other: Pick) -> bool:
    """Whether two pairs' halves are the same image files byte for byte."""
    import hashlib

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return (digest(one.spec.first), digest(one.spec.second)) == (digest(other.spec.first), digest(other.spec.second))


def _level_costs(
    member: Member,
    dungeon_id: str,
    number: int,
    readings: dict[str, MapReading],
    sources: Sources,
    pages: dict[str, Any],
) -> dict[str, Any]:
    """One level's share of the requests and dollars."""
    from .screen_wholemap import levels_on

    tally = sources.tally
    claude_requests = 0.0
    claude_usd = 0.0
    clef_requests = 0.0
    clef_usd = 0.0
    for page in member.pages:
        listed = [(d, n) for d, n, _keys in levels_on(page)]
        if (dungeon_id, number) not in listed:
            continue
        for (owner, kind, how), count in tally.counts.items():
            if owner == page.id and how != "missing":
                claude_requests += count
                claude_usd += tally.dollars[(owner, kind, how)] / len(listed)
    for reading in readings.values():
        if reading.run_map.member != member.id:
            continue
        share = _shares(reading).get((dungeon_id, number), 0.0)
        if not share:
            continue
        for (owner, kind, how), count in tally.counts.items():
            if owner != reading.run_map.uid or how == "missing":
                continue
            if kind.startswith("clef"):
                clef_requests += count * share
                clef_usd += (HOSTED_FLASH_USD * count if how == "hosted" else 0.0) * share
            else:
                claude_requests += count * share
                claude_usd += tally.dollars[(owner, kind, how)] * share
    return {
        "claude_requests": round(claude_requests, 2),
        "claude_usd": round(claude_usd, 4),
        "clef_requests": round(clef_requests, 1),
        "clef_usd": round(clef_usd, 4),
    }


def _by_style(levels: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for style in sorted({level["style"] for level in levels}):
        for synthetic in (False, True):
            mine = [level for level in levels if level["style"] == style and level["synthetic"] == synthetic]
            if not mine:
                continue
            edges: dict[str, Counter[str]] = defaultdict(Counter)
            for level in mine:
                for part, tally in level["after"]["edges"].items():
                    edges[part].update(tally)
            out[f"{style} {'synthetic' if synthetic else 'real'}"] = {
                "levels": len(mine),
                "needs_no_repair": sum(level["needs_no_repair"] for level in mine),
                "key_needs_no_repair": sum(level["key_checks"]["needs_no_repair"] for level in mine),
                "edges": {part: dict(tally) for part, tally in edges.items()},
                "floor": dict(sum((Counter(level["after"]["floor"]) for level in mine), Counter())),
                "keys": dict(sum((Counter(level["after"]["keys"]) for level in mine), Counter())),
                "areas_perfect": sum(level["after"]["areas"]["perfect"] for level in mine),
                "areas": sum(level["after"]["areas"]["areas"] for level in mine),
                "claude_requests": round(sum(level["costs"]["claude_requests"] for level in mine), 2),
                "claude_usd": round(sum(level["costs"]["claude_usd"] for level in mine), 4),
            }
    return out


def _requests(sources: Sources) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    dollars: defaultdict[str, float] = defaultdict(float)
    for (owner, kind, how), count in sources.tally.counts.items():
        totals[f"{kind} {how}"] += count
        dollars[f"{kind} {how}"] += sources.tally.dollars[(owner, kind, how)]
    return {name: {"requests": totals[name], "usd": round(dollars[name], 4)} for name in sorted(totals)}


# The dry run's estimate.


def estimate(result: RunResult, sources: Sources, settings: FullRunSettings, means: dict[str, float]) -> dict[str, Any]:
    """The requests and dollars each map and page still needs, from a run that sent nothing.

    Args:
        result: The dry run's result.
        sources: Its sources, whose tally counts the requests the stores lack.
        settings: The run's settings.
        means: Dollars a request of each Claude kind, as the screening measured them at the run's settings.

    Returns:
        Per map and page, and in all, the requests the stores lack: Claude's (with the item requests' range when Clef
        answers are missing) and Clef's, and their dollars.
    """
    per: dict[str, dict[str, Any]] = defaultdict(dict)
    for (owner, kind, how), count in sources.tally.counts.items():
        if how == "missing":
            per[owner][kind] = per[owner].get(kind, 0) + count
    unanswered = result.report.get("unanswered", {})
    for uid, reading in result.readings.items():
        if reading.style is None:
            continue
        extra = unanswered.get(f"{uid} edges between floor", 0)
        known = per[uid].get("reading-items", 0)
        per[uid]["reading-items at most"] = max(
            known, math.ceil((len(reading.items) + extra) / settings.items_per_request)
        )
        per[uid]["items"] = len(reading.items)
        per[uid]["reading-items"] = known
    totals: Counter[str] = Counter()
    for counts in per.values():
        for kind, count in counts.items():
            if kind.startswith(("reading-", "clef")):
                totals[kind] += count
    usd = {
        "claude": round(
            totals["reading-wholemap"] * means.get("reading-wholemap", 0.0)
            + totals["reading-items"] * means.get("reading-items", 0.0)
            + totals["reading-offset"] * means.get("reading-offset", 0.0),
            4,
        ),
        "claude_at_most": round(
            totals["reading-wholemap"] * means.get("reading-wholemap", 0.0)
            + totals["reading-items at most"] * means.get("reading-items", 0.0)
            + totals["reading-offset"] * means.get("reading-offset", 0.0),
            4,
        ),
        "clef_local": 0.0,
        "clef_if_hosted": round((totals["clef-edge"] + totals["clef-mark"]) * HOSTED_FLASH_USD, 4),
    }
    claude = totals["reading-wholemap"] + totals["reading-items"] + totals["reading-offset"]
    at_most = totals["reading-wholemap"] + totals["reading-items at most"] + totals["reading-offset"]
    return {
        "per": {owner: counts for owner, counts in sorted(per.items())},
        "totals": dict(totals),
        "claude_requests": claude,
        "claude_requests_at_most": at_most,
        "claude_requests_with_one_retry_each": 2 * at_most,
        "usd": usd,
        "means": means,
    }


def screening_means(path: Path, settings: FullRunSettings) -> dict[str, float]:
    """Dollars a request of each kind at the run's settings, from the screening's summary."""
    data = json.loads(path.read_text())

    def mean(setting: Any, kind: str) -> float:
        figures = data.get(f"{setting.model} {setting.effort}", {}).get(kind)
        return round(figures["cost_usd"] / figures["requests"], 6) if figures and figures["requests"] else 0.0

    return {
        "reading-wholemap": mean(settings.whole_page, "wholemap"),
        "reading-items": mean(settings.items, "itemsk30"),
        "reading-offset": mean(settings.offset_pick, "offset"),
    }


# Sources.


def make_sources(args: argparse.Namespace, settings: FullRunSettings, members: Sequence[Member]) -> Sources:
    """The stores, seeded, and the sources the arguments name."""
    clef_store = ClefStore(args.out / "clef.jsonl")
    claude_store = ClaudeStore(args.out / "claude")
    sources = Sources(clef_store=clef_store, claude_store=claude_store)
    maps = {run_map.uid: run_map.screen for member in members for run_map in member.maps}
    if args.claude == "fake" or args.clef == "fake":
        from .full_fakes import FakeClaude, FakeClef

        pages = {page.id: (page, member.document()) for member in members for page in member.pages}
        if args.claude == "fake":
            sources.claude, sources.claude_how = FakeClaude(maps, pages), "fake"
        if args.clef == "fake":
            sources.clef, sources.clef_how = FakeClef(maps), "fake"
    if args.claude == "live":
        from .claude_run import ask, make_provider

        provider = make_provider(settings.whole_page.model)
        cap = RequestCap(provider.settings.ledger_path, settings.cap)

        def live_claude(request: Any, check: Any, about: dict[str, Any]) -> dict[str, Any]:
            return ask(provider, request, check, cap).to_json()

        sources.claude, sources.claude_how = live_claude, "asked"
    if args.clef == "local":
        from osrforge.providers.clef_local import LocalClefProvider, LocalClefSettings

        local = LocalClefProvider(LocalClefSettings.from_env())
        sources.clef, sources.clef_how = (lambda request, about: dict(local.decide(request).probabilities)), "local"
    if args.clef == "hosted":
        from osrforge.providers.clef import ClefProvider, ClefSettings

        from .clefask import capped_stop

        hosted_settings = ClefSettings.from_env()
        hosted_settings = hosted_settings.model_copy(
            update={
                "run_label": "reading full run",
                "ledger_stop_usd": capped_stop(
                    hosted_settings.ledger_path, hosted_settings.ledger_stop_usd, settings.hosted_spend_usd
                ),
            }
        )
        hosted = ClefProvider(hosted_settings)
        model = settings.hosted_model

        def hosted_clef(request: Any, about: dict[str, Any]) -> dict[str, float]:
            from .hosted_exported import for_model

            return dict(hosted.decide(for_model(request, model)).probabilities)  # type: ignore[arg-type]

        sources.clef, sources.clef_how = hosted_clef, "hosted"
    return sources


def seed(
    sources: Sources,
    members: Sequence[Member],
    halves: Sequence[HalvesSpec],
    screening: Path | None,
    settings: FullRunSettings,
) -> dict[str, int]:
    """Seed the stores with the answer-key pass's and the screening's answers."""
    filed: Counter[str] = Counter()
    for member in members:
        for run_map in member.maps:
            filed["clef answer-key pass"] += seed_clef_exported(sources.clef_store, run_map.uid, run_map.screen)
            if screening is not None:
                name = run_map.screen.id
                stores = [screening / "clef" / "local" / "keyset" / f"{name}.edge.jsonl"] if member.synthetic else []
                if not member.synthetic:
                    stores.append(screening / "clef" / "marks" / f"{name}.mark.jsonl")
                filed["clef screening"] += seed_clef_stores(sources.clef_store, run_map.uid, stores, "screening")
    if screening is not None:
        pages = [page for member in members if not member.synthetic for page in member.pages]
        specs = [*halves, *(spec for member in members for spec in member.halves)]
        for kind, count in seed_claude(
            sources.claude_store,
            screening / "claude",
            pages,
            (settings.whole_page.model, settings.whole_page.effort),
            specs,
            (settings.offset_pick.model, settings.offset_pick.effort),
        ).items():
            filed[f"claude {kind}"] += count
    return dict(filed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The reading measurement's full run.")
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--maps", type=Path, action="append", default=[])
    parser.add_argument("--pages", type=Path, action="append", default=[])
    parser.add_argument("--keys", type=Path, action="append", default=[])
    parser.add_argument("--halves", type=Path, action="append", default=[])
    parser.add_argument("--synthetic", type=Path, default=None)
    parser.add_argument("--screening", type=Path, default=None, help="the screening's results directory, to seed from")
    parser.add_argument("--only", action="append", default=None, help="a member id to run, of several")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--stand-in", action="store_true", help="let the key's whole-page answer stand in where none is stored"
    )
    parser.add_argument("--claude", choices=("none", "fake", "live"), default="none")
    parser.add_argument("--clef", choices=("none", "fake", "local", "hosted"), default="none")
    parser.add_argument(
        "--claude-for", action="append", default=None, help="a member whose requests Claude answers, of several"
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--ledger", type=Path, default=None, help="the Claude ledger a dry run counts the cap on")
    parser.add_argument("--pictures", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run and (args.claude != "none" or args.clef != "none"):
        parser.error("a dry run sends nothing: leave --claude and --clef at none")
    fake = args.claude == "fake" or args.clef == "fake"
    marker = args.out / "FAKE"
    stored = (args.out / "clef.jsonl").exists() or (args.out / "claude").exists()
    if fake and stored and not marker.exists():
        parser.error(f"{args.out} holds real answers, and a run on fake answers would mix its answers in")
    if not fake and marker.exists():
        parser.error(f"{args.out} holds fake answers, so a run on real ones can't use it")
    settings = load_settings(args.settings)
    members = real_members(args.maps, args.pages, args.keys) if args.maps else []
    if args.synthetic is not None:
        members += synthetic_members(args.synthetic)
    if args.only:
        members = [member for member in members if member.id in args.only]
    halves = [spec for path in args.halves for spec in load_halves(path) if spec.real]
    args.out.mkdir(parents=True, exist_ok=True)
    if fake:
        marker.write_text("The answers here include fake ones made from the answer keys.\n")
    sources = make_sources(args, settings, members)
    if args.claude_for:
        chosen = [member for member in members if member.id in args.claude_for]
        sources.claude_owners = {
            *(run_map.uid for member in chosen for run_map in member.maps),
            *(page.id for member in chosen for page in member.pages),
            *(spec.id for member in chosen for spec in member.halves),
            *(spec.id for spec in halves),
        }
    seeded = seed(sources, members, halves, args.screening, settings)
    print(f"seeded: {seeded}", flush=True)
    result = run(members, halves, settings, sources, stand_in=args.dry_run or args.stand_in, workers=args.workers)
    result.report["seeded"] = seeded
    name = "dry-run" if args.dry_run else f"report-claude-{args.claude}-clef-{args.clef}"
    name += "-stand-in" if args.stand_in and not args.dry_run else ""
    if args.dry_run:
        means = screening_means(args.screening / "claude" / "summary.json", settings) if args.screening else {}
        result.report["estimate"] = estimate(result, sources, settings, means)
        if args.ledger is not None:
            sent = RequestCap(args.ledger.expanduser(), settings.cap).sent()
            result.report["estimate"]["cap"] = {"cap": settings.cap, "sent": sent, "left": settings.cap - sent}
    (args.out / f"{name}.json").write_text(json.dumps(result.report, indent=1) + "\n")
    for member_id, trace in result.traces.items():
        safe = member_id.replace("/", "__")
        (args.out / "traces").mkdir(exist_ok=True)
        (args.out / "traces" / f"{safe}.{name}.json").write_text(trace.model_dump_json(indent=1) + "\n")
    print_report(result.report, sys.stdout)
    if args.pictures:
        from .full_pictures import milestone_pictures

        for path in milestone_pictures(result, members, args.out / "pictures", settings.milestone):
            print(f"picture: {path}")
    return 0


def print_report(report: dict[str, Any], out: Any) -> None:
    """The report's figures, one line per level, then by style, then the requests."""
    for member, figures in report["members"].items():
        out.write(f"{member}: {figures['resolution']}\n")
    out.write(
        "member | level | style | floor both/key | edges right/total (walls open, open blocked) | cave edges | "
        "keys | areas perfect | joins matched/key | reach | needs no repair (key) | Claude req, $\n"
    )
    for level in report["levels"]:
        a = level["after"]
        e, c = a["edges"]["all"], a["edges"]["cave"]
        out.write(
            f"{level['member']} | {level['dungeon']}/{level['level']} | {level['style']} | "
            f"{a['floor']['both']}/{a['floor']['key']} | {e['right']}/{e['total']} "
            f"({e['walls_read_as_open']}, {e['open_read_as_blocked']}) | {c['right']}/{c['total']} | "
            f"{a['keys']['right']}/{a['keys']['total']} | {a['areas']['perfect']}/{a['areas']['areas']} | "
            f"{a['joins']['matched']}/{a['joins']['key']} | "
            f"{a['reachability']['right']}/{a['reachability']['total']} | "
            f"{level['needs_no_repair']} ({level['key_checks']['needs_no_repair']}) | "
            f"{level['costs']['claude_requests']}, {level['costs']['claude_usd']}\n"
        )
    for style, figures in report["by_style"].items():
        out.write(f"{style}: {json.dumps(figures)}\n")
    out.write(f"halves: {json.dumps(report['halves'])}, apart: {report['halves_apart']}\n")
    out.write(f"requests: {json.dumps(report['requests'])}\n")
    if "estimate" in report:
        estimate_ = report["estimate"]
        out.write(f"estimate totals: {json.dumps(estimate_['totals'])}\n")
        out.write(
            f"estimate: Claude {estimate_['claude_requests']} to {estimate_['claude_requests_at_most']} requests "
            f"({estimate_['claude_requests_with_one_retry_each']} with one retry each), "
            f"{json.dumps(estimate_['usd'])}, "
            f"cap {json.dumps(estimate_.get('cap'))}\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
