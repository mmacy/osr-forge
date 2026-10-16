"""The scorer: how far a trace is from its answer key, one family of numbers at a time.

[`score_trace`][osrforge.tracing.score.score_trace] lines each panel of the
trace up with the key at the whole-cell offset that fits best, then reports
the families below. It scores the trace as repaired and, when the trace has a
`before_repair`, the trace before repair, and each of those twice: over every
cell, and without the cells the key marks as partly floor.

The rules are in `docs/tracing-conventions.md` under "The scorer".
"""

from collections.abc import Hashable, Iterable, Iterator

from pydantic import BaseModel, ConfigDict, Field

from osrforge.tracing.fivefoot import convert_five_foot
from osrforge.tracing.truth import (
    DOOR_LETTERS,
    PASSABLE_LETTERS,
    Area,
    EdgeGrids,
    GeometryTruth,
    Level,
    LevelPanel,
    Panel,
    parse_cell,
    parse_edge,
    rotate_cell,
    rotate_side,
    rotated_size,
)

__all__ = [
    "ALIGNMENT_SEARCH",
    "FAMILIES",
    "AreaOverlap",
    "AreasScore",
    "DoorTally",
    "DoorsScore",
    "EdgeTally",
    "EdgesScore",
    "FloorScore",
    "HalvesScore",
    "JoinsScore",
    "KeysScore",
    "PlacementScore",
    "ReachabilityScore",
    "RepairsScore",
    "ScoreSet",
    "TraceScore",
    "VerdictsScore",
    "score_trace",
]

ALIGNMENT_SEARCH = 3
"""How far, in cells, the alignment looks for each panel's best offset in each direction."""

FAMILIES: tuple[str, ...] = (
    "floor",
    "areas",
    "keys",
    "joins",
    "doors",
    "edges",
    "reachability",
    "entrance",
    "transitions",
    "passages",
    "halves",
    "verdicts",
    "repairs",
)
"""The family names, in the order `ScoreSet` lists them."""


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


class FloorScore(BaseModel):
    """How much of the floor the trace and the key agree on: the `.` cells of the panels, after alignment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_cells: int = Field(ge=0)
    """Floor cells in the key."""

    trace_cells: int = Field(ge=0)
    """Floor cells in the trace, after alignment."""

    both: int = Field(ge=0)
    """Cells that are floor in both."""

    @property
    def overlap(self) -> float | None:
        """`both` over the union, or `None` when neither has floor."""
        return _ratio(self.both, self.key_cells + self.trace_cells - self.both)

    @property
    def perfect(self) -> bool:
        """True when the two floors are the same cells."""
        return self.key_cells == self.trace_cells == self.both


class AreaOverlap(BaseModel):
    """One key's area in the key and in the trace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dungeon: str
    level: int
    key: str
    key_cells: int = Field(ge=0)
    trace_cells: int = Field(ge=0)
    """0 when the trace has no area with this key on this level."""

    both: int = Field(ge=0)

    @property
    def overlap(self) -> float | None:
        """`both` over the union."""
        return _ratio(self.both, self.key_cells + self.trace_cells - self.both)


class AreasScore(BaseModel):
    """Each area's cell overlap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    areas: tuple[AreaOverlap, ...] = ()
    """One entry per area of the key, in key order."""

    extra: tuple[str, ...] = ()
    """Areas the trace has and the key lacks, as `dungeon/level/key`."""

    @property
    def mean_overlap(self) -> float | None:
        """The mean of the areas' overlaps, or `None` with no areas."""
        values = [area.overlap for area in self.areas if area.overlap is not None]
        return None if not values else sum(values) / len(values)

    @property
    def perfect(self) -> bool:
        """True when every area covers the same cells in both and the trace has no extra area."""
        return not self.extra and all(area.overlap == 1.0 for area in self.areas)


class KeysScore(BaseModel):
    """Whether each printed key landed in the right area.

    A key is right when the trace's placed cell for it lies in the key's area
    of the same name, and the key's placed cell lies in the trace's area of
    that name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    right: int = Field(ge=0)
    wrong: tuple[str, ...] = ()
    """The keys that landed wrong, as `dungeon/level/label`."""

    @property
    def perfect(self) -> bool:
        """True when every key landed right."""
        return self.right == self.total


class JoinsScore(BaseModel):
    """Whether the areas adjoin the same way.

    Two areas of one level adjoin when a passable edge or a door joins a cell of
    one to a cell of the other.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_joins: int = Field(ge=0)
    matched: int = Field(ge=0)
    """Joins in both."""

    missing: tuple[str, ...] = ()
    """Joins in the key the trace lacks, as `dungeon/level/key~key`."""

    extra: tuple[str, ...] = ()
    """Joins in the trace the key lacks."""

    @property
    def perfect(self) -> bool:
        """True with nothing missing and nothing extra."""
        return not self.missing and not self.extra


