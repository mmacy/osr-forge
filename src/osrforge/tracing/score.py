"""The scorer: how far a trace is from its answer key, one family of numbers at a time.

[`score_trace`][osrforge.tracing.score.score_trace] lines each panel of the
trace up with the key at the whole-cell offset that fits best, then reports
the families below. It scores the trace as repaired and, when the trace has a
`before_repair`, the trace before repair, and each of those twice: over every
cell, and without the cells the key marks as partly floor.

The rules are in `docs/tracing-conventions.md` under "The scorer".
"""

from pydantic import BaseModel, ConfigDict, Field

from osrforge.tracing.truth import GeometryTruth

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
    raise NotImplementedError("chunk: scorer")
