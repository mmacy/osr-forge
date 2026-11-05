"""Where each request of a run lands: on exactly one level, or on a row the report names for what no level owns.

Each request is counted once, by its key (`full_store.Tally`), and placed by what it is about:

- A whole-page read goes to the one traced level its page lists. A page that lists several traced levels, or none,
  puts its read on the member's row `whole-page reads of pages with several levels or none`.
- An item request goes to the one level whose read outline holds every one of its items. A request whose items lie
  in several outlines, or outside every one, goes to `item requests across levels or outside every read outline`.
- A Clef question goes to the level whose read outline holds the cell it asks about (an edge's named cell, or a
  mark's cell). One outside every outline goes to `Clef questions outside every read outline`, and one inside
  several to `Clef questions inside several read outlines`.
- A read outline that names a level the key doesn't trace puts its requests on `requests in outlines of levels the
  key doesn't trace`.
- An offset pick of a synthetic member goes to its one traced level, or to `offset picks` when it has several. The
  real pairs' picks, which decide the halves rule, go to the run's row `real pairs`.

Each row adds up its requests (ledger entries) and dollars by origin (`full_store.ORIGINS`), Claude and Clef apart,
and the rows add up to the run's totals.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .full_members import HalvesSpec, Member
from .full_reading import MapReading
from .full_store import ORIGINS, Counted, Tally
from .screen_wholemap import levels_on

__all__ = ["allocate", "level_at", "place_cell", "row_totals"]

LevelId = tuple[str, int]


def level_at(reading: MapReading, cell: tuple[int, int]) -> LevelId | str:
    """The level whose read outline holds a cell, or `outside` or `several`."""
    holding = {
        (outline.dungeon, outline.level)
        for outline in reading.outlines
        if any(x <= cell[0] < x + w and y <= cell[1] < y + h for x, y, w, h in outline.rects)
    }
    if not holding:
        return "outside"
    if len(holding) > 1:
        return "several"
    return holding.pop()


def place_cell(reading: MapReading, cell: tuple[int, int], traced: set[LevelId]) -> str | LevelId:
    """The row a request about one cell of a map lands on: a traced level, or a named row."""
    where = level_at(reading, cell)
    if where == "outside":
        return "Clef questions outside every read outline"
    if where == "several":
        return "Clef questions inside several read outlines"
    if where not in traced:
        return "requests in outlines of levels the key doesn't trace"
    return where


def _empty() -> dict[str, Any]:
    return {
        part: {origin: {"requests": 0, "usd": 0.0} for origin in ORIGINS} | {"answers": 0}
        for part in ("claude", "clef")
    }


def _add(row: dict[str, Any], entry: Counted) -> None:
    part = "clef" if entry.kind.startswith("clef") else "claude"
    figures = row[part][entry.origin]
    figures["requests"] += entry.requests
    figures["usd"] = round(figures["usd"] + entry.usd, 6)
    row[part]["answers"] += 1


def allocate(
    tally: Tally,
    members: Sequence[Member],
    readings: Mapping[str, MapReading],
    traced: Mapping[str, set[LevelId]],
    real_pairs: Iterable[HalvesSpec],
) -> dict[str, dict[str, Any]]:
    """Every counted request placed on one row.

    Args:
        tally: The run's requests, each counted once.
        members: The members.
        readings: Each map's reading, by uid.
        traced: Each member's traced levels in its key.
        real_pairs: The real pairs of halves.

    Returns:
        Rows by name: `<member> <dungeon>/<level>` for a level, `<member>: <what>` for a member's named row, and
        `real pairs`.
    """
    member_of_page = {page.id: member for member in members for page in member.pages}
    member_of_map = {run_map.uid: member for member in members for run_map in member.maps}
    member_of_pair = {spec.id: member for member in members for spec in member.halves}
    real = {spec.id for spec in real_pairs}
    rows: dict[str, dict[str, Any]] = {}

    def land(member: Member | None, where: str | LevelId, entry: Counted) -> None:
        if member is None:
            name = str(where)
        elif isinstance(where, tuple):
            name = f"{member.id} {where[0]}/{where[1]}"
        else:
            name = f"{member.id}: {where}"
        _add(rows.setdefault(name, _empty()), entry)

    for entry in tally.entries.values():
        if entry.kind == "reading-wholemap":
            member = member_of_page.get(entry.owner)
            if member is None:
                land(None, "whole-page reads of pages outside every member", entry)
                continue
            page = next(page for page in member.pages if page.id == entry.owner)
            listed = {(d, n) for d, n, _keys in levels_on(page)} & traced[member.id]
            where: str | LevelId = (
                next(iter(listed)) if len(listed) == 1 else "whole-page reads of pages with several levels or none"
            )
            land(member, where, entry)
        elif entry.kind == "reading-offset":
            if entry.owner in real:
                land(None, "real pairs", entry)
                continue
            member = member_of_pair.get(entry.owner)
            if member is None:
                land(None, "offset picks of pairs outside every member", entry)
                continue
            levels = traced[member.id]
            land(member, next(iter(levels)) if len(levels) == 1 else "offset picks", entry)
        else:
            member = member_of_map.get(entry.owner)
            reading = readings.get(entry.owner)
            if member is None or reading is None:
                land(None, "requests about maps outside every member", entry)
                continue
            if entry.kind == "reading-items":
                places = {place_cell(reading, tuple(cell), traced[member.id]) for cell, _side in entry.about["items"]}
                one = next(iter(places)) if len(places) == 1 else None
                land(
                    member,
                    one if isinstance(one, tuple) else "item requests across levels or outside every read outline",
                    entry,
                )
            else:
                cell = entry.about["edge"][0] if "edge" in entry.about else entry.about["cell"]
                land(member, place_cell(reading, tuple(cell), traced[member.id]), entry)
    return rows


def row_totals(rows: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """The rows added up, by part and origin."""
    out = _empty()
    for row in rows.values():
        for part in ("claude", "clef"):
            out[part]["answers"] += row[part]["answers"]
            for origin in ORIGINS:
                out[part][origin]["requests"] += row[part][origin]["requests"]
                out[part][origin]["usd"] = round(out[part][origin]["usd"] + row[part][origin]["usd"], 6)
    return out