class DoorTally(BaseModel):
    """Precision and recall for one door kind."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: int = Field(ge=0)
    """Doors of this kind in the key."""

    trace: int = Field(ge=0)
    """Doors of this kind in the trace."""

    matched: int = Field(ge=0)
    """Edges that are this kind in both."""

    @property
    def precision(self) -> float | None:
        """The share of the trace's doors the key has."""
        return _ratio(self.matched, self.trace)

    @property
    def recall(self) -> float | None:
        """The share of the key's doors the trace has."""
        return _ratio(self.matched, self.key)

    @property
    def perfect(self) -> bool:
        """True when the two sets are the same edges."""
        return self.key == self.trace == self.matched


class DoorsScore(BaseModel):
    """Doors by kind: plain, locked, and secret."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    door: DoorTally
    locked: DoorTally
    secret: DoorTally

    @property
    def perfect(self) -> bool:
        """True when every kind is perfect."""
        return self.door.perfect and self.locked.perfect and self.secret.perfect


class EdgeTally(BaseModel):
    """Passable or blocked, over a set of edges."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    """Edges in the key's universe: edges that touch floor or water."""

    right: int = Field(ge=0)
    """Edges the trace reads as passable or blocked the way the key does."""

    walls_read_as_open: int = Field(ge=0)
    """Edges blocked in the key and passable in the trace."""

    open_read_as_blocked: int = Field(ge=0)
    """Edges passable in the key and blocked in the trace."""

    @property
    def accuracy(self) -> float | None:
        """`right` over `total`."""
        return _ratio(self.right, self.total)

    @property
    def perfect(self) -> bool:
        """True when every edge is read the way the key reads it."""
        return self.right == self.total


class EdgesScore(BaseModel):
    """Passable or blocked, with cave edges apart from straight walls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    all: EdgeTally
    straight: EdgeTally
    cave: EdgeTally
    """Edges with a cave cell on either side."""

    @property
    def perfect(self) -> bool:
        """True when every edge is right."""
        return self.all.perfect


class ReachabilityScore(BaseModel):
    """Per dungeon, whether the same areas are reachable from the entrance.

    The walk crosses passable edges, doors of every kind, transitions, and
    passages. A dungeon is right when the trace reaches the areas the key
    reaches and no others.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    right: int = Field(ge=0)
    wrong: tuple[str, ...] = ()
    """The dungeons that differ, with what differs, as `dungeon: ...`."""

    @property
    def perfect(self) -> bool:
        """True when every dungeon is right."""
        return self.right == self.total


class PlacementScore(BaseModel):
    """Whether each entrance, transition, or passage stands where the key puts it, leading where the key says."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    """Items in the key."""

    right: int = Field(ge=0)
    """Items the trace has on the same cell with the same kind, side, and destination."""

    extra: int = Field(ge=0)
    """Items in the trace that match nothing in the key."""

    wrong: tuple[str, ...] = ()
    """The key's items the trace lacks, as `dungeon/level/cell`."""

    @property
    def perfect(self) -> bool:
        """True when the two sets are the same."""
        return self.right == self.total and self.extra == 0


class HalvesScore(BaseModel):
    """Whether each pair of halves was joined at the key's offset, or left apart as the key says."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    right: int = Field(ge=0)

    @property
    def perfect(self) -> bool:
        """True when every pair is right."""
        return self.right == self.total


class VerdictsScore(BaseModel):
    """Whether refusals, registration failures, and fallbacks are right.

    A region is right when its verdict, style, scale, north, and legend page
    match the key's. A level is right when its verdict matches.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    regions: int = Field(ge=0)
    regions_right: int = Field(ge=0)
    levels: int = Field(ge=0)
    levels_right: int = Field(ge=0)
    wrong: tuple[str, ...] = ()
    """The regions and levels that differ, with what differs."""

    @property
    def perfect(self) -> bool:
        """True when every region and level is right."""
        return self.regions_right == self.regions and self.levels_right == self.levels


class RepairsScore(BaseModel):
    """What the trace repaired, and whether each repaired edge ended up as the key reads it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    per_level: dict[str, int] = {}
    """Repairs per level, keyed `dungeon/level`, for levels with at least one."""

    edges: int = Field(ge=0)
    """Edges the repairs changed."""

    edges_matching: int = Field(ge=0)
    """Changed edges whose final reading matches the key."""

    @property
    def perfect(self) -> bool:
        """True when every repaired edge matches the key."""
        return self.edges_matching == self.edges


class ScoreSet(BaseModel):
    """Every family, for one way of scoring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    floor: FloorScore
    areas: AreasScore
    keys: KeysScore
    joins: JoinsScore
    doors: DoorsScore
    edges: EdgesScore
    reachability: ReachabilityScore
    entrance: PlacementScore
    transitions: PlacementScore
    passages: PlacementScore
    halves: HalvesScore
    verdicts: VerdictsScore
    repairs: RepairsScore

    def imperfect_families(self) -> tuple[str, ...]:
        """The families that aren't perfect, in `FAMILIES` order.

        Returns:
            The names. Empty when the trace matches the key in every family.
        """
        return tuple(name for name in FAMILIES if not getattr(self, name).perfect)


