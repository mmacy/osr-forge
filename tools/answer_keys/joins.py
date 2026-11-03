"""The join round: the prose's joins between areas that a key doesn't make, and the reading's cells and edges between
them, sent back to the settler.

A *prose join* is a connection the content cache gives from one area to another of the same level by passage,
door, or secret door. The key makes it when a walk across passable edges and doors joins the two areas
(`assemble.prose_joins`). When it doesn't, the reading has lost floor or an opening between them, or the drawing
has none. The prose only says where to look: for each join the key doesn't make, `join_gaps` finds the cheapest way
from one area to the other on the panel's reading, where entering a rock cell costs one and crossing a wall between
two floor cells costs one, through cells that are rock or the level's own floor. The rock cells and walls on it are
the *gap*. Each gap's cells go to the settler first, with pixel counts and the diagonal rule, and then
`recheck.cell_edge_items` asks the edges of every cell that turned floor along with the gap's walls; the settler
decides each from the crops and records a join the drawing truly lacks as lacking.

The requests go through the map's `recheck` batch store, in jobs that supersede earlier recheck answers on the same
items, so their values are the recheck's.
"""

import heapq
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from osrforge.tracing.truth import GeometryTruth, Page, PageImage, Panel, Region

from .batches import BatchStore
from .keyspec import KeySpec, MapContext
from .readings import edge_cells, edge_key, edge_name, load_items
from .recheck import _shares, pixel_count_text
from .settler import (
    RECHECK_SYSTEM,
    SCHEMA,
    _describe_edge,
    _load_calls,
    _readings_text,
    _rebuild_item,
    _request,
    with_reading,
)

__all__ = [
    "GAP_CAP",
    "Gap",
    "changed_by_round",
    "collect_check",
    "confirmed",
    "join_gaps",
    "load_gaps",
    "resolved",
    "save_gaps",
    "submit_check",
    "submit_consistency",
    "submit_edges",
    "submit_gaps",
    "submit_resettle",
]

GAP_CAP = 6
"""The most rock cells and walls a gap may hold. A join farther apart than that is one the drawing doesn't show near
the two areas, and the round leaves it as the key has it."""

_SIDES = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


@dataclass
class Gap:
    """The cells and edges between two areas of a level that the prose joins and the key doesn't."""

    level: str
    areas: tuple[str, str]
    via: str
    panel: str
    cells: list[tuple[int, int]] = field(default_factory=list)
    """Rock cells on the cheapest way between the areas, on the panel's grid."""

    edges: list[str] = field(default_factory=list)
    """Walls between two floor cells on that way, as edge names."""

    cost: int | None = None
    """How many cells and edges the gap holds, or `None` when none was found within `GAP_CAP`."""


def resolved(keys: list[KeySpec], stages: Path, lenient: bool = False) -> tuple[Any, dict[Any, Any]]:
    """Resolve a member's regions with the reference script and return the resolver, with its state, and the content
    caches.

    `lenient` notes an unprinted entrance key that finds no outer opening in the resolver's `passed_over` and goes
    on, instead of stopping: the gaps are the reading's, so that stop doesn't change where they are. It is for
    measuring only, and no key is written from it."""
    from .assemble import _shell_dungeons
    from .resolve import ConventionGap, Resolver, load_caches, reading_of

    class Lenient(Resolver):
        passed_over: list[str]

        def entrance_key_area(self, key: Any) -> Any:
            try:
                return super().entrance_key_area(key)
            except ConventionGap as gap:
                self.passed_over.append(str(gap))
                return None

    pages: dict[int, list[PageImage]] = {}
    regions: list[Region] = []
    panels: list[Panel] = []
    for key in keys:
        document = GeometryTruth.model_validate(json.loads((key.directory / "reading.json").read_text()))
        for page in document.pages:
            pages.setdefault(page.number, [])
            pages[page.number] += [image for image in page.images if image not in pages[page.number]]
        regions.extend(document.regions)
        panels.extend(document.panels)
    reading = GeometryTruth(
        pages=tuple(Page(number=number, images=tuple(images)) for number, images in sorted(pages.items())),
        regions=tuple(regions),
        panels=tuple(panels),
        dungeons=_shell_dungeons(keys[0]),
    )
    survey, contents, mapread = load_caches(stages)
    resolver = (Lenient if lenient else Resolver)(reading_of(reading), survey, contents, mapread)
    resolver.passed_over = []  # type: ignore[attr-defined]
    resolver.run()
    return resolver, contents


