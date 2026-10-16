"""The reference resolution script: a map's reading becomes its levels and areas by the conventions.

`resolve` takes a geometry truth document's reading layer (its pages, regions,
panels, and half pairs) with the map's survey, content, and map-reading caches,
and returns the document with its resolution layer filled in: each dungeon's
levels with their placed panels, areas, placed keys, transitions, and passages,
and each dungeon's entrance. It applies the rules of
`docs/tracing-conventions.md` in their order, and nothing else. On a synthetic
map its result equals the committed answer in canonical form, flags aside.

Run it on one map directory to compare its result with the committed answer:

    uv run python -m tools.answer_keys.resolve tests/assets/synthetic-maps/manor
"""

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from tools.answer_keys.world import (
    DOORS,
    OPPOSITE,
    PASSABLE,
    SIDES,
    Cell,
    Grid,
    Space,
    WCell,
    World,
    chebyshev,
    rect_contains,
    step,
)

from osrforge.contracts.stages import AreaConnection, LevelContent, MapReading, SurveyIndex
from osrforge.tracing.truth import (
    Area,
    Dungeon,
    EdgeGrids,
    Entrance,
    GeometryTruth,
    Level,
    LevelPanel,
    Passage,
    PlacedKey,
    Transition,
    canonical_form,
    cell_key,
    edge_key,
    load_geometry_truth,
    parse_cell,
    parse_edge,
    rotate_cell,
    rotate_side,
    rotated_size,
    rotation_for,
)

LevelId = tuple[str, int]
"""A level: its dungeon id and number."""

LINK_SYMBOLS = ("stairs", "ladder", "trapdoor", "hinged_floor", "chute", "slide")
LINK_VIAS = {"stairs": "stairs", "trapdoor": "trapdoor", "chute": "chute"}
ENTRANCE_TITLES = ("entrance", "entranceway")


class ConventionGap(Exception):
    """The conventions give no answer for an input, so the script stops and the case is logged."""


@dataclass(frozen=True)
class SurveyKey:
    """One survey key with its place in survey order."""

    level: LevelId
    key: str
    name: str
    source_label: str | None
    order: tuple[int, int, int]

    def matches(self, label: str) -> bool:
        """Whether a printed label is this key, ignoring case."""
        text = label.strip().lower()
        return text == self.key.lower() or (self.source_label is not None and text == self.source_label.strip().lower())


@dataclass(eq=False)
class Placed:
    """A printed key or letter and where the rules put it."""

    label: str
    key: SurveyKey
    mark_cell: WCell
    cell: WCell
    letter: bool = False
    edge: tuple[WCell, str] | None = None
    leader: bool = False

    @property
    def moved(self) -> bool:
        """True when the placed cell differs from the cell the mark names."""
        return self.cell != self.mark_cell


