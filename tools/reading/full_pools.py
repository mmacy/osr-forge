"""The full run's panels split by level, its pools by style, and the bar each style must meet.

**Panels split by level.** A level row's reading families (floor, edges with cave edges apart, and doors) count the
cells and edges of its panels that the key gives the level, so the rows on a panel two levels share add up to the
panel's score. `panel_owners` gives each cell of a panel, on the scorer's 10-foot grid, the traced level of the key
that owns it: on a panel one traced level of the key uses, that level owns every cell, and on a shared panel a level
owns the cells its grid in the key makes floor or water, mapped to the panel through the level's placement. A cell
no traced level owns belongs to *no level*: on page 38 these are the ravine floor between the lairs and the rock. An
edge belongs to the owner of its west or north cell, or to the owner of its east or south cell when no level owns
the first, so an edge on the boundary between two levels' cells counts for the level west or north of it. A trace
cell or edge belongs to the owner of the key's cell or edge it lands on after the scorer's alignment.
`panel_scores` scores each panel whole with `traces.score_panel` and splits the same comparison by owner, so a
panel's parts add up to its whole score.

**Pools.** A style's pool adds up its members' whole scores, split by style, so that each drawn cell and edge, each
area, key, join, and passage, and each region, level, pair, and dungeon counts once:

- the reading families over the key's panels, each panel scored once against the trace's panel of the same id, by
  the panel's style. As in the scorer, a panel the trace doesn't have (two halves the trace joins and the key leaves
  apart, or the other way round) has no partner, and a panel used only by levels that fell back in the trace or the
  key is left out.
- the areas, keys, joins, transitions, and passages families over the levels the scorer compares, by the style of
  the level each item stands on. A passage or transition between levels of two styles counts for the style of the
  level it leaves from, which is the level the scorer lists it on.
- the entrance and reachability families per dungeon, by the style of the level the key's entrance stands on, or of
  the dungeon's first compared level when the key's entrance is on no styled level
- the verdicts family per region, by the region's style, and per level, by the level's style
- the halves family per pair, by the style of the pair's first region
- the repairs family per repair, by the style of its level
- each level row's checks, completeness, and Claude and Clef requests and dollars, by origin

A level's style is the key's style of the first region of its first panel. A level the key draws on no panel has no
style and counts in the `none` pool. Each member's split (`member_by_style`) adds up, family by family, to its whole
score, and each pool adds up its members' splits. The level rows are for display: the scorer's resolution families on
a level's own documents differ from the member's where the level's passages and stand-in entrance differ.

**The bar** (step 8 of the plan's measurements). For each style of the answer keys, caves included, the stage must
beat the classifier on whether each edge is passable or blocked and on the joins between areas, over every edge of
the answer keys, and its rate of walls read as open must be no worse than the classifier's on the same keys, which is
the bar the screening set. The table gives, per style:

- the edges of the keys' universe, and how many each reading gets right as passable or blocked, with its walls read
  as open and open edges read as blocked, from `traces.score_panel`
- the rate of walls read as open over those edges, the stage's beside the classifier's
- the joins between areas on each traced level of the keys: the key's joins, and each reading's errors (joins it
  lacks and joins it adds), with the scorer's rule. A reading whose level fell back lacks every join of that level,
  so both readings are charged on the same levels, and the levels each reading fell back on are listed. The
  classifier reads only cells and edges, so its trace is the key's reading (its outlines, marks, and symbols) with
  each panel's cells and edges replaced by the classifier's, resolved by the reference resolution script with the
  member's caches. The stage's own trace resolves its own marks and outlines, so its join errors include theirs. For
  a like-for-like comparison the table also gives the stage's join errors with its cells and edges resolved on the
  key's reading, as the classifier's are.
- the edges the answer-key pass's readings disagreed on and its settler decided (`<work>/items.json`, status
  `disputed`), and how many each reading gets right as passable or blocked. They are counted on the scorer's 10-foot
  grid, within the scorer's edge universe of the key's panel and at the scorer's alignment, so they are a subset of
  the edges above. On a 5-foot panel an edge is disputed when any 5-foot segment the key's conversion lays on it was.
  Elsewhere every key edge the pass's readers agreed on is undisputed, and the classifier and Clef's top answer
  were among those readers, so on undisputed edges they are right by construction.
- the members left out of the table because the classifier has no reading of one of their maps

A style *passes* when the stage's rate of walls read as open is at most the classifier's, the stage gets more edges
right than the classifier, and the stage makes fewer join errors than the classifier, both counted on the stage's own
trace.
"""

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from osrforge.tracing.fivefoot import convert_five_foot
from osrforge.tracing.score import (
    ScoreSet,
    _alignment,
    _cells_of,
    _Comparison,
    _Document,
    _edge,
    _edge_cells,
    _grid_edges,
    _in_universe,
    _joins,
    _letter,
    _shift,
    score_trace,
)
from osrforge.tracing.truth import DOOR_LETTERS, PASSABLE_LETTERS, GeometryTruth, Level, Panel, parse_cell, rotate_cell