def confirmed(ctx: MapContext) -> tuple[set[tuple[int, int]], set[str]]:
    """The rock cells and walls the join round has already confirmed on a map, which a later gap goes around."""
    cells, edges = set(), set()
    for name, call in _load_calls(ctx, "recheck").items():
        if "__zjoins" not in call["job"]:
            continue
        if name.startswith("c:") and call["value"] == "#":
            x, y = (int(part) for part in name[2:].split(","))
            cells.add((x, y))
        elif name.startswith("e:") and call["value"] in "wn":
            edges.add(name[2:])
    return cells, edges


def join_gaps(
    resolver: Any, contents: dict[Any, Any], refused: dict[str, tuple[set[tuple[int, int]], set[str]]] | None = None
) -> list[Gap]:
    """The gap of every prose join the resolved key doesn't make.

    Args:
        resolver: A `Resolver` after `run()`.
        contents: The content caches by `(dungeon id, level number)`.
        refused: By panel, the rock cells and walls the round has confirmed, which a gap goes around.

    Returns:
        One gap per join the key doesn't make, its `cost` `None` when no way within `GAP_CAP` was found.
    """
    world = resolver.world
    gaps: list[Gap] = []
    for level_id, state in resolver.levels.items():
        content = contents.get(level_id)
        if state.verdict != "traced" or content is None:
            continue
        mine = {cell for cell, owner in resolver.owner.items() if owner == level_id}

        def floor_part(start: Any, mine: set[Any] = mine) -> set[Any]:
            seen, stack = {start}, [start]
            while stack:
                cell = stack.pop()
                for side in _SIDES:
                    other = world.across(cell, side)
                    if other in mine and other not in seen and world.edge(cell, side) in "opdls":
                        seen.add(other)
                        stack.append(other)
            return seen

        pairs: set[tuple[str, str]] = set()
        for area in content.areas:
            for connection in area.connections:
                one, other = str(area.key), str(connection.to_key)
                if connection.via not in ("passage", "door", "secret_door"):
                    continue
                if one not in state.areas or other not in state.areas or not state.areas[one] or not state.areas[other]:
                    continue
                pair = (min(one, other), max(one, other))
                if pair in pairs:
                    continue
                pairs.add(pair)
                start = next(iter(state.areas[one]))
                if floor_part(start) & state.areas[other]:
                    continue
                gaps.append(
                    _gap(
                        world, level_id, mine, state.areas[one], state.areas[other], pair, connection.via, refused or {}
                    )
                )
    return gaps


