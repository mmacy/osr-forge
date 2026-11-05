"""The full run's pools by style, and the bar each style must meet.

**Pools.** A style's pool adds up its levels and panels so that each drawn cell and edge counts once:

- the reading families (floor, edges with cave edges apart, and doors) over the key's panels, each panel scored once
  against the trace's panel of the same id with `traces.score_panel`. As in the scorer, a panel the trace doesn't have
  (two halves the trace joins and the key leaves apart, or the other way round) has no partner, and a panel used only
  by levels that fell back in the trace or the key is left out, so a pool's reading families add up its members'.
- the resolution families (areas, keys, joins, reachability, entrance, transitions, passages, repairs, and the levels'
  verdicts) over the levels' rows, each level once. A level's entrance family counts only where the key's entrance
  stands on it.
- the halves family over the members' pairs, by the style of the pair's first region
- each level's Claude and Clef requests and dollars, by origin

A panel's or a level's style is the key's style of its region (the first region of a joined panel).

**The bar** (step 8 of the plan's measurements). For each style of the answer keys, caves included, the stage must
beat the classifier on whether each edge is passable or blocked and on the joins between areas, over every edge of
the answer keys, and its rate of walls read as open must be no worse than the classifier's on the same keys, which is
the bar the screening set. The table gives, per style:

- the edges of the keys' universe, and how many each reading gets right as passable or blocked, with its walls read
  as open and open edges read as blocked, from `traces.score_panel`
- the rate of walls read as open over those edges, the stage's beside the classifier's
- the joins between areas: the key's, and each reading's errors (joins it lacks and joins it adds), from
  `score_trace` on each level's documents. The classifier reads only cells and edges, so its trace is the key's
  reading with each panel's cells and edges replaced by the classifier's, resolved by the reference resolution
  script with the member's caches.
- the edges the answer-key pass's readings disagreed on and its settler decided (`<work>/items.json`, status
  `disputed`), and how many each reading gets right as passable or blocked. These are counted on the key's grid edge
  by edge, 5-foot squares as drawn, because the scorer has no family for a subset of edges.

A style *passes* when the stage's rate of walls read as open is at most the classifier's, the stage gets more edges
right than the classifier, and the stage makes fewer join errors than the classifier.
"""

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from osrforge.tracing.score import ScoreSet
from osrforge.tracing.truth import GeometryTruth, Panel

from .answers import _canonical
from .full_members import Member
from .traces import score_panel

__all__ = ["bar_table", "disputed_edges", "panel_scores", "pools"]

Edge = tuple[tuple[int, int], str]


def _reading(score: ScoreSet) -> dict[str, Any]:
    edges = {
        part: {
            "total": tally.total,
            "right": tally.right,
            "walls_read_as_open": tally.walls_read_as_open,
            "open_read_as_blocked": tally.open_read_as_blocked,
        }
        for part, tally in (("all", score.edges.all), ("straight", score.edges.straight), ("cave", score.edges.cave))
    }
    doors = {
        kind: {
            "key": getattr(score.doors, kind).key,
            "trace": getattr(score.doors, kind).trace,
            "matched": getattr(score.doors, kind).matched,
        }
        for kind in ("door", "locked", "secret")
    }
    return {
        "floor": {"key": score.floor.key_cells, "trace": score.floor.trace_cells, "both": score.floor.both},
        "edges": edges,
        "doors": doors,
    }