from .answers import _canonical
from .full_members import Member
from .full_score import summary
from .traces import score_panel

__all__ = [
    "NO_LEVEL",
    "bar_table",
    "disputed_edges",
    "level_reading",
    "level_style",
    "member_by_style",
    "panel_owners",
    "panel_scores",
    "pools",
]

Edge = tuple[tuple[int, int], str]
GridEdge = tuple[str, int, int]
"""An edge as the scorer keys it: its axis, the grid line it lies on, and the cell along that line."""

NO_LEVEL = "no level"
"""The part of a panel no traced level of the key owns."""

READING = ("floor", "edges", "doors")


def _reading(score: ScoreSet) -> dict[str, Any]:
    figures = summary(score)
    return {name: figures[name] for name in READING}


def _converted(panel: Panel) -> Panel:
    return convert_five_foot(panel).panel if panel.scale_feet == 5 else panel


def level_style(key: GeometryTruth, dungeon_id: str, number: int) -> str:
    """A level's style: the key's style of the first region of its first panel, or `none` without a panel."""
    for dungeon in key.dungeons:
        for level in dungeon.levels:
            if (dungeon.id, level.number) == (dungeon_id, number):
                for placement in level.panels:
                    return key.region(key.panel(placement.panel).regions[0].region).style
    return "none"


def _label(dungeon_id: str, number: int) -> str:
    return f"{dungeon_id}/{number}"


def panel_owners(key: GeometryTruth, panel_id: str) -> dict[tuple[int, int], str]:
    """Each cell of a key panel, on the scorer's 10-foot grid, with the traced level of the key that owns it.

    A panel one traced level of the key uses belongs to that level whole. On a panel several traced levels use, a
    level owns the cells its grid makes floor or water, mapped to the panel through its placement. The first level in
    key order wins a cell two levels claim. A cell no level owns is left out of the result.

    Returns:
        By panel cell: the level as `<dungeon>/<number>`.
    """
    panel = _converted(key.panel(panel_id))
    users = [
        (dungeon.id, level, placement)
        for dungeon in key.dungeons
        for level in dungeon.levels
        if level.verdict == "traced"
        for placement in level.panels
        if placement.panel == panel_id
    ]
    cells = [(x, y) for y in range(panel.height) for x in range(panel.width)]
    if len(users) == 1:
        dungeon_id, level, _placement = users[0]
        return dict.fromkeys(cells, _label(dungeon_id, level.number))
    out: dict[tuple[int, int], str] = {}
    size = (panel.width, panel.height)
    for dungeon_id, level, placement in users:
        for cell in cells:
            x, y = rotate_cell(cell, size, placement.rotation)
            x, y = placement.origin[0] + x, placement.origin[1] + y
            if 0 <= y < level.height and 0 <= x < level.width and level.cells[y][x] in ".~":
                out.setdefault(cell, _label(dungeon_id, level.number))
    return out


