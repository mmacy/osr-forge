"""Stage 4: deterministic geometry synthesis from the extracted room graph.

Deterministic code only; recomputed inside every assembly (no cache),
completing the `run.json` `geometry` entry. Input: the survey index plus the
levels' content caches; output: a frozen per-level result (area cell clusters,
the `edges` dict, entrance, transitions, width/height) that assembly folds
into osrlib `LevelSpec`s.

The impassability hazard, defused here: osrlib grids are walls by default —
an edge absent from `LevelSpec.edges` is a wall, and the boundary is an
implicit wall — so synthesis emits `open` edges between every orthogonally
adjacent pair of same-area cells, along every corridor path, and at every
room-corridor junction. A connection whose stated mechanism is a door kind
realizes as a `door` edge on the stating area's wall (phase 6); overrides
remain the last word.

Every choice below is pinned for determinism: BFS visit order, candidate
placement order, component ordering, edge-key ordering, and row-major cell
sorting — the byte-stability tests rely on all of them.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from osrlib.crawl.dungeon import Direction as GridDirection
from osrlib.crawl.dungeon import DoorSpec, Edge, EdgeKind, Position, TransitionSpec, edge_key, step

from osrforge.contracts.stages import LevelContent, SurveyDungeon, SurveyIndex
from osrforge.survey import canonical_slug

__all__ = [
    "DEFAULT_ROOM_CELLS",
    "LevelGeometry",
    "edge_sort_key",
    "parse_dimensions",
    "synthesize_geometry",
]

DEFAULT_ROOM_CELLS = (2, 2)
"""The room size in cells when an area's description states no dimensions."""

_PLACEMENT_ORDER = (GridDirection.NORTH, GridDirection.EAST, GridDirection.SOUTH, GridDirection.WEST)
"""The pinned direction order for unknown-direction placement and fallback."""

_COMPASS = {
    "north": GridDirection.NORTH,
    "east": GridDirection.EAST,
    "south": GridDirection.SOUTH,
    "west": GridDirection.WEST,
}

# A feet-by-feet dimension statement: two numbers joined by an x, the
# multiplication sign, or "by"; at least one unit marker (an apostrophe, a
# prime, or a feet word) required so bare number pairs ("2 x 4 posts") don't
# match. The Unicode characters are the point — modules print them.
_UNIT = r"(?:feet|foot|ft\.?|'|′)"  # noqa: RUF001
_DIMENSIONS_PATTERN = re.compile(
    rf"([1-9][0-9]{{0,3}})\s*(?P<unit1>{_UNIT})?\s*(?:[x×]|by)\s*([1-9][0-9]{{0,3}})\s*(?P<unit2>{_UNIT})?",  # noqa: RUF001
    re.IGNORECASE,
)