@dataclass
class Opening:
    """An outer opening: consecutive outer-opening edges on one line, seen from a level's floor."""

    level: LevelId
    edges: list[tuple[WCell, str]]

    @property
    def inner_cells(self) -> list[WCell]:
        """The floor cells just inside, along the opening."""
        return [cell for cell, _side in self.edges]

    def middle(self, world: World) -> WCell:
        """The inner cell at the middle, the north or west one when the width is even."""
        width = len(self.edges)
        if width % 2:
            return self.edges[width // 2][0]
        pair = [self.edges[width // 2 - 1][0], self.edges[width // 2][0]]
        return world.first(pair)

    def order(self, world: World) -> tuple[int, float, float]:
        """The north-then-west order of the opening's middle edge."""
        return min(world.edge_order(cell, side) for cell, side in self.edges)


@dataclass
class Link:
    """A prose connection the trace has to realize as transitions."""

    start: tuple[LevelId, str]
    target: tuple[LevelId, str]
    means: str
    to_dungeon: str | None = None


@dataclass
class LevelState:
    """What the rules have decided about one level so far."""

    id: LevelId
    number: int
    grids: list[Grid] = field(default_factory=list)
    areas: dict[str, set[WCell]] = field(default_factory=dict)
    synthesized: set[str] = field(default_factory=set)
    synth_cells: set[WCell] = field(default_factory=set)
    overrides: dict[tuple[WCell, str], str] = field(default_factory=dict)
    placements: dict[str, tuple[Cell, int]] = field(default_factory=dict)
    """Each panel's origin and rotation on the level grid."""

    passages: list[tuple[WCell, str, WCell, LevelId]] = field(default_factory=list)
    """Each passage as its cell, its panel side, its destination cell, and the destination level."""

    transitions: list[Transition] = field(default_factory=list)
    verdict: str = "traced"
    reason: str | None = None


def load_caches(directory: Path) -> tuple[SurveyIndex, dict[LevelId, LevelContent], MapReading]:
    """Load a synthetic map's survey, content, and map-reading caches.

    Args:
        directory: The map's directory, with its `stages/` subdirectory.

    Returns:
        The survey, the content caches by level, and the map reading.
    """
    stages = directory / "stages"
    survey = SurveyIndex.model_validate(json.loads((stages / "survey.json").read_text(encoding="utf-8")))
    mapread = MapReading.model_validate(json.loads((stages / "mapread.json").read_text(encoding="utf-8")))
    contents: dict[LevelId, LevelContent] = {}
    for path in sorted(stages.glob("areas.*.json")):
        content = LevelContent.model_validate(json.loads(path.read_text(encoding="utf-8")))
        contents[(content.dungeon_id, content.level_number)] = content
    return survey, contents, mapread


def reading_of(truth: GeometryTruth) -> GeometryTruth:
    """The reading layer of a document alone, with each level emptied to a traced shell.

    The resolution layer is dropped, so a comparison of `resolve`'s result with
    the document can't be reading the answer back.
    """
    dungeons = tuple(
        dungeon.model_copy(
            update={
                "entrance": None,
                "levels": tuple(
                    Level(number=level.number, verdict="fallback", reason="reading only") for level in dungeon.levels
                ),
            }
        )
        for dungeon in truth.dungeons
    )
    return truth.model_copy(update={"dungeons": dungeons, "repairs": (), "before_repair": None})


class Resolver:
    """One run of the rules over one map."""

    def __init__(
        self,
        reading: GeometryTruth,
        survey: SurveyIndex,
        contents: dict[LevelId, LevelContent],
        mapread: MapReading,
    ) -> None:
        self.reading = reading
        self.survey = survey
        self.contents = contents
        self.mapread = mapread
        self.world = World(reading)
        self.levels: dict[LevelId, LevelState] = {}
        self.level_order: list[LevelId] = []
        self.keys: dict[LevelId, list[SurveyKey]] = {}
        self.dungeon_names: dict[str, str] = {}
        for d_index, dungeon in enumerate(survey.dungeons):
            self.dungeon_names[dungeon.id] = dungeon.name
            for l_index, level in enumerate(dungeon.levels):
                level_id = (dungeon.id, level.number)
                self.level_order.append(level_id)
                self.levels[level_id] = LevelState(level_id, level.number)
                self.keys[level_id] = [
                    SurveyKey(level_id, area.key, area.name, area.source_label, (d_index, l_index, k_index))
                    for k_index, area in enumerate(level.areas)
                ]
        self.outlines: dict[LevelId, list[tuple[Grid, tuple[tuple[int, int, int, int], ...], list[WCell]]]] = {}
        for grid in self.world.grids.values():
            for outline in grid.panel.outlines:
                level_id = (outline.dungeon, outline.level)
                seeds = [(grid.id, *parse_cell(text)) for text in outline.seeds]
                self.outlines.setdefault(level_id, []).append((grid, outline.rects, seeds))
                if grid not in self.levels[level_id].grids:
                    self.levels[level_id].grids.append(grid)
        for state in self.levels.values():
            state.grids.sort(key=lambda grid: grid.index)
        self.placed: list[Placed] = []
        self.owner: dict[WCell, LevelId] = {}
        self.outside: set[WCell] = set()
        self.mouth_owner: dict[tuple[WCell, str], tuple[LevelId, WCell]] = {}
        self.openings: dict[LevelId, list[Opening]] = {}
        self.assigned: dict[WCell, tuple[LevelId, str]] = {}
        self.corridor_runs: set[WCell] = set()
        self.synth_doors: dict[LevelId, list[tuple[WCell, str]]] = defaultdict(list)
        self.water_keyed: set[WCell] = set()
        self.far_used: set[tuple[LevelId, Cell]] = set()
        self.level_to_world: dict[LevelId, dict[Cell, WCell]] = {}
        self.locked: dict[LevelId, list[tuple[WCell, str]]] = defaultdict(list)

    # ----- survey helpers -----

    def level_index(self, level_id: LevelId) -> int:
        """A level's place in survey order."""
        return self.level_order.index(level_id)

    def key_of(self, level_id: LevelId, key: str) -> SurveyKey | None:
        """A level's survey key by its canonical key."""
        for item in self.keys[level_id]:
            if item.key == key:
                return item
        return None

    def resolve_target(self, level_id: LevelId, connection: AreaConnection) -> tuple[LevelId, str | None] | None:
        """Where a prose connection leads: the same level's key first, then the dungeon's other levels in order."""
        dungeon = level_id[0]
        if connection.to_key is not None:
            for candidate in [level_id] + [
                other for other in self.level_order if other[0] == dungeon and other != level_id
            ]:
                for item in self.keys[candidate]:
                    if item.matches(connection.to_key):
                        return (candidate, item.key)
        if connection.to_level is not None:
            target = (dungeon, connection.to_level)
            if target in self.levels:
                return (target, None)
        return None

    # ----- outlines and ownership -----

    def outlines_containing(self, wcell: WCell) -> list[LevelId]:
        """The levels whose outlines contain a cell, in survey order."""
        found: list[LevelId] = []
        for level_id in self.level_order:
            for grid, rects, _seeds in self.outlines.get(level_id, []):
                if grid.id == wcell[0] and any(rect_contains(rect, (wcell[1], wcell[2])) for rect in rects):
                    found.append(level_id)
                    break
        return found

    def is_outside(self, wcell: WCell) -> bool:
        """Whether a floor or water cell belongs to the outside."""
        return wcell in self.outside

    def partition(self, keys_only_on_floor: bool) -> None:
        """Divide the floor and water inside the outlines among the levels, from the seeds and the placed keys."""
        world = self.world
        necks = world.necks()

        def cost(a: WCell, side: str, letter: str, b: WCell) -> int:
            total = 1
            if b in necks:
                total = max(total, 2)
            if letter in DOORS or world.is_cave_mouth(a, side):
                total = max(total, 4)
            if letter in "wn-":
                total = max(total, 20)
            if world.letter(b) not in ".~":
                total = max(total, 20)
            return total

        for wcell in world.all_cells():
            if world.letter(wcell) in ".~" and not self.outlines_containing(wcell):
                self.outside.add(wcell)
        best: dict[LevelId, dict[WCell, tuple[int, tuple[int, int, float, float]]]] = {}
        for level_id in self.level_order:
            sources: list[tuple[WCell, tuple[int, int, float, float]]] = []
            for _grid, _rects, seeds in self.outlines.get(level_id, []):
                for seed in seeds:
                    sources.append((seed, (1, *world.order(seed))))
            for placed in self.placed:
                if placed.key.level != level_id:
                    continue
                if keys_only_on_floor and not world.is_floor(placed.cell):
                    continue
                if world.letter(placed.cell) not in ".~":
                    continue
                sources.append((placed.cell, (0, *world.order(placed.cell))))
            best[level_id] = world.dijkstra(sources, cost) if sources else {}
        self.owner = {}
        for wcell in world.all_cells():
            if world.letter(wcell) not in ".~" or wcell in self.outside:
                continue
            levels = self.outlines_containing(wcell)
            if not levels:
                continue
            if len(levels) == 1:
                self.owner[wcell] = levels[0]
                continue
            ranked = [
                (best[level_id].get(wcell, (10**9, (2, 0, 0.0, 0.0))), self.level_index(level_id), level_id)
                for level_id in levels
            ]
            self.owner[wcell] = min(ranked)[2]

    def level_floor(self, level_id: LevelId) -> set[WCell]:
        """The floor cells a level owns."""
        return {wcell for wcell, owner in self.owner.items() if owner == level_id and self.world.is_floor(wcell)}

    def keys_on_floor(self, level_id: LevelId) -> list[WCell]:
        """The placed cells of a level's keys that stand on floor the level owns."""
        return [
            placed.cell
            for placed in self.placed
            if placed.key.level == level_id
            and self.world.is_floor(placed.cell)
            and self.owner.get(placed.cell) == level_id
        ]

    def cave_mouth_rule(self) -> None:
        """Give each cave mouth to a level, and put floor reached only through a level's mouths on the outside."""
        world = self.world
        mouths = world.cave_mouth_edges()
        reach: dict[LevelId, dict[WCell, int]] = {}
        for level_id in self.level_order:

            def crosses(a: WCell, side: str, letter: str, b: WCell) -> bool:
                return world.walking_floor(a, side, letter, b) and not world.is_cave_mouth(a, side)

            reach[level_id] = world.walk(self.keys_on_floor(level_id), crosses)
        self.mouth_owner = {}
        for cell, side in mouths:
            other = world.across(cell, side)
            found: tuple[LevelId, WCell] | None = None
            for level_id in self.level_order:
                for candidate in (cell, other):
                    if candidate is not None and candidate in reach[level_id] and self.owner.get(candidate) == level_id:
                        found = (level_id, candidate)
                        break
                if found:
                    break
            if found is None:
                owners = [
                    (self.level_index(self.owner[c]), self.owner[c], c)
                    for c in (cell, other)
                    if c is not None and c in self.owner
                ]
                if not owners:
                    continue
                _index, level_id, inner = min(owners)
                found = (level_id, inner)
            self.mouth_owner[(cell, side)] = found
            if other is not None:
                self.mouth_owner[(other, OPPOSITE[side])] = found
        open_ground = world.open_ground()
        for level_id in self.level_order:

            def through(a: WCell, side: str, letter: str, b: WCell, level_id: LevelId = level_id) -> bool:
                if not world.walking_floor(a, side, letter, b):
                    return False
                if world.is_cave_mouth(a, side):
                    owner = self.mouth_owner.get((a, side))
                    return owner is not None and owner[0] == level_id
                return True

            beyond = world.walk(self.keys_on_floor(level_id), through)
            for wcell in beyond:
                if wcell in reach[level_id] or self.owner.get(wcell) != level_id or wcell in open_ground:
                    continue
                self.outside.add(wcell)
                del self.owner[wcell]

    def find_openings(self) -> None:
        """Find each level's outer openings from the drawing."""
        world = self.world
        open_ground = world.open_ground()
        self.openings = {}
        for level_id in self.level_order:
            edges: list[tuple[WCell, str]] = []
            for wcell in sorted(self.level_floor(level_id), key=world.order):
                grid = world.grid(wcell)
                for side in SIDES:
                    letter = world.edge(wcell, side)
                    other = world.across(wcell, side)
                    mouth = (
                        world.is_cave_mouth(wcell, side)
                        and self.mouth_owner.get((wcell, side), (level_id,))[0] == level_id
                    )
                    if mouth:
                        edges.append((wcell, side))
                        continue
                    if other is None:
                        if letter in PASSABLE and side != grid.fold_side:
                            edges.append((wcell, side))
                        continue
                    other_floor = world.letter(other) == "."
                    other_outside = other_floor and self.is_outside(other)
                    other_open = other in open_ground
                    if wcell in open_ground:
                        if letter in PASSABLE and other_outside:
                            edges.append((wcell, side))
                    elif (letter in PASSABLE or letter in DOORS) and (other_outside or other_open):
                        edges.append((wcell, side))
            self.openings[level_id] = self.group_openings(level_id, edges)

    def group_openings(self, level_id: LevelId, edges: list[tuple[WCell, str]]) -> list[Opening]:
        """Join consecutive outer-opening edges on one line into openings."""
        world = self.world
        lines: dict[tuple[str, str, int, str], list[tuple[int, WCell, str]]] = defaultdict(list)
        for wcell, side in edges:
            x, y = world.cell(wcell)
            if side in ("west", "east"):
                line = x if side == "west" else x + 1
                lines[(wcell[0], "v", line, side)].append((y, wcell, side))
            else:
                line = y if side == "north" else y + 1
                lines[(wcell[0], "h", line, side)].append((x, wcell, side))
        openings: list[Opening] = []
        for members in lines.values():
            members.sort()
            run: list[tuple[WCell, str]] = []
            last = None
            for position, wcell, side in members:
                if run and last is not None and position != last + 1:
                    openings.append(Opening(level_id, run))
                    run = []
                run.append((wcell, side))
                last = position
            if run:
                openings.append(Opening(level_id, run))
        openings.sort(key=lambda opening: opening.order(world))
        return openings

    # ----- marks and keys -----

    def read_marks(self) -> None:
        """Turn each panel's marks into placed keys, at the cells they're printed on or where their leader lines end."""
        self.placed = []
        self.letters: list[tuple[str, WCell, Grid]] = []
        for grid in self.world.grids.values():
            candidates = [(outline.dungeon, outline.level) for outline in grid.panel.outlines]
            candidates = [level_id for level_id in self.level_order if level_id in candidates]
            for mark in grid.panel.marks:
                cell: WCell = (grid.id, *parse_cell(mark.cell))
                whole = self.match_label(mark.text, candidates, cell)
                labels = [mark.text] if whole is not None or "/" not in mark.text else mark.text.split("/")
                for label in labels:
                    key = self.match_label(label, candidates, cell)
                    letter = False
                    if key is None:
                        suffix = re.fullmatch(r"(\d+)([A-Za-z])", label)
                        if suffix:
                            key = self.match_label(suffix.group(1), candidates, cell)
                    if key is None:
                        if re.fullmatch(r"[A-Za-z]", label):
                            self.letters.append((label, cell, grid))
                        continue
                    placed_cell = cell if mark.leader_to is None else (grid.id, *parse_cell(mark.leader_to))
                    edge = None
                    if mark.edge is not None:
                        edge_cell, side = parse_edge(mark.edge)
                        edge = ((grid.id, *edge_cell), side)
                    self.placed.append(Placed(label, key, cell, placed_cell, letter, edge, mark.leader_to is not None))

    def match_label(self, label: str, candidates: list[LevelId], cell: WCell) -> SurveyKey | None:
        """The survey key a printed label names, among the levels drawn on its panel."""
        found = [key for level_id in candidates for key in self.keys[level_id] if key.matches(label)]
        if not found:
            return None
        if len(found) == 1:
            return found[0]
        containing = self.outlines_containing(cell)
        inside = [key for key in found if key.level in containing]
        if len(inside) == 1:
            return inside[0]
        raise ConventionGap(f"label {label!r} at {cell} matches keys on several levels: {[key.level for key in found]}")

    def place_letters(self) -> None:
        """Join each printed letter to the key that names it, or leave it as a label."""
        world = self.world
        for label, cell, _grid in self.letters:
            if self.is_outside(cell):
                continue
            level_id = self.owner.get(cell)
            if level_id is None:
                containing = self.outlines_containing(cell)
                if not containing:
                    continue
                level_id = containing[0]
            dungeon_name = self.dungeon_names[level_id[0]]
            if label.lower() in [word.lower() for word in re.findall(r"[A-Za-z0-9']+", dungeon_name)]:
                continue
            naming = [key for key in self.keys[level_id] if self.names_letter(key, label)]
            if not naming:
                continue
            if len(naming) == 1:
                chosen = naming[0]
            else:
                distances = world.walk([cell], world.walking_floor)
                ranked = []
                for key in naming:
                    cells = [placed.cell for placed in self.placed if placed.key == key]
                    nearest = min((distances.get(c, 10**9) for c in cells), default=10**9)
                    ranked.append((nearest, key.order, key))
                chosen = min(ranked)[2]
            self.placed.append(Placed(label, chosen, cell, cell, letter=True))

    def names_letter(self, key: SurveyKey, letter: str) -> bool:
        """Whether a key's description names a letter: quoted, attached to the key's number, or after a naming word."""
        content = self.contents.get(key.level)
        if content is None:
            return False
        description = ""
        for area in content.areas:
            if area.key == key.key:
                description = area.description
        text = description.lower()
        low = letter.lower()
        quotes = "\"'\u201c\u2018\u201d\u2019"
        if re.search(f"[{quotes}]{re.escape(low)}[{quotes}]", text):
            return True
        if re.search(rf"\b{re.escape(key.key.lower())}{re.escape(low)}\b", text):
            return True
        for word in ("cell", "area", "marked", "at"):
            if word == "at" and low in ("a", "i"):
                continue
            if re.search(rf"\b{word}\s+{re.escape(low)}\b", text):
                return True
        return False

    def place_keys(self) -> None:
        """Move each key printed off its level's floor to the cell the rules give it."""
        world = self.world
        open_ground = world.open_ground()
        for placed in self.placed:
            if placed.letter or placed.leader:
                continue
            level_id = placed.key.level
            cell = placed.mark_cell
            letter = world.letter(cell)
            owner = self.owner.get(cell)
            if placed.edge is not None:
                edge_cell, side = placed.edge
                outer = any((edge_cell, side) in opening.edges for opening in self.openings.get(level_id, []))
                other = world.across(edge_cell, side)
                if not outer and other is not None:
                    sides = [edge_cell, other]
                    with_key = [self.space_has_other_key(c, placed.key) for c in sides]
                    free = [c for c, has in zip(sides, with_key, strict=True) if not has]
                    placed.cell = world.first(free) if len(free) == 1 else world.first(sides)
                    continue
                placed.cell = self.nearest_floor(level_id, cell, exclude=open_ground)
                continue
            if letter in ".~" and owner == level_id:
                continue
            if letter == "." and self.is_outside(cell):
                inner = [opening.middle(world) for opening in self.openings.get(level_id, [])]
                inner += [c for opening in self.openings.get(level_id, []) for c in opening.inner_cells]
                near = [c for c in inner if c[0] == cell[0] and chebyshev(world.cell(c), world.cell(cell)) <= 2]
                if near:
                    placed.cell = world.nearest(cell, near) or placed.cell
                else:
                    placed.cell = self.nearest_floor(level_id, cell, exclude=set())
                continue
            placed.cell = self.nearest_floor(level_id, cell, exclude=open_ground)

    def space_has_other_key(self, wcell: WCell, key: SurveyKey) -> bool:
        """Whether the open space of a cell holds a key other than the given one."""
        space = self.world.space_of().get(wcell)
        if space is None:
            return False
        return any(p.cell in space.cells and p.key != key for p in self.placed)

    def nearest_floor(self, level_id: LevelId, cell: WCell, exclude: set[WCell]) -> WCell:
        """The nearest floor cell of a level by Chebyshev distance, ties in north-then-west order."""
        candidates = [c for c in self.level_floor(level_id) if c not in exclude and c not in self.outside]
        found = self.world.nearest(cell, candidates)
        if found is None:
            raise ConventionGap(f"key at {cell} has no floor of level {level_id} on its panel to move to")
        return found

    # ----- areas -----

    def area_of(self, level_id: LevelId, key: str) -> set[WCell]:
        """A level's area by key, empty when the key has none yet."""
        return self.levels[level_id].areas.get(key, set())

    def assign(self, level_id: LevelId, key: str, cells: set[WCell]) -> None:
        """Give cells to an area, skipping cells an earlier rule took."""
        area = self.levels[level_id].areas.setdefault(key, set())
        for wcell in cells:
            if wcell in self.assigned or wcell in self.outside or wcell in self.corridor_runs:
                continue
            self.assigned[wcell] = (level_id, key)
            area.add(wcell)

    def placed_in(self, cells: set[WCell], level_id: LevelId | None = None) -> list[Placed]:
        """The placed keys standing on some cells, letters left out."""
        return [
            p for p in self.placed if p.cell in cells and not p.letter and (level_id is None or p.key.level == level_id)
        ]

    def divide(self, cells: set[WCell], keys: list[Placed]) -> dict[Placed, set[WCell]]:
        """Divide a space among the keys placed in it by walking distance, printed open lines counted as walls."""
        world = self.world
        ordered = sorted(keys, key=lambda p: (world.order(p.cell), p.key.order))
        distances: dict[Placed, dict[WCell, int]] = {}
        for placed in ordered:
            distances[placed] = world.walk(
                [placed.cell], lambda a, s, letter, b: letter == "o" and b in cells, lambda c: c in cells
            )
        out: dict[Placed, set[WCell]] = {p: set() for p in ordered}
        for wcell in cells:
            ranked = [
                (distances[p].get(wcell, 10**9), world.order(p.cell), p.key.order, i) for i, p in enumerate(ordered)
            ]
            best = min(ranked)
            if best[0] >= 10**9:
                continue
            out[ordered[best[3]]].add(wcell)
        return out

    def areas(self) -> None:
        """Find every level's areas from the placed keys and the drawing, in the conventions' rule order."""
        world = self.world
        spaces = world.spaces()
        space_of = world.space_of()
        rooms = world.rooms()
        structures = world.structures()
        runs = world.narrow_runs()
        open_ground = world.open_ground()
        self.assigned = {}
        self.corridor_runs = set()
        for state in self.levels.values():
            state.areas = {}
        run_of: dict[WCell, list[WCell]] = {wcell: run for run in runs for wcell in run}
        placed_floor = [p for p in self.placed if world.is_floor(p.cell)]

        # Walled rooms with keys, then the other spaces with keys.
        for space in spaces:
            keys = [p for p in placed_floor if p.cell in space.cells and not p.letter and p.cell not in open_ground]
            if not keys:
                continue
            if space.is_room:
                cells = set(space.cells) - self.outside
            else:
                cells = {c for c in space.cells if c not in open_ground and c not in self.outside}
                for wcell in list(cells):
                    run = run_of.get(wcell)
                    if (
                        run is not None
                        and len(run) >= 3
                        and not any(p.cell in run for p in placed_floor if not p.letter)
                    ):
                        cells.discard(wcell)
                        self.corridor_runs.add(wcell)
            by_key: dict[SurveyKey, list[Placed]] = defaultdict(list)
            for p in keys:
                by_key[p.key].append(p)
            if len(by_key) == 1:
                key = keys[0].key
                self.assign(key.level, key.key, cells)
                continue
            shares = self.divide(cells, keys)
            for placed, share in shares.items():
                self.assign(placed.key.level, placed.key.key, share)

        # Labelled narrow runs.
        for run in runs:
            labels = [p for p in placed_floor if p.cell in run and not p.letter]
            if labels:
                key = labels[0].key
                self.assign(key.level, key.key, set(run))
            elif len(run) >= 3:
                self.corridor_runs.update(c for c in run if c not in self.assigned)

        # Keys on open ground.
        taken: dict[int, SurveyKey] = {}
        for placed in sorted(placed_floor, key=lambda p: (p.key.order, world.order(p.cell))):
            if placed.letter or placed.cell not in open_ground:
                continue
            cell = world.cell(placed.cell)
            best: tuple[int, int, list[Space]] | None = None
            for group in structures:
                keys_in = {p.key for room in group for p in self.placed_in(set(room.cells))}
                keys_in |= {taken[room.index] for room in group if room.index in taken}
                if keys_in - {placed.key}:
                    continue
                distance = (
                    min(chebyshev(cell, world.cell(c)) for room in group for c in room.cells if c[0] == placed.cell[0])
                    if any(c[0] == placed.cell[0] for room in group for c in room.cells)
                    else 10**9
                )
                if distance <= 2 and (best is None or (distance, group[0].index) < (best[0], best[1])):
                    best = (distance, group[0].index, group)
            if best is not None:
                group = best[2]
                cells = {c for room in group for c in room.cells}
                for room in group:
                    taken[room.index] = placed.key
                self.assign(placed.key.level, placed.key.key, cells)
                placed.cell = world.nearest(placed.cell, cells) or placed.cell
            else:
                near = {
                    c
                    for c in world.within(placed.cell, 2)
                    if world.is_floor(c) and c not in self.outside and space_of[c].open_ground
                }
                others = [p for p in placed_floor if p is not placed and p.cell in open_ground and p.key != placed.key]
                if others:
                    shares = self.divide(near | {p.cell for p in others}, [placed, *others])
                    near = shares[placed] & near
                self.assign(placed.key.level, placed.key.key, near)

        # Keys on water.
        water_keys = [p for p in self.placed if world.is_water(p.cell) and not p.letter]
        for placed in water_keys:
            near = {c for c in world.within(placed.cell, 3) if world.is_water(c) and c not in self.outside}
            others = [p for p in water_keys if p is not placed and p.key != placed.key]
            if others:
                distances = {
                    p: world.walk([p.cell], lambda a, s, letter, b: letter == "o" and world.is_water(b))
                    for p in [placed, *others]
                }
                near = {
                    c
                    for c in near
                    if min(
                        (
                            (distances[p].get(c, 10**9), world.order(p.cell), p.key.order, i)
                            for i, p in enumerate([placed, *others])
                        )
                    )[3]
                    == 0
                }
            self.assign(placed.key.level, placed.key.key, near)
            self.water_keyed |= near

        # Unprinted keys: the entrance key, then the others.
        for level_id in self.level_order:
            if self.levels[level_id].verdict != "traced":
                continue
            printed = {p.key.key for p in self.placed if p.key.level == level_id}
            for key in self.keys[level_id]:
                if key.key in printed:
                    continue
                if self.is_entrance_title(key):
                    self.entrance_key_area(key, rooms)
            for key in self.keys[level_id]:
                if key.key in printed or self.is_entrance_title(key) or key.key in self.levels[level_id].areas:
                    continue
                self.unprinted_key_area(key, rooms)

        # Unlabelled walled rooms and unkeyed parts closed off by printed lines join the areas they open onto.
        self.join_unlabelled(rooms, space_of)

        # Keyed walled structures with no door get one.
        for group in structures:
            keyed = {self.assigned[c] for room in group for c in room.cells if c in self.assigned}
            if not keyed:
                continue
            if any(world.edge(c, side) in DOORS for room in group for c in room.cells for side in SIDES):
                continue
            level_id = min(keyed, key=lambda item: self.level_index(item[0]))[0]
            self.synthesize_door(level_id, group)

        # The stacked-key rule: a key left outside its own area moves to that area's cell nearest its mark.
        for placed in self.placed:
            if placed.letter:
                continue
            area = self.area_of(placed.key.level, placed.key.key)
            if area and placed.cell not in area:
                placed.cell = world.nearest(placed.mark_cell, area) or placed.cell

        # Ownership follows areas.
        for level_id, state in self.levels.items():
            for cells in state.areas.values():
                for wcell in cells:
                    if wcell not in state.synth_cells:
                        self.owner[wcell] = level_id

    def is_entrance_title(self, key: SurveyKey) -> bool:
        """Whether a key is the map reading's entrance key or is titled Entrance or Entranceway."""
        for level in self.mapread.levels:
            same = (level.dungeon_id, level.level_number) == key.level
            if same and level.entrance_key is not None and key.matches(level.entrance_key):
                return True
        return key.name.strip().lower() in ENTRANCE_TITLES

    def connected_area(self, key: SurveyKey) -> tuple[str, AreaConnection] | None:
        """The first same-level area with cells that a key's prose connects it to, with the connection."""
        content = self.contents.get(key.level)
        if content is None:
            return None
        for area in content.areas:
            if area.key != key.key:
                continue
            for connection in area.connections:
                target = self.resolve_target(key.level, connection)
                if target is None or target[0] != key.level or target[1] is None:
                    continue
                if self.area_of(key.level, target[1]):
                    return (target[1], connection)
        return None

    def entrance_key_area(self, key: SurveyKey, rooms: list[Space]) -> None:
        """An unprinted entrance key takes the unkeyed floor one outer opening leads into."""
        world = self.world
        level_id = key.level
        openings = self.openings.get(level_id, [])
        if not openings:
            raise ConventionGap(f"unprinted entrance key {key.key} on {level_id}: the level has no outer opening")
        connected = self.connected_area(key)
        if connected is not None:
            target_first = world.first(self.area_of(level_id, connected[0]))
            ranked = []
            for opening in openings:
                distances = world.walk([opening.middle(world)], world.walking_floor)
                ranked.append((distances.get(target_first, 10**9), opening.order(world), opening))
            opening = min(ranked, key=lambda item: item[:2])[2]
        else:
            opening = openings[0]
        floor = self.level_floor(level_id)

        def allowed(c: WCell) -> bool:
            return c in floor and c not in self.assigned and c not in self.corridor_runs

        reached = world.walk(opening.inner_cells, world.open_floor, allowed)
        if not reached:
            raise ConventionGap(
                f"unprinted entrance key {key.key} on {level_id}: its opening leads into no unkeyed floor"
            )
        self.assign(level_id, key.key, set(reached))

    def unprinted_key_area(self, key: SurveyKey, rooms: list[Space]) -> None:
        """An unprinted key takes the unlabelled walled room beside its connected area, or a synthesized area."""
        world = self.world
        level_id = key.level
        state = self.levels[level_id]
        connected = self.connected_area(key)
        if connected is not None:
            beside_key, connection = connected
        else:
            first = next((k.key for k in self.keys[level_id] if self.area_of(level_id, k.key)), None)
            if first is None:
                raise ConventionGap(f"unprinted key {key.key} on {level_id}: the level has no area to stand beside")
            beside_key, connection = first, None
        beside = self.area_of(level_id, beside_key)
        if connection is not None:
            candidates: list[tuple[tuple[int, float, float], Space]] = []
            for room in rooms:
                if any(c in self.assigned for c in room.cells) or self.placed_in(set(room.cells)):
                    continue
                joins: list[tuple[int, float, float]] = []
                for c in room.cells:
                    for side in SIDES:
                        other = world.across(c, side)
                        if other in beside:
                            letter = world.edge(c, side)
                            if connection.via in ("door", "secret_door") and letter not in DOORS:
                                continue
                            joins.append(world.edge_order(c, side))
                if joins:
                    candidates.append((min(joins), room))
            if candidates:
                room = min(candidates, key=lambda item: item[0])[1]
                self.assign(level_id, key.key, set(room.cells))
                return
        block = self.synthesized_block(level_id, beside)
        state.synthesized.add(key.key)
        state.synth_cells |= block
        state.areas[key.key] = set(block)
        for wcell in block:
            self.assigned[wcell] = (level_id, key.key)
        shared: list[tuple[tuple[int, float, float], WCell, str]] = []
        for wcell in block:
            for side in SIDES:
                other = world.across(wcell, side)
                if other in beside:
                    shared.append((world.edge_order(wcell, side), wcell, side))
        if shared:
            _order, wcell, side = min(shared)
            self.synth_doors[level_id].append((wcell, side))

    def synthesized_block(self, level_id: LevelId, beside: set[WCell]) -> set[WCell]:
        """The 2 by 2 block of rock for a synthesized area beside an area."""
        world = self.world
        panels = {c[0] for c in beside} or {grid.id for grid in self.levels[level_id].grids}
        used = set(self.assigned) | {c for state in self.levels.values() for c in state.synth_cells}
        best_adjacent: list[tuple[tuple[int, float, float], set[WCell]]] = []
        best_lonely: list[tuple[tuple[int, float, float], set[WCell]]] = []
        for panel_id in sorted(panels, key=lambda p: world.grids[p].index):
            grid = world.grids[panel_id]
            for y in range(grid.height - 1):
                for x in range(grid.width - 1):
                    block = {(panel_id, x + i, y + j) for i in (0, 1) for j in (0, 1)}
                    if any(world.letter(c) != "#" or c in used for c in block):
                        continue
                    touches_beside = False
                    touches_other = False
                    lonely = True
                    for c in block:
                        for side in SIDES:
                            other = world.across(c, side)
                            if other is None or other in block:
                                continue
                            if other in beside:
                                touches_beside = True
                            elif world.letter(other) in ".~" or other in used:
                                touches_other = True
                        for n in world.within(c, 1):
                            if n not in block and (world.letter(n) in ".~" or n in used):
                                lonely = False
                    order = world.order((panel_id, x, y))
                    if touches_beside and not touches_other:
                        best_adjacent.append((order, block))
                    if lonely:
                        best_lonely.append((order, block))
        if best_adjacent:
            return min(best_adjacent, key=lambda item: item[0])[1]
        if best_lonely:
            return min(best_lonely, key=lambda item: item[0])[1]
        raise ConventionGap(f"no 2 by 2 block of rock on level {level_id} for a synthesized area")

    def join_unlabelled(self, rooms: list[Space], space_of: dict[WCell, Space]) -> None:
        """Unlabelled walled rooms and unkeyed parts behind printed lines join the keyed areas they open onto."""
        world = self.world
        changed = True
        while changed:
            before = len(self.assigned)
            for room in rooms:
                if (
                    any(c in self.assigned or c in self.corridor_runs for c in room.cells)
                    or self.placed_in(set(room.cells))
                    or room.cells & self.outside
                ):
                    continue
                doors: list[tuple[tuple[int, float, float], tuple[LevelId, str]]] = []
                for c in room.cells:
                    for side in SIDES:
                        if world.edge(c, side) in DOORS:
                            other = world.across(c, side)
                            if other is not None and other in self.assigned:
                                doors.append((world.edge_order(c, side), self.assigned[other]))
                if doors:
                    level_id, key = min(doors, key=lambda item: item[0])[1]
                    self.assign(level_id, key, set(room.cells))
            for space in world.spaces():
                unkeyed = {
                    c
                    for c in space.cells
                    if c not in self.assigned
                    and c not in self.outside
                    and c not in space.open_ground
                    and c not in self.corridor_runs
                }
                keyed = {c for c in space.cells if c in self.assigned}
                if not unkeyed or not keyed:
                    continue
                parts = world.walk(list(unkeyed), self.open_within(unkeyed))
                lines: list[tuple[tuple[int, float, float], tuple[LevelId, str]]] = []
                for c in parts:
                    for side in SIDES:
                        if world.edge(c, side) == "p":
                            other = world.across(c, side)
                            if other is not None and other in self.assigned:
                                lines.append((world.edge_order(c, side), self.assigned[other]))
                if lines and all(
                    world.edge(c, side) in "pwndls" or world.across(c, side) in parts for c in parts for side in SIDES
                ):
                    level_id, key = min(lines, key=lambda item: item[0])[1]
                    self.assign(level_id, key, set(parts))
            self.join_rows(rooms)
            changed = len(self.assigned) > before

    @staticmethod
    def open_within(cells: set[WCell]):
        """A step rule: across open edges only, onto the given cells."""

        def crosses(a: WCell, side: str, letter: str, b: WCell) -> bool:
            return letter == "o" and b in cells

        return crosses

    def join_rows(self, rooms: list[Space]) -> bool:
        """A row of unlabelled rooms beside a keyed room of the same width and height joins that key."""
        world = self.world
        shapes: dict[Space, tuple[Cell, Cell]] = {}
        for room in rooms:
            xs = [c[1] for c in room.cells]
            ys = [c[2] for c in room.cells]
            size = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
            if size[0] * size[1] == len(room.cells) and len({c[0] for c in room.cells}) == 1:
                shapes[room] = ((min(xs), min(ys)), size)
        changed = False
        for room, ((x, y), (w, h)) in shapes.items():
            keyed = {self.assigned[c] for c in room.cells if c in self.assigned}
            if len(keyed) != 1:
                continue
            level_id, key = next(iter(keyed))
            panel = next(iter(room.cells))[0]
            for dx, dy in ((w, 0), (-w, 0), (0, h), (0, -h)):
                chain: list[Space] = []
                cx, cy = x + dx, y + dy
                while True:
                    nxt = next(
                        (
                            r
                            for r, ((rx, ry), size) in shapes.items()
                            if next(iter(r.cells))[0] == panel and (rx, ry) == (cx, cy) and size == (w, h)
                        ),
                        None,
                    )
                    if nxt is None or any(c in self.assigned for c in nxt.cells) or self.placed_in(set(nxt.cells)):
                        break
                    chain.append(nxt)
                    cx, cy = cx + dx, cy + dy
                if len(chain) >= 2:
                    for member in chain:
                        self.assign(level_id, key, set(member.cells))
                    changed = True
        del world
        return changed

    def synthesize_door(self, level_id: LevelId, group: list[Space]) -> None:
        """Give a keyed walled structure with no door a synthesized door on the wall nearest the level's first key."""
        world = self.world
        first_key = next((k for k in self.keys[level_id] if any(p.key == k for p in self.placed)), None)
        key_cell = next((p.cell for p in self.placed if first_key is not None and p.key == first_key), None)
        cells = {c for room in group for c in room.cells}
        candidates: list[tuple[int, tuple[int, float, float], WCell, str]] = []
        fallback: list[tuple[int, tuple[int, float, float], WCell, str]] = []
        distances = world.walk([key_cell], world.walking_floor) if key_cell is not None else {}
        for c in cells:
            for side in SIDES:
                if world.edge(c, side) not in "wn":
                    continue
                other = world.across(c, side)
                order = world.edge_order(c, side)
                if other is not None and world.is_floor(other) and other not in cells:
                    candidates.append((distances.get(other, 10**9), order, c, side))
                if key_cell is not None and c[0] == key_cell[0]:
                    fallback.append((chebyshev(world.cell(c), world.cell(key_cell)), order, c, side))
        chosen = min(candidates) if candidates else (min(fallback) if fallback else None)
        if chosen is None:
            raise ConventionGap(f"no wall edge for a synthesized door on level {level_id}")
        _distance, _order, c, side = chosen
        world.grid(c).set_edge(world.cell(c), side, "d")

    # ----- doors -----

    def doors(self) -> None:
        """Apply the legend to locked doors and put the prose's locks on their doors."""
        world = self.world
        for grid in world.grids.values():
            legend = grid.regions[0].legend
            if "locked_door" in legend.symbols:
                continue
            for edge, letter in list(grid.edges.items()):
                if letter == "l":
                    grid.edges[edge] = "d"
        for level_id in self.level_order:
            content = self.contents.get(level_id)
            if content is None:
                continue
            for area in content.areas:
                for connection in area.connections:
                    if not connection.door_locked or connection.via not in ("door", "secret_door"):
                        continue
                    target = self.resolve_target(level_id, connection)
                    if target is None or target[1] is None:
                        continue
                    start = self.area_of(level_id, area.key)
                    goal = self.area_of(*target)
                    if not start or not goal:
                        continue
                    path = world.shortest_path(world.first(start), world.first(goal), world.walking_floor)
                    for a, b in pairwise(path):
                        side = next(s for s in SIDES if world.across(a, s) == b)
                        letter = world.edge(a, side)
                        if letter in DOORS:
                            if letter != "l":
                                self.locked[level_id].append((a, side))
                            break

    # ----- placement -----

    def place_panels(self) -> None:
        """Put each level's panels on its grid: west to east in page order, turned north-up, two cells apart."""
        for state in self.levels.values():
            x = 0
            placements: dict[str, tuple[Cell, int]] = {}
            previous: Grid | None = None
            for grid in state.grids:
                rotation = rotation_for(grid.north)
                width, _height = rotated_size((grid.width, grid.height), rotation)
                y = 0
                if previous is not None:
                    pair = self.half_pair_of(previous.id, grid.id)
                    if pair is not None and not pair.joined:
                        y = placements[previous.id][0][1] + pair.rows
                placements[grid.id] = ((x, y), rotation)
                x += width + 2
                previous = grid
            state.placements = placements

    def half_pair_of(self, first: str, second: str):
        """The half pair that two panels' regions form, if any."""
        for pair in self.world.half_pairs:
            regions = {part.region for part in self.world.grids[first].source.regions} | {
                part.region for part in self.world.grids[second].source.regions
            }
            if set(pair.regions) <= regions:
                return pair
        return None

    def to_level(self, level_id: LevelId, wcell: WCell) -> Cell:
        """A world cell's place on a level's grid, before the final shift."""
        origin, rotation = self.levels[level_id].placements[wcell[0]]
        grid = self.world.grid(wcell)
        x, y = rotate_cell(self.world.cell(wcell), (grid.width, grid.height), rotation)
        return (origin[0] + x, origin[1] + y)

    def to_level_side(self, level_id: LevelId, wcell: WCell, side: str) -> str:
        """A panel side's name on the level grid."""
        _origin, rotation = self.levels[level_id].placements[wcell[0]]
        return rotate_side(side, rotation)

    def level_cells(self, level_id: LevelId) -> set[WCell]:
        """Every cell that is floor or water on a level: owned cells and synthesized cells."""
        state = self.levels[level_id]
        owned = {wcell for wcell, owner in self.owner.items() if owner == level_id}
        return owned | state.synth_cells

    def shift_levels(self) -> None:
        """Size each level's grid to its panels plus one cell of rock beyond its floor, and shift the origins."""
        for level_id, state in self.levels.items():
            if not state.placements:
                continue
            xs: list[int] = []
            ys: list[int] = []
            for grid in state.grids:
                origin, rotation = state.placements[grid.id]
                width, height = rotated_size((grid.width, grid.height), rotation)
                xs += [origin[0], origin[0] + width - 1]
                ys += [origin[1], origin[1] + height - 1]
            for wcell in self.level_cells(level_id):
                x, y = self.to_level(level_id, wcell)
                xs += [x - 1, x + 1]
                ys += [y - 1, y + 1]
            min_x, min_y = min(xs), min(ys)
            state.placements = {
                panel: ((origin[0] - min_x, origin[1] - min_y), rotation)
                for panel, (origin, rotation) in state.placements.items()
            }
            state.width = max(xs) - min_x + 1
            state.height = max(ys) - min_y + 1

    # ----- cuts and passages -----

    def cuts(self) -> None:
        """Wall off each cut between levels and lay its passages, vestibules first; then link the halves left apart."""
        world = self.world
        self.far_used = set()
        door_cuts: list[tuple[tuple[int, float, float], WCell, str, WCell]] = []
        for wcell, owner in self.owner.items():
            for side in SIDES:
                if (wcell, side) in world.fold:
                    continue
                other = world.across(wcell, side)
                if other is None or other not in self.owner or self.owner[other] == owner:
                    continue
                if world.edge(wcell, side) in DOORS and self.level_index(owner) < self.level_index(self.owner[other]):
                    door_cuts.append((world.edge_order(wcell, side), wcell, side, other))
        for _order, a, side, b in sorted(door_cuts):
            self.vestibule(a, side, b)
        passable: list[tuple[tuple[int, float, float], WCell, str, WCell]] = []
        for wcell, owner in self.owner.items():
            for side in SIDES:
                if (wcell, side) in world.fold:
                    continue
                other = world.across(wcell, side)
                if other is None or other not in self.owner or self.owner[other] == owner:
                    continue
                self.levels[owner].overrides[(wcell, side)] = "w"
                letter = world.edge(wcell, side)
                if letter in PASSABLE and self.level_index(owner) < self.level_index(self.owner[other]):
                    passable.append((world.edge_order(wcell, side), wcell, side, other))
        for _order, a, side, b in sorted(passable):
            self.passage_pair(a, side, b)
        for link in world.fold_links:
            a, b = link.first, link.second
            if a not in self.owner or b not in self.owner:
                continue
            side = world.grid(a).fold_side or "east"
            self.passage_pair(a, side, b, force=True)

    def passage_pair(self, a: WCell, side: str, b: WCell, force: bool = False) -> bool:
        """Lay a passage on each side of an edge between two levels' cells, unless a far cell is already used."""
        level_a, level_b = self.owner[a], self.owner[b]
        la, lb = self.to_level(level_a, a), self.to_level(level_b, b)
        side_a = self.to_level_side(level_a, a, side)
        side_b = self.to_level_side(level_b, b, OPPOSITE[side])
        far_a, far_b = step(la, side_a), step(lb, side_b)
        if not force and ((level_a, far_a) in self.far_used or (level_b, far_b) in self.far_used):
            return False
        self.far_used |= {(level_a, far_a), (level_b, far_b)}
        self.levels[level_a].overrides[(a, side)] = "w"
        self.levels[level_b].overrides[(b, OPPOSITE[side])] = "w"
        self.levels[level_a].passages.append((a, side, b, level_b))
        self.levels[level_b].passages.append((b, OPPOSITE[side], a, level_a))
        return True

    def vestibule(self, a: WCell, side: str, b: WCell) -> None:
        """Give a door on a cut a vestibule on the later level's side, or on the earlier's, or drop the door."""
        world = self.world
        if self.try_vestibule(a, side, b) or self.try_vestibule(b, OPPOSITE[side], a):
            return
        letter = world.edge(a, side)
        world.grid(a).set_edge(world.cell(a), side, "o")
        self.passage_pair(a, side, b)
        del letter

    def try_vestibule(self, a: WCell, side: str, v: WCell) -> bool:
        """Try to take the cell beyond a door into the earlier side's level as a vestibule."""
        world = self.world
        earlier, later = self.owner[a], self.owner[v]
        if any(world.edge(v, s) in DOORS for s in SIDES if s != OPPOSITE[side]):
            return False
        for grid in self.levels[later].grids:
            for symbol in grid.panel.symbols:
                if symbol.kind in LINK_SYMBOLS and v in {(grid.id, *parse_cell(text)) for text in symbol.cells}:
                    return False
        later_floor = {c for c, owner in self.owner.items() if owner == later and world.letter(c) in ".~"}
        remaining = later_floor - {v}
        if remaining:
            before = self.component_count(later_floor)
            after = self.component_count(remaining)
            if after > before:
                return False
        area = self.assigned.get(v)
        if area is not None:
            cells = self.levels[area[0]].areas[area[1]] - {v}
            if not cells or self.component_count(cells) > 1:
                return False
        sides: list[tuple[str, WCell]] = []
        for s in SIDES:
            if s == OPPOSITE[side]:
                continue
            other = world.across(v, s)
            if other is None or self.owner.get(other) != later or world.edge(v, s) not in PASSABLE:
                continue
            lv = self.to_level(earlier, v)
            lo = self.to_level(later, other)
            far_v = step(lv, self.to_level_side(earlier, v, s))
            far_o = step(lo, self.to_level_side(later, other, OPPOSITE[s]))
            if (earlier, far_v) in self.far_used or (later, far_o) in self.far_used:
                continue
            sides.append((s, other))
        if not sides:
            return False

        def free(s: str, other: WCell) -> bool:
            for t in SIDES:
                neighbour = world.across(other, t)
                if neighbour is None or neighbour == v:
                    continue
                if self.owner.get(neighbour) == earlier and world.edge(other, t) in PASSABLE + DOORS:
                    return False
            return True

        free_sides = [(s, other) for s, other in sides if free(s, other)]
        pool = free_sides or sides
        opposite = [(s, other) for s, other in pool if s == side]
        chosen = opposite[0] if opposite else min(pool, key=lambda item: world.edge_order(v, item[0]))
        if area is not None:
            self.levels[area[0]].areas[area[1]].discard(v)
            del self.assigned[v]
        self.owner[v] = earlier
        self.passage_pair(v, chosen[0], chosen[1])
        return True

    def component_count(self, cells: set[WCell]) -> int:
        """How many pieces some cells fall into when walked across passable edges and doors."""
        world = self.world
        seen: set[WCell] = set()
        count = 0
        for start in cells:
            if start in seen:
                continue
            count += 1
            seen |= set(world.walk([start], lambda a, s, letter, b: letter in PASSABLE + DOORS and b in cells))
        return count

    # ----- entrances and links -----

    def entrance(self, dungeon_id: str) -> Entrance | None:
        """The dungeon's entrance by the entrance rules."""
        world = self.world
        levels = [
            level_id
            for level_id in self.level_order
            if level_id[0] == dungeon_id and self.levels[level_id].verdict == "traced"
        ]
        if not levels:
            return None
        area_keys = [
            (level_id, key.key) for level_id in levels for key in self.keys[level_id] if self.area_of(level_id, key.key)
        ]
        chosen: tuple[LevelId, str] | None = None
        opening: Opening | None = None
        for level in self.mapread.levels:
            if level.dungeon_id == dungeon_id and level.entrance_key is not None:
                for level_id, key in area_keys:
                    if self.key_of(level_id, key) is not None and self.key_of(level_id, key).matches(
                        level.entrance_key
                    ):
                        chosen = (level_id, key)
                        break
            if chosen:
                break
        if chosen is None:
            for level_id, key in area_keys:
                if self.key_of(level_id, key).name.strip().lower() in ENTRANCE_TITLES:
                    chosen = (level_id, key)
                    break
        if chosen is None:
            reached: dict[tuple[LevelId, str], list[tuple[int, tuple[int, float, float], Opening]]] = defaultdict(list)
            for level_id in levels:
                floor = self.level_floor(level_id)
                for candidate in self.openings.get(level_id, []):
                    distances: dict[WCell, int] = {}
                    frontier = list(candidate.inner_cells)
                    for c in frontier:
                        distances[c] = 0
                    index = 0
                    while index < len(frontier):
                        c = frontier[index]
                        index += 1
                        if c in self.assigned:
                            reached[self.assigned[c]].append((distances[c], candidate.order(world), candidate))
                            continue
                        for s in SIDES:
                            other = world.across(c, s)
                            if (
                                other is None
                                or other in distances
                                or other not in floor
                                or world.edge(c, s) not in PASSABLE
                            ):
                                continue
                            distances[other] = distances[c] + 1
                            frontier.append(other)
            for level_id, key in area_keys:
                if (level_id, key) in reached:
                    chosen = (level_id, key)
                    opening = min(reached[chosen], key=lambda item: item[:2])[2]
                    break
        if chosen is None:
            counts: list[tuple[int, int, tuple[LevelId, str]]] = []
            for index, (level_id, key) in enumerate(area_keys):
                count = 0
                for c in self.area_of(level_id, key):
                    for s in SIDES:
                        other = world.across(c, s)
                        if other is not None and world.is_floor(other) and self.is_outside(other):
                            count += 1
                counts.append((-count, index, (level_id, key)))
            if counts and min(counts)[0] < 0:
                chosen = min(counts)[2]
        if chosen is None and area_keys:
            chosen = area_keys[0]
        if chosen is None:
            return None
        level_id, key = chosen
        area = self.area_of(level_id, key)
        first = world.first(area)
        if opening is None and self.openings.get(level_id):
            ranked = []
            for candidate in self.openings[level_id]:
                distances = world.walk([candidate.middle(world)], world.walking_floor)
                ranked.append((distances.get(first, 10**9), candidate.order(world), candidate))
            opening = min(ranked, key=lambda item: item[:2])[2]
        if opening is None:
            cell = first
        else:
            middle = opening.middle(world)
            inner = [c for c in opening.inner_cells if c in area]
            if middle in area:
                cell = middle
            elif inner:
                cell = min(inner, key=lambda c: (chebyshev(world.cell(c), world.cell(middle)), world.order(c)))
            else:
                cell = middle
        owner = self.owner.get(cell, level_id)
        return Entrance(level=owner[1], cell=cell_key(self.final_cell(owner, cell)))

    def final_cell(self, level_id: LevelId, wcell: WCell) -> Cell:
        """A world cell's place on the level's final grid."""
        return self.to_level(level_id, wcell)

    def links(self) -> None:
        """Realize each link as transitions on the stair, ladder, trapdoor, and chute symbols beside its areas."""
        seen: set[tuple[frozenset[tuple[LevelId, str]], str]] = set()
        for level_id in self.level_order:
            content = self.contents.get(level_id)
            if content is None or self.levels[level_id].verdict != "traced":
                continue
            for area in content.areas:
                if not self.area_of(level_id, area.key):
                    continue
                for connection in area.connections:
                    target = self.resolve_target(level_id, connection)
                    if target is None:
                        continue
                    target_level, target_key = target
                    if target_level == level_id or self.levels[target_level].verdict != "traced":
                        continue
                    if target_key is None:
                        raise ConventionGap(
                            f"connection from {level_id}/{area.key} names only level {target_level}: no target area"
                        )
                    if not self.area_of(target_level, target_key):
                        continue
                    if self.passages_make(level_id, area.key, target_level, target_key):
                        continue
                    if connection.via not in LINK_VIAS:
                        raise ConventionGap(
                            f"connection from {level_id}/{area.key} to {target} by {connection.via!r}: "
                            "no transition kind for it"
                        )
                    identity = (
                        frozenset({(level_id, area.key), (target_level, target_key)}),
                        LINK_VIAS[connection.via],
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    self.realize_link(Link((level_id, area.key), (target_level, target_key), LINK_VIAS[connection.via]))

    def passages_make(self, level_id: LevelId, key: str, target_level: LevelId, target_key: str) -> bool:
        """Whether a passage pair across a cut already joins two areas, through a door, a vestibule, or an open edge."""
        world = self.world

        def around(level: LevelId, area_key: str) -> set[Cell]:
            cells = self.area_of(level, area_key)
            near = set(cells)
            for c in cells:
                for side in SIDES:
                    other = world.across(c, side)
                    if other is not None and world.edge(c, side) in PASSABLE + DOORS and other in self.owner:
                        near.add(other)
            return {self.to_level(level, c) for c in near if c[0] in self.levels[level].placements}

        start, goal = around(level_id, key), around(target_level, target_key)
        for cell, _side, to_cell, to_level in self.levels[level_id].passages:
            if (
                to_level == target_level
                and self.to_level(level_id, cell) in start
                and self.to_level(to_level, to_cell) in goal
            ):
                return True
        return False

    def symbol_ends(self, level_id: LevelId, key: str, direction: str | None) -> list[tuple[Cell, str | None]]:
        """Where the ends of a link stand beside an area: one cell per symbol, in north-then-west order."""
        world = self.world
        area = self.area_of(level_id, key)
        first = self.to_level(level_id, world.first(area))
        ends: list[Cell] = []
        for grid in self.levels[level_id].grids:
            for symbol in grid.panel.symbols:
                if symbol.kind not in LINK_SYMBOLS:
                    continue
                if symbol.direction is not None and direction is not None and symbol.direction != direction:
                    continue
                cells = [(grid.id, *parse_cell(text)) for text in symbol.cells]
                touching = any(c in area or any(world.across(c, s) in area for s in SIDES) for c in cells)
                if not touching:
                    continue
                level_cells = [self.to_level(level_id, c) for c in cells]
                end = max(level_cells, key=lambda c: (chebyshev(c, first), (-c[1], -c[0])))
                ends.append((end, symbol.direction))
        return sorted(ends, key=lambda item: (item[0][1], item[0][0]))

    def realize_link(self, link: Link) -> None:
        """Place a link's transitions at both ends and pair them in north-then-west order."""
        world = self.world
        (start_level, start_key), (target_level, target_key) = link.start, link.target
        direction: str | None = None
        if target_level[1] > start_level[1]:
            direction = "down"
        elif target_level[1] < start_level[1]:
            direction = "up"
        back = {"down": "up", "up": "down"}.get(direction or "")
        starts = self.symbol_ends(start_level, start_key, direction)
        targets = self.symbol_ends(target_level, target_key, back)
        start_first = self.to_level(start_level, world.first(self.area_of(start_level, start_key)))
        target_first = self.to_level(target_level, world.first(self.area_of(target_level, target_key)))
        if not starts:
            starts = [(start_first, None)]
        if not targets:
            targets = [(target_first, None)]
        if link.means == "stairs":
            start_panel = world.grid(world.first(self.area_of(start_level, start_key))).index
            target_panel = world.grid(world.first(self.area_of(target_level, target_key))).index
            start_first_placed = (start_level[0] == target_level[0] and start_panel <= target_panel) or (
                start_level[0] != target_level[0] and self.level_index(start_level) < self.level_index(target_level)
            )
            count = max(len(starts), len(targets))
            for index in range(count):
                if index < len(starts):
                    cell, recorded = starts[index]
                    to_cell = targets[index][0] if index < len(targets) else target_first
                    kind = self.stairs_kind(direction, recorded, start=start_first_placed)
                    self.levels[start_level].transitions.append(
                        Transition(cell=cell_key(cell), kind=kind, to_level=target_level[1], to_cell=cell_key(to_cell))
                    )
                if index < len(targets):
                    cell, recorded = targets[index]
                    to_cell = starts[index][0] if index < len(starts) else start_first
                    kind = self.stairs_kind(back, recorded, start=not start_first_placed)
                    self.levels[target_level].transitions.append(
                        Transition(cell=cell_key(cell), kind=kind, to_level=start_level[1], to_cell=cell_key(to_cell))
                    )
            return
        for index, (cell, _recorded) in enumerate(starts):
            to_cell = targets[index][0] if index < len(targets) else target_first
            self.levels[start_level].transitions.append(
                Transition(cell=cell_key(cell), kind=link.means, to_level=target_level[1], to_cell=cell_key(to_cell))
            )

    @staticmethod
    def stairs_kind(direction: str | None, recorded: str | None, start: bool) -> str:
        """The transition kind at one end of a stairs link.

        With a direction, the end goes that way. With none, the symbol's recorded direction decides, and with none
        recorded, the end on the panel first in placement order (`start` true) is `stairs_down` and the other end
        `stairs_up`.
        """
        way = direction or recorded or ("down" if start else "up")
        return "stairs_down" if way == "down" else "stairs_up"

    # ----- the level grids -----

    def build_level(self, level_id: LevelId) -> Level:
        """Write one level's grid, edges, areas, keys, transitions, and passages in level coordinates."""
        world = self.world
        state = self.levels[level_id]
        if state.verdict != "traced":
            return Level(number=state.number, verdict="fallback", reason=state.reason or "no map page")
        width, height = state.width, state.height
        cells = [["#"] * width for _ in range(height)]
        back: dict[Cell, WCell] = {}
        for grid in state.grids:
            for y in range(grid.height):
                for x in range(grid.width):
                    wcell = (grid.id, x, y)
                    back[self.to_level(level_id, wcell)] = wcell
        self.level_to_world[level_id] = back
        floor = self.level_cells(level_id)
        for wcell in floor:
            x, y = self.to_level(level_id, wcell)
            letter = world.letter(wcell)
            if wcell in state.synth_cells or wcell in self.water_keyed:
                letter = "."
            cells[y][x] = letter
        vertical = [["-"] * (width + 1) for _ in range(height)]
        horizontal = [["-"] * width for _ in range(height + 1)]

        def occupied(cell: Cell) -> bool:
            return 0 <= cell[0] < width and 0 <= cell[1] < height and cells[cell[1]][cell[0]] != "#"

        def panel_letter(cell: Cell, side: str) -> str:
            wcell = back.get(cell)
            if wcell is None:
                return "-"
            _origin, rotation = state.placements[wcell[0]]
            panel_side = rotate_side(side, (360 - rotation) % 360)
            override = state.overrides.get((wcell, panel_side))
            if override is None:
                across = world.across(wcell, panel_side)
                if across is not None:
                    override = state.overrides.get((across, OPPOSITE[panel_side]))
            if override is not None:
                return override
            if wcell in state.synth_cells:
                other = world.across(wcell, panel_side)
                if (wcell, panel_side) in self.synth_doors[level_id] or (
                    other is not None and (other, OPPOSITE[panel_side]) in self.synth_doors[level_id]
                ):
                    return "d"
                return "o" if other in state.synth_cells else "w"
            other = world.across(wcell, panel_side)
            if other is not None and other in state.synth_cells:
                if (other, OPPOSITE[panel_side]) in self.synth_doors[level_id]:
                    return "d"
                return "w"
            return world.grid(wcell).edge(world.cell(wcell), panel_side)

        for y in range(height):
            for x in range(width):
                for side, store, index in (
                    ("west", vertical, (y, x)),
                    ("east", vertical, (y, x + 1)),
                    ("north", horizontal, (y, x)),
                    ("south", horizontal, (y + 1, x)),
                ):
                    here = (x, y)
                    there = step(here, side)
                    if not occupied(here) and not occupied(there):
                        continue
                    letter = panel_letter(here, side) if occupied(here) or here in back else "-"
                    if letter == "-":
                        letter = panel_letter(there, OPPOSITE[side])
                    if letter == "-":
                        letter = "w"
                    store[index[0]][index[1]] = letter
        edges = EdgeGrids(
            vertical=tuple("".join(row) for row in vertical), horizontal=tuple("".join(row) for row in horizontal)
        )
        areas: dict[str, Area] = {}
        for key, members in state.areas.items():
            if not members:
                continue
            areas[key] = Area(
                cells=tuple(cell_key(self.to_level(level_id, c)) for c in members), synthesized=key in state.synthesized
            )
        keys = [
            PlacedKey(label=p.label, cell=cell_key(self.to_level(level_id, p.cell)), area=p.key.key, moved=p.moved)
            for p in self.placed
            if p.key.level == level_id and p.key.key in areas
        ]
        panels = tuple(
            LevelPanel(panel=grid.id, origin=state.placements[grid.id][0], rotation=state.placements[grid.id][1])
            for grid in state.grids
        )
        passages = tuple(
            Passage(
                cell=cell_key(self.to_level(level_id, cell)),
                side=self.to_level_side(level_id, cell, side),
                to_level=to_level[1],
                to_cell=cell_key(self.to_level(to_level, to_cell)),
            )
            for cell, side, to_cell, to_level in state.passages
        )
        locked = tuple(
            edge_key(self.to_level(level_id, wcell), self.to_level_side(level_id, wcell, side))
            for wcell, side in self.locked[level_id]
            if wcell in floor
        )
        return Level(
            number=state.number,
            verdict="traced",
            panels=panels,
            cells=tuple("".join(row) for row in cells),
            edges=edges,
            areas=areas,
            keys=tuple(keys),
            transitions=tuple(state.transitions),
            passages=passages,
            locked_doors=locked,
        )

    def fallback_reason(self, level_id: LevelId) -> str:
        """Why a level with no registered panel fell back: the reason of the first region on its survey map pages.

        The conventions give no text for a fallback level's `reason`, so this is the region's own reason, or
        `no map page` when the survey lists no page with a region.
        """
        pages: tuple[int, ...] = ()
        for dungeon in self.survey.dungeons:
            for level in dungeon.levels:
                if (dungeon.id, level.number) == level_id:
                    pages = level.map_pages
        for page in pages:
            for region in self.reading.regions:
                if region.page == page and region.reason:
                    return region.reason
        return "no map page"

    # ----- the run -----

    def run(self) -> GeometryTruth:
        """Apply the rules in their order and return the resolved document."""
        for level_id, state in self.levels.items():
            if state.grids:
                continue
            state.verdict = "fallback"
            state.reason = self.fallback_reason(level_id)
        self.world.mark_open_ground()
        self.read_marks()
        self.partition(keys_only_on_floor=True)
        self.cave_mouth_rule()
        self.find_openings()
        self.place_letters()
        self.place_keys()
        self.partition(keys_only_on_floor=False)
        self.cave_mouth_rule()
        self.find_openings()
        self.areas()
        self.doors()
        self.place_panels()
        self.cuts()
        self.shift_levels()
        self.links()
        dungeons: list[Dungeon] = []
        for dungeon in self.survey.dungeons:
            levels = tuple(self.build_level((dungeon.id, level.number)) for level in dungeon.levels)
            dungeons.append(
                Dungeon(id=dungeon.id, name=dungeon.name, entrance=self.entrance(dungeon.id), levels=levels)
            )
        return self.reading.model_copy(update={"dungeons": tuple(dungeons), "repairs": (), "before_repair": None})


def resolve(
    reading: GeometryTruth, survey: SurveyIndex, contents: dict[LevelId, LevelContent], mapread: MapReading
) -> GeometryTruth:
    """Resolve a map's reading into its levels and areas by the conventions.

    Args:
        reading: A geometry truth document whose reading layer is filled in. Its resolution layer is ignored.
        survey: The survey cache.
        contents: The content caches by level.
        mapread: The map-reading cache.

    Returns:
        The document with its resolution layer derived from the reading, in canonical form.
    """
    return canonical_form(Resolver(reading_of(reading), survey, contents, mapread).run())


def strip_flags(truth: GeometryTruth) -> GeometryTruth:
    """A document with every level's flags removed, for comparison."""
    dungeons = tuple(
        dungeon.model_copy(update={"levels": tuple(level.model_copy(update={"flags": ()}) for level in dungeon.levels)})
        for dungeon in truth.dungeons
    )
    return truth.model_copy(update={"dungeons": dungeons})


def differences(answer: GeometryTruth, result: GeometryTruth) -> list[str]:
    """Where a result differs from an answer, one line per field of each level that differs."""
    lines: list[str] = []
    a, r = strip_flags(canonical_form(answer)), strip_flags(canonical_form(result))
    for dungeon_a in a.dungeons:
        dungeon_r = next((d for d in r.dungeons if d.id == dungeon_a.id), None)
        if dungeon_r is None:
            lines.append(f"{dungeon_a.id}: missing")
            continue
        if dungeon_a.entrance != dungeon_r.entrance:
            lines.append(f"{dungeon_a.id} entrance: answer {dungeon_a.entrance} result {dungeon_r.entrance}")
        for level_a in dungeon_a.levels:
            level_r = next((lv for lv in dungeon_r.levels if lv.number == level_a.number), None)
            name = f"{dungeon_a.id}/{level_a.number}"
            if level_r is None:
                lines.append(f"{name}: missing")
                continue
            for field_name in (
                "verdict",
                "reason",
                "panels",
                "cells",
                "edges",
                "keys",
                "transitions",
                "passages",
                "locked_doors",
            ):
                va, vr = getattr(level_a, field_name), getattr(level_r, field_name)
                if va != vr:
                    detail = ""
                    if field_name in ("keys", "transitions", "passages", "locked_doors"):
                        detail = f" answer {list(va)} result {list(vr)}"
                    elif field_name == "cells":
                        detail = (
                            f" answer {len(va[0]) if va else 0}x{len(va)} result {len(vr[0]) if vr else 0}x{len(vr)}"
                        )
                    elif field_name in ("verdict", "reason", "panels"):
                        detail = f" answer {va} result {vr}"
                    lines.append(f"{name} {field_name}:{detail}")
            for key in sorted(set(level_a.areas) | set(level_r.areas)):
                area_a, area_r = level_a.areas.get(key), level_r.areas.get(key)
                if area_a != area_r:
                    ca = set(area_a.cells) if area_a else set()
                    cr = set(area_r.cells) if area_r else set()
                    lines.append(
                        f"{name} area {key}: answer-only {sorted(ca - cr, key=parse_cell_order)} "
                        f"result-only {sorted(cr - ca, key=parse_cell_order)}"
                        + (
                            f" synthesized {area_a.synthesized if area_a else None}"
                            f"/{area_r.synthesized if area_r else None}"
                            if (area_a and area_r and area_a.synthesized != area_r.synthesized)
                            else ""
                        )
                    )
    return lines


def parse_cell_order(text: str) -> tuple[int, int]:
    """Sort key for cell addresses: row first."""
    x, y = parse_cell(text)
    return (y, x)


def edge_differences(answer: GeometryTruth, result: GeometryTruth, dungeon_id: str, number: int) -> list[str]:
    """Every edge of a level whose letter differs between an answer and a result."""
    level_a = answer.level(dungeon_id, number)
    level_r = result.level(dungeon_id, number)
    if level_a.edges is None or level_r.edges is None or level_a.cells != level_r.cells:
        return ["grids differ"]
    out: list[str] = []
    for cell, side in level_a.edges.positions():
        la, lr = level_a.edges.at(cell, side), level_r.edges.at(cell, side)
        if la != lr:
            out.append(f"{cell_key(cell)}:{side} answer {la} result {lr}")
    return out


def main(argv: list[str]) -> int:
    """Resolve each named map directory and print how its result differs from the committed answer."""
    failures = 0
    for name in argv:
        directory = Path(name)
        if not (directory / "geometry.json").is_file():
            continue
        answer = load_geometry_truth(directory / "geometry.json")
        survey, contents, mapread = load_caches(directory)
        try:
            result = resolve(answer, survey, contents, mapread)
        except ConventionGap as gap:
            print(f"{directory.name}: open case: {gap}")
            failures += 1
            continue
        lines = differences(answer, result)
        if lines:
            failures += 1
            print(f"{directory.name}: {len(lines)} differences")
            for line in lines:
                print("  " + line)
                if line.endswith(" edges:"):
                    name_part = line.split(" ")[0]
                    dungeon_id, number = name_part.split("/")
                    for detail in edge_differences(answer, result, dungeon_id, int(number))[:20]:
                        print("    " + detail)
        else:
            print(f"{directory.name}: exact")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