def _gap(
    world: Any,
    level_id: Any,
    mine: set[Any],
    one: set[Any],
    other: set[Any],
    pair: tuple[str, str],
    via: str,
    refused: dict[str, tuple[set[tuple[int, int]], set[str]]] | None = None,
) -> Gap:
    """The cheapest way from one area to another through the level's floor and rock, by Dijkstra on costs 0 and 1."""
    level = f"{level_id[0]}/{level_id[1]}"
    panel = next(iter(one))[0]
    gap = Gap(level=level, areas=pair, via=via, panel=panel)
    rock_done, walls_done = (refused or {}).get(panel, (set(), set()))
    best: dict[Any, int] = {}
    before: dict[Any, tuple[Any, str]] = {}
    heap: list[tuple[int, tuple[int, int, int], Any]] = []
    for cell in one:
        best[cell] = 0
        heapq.heappush(heap, (0, world.order(cell), cell))
    end = None
    while heap:
        cost, _order, cell = heapq.heappop(heap)
        if cost > best.get(cell, GAP_CAP + 1) or cost > GAP_CAP:
            continue
        if cell in other:
            end = cell
            break
        for side in _SIDES:
            nxt = world.across(cell, side)
            if nxt is None or nxt[0] != panel:
                continue
            rock = not world.is_floor(nxt) and not world.is_water(nxt)
            if not rock and nxt not in mine:
                continue
            if rock and world.cell(nxt) in rock_done:
                continue
            here_floor = world.is_floor(cell) or world.is_water(cell)
            cx, cy = world.cell(cell)
            if here_floor and not rock and _name(cx, cy, side) in walls_done:
                continue
            step = 1 if rock else (1 if here_floor and world.edge(cell, side) not in "opdls" else 0)
            if cost + step < best.get(nxt, GAP_CAP + 1):
                best[nxt] = cost + step
                before[nxt] = (cell, side)
                heapq.heappush(heap, (cost + step, world.order(nxt), nxt))
    if end is None:
        return gap
    gap.cost = best[end]
    cell = end
    while cell in before:
        previous, side = before[cell]
        if not world.is_floor(cell) and not world.is_water(cell):
            gap.cells.append(world.cell(cell))
        elif (world.is_floor(previous) or world.is_water(previous)) and world.edge(previous, side) not in "opdls":
            x, y = world.cell(previous)
            gap.edges.append(_name(x, y, side))
        cell = previous
    gap.cells.reverse()
    gap.edges.reverse()
    return gap


def _name(x: int, y: int, side: str) -> str:
    """An edge's canonical name from a cell and side."""
    key = {"west": ("v", x, y), "east": ("v", x + 1, y), "north": ("h", x, y), "south": ("h", x, y + 1)}[side]
    return edge_name(key, 10**6, 10**6)


def _squares(ctx: MapContext, gap: Gap) -> tuple[list[tuple[int, int]], list[str]]:
    """A gap's cells and edges in the reading's own squares. The resolver works on a 5-foot panel converted to
    10-foot cells, so a converted cell maps back to the up to four squares it covers that the reading has as rock, and
    a converted edge to its two segments."""
    from osrforge.tracing.fivefoot import convert_five_foot

    document = GeometryTruth.model_validate(json.loads((ctx.key.directory / "reading.json").read_text()))
    panel = next(panel for panel in document.panels if panel.id == ctx.key.id)
    if panel.scale_feet != 5:
        return gap.cells, gap.edges
    ox, oy = convert_five_foot(panel).phase
    cells: list[tuple[int, int]] = []
    for x, y in gap.cells:
        for sx in (2 * x - ox, 2 * x - ox + 1):
            for sy in (2 * y - oy, 2 * y - oy + 1):
                if 0 <= sx < panel.width and 0 <= sy < panel.height and panel.cells[sy][sx] == "#":
                    cells.append((sx, sy))
    edges: list[str] = []
    for name in gap.edges:
        text, _, side = name.partition(":")
        x, y = (int(part) for part in text.split(","))
        if side == "west":
            edges += [f"{2 * x - ox},{sy}:west" for sy in (2 * y - oy, 2 * y - oy + 1) if 0 <= sy < panel.height]
        else:
            edges += [f"{sx},{2 * y - oy}:north" for sx in (2 * x - ox, 2 * x - ox + 1) if 0 <= sx < panel.width]
    return cells, edges