class TraceScore(BaseModel):
    """A trace scored against its key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    after: ScoreSet
    """The trace as repaired, over every cell."""

    after_whole: ScoreSet
    """The trace as repaired, without the cells the key marks as partly floor and the edges that touch them."""

    before: ScoreSet | None = None
    """The trace before repair, over every cell, or `None` when the trace has no `before_repair`."""

    before_whole: ScoreSet | None = None
    """The trace before repair, without the partly-floor cells."""


type _Cell = tuple[int, int]

type _Edge = tuple[str, int, int]
"""An edge as its axis, the grid line it lies on, and the cell along that line, so both names of an edge agree."""

type _LevelId = tuple[str, int]
"""A level as its dungeon id and number."""

type _Destination = tuple[str, int, _Cell | None]
"""Where a transition or passage leads, in the key's coordinates: no cell when the target level fell back."""

type _Placed = tuple[_LevelId, _Cell, str, _Destination]
"""A transition or passage: its level, its cell, its kind or side, and where it leads."""

_WALKABLE = PASSABLE_LETTERS + DOOR_LETTERS
"""The edge letters the reachability walk crosses."""

_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _edge(cell: _Cell, side: str) -> _Edge:
    x, y = cell
    if side == "north":
        return ("horizontal", y, x)
    if side == "south":
        return ("horizontal", y + 1, x)
    if side == "west":
        return ("vertical", x, y)
    return ("vertical", x + 1, y)


def _edge_cells(edge: _Edge) -> tuple[_Cell, _Cell]:
    """The cells west and east of a vertical edge, or north and south of a horizontal one."""
    axis, line, index = edge
    if axis == "vertical":
        return (line - 1, index), (line, index)
    return (index, line - 1), (index, line)


def _letter(edges: EdgeGrids, edge: _Edge) -> str:
    """An edge's letter, `-` when the edge is off the grid."""
    axis, line, index = edge
    if axis == "vertical":
        if 0 <= index < edges.height and 0 <= line <= edges.width:
            return edges.vertical[index][line]
    elif 0 <= line <= edges.height and 0 <= index < edges.width:
        return edges.horizontal[line][index]
    return "-"


def _shift(edge: _Edge, dx: int, dy: int) -> _Edge:
    axis, line, index = edge
    if axis == "vertical":
        return (axis, line + dx, index + dy)
    return (axis, line + dy, index + dx)


def _grid_edges(width: int, height: int) -> Iterator[_Edge]:
    for index in range(height):
        for line in range(width + 1):
            yield ("vertical", line, index)
    for line in range(height + 1):
        for index in range(width):
            yield ("horizontal", line, index)


def _cells_of(rows: tuple[str, ...], letters: str) -> set[_Cell]:
    return {(x, y) for y, row in enumerate(rows) for x, letter in enumerate(row) if letter in letters}


def _neighbour(cell: _Cell, side: str) -> _Cell:
    x, y = cell
    return {"north": (x, y - 1), "south": (x, y + 1), "west": (x - 1, y), "east": (x + 1, y)}[side]


def _unturn(cell: _Cell, size: tuple[int, int], rotation: int) -> _Cell:
    """The page cell that `rotate_cell` turns onto `cell`, for a panel `size` wide and high in page orientation."""
    x, y = cell
    width, height = size
    if rotation == 0:
        return (x, y)
    if rotation == 90:
        return (y, height - 1 - x)
    if rotation == 180:
        return (width - 1 - x, height - 1 - y)
    return (width - 1 - y, x)


def _alignment(trace: Panel, key: Panel) -> _Cell:
    """The offset `(dx, dy)` that takes the trace panel's page cells onto the key panel's.

    It makes the most floor cells agree, within `ALIGNMENT_SEARCH` cells each
    way, ties going to the smallest `|dx| + |dy|`, then the smallest `dy`, then
    the smallest `dx`.
    """
    trace_floor = _cells_of(trace.cells, ".")
    key_floor = _cells_of(key.cells, ".")
    span = range(-ALIGNMENT_SEARCH, ALIGNMENT_SEARCH + 1)

    def preference(offset: _Cell) -> tuple[int, int, int, int]:
        dx, dy = offset
        agree = sum((x + dx, y + dy) in key_floor for x, y in trace_floor)
        return (-agree, abs(dx) + abs(dy), dy, dx)

    return min(((dx, dy) for dy in span for dx in span), key=preference)


