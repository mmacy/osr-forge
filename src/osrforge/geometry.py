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
room-corridor junction. All synthesized edges are `open` in v1; doors arrive
through geometry overrides (phase 3) or a future vision pass.

Every choice below is pinned for determinism: BFS visit order, candidate
placement order, component ordering, edge-key ordering, and row-major cell
sorting — the byte-stability tests rely on all of them.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from osrlib.crawl.dungeon import Direction as GridDirection
from osrlib.crawl.dungeon import Edge, EdgeKind, Position, TransitionSpec, edge_key, step

from osrforge.contracts.stages import AreaConnection, LevelContent, SurveyDungeon, SurveyIndex
from osrforge.survey import canonical_slug

__all__ = [
    "DEFAULT_ROOM_CELLS",
    "LevelGeometry",
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
        transitions: This level's reciprocal transition specs, in link
            derivation order.
        unresolved_connections: `(area key, to_key)` pairs geometry dropped —
            assembly flags them `connection_ambiguous`.
        unknown_direction_connections: `(area key, resolved target key)` pairs
            whose extracted direction was `unknown` — assembly flags them.
        disconnected_areas: Area keys joined by a synthetic component link —
            assembly flags them not connected to the entrance.
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


@dataclass(frozen=True)
class _GraphEdge:
    """One undirected intra-level connection, as collapsed from its mentions.

    `owner` is the area whose mention pinned the direction; placing the other
    end as the BFS child uses `direction` when the parent is the owner and its
    opposite otherwise. `direction` is `None` for unknown or vertical mentions.
    """

    a: str
    b: str
    owner: str
    direction: GridDirection | None

    def other(self, key: str) -> str:
        return self.b if key == self.a else self.a

    def placement_direction(self, parent: str) -> GridDirection | None:
        if self.direction is None:
            return None
        return self.direction if parent == self.owner else self.direction.opposite


@dataclass(frozen=True)
class _CrossLevelLink:
    """One vertical link between two levels of the same dungeon.

    `up` is the stated direction of the first mention: `True` means the source
    area's stairs lead up to the target.
    """

    source_level: int
    source_key: str
    target_level: int
    target_key: str
    up: bool


@dataclass
class _LevelResolution:
    """One level's resolved connection data, before placement."""

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


def _resolve_dungeon_connections(
    dungeon: SurveyDungeon, contents: dict[tuple[str, int], LevelContent]
) -> tuple[dict[int, _LevelResolution], list[_CrossLevelLink]]:
    """Resolve every connection mention in one dungeon, in survey order.

    Duplicate mentions of an area pair collapse to one undirected edge whose
    direction is the first *stated* compass direction in survey order (a
    reverse-only mention is inverted at placement); mentions that state no
    compass direction — `unknown`, or a vertical direction resolving within
    one level (a 2D grid has no third axis) — leave the edge direction-unknown.
    Self-connections are dropped. A vertical mention unresolved locally tries
    the sibling levels (exact over all, then slug over all, in survey order);
    a hit is a cross-level link, deduplicated by area pair with the first
    mention winning. Anything still unresolved is dropped and reported for
    assembly's `connection_ambiguous` flags.
    """
    level_keys = {level.number: [area.key for area in level.areas] for level in dungeon.levels}
    resolutions: dict[int, _LevelResolution] = {}
    links: list[_CrossLevelLink] = []
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
                target = _resolve_target(connection.to_key, level_keys[level.number])
                if target is None:
                    if connection.direction in ("up", "down"):
                        hit = _resolve_on_siblings(connection, dungeon, level.number, level_keys)
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
                                    )
                                )
                            continue
                    resolution.unresolved.append((area.key, connection.to_key))
                    continue
                if target == area.key:
                    continue
                if connection.direction == "unknown":
                    resolution.unknown_direction.append((area.key, target))
                pair = frozenset((area.key, target))
                stated = _COMPASS.get(connection.direction)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    edge_by_pair[pair] = _GraphEdge(a=area.key, b=target, owner=area.key, direction=stated)
                    resolution.edges.append(edge_by_pair[pair])
                elif stated is not None and edge_by_pair[pair].direction is None:
                    # A later mention states the compass direction an earlier
                    # one lacked: the first *stated* direction wins.
                    upgraded = _GraphEdge(
                        a=edge_by_pair[pair].a, b=edge_by_pair[pair].b, owner=area.key, direction=stated
                    )
                    resolution.edges[resolution.edges.index(edge_by_pair[pair])] = upgraded
                    edge_by_pair[pair] = upgraded
    return resolutions, links


def _resolve_on_siblings(
    connection: AreaConnection,
    dungeon: SurveyDungeon,
    level_number: int,
    level_keys: dict[int, list[str]],
) -> tuple[int, str] | None:
    """Resolve a vertical connection target on the dungeon's other levels: exact pass, then slug pass."""
    siblings = [level.number for level in dungeon.levels if level.number != level_number]
    for sibling in siblings:
        if connection.to_key in level_keys[sibling]:
            return (sibling, connection.to_key)
    slug = canonical_slug(connection.to_key)
    if slug:
        for sibling in siblings:
            if slug in level_keys[sibling]:
                return (sibling, slug)
    return None


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
) -> tuple[tuple[Position, ...], list[Position]]:
    """Place one child room: the stated direction, the pinned fallback order, then the routed fallback."""
    tried: list[GridDirection] = [direction] if direction is not None else []
    tried.extend(d for d in _PLACEMENT_ORDER if d not in tried)
    for candidate_direction in tried:
        candidate = _first_free_candidate(parent_cells, child_size, candidate_direction, occupied)
        if candidate is not None:
            return candidate
    routed = _routed_candidate(parent_cells, child_size, occupied)
    if routed is not None:
        return routed
    raise AssertionError("no placement candidate in any direction — the parent room is fully enclosed (a code bug)")