def gap_items(ctx: MapContext, gaps: list[Gap], kind: str) -> list[dict[str, Any]]:
    """Settling items for one map's gaps: their rock cells (`kind` `"cells"`) or their walls (`"edges"`), each with
    the prose's join among its readings, and pixel counts."""
    items = load_items(ctx)
    shares = _shares(ctx)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gap in gaps:
        if gap.panel != ctx.key.id or gap.cost is None:
            continue
        prose = (
            f"area {gap.areas[0]} and area {gap.areas[1]} of level {gap.level} are joined by a "
            f"{gap.via.replace('_', ' ')}, "
            "and the key doesn't join them; this item lies on the shortest way between them. That says where to look, "
            "not what the drawing shows"
        )
        gap_cells, gap_edges = _squares(ctx, gap)
        names = [f"c:{x},{y}" for x, y in gap_cells] if kind == "cells" else [f"e:{name}" for name in gap_edges]
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            item = _rebuild_item(ctx, items, name)
            if item is None:
                text = name[2:]
                describe = (
                    f"cell {text}: is it floor, rock, or water?"
                    if kind == "cells"
                    else (f"edge {text}, {_describe_edge(text)}: which letter?")
                )
                item = {
                    "id": name,
                    "kind": "cell" if kind == "cells" else "edge",
                    "text": f"{describe} Readings: none.",
                    "readings": [],
                }
            if kind == "cells":
                x, y = (int(part) for part in name[2:].split(","))
                item = with_reading(item, "the pixel count", pixel_count_text(shares, (x, y)))
            else:
                for cx, cy in edge_cells(edge_key(name[2:])):
                    item = with_reading(item, "the pixel count", pixel_count_text(shares, (cx, cy)).split(";")[0])
            item = with_reading(item, "the prose", prose)
            item["text"] = item["text"].split(" Readings:")[0] + f" Readings: {_readings_text(item['readings'])}."
            out.append(item)
    return out


def submit_gaps(ctx: MapContext, gaps: list[Gap], kind: str, client: Any, ledger: Any) -> tuple[str | None, int]:
    """Send one map's gap items in fresh recheck requests that supersede earlier recheck answers on the same items."""
    store = BatchStore(ctx.key.directory / "recheck")
    rechecked = _load_calls(ctx, "recheck")
    prefix = f"{ctx.key.id}__recheck__zjoins-{kind}"
    asked = {
        item["id"]
        for custom_id in store.job_ids()
        if custom_id.startswith(prefix)
        for item in store.job(custom_id)["meta"]["items"]
    }
    items = [item for item in gap_items(ctx, gaps, kind) if item["id"] not in asked]
    start = sum(1 for custom_id in store.job_ids() if custom_id.startswith(prefix) and not custom_id.endswith("__r"))
    ids = []
    for number, first in enumerate(range(0, len(items), 8), start):
        batch = items[first : first + 8]
        custom_id = f"{prefix}{number:03d}"
        supersedes = sorted({rechecked[item["id"]]["job"] for item in batch if item["id"] in rechecked})
        params = _request(ctx, batch, RECHECK_SYSTEM, SCHEMA, False)
        store.add_job(custom_id, params, {"map": ctx.key.id, "items": batch, "supersedes": supersedes})
        ids.append(custom_id)
    if not ids:
        return None, 0
    return store.submit(client, ledger, f"recheck joins {kind} {ctx.key.id}", ids=ids), len(items)


def save_gaps(ctx: MapContext, gaps: list[Gap]) -> Path:
    """Write one map's gaps to `joins/gaps.json`."""
    path = ctx.key.directory / "joins" / "gaps.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    mine = [gap.__dict__ for gap in gaps if gap.panel == ctx.key.id]
    path.write_text(json.dumps(mine, indent=1) + "\n")
    return path


def load_gaps(ctx: MapContext) -> list[Gap]:
    """Read one map's gaps back."""
    path = ctx.key.directory / "joins" / "gaps.json"
    if not path.exists():
        return []
    gaps = []
    for row in json.loads(path.read_text()):
        row["areas"] = tuple(row["areas"])
        row["cells"] = [tuple(cell) for cell in row["cells"]]
        gaps.append(Gap(**row))
    return gaps