def _joins(level: Level | None) -> set[tuple[str, str]]:
    """The pairs of areas that a passable edge or a door joins, each pair in sorted order."""
    if level is None or level.edges is None:
        return set()
    owner: dict[_Cell, str] = {}
    for name, area in level.areas.items():
        for text in area.cells:
            owner.setdefault(parse_cell(text), name)
    joins: set[tuple[str, str]] = set()
    for cell, name in owner.items():
        for side in ("east", "south"):
            other = owner.get(_neighbour(cell, side))
            if other is not None and other != name and _letter(level.edges, _edge(cell, side)) in _WALKABLE:
                first, second = sorted((name, other))
                joins.add((first, second))
    return joins


def _placement[T: Hashable](key_items: list[tuple[str, T]], trace_items: list[T | None]) -> PlacementScore:
    """Match the key's items, each with its `dungeon/level/cell` label, with the trace's.

    A trace item that can't be mapped onto the key is `None`, and counts as extra.
    """
    found = {item for item in trace_items if item is not None}
    wanted = {item for _label, item in key_items}
    return PlacementScore(
        total=len(key_items),
        right=sum(item in found for _label, item in key_items),
        extra=sum(item is None or item not in wanted for item in trace_items),
        wrong=tuple(label for label, item in key_items if item not in found),
    )


class _Document:
    """A document with its 5-foot panels converted, indexed by panel id and by level."""

    def __init__(self, truth: GeometryTruth) -> None:
        self.truth = truth
        self.panels: dict[str, Panel] = {
            panel.id: convert_five_foot(panel).panel if panel.scale_feet == 5 else panel for panel in truth.panels
        }
        self.levels: dict[_LevelId, Level] = {
            (dungeon.id, level.number): level for dungeon in truth.dungeons for level in dungeon.levels
        }

    def areas(self, level_id: _LevelId) -> dict[str, Area]:
        level = self.levels.get(level_id)
        return {} if level is None else level.areas

    def page_cell(self, level_id: _LevelId, cell: _Cell) -> tuple[LevelPanel, _Cell] | None:
        """The placed panel a level cell lies on, and the cell's place on that panel's page."""
        level = self.levels.get(level_id)
        if level is None:
            return None
        for part in level.panels:
            panel = self.panels[part.panel]
            size = (panel.width, panel.height)
            width, height = rotated_size(size, part.rotation)
            turned = (cell[0] - part.origin[0], cell[1] - part.origin[1])
            if 0 <= turned[0] < width and 0 <= turned[1] < height:
                return part, _unturn(turned, size, part.rotation)
        return None

    def level_cell(self, level_id: _LevelId, panel_id: str, page: _Cell) -> tuple[_Cell, int] | None:
        """Where a panel's page cell lands on a level that places the panel, and the panel's turn there."""
        level = self.levels.get(level_id)
        if level is None:
            return None
        for part in level.panels:
            if part.panel == panel_id:
                panel = self.panels[panel_id]
                x, y = rotate_cell(page, (panel.width, panel.height), part.rotation)
                return (part.origin[0] + x, part.origin[1] + y), part.rotation
        return None