def _no_reading() -> dict[str, Any]:
    """The reading families with every count 0."""
    return {
        "floor": {"key": 0, "trace": 0, "both": 0},
        "edges": {
            name: {"total": 0, "right": 0, "walls_read_as_open": 0, "open_read_as_blocked": 0}
            for name in ("all", "straight", "cave")
        },
        "doors": {kind: {"key": 0, "trace": 0, "matched": 0} for kind in ("door", "locked", "secret")},
    }


def _split(
    key: Panel, trace: Panel, owners: Mapping[tuple[int, int], str], *, whole: bool
) -> dict[str, dict[str, Any]]:
    """The scorer's reading families of one converted panel, split by the owner of each cell and edge.

    This follows `score.reading` step for step, so the parts add up to the panel's score.
    """
    dx, dy = _alignment(trace, key)
    partial = {parse_cell(text) for text in key.partial} if whole else set[tuple[int, int]]()
    cave = {parse_cell(text) for text in key.cave}
    occupied = _cells_of(key.cells, ".~")
    floor = _cells_of(key.cells, ".")
    parts: dict[str, dict[str, Any]] = {}

    def part(owner: str | None) -> dict[str, Any]:
        return parts.setdefault(owner or NO_LEVEL, _no_reading())

    def edge_owner(sides: tuple[tuple[int, int], tuple[int, int]]) -> str | None:
        return owners.get(sides[0]) or owners.get(sides[1])

    kinds = dict(zip(DOOR_LETTERS, ("door", "locked", "secret"), strict=True))
    for cell in floor - partial:
        part(owners.get(cell))["floor"]["key"] += 1
    for x, y in _cells_of(trace.cells, "."):
        landed = (x + dx, y + dy)
        if landed not in partial:
            figures = part(owners.get(landed))["floor"]
            figures["trace"] += 1
            figures["both"] += landed in floor
    for edge in _grid_edges(key.width, key.height):
        sides = _edge_cells(edge)
        if not partial.isdisjoint(sides):
            continue
        key_letter = _letter(key.edges, edge)
        trace_letter = _letter(trace.edges, _shift(edge, -dx, -dy))
        figures = part(edge_owner(sides))
        if key_letter in DOOR_LETTERS:
            figures["doors"][kinds[key_letter]]["key"] += 1
            figures["doors"][kinds[key_letter]]["matched"] += trace_letter == key_letter
        if not _in_universe(sides, occupied, partial):
            continue
        key_open, trace_open = key_letter in PASSABLE_LETTERS, trace_letter in PASSABLE_LETTERS
        for name in ("all", "straight" if cave.isdisjoint(sides) else "cave"):
            tally = figures["edges"][name]
            tally["total"] += 1
            tally["right"] += key_open == trace_open
            tally["walls_read_as_open"] += trace_open and not key_open
            tally["open_read_as_blocked"] += key_open and not trace_open
    for edge in _grid_edges(trace.width, trace.height):
        trace_letter = _letter(trace.edges, edge)
        sides = _edge_cells(_shift(edge, dx, dy))
        if trace_letter in DOOR_LETTERS and partial.isdisjoint(sides):
            part(edge_owner(sides))["doors"][kinds[trace_letter]]["trace"] += 1
    return parts


def _users(trace: GeometryTruth, key: GeometryTruth) -> tuple[dict[str, set[tuple[str, int]]], set[tuple[str, int]]]:
    users: dict[str, set[tuple[str, int]]] = {}
    fell_back: set[tuple[str, int]] = set()
    for document in (trace, key):
        for dungeon in document.dungeons:
            for level in dungeon.levels:
                if level.verdict == "fallback":
                    fell_back.add((dungeon.id, level.number))
                for part in level.panels:
                    users.setdefault(part.panel, set()).add((dungeon.id, level.number))
    return users, fell_back