def parse_dimensions(description: str) -> tuple[int, int] | None:
    """Parse an area's stated dimensions into a cell-count rectangle.

    The first match of a feet-by-feet pattern (`30' x 40'`, a multiplication
    sign, `30 feet by 40 feet`) wins; at least one unit marker is required.
    Cells are
    `ceil(feet / 10)` per axis (the spec's 10' cell), minimum 1; the first
    number is width (east-west), the second height (north-south).

    Args:
        description: The area's extracted description.

    Returns:
        `(width, height)` in cells, or `None` when no dimensions are stated.
    """
    for match in _DIMENSIONS_PATTERN.finditer(description):
        if match.group("unit1") is None and match.group("unit2") is None:
            continue
        width_feet, height_feet = int(match.group(1)), int(match.group(3))
        return (max(1, -(-width_feet // 10)), max(1, -(-height_feet // 10)))
    return None


@dataclass(frozen=True)
class LevelGeometry:
    """One level's synthesized geometry, ready for `LevelSpec` assembly.

    Attributes:
        dungeon_id: The canonical dungeon id.
        level_number: The 1-based level number.
        width: The grid width (bounding box).
        height: The grid height (bounding box).
        areas: Area key → cell cluster, in survey order; cells sorted
            row-major (y, then x).
        corridors: Corridor cells (cells in no area), sorted row-major.
        edges: The `open` edge map in osrlib's canonical `edge_key` form,
            keys sorted by (y, x, side).
        entrance: The entrance area's first cell on the dungeon's entrance
            level; `None` elsewhere.
        transitions: This level's transition specs, in link derivation order
            (stairs reciprocal, trapdoors and chutes one-way).
        unresolved_connections: `(area key, flag detail)` pairs geometry
            dropped — assembly emits each detail verbatim as a
            `connection_ambiguous` flag.
        unknown_direction_connections: `(area key, resolved target key)` pairs
            whose extracted direction was `unknown` — assembly flags them.
        disconnected_areas: Area keys joined by a synthetic component link —
            assembly flags them not connected to the entrance.
        guessed_transitions: `(area key, far-end address)` pairs for this
            level's `to_level`-derived transitions — assembly flags them
            `transition_guessed`, the badge that asks a human to confirm or
            correct the landing.
    """

    dungeon_id: str
    level_number: int
    width: int
    height: int
    areas: dict[str, tuple[Position, ...]]
    corridors: tuple[Position, ...]
    edges: dict[str, Edge]
    entrance: Position | None
    transitions: tuple[TransitionSpec, ...]
    unresolved_connections: tuple[tuple[str, str], ...]
    unknown_direction_connections: tuple[tuple[str, str], ...]
    disconnected_areas: tuple[str, ...]
    guessed_transitions: tuple[tuple[str, str], ...] = ()


_DOOR_VIAS = ("door", "secret_door")

_TransitionKind = Literal["stairs_up", "stairs_down", "trapdoor", "chute"]


@dataclass
class _GraphEdge:
    """One undirected intra-level connection, as collapsed from its mentions.

    `owner` is the area whose mention pinned the direction; placing the other
    end as the BFS child uses `direction` when the parent is the owner and its
    opposite otherwise. `direction` is `None` for unknown or vertical mentions.

    `via` is the first *stated* (non-`passage`) mechanism in survey order and
    `via_owner` the area whose mention stated it — the default `passage` is an
    absence, not an assertion, so a later stated mechanism fills it exactly as
    a later stated compass direction fills an earlier `None`. Door conditions
    merge first-stated the same way: on the wire `False` is the unstated
    default, so the first mention stating a condition sets it and nothing
    unsets it; conditions on a non-door `via` are ignored at emission (the
    tolerate-and-flag posture).
    """

    a: str
    b: str
    owner: str
    direction: GridDirection | None
    via: str | None = None
    via_owner: str | None = None
    door_stuck: bool = False
    door_locked: bool = False

    def other(self, key: str) -> str:
        return self.b if key == self.a else self.a

    def placement_direction(self, parent: str) -> GridDirection | None:
        if self.direction is None:
            return None
        return self.direction if parent == self.owner else self.direction.opposite


@dataclass(frozen=True)
class _CrossLevelLink:
    """One vertical link with a keyed target between two levels of the same dungeon.

    `up` is the stated direction of the first mention: `True` means the source
    area's stairs lead up to the target. `via` is the mention's mechanism,
    already narrowed to a transition family (`trapdoor` and `chute` as
    themselves, everything else `stairs` — the overwhelmingly common printed
    mechanism).
    """

    source_level: int
    source_key: str
    target_level: int
    target_key: str
    up: bool
    via: str = "stairs"


@dataclass(frozen=True)
class _LevelLink:
    """One vertical mention with a level-shaped target — no keyed area to land on.

    `down` is the vertical sense: a stated `up`/`down` direction wins;
    otherwise the level numbers decide (a higher target number is down —
    deeper levels number higher throughout the pipeline).
    """

    source_level: int
    source_key: str
    to_level: int
    down: bool
    via: str


@dataclass(frozen=True)
class _RealizedLink:
    """One vertical link ready for cell assignment and transition emission.

    `target_kind` is `None` when no return transition is emitted — trapdoors
    and chutes are one-way by osrlib's design, and synthesizing return stairs
    up a chute would manufacture structure no page states. `flags` carries
    `(level number, area key, far-end address)` triples assembly turns into
    `transition_guessed` flags.
    """

    source_level: int
    source_key: str
    target_level: int
    target_key: str
    source_kind: _TransitionKind
    target_kind: _TransitionKind | None
    flags: tuple[tuple[int, str, str], ...] = ()


@dataclass
class _LevelResolution:
    """One level's resolved connection data, before placement.

    `unresolved` pairs are `(area key, flag detail)` — geometry owns the
    detail wording (`unresolved target <key>`, `no target stated`,
    `level <N>`, `door to <key> not placed`) and assembly emits each verbatim
    as a `connection_ambiguous` flag.
    """

    edges: list[_GraphEdge]
    unresolved: list[tuple[str, str]]
    unknown_direction: list[tuple[str, str]]


def _resolve_target(to_key: str, level_keys: Sequence[str]) -> str | None:
    """Resolve a connection target on one level: exact canonical key, else slug match."""
    if to_key in level_keys:
        return to_key
    slug = canonical_slug(to_key)
    if slug and slug in level_keys:
        return slug
    return None


def _transition_via(via: str) -> str:
    """Narrow a mention's `via` to a transition family: trapdoors and chutes as themselves, else stairs."""
    return via if via in ("trapdoor", "chute") else "stairs"


def _resolve_dungeon_connections(
    dungeon: SurveyDungeon, contents: dict[tuple[str, int], LevelContent]
) -> tuple[dict[int, _LevelResolution], list[_CrossLevelLink], list[_LevelLink]]:
    """Resolve every connection mention in one dungeon, in survey order.

    Duplicate mentions of an area pair collapse to one undirected edge whose
    direction is the first *stated* compass direction in survey order (a
    reverse-only mention is inverted at placement); mentions that state no
    compass direction — `unknown`, or a vertical direction resolving within
    one level (a 2D grid has no third axis) — leave the edge direction-unknown.
    The stated mechanism and door conditions merge with the same upgrade
    clause (see `_GraphEdge`). Self-connections are dropped. A vertical
    mention unresolved locally tries the sibling levels (exact over all, then
    slug over all, in survey order); a hit is a cross-level link, deduplicated
    by area pair with the first mention winning. A mention with `to_level` and
    no `to_key` is a level link, validated and landed by
    `_realize_level_links`. A mention with neither target is dropped with
    `no target stated`; anything else unresolved is dropped with its target in
    the detail — both surface as assembly's `connection_ambiguous` flags.
    """
    level_keys = {level.number: [area.key for area in level.areas] for level in dungeon.levels}
    resolutions: dict[int, _LevelResolution] = {}
    links: list[_CrossLevelLink] = []
    level_links: list[_LevelLink] = []
    linked_pairs: set[frozenset[tuple[int, str]]] = set()
    for level in dungeon.levels:
        resolution = _LevelResolution(edges=[], unresolved=[], unknown_direction=[])
        resolutions[level.number] = resolution
        content = contents.get((dungeon.id, level.number))
        contents_by_key = {area.key: area for area in content.areas} if content is not None else {}
        seen_pairs: set[frozenset[str]] = set()
        edge_by_pair: dict[frozenset[str], _GraphEdge] = {}
        for area in level.areas:
            extracted = contents_by_key.get(area.key)
            if extracted is None:
                continue
            for connection in extracted.connections:
                if connection.to_key is None:
                    # The tolerate-and-flag posture: schema-legal junk with
                    # neither target is skipped, never a crash.
                    if connection.to_level is None:
                        resolution.unresolved.append((area.key, "no target stated"))
                        continue
                    if connection.direction in ("up", "down"):
                        down = connection.direction == "down"
                    else:
                        down = connection.to_level > level.number
                    level_links.append(
                        _LevelLink(
                            source_level=level.number,
                            source_key=area.key,
                            to_level=connection.to_level,
                            down=down,
                            via=_transition_via(connection.via),
                        )
                    )
                    continue
                target = _resolve_target(connection.to_key, level_keys[level.number])
                if target is None:
                    if connection.direction in ("up", "down"):
                        hit = _resolve_on_siblings(connection.to_key, dungeon, level.number, level_keys)
                        if hit is not None:
                            sibling_number, sibling_key = hit
                            pair = frozenset(((level.number, area.key), (sibling_number, sibling_key)))
                            if pair not in linked_pairs:
                                linked_pairs.add(pair)
                                links.append(
                                    _CrossLevelLink(
                                        source_level=level.number,
                                        source_key=area.key,
                                        target_level=sibling_number,
                                        target_key=sibling_key,
                                        up=connection.direction == "up",
                                        via=_transition_via(connection.via),
                                    )
                                )
                            continue
                    resolution.unresolved.append((area.key, f"unresolved target {connection.to_key}"))
                    continue
                if target == area.key:
                    continue
                if connection.direction == "unknown":
                    resolution.unknown_direction.append((area.key, target))
                pair = frozenset((area.key, target))
                stated = _COMPASS.get(connection.direction)
                mechanism = connection.via if connection.via != "passage" else None
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    edge = _GraphEdge(a=area.key, b=target, owner=area.key, direction=stated)
                    edge_by_pair[pair] = edge
                    resolution.edges.append(edge)
                else:
                    edge = edge_by_pair[pair]
                    if edge.direction is None and stated is not None:
                        # A later mention states the compass direction an
                        # earlier one lacked: the first *stated* direction wins.
                        edge.direction = stated
                        edge.owner = area.key
                if mechanism is not None and edge.via is None:
                    edge.via = mechanism
                    edge.via_owner = area.key
                if connection.via in _DOOR_VIAS:
                    edge.door_stuck = edge.door_stuck or connection.door_stuck
                    edge.door_locked = edge.door_locked or connection.door_locked
    return resolutions, links, level_links


def _resolve_on_siblings(
    to_key: str,
    dungeon: SurveyDungeon,
    level_number: int,
    level_keys: dict[int, list[str]],
) -> tuple[int, str] | None:
    """Resolve a vertical connection target on the dungeon's other levels: exact pass, then slug pass."""
    siblings = [level.number for level in dungeon.levels if level.number != level_number]
    for sibling in siblings:
        if to_key in level_keys[sibling]:
            return (sibling, to_key)
    slug = canonical_slug(to_key)
    if slug:
        for sibling in siblings:
            if slug in level_keys[sibling]:
                return (sibling, slug)
    return None


def _transition_kinds(via: str, down: bool) -> tuple[_TransitionKind, _TransitionKind | None]:
    """One vertical link's emitted transition kinds, by mechanism and vertical sense.

    Stairs emit the reciprocal return transition; trapdoors and chutes emit
    none — they are one-way by osrlib's design, and synthesizing return stairs
    up a chute would manufacture structure no page states.
    """
    if via == "trapdoor":
        return ("trapdoor", None)
    if via == "chute":
        return ("chute", None)
    return ("stairs_down", "stairs_up") if down else ("stairs_up", "stairs_down")


def _realize_links(
    dungeon: SurveyDungeon,
    links: list[_CrossLevelLink],
    level_links: list[_LevelLink],
    resolutions: dict[int, _LevelResolution],
) -> list[_RealizedLink]:
    """Realize every vertical link: keyed targets as stated, level targets under the total landing policy.

    Keyed links realize directly, kinds from their mechanism and stated sense.
    Level links first validate their target — a level the dungeon doesn't
    have, the source's own level, or a level with zero surveyed areas drops
    the link with `connection_ambiguous:level <N>` on the source area.
    Survivors between the same level pair merge pairwise in survey order when
    their sources sit on opposite levels and their vertical senses oppose —
    each merged pair yields one reciprocal transition between the two
    mentioning areas (both ends were stated as stair-bearing; the guess is
    only that they are the same stairway). Leftover links land on the target
    level's first keyed area in survey order. Every `to_level`-derived
    transition flags its source area with the chosen far end's address —
    merged pairs flag both mentioning areas — and the geometry `transitions`
    override corrects any landing.
    """
    realized = [
        _RealizedLink(
            source_level=link.source_level,
            source_key=link.source_key,
            target_level=link.target_level,
            target_key=link.target_key,
            source_kind=_transition_kinds(link.via, down=not link.up)[0],
            target_kind=_transition_kinds(link.via, down=not link.up)[1],
        )
        for link in links
    ]
    numbers = {level.number for level in dungeon.levels}
    first_keys = {level.number: level.areas[0].key for level in dungeon.levels if level.areas}
    surviving: list[_LevelLink] = []
    for link in level_links:
        if link.to_level not in numbers or link.to_level == link.source_level or link.to_level not in first_keys:
            resolutions[link.source_level].unresolved.append((link.source_key, f"level {link.to_level}"))
            continue
        surviving.append(link)
    used = [False] * len(surviving)
    for position, one in enumerate(surviving):
        if used[position]:
            continue
        used[position] = True
        partner: _LevelLink | None = None
        for later in range(position + 1, len(surviving)):
            two = surviving[later]
            if (
                not used[later]
                and two.source_level == one.to_level
                and two.to_level == one.source_level
                and two.down != one.down
            ):
                used[later] = True
                partner = two
                break
        if partner is not None:
            realized.append(
                _RealizedLink(
                    source_level=one.source_level,
                    source_key=one.source_key,
                    target_level=partner.source_level,
                    target_key=partner.source_key,
                    source_kind=_transition_kinds(one.via, one.down)[0],
                    target_kind=_transition_kinds(partner.via, partner.down)[0],
                    flags=(
                        (
                            one.source_level,
                            one.source_key,
                            f"{dungeon.id}/{partner.source_level}/{partner.source_key}",
                        ),
                        (
                            partner.source_level,
                            partner.source_key,
                            f"{dungeon.id}/{one.source_level}/{one.source_key}",
                        ),
                    ),
                )
            )
            continue
        landing = first_keys[one.to_level]
        source_kind, target_kind = _transition_kinds(one.via, one.down)
        realized.append(
            _RealizedLink(
                source_level=one.source_level,
                source_key=one.source_key,
                target_level=one.to_level,
                target_key=landing,
                source_kind=source_kind,
                target_kind=target_kind,
                flags=((one.source_level, one.source_key, f"{dungeon.id}/{one.to_level}/{landing}"),),
            )
        )
    return realized


def _room_size(key: str, content_by_key: dict[str, str]) -> tuple[int, int]:
    description = content_by_key.get(key)
    if description is not None:
        parsed = parse_dimensions(description)
        if parsed is not None:
            return parsed
    return DEFAULT_ROOM_CELLS


def _rect_cells(origin: Position, size: tuple[int, int]) -> tuple[Position, ...]:
    """A rectangle's cells from its northwest corner, sorted row-major."""
    x0, y0 = origin
    width, height = size
    return tuple((x0 + dx, y0 + dy) for dy in range(height) for dx in range(width))


def _facing_midpoint(cells: Iterable[Position], direction: GridDirection) -> Position:
    """The midpoint cell of a room's side facing `direction` (smaller coordinate on even sides)."""
    cells = list(cells)
    if direction is GridDirection.NORTH:
        edge_coord = min(y for _, y in cells)
        side = sorted(x for x, y in cells if y == edge_coord)
        return (side[(len(side) - 1) // 2], edge_coord)
    if direction is GridDirection.SOUTH:
        edge_coord = max(y for _, y in cells)
        side = sorted(x for x, y in cells if y == edge_coord)
        return (side[(len(side) - 1) // 2], edge_coord)
    if direction is GridDirection.WEST:
        edge_coord = min(x for x, _ in cells)
        side = sorted(y for x, y in cells if x == edge_coord)
        return (edge_coord, side[(len(side) - 1) // 2])
    edge_coord = max(x for x, _ in cells)
    side = sorted(y for x, y in cells if x == edge_coord)
    return (edge_coord, side[(len(side) - 1) // 2])


def _child_rect(child_mid: Position, size: tuple[int, int], direction: GridDirection) -> tuple[Position, ...]:
    """The child rectangle whose parent-facing side's midpoint is `child_mid`."""
    width, height = size
    x, y = child_mid
    if direction is GridDirection.EAST:
        return _rect_cells((x, y - (height - 1) // 2), size)
    if direction is GridDirection.WEST:
        return _rect_cells((x - width + 1, y - (height - 1) // 2), size)
    if direction is GridDirection.SOUTH:
        return _rect_cells((x - (width - 1) // 2, y), size)
    return _rect_cells((x - (width - 1) // 2, y - height + 1), size)


def _direction_first_path(start: Position, end: Position, direction: GridDirection) -> list[Position]:
    """The 1-cell-wide Manhattan path from `start` to `end`, moving along `direction` first."""
    path = [start]
    x, y = start
    dx, dy = direction.vector
    if dx:
        while x != end[0]:
            x += dx
            path.append((x, y))
        while y != end[1]:
            y += 1 if end[1] > y else -1
            path.append((x, y))
    else:
        while y != end[1]:
            y += dy
            path.append((x, y))
        while x != end[0]:
            x += 1 if end[0] > x else -1
            path.append((x, y))
    return path


def _offsets(bound: int) -> Iterable[int]:
    yield 0
    for magnitude in range(1, bound + 1):
        yield magnitude
        yield -magnitude


def _first_free_candidate(
    parent_cells: tuple[Position, ...],
    child_size: tuple[int, int],
    direction: GridDirection,
    occupied: set[Position],
) -> tuple[tuple[Position, ...], list[Position]] | None:
    """The first free placement in one direction, in the pinned candidate order.

    Candidates enumerate by increasing corridor length from 1, then lateral
    offset 0, +1, -1, +2, … perpendicular to the direction; the first whose
    room cells *and* corridor cells are all unoccupied wins. The corridor is
    the direction-first Manhattan path from the parent's facing-side midpoint
    to the child's. The enumeration is bounded by the occupied region's span
    plus the child size — beyond that bound every candidate the search could
    still find would be strictly farther than one it already rejected, so an
    exhausted bound means the direction is genuinely blocked.
    """
    parent_mid = _facing_midpoint(parent_cells, direction)
    xs = [x for x, _ in occupied]
    ys = [y for _, y in occupied]
    span = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    length_bound = (span[0] if direction.vector[0] else span[1]) + max(child_size) + 2
    offset_bound = (span[1] if direction.vector[0] else span[0]) + max(child_size) + 2
    perpendicular = (0, 1) if direction.vector[0] else (1, 0)
    for length in range(1, length_bound + 1):
        for offset in _offsets(offset_bound):
            child_mid = (
                parent_mid[0] + (length + 1) * direction.vector[0] + offset * perpendicular[0],
                parent_mid[1] + (length + 1) * direction.vector[1] + offset * perpendicular[1],
            )
            child_cells = _child_rect(child_mid, child_size, direction)
            child_set = set(child_cells)
            path = _direction_first_path(parent_mid, child_mid, direction)
            corridor = [cell for cell in path[1:] if cell not in child_set]
            if any(cell in occupied for cell in child_cells) or any(cell in occupied for cell in corridor):
                continue
            return child_cells, path
    return None


def _routed_candidate(
    parent_cells: tuple[Position, ...],
    child_size: tuple[int, int],
    occupied: set[Position],
) -> tuple[tuple[Position, ...], list[Position]] | None:
    """The routed fallback: a BFS-shortest free-cell corridor to the first place the child fits.

    The straight-with-one-bend candidate family can exhaust on real data — a
    six-connection hub's later children find every single-bend route walled by
    earlier siblings (JN1's cave-of-horrors) — so when every direction
    exhausts, the corridor is routed instead: a multi-source BFS over free
    cells (sources are the free cells orthogonally adjacent to the parent, in
    row-major parent-cell then pinned direction order; neighbours expand in
    the pinned direction order), and the first dequeued cell against which the
    child rectangle fits (tried in the pinned direction order, overlapping
    neither occupied cells nor the corridor) wins. FIFO order plus the pinned
    tie-breaks keep it deterministic. The search is windowed to the occupied
    bounding box plus a child-sized margin: free space outside the box is
    unobstructed, so a reachable placement always exists inside the window
    unless the parent is genuinely walled in.
    """
    xs = [x for x, _ in occupied]
    ys = [y for _, y in occupied]
    margin = max(child_size) + 2
    window = (min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin)
    parent_set = set(parent_cells)
    came_from: dict[Position, Position] = {}
    queue: list[Position] = []
    for cell in sorted(parent_cells, key=lambda cell: (cell[1], cell[0])):
        for direction in _PLACEMENT_ORDER:
            seed = step(cell, direction)
            if seed in occupied or seed in came_from:
                continue
            came_from[seed] = cell
            queue.append(seed)
    while queue:
        current = queue.pop(0)
        path = [current]
        while path[-1] not in parent_set:
            path.append(came_from[path[-1]])
        path.reverse()  # parent cell first, `current` last
        for direction in _PLACEMENT_ORDER:
            child_mid = step(current, direction)
            child_cells = _child_rect(child_mid, child_size, direction)
            blocked = set(occupied) | set(path)
            if any(cell in blocked for cell in child_cells):
                continue
            return child_cells, [*path, child_mid]
        for direction in _PLACEMENT_ORDER:
            neighbor = step(current, direction)
            in_window = window[0] <= neighbor[0] <= window[1] and window[2] <= neighbor[1] <= window[3]
            if neighbor in occupied or neighbor in came_from or not in_window:
                continue
            came_from[neighbor] = current
            queue.append(neighbor)
    return None


def _place_child(
    parent_cells: tuple[Position, ...],
    child_size: tuple[int, int],
    direction: GridDirection | None,
    occupied: set[Position],
) -> tuple[tuple[Position, ...], list[Position]] | None:
    """Place one child room: the stated direction, the pinned fallback order, then the routed fallback.

    Returns None when every route out of the parent is walled by earlier
    placements — on dense graphs a hub's corridors can enclose it completely
    (a fresh JN1 extraction's manor level did exactly this, phase 4's baseline
    sweep) — and the caller re-anchors the child on another placed room.
    """
    tried: list[GridDirection] = [direction] if direction is not None else []
    tried.extend(d for d in _PLACEMENT_ORDER if d not in tried)
    for candidate_direction in tried:
        candidate = _first_free_candidate(parent_cells, child_size, candidate_direction, occupied)
        if candidate is not None:
            return candidate
    return _routed_candidate(parent_cells, child_size, occupied)


@dataclass
class _Placement:
    """One level's placement state: raw-coordinate cells before normalization.

    `edge_paths` maps each graph edge the router realized *as stated* (child
    placed from its graph parent — not re-anchored, not a cycle edge) to its
    corridor path, the association door synthesis reads.
    """

    rooms: dict[str, tuple[Position, ...]]
    paths: list[list[Position]]
    edge_paths: dict[frozenset[str], list[Position]] = field(default_factory=dict[frozenset[str], list[Position]])


def _order_components(
    survey_keys: Sequence[str], adjacency: dict[str, list[_GraphEdge]], anchor: str
) -> list[list[str]]:
    """Connected components, each in survey order: the anchor's first, the rest by first area."""
    position = {key: index for index, key in enumerate(survey_keys)}
    unseen = set(survey_keys)
    components: list[list[str]] = []
    for key in survey_keys:
        if key not in unseen:
            continue
        component: list[str] = []
        queue = [key]
        unseen.discard(key)
        while queue:
            current = queue.pop(0)
            component.append(current)
            for graph_edge in adjacency[current]:
                neighbor = graph_edge.other(current)
                if neighbor in unseen:
                    unseen.discard(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component, key=lambda k: position[k]))
    components.sort(key=lambda component: (anchor not in component, position[component[0]]))
    return components


def _place_level(
    survey_keys: Sequence[str],
    sizes: dict[str, tuple[int, int]],
    graph_edges: list[_GraphEdge],
    anchor: str,
) -> tuple[_Placement, tuple[str, ...]]:
    """BFS placement of one level from its anchor; returns the placement and the synthetic-linked areas."""
    position = {key: index for index, key in enumerate(survey_keys)}
    adjacency: dict[str, list[_GraphEdge]] = {key: [] for key in survey_keys}
    for graph_edge in graph_edges:
        adjacency[graph_edge.a].append(graph_edge)
        adjacency[graph_edge.b].append(graph_edge)
    components = _order_components(survey_keys, adjacency, anchor)
    disconnected: list[str] = []
    for previous, component in pairwise(components):
        synthetic = _GraphEdge(a=component[0], b=previous[0], owner=component[0], direction=None)
        adjacency[synthetic.a].append(synthetic)
        adjacency[synthetic.b].append(synthetic)
        disconnected.extend(component)
    for key in survey_keys:
        adjacency[key].sort(key=lambda graph_edge: position[graph_edge.other(key)])

    placement = _Placement(rooms={}, paths=[])
    occupied: set[Position] = set()
    anchor_cells = _rect_cells((0, 0), sizes[anchor])
    placement.rooms[anchor] = anchor_cells
    occupied.update(anchor_cells)
    queue = [anchor]
    while queue:
        parent = queue.pop(0)
        for graph_edge in adjacency[parent]:
            child = graph_edge.other(parent)
            if child in placement.rooms:
                continue
            placed = _place_child(
                placement.rooms[parent], sizes[child], graph_edge.placement_direction(parent), occupied
            )
            re_anchored = False
            if placed is None:
                # The parent walled itself in: re-anchor on the earliest
                # placed room with a free way out (dict order is placement
                # order — deterministic). The child joins the component there
                # instead of at its graph parent; synthesized geometry is
                # approximate by charter, and the room stays reachable, which
                # is the postcondition that matters. A re-anchored route does
                # not realize the stated connection, so it never carries its
                # door (case b of the door rule).
                re_anchored = True
                for fallback_parent, fallback_cells in placement.rooms.items():
                    if fallback_parent == parent:
                        continue
                    placed = _place_child(fallback_cells, sizes[child], None, occupied)
                    if placed is not None:
                        break
            if placed is None:
                raise AssertionError(f"placement exhausted: no placed room has a free corridor route to {child!r}")
            child_cells, path = placed
            placement.rooms[child] = child_cells
            occupied.update(child_cells)
            occupied.update(path)
            placement.paths.append(path)
            if not re_anchored:
                placement.edge_paths[frozenset((graph_edge.a, graph_edge.b))] = path
            queue.append(child)
    return placement, tuple(disconnected)


def _normalize_placement(
    placement: _Placement,
) -> tuple[dict[str, tuple[Position, ...]], list[list[Position]], dict[frozenset[str], list[Position]]]:
    """Translate all cells so the minimum coordinate is (0, 0)."""
    all_cells = [cell for cells in placement.rooms.values() for cell in cells]
    all_cells.extend(cell for path in placement.paths for cell in path)
    min_x = min(x for x, _ in all_cells)
    min_y = min(y for _, y in all_cells)
    rooms = {
        key: tuple(sorted(((x - min_x, y - min_y) for x, y in cells), key=lambda cell: (cell[1], cell[0])))
        for key, cells in placement.rooms.items()
    }
    paths = [[(x - min_x, y - min_y) for x, y in path] for path in placement.paths]
    edge_paths = {pair: [(x - min_x, y - min_y) for x, y in path] for pair, path in placement.edge_paths.items()}
    return rooms, paths, edge_paths


def _edge_direction(from_cell: Position, to_cell: Position) -> GridDirection:
    for direction in _PLACEMENT_ORDER:
        if step(from_cell, direction) == to_cell:
            return direction
    raise AssertionError(f"cells {from_cell} and {to_cell} are not orthogonally adjacent")


def edge_sort_key(key: str) -> tuple[int, int, str]:
    """The pinned edge-map key ordering — `(y, x, side)` over canonical keys.

    Synthesis emits its edge map in this order, and override application
    (phase 3) re-sorts the merged map with it, so the serialized `edges` dict
    stays byte-stable regardless of where an edge came from.

    Args:
        key: A canonical edge key, `x,y:side`.

    Returns:
        The sort key.
    """
    coordinates, _, side = key.partition(":")
    x, _, y = coordinates.partition(",")
    return (int(y), int(x), side)


def _door_edges(
    graph_edges: Sequence[_GraphEdge],
    rooms: dict[str, tuple[Position, ...]],
    edge_paths: dict[frozenset[str], list[Position]],
    unresolved: list[tuple[str, str]],
) -> dict[str, Edge]:
    """Realize stated doors onto route edges — the total rule over the three materializations.

    (a) A connection the router realized as its own tree edge carries its door
    on the first edge the route opens leaving the door-stating area's cluster
    — the prose describes the door in the wall of the room being described, so
    the source end is the faithful placement (the arriving end of the route
    when the source was placed as the child). (b) A re-anchored child's route
    joins another room, so the stated door has no edge on the described wall;
    (c) a cycle-closing connection places no route at all. Both drop the door
    fact with `connection_ambiguous:door to <key> not placed` on the source
    area — the geometry `edges` override is the designed remedy, and the flag
    is what tells the human to reach for it.
    """
    doors: dict[str, Edge] = {}
    for graph_edge in graph_edges:
        if graph_edge.via not in _DOOR_VIAS or graph_edge.via_owner is None:
            continue
        owner = graph_edge.via_owner
        target = graph_edge.other(owner)
        path = edge_paths.get(frozenset((graph_edge.a, graph_edge.b)))
        pair: tuple[Position, Position] | None = None
        if path is not None and len(path) >= 2:
            owner_cells = set(rooms[owner])
            if path[0] in owner_cells and path[1] not in owner_cells:
                pair = (path[0], path[1])
            elif path[-1] in owner_cells and path[-2] not in owner_cells:
                pair = (path[-2], path[-1])
        if pair is None:
            unresolved.append((owner, f"door to {target} not placed"))
            continue
        door = DoorSpec(
            kind="secret" if graph_edge.via == "secret_door" else "normal",
            stuck=graph_edge.door_stuck,
            locked=graph_edge.door_locked,
        )
        doors[edge_key(pair[0], _edge_direction(pair[0], pair[1]))] = Edge(kind=EdgeKind.DOOR, door=door)
    return doors


def _open_edges(rooms: dict[str, tuple[Position, ...]], paths: list[list[Position]]) -> dict[str, Edge]:
    """The `open` edge map: within-room adjacency plus every corridor path, canonically keyed."""
    keys: set[str] = set()
    for cells in rooms.values():
        cell_set = set(cells)
        for cell in cells:
            for direction in (GridDirection.EAST, GridDirection.SOUTH):
                if step(cell, direction) in cell_set:
                    keys.add(edge_key(cell, direction))
    for path in paths:
        for from_cell, to_cell in pairwise(path):
            keys.add(edge_key(from_cell, _edge_direction(from_cell, to_cell)))
    return {key: Edge(kind=EdgeKind.OPEN) for key in sorted(keys, key=edge_sort_key)}


def synthesize_geometry(index: SurveyIndex, levels: Sequence[LevelContent]) -> tuple[LevelGeometry, ...]:
    """Synthesize every level's geometry, in survey order.

    Args:
        index: The normalized survey index.
        levels: The available content caches; a level absent here gets
            default-sized rooms and no connections. Assembly and the preview
            path both enforce cache completeness upstream — the tolerance
            serves direct callers (tests, future partial-cache paths).

    Returns:
        One result per survey level, in survey order, postconditions asserted.
    """
    contents = {(level.dungeon_id, level.level_number): level for level in levels}
    results: list[LevelGeometry] = []
    for dungeon in index.dungeons:
        resolutions, links, level_links = _resolve_dungeon_connections(dungeon, contents)
        realized = _realize_links(dungeon, links, level_links, resolutions)
        # The dungeon's entrance lives on its lowest-numbered level (with any
        # areas at all) — survey order is not guaranteed number-sorted.
        entrance_level = min((level.number for level in dungeon.levels if level.areas), default=None)
        transition_areas: dict[int, list[str]] = {}
        for link in realized:
            transition_areas.setdefault(link.source_level, []).append(link.source_key)
            transition_areas.setdefault(link.target_level, []).append(link.target_key)

        placed: dict[
            int,
            tuple[
                dict[str, tuple[Position, ...]],
                list[list[Position]],
                dict[frozenset[str], list[Position]],
                tuple[str, ...],
            ],
        ] = {}
        for level in dungeon.levels:
            if not level.areas:
                placed[level.number] = ({}, [], {}, ())
                continue
            survey_keys = [area.key for area in level.areas]
            content = contents.get((dungeon.id, level.number))
            descriptions = {area.key: area.description for area in content.areas} if content is not None else {}
            sizes = {key: _room_size(key, descriptions) for key in survey_keys}
            targets = transition_areas.get(level.number, ())
            if level.number == entrance_level or not targets:
                anchor = survey_keys[0]
            else:
                anchor = min(targets, key=survey_keys.index)
            placement, disconnected = _place_level(survey_keys, sizes, resolutions[level.number].edges, anchor)
            rooms, paths, edge_paths = _normalize_placement(placement)
            placed[level.number] = (rooms, paths, edge_paths, disconnected)

        # An area's transition cells assign in link derivation order: its Nth
        # transition takes its Nth cell (wrapping) — an area carrying both up-
        # and down-stairs would otherwise stack two specs on one cell, and
        # osrlib's `transition_at` returns only the first, shadowing the
        # second staircase in play. Both sides of a link share the assignment
        # so the reciprocal pair stays aligned (`UseStairs` needs a transition
        # back on the arrival cell).
        occupancy: dict[tuple[int, str], int] = {}
        link_positions: list[tuple[Position, Position]] = []
        for link in realized:
            ends: list[Position] = []
            for level_number, area_key in ((link.source_level, link.source_key), (link.target_level, link.target_key)):
                cells = placed[level_number][0][area_key]
                slot = occupancy.get((level_number, area_key), 0)
                occupancy[(level_number, area_key)] = slot + 1
                ends.append(cells[slot % len(cells)])
            link_positions.append((ends[0], ends[1]))

        guessed: dict[int, list[tuple[str, str]]] = {}
        for link in realized:
            for flag_level, area_key, detail in link.flags:
                guessed.setdefault(flag_level, []).append((area_key, detail))

        for level in dungeon.levels:
            rooms, paths, edge_paths, disconnected = placed[level.number]
            room_cells = {cell for cells in rooms.values() for cell in cells}
            corridor_cells = sorted(
                {cell for path in paths for cell in path if cell not in room_cells},
                key=lambda cell: (cell[1], cell[0]),
            )
            all_cells = room_cells | set(corridor_cells)
            width = max((x for x, _ in all_cells), default=0) + 1
            height = max((y for _, y in all_cells), default=0) + 1
            resolution = resolutions[level.number]
            edges = _open_edges(rooms, paths)
            # Door replacement keeps the sorted key order: every door key is a
            # route edge `_open_edges` already emitted.
            edges.update(_door_edges(resolution.edges, rooms, edge_paths, resolution.unresolved))
            transitions: list[TransitionSpec] = []
            for link, (source_position, target_position) in zip(realized, link_positions, strict=True):
                if link.source_level == level.number:
                    transitions.append(
                        TransitionSpec(
                            kind=link.source_kind,
                            position=source_position,
                            to_dungeon_id=dungeon.id,
                            to_level_number=link.target_level,
                            to_position=target_position,
                            to_facing=GridDirection.NORTH,
                        )
                    )
                if link.target_kind is not None and link.target_level == level.number:
                    transitions.append(
                        TransitionSpec(
                            kind=link.target_kind,
                            position=target_position,
                            to_dungeon_id=dungeon.id,
                            to_level_number=link.source_level,
                            to_position=source_position,
                            to_facing=GridDirection.NORTH,
                        )
                    )
            entrance = None
            if level.number == entrance_level:
                entrance = rooms[level.areas[0].key][0]
            geometry = LevelGeometry(
                dungeon_id=dungeon.id,
                level_number=level.number,
                width=width,
                height=height,
                areas={area.key: rooms[area.key] for area in level.areas},
                corridors=tuple(corridor_cells),
                edges=edges,
                entrance=entrance,
                transitions=tuple(transitions),
                unresolved_connections=tuple(resolution.unresolved),
                unknown_direction_connections=tuple(resolution.unknown_direction),
                disconnected_areas=disconnected,
                guessed_transitions=tuple(guessed.get(level.number, ())),
            )
            results.append(geometry)
    _assert_postconditions(results)
    return tuple(results)


def _assert_postconditions(results: Sequence[LevelGeometry]) -> None:
    """The structural postconditions that make `validate_adventure`'s geometry checks pass by construction."""
    by_address = {(geometry.dungeon_id, geometry.level_number): geometry for geometry in results}
    entrance_dungeons = {geometry.dungeon_id for geometry in results if geometry.entrance is not None}
    populated_dungeons = {geometry.dungeon_id for geometry in results if geometry.areas}
    assert populated_dungeons <= entrance_dungeons, "a dungeon with areas has no entrance level"
    for geometry in results:
        seen: dict[Position, str] = {}
        for key, cells in geometry.areas.items():
            assert cells, f"area {key} has no cells"
            for cell in cells:
                assert cell not in seen, f"cell {cell} is in both {seen[cell]!r} and {key!r}"
                assert 0 <= cell[0] < geometry.width and 0 <= cell[1] < geometry.height, f"{cell} out of bounds"
                seen[cell] = key
        for cell in geometry.corridors:
            assert cell not in seen, f"corridor cell {cell} crosses area {seen[cell]!r}"
            assert 0 <= cell[0] < geometry.width and 0 <= cell[1] < geometry.height, f"{cell} out of bounds"
        if geometry.entrance is not None:
            assert geometry.entrance in seen, "the entrance is not an area cell"
        for transition in geometry.transitions:
            assert transition.position in seen, "a transition position is not an area cell"
            target = by_address.get((transition.to_dungeon_id, transition.to_level_number))
            assert target is not None, "a transition targets a missing level"
            target_cells = {cell for cells in target.areas.values() for cell in cells}
            assert transition.to_position in target_cells, "a transition targets a missing cell"
        _assert_reachable(geometry)


def _assert_reachable(geometry: LevelGeometry) -> None:
    """Every area cell is reachable from the level's first area cell through open edges."""
    if not geometry.areas:
        return
    all_cells = {cell for cells in geometry.areas.values() for cell in cells} | set(geometry.corridors)
    start = next(iter(geometry.areas.values()))[0]
    visited = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        for direction in _PLACEMENT_ORDER:
            neighbor = step(current, direction)
            if neighbor in visited or neighbor not in all_cells:
                continue
            if edge_key(current, direction) in geometry.edges:
                visited.add(neighbor)
                queue.append(neighbor)
    unreached = {key for key, cells in geometry.areas.items() if not any(cell in visited for cell in cells)}
    assert not unreached, f"areas unreachable through open edges: {sorted(unreached)}"