class _Comparison:
    """One trace document lined up with the key, and every family scored from it."""

    def __init__(self, trace: _Document, key: _Document) -> None:
        self.trace = trace
        self.key = key
        self.offsets: dict[str, _Cell] = {
            panel_id: _alignment(trace.panels[panel_id], panel)
            for panel_id, panel in key.panels.items()
            if panel_id in trace.panels
        }
        self.excluded: set[_LevelId] = {
            level_id
            for document in (trace, key)
            for level_id, level in document.levels.items()
            if level.verdict == "fallback"
        }
        """The levels that fell back in either document."""

        self.compared: tuple[_LevelId, ...] = tuple(
            dict.fromkeys(
                level_id for document in (key, trace) for level_id in document.levels if level_id not in self.excluded
            )
        )
        """The levels the resolution families compare: traced wherever they appear, in key order."""

        users: dict[str, set[_LevelId]] = {}
        for document in (trace, key):
            for level_id, level in document.levels.items():
                for part in level.panels:
                    users.setdefault(part.panel, set()).add(level_id)
        self.reading_panels: tuple[str, ...] = tuple(
            panel_id for panel_id in self.offsets if not (users.get(panel_id) and users[panel_id] <= self.excluded)
        )
        """The panels the reading families compare: matched by id and not used only by levels that fell back."""

    def scores(self) -> tuple[ScoreSet, ScoreSet]:
        """The score over every cell, and the score without the key's partly-floor cells."""
        floor, edges, doors = self.reading(whole=False)
        every = ScoreSet(
            floor=floor,
            areas=self.areas(),
            keys=self.keys(),
            joins=self.joins(),
            doors=doors,
            edges=edges,
            reachability=self.reachability(),
            entrance=self.entrance(),
            transitions=self.transitions(),
            passages=self.passages(),
            halves=self.halves(),
            verdicts=self.verdicts(),
            repairs=self.repairs(),
        )
        floor, edges, doors = self.reading(whole=True)
        return every, every.model_copy(update={"floor": floor, "edges": edges, "doors": doors})

    # The reading families compare the panels, in page coordinates.

    def reading(self, *, whole: bool) -> tuple[FloorScore, EdgesScore, DoorsScore]:
        """The floor, edges, and doors families, without the key's partly-floor cells and their edges when `whole`."""
        key_floor = trace_floor = both_floor = 0
        tallies = {name: [0, 0, 0, 0] for name in ("all", "straight", "cave")}
        doors = {letter: [0, 0, 0] for letter in DOOR_LETTERS}
        for panel_id in self.reading_panels:
            key_panel, trace_panel = self.key.panels[panel_id], self.trace.panels[panel_id]
            dx, dy = self.offsets[panel_id]
            partial = {parse_cell(text) for text in key_panel.partial} if whole else set[_Cell]()
            cave = {parse_cell(text) for text in key_panel.cave}
            occupied = _cells_of(key_panel.cells, ".~")
            floor = _cells_of(key_panel.cells, ".")
            key_floor += len(floor - partial)
            for x, y in _cells_of(trace_panel.cells, "."):
                if (x + dx, y + dy) not in partial:
                    trace_floor += 1
                    both_floor += (x + dx, y + dy) in floor
            for edge in _grid_edges(key_panel.width, key_panel.height):
                sides = _edge_cells(edge)
                if not partial.isdisjoint(sides):
                    continue
                key_letter = _letter(key_panel.edges, edge)
                trace_letter = _letter(trace_panel.edges, _shift(edge, -dx, -dy))
                if key_letter in DOOR_LETTERS:
                    doors[key_letter][0] += 1
                    doors[key_letter][2] += trace_letter == key_letter
                if occupied.isdisjoint(sides):
                    continue
                key_open, trace_open = key_letter in PASSABLE_LETTERS, trace_letter in PASSABLE_LETTERS
                for name in ("all", "straight" if cave.isdisjoint(sides) else "cave"):
                    tally = tallies[name]
                    tally[0] += 1
                    tally[1] += key_open == trace_open
                    tally[2] += trace_open and not key_open
                    tally[3] += key_open and not trace_open
            for edge in _grid_edges(trace_panel.width, trace_panel.height):
                trace_letter = _letter(trace_panel.edges, edge)
                if trace_letter in DOOR_LETTERS and partial.isdisjoint(_edge_cells(_shift(edge, dx, dy))):
                    doors[trace_letter][1] += 1

        def edge_tally(name: str) -> EdgeTally:
            total, right, walls_read_as_open, open_read_as_blocked = tallies[name]
            return EdgeTally(
                total=total,
                right=right,
                walls_read_as_open=walls_read_as_open,
                open_read_as_blocked=open_read_as_blocked,
            )

        def door_tally(letter: str) -> DoorTally:
            key, trace, matched = doors[letter]
            return DoorTally(key=key, trace=trace, matched=matched)

        return (
            FloorScore(key_cells=key_floor, trace_cells=trace_floor, both=both_floor),
            EdgesScore(all=edge_tally("all"), straight=edge_tally("straight"), cave=edge_tally("cave")),
            DoorsScore(door=door_tally("d"), locked=door_tally("l"), secret=door_tally("s")),
        )

    # The resolution families compare the levels, with each trace cell mapped onto the key's level.

    def located(self, level_id: _LevelId, cell: _Cell) -> tuple[_Cell, int] | None:
        """Where a cell of the trace's level lies on the key's level, and the turn from the trace's sides to the key's.

        The cell goes back to the page through the trace's panel origin and
        turn, across by the panel's offset, and onto the key's level by the
        key's turn and origin for that panel. `None` when the cell lies on no
        panel, the panel has no partner in the key, or the key's level doesn't
        place it.
        """
        found = self.trace.page_cell(level_id, cell)
        if found is None:
            return None
        part, page = found
        offset = self.offsets.get(part.panel)
        if offset is None:
            return None
        landed = self.key.level_cell(level_id, part.panel, (page[0] + offset[0], page[1] + offset[1]))
        if landed is None:
            return None
        key_cell, key_rotation = landed
        return key_cell, (key_rotation - part.rotation) % 360

    def located_cells(self, level_id: _LevelId, cells: Iterable[str]) -> set[_Cell]:
        located = (self.located(level_id, parse_cell(text)) for text in cells)
        return {found[0] for found in located if found is not None}

    def key_destination(self, dungeon: str, to_dungeon: str | None, to_level: int, to_cell: str) -> _Destination:
        target = (to_dungeon or dungeon, to_level)
        return (target[0], target[1], None if target in self.excluded else parse_cell(to_cell))

    def trace_destination(
        self, dungeon: str, to_dungeon: str | None, to_level: int, to_cell: str
    ) -> _Destination | None:
        target = (to_dungeon or dungeon, to_level)
        if target in self.excluded:
            return (target[0], target[1], None)
        found = self.located(target, parse_cell(to_cell))
        return None if found is None else (target[0], target[1], found[0])

    def areas(self) -> AreasScore:
        overlaps: list[AreaOverlap] = []
        extra: list[str] = []
        for level_id in self.compared:
            dungeon, number = level_id
            key_areas, trace_areas = self.key.areas(level_id), self.trace.areas(level_id)
            for name, area in key_areas.items():
                key_cells = {parse_cell(text) for text in area.cells}
                trace_area = trace_areas.get(name)
                trace_cells = () if trace_area is None else trace_area.cells
                overlaps.append(
                    AreaOverlap(
                        dungeon=dungeon,
                        level=number,
                        key=name,
                        key_cells=len(key_cells),
                        trace_cells=len(set(trace_cells)),
                        both=len(self.located_cells(level_id, trace_cells) & key_cells),
                    )
                )
            extra.extend(f"{dungeon}/{number}/{name}" for name in trace_areas if name not in key_areas)
        return AreasScore(areas=tuple(overlaps), extra=tuple(extra))

    def keys(self) -> KeysScore:
        total = right = 0
        wrong: list[str] = []
        for level_id in self.compared:
            key_level = self.key.levels.get(level_id)
            if key_level is None:
                continue
            trace_level = self.trace.levels.get(level_id)
            trace_keys = () if trace_level is None else trace_level.keys
            trace_areas = self.trace.areas(level_id)
            for placed in key_level.keys:
                total += 1
                key_area = {parse_cell(text) for text in key_level.areas[placed.area].cells}
                trace_area = trace_areas.get(placed.area)
                in_trace_area = trace_area is not None and parse_cell(placed.cell) in self.located_cells(
                    level_id, trace_area.cells
                )
                in_key_area = any(
                    other.label == placed.label
                    and other.area == placed.area
                    and not self.located_cells(level_id, (other.cell,)).isdisjoint(key_area)
                    for other in trace_keys
                )
                if in_trace_area and in_key_area:
                    right += 1
                else:
                    wrong.append(f"{level_id[0]}/{level_id[1]}/{placed.label}")
        return KeysScore(total=total, right=right, wrong=tuple(wrong))

    def joins(self) -> JoinsScore:
        key_joins = matched = 0
        missing: list[str] = []
        extra: list[str] = []
        for level_id in self.compared:
            dungeon, number = level_id
            key_set, trace_set = _joins(self.key.levels.get(level_id)), _joins(self.trace.levels.get(level_id))
            key_joins += len(key_set)
            matched += len(key_set & trace_set)
            missing.extend(f"{dungeon}/{number}/{first}~{second}" for first, second in sorted(key_set - trace_set))
            extra.extend(f"{dungeon}/{number}/{first}~{second}" for first, second in sorted(trace_set - key_set))
        return JoinsScore(key_joins=key_joins, matched=matched, missing=tuple(missing), extra=tuple(extra))

    def reached(self, document: _Document, dungeon_id: str) -> set[tuple[int, str]]:
        """The areas a walk from the dungeon's entrance reaches, never entering a level that fell back in either."""
        dungeon = next((candidate for candidate in document.truth.dungeons if candidate.id == dungeon_id), None)
        if dungeon is None or dungeon.entrance is None:
            return set()
        levels = {
            level.number: level
            for level in dungeon.levels
            if level.verdict == "traced" and (dungeon_id, level.number) not in self.excluded
        }
        start = (dungeon.entrance.level, parse_cell(dungeon.entrance.cell))
        if start[0] not in levels:
            return set()
        links: dict[tuple[int, _Cell], list[tuple[int, _Cell]]] = {}
        for number, level in levels.items():
            for transition in level.transitions:
                if transition.to_dungeon in (None, dungeon_id) and transition.to_level in levels:
                    links.setdefault((number, parse_cell(transition.cell)), []).append(
                        (transition.to_level, parse_cell(transition.to_cell))
                    )
            for passage in level.passages:
                if passage.to_level in levels:
                    links.setdefault((number, parse_cell(passage.cell)), []).append(
                        (passage.to_level, parse_cell(passage.to_cell))
                    )
        seen = {start}
        stack = [start]
        while stack:
            number, cell = stack.pop()
            level = levels[number]
            assert level.edges is not None
            steps = [
                (number, _neighbour(cell, side))
                for side in _OPPOSITE
                if _letter(level.edges, _edge(cell, side)) in _WALKABLE
            ]
            for step in [*steps, *links.get((number, cell), ())]:
                (x, y), target = step[1], levels[step[0]]
                if step not in seen and 0 <= y < target.height and 0 <= x < target.width and target.cells[y][x] == ".":
                    seen.add(step)
                    stack.append(step)
        return {
            (number, name)
            for number, level in levels.items()
            for name, area in level.areas.items()
            if any((number, parse_cell(text)) in seen for text in area.cells)
        }

    def reachability(self) -> ReachabilityScore:
        compared = set(self.compared)
        total = right = 0
        wrong: list[str] = []
        for dungeon in self.key.truth.dungeons:
            if not any((dungeon.id, level.number) in compared for level in dungeon.levels):
                continue
            total += 1
            key_reached, trace_reached = self.reached(self.key, dungeon.id), self.reached(self.trace, dungeon.id)
            if key_reached == trace_reached:
                right += 1
                continue
            parts: list[str] = []
            if missed := sorted(key_reached - trace_reached):
                parts.append("the trace doesn't reach " + ", ".join(f"{number}/{name}" for number, name in missed))
            if added := sorted(trace_reached - key_reached):
                parts.append("the trace also reaches " + ", ".join(f"{number}/{name}" for number, name in added))
            wrong.append(f"{dungeon.id}: {'; '.join(parts)}")
        return ReachabilityScore(total=total, right=right, wrong=tuple(wrong))

    def entrance(self) -> PlacementScore:
        key_items: list[tuple[str, tuple[_LevelId, _Cell]]] = []
        for dungeon in self.key.truth.dungeons:
            entrance = dungeon.entrance
            if entrance is not None and (dungeon.id, entrance.level) not in self.excluded:
                label = f"{dungeon.id}/{entrance.level}/{entrance.cell}"
                key_items.append((label, ((dungeon.id, entrance.level), parse_cell(entrance.cell))))
        trace_items: list[tuple[_LevelId, _Cell] | None] = []
        for dungeon in self.trace.truth.dungeons:
            entrance = dungeon.entrance
            if entrance is not None and (dungeon.id, entrance.level) not in self.excluded:
                level_id = (dungeon.id, entrance.level)
                found = self.located(level_id, parse_cell(entrance.cell))
                trace_items.append(None if found is None else (level_id, found[0]))
        return _placement(key_items, trace_items)

    def transitions(self) -> PlacementScore:
        key_items: list[tuple[str, _Placed]] = []
        trace_items: list[_Placed | None] = []
        for level_id in self.compared:
            dungeon, number = level_id
            key_level, trace_level = self.key.levels.get(level_id), self.trace.levels.get(level_id)
            for transition in () if key_level is None else key_level.transitions:
                to = self.key_destination(dungeon, transition.to_dungeon, transition.to_level, transition.to_cell)
                item = (level_id, parse_cell(transition.cell), transition.kind, to)
                key_items.append((f"{dungeon}/{number}/{transition.cell}", item))
            for transition in () if trace_level is None else trace_level.transitions:
                found = self.located(level_id, parse_cell(transition.cell))
                to = self.trace_destination(dungeon, transition.to_dungeon, transition.to_level, transition.to_cell)
                trace_items.append(None if found is None or to is None else (level_id, found[0], transition.kind, to))
        return _placement(key_items, trace_items)

    def passages(self) -> PlacementScore:
        key_items: list[tuple[str, _Placed]] = []
        trace_items: list[_Placed | None] = []
        for level_id in self.compared:
            dungeon, number = level_id
            key_level, trace_level = self.key.levels.get(level_id), self.trace.levels.get(level_id)
            for passage in () if key_level is None else key_level.passages:
                to = self.key_destination(dungeon, None, passage.to_level, passage.to_cell)
                item = (level_id, parse_cell(passage.cell), passage.side, to)
                key_items.append((f"{dungeon}/{number}/{passage.cell}", item))
            for passage in () if trace_level is None else trace_level.passages:
                found = self.located(level_id, parse_cell(passage.cell))
                to = self.trace_destination(dungeon, None, passage.to_level, passage.to_cell)
                trace_items.append(
                    None
                    if found is None or to is None
                    else (level_id, found[0], rotate_side(passage.side, found[1]), to)
                )
        return _placement(key_items, trace_items)

    def repairs(self) -> RepairsScore:
        per_level: dict[str, int] = {}
        edges = matching = 0
        for repair in self.trace.truth.repairs:
            level_id = (repair.dungeon, repair.level)
            if level_id in self.excluded:
                continue
            name = f"{repair.dungeon}/{repair.level}"
            per_level[name] = per_level.get(name, 0) + 1
            key_level = self.key.levels.get(level_id)
            for damaged in repair.edges:
                edges += 1
                cell, side = parse_edge(damaged.edge)
                # An edge on a panel's border may be named from the cell beyond the panel.
                found = self.located(level_id, cell)
                if found is None:
                    cell, side = _neighbour(cell, side), _OPPOSITE[side]
                    found = self.located(level_id, cell)
                if found is None or key_level is None or key_level.edges is None:
                    continue
                key_cell, turn = found
                matching += _letter(key_level.edges, _edge(key_cell, rotate_side(side, turn))) == damaged.after
        return RepairsScore(per_level=per_level, edges=edges, edges_matching=matching)

    # The halves and verdicts families compare the documents' records directly.

    def halves(self) -> HalvesScore:
        trace_pairs = {pair.regions: pair for pair in self.trace.truth.half_pairs}
        right = 0
        for pair in self.key.truth.half_pairs:
            other = trace_pairs.get(pair.regions)
            right += other is not None and (other.joined, other.rows, other.columns) == (
                pair.joined,
                pair.rows,
                pair.columns,
            )
        return HalvesScore(total=len(self.key.truth.half_pairs), right=right)

    def verdicts(self) -> VerdictsScore:
        wrong: list[str] = []
        trace_regions = {region.id: region for region in self.trace.truth.regions}
        regions_right = 0
        for region in self.key.truth.regions:
            other = trace_regions.get(region.id)
            if other is None:
                wrong.append(f"region {region.id}: missing")
                continue
            facts = {
                "verdict": (region.verdict, other.verdict),
                "style": (region.style, other.style),
                "scale": ((region.scale.feet, region.scale.printed), (other.scale.feet, other.scale.printed)),
                "compass": (
                    (region.compass.north, region.compass.printed),
                    (other.compass.north, other.compass.printed),
                ),
                "legend": (
                    (region.legend.from_page, region.legend.printed),
                    (other.legend.from_page, other.legend.printed),
                ),
            }
            differs = [name for name, (key_fact, trace_fact) in facts.items() if key_fact != trace_fact]
            if differs:
                wrong.append(f"region {region.id}: {', '.join(differs)}")
            else:
                regions_right += 1
        levels_right = 0
        for level_id, level in self.key.levels.items():
            other = self.trace.levels.get(level_id)
            if other is not None and other.verdict == level.verdict:
                levels_right += 1
            else:
                wrong.append(f"level {level_id[0]}/{level_id[1]}: {'missing' if other is None else 'verdict'}")
        return VerdictsScore(
            regions=len(self.key.truth.regions),
            regions_right=regions_right,
            levels=len(self.key.levels),
            levels_right=levels_right,
            wrong=tuple(wrong),
        )