def panel_scores(trace: GeometryTruth, key: GeometryTruth) -> dict[str, dict[str, Any]]:
    """The reading families of each key panel, scored once against the trace's panel of the same id, and split by level.

    Returns:
        By key panel id: its style, its `after` and `after_whole` reading families, and its `parts`, the same figures
        by owner (`<dungeon>/<number>`, or `NO_LEVEL`), each with its `after` and `after_whole`. The figures and the
        parts are `None` with no partner or when only levels that fell back in either document use the panel.
    """
    users, fell_back = _users(trace, key)
    mine = {panel.id: panel for panel in trace.panels}
    out: dict[str, dict[str, Any]] = {}
    for panel in key.panels:
        style = key.region(panel.regions[0].region).style
        partner = mine.get(panel.id)
        if partner is None or (users.get(panel.id) and users[panel.id] <= fell_back):
            out[panel.id] = {"style": style, "after": None, "after_whole": None, "parts": None}
            continue
        after, whole = score_panel(key, partner)
        owners = panel_owners(key, panel.id)
        key_panel, trace_panel = _converted(panel), _converted(partner)
        every = _split(key_panel, trace_panel, owners, whole=False)
        apart = _split(key_panel, trace_panel, owners, whole=True)
        parts = {
            owner: {"after": every.get(owner), "after_whole": apart.get(owner)}
            for owner in sorted({*every, *apart}, key=lambda name: (name == NO_LEVEL, name))
        }
        out[panel.id] = {"style": style, "after": _reading(after), "after_whole": _reading(whole), "parts": parts}
    return out


def level_reading(
    panels: Mapping[str, dict[str, Any]], key: GeometryTruth, dungeon_id: str, number: int
) -> dict[str, dict[str, Any]]:
    """A level's reading families: its parts of the panels it uses in the key, added up.

    A panel `panel_scores` leaves out adds nothing, so a level whose panels are all left out has every count 0.

    Args:
        panels: The member's `panel_scores`.
        key: The answer key.
        dungeon_id: The level's dungeon.
        number: The level's number.

    Returns:
        The `after` and `after_whole` reading families.
    """
    label = _label(dungeon_id, number)
    out = {"after": _no_reading(), "after_whole": _no_reading()}
    for placement in key.level(dungeon_id, number).panels:
        parts = (panels.get(placement.panel) or {}).get("parts") or {}
        for part in ("after", "after_whole"):
            _add(out[part], (parts.get(label) or {}).get(part) or {})
    return out