def panel_scores(trace: GeometryTruth, key: GeometryTruth) -> dict[str, dict[str, Any]]:
    """The reading families of each key panel, scored once against the trace's panel of the same id.

    Returns:
        By key panel id: its style, and its `after` and `after_whole` reading families, or `None` with no partner or
        when only levels that fell back in either document use it.
    """
    users: dict[str, set[tuple[str, int]]] = {}
    fell_back: set[tuple[str, int]] = set()
    for document in (trace, key):
        for dungeon in document.dungeons:
            for level in dungeon.levels:
                if level.verdict == "fallback":
                    fell_back.add((dungeon.id, level.number))
                for part in level.panels:
                    users.setdefault(part.panel, set()).add((dungeon.id, level.number))
    mine = {panel.id: panel for panel in trace.panels}
    out: dict[str, dict[str, Any]] = {}
    for panel in key.panels:
        style = key.region(panel.regions[0].region).style
        partner = mine.get(panel.id)
        if partner is None or (users.get(panel.id) and users[panel.id] <= fell_back):
            out[panel.id] = {"style": style, "after": None, "after_whole": None}
            continue
        after, whole = score_panel(key, partner)
        out[panel.id] = {"style": style, "after": _reading(after), "after_whole": _reading(whole)}
    return out


def _add(into: dict[str, Any], figures: Mapping[str, Any]) -> None:
    for name, value in figures.items():
        if value is None:
            continue
        if isinstance(value, Mapping):
            _add(into.setdefault(name, {}), value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            into[name] = round(into.get(name, 0) + value, 6)


RESOLUTION = ("areas", "keys", "joins", "reachability", "entrance", "transitions", "passages", "repairs", "verdicts")


def pools(
    levels: Sequence[dict[str, Any]],
    panels: Mapping[str, Mapping[str, dict[str, Any]]],
    halves: Mapping[str, dict[str, Any]],
    synthetic: Mapping[str, bool],
) -> dict[str, Any]:
    """Every family pooled by style, real maps apart from synthetic ones, each cell, edge, level, and pair once.

    Args:
        levels: The report's level rows.
        panels: Each member's `panel_scores`.
        halves: Each member's halves family and the style of its pairs: `{"style", "total", "right"}`.
        synthetic: Whether each member is synthetic.

    Returns:
        By `<style> real` or `<style> synthetic`: the pooled families, the levels, the levels that need no repair by
        the trace's checks and by the key's, the incomplete levels, and the costs.
    """
    out: dict[str, dict[str, Any]] = {}

    def pool(style: str, member: str) -> dict[str, Any]:
        name = f"{style} {'synthetic' if synthetic[member] else 'real'}"
        return out.setdefault(name, {"levels": 0, "needs_no_repair": 0, "key_needs_no_repair": 0, "incomplete": 0})

    for member, scores in panels.items():
        for figures in scores.values():
            if figures["after"] is None:
                continue
            target = pool(figures["style"], member)
            _add(target.setdefault("after", {}), figures["after"])
            _add(target.setdefault("after_whole", {}), figures["after_whole"])
    for row in levels:
        target = pool(row["style"], row["member"])
        target["levels"] += 1
        target["needs_no_repair"] += row["needs_no_repair"]
        target["key_needs_no_repair"] += row["key_checks"]["needs_no_repair"]
        target["incomplete"] += not row["complete"]
        for part in ("after", "after_whole"):
            _add(target.setdefault(part, {}), {name: row[part][name] for name in RESOLUTION})
        _add(target.setdefault("costs", {}), row["costs"])
    for member, figures in halves.items():
        if figures["total"]:
            target = pool(figures["style"], member)
            _add(target.setdefault("after", {}), {"halves": {"total": figures["total"], "right": figures["right"]}})
    return dict(sorted(out.items()))


def disputed_edges(work: Path | None, frame: tuple[int, int] | None, width: int, height: int) -> set[Edge]:
    """The edges the answer-key pass's readings disagreed on and its settler decided, on the key's grid."""
    if work is None or not (work / "items.json").exists():
        return set()
    items = json.loads((work / "items.json").read_text())
    dx, dy = frame or (0, 0)
    out: set[Edge] = set()
    for name, item in items["edges"].items():
        if item.get("status") != "disputed":
            continue
        cell, side = name.split(":")
        x, y = (int(part) for part in cell.split(","))
        x, y = x - dx, y - dy
        if 0 <= x < width and 0 <= y < height:
            out.add(_canonical((x, y), side, width, height))
    return out


def _edge_tally(panel: Panel, key: Panel, edges: set[Edge]) -> dict[str, int]:
    tally: Counter[str] = Counter()
    for edge in edges:
        if not (0 <= edge[0][0] < key.width and 0 <= edge[0][1] < key.height):
            continue
        keyed = key.edges.at(*edge) in "op"
        try:
            read = panel.edges.at(*edge) in "op"
        except IndexError:
            read = False
        tally["total"] += 1
        tally["right"] += read == keyed
        tally["walls_read_as_open"] += read and not keyed
        tally["open_read_as_blocked"] += keyed and not read
    return dict(tally)


def bar_table(
    members: Sequence[Member],
    traces: Mapping[str, GeometryTruth],
    classifier_traces: Mapping[str, GeometryTruth],
    levels: Sequence[dict[str, Any]],
    classifier_levels: Mapping[tuple[str, str, int], dict[str, Any]],
) -> dict[str, Any]:
    """The bar, per style of the answer keys: the stage beside the classifier.

    Args:
        members: The real members.
        traces: Each member's trace.
        classifier_traces: Each member's classifier trace.
        levels: The report's level rows.
        classifier_levels: The classifier's `after` summary per `(member, dungeon, level)`.

    Returns:
        By style: the edges, the stage's and the classifier's figures, the join errors, the disputed edges, the walls
        rate against the bar, and whether the style passes.
    """
    out: dict[str, dict[str, Any]] = {}
    for member in members:
        key = member.document()
        trace, classifier = traces.get(member.id), classifier_traces.get(member.id)
        if trace is None or classifier is None:
            continue
        stage_panels = {panel.id: panel for panel in trace.panels}
        classifier_panels = {panel.id: panel for panel in classifier.panels}
        screens = {run_map.screen.id: run_map.screen for run_map in member.maps}
        for panel in key.panels:
            style = key.region(panel.regions[0].region).style
            row = out.setdefault(style, {"stage": {}, "classifier": {}, "disputed": {"stage": {}, "classifier": {}}})
            screen = screens.get(panel.id)
            disputed = (
                disputed_edges(screen.work, screen.frame, panel.width, panel.height) if screen is not None else set()
            )
            for name, panels in (("stage", stage_panels), ("classifier", classifier_panels)):
                partner = panels.get(panel.id)
                if partner is None:
                    _add(row[name], {"edges": {"total": 0}, "unpartnered panels": 1})
                    continue
                after, _whole = score_panel(key, partner)
                _add(row[name], {"edges": _reading(after)["edges"]["all"]})
                _add(row["disputed"][name], _edge_tally(partner, panel, disputed))
    for level in levels:
        if level["synthetic"] or level["style"] not in out:
            continue
        joins = level["after"]["joins"]
        row = out[level["style"]]
        _add(row["stage"], {"joins": {"key": joins["key"], "errors": joins["missing"] + joins["extra"]}})
        theirs = classifier_levels.get((level["member"], level["dungeon"], level["level"]))
        if theirs is not None:
            _add(row["classifier"], {"joins": {"errors": theirs["joins"]["missing"] + theirs["joins"]["extra"]}})
    for row in out.values():
        for name in ("stage", "classifier"):
            edges = row[name].get("edges", {})
            total = edges.get("total", 0)
            row[name]["walls_rate"] = round(edges.get("walls_read_as_open", 0) / total, 6) if total else None
        stage, classifier = row["stage"], row["classifier"]
        walls = stage["walls_rate"] is not None and stage["walls_rate"] <= (classifier["walls_rate"] or 0.0)
        edges_beat = stage.get("edges", {}).get("right", 0) > classifier.get("edges", {}).get("right", 0)
        joins_beat = stage.get("joins", {}).get("errors", 0) < classifier.get("joins", {}).get("errors", 0)
        row["passes"] = {
            "walls": walls,
            "edges": edges_beat,
            "joins": joins_beat,
            "all": walls and edges_beat and joins_beat,
        }
    return dict(sorted(out.items()))