def submit_edges(ctx: MapContext, client: Any, ledger: Any) -> tuple[str | None, int]:
    """Send the gaps' walls and the edges of every cell the round turned into floor, with the key's cell letters,
    in fresh recheck requests that supersede earlier answers on the same items."""
    from .recheck import cell_edge_items

    store = BatchStore(ctx.key.directory / "recheck")
    rechecked = _load_calls(ctx, "recheck")
    walls = gap_items(ctx, load_gaps(ctx), "edges")
    done = {name for name, call in rechecked.items() if "__zedges" in call["job"] or "__zjoins" in call["job"]}
    asked = {item["id"] for item in walls}
    items = [item for item in walls if item["id"] not in done]
    for item in cell_edge_items(ctx):
        if item["id"] not in done and item["id"] not in asked:
            items.append(item)
    prefix = f"{ctx.key.id}__recheck__zjoins-edges"
    start = sum(1 for custom_id in store.job_ids() if custom_id.startswith(prefix) and not custom_id.endswith("__r"))
    ids = []
    for number, first in enumerate(range(0, len(items), 8), start):
        batch = items[first : first + 8]
        custom_id = f"{prefix}{number:03d}"
        supersedes = sorted({rechecked[item["id"]]["job"] for item in batch if item["id"] in rechecked})
        params = _request(ctx, batch, RECHECK_SYSTEM, SCHEMA, False)
        store.add_job(custom_id, params, {"map": ctx.key.id, "items": batch, "supersedes": supersedes})
        ids.append(custom_id)
    if not ids:
        return None, 0
    return store.submit(client, ledger, f"recheck joins edges {ctx.key.id}", ids=ids), len(items)


def changed_by_round(ctx: MapContext) -> list[str]:
    """The calls whose value the join round changed: answered by a join-round job, and different from before."""
    from .recheck import changed_calls

    rechecked = _load_calls(ctx, "recheck")
    return [name for name in changed_calls(ctx) if "__zjoins" in rechecked.get(name, {}).get("job", "")]


def submit_check(ctx: MapContext, client: Any, ledger: Any) -> tuple[str | None, int]:
    """The adversarial pass over the calls the join round changed, in the map's `joins-adversary` store."""
    from .settler import ADVERSARY_SCHEMA, ADVERSARY_SYSTEM, _recorded_text, final_values

    store = BatchStore(ctx.key.directory / "joins-adversary")
    final = final_values(ctx)
    items_table = load_items(ctx)
    asked = {item["id"] for custom_id in store.job_ids() for item in store.job(custom_id)["meta"]["items"]}
    items = []
    for name in changed_by_round(ctx):
        if name in asked:
            continue
        item = _rebuild_item(ctx, items_table, name)
        if item is None:
            continue
        kind = item["kind"]
        if kind == "cell":
            x, y = (int(part) for part in name[2:].split(","))
            item = with_reading(item, "the pixel count", pixel_count_text(_shares(ctx), (x, y)))
        items.append({**item, "recorded": _recorded_text(kind, final[name]["value"]), "category": "joins"})
    ids = []
    start = len(store.job_ids())
    for number, first in enumerate(range(0, len(items), 8), start):
        batch = items[first : first + 8]
        custom_id = f"{ctx.key.id}__joins-adversary__{number:03d}"
        params = _request(ctx, batch, ADVERSARY_SYSTEM, ADVERSARY_SCHEMA, True)
        store.add_job(custom_id, params, {"map": ctx.key.id, "items": batch})
        ids.append(custom_id)
    if not ids:
        return None, 0
    return store.submit(client, ledger, f"joins adversary {ctx.key.id}", ids=ids), len(items)


def collect_check(ctx: MapContext, client: Any, ledger: Any) -> dict[str, Any]:
    """Collect the join round's adversarial pass into `joins-adversary/calls.json`."""
    from .settler import _harvest, _save_calls

    store = BatchStore(ctx.key.directory / "joins-adversary")
    counts = store.collect(client, ledger)
    result: dict[str, Any] = {"calls": 0, "retries": 0, "open": counts["open"], "errored": counts["errored"]}
    calls = _load_calls(ctx, "joins-adversary")
    _harvest(store, calls, True, result)
    _save_calls(ctx, "joins-adversary", calls)
    result["challenged"] = sum(1 for call in calls.values() if call.get("challenged"))
    result["retry_ids"] = list(store.added)
    if store.added:
        store.submit(client, ledger, f"joins adversary {ctx.key.id} retries", ids=store.added)
    return result