def _add(into: dict[str, Any], figures: Mapping[str, Any]) -> None:
    for name, value in figures.items():
        if value is None or name == "mean_overlap":
            continue
        if isinstance(value, Mapping):
            _add(into.setdefault(name, {}), value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            into[name] = round(into.get(name, 0) + value, 6)


def _mean_overlaps(figures: dict[str, Any]) -> None:
    for part in ("after", "after_whole"):
        areas = figures.get(part, {}).get("areas")
        if areas is not None:
            counted = areas.get("overlapped", 0)
            areas["mean_overlap"] = round(areas.get("overlaps", 0) / counted, 4) if counted else None


def _placement(total: int, right: int, extra: int) -> dict[str, int]:
    return {"total": total, "right": right, "extra": extra}


def member_by_style(trace: GeometryTruth, key: GeometryTruth, panels: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """A member's whole score split by style, each item counted once, so the styles add up to the whole score.

    Args:
        trace: The member's trace.
        key: Its answer key.
        panels: Its `panel_scores`.

    Returns:
        By style: the `after` and `after_whole` families, in `full_score.summary`'s form.
    """
    out: dict[str, dict[str, Any]] = {}

    def style_part(style: str) -> dict[str, Any]:
        return out.setdefault(style, {"after": {}, "after_whole": {}})

    def both(style: str, figures: Mapping[str, Any]) -> None:
        for part in ("after", "after_whole"):
            _add(style_part(style)[part], figures)

    for figures in panels.values():
        if figures["after"] is not None:
            _add(style_part(figures["style"])["after"], figures["after"])
            _add(style_part(figures["style"])["after_whole"], figures["after_whole"])
    whole = score_trace(trace, key).after
    comparison = _Comparison(_Document(trace), _Document(key))
    compared = comparison.compared
    styles = {level_id: level_style(key, *level_id) for level_id in compared}
    for style in sorted(set(styles.values())):
        comparison.compared = tuple(level_id for level_id in compared if styles[level_id] == style)
        score = ScoreSet.model_construct(
            areas=comparison.areas(),
            keys=comparison.keys(),
            joins=comparison.joins(),
            transitions=comparison.transitions(),
            passages=comparison.passages(),
        )
        both(style, _resolution_parts(score))
    comparison.compared = compared
    excluded = comparison.excluded
    compared_set = set(compared)
    trace_dungeons = {dungeon.id: dungeon for dungeon in trace.dungeons}
    for dungeon in key.dungeons:
        entrance = dungeon.entrance
        style = level_style(key, dungeon.id, entrance.level) if entrance is not None else "none"
        if style == "none":
            style = next(
                (
                    styles[(dungeon.id, level.number)]
                    for level in dungeon.levels
                    if (dungeon.id, level.number) in styles
                ),
                "none",
            )
        if any((dungeon.id, level.number) in compared_set for level in dungeon.levels):
            wrong = any(text.startswith(f"{dungeon.id}: ") for text in whole.reachability.wrong)
            both(style, {"reachability": {"total": 1, "right": int(not wrong)}})
        keyed = entrance is not None and (dungeon.id, entrance.level) not in excluded
        right = keyed and f"{dungeon.id}/{entrance.level}/{entrance.cell}" not in whole.entrance.wrong  # type: ignore[union-attr]
        theirs = trace_dungeons.get(dungeon.id)
        traced = (
            theirs is not None and theirs.entrance is not None and (dungeon.id, theirs.entrance.level) not in excluded
        )
        both(style, {"entrance": _placement(int(keyed), int(right), int(traced and not right))})
    for dungeon in trace.dungeons:
        entrance = dungeon.entrance
        if (
            all(other.id != dungeon.id for other in key.dungeons)
            and entrance is not None
            and (dungeon.id, entrance.level) not in excluded
        ):
            both(level_style(trace, dungeon.id, entrance.level), {"entrance": _placement(0, 0, 1)})
    regions_wrong = {
        text.split(":", 1)[0].removeprefix("region ") for text in whole.verdicts.wrong if text.startswith("region ")
    }
    levels_wrong = {
        text.split(":", 1)[0].removeprefix("level ") for text in whole.verdicts.wrong if text.startswith("level ")
    }
    for region in key.regions:
        right = int(region.id not in regions_wrong)
        both(region.style, {"verdicts": {"regions": 1, "regions_right": right, "levels": 0, "levels_right": 0}})
    for dungeon in key.dungeons:
        for level in dungeon.levels:
            right = int(_label(dungeon.id, level.number) not in levels_wrong)
            both(
                level_style(key, dungeon.id, level.number),
                {"verdicts": {"regions": 0, "regions_right": 0, "levels": 1, "levels_right": right}},
            )
    pairs = {pair.regions: pair for pair in trace.half_pairs}
    for pair in key.half_pairs:
        other = pairs.get(pair.regions)
        right = other is not None and (other.joined, other.rows, other.columns) == (
            pair.joined,
            pair.rows,
            pair.columns,
        )
        both(key.region(pair.regions[0]).style, {"halves": {"total": 1, "right": int(right)}})
    for style in list(out):
        both(style, {"repairs": {"edges": 0, "matching": 0}})
    by_level: dict[str, list[Any]] = {}
    for repair in trace.repairs:
        if (repair.dungeon, repair.level) not in excluded:
            by_level.setdefault(level_style(key, repair.dungeon, repair.level), []).append(repair)
    for style, repairs in by_level.items():
        comparison.trace = _Document(trace.model_copy(update={"repairs": tuple(repairs)}))
        score = comparison.repairs()
        both(style, {"repairs": {"edges": score.edges, "matching": score.edges_matching}})
    for figures in out.values():
        _mean_overlaps(figures)
    return dict(sorted(out.items()))


def _resolution_parts(score: ScoreSet) -> dict[str, Any]:
    """The areas, keys, joins, transitions, and passages families of a partial score set, as `summary` gives them."""
    areas = score.areas
    overlaps = [area.overlap for area in areas.areas if area.overlap is not None]
    return {
        "areas": {
            "areas": len(areas.areas),
            "perfect": sum(a.key_cells == a.trace_cells == a.both for a in areas.areas),
            "extra": len(areas.extra),
            "overlaps": round(sum(overlaps), 6),
            "overlapped": len(overlaps),
        },
        "keys": {"total": score.keys.total, "right": score.keys.right},
        "joins": {
            "key": score.joins.key_joins,
            "matched": score.joins.matched,
            "missing": len(score.joins.missing),
            "extra": len(score.joins.extra),
        },
        "transitions": _placement(score.transitions.total, score.transitions.right, score.transitions.extra),
        "passages": _placement(score.passages.total, score.passages.right, score.passages.extra),
    }


def pools(
    levels: Sequence[dict[str, Any]],
    by_style: Mapping[str, Mapping[str, dict[str, Any]]],
    synthetic: Mapping[str, bool],
) -> dict[str, Any]:
    """Every family pooled by style, real maps apart from synthetic ones: each member's split, added up.

    Args:
        levels: The report's level rows, for the levels' checks, completeness, and costs.
        by_style: Each member's `member_by_style`.
        synthetic: Whether each member is synthetic.

    Returns:
        By `<style> real` or `<style> synthetic`: the pooled families, the levels, the levels that need no repair by
        the trace's checks and by the key's, the incomplete levels, and the costs.
    """
    out: dict[str, dict[str, Any]] = {}

    def pool(style: str, member: str) -> dict[str, Any]:
        name = f"{style} {'synthetic' if synthetic[member] else 'real'}"
        return out.setdefault(name, {"levels": 0, "needs_no_repair": 0, "key_needs_no_repair": 0, "incomplete": 0})

    for member, styles in by_style.items():
        for style, figures in styles.items():
            target = pool(style, member)
            for part in ("after", "after_whole"):
                _add(target.setdefault(part, {}), figures[part])
    for row in levels:
        target = pool(row["style"], row["member"])
        target["levels"] += 1
        target["needs_no_repair"] += row["needs_no_repair"]
        target["key_needs_no_repair"] += row["key_checks"]["needs_no_repair"]
        target["incomplete"] += not row["complete"]
        _add(target.setdefault("costs", {}), row["costs"])
    for figures in out.values():
        _mean_overlaps(figures)
    return dict(sorted(out.items()))


# The bar.


def disputed_edges(work: Path | None, frame: tuple[int, int] | None, width: int, height: int) -> set[Edge]:
    """The edges the answer-key pass's readings disagreed on and its settler decided, on the key's grid as drawn."""
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


def ten_foot_edges(panel: Panel, edges: set[Edge]) -> set[GridEdge]:
    """Edges of a key panel as drawn, on the scorer's 10-foot grid.

    A 10-foot panel's edges are themselves. On a 5-foot panel, a segment on a 10-foot line of the key's conversion is
    part of the 10-foot edge that covers it, and a wall or door the conversion moved onto a 10-foot line is part of
    the edge it moved to. A segment inside a 10-foot cell is part of no edge, as in the scorer.
    """
    grid = {_edge(cell, side) for cell, side in edges}
    if panel.scale_feet != 5:
        return grid
    conversion = convert_five_foot(panel)
    ox, oy = conversion.phase
    out: set[GridEdge] = set()
    for axis, line, index in grid:
        for move in conversion.moves:
            if move.axis == axis and move.line == line and move.start <= index < move.end:
                line = move.moved_to
                break
        across, along = (ox, oy) if axis == "vertical" else (oy, ox)
        if (line + across) % 2 == 0:
            out.add((axis, (line + across) // 2, (index + along) // 2))
    return out


def _edge_tally(key: Panel, reading: Panel, edges: set[GridEdge]) -> dict[str, int]:
    """The scorer's edge tally on some of a converted key panel's edges, at the scorer's alignment."""
    dx, dy = _alignment(reading, key)
    occupied = _cells_of(key.cells, ".~")
    tally: Counter[str] = Counter()
    for edge in edges:
        if not _in_universe(_edge_cells(edge), occupied, set()):
            continue
        keyed = _letter(key.edges, edge) in PASSABLE_LETTERS
        read = _letter(reading.edges, _shift(edge, -dx, -dy)) in PASSABLE_LETTERS
        tally["total"] += 1
        tally["right"] += read == keyed
        tally["walls_read_as_open"] += read and not keyed
        tally["open_read_as_blocked"] += keyed and not read
    return dict(tally)


def _level_joins(key_level: Level, reader: GeometryTruth, dungeon_id: str) -> tuple[dict[str, int], bool]:
    """A reading's joins on one traced level of the key, and whether its level fell back."""
    wanted = _joins(key_level)
    try:
        level = reader.level(dungeon_id, key_level.number)
    except KeyError:
        level = None
    fell_back = level is None or level.verdict != "traced"
    found = set() if fell_back else _joins(level)
    missing, extra = len(wanted - found), len(found - wanted)
    return {"key": len(wanted), "missing": missing, "extra": extra, "errors": missing + extra}, fell_back


READERS = ("stage", "stage_on_key_reading", "classifier")


def bar_table(
    members: Sequence[Member],
    traces: Mapping[str, GeometryTruth],
    classifier_traces: Mapping[str, GeometryTruth | None],
    on_key_reading: Mapping[str, GeometryTruth | None],
) -> dict[str, Any]:
    """The bar, per style of the answer keys: the stage beside the classifier.

    Args:
        members: The real members.
        traces: Each member's trace.
        classifier_traces: Each member's classifier trace, `None` when the classifier has no reading of a map.
        on_key_reading: Each member's stage cells and edges resolved on the key's reading, `None` when the stage's
            trace lacks a key panel.

    Returns:
        By style: the edges, each reading's figures and the levels it fell back on, the join errors, the disputed
        edges, the walls rate against the bar, whether the style passes, and the members left out.
    """
    out: dict[str, dict[str, Any]] = {}

    def row_of(style: str) -> dict[str, Any]:
        return out.setdefault(
            style,
            {
                **{name: {"fell_back": []} for name in READERS},
                "disputed": {"stage": {}, "classifier": {}},
                "left_out": [],
            },
        )

    for member in members:
        key = member.document()
        trace, classifier = traces.get(member.id), classifier_traces.get(member.id)
        if trace is None or classifier is None:
            styles = {key.region(panel.regions[0].region).style for panel in key.panels}
            for style in sorted(styles):
                row_of(style)["left_out"].append(member.id)
            continue
        readers = {"stage": trace, "stage_on_key_reading": on_key_reading.get(member.id), "classifier": classifier}
        stage_panels = {panel.id: panel for panel in trace.panels}
        classifier_panels = {panel.id: panel for panel in classifier.panels}
        screens = {run_map.screen.id: run_map.screen for run_map in member.maps}
        for panel in key.panels:
            style = key.region(panel.regions[0].region).style
            row = row_of(style)
            screen = screens.get(panel.id)
            disputed = (
                ten_foot_edges(panel, disputed_edges(screen.work, screen.frame, panel.width, panel.height))
                if screen is not None
                else set()
            )
            for name, panels in (("stage", stage_panels), ("classifier", classifier_panels)):
                partner = panels.get(panel.id)
                if partner is None:
                    _add(row[name], {"edges": {"total": 0}, "unpartnered panels": 1})
                    continue
                after, _whole = score_panel(key, partner)
                _add(row[name], {"edges": _reading(after)["edges"]["all"]})
                _add(row["disputed"][name], _edge_tally(_converted(panel), _converted(partner), disputed))
        for dungeon in key.dungeons:
            for level in dungeon.levels:
                if level.verdict != "traced":
                    continue
                row = row_of(level_style(key, dungeon.id, level.number))
                for name, reader in readers.items():
                    if reader is None:
                        if member.id not in row[name].setdefault("left_out", []):
                            row[name]["left_out"].append(member.id)
                        continue
                    joins, fell_back = _level_joins(level, reader, dungeon.id)
                    _add(row[name], {"joins": joins})
                    if fell_back:
                        row[name]["fell_back"].append(f"{member.id} {_label(dungeon.id, level.number)}")
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