def score_trace(trace: GeometryTruth, key: GeometryTruth) -> TraceScore:
    """Score a trace against its answer key.

    A 5-foot panel in either document is converted with
    [`convert_five_foot`][osrforge.tracing.fivefoot.convert_five_foot] before
    anything is compared. Panels are matched by id. Each panel of the trace is
    then lined up with the key's panel of the same id at the whole-cell offset
    `(dx, dy)` within `ALIGNMENT_SEARCH` cells that makes the most floor cells
    agree, ties going to the smallest `|dx| + |dy|`, then the smallest `dy`,
    then the smallest `dx`. A panel with no partner of the same id is left out
    of the reading families, and the halves family reports the difference.

    The floor, edges, and doors families compare the panels: the reading, with
    the trace's panels as repaired and `before_repair`'s as read. A panel used
    only by a level that fell back in either document is left out of them. The
    areas, keys, joins, reachability, entrance, transitions, passages, and
    repairs families compare the levels: the resolution. A level cell is
    mapped through the panel it lies on: back to that panel's page coordinates
    by undoing the trace's panel origin and turn, across by the panel's
    offset, and onto the key's level by the key's panel turn and origin. A
    destination cell is mapped the same way through its target level's panels,
    and when the target level fell back in either document the destination
    compares by level number alone. A level that fell back in either document
    scores in the verdicts family only. The whole-cell scores leave the
    partly-floor cells and their edges out of the floor, edges, and doors
    families and change nothing else. The rules are in
    `docs/tracing-conventions.md` under "The scorer".

    Args:
        trace: The trace.
        key: The answer key, or a synthetic map's answer.

    Returns:
        The score.
    """
    key_document = _Document(key)
    after, after_whole = _Comparison(_Document(trace), key_document).scores()
    if trace.before_repair is None:
        return TraceScore(after=after, after_whole=after_whole)
    before, before_whole = _Comparison(_Document(trace.before_repair), key_document).scores()
    return TraceScore(after=after, after_whole=after_whole, before=before, before_whole=before_whole)