def submit_resettle(ctx: MapContext, client: Any, ledger: Any) -> tuple[str | None, int]:
    """Settle each challenged join-round call once more, with the challenge among its readings, in recheck jobs that
    supersede the round's answer; that value is final."""
    store = BatchStore(ctx.key.directory / "recheck")
    rechecked = _load_calls(ctx, "recheck")
    checks = _load_calls(ctx, "joins-adversary")
    items_table = load_items(ctx)
    prefix = f"{ctx.key.id}__recheck__zjoins-fix"
    asked = {
        item["id"]
        for custom_id in store.job_ids()
        if custom_id.startswith(prefix)
        for item in store.job(custom_id)["meta"]["items"]
    }
    items = []
    for name, check in sorted(checks.items()):
        if not check.get("challenged") or name in asked:
            continue
        item = _rebuild_item(ctx, items_table, name)
        if item is None:
            continue
        if item["kind"] == "cell":
            x, y = (int(part) for part in name[2:].split(","))
            item = with_reading(item, "the pixel count", pixel_count_text(_shares(ctx), (x, y)))
        item = with_reading(item, "the join round", rechecked[name]["value"])
        item = with_reading(item, "the adversarial check", check["value"])
        item["earlier_job"] = rechecked[name]["job"]
        items.append(item)
    start = sum(1 for custom_id in store.job_ids() if custom_id.startswith(prefix) and not custom_id.endswith("__r"))
    ids = []
    for number, first in enumerate(range(0, len(items), 8), start):
        batch = items[first : first + 8]
        custom_id = f"{prefix}{number:03d}"
        supersedes = sorted({item["earlier_job"] for item in batch})
        params = _request(ctx, batch, RECHECK_SYSTEM, SCHEMA, False)
        store.add_job(custom_id, params, {"map": ctx.key.id, "items": batch, "supersedes": supersedes})
        ids.append(custom_id)
    if not ids:
        return None, 0
    return store.submit(client, ledger, f"recheck joins fix {ctx.key.id}", ids=ids), len(items)


def submit_consistency(ctx: MapContext, client: Any, ledger: Any) -> tuple[str | None, int]:
    """Ask once more each wall the re-settling put between two cells the key has as floor, with both cells' key
    letters and pixel counts: the re-settling asks an edge alone, and an edge called a wall because a neighbour was
    taken for rock contradicts the key's cell."""
    from .recheck import cell_edge_items

    store = BatchStore(ctx.key.directory / "recheck")
    rechecked = _load_calls(ctx, "recheck")
    prefix = f"{ctx.key.id}__recheck__zjoins-final"
    asked = {
        item["id"]
        for custom_id in store.job_ids()
        if custom_id.startswith(prefix)
        for item in store.job(custom_id)["meta"]["items"]
    }
    items = []
    for item in cell_edge_items(ctx):
        call = rechecked.get(item["id"])
        if call is None or "__zjoins-fix" not in call["job"] or call["value"] not in "wn" or item["id"] in asked:
            continue
        item = with_reading(item, "the re-settling", f"{call['value']}, because: {call['reason']}")
        item["earlier_job"] = call["job"]
        items.append(item)
    ids = []
    for number, first in enumerate(range(0, len(items), 8)):
        batch = items[first : first + 8]
        custom_id = f"{prefix}{number:03d}"
        params = _request(ctx, batch, RECHECK_SYSTEM, SCHEMA, False)
        store.add_job(
            custom_id,
            params,
            {"map": ctx.key.id, "items": batch, "supersedes": sorted({i["earlier_job"] for i in batch})},
        )
        ids.append(custom_id)
    if not ids:
        return None, 0
    return store.submit(client, ledger, f"recheck joins final {ctx.key.id}", ids=ids), len(items)