@dataclass
class _Placement:
    """One level's placement state: raw-coordinate cells before normalization."""

    rooms: dict[str, tuple[Position, ...]]
    paths: list[list[Position]]


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
            child_cells, path = _place_child(
                placement.rooms[parent], sizes[child], graph_edge.placement_direction(parent), occupied
            )
            placement.rooms[child] = child_cells
            occupied.update(child_cells)
            occupied.update(path)
            placement.paths.append(path)
            queue.append(child)
    return placement, tuple(disconnected)


def _normalize_placement(placement: _Placement) -> tuple[dict[str, tuple[Position, ...]], list[list[Position]]]:
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
    return rooms, paths


def _edge_direction(from_cell: Position, to_cell: Position) -> GridDirection:
    for direction in _PLACEMENT_ORDER:
        if step(from_cell, direction) == to_cell:
            return direction
    raise AssertionError(f"cells {from_cell} and {to_cell} are not orthogonally adjacent")


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

    def sort_key(key: str) -> tuple[int, int, str]:
        coordinates, _, side = key.partition(":")
        x, _, y = coordinates.partition(",")
        return (int(y), int(x), side)

    return {key: Edge(kind=EdgeKind.OPEN) for key in sorted(keys, key=sort_key)}


def synthesize_geometry(index: SurveyIndex, levels: Sequence[LevelContent]) -> tuple[LevelGeometry, ...]:
    """Synthesize every level's geometry, in survey order.

    Args:
        index: The normalized survey index.
        levels: The available content caches; a level absent here gets
            default-sized rooms and no connections (assembly guarantees
            completeness; the preview path tolerates gaps).

    Returns:
        One result per survey level, in survey order, postconditions asserted.
    """
    contents = {(level.dungeon_id, level.level_number): level for level in levels}
    results: list[LevelGeometry] = []
    for dungeon in index.dungeons:
        resolutions, links = _resolve_dungeon_connections(dungeon, contents)
        entrance_level = next((level.number for level in dungeon.levels if level.areas), None)
        transition_areas: dict[int, list[str]] = {}
        for link in links:
            transition_areas.setdefault(link.source_level, []).append(link.source_key)
            transition_areas.setdefault(link.target_level, []).append(link.target_key)

        placed: dict[int, tuple[dict[str, tuple[Position, ...]], list[list[Position]], tuple[str, ...]]] = {}
        for level in dungeon.levels:
            if not level.areas:
                placed[level.number] = ({}, [], ())
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
            rooms, paths = _normalize_placement(placement)
            placed[level.number] = (rooms, paths, disconnected)

        for level in dungeon.levels:
            rooms, paths, disconnected = placed[level.number]
            room_cells = {cell for cells in rooms.values() for cell in cells}
            corridor_cells = sorted(
                {cell for path in paths for cell in path if cell not in room_cells},
                key=lambda cell: (cell[1], cell[0]),
            )
            all_cells = room_cells | set(corridor_cells)
            width = max((x for x, _ in all_cells), default=0) + 1
            height = max((y for _, y in all_cells), default=0) + 1
            transitions: list[TransitionSpec] = []
            for link in links:
                if link.source_level == level.number:
                    transitions.append(
                        TransitionSpec(
                            kind="stairs_up" if link.up else "stairs_down",
                            position=rooms[link.source_key][0],
                            to_dungeon_id=dungeon.id,
                            to_level_number=link.target_level,
                            to_position=placed[link.target_level][0][link.target_key][0],
                            to_facing=GridDirection.NORTH,
                        )
                    )
                if link.target_level == level.number:
                    transitions.append(
                        TransitionSpec(
                            kind="stairs_down" if link.up else "stairs_up",
                            position=rooms[link.target_key][0],
                            to_dungeon_id=dungeon.id,
                            to_level_number=link.source_level,
                            to_position=placed[link.source_level][0][link.source_key][0],
                            to_facing=GridDirection.NORTH,
                        )
                    )
            entrance = None
            if level.number == entrance_level:
                entrance = rooms[level.areas[0].key][0]
            resolution = resolutions[level.number]
            geometry = LevelGeometry(
                dungeon_id=dungeon.id,
                level_number=level.number,
                width=width,
                height=height,
                areas={area.key: rooms[area.key] for area in level.areas},
                corridors=tuple(corridor_cells),
                edges=_open_edges(rooms, paths),
                entrance=entrance,
                transitions=tuple(transitions),
                unresolved_connections=tuple(resolution.unresolved),
                unknown_direction_connections=tuple(resolution.unknown_direction),
                disconnected_areas=disconnected,
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
