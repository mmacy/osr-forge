"""A synthetic map's answer, worked out from its reading by the rules in `docs/tracing-conventions.md`.

The generator never writes an area, a placed key, an entrance, a transition,
or a passage by hand. It applies the conventions to the reading it drew, so
the answer is the one the reference resolution script must reach. It applies
only the rules its maps exercise: halves left apart are 10-foot panels that
aren't turned, and levels that share a panel share all their panels, aren't
turned, and print every key on floor. Where a map would need a rule this
module doesn't apply, or reaches a case the conventions leave open, it raises
[`DesignError`][osrforge.tracing.synthetic._resolve.DesignError] rather than
choose, so a map can't quietly carry an answer the rules don't give.

Everything here works on a board: a level's panels, converted to 10-foot cells
and turned to put their north at the top, copied to their places on the
level's grid. North-then-west order on the board is then the order the rules
ask for, the order `order_key` gives on a panel before its turn. Levels whose
floors meet on the panels they share are worked out together on one board, as
the conventions find rooms and areas before ownership divides the floor, and
then cut apart, each level's grid being the part of the board its own floor
needs.
"""

import heapq
import itertools
import re
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from typing import Literal

from osrforge.contracts.stages import AreaConnection
from osrforge.tracing.fivefoot import convert_five_foot
from osrforge.tracing.synthetic._plan import SIDES, Plan, canonical, neighbour, seed_cells
from osrforge.tracing.synthetic._spec import AreaSpec, LevelSpec, MapSpec, PanelSpec, RegionSpec
from osrforge.tracing.truth import (
    Area,
    EdgeGrids,
    Entrance,
    HalfPair,
    Level,
    LevelPanel,
    Mark,
    Outline,
    Panel,
    PanelRegion,
    Passage,
    PlacedKey,
    Rotation,
    Side,
    Symbol,
    Transition,
    cell_key,
    parse_cell,
    parse_edge,
    rotation_for,
)

type Cell = tuple[int, int]
type EdgeId = tuple[int, int, str]
"""An edge as the cell east or south of it and the side `west` or `north`."""

type Point = tuple[float, float]

PASSABLE = "op"
DOORS = "dls"
BLOCKED = "wndls"
LINK_KINDS = ("stairs", "ladder", "trapdoor", "hinged_floor", "chute", "slide")
_SIDE_NAMES: dict[str, Side] = {"north": "north", "east": "east", "south": "south", "west": "west"}
_STAIRS: dict[str, Literal["stairs_up", "stairs_down"]] = {"up": "stairs_up", "down": "stairs_down"}


class DesignError(ValueError):
    """A map reaches a case this module doesn't resolve, or the conventions leave open."""


def eid(cell: Cell, side: str) -> EdgeId:
    """The canonical id of one side of a cell.

    Args:
        cell: The cell.
        side: The side.

    Returns:
        The edge id.
    """
    named, named_side = canonical(cell, side)
    return (named[0], named[1], named_side)


def edge_cells(edge: EdgeId) -> tuple[Cell, Cell]:
    """The two cells beside an edge: west then east, or north then south.

    Args:
        edge: The edge.

    Returns:
        The cells, either of which may be off the grid.
    """
    x, y, side = edge
    return ((x - 1, y), (x, y)) if side == "west" else ((x, y - 1), (x, y))


def edge_middle(edge: EdgeId) -> Point:
    """The midpoint of an edge.

    Args:
        edge: The edge.

    Returns:
        The point.
    """
    x, y, side = edge
    return (float(x), y + 0.5) if side == "west" else (x + 0.5, float(y))


def order(point: Point) -> tuple[float, float]:
    """The north-then-west sort key of a point on the level grid.

    Args:
        point: The point.

    Returns:
        `(y, x)`.
    """
    return (point[1], point[0])


def cell_order(cell: Cell) -> tuple[int, int]:
    """The north-then-west sort key of a cell.

    Args:
        cell: The cell.

    Returns:
        `(y, x)`.
    """
    return (cell[1], cell[0])


def chebyshev(a: Cell, b: Cell) -> int:
    """The Chebyshev distance between two cells.

    Args:
        a: One cell.
        b: The other.

    Returns:
        The larger of the column difference and the row difference.
    """
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


@dataclass
class Board:
    """A level's panels copied onto the level's grid, with what the rules need to know about each cell and edge."""

    width: int
    height: int
    cells: dict[Cell, str] = field(default_factory=dict[Cell, str])
    edges: dict[EdgeId, str] = field(default_factory=dict[EdgeId, str])
    frame: dict[EdgeId, bool] = field(default_factory=dict[EdgeId, bool])
    """Each panel's frame edges, true for an edge at a fold."""

    owned: set[Cell] = field(default_factory=set[Cell])
    partner: dict[Cell, Cell] = field(default_factory=dict[Cell, Cell])
    fold_side: dict[Cell, str] = field(default_factory=dict[Cell, str])
    marks: list[tuple[Mark, Cell, EdgeId | None, Cell | None]] = field(
        default_factory=list[tuple[Mark, Cell, EdgeId | None, Cell | None]]
    )
    symbols: list[tuple[Symbol, tuple[Cell, ...], EdgeId | None]] = field(
        default_factory=list[tuple[Symbol, tuple[Cell, ...], EdgeId | None]]
    )
    ground_rects: list[tuple[int, int, int, int]] = field(default_factory=list[tuple[int, int, int, int]])
    legends: set[str] = field(default_factory=set[str])
    """The symbols that the legends of the level's regions explain."""

    turns: dict[str, Rotation] = field(default_factory=dict[str, Rotation])
    """Each panel's quarter turn onto the level."""

    seeds: list[tuple[Cell, int]] = field(default_factory=list[tuple[Cell, int]])
    """Each outline's seed cells, with the outline's level."""

    outlines: dict[Cell, set[int]] = field(default_factory=dict[Cell, set[int]])
    """The levels whose outlines hold each owned cell."""

    sizes: dict[str, Cell] = field(default_factory=dict[str, Cell])
    """Each panel's width and height in 10-foot cells, after its turn."""

    def letter(self, cell: Cell, side: str) -> str:
        """The reading's letter on one side of a cell, `-` where no panel reads it."""
        return self.edges.get(eid(cell, side), "-")

    def floor(self, cell: Cell) -> bool:
        """True when a panel reads the cell as floor, inside an outline or not."""
        return self.cells.get(cell) == "."

    def water(self, cell: Cell) -> bool:
        """True when a panel reads the cell as water."""
        return self.cells.get(cell) == "~"

    def outside(self, cell: Cell) -> bool:
        """True for floor or water outside every outline."""
        return self.cells.get(cell) in (".", "~") and cell not in self.owned

    def steps(self, cell: Cell, letters: str) -> Iterator[Cell]:
        """The cells one step away across edges with one of the letters, a fold link counting as open."""
        for side in SIDES:
            if cell in self.partner and self.fold_side[cell] == side:
                if "o" in letters:
                    yield self.partner[cell]
                continue
            if self.letter(cell, side) in letters:
                yield neighbour(cell, side)


@dataclass(frozen=True)
class Opening:
    """One outer opening: edges on one line between consecutive cells."""

    edges: tuple[EdgeId, ...]
    inside: tuple[Cell, ...]
    """The floor cell just inside each edge, in the edges' order."""

    @property
    def middle(self) -> Point:
        """The midpoint of the run of edges."""
        first, last = edge_middle(self.edges[0]), edge_middle(self.edges[-1])
        return ((first[0] + last[0]) / 2.0, (first[1] + last[1]) / 2.0)


@dataclass(frozen=True)
class Placed:
    """A printed key or letter after placement."""

    label: str
    key: str
    cell: Cell
    moved: bool


@dataclass
class Resolved:
    """One level's answer, and what the caches and the other levels need from it."""

    spec: LevelSpec
    board: Board
    origins: dict[str, Cell]
    areas: dict[str, list[Cell]]
    placed: list[Placed]
    openings: list[Opening]
    overrides: dict[EdgeId, str]
    level_cells: dict[Cell, str]
    transitions: list[Transition] = field(default_factory=list[Transition])
    passages: list[Passage] = field(default_factory=list[Passage])
    """Passages and transitions name cells on the board, the target level's board for a destination."""

    area_of: dict[Cell, str] = field(default_factory=dict[Cell, str])
    locked: list[EdgeId] = field(default_factory=list[EdgeId])
    """The doors the prose locks, as Doors says, when the reading doesn't lock them by their letter."""

    frame: tuple[int, int, int, int] | None = None
    """The level's grid as `(x, y, width, height)` on the board, or `None` when it is the whole board. Levels that
    share a panel share a board, and each level's grid is the part of it its own floor needs."""

    def joins(self) -> dict[tuple[str, str], list[tuple[EdgeId, str, str]]]:
        """Each pair of areas a passable edge or a door joins, with the edges and the side of the first area's cell.

        Returns:
            The joining edges by pair, keyed in survey order of the two areas.
        """
        position = {area.key: index for index, area in enumerate(self.spec.areas)}
        found: dict[tuple[str, str], list[tuple[EdgeId, str, str]]] = {}
        for cell, key in sorted(self.area_of.items(), key=lambda item: cell_order(item[0])):
            for side in SIDES:
                other = self.area_of.get(neighbour(cell, side))
                letter = self.level_letter(cell, side)
                if other is None or other == key or letter not in PASSABLE + DOORS:
                    continue
                if position[key] < position[other]:
                    found.setdefault((key, other), []).append((eid(cell, side), side, letter))
        return found

    def level_letter(self, cell: Cell, side: str) -> str:
        """The level's letter on one side of a cell, after the rules change the reading."""
        edge = eid(cell, side)
        if edge in self.overrides:
            return self.overrides[edge]
        return self.board.edges.get(edge, "-")


def names(key: str, description: str, letter: str) -> bool:
    """Whether a key's description names a letter, as Keys and areas defines it.

    Args:
        key: The key.
        description: Its description.
        letter: The letter.

    Returns:
        True when the description quotes the letter, attaches it to the key's
        number, or puts it after "cell", "area", "marked", or "at", ignoring
        case, with a and i never counting after "at".
    """
    text = description.lower()
    wanted = letter.lower()
    if f'"{wanted}"' in text:
        return True
    if re.search(rf"(?<![0-9a-z]){re.escape(key.lower())}{re.escape(wanted)}(?![0-9a-z])", text):
        return True
    for match in re.finditer(r"\b(cell|area|marked|at)\s+\"?([a-z])\"?(?![0-9a-z])", text):
        word, found = match.group(1), match.group(2)
        if found == wanted and not (word == "at" and found in "ai"):
            return True
    return False


def _floor_bfs(board: Board, starts: Iterable[Cell], letters: str, allowed: set[Cell]) -> dict[Cell, int]:
    distance: dict[Cell, int] = {}
    queue: deque[Cell] = deque()
    for start in starts:
        if start in allowed and start not in distance:
            distance[start] = 0
            queue.append(start)
    while queue:
        cell = queue.popleft()
        for step in board.steps(cell, letters):
            if step in allowed and step not in distance:
                distance[step] = distance[cell] + 1
                queue.append(step)
    return distance


class LevelRules:
    """The rules for one level, or for levels that share their panels, up to the areas and doors."""

    def __init__(self, spec: MapSpec, level: LevelSpec, members: tuple[LevelSpec, ...] = ()) -> None:
        """Copy the level's panels onto its grid and find what the drawing shows.

        Args:
            spec: The map.
            level: The level, or for levels that share their panels, one level made of all their areas in survey
                order.
            members: The levels that share the panels, when `level` stands for them, in survey order.
        """
        self.spec = spec
        self.level = level
        self.members = members
        self.keys: dict[str, AreaSpec] = {area.key: area for area in level.areas}
        numbers = frozenset(member.number for member in members) if members else frozenset({level.number})
        self.board, self.origins = _board(spec, level, numbers)
        board = self.board
        self.crossing: set[Cell] = set()
        for symbol, cells, _edge in board.symbols:
            if symbol.kind == "bridge":
                self.crossing |= {cell for cell in cells if board.water(cell) and cell in board.owned}
            elif symbol.kind == "footing":
                raise DesignError("footings aren't resolved by this generator")
        self.walkable: set[Cell] = {cell for cell in board.owned if board.floor(cell)} | self.crossing
        self.spaces: list[list[Cell]] = self._open_spaces()
        self.space_of: dict[Cell, int] = {cell: index for index, space in enumerate(self.spaces) for cell in space}
        self.ground: set[Cell] = self._open_ground()
        self.enclosed: list[bool] = [self._enclosed(space) for space in self.spaces]
        self.walled: list[int] = [
            index
            for index, space in enumerate(self.spaces)
            if self.enclosed[index] and not any(cell in self.ground for cell in space)
        ]
        self.runs: list[list[Cell]] = self._narrow_runs()
        self.structures: list[list[int]] = self._structures()
        self.mouths: dict[EdgeId, Symbol] = {
            edge: symbol for symbol, _cells, edge in board.symbols if symbol.kind == "cave_mouth" and edge is not None
        }
        self.openings: list[Opening] = self._openings()
        self._check_cave_mouths()

    # What the drawing shows.

    def _open_spaces(self) -> list[list[Cell]]:
        board = self.board
        floor = sorted((cell for cell in board.cells if board.floor(cell)), key=cell_order)
        seen: set[Cell] = set()
        spaces: list[list[Cell]] = []
        for start in floor:
            if start in seen:
                continue
            space = sorted(_floor_bfs(board, [start], PASSABLE, set(floor)), key=cell_order)
            seen |= set(space)
            spaces.append(space)
        return spaces

    def _enclosed(self, space: list[Cell]) -> bool:
        members = set(space)
        for cell in space:
            for side in SIDES:
                if cell in self.board.partner and self.board.fold_side[cell] == side:
                    continue
                if neighbour(cell, side) in members:
                    continue
                if self.board.letter(cell, side) not in BLOCKED:
                    return False
        return True

    def _open_ground(self) -> set[Cell]:
        ground: set[Cell] = set()
        for x, y, w, h in self.board.ground_rects:
            inside = {(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
            best: tuple[int, tuple[int, int]] | None = None
            chosen = -1
            for index, space in enumerate(self.spaces):
                count = sum(1 for cell in space if cell in inside)
                if count == 0:
                    continue
                rank = (-count, cell_order(space[0]))
                if best is None or rank < best:
                    best, chosen = rank, index
            if chosen >= 0:
                ground |= {cell for cell in self.spaces[chosen] if cell in inside}
        return ground

    def _structures(self) -> list[list[int]]:
        """The walled structures, each as the indices of its walled rooms: rooms joined by doors with no corridor."""
        parent = {index: index for index in self.walled}

        def find(index: int) -> int:
            while parent[index] != index:
                index = parent[index]
            return index

        for index in self.walled:
            for cell in self.spaces[index]:
                for side in SIDES:
                    other = self.space_of.get(neighbour(cell, side))
                    if (
                        other is not None
                        and other != index
                        and other in parent
                        and self.board.letter(cell, side) in DOORS
                    ):
                        parent[find(index)] = find(other)
        groups: dict[int, list[int]] = {}
        for index in self.walled:
            groups.setdefault(find(index), []).append(index)
        return list(groups.values())

    def _narrow_runs(self) -> list[list[Cell]]:
        board = self.board
        floor = {cell for cell in board.cells if board.floor(cell)}

        def passable_floor(cell: Cell) -> list[Cell]:
            return [step for step in board.steps(cell, PASSABLE) if step in floor]

        blocked: set[Cell] = set()
        for x, y in floor:
            block = [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]
            if not all(cell in floor for cell in block):
                continue
            inner = [
                board.letter((x, y), "east"),
                board.letter((x, y), "south"),
                board.letter((x + 1, y), "south"),
                board.letter((x, y + 1), "east"),
            ]
            if sum(letter in PASSABLE for letter in inner) >= 3:
                blocked |= set(block)
        candidates = {cell for cell in floor if cell not in blocked and len(passable_floor(cell)) <= 2}
        runs: list[list[Cell]] = []
        seen: set[Cell] = set()
        for start in sorted(candidates, key=cell_order):
            if start in seen:
                continue
            run = sorted(_floor_bfs(board, [start], "o", candidates), key=cell_order)
            seen |= set(run)
            if len(run) >= 2:
                runs.append(run)
        return runs

    def _openings(self) -> list[Opening]:
        board = self.board
        found: dict[EdgeId, Cell] = {}
        for edge, letter in board.edges.items():
            if letter == "-":
                continue
            first, second = edge_cells(edge)
            mouth = edge in self.mouths
            if edge in board.frame:
                if board.frame[edge]:
                    continue
                inner = first if first in board.cells else second
                if letter in PASSABLE and board.floor(inner) and inner in board.owned:
                    found[edge] = inner
            for here, there in ((first, second), (second, first)):
                level_floor = board.floor(here) and here in board.owned
                beyond = board.outside(there) or there in self.ground
                if level_floor and here not in self.ground and beyond and (letter in PASSABLE + DOORS or mouth):
                    found[edge] = here
                if level_floor and here in self.ground and board.outside(there) and letter in PASSABLE:
                    found[edge] = here
            if mouth:
                inner = [cell for cell in (first, second) if board.floor(cell) and cell in board.owned]
                if len(inner) != 1:
                    raise DesignError(f"cave mouth {edge} doesn't have level floor on exactly one side")
                found[edge] = inner[0]
        openings: list[Opening] = []
        for side in ("west", "north"):
            lines: dict[int, list[EdgeId]] = {}
            for edge in found:
                if edge[2] == side:
                    lines.setdefault(edge[0] if side == "west" else edge[1], []).append(edge)
            for line in sorted(lines):
                edges = sorted(lines[line], key=lambda item: item[1] if side == "west" else item[0])
                group: list[EdgeId] = []
                for edge in edges:
                    along = edge[1] if side == "west" else edge[0]
                    if group and along != (group[-1][1] if side == "west" else group[-1][0]) + 1:
                        openings.append(Opening(tuple(group), tuple(found[item] for item in group)))
                        group = []
                    group.append(edge)
                if group:
                    openings.append(Opening(tuple(group), tuple(found[item] for item in group)))
        return sorted(openings, key=lambda opening: order(opening.middle))

    def _check_cave_mouths(self) -> None:
        """Make sure the cave-mouth rule under Who owns a cell moves no floor to the outside."""
        board = self.board
        keyed = [cell for _mark, cell, _edge, _leader in board.marks if board.floor(cell) and cell in board.owned]
        everywhere = {cell for cell in board.cells if board.floor(cell)}
        reach_all = _floor_bfs(board, keyed, PASSABLE + DOORS, everywhere)
        without = set(_floor_bfs(_without_mouths(board, self.mouths), keyed, PASSABLE + DOORS, everywhere))
        lost = [cell for cell in reach_all if cell not in without and cell in board.owned and cell not in self.ground]
        if lost:
            raise DesignError(f"floor {sorted(lost)} is reached only through a cave mouth")

    # Distances.

    def walking(self, starts: Iterable[Cell]) -> dict[Cell, int]:
        """Walking distances over the level's floor and crossings, across passable edges and doors.

        Args:
            starts: The cells the walk starts from.

        Returns:
            Each reachable cell's distance.
        """
        return _floor_bfs(self.board, starts, PASSABLE + DOORS, self.walkable)

    def nearest_floor(self, cell: Cell, allowed: Iterable[Cell]) -> Cell:
        """The nearest allowed cell by Chebyshev distance, and of several the first in north-then-west order.

        Args:
            cell: Where to measure from.
            allowed: The candidates.

        Returns:
            The nearest one.
        """
        return min(allowed, key=lambda other: (chebyshev(cell, other), cell_order(other)))

    # Keys.

    def labels(self, mark: Mark) -> list[tuple[str, str]]:
        """A mark's labels, each with what it is: `key`, `suffix`, or `letter`."""
        text = mark.text
        parts = [text] if text.lower() in self.keys else text.split("/")
        found: list[tuple[str, str]] = []
        for part in parts:
            if part.lower() in self.keys:
                found.append((part, "key"))
            elif len(part) > 1 and part[-1].isalpha() and part[:-1].lower() in self.keys:
                found.append((part, "suffix"))
            elif len(part) == 1 and part.isalpha():
                found.append((part, "letter"))
            else:
                raise DesignError(f"label {part!r} matches no key of level {self.level.number}")
        return found

    def place_keys(self) -> list[Placed]:
        """Place every printed key and letter, as Keys and areas says.

        Returns:
            The placed keys and letters, in the order they're printed.
        """
        board = self.board
        placed: list[Placed] = []
        letters: list[tuple[str, Cell]] = []
        on_ground: list[tuple[str, str, Cell]] = []
        settled = [cell for cell in self.walkable if cell not in self.ground and cell not in self.crossing]
        for mark, cell, edge, leader in board.marks:
            for label, kind in self.labels(mark):
                if self.members and (kind == "letter" or edge is not None or leader is not None or cell not in settled):
                    raise DesignError(
                        f"label {label!r} isn't a key printed on floor, which levels sharing a panel need here, "
                        "because placing it needs the first partition"
                    )
                if kind == "letter":
                    letters.append((label, cell))
                    continue
                key = label.lower() if kind == "key" else label[:-1].lower()
                if leader is None and edge is None and cell in self.ground:
                    on_ground.append((label, key, cell))
                    continue
                target = self._place(cell, edge, leader, settled)
                placed.append(Placed(label=label, key=key, cell=target, moved=target != cell))
        taken: dict[int, str] = {}
        for label, key, cell in on_ground:
            index = self._structure_for(key, cell, placed)
            if taken.setdefault(index, key) != key:
                raise DesignError("two keys on open ground take one structure, and their order would decide it")
            if len(self.structures[index]) > 1:
                raise DesignError("a key on open ground taking a structure of several rooms isn't resolved here")
            target = self.nearest_floor(cell, self.spaces[self.structures[index][0]])
            placed.append(Placed(label=label, key=key, cell=target, moved=target != cell))
        dungeon_words = {word.lower() for word in re.split(r"[^0-9A-Za-z]+", self.spec.dungeon.name) if word}
        for label, cell in letters:
            if board.outside(cell) or label.lower() in dungeon_words:
                continue
            namers = [area.key for area in self.level.areas if names(area.key, area.description, label)]
            if not namers:
                continue
            if not (board.floor(cell) and cell in board.owned) or cell in self.ground:
                raise DesignError(f"letter {label!r} at {cell} isn't printed on the level's floor")
            if len(namers) > 1:
                distance = self.walking([cell])
                ranked = sorted(
                    (min(distance.get(item.cell, 10**9) for item in placed if item.key == namer), namer)
                    for namer in namers
                )
                if ranked[0][0] == ranked[1][0]:
                    raise DesignError(f"letter {label!r} is equally near keys {namers}")
                namers = [ranked[0][1]]
            placed.append(Placed(label=label, key=namers[0], cell=cell, moved=False))
        return placed

    def _structure_for(self, key: str, cell: Cell, placed: list[Placed]) -> int:
        """The walled structure a key on open ground takes, as Keys and areas says.

        A structure with a cell within two cells of the key's cell that has no
        other key qualifies, and the nearest by Chebyshev distance wins. Keys on
        open ground don't count as other keys, since their own order would
        decide between them.
        """
        ranked: list[tuple[int, int]] = []
        for index, rooms in enumerate(self.structures):
            cells = [room_cell for room in rooms for room_cell in self.spaces[room]]
            if any(item.cell in cells and item.key != key for item in placed):
                continue
            distance = min(chebyshev(cell, other) for other in cells)
            if distance <= 2:
                ranked.append((distance, index))
        ranked.sort()
        if not ranked:
            raise DesignError(f"key {key!r} on open ground has no structure to take, which isn't resolved here")
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            raise DesignError(f"key {key!r} on open ground is equally near two structures")
        return ranked[0][1]

    def _place(self, cell: Cell, edge: EdgeId | None, leader: Cell | None, settled: list[Cell]) -> Cell:
        board = self.board
        if leader is not None:
            return leader
        if edge is not None:
            if board.edges.get(edge, "-") not in DOORS:
                raise DesignError(f"a key printed on edge {edge} isn't on a door")
            if not any(edge in opening.edges for opening in self.openings):
                raise DesignError(f"a key printed on the inner door {edge} isn't resolved by this generator")
            return self.nearest_floor(cell, settled)
        if board.floor(cell) and cell in board.owned:
            return cell
        if board.water(cell) and cell in board.owned:
            return cell
        if board.outside(cell):
            near = [
                opening
                for opening in self.openings
                if any(chebyshev(cell, other) <= 2 for item in opening.edges for other in edge_cells(item))
            ]
            if near:
                inner = sorted({inside for opening in near for inside in opening.inside}, key=cell_order)
                ranked = sorted(inner, key=lambda other: (chebyshev(cell, other), cell_order(other)))
                if len(ranked) > 1 and chebyshev(cell, ranked[0]) == chebyshev(cell, ranked[1]):
                    raise DesignError(f"the key printed outside at {cell} is equally near two openings")
                return ranked[0]
            return self.nearest_floor(cell, self.walkable)
        return self.nearest_floor(cell, settled)

    # Areas.

    def divide(self, cells: set[Cell], space: set[Cell], sources: list[tuple[str, Cell]]) -> dict[Cell, str]:
        """Give an open space's unclaimed cells to its keys, as Keys and areas says.

        One key takes every cell. Several keys divide the cells by walking
        distance across the whole space, printed open lines counting as walls
        for the walk. A cell at the same distance from two keys goes to the key
        whose placed cell is first in north-then-west order, and of two keys
        placed on one cell, to the earlier in survey order.

        Args:
            cells: The cells to give out.
            space: The whole open space the walk crosses.
            sources: Each key with a placed cell of it.

        Returns:
            Each cell a key takes, with its key. Cells no key reaches are left out.
        """
        keys = {key for key, _cell in sources}
        if len(keys) == 1:
            return dict.fromkeys(cells, next(iter(keys)))
        survey = [area.key for area in self.level.areas]
        best: dict[Cell, tuple[int, tuple[int, int], int]] = {}
        for key, start in sources:
            for cell, distance in _floor_bfs(self.board, [start], "o", space).items():
                rank = (distance, cell_order(start), survey.index(key))
                if cell not in best or rank < best[cell]:
                    best[cell] = rank
        return {cell: survey[rank[2]] for cell, rank in best.items() if cell in cells}

    def areas(self, placed: list[Placed]) -> tuple[dict[Cell, str], dict[EdgeId, str]]:
        """Work out the areas, in the order Keys and areas lists its rules, and the doors they synthesize.

        Args:
            placed: The placed keys.

        Returns:
            Each cell's key, for the cells some key takes, and each synthesized door.
        """
        board = self.board
        claim: dict[Cell, str | None] = {}

        def take(cells: Iterable[Cell], key: str | None) -> None:
            for cell in cells:
                claim.setdefault(cell, key)

        printed = {item.key for item in placed}
        run_cells = {cell: index for index, run in enumerate(self.runs) for cell in run}
        for area in self.level.areas:
            if area.key in printed:
                continue
            if self.members:
                raise DesignError(f"unprinted key {area.key!r} on a panel several levels share isn't resolved here")
            if area.key == self.spec.entrance_key or area.name.lower() in ("entrance", "entranceway"):
                take(self._entrance_chamber(area, placed, run_cells), area.key)
            else:
                raise DesignError(f"unprinted key {area.key!r} isn't an entrance, which this generator needs")
        placed_at: dict[Cell, list[str]] = {}
        for item in placed:
            placed_at.setdefault(item.cell, []).append(item.key)
        unreached: list[set[Cell]] = []
        for index in self.walled:
            room = set(self.spaces[index]) - claim.keys()
            sources = [(item.key, item.cell) for item in placed if item.cell in room]
            if sources:
                divided = self.divide(room, set(self.spaces[index]), sources)
                for cell, key in divided.items():
                    claim[cell] = key
                if room - divided.keys():
                    unreached.append(room - divided.keys())
        for run in self.runs:
            keys = {key for cell in run for key in placed_at.get(cell, [])}
            if len(keys) > 1:
                raise DesignError(f"narrow run {run} holds several keys")
            if keys:
                take(run, keys.pop())
            elif len(run) >= 3:
                take(run, None)
        for space in self.spaces:
            rest = set(space) - claim.keys()
            sources = [(item.key, item.cell) for item in placed if item.cell in rest]
            if not sources:
                continue
            divided = self.divide(rest, set(space), sources)
            for cell, key in divided.items():
                claim[cell] = key
            if rest - divided.keys():
                unreached.append(rest - divided.keys())
        water_keys = [item for item in placed if board.water(item.cell)]
        for item in water_keys:
            near = {
                cell
                for cell in board.owned
                if board.water(cell) and chebyshev(cell, item.cell) <= 3 and cell not in claim
            }
            others = [other for other in water_keys if other.cell in near and other.key != item.key]
            if others:
                raise DesignError("water with several keys isn't resolved by this generator")
            take(near, item.key)
        self._join_unlabelled(claim, unreached)
        doors = self._synthesized_doors(claim, placed)
        if self.members and doors:
            raise DesignError("a synthesized door on a panel several levels share isn't resolved here")
        keyed = {cell: key for cell, key in claim.items() if key is not None and cell in board.owned}
        return keyed, doors

    def _entrance_chamber(self, area: AreaSpec, placed: list[Placed], run_cells: dict[Cell, int]) -> list[Cell]:
        targets = [link.to_key for link in area.links if link.to_key in self.keys]
        if not targets:
            raise DesignError(f"entrance {area.key!r} has no prose connection to an area of its level")
        target_cells = [item.cell for item in placed if item.key == targets[0]]
        if not target_cells or not self.openings:
            raise DesignError(f"entrance {area.key!r} has no opening or no target")
        distance = self.walking(target_cells)
        ranked = sorted(
            (min(distance.get(cell, 10**9) for cell in opening.inside), order(opening.middle), index)
            for index, opening in enumerate(self.openings)
        )
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            raise DesignError(f"entrance {area.key!r} is equally near two openings")
        opening = self.openings[ranked[0][2]]
        keyed_spaces = {self.space_of[item.cell] for item in placed if item.cell in self.space_of}
        allowed = {
            cell
            for cell in self.walkable
            if cell not in self.crossing and not (cell in run_cells and len(self.runs[run_cells[cell]]) >= 3)
        }
        chamber = _floor_bfs(self.board, opening.inside, PASSABLE, allowed)
        if any(self.space_of[cell] in keyed_spaces for cell in chamber):
            raise DesignError(f"entrance {area.key!r} runs into a keyed area without a door between")
        return sorted(chamber, key=cell_order)

    def _join_unlabelled(self, claim: dict[Cell, str | None], unreached: list[set[Cell]]) -> None:
        board = self.board
        for part in unreached:
            lines = sorted(
                (
                    eid(cell, side)
                    for cell in part
                    for side in SIDES
                    if board.letter(cell, side) == "p" and claim.get(neighbour(cell, side)) is not None
                ),
                key=lambda edge: order(edge_middle(edge)),
            )
            if lines:
                first, second = edge_cells(lines[0])
                key = claim[second] if first in part else claim[first]
                for cell in part:
                    claim[cell] = key
        pending: list[int] = []
        for index in self.walled:
            room = [cell for cell in self.spaces[index] if cell not in claim]
            if room and len(room) != len(self.spaces[index]):
                raise DesignError(f"walled room {self.spaces[index][0]} is partly claimed, which isn't resolved here")
            if not room:
                continue
            doors = sorted(
                (
                    (order(edge_middle(eid(cell, side))), claim[neighbour(cell, side)])
                    for cell in room
                    for side in SIDES
                    if board.letter(cell, side) in DOORS and claim.get(neighbour(cell, side)) is not None
                ),
                key=lambda item: item[0],
            )
            if doors:
                for cell in room:
                    claim[cell] = doors[0][1]
            else:
                pending.append(index)
        for index in pending:
            room = self.spaces[index]
            key = self._row_key(index, claim)
            if key is not None:
                for cell in room:
                    claim[cell] = key

    def _row_key(self, index: int, claim: dict[Cell, str | None]) -> str | None:
        """The key a row of walled rooms of one size gives an unlabelled room in it, or `None`.

        The row runs from the room along a line through unlabelled rooms of the
        same width and height, each beside the next, to a keyed walled room of
        that size.
        """
        rects = {i: _rect(self.spaces[i]) for i in self.walled}
        mine = rects[index]
        if mine is None:
            return None
        x, y, w, h = mine
        found: set[str] = set()
        for dx, dy in ((w, 0), (-w, 0), (0, h), (0, -h)):
            at = (x, y)
            while True:
                at = (at[0] + dx, at[1] + dy)
                match = [i for i, rect in rects.items() if rect == (at[0], at[1], w, h)]
                if not match:
                    break
                keys = {claim.get(cell, "") for cell in self.spaces[match[0]]}
                if keys == {""}:
                    continue
                if len(keys) == 1 and None not in keys and "" not in keys:
                    found.add(next(iter(keys)) or "")
                break
        if len(found) > 1:
            raise DesignError(f"an unlabelled room lies in rows beside several keys: {sorted(found)}")
        return found.pop() if found else None

    def _synthesized_doors(self, claim: dict[Cell, str | None], placed: list[Placed]) -> dict[EdgeId, str]:
        board = self.board
        doors: dict[EdgeId, str] = {}
        first = next((area.key for area in self.level.areas if any(item.key == area.key for item in placed)), None)
        for rooms in self.structures:
            cells = [cell for room in rooms for cell in self.spaces[room]]
            members = set(cells)
            if not any(claim.get(cell) is not None for cell in cells):
                continue
            boundary = [(cell, side) for cell in cells for side in SIDES if neighbour(cell, side) not in members]
            if any(board.letter(cell, side) in DOORS for cell, side in boundary):
                continue
            if first is None:
                raise DesignError("a doorless structure needs a first key to face")
            distance = self.walking([item.cell for item in placed if item.key == first])
            candidates = [
                (distance.get(neighbour(cell, side), 10**9), order(edge_middle(eid(cell, side))), eid(cell, side))
                for cell, side in boundary
                if board.letter(cell, side) == "w" and neighbour(cell, side) in self.walkable
            ]
            if not candidates:
                raise DesignError("a doorless structure with no floor beyond its walls isn't resolved")
            doors[min(candidates)[2]] = "d"
        return doors

    # The level.

    def resolve(self) -> Resolved:
        """Place the keys, work out the areas and doors, and build the level's cells.

        Returns:
            The level so far, without its transitions, passages, or entrance.
        """
        board = self.board
        placed = self.place_keys()
        keyed, doors = self.areas(placed)
        areas: dict[str, list[Cell]] = {}
        for cell, key in keyed.items():
            areas.setdefault(key, []).append(cell)
        for item in placed:
            if keyed.get(item.cell) != item.key:
                raise DesignError(f"placed key {item.label!r} at {item.cell} isn't in its area")
        level_cells: dict[Cell, str] = {}
        for cell in board.owned:
            if board.floor(cell) or cell in self.crossing or cell in keyed:
                level_cells[cell] = "."
            else:
                level_cells[cell] = "~"
        overrides = dict(doors)
        if "l" in board.edges.values() and "locked_door" not in board.legends:
            raise DesignError("a locked door read without the legend's symbol isn't resolved by this generator")
        resolved = Resolved(
            spec=self.level,
            board=board,
            origins=self.origins,
            areas={key: sorted(cells, key=cell_order) for key, cells in areas.items()},
            placed=placed,
            openings=self.openings,
            overrides=overrides,
            level_cells=level_cells,
            area_of=dict(keyed),
        )
        resolved.locked = self.locks(resolved)
        return resolved

    def locks(self, resolved: Resolved) -> list[EdgeId]:
        """The doors the prose locks, as Doors says, leaving out doors the reading locks by their letter.

        The prose locks a door where it states a lock: the connections the
        writer derives from a locked door the map shows, and the links an area
        spec declares with `door_locked`. Each lock goes on the door nearest the
        first area on the shortest walk from the first area's first cell to the
        second's, equally short walks compared cell by cell in north-then-west
        order.

        Args:
            resolved: The level's areas and doors.

        Returns:
            The locked doors' edges, in the order the prose states them.
        """
        stated: list[tuple[str, str]] = []
        for (first, second), edges in resolved.joins().items():
            if any(letter == "l" for _edge, _side, letter in edges):
                stated += [(first, second), (second, first)]
        for area in self.level.areas:
            for link in area.links:
                if link.door_locked and link.via in ("door", "secret_door"):
                    if link.to_key not in self.keys:
                        raise DesignError(f"a lock on the way from {area.key!r} to another board isn't resolved here")
                    stated.append((area.key, link.to_key))
        found: list[EdgeId] = []
        for first, second in stated:
            door = self._locked_door(resolved, first, second)
            if door is None:
                raise DesignError(f"the lock between {first!r} and {second!r} finds no door, which isn't resolved here")
            if self.board.edges[door] != "l" and door not in found:
                found.append(door)
        return found

    def _locked_door(self, resolved: Resolved, first: str, second: str) -> EdgeId | None:
        start = min(resolved.areas[first], key=cell_order)
        goal = min(resolved.areas[second], key=cell_order)
        distance = self.walking([goal])
        if start not in distance:
            return None
        cell = start
        while cell != goal:
            step = min(
                (step for step in self.board.steps(cell, PASSABLE + DOORS) if distance.get(step) == distance[cell] - 1),
                key=cell_order,
            )
            side = next((side for side in SIDES if neighbour(cell, side) == step), None)
            if side is not None and self.board.letter(cell, side) in DOORS:
                return eid(cell, side)
            cell = step
        return None


def _rect(cells: list[Cell]) -> tuple[int, int, int, int] | None:
    """The rectangle `(x, y, width, height)` the cells fill, or `None` when they don't fill one."""
    xs = [cell[0] for cell in cells]
    ys = [cell[1] for cell in cells]
    width, height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    return (min(xs), min(ys), width, height) if len(cells) == width * height else None


def _without_mouths(board: Board, mouths: dict[EdgeId, Symbol]) -> Board:
    edges = {edge: ("w" if edge in mouths else letter) for edge, letter in board.edges.items()}
    return Board(
        width=board.width,
        height=board.height,
        cells=board.cells,
        edges=edges,
        frame=board.frame,
        owned=board.owned,
        partner=board.partner,
        fold_side=board.fold_side,
    )


def _apart_pair(spec: MapSpec, first: PanelSpec, second: PanelSpec) -> HalfPair | None:
    ids = ({region for region, _origin in first.regions}, {region for region, _origin in second.regions})
    for pair in spec.half_pairs:
        if not pair.joined and pair.regions[0] in ids[0] and pair.regions[1] in ids[1]:
            return pair
    return None


def panel_model(spec: MapSpec, panel: PanelSpec) -> Panel:
    """A panel's reading in the truth format, in its own squares, with the outline of each level that uses it.

    Args:
        spec: The map.
        panel: The panel.

    Returns:
        The panel as read.

    Raises:
        DesignError: If several levels use the panel without an outline each.
    """
    plan = panel.plan
    users = [level.number for level in spec.dungeon.levels if panel.id in level.panels]
    if panel.outlines:
        if sorted(outline.level for outline in panel.outlines) != users or plan.open_ground:
            raise DesignError(f"panel {panel.id!r} needs one outline for each level that uses it, and no open ground")
        outlines = tuple(
            Outline(
                dungeon=spec.dungeon.id,
                level=outline.level,
                rects=outline.rects,
                seeds=tuple(cell_key(cell) for cell in outline.seeds),
            )
            for outline in panel.outlines
        )
    else:
        if len(users) != 1:
            raise DesignError(f"panel {panel.id!r} is used by levels {users} but has one outline")
        outlines = (
            Outline(
                dungeon=spec.dungeon.id,
                level=users[0],
                rects=plan.rects,
                seeds=seed_cells(plan),
                open_ground=plan.open_ground,
            ),
        )
    return Panel(
        id=panel.id,
        regions=tuple(PanelRegion(region=region, origin=origin) for region, origin in panel.regions),
        scale_feet=panel.scale_feet,
        cells=plan.cells,
        edges=plan.edges,
        partial=plan.partial,
        cave=plan.cave,
        marks=plan.marks,
        outlines=outlines,
        symbols=plan.symbols,
    )


def resolution_plan(spec: MapSpec, panel: PanelSpec) -> tuple[Plan, tuple[Outline, ...]]:
    """A panel's reading as the rules after placement see it, converted to 10-foot cells and turned, with its outlines.

    A 5-foot panel is converted by [`convert_five_foot`][osrforge.tracing.fivefoot.convert_five_foot] first, as the
    conventions' order puts the conversion before every other rule. The panel then turns by the quarter turn that
    puts its north at the top.

    Args:
        spec: The map.
        panel: The panel.

    Returns:
        The plan in 10-foot cells on the level, and the panel's outlines in those cells.

    Raises:
        DesignError: If a panel that several levels share is turned.
    """
    model = panel_model(spec, panel)
    plan, outlines = panel.plan, model.outlines
    if panel.scale_feet == 5:
        converted = convert_five_foot(model).panel
        plan = Plan(
            width=converted.width,
            height=converted.height,
            cells=converted.cells,
            edges=converted.edges,
            marks=converted.marks,
            symbols=converted.symbols,
            partial=converted.partial,
            cave=converted.cave,
        )
        outlines = converted.outlines
    rotation = _turn(spec, panel)
    if rotation == 0:
        return plan, outlines
    if len(outlines) != 1:
        raise DesignError(f"panel {panel.id!r} is turned and shared by several levels, which isn't resolved here")
    outline = outlines[0]
    turned = replace(plan, rects=outline.rects, open_ground=outline.open_ground, seeds=outline.seeds).turned(rotation)
    return turned, (
        outline.model_copy(update={"rects": turned.rects, "open_ground": turned.open_ground, "seeds": turned.seeds}),
    )


def _owned(plan: Plan, outlines: Iterable[Outline]) -> set[Cell]:
    owned: set[Cell] = set()
    for outline in outlines:
        for x, y, w, h in outline.rects:
            for cy in range(y, y + h):
                for cx in range(x, x + w):
                    if plan.cell((cx, cy)) in ".~":
                        owned.add((cx, cy))
    return owned


def _turn(spec: MapSpec, panel: PanelSpec) -> Rotation:
    """The quarter turn that puts a panel's north at the top: its first region's printed compass, or none."""
    region = next(region for region in spec.regions if region.id == panel.regions[0][0])
    return rotation_for(region.compass.north) if region.compass.printed else 0


def _board(spec: MapSpec, level: LevelSpec, numbers: frozenset[int]) -> tuple[Board, dict[str, Cell]]:
    """Copy a level's panels onto its grid, the floor inside the outlines of the levels in `numbers` being owned."""
    panels = [next(panel for panel in spec.panels if panel.id == name) for name in level.panels]
    readings = [resolution_plan(spec, panel) for panel in panels]
    starts: list[Cell] = []
    for index, panel in enumerate(panels):
        if index == 0:
            starts.append((0, 0))
            continue
        before = panels[index - 1]
        pair = _apart_pair(spec, before, panel)
        x = starts[-1][0] + readings[index - 1][0].width + 2
        y = starts[-1][1] + pair.rows if pair is not None else 0
        starts.append((x, y))
    xs: list[int] = []
    ys: list[int] = []
    for (plan, outlines), (px, py) in zip(readings, starts, strict=True):
        own = [outline for outline in outlines if outline.level in numbers]
        xs += [px, px + plan.width - 1]
        ys += [py, py + plan.height - 1]
        for x, y in _owned(plan, own):
            xs += [px + x - 1, px + x + 1]
            ys += [py + y - 1, py + y + 1]
    shift = (-min(xs), -min(ys))
    board = Board(width=max(xs) - min(xs) + 1, height=max(ys) - min(ys) + 1)
    origins: dict[str, Cell] = {}
    regions = {region.id: region for region in spec.regions}
    for panel, (plan, outlines), (px, py) in zip(panels, readings, starts, strict=True):
        ox, oy = px + shift[0], py + shift[1]
        origins[panel.id] = (ox, oy)
        own = [outline for outline in outlines if outline.level in numbers]
        board.turns[panel.id] = _turn(spec, panel)
        for y in range(plan.height):
            for x in range(plan.width):
                board.cells[(ox + x, oy + y)] = plan.cells[y][x]
                for side in SIDES:
                    board.edges[eid((ox + x, oy + y), side)] = plan.letter((x, y), side)
                    if not (
                        0 <= neighbour((x, y), side)[0] < plan.width and 0 <= neighbour((x, y), side)[1] < plan.height
                    ):
                        board.frame[eid((ox + x, oy + y), side)] = False
        board.owned |= {(ox + x, oy + y) for x, y in _owned(plan, own)}
        for outline in own:
            board.ground_rects += [(ox + x, oy + y, w, h) for x, y, w, h in outline.open_ground]
            board.seeds += [
                ((ox + parse_cell(seed)[0], oy + parse_cell(seed)[1]), outline.level) for seed in outline.seeds
            ]
            for x, y in _owned(plan, [outline]):
                board.outlines.setdefault((ox + x, oy + y), set()).add(outline.level)
        board.sizes[panel.id] = (plan.width, plan.height)
        for mark in plan.marks:
            cell = parse_cell(mark.cell)
            edge = None
            if mark.edge is not None:
                edge_cell, side = parse_edge(mark.edge)
                edge = eid((ox + edge_cell[0], oy + edge_cell[1]), side)
            leader = None if mark.leader_to is None else parse_cell(mark.leader_to)
            board.marks.append(
                (
                    mark,
                    (ox + cell[0], oy + cell[1]),
                    edge,
                    None if leader is None else (ox + leader[0], oy + leader[1]),
                )
            )
        for symbol in plan.symbols:
            cells = tuple((ox + parse_cell(text)[0], oy + parse_cell(text)[1]) for text in symbol.cells)
            edge = None
            if symbol.edge is not None:
                edge_cell, side = parse_edge(symbol.edge)
                edge = eid((ox + edge_cell[0], oy + edge_cell[1]), side)
            board.symbols.append((symbol, cells, edge))
        for region, _origin in panel.regions:
            board.legends |= set(regions[region].legend)
    for first, second in itertools.pairwise(panels):
        pair = _apart_pair(spec, first, second)
        if pair is not None:
            _link_fold(board, first, origins[first.id], second, origins[second.id], regions)
    return board, origins


def _stretches(plan: Plan, column: int) -> list[list[int]]:
    stretches: list[list[int]] = []
    for y in range(plan.height):
        if plan.cells[y][column] != ".":
            continue
        if stretches and stretches[-1][-1] == y - 1 and plan.letter((column, y - 1), "south") in PASSABLE:
            stretches[-1].append(y)
        else:
            stretches.append([y])
    return stretches


def _link_fold(
    board: Board,
    first: PanelSpec,
    first_origin: Cell,
    second: PanelSpec,
    second_origin: Cell,
    regions: dict[str, RegionSpec],
) -> None:
    """Link the fold cells of two halves left apart, stretch by stretch, as Halves that stay apart says."""
    left_cut = regions[first.regions[0][0]].cut_off
    right_cut = regions[second.regions[0][0]].cut_off
    if "right" not in left_cut or "left" not in right_cut:
        raise DesignError("halves left apart must be cut off at facing left and right edges")
    if (
        first.scale_feet != 10
        or second.scale_feet != 10
        or any(regions[part.regions[0][0]].compass.north != "top" for part in (first, second))
    ):
        raise DesignError("5-foot or turned halves left apart aren't resolved here")
    left_column, right_column = first.plan.width - 1, 0
    shift = second_origin[1] - first_origin[1]
    left = _stretches(first.plan, left_column)
    right = [[y + shift for y in stretch] for stretch in _stretches(second.plan, right_column)]
    pairs: list[tuple[int, int, int, int]] = []
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            overlap = len(set(a) & set(b))
            pairs.append((-overlap, min(min(a), min(b)), i, j))
    used_left: set[int] = set()
    used_right: set[int] = set()
    for _overlap, _north, i, j in sorted(pairs):
        if i in used_left or j in used_right:
            continue
        used_left.add(i)
        used_right.add(j)
        a, b = left[i], right[j]
        shared = sorted(set(a) & set(b))
        matches = [(y, y) for y in shared] if shared else list(zip(a, b, strict=False))
        for ya, yb in matches:
            cell_a = (first_origin[0] + left_column, first_origin[1] + ya)
            cell_b = (second_origin[0] + right_column, first_origin[1] + yb)
            board.partner[cell_a] = cell_b
            board.partner[cell_b] = cell_a
            board.fold_side[cell_a] = "east"
            board.fold_side[cell_b] = "west"
            board.frame[eid(cell_a, "east")] = True
            board.frame[eid(cell_b, "west")] = True
    for y in range(first.plan.height):
        board.frame[eid((first_origin[0] + left_column, first_origin[1] + y), "east")] = True
    for y in range(second.plan.height):
        board.frame[eid((second_origin[0] + right_column, second_origin[1] + y), "west")] = True


# Levels whose floors meet: the partition, the cut between them, and its vestibules and passages.


def _neck(board: Board, cell: Cell) -> bool:
    """Whether a cell is a one-cell neck: floor with floor on two opposite sides and no floor on the other two."""
    if not board.floor(cell):
        return False
    floor = {side: board.floor(neighbour(cell, side)) for side in SIDES}
    return (floor["north"] and floor["south"] and not floor["east"] and not floor["west"]) or (
        floor["east"] and floor["west"] and not floor["north"] and not floor["south"]
    )


def _step_cost(board: Board, step: Cell, letter: str, mouth: bool) -> int:
    """The partition's cost of one step onto `step` across an edge with `letter`, the largest that applies."""
    costs: list[int] = []
    if letter in PASSABLE and (board.floor(step) or board.water(step)):
        costs.append(1)
    if _neck(board, step):
        costs.append(2)
    if letter in DOORS or mouth:
        costs.append(4)
    if letter in "wn":
        costs.append(20)
    if board.cells.get(step) == "#":
        costs.append(20)
    if not costs:
        raise DesignError(f"no partition cost applies to the step onto {step} across {letter!r}")
    return max(costs)


def partition(board: Board, mouths: Iterable[EdgeId], sources: list[tuple[Cell, int, int]]) -> dict[Cell, int]:
    """The level each owned cell goes to, as Who owns a cell says.

    The walk grows from every source at once over every cell of the panels,
    rock included, and over the links of halves left apart as open edges. A
    cell goes to the level of the source that reaches it most cheaply, a key
    beating a seed at the same cost, and of two sources of one kind the one
    whose cell comes first in north-then-west order.

    Args:
        board: The panels on the board.
        mouths: The cave mouths' edges.
        sources: Each source's cell, rank (0 for a placed key, 1 for a seed), and level.

    Returns:
        The owner of each owned floor or water cell. A cell inside one outline goes to that outline's level.
    """
    mouth_edges = set(mouths)
    best: dict[Cell, tuple[int, int, int, int, int]] = {}
    heap: list[tuple[tuple[int, int, int, int, int], Cell]] = []
    for cell, rank, level in sources:
        label = (0, rank, cell[1], cell[0], level)
        if cell not in best or label < best[cell]:
            best[cell] = label
            heapq.heappush(heap, (label, cell))
    while heap:
        label, cell = heapq.heappop(heap)
        if best[cell] != label:
            continue
        for side in SIDES:
            if cell in board.partner and board.fold_side[cell] == side:
                step, letter, mouth = board.partner[cell], "o", False
            else:
                step = neighbour(cell, side)
                if step not in board.cells:
                    continue
                letter, mouth = board.letter(cell, side), eid(cell, side) in mouth_edges
            reached = (label[0] + _step_cost(board, step, letter, mouth), *label[1:])
            if step not in best or reached < best[step]:
                best[step] = reached
                heapq.heappush(heap, (reached, step))
    owners: dict[Cell, int] = {}
    for cell in board.owned:
        levels = board.outlines[cell]
        owner = next(iter(levels)) if len(levels) == 1 else best[cell][4]
        if owner not in levels:
            raise DesignError(f"cell {cell} is nearest a source of level {owner}, whose outline doesn't hold it")
        owners[cell] = owner
    return owners


def _parts(board: Board, cells: set[Cell]) -> int:
    """How many parts a set of cells falls into when joined across passable edges and doors."""
    seen: set[Cell] = set()
    parts = 0
    for start in sorted(cells, key=cell_order):
        if start in seen:
            continue
        parts += 1
        seen |= set(_floor_bfs(board, [start], PASSABLE + DOORS, cells))
    return parts


type FarCell = tuple[int, Cell]
"""A passage's far cell, with the level whose grid it is on."""


class Cut:
    """The cut between the levels that share a board, worked out as Who owns a cell says.

    `owner` starts as the partition with each area given whole to its key's
    level, and the vestibules change it. The pairs are recorded as each
    passage's cell, side, level, destination, and destination level.
    """

    def __init__(self, rules: LevelRules, shared: Resolved, owner: dict[Cell, int], survey: list[int]) -> None:
        """Make the cut's vestibules and passages.

        Args:
            rules: The rules that resolved the shared board.
            shared: The board's areas, worked out over every level's keys.
            owner: Each owned cell's level after the final partition and the areas.
            survey: The level numbers in survey order.
        """
        self.board = rules.board
        self.owner = owner
        self.survey = survey
        self.areas = {key: list(cells) for key, cells in shared.areas.items()}
        self.area_of = dict(shared.area_of)
        self.link_cells = {
            cell for symbol, cells, _edge in self.board.symbols if symbol.kind in LINK_KINDS for cell in cells
        }
        self.pairs: list[tuple[Cell, str, int, Cell, int]] = []
        self.far: set[FarCell] = set()
        self.dropped: set[EdgeId] = set()
        self.quiet: set[EdgeId] = set()
        """Vestibule sides that are walls with no passages, and the side that has the vestibule's pair."""

        self.keyed = {item.cell for item in shared.placed}

        doors = sorted(
            (edge for edge in self.edges() if self.board.edges[edge] in DOORS),
            key=lambda edge: order(edge_middle(edge)),
        )
        for edge in doors:
            self._vestibule(edge)
        for edge in sorted(self.edges(), key=lambda edge: order(edge_middle(edge))):
            letter = self.board.edges[edge]
            if edge in self.quiet or edge in self.dropped or letter not in PASSABLE:
                if letter not in PASSABLE + DOORS + "w":
                    raise DesignError(f"the cut crosses {letter!r} at {edge}, which isn't resolved here")
                continue
            first, second = edge_cells(edge)
            self._pair(first, second, keep_or_drop=True)

    def edges(self) -> list[EdgeId]:
        """The edges between owned cells of two levels."""
        found: list[EdgeId] = []
        for edge in self.board.edges:
            first, second = edge_cells(edge)
            if first in self.owner and second in self.owner and self.owner[first] != self.owner[second]:
                found.append(edge)
        for cell, other in self.board.partner.items():
            if cell in self.owner and other in self.owner and self.owner[cell] != self.owner[other]:
                raise DesignError("a cut across the fold of halves left apart isn't resolved here")
        return found

    def _pair_far(self, cell: Cell, other: Cell) -> set[FarCell]:
        return {(self.owner[cell], other), (self.owner[other], cell)}

    def _pair(self, cell: Cell, other: Cell, keep_or_drop: bool) -> bool:
        """Make the pair of passages across the edge between two cells, unless it shares a far cell with a kept pair."""
        far = self._pair_far(cell, other)
        if far & self.far:
            if not keep_or_drop:
                raise DesignError(f"the pair between {cell} and {other} would be dropped, which isn't resolved here")
            return False
        side = next(side for side in SIDES if neighbour(cell, side) == other)
        opposite = next(back for back in SIDES if neighbour(other, back) == cell)
        self.pairs.append((cell, side, self.owner[cell], other, self.owner[other]))
        self.pairs.append((other, opposite, self.owner[other], cell, self.owner[cell]))
        self.far |= far
        return True

    def _qualifying(self, cell: Cell, door: EdgeId, leaving: int) -> list[str]:
        """The sides of a cell that qualify for its pair as a door's vestibule, or none when the vestibule is refused.

        A placed key on the cell isn't resolved here, since the cell would leave the key's area.
        """
        board = self.board
        if any(board.letter(cell, side) in DOORS for side in SIDES if eid(cell, side) != door):
            return []
        if cell in self.link_cells:
            return []
        floor = {other for other, level in self.owner.items() if level == leaving}
        if _parts(board, floor - {cell}) > _parts(board, floor):
            return []
        key = self.area_of.get(cell)
        if key is not None:
            rest = set(self.areas[key]) - {cell}
            if not rest or _parts(board, rest) > _parts(board, set(self.areas[key])):
                return []
        sides: list[str] = []
        joining = next(
            level for level in self.survey if level != leaving and level in (self.owner[c] for c in edge_cells(door))
        )
        for side in SIDES:
            other = neighbour(cell, side)
            if eid(cell, side) == door or board.letter(cell, side) not in PASSABLE:
                continue
            if self.owner.get(other) == leaving:
                if not {(joining, other), (leaving, cell)} & self.far:
                    sides.append(side)
            elif other in self.owner:
                raise DesignError(f"vestibule {cell} opens on its new level's floor, which isn't resolved here")
        if sides and cell in self.keyed:
            raise DesignError(f"a key stands on vestibule {cell}, which isn't resolved here")
        return sides

    def _vestibule(self, door: EdgeId) -> None:
        """Give a door on the cut its vestibule, or drop the door, as Who owns a cell says."""
        first, second = edge_cells(door)
        if self.owner[first] == self.owner[second]:
            return
        later_first = self.survey.index(self.owner[first]) > self.survey.index(self.owner[second])
        tries = [first, second] if later_first else [second, first]
        for cell in tries:
            leaving = self.owner[cell]
            joining = self.owner[first if cell == second else second]
            sides = self._qualifying(cell, door, leaving)
            if not sides:
                continue
            self.owner[cell] = joining
            key = self.area_of.pop(cell, None)
            if key is not None:
                self.areas[key].remove(cell)
            door_side = next(side for side in SIDES if eid(cell, side) == door)
            free = [side for side in sides if self._free(cell, side, joining)]
            choices = free or sides
            opposite = _OPPOSITE[door_side]
            side = (
                opposite if opposite in choices else min(choices, key=lambda item: order(edge_middle(eid(cell, item))))
            )
            self._pair(cell, neighbour(cell, side), keep_or_drop=False)
            self.quiet |= {eid(cell, other) for other in SIDES if other != door_side}
            return
        self.dropped.add(door)
        self._pair(first, second, keep_or_drop=False)

    def _free(self, cell: Cell, side: str, joining: int) -> bool:
        """Whether no pair across another passable edge or door of the cut would use a vestibule side's far cells."""
        far = {(joining, neighbour(cell, side)), (self.owner[neighbour(cell, side)], cell)}
        own = {eid(cell, other) for other in SIDES}
        for edge in self.edges():
            if edge in own or self.board.edges[edge] not in PASSABLE + DOORS:
                continue
            a, b = edge_cells(edge)
            if self._pair_far(a, b) & far:
                return False
        return True


_OPPOSITE: dict[str, str] = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _frame(board: Board, origins: dict[str, Cell], cells: Iterable[Cell]) -> tuple[int, int, int, int]:
    """The smallest grid that holds every panel and a cell of rock beyond the level's own floor and water."""
    xs: list[int] = []
    ys: list[int] = []
    for panel, (ox, oy) in origins.items():
        width, height = board.sizes[panel]
        xs += [ox, ox + width - 1]
        ys += [oy, oy + height - 1]
    for x, y in cells:
        xs += [x - 1, x + 1]
        ys += [y - 1, y + 1]
    return (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def resolve_shared(spec: MapSpec, members: tuple[LevelSpec, ...]) -> list[Resolved]:
    """Resolve levels whose floors meet on the panels they share, and cut them apart.

    The keys and areas are worked out once over every level's keys, so a
    room is never split by ownership. The final partition then gives each
    owned cell a level, each area goes whole to its key's level, and the cut
    between the levels makes its vestibules and passages.

    Args:
        spec: The map.
        members: The levels, in survey order, each placing the same panels.

    Returns:
        One resolved level for each member, in survey order.
    """
    if len({member.panels for member in members}) != 1:
        raise DesignError("levels that share a panel but not all their panels aren't resolved here")
    shared_spec = LevelSpec(
        number=members[0].number,
        areas=tuple(area for member in members for area in member.areas),
        panels=members[0].panels,
    )
    rules = LevelRules(spec, shared_spec, members)
    shared = rules.resolve()
    board = rules.board
    if board.partner:
        raise DesignError("halves left apart on panels that several levels share aren't resolved here")
    level_of_key = {area.key: member.number for member in members for area in member.areas}
    sources = [(item.cell, 0, level_of_key[item.key]) for item in shared.placed]
    sources += [(cell, 1, level) for cell, level in board.seeds]
    owner = partition(board, rules.mouths, sources)
    for key, cells in shared.areas.items():
        for cell in cells:
            owner[cell] = level_of_key[key]
    cut = Cut(rules, shared, owner, [member.number for member in members])
    walls: set[EdgeId] = set(cut.edges()) | cut.dropped
    keeper: dict[EdgeId, int] = {}
    for edge in shared.locked:
        first, second = edge_cells(edge)
        if edge not in cut.dropped:
            keeper[edge] = cut.owner[first]
            if cut.owner[second] != keeper[edge]:
                raise DesignError(f"locked door {edge} lies on the cut without a vestibule or a drop")
    levels: list[Resolved] = []
    for member in members:
        number = member.number
        own = {cell for cell, level in cut.owner.items() if level == number}
        keys = {area.key for area in member.areas}
        level_cells = {cell: letter for cell, letter in shared.level_cells.items() if cell in own}
        for cell in own - level_cells.keys():
            level_cells[cell] = "."
        areas = {key: sorted(cells, key=cell_order) for key, cells in cut.areas.items() if key in keys}
        levels.append(
            Resolved(
                spec=member,
                board=board,
                origins=shared.origins,
                areas=areas,
                placed=[item for item in shared.placed if item.key in keys],
                openings=[opening for opening in shared.openings if all(cell in own for cell in opening.inside)],
                overrides={**shared.overrides, **dict.fromkeys(walls, "w")},
                level_cells=level_cells,
                passages=[
                    Passage(cell=cell_key(cell), side=_SIDE_NAMES[side], to_level=to_level, to_cell=cell_key(other))
                    for cell, side, level, other, to_level in cut.pairs
                    if level == number
                ],
                area_of={cell: key for cell, key in cut.area_of.items() if key in keys},
                locked=[edge for edge in shared.locked if keeper.get(edge) == number],
                frame=_frame(board, shared.origins, own),
            )
        )
    for opening in shared.openings:
        if not any(opening in level.openings for level in levels):
            raise DesignError(f"outer opening {opening.edges} lies across the cut, which isn't resolved here")
    return levels


# The dungeon: links, passages, and the entrance.


def _target(levels: list[Resolved], own: Resolved, link: AreaConnection) -> tuple[Resolved, str]:
    if link.to_key is None:
        raise DesignError("a link without a key isn't resolved by this generator")
    for candidate in [own, *[level for level in levels if level is not own]]:
        if any(area.key == link.to_key for area in candidate.spec.areas):
            return candidate, link.to_key
    raise DesignError(f"link target {link.to_key!r} is no surveyed key")


def _ends(level: Resolved, key: str, directions: tuple[str | None, ...]) -> list[tuple[Cell, Symbol]]:
    cells = set(level.areas[key])
    first = min(cells, key=cell_order)
    ends: list[tuple[Cell, Symbol]] = []
    for symbol, symbol_cells, _edge in level.board.symbols:
        if symbol.kind not in LINK_KINDS:
            continue
        touches = any(cell in cells or any(neighbour(cell, side) in cells for side in SIDES) for cell in symbol_cells)
        if not touches or symbol.direction not in directions:
            continue
        end = min(symbol_cells, key=lambda cell: (-chebyshev(cell, first), cell_order(cell)))
        ends.append((end, symbol))
    return sorted(ends, key=lambda item: cell_order(item[0]))


def _panel_at(level: Resolved, cell: Cell) -> int:
    """The index, in placement order, of the panel a board cell of the level lies on."""
    for index, (panel, (ox, oy)) in enumerate(level.origins.items()):
        width, height = level.board.sizes[panel]
        if ox <= cell[0] < ox + width and oy <= cell[1] < oy + height:
            return index
    raise DesignError(f"cell {cell} lies on none of level {level.spec.number}'s panels")


def _made(levels: list[Resolved], level: Resolved, key: str, target: Resolved, target_key: str) -> bool:
    """Whether the trace already makes a prose connection through floor, doors, or passages.

    The walk starts on the area's cells, crosses the level's passable edges and doors and its passages, and
    goes through corridor only, never another area, until it reaches the target area.
    """
    by_number = {item.spec.number: item for item in levels}
    goal = {(target.spec.number, cell) for cell in target.areas.get(target_key, [])}
    start = [(level.spec.number, cell) for cell in level.areas[key]]
    seen = set(start)
    queue = deque(start)
    while queue:
        number, cell = queue.popleft()
        here = by_number[number]
        steps: list[tuple[int, Cell]] = []
        for side in SIDES:
            if here.level_letter(cell, side) in PASSABLE + DOORS:
                steps.append((number, neighbour(cell, side)))
        for passage in here.passages:
            if parse_cell(passage.cell) == cell:
                steps.append((passage.to_level, parse_cell(passage.to_cell)))
        for step in steps:
            if step in goal:
                return True
            other = by_number[step[0]]
            if step not in seen and other.level_cells.get(step[1]) == "." and step[1] not in other.area_of:
                seen.add(step)
                queue.append(step)
    return False


def _stairs(symbol: Symbol, first: bool) -> Literal["stairs_up", "stairs_down"]:
    """The kind of one end of a link with no direction made by stairs or a ladder, as Links says."""
    if symbol.direction is not None:
        return _STAIRS[symbol.direction]
    return "stairs_down" if first else "stairs_up"


def link_levels(levels: list[Resolved]) -> None:
    """Realize each prose link as transitions, as Links says.

    A connection and its reverse between the same two areas by the same means
    are one link, made from the connection that comes first in survey order.
    A connection the trace already makes through floor, doors, or passages
    isn't a link. A link to an area on another panel of the same level has no
    direction.

    Args:
        levels: The dungeon's resolved levels, in survey order.
    """
    made: set[tuple[frozenset[tuple[int, str]], str]] = set()
    for level in levels:
        for area in level.spec.areas:
            for link in area.links:
                target, key = _target(levels, level, link)
                if _made(levels, level, area.key, target, key):
                    continue
                ends = frozenset({(level.spec.number, area.key), (target.spec.number, key)})
                if (ends, link.via) in made:
                    continue
                made.add((ends, link.via))
                same = target is level
                if same:
                    here = _panel_at(level, min(level.areas[area.key], key=cell_order))
                    there = _panel_at(level, min(level.areas[key], key=cell_order))
                    if here == there:
                        raise DesignError(f"{area.key!r} and {key!r} share a panel but the trace doesn't join them")
                    starts = _ends(level, area.key, ("up", "down", None))
                    finishes = _ends(target, key, ("up", "down", None))
                    way = back = None
                else:
                    way = "down" if target.spec.number > level.spec.number else "up"
                    back = "up" if way == "down" else "down"
                    starts = _ends(level, area.key, (way, None))
                    finishes = _ends(target, key, (back, None))
                if not starts or len(finishes) > len(starts):
                    raise DesignError(f"link from {area.key!r} to {key!r} needs a symbol at each end for every end")
                target_first = min(target.areas[key], key=cell_order)
                for index, (start, symbol) in enumerate(starts):
                    finish = finishes[index][0] if index < len(finishes) else target_first
                    if symbol.kind in ("stairs", "ladder"):
                        if way is None or back is None:
                            if index >= len(finishes):
                                raise DesignError(f"stairs from {area.key!r} to {key!r} need a symbol at each end")
                            first = _panel_at(level, start) < _panel_at(target, finish)
                            kind, kind_back = _stairs(symbol, first), _stairs(finishes[index][1], not first)
                        else:
                            kind, kind_back = _STAIRS[way], _STAIRS[back]
                        level.transitions.append(
                            Transition(
                                cell=cell_key(start), kind=kind, to_level=target.spec.number, to_cell=cell_key(finish)
                            )
                        )
                        if index < len(finishes):
                            target.transitions.append(
                                Transition(
                                    cell=cell_key(finish),
                                    kind=kind_back,
                                    to_level=level.spec.number,
                                    to_cell=cell_key(start),
                                )
                            )
                    else:
                        kind_name = "trapdoor" if symbol.kind in ("trapdoor", "hinged_floor") else "chute"
                        level.transitions.append(
                            Transition(
                                cell=cell_key(start),
                                kind=kind_name,
                                to_level=target.spec.number,
                                to_cell=cell_key(finish),
                            )
                        )


def fold_passages(level: Resolved) -> None:
    """Put a passage pair on each pair of cells linked across a fold, as Halves that stay apart says.

    Args:
        level: The resolved level.
    """
    board = level.board
    for cell, other in board.partner.items():
        side = board.fold_side[cell]
        level.passages.append(
            Passage(cell=cell_key(cell), side=_SIDE_NAMES[side], to_level=level.spec.number, to_cell=cell_key(other))
        )
        level.overrides[eid(cell, side)] = "w"


def entrance(levels: list[Resolved], entrance_key: str | None) -> Entrance:
    """The dungeon's entrance, as Entrances says.

    Args:
        levels: The dungeon's resolved levels, in survey order.
        entrance_key: The map-reading stage's entrance key.

    Returns:
        The entrance.
    """
    survey = [(level, area) for level in levels for area in level.spec.areas]
    chosen: tuple[Resolved, str] | None = None
    opening: Opening | None = None
    if entrance_key is not None:
        chosen = next(((level, area.key) for level, area in survey if area.key == entrance_key), None)
    if chosen is None:
        chosen = next(
            ((level, area.key) for level, area in survey if area.name.lower() in ("entrance", "entranceway")), None
        )
    if chosen is None:
        reached: dict[tuple[int, str], list[Opening]] = {}
        for level in levels:
            for candidate in level.openings:
                for key in _walk_from(level, candidate):
                    reached.setdefault((level.spec.number, key), []).append(candidate)
        for level, area in survey:
            if (level.spec.number, area.key) in reached:
                ways = reached[(level.spec.number, area.key)]
                if len(ways) > 1:
                    raise DesignError(f"area {area.key!r} is reached from several outer openings")
                chosen, opening = (level, area.key), ways[0]
                break
    if chosen is None:
        outside_edges = [
            (
                sum(
                    1
                    for cell in level.areas[area.key]
                    for side in SIDES
                    if level.board.outside(neighbour(cell, side)) and level.board.floor(neighbour(cell, side))
                ),
                -index,
            )
            for index, (level, area) in enumerate(survey)
        ]
        best = max(range(len(survey)), key=lambda index: outside_edges[index])
        chosen = (survey[best][0], survey[best][1].key)
    level, key = chosen
    cells = level.areas[key]
    if opening is None:
        if not level.openings:
            return Entrance(level=level.spec.number, cell=cell_key(cells[0]))
        distance = _walk_level(level, [cells[0]])
        ranked = sorted(
            (min(distance.get(cell, 10**9) for cell in candidate.inside), order(candidate.middle), index)
            for index, candidate in enumerate(level.openings)
        )
        opening = level.openings[ranked[0][2]]
    return Entrance(level=level.spec.number, cell=cell_key(_entrance_cell(opening, set(cells))))


def _walk_level(level: Resolved, starts: list[Cell]) -> dict[Cell, int]:
    walkable = {cell for cell, letter in level.level_cells.items() if letter == "."}
    return _floor_bfs(level.board, starts, PASSABLE + DOORS, walkable)


def _walk_from(level: Resolved, opening: Opening) -> list[str]:
    """The areas a walk from an opening reaches across unkeyed floor alone, with no door or other area on the way."""
    walkable = {cell for cell, letter in level.level_cells.items() if letter == "."}
    seen: set[Cell] = set()
    queue: deque[Cell] = deque(cell for cell in opening.inside if cell in walkable)
    found: list[str] = []
    while queue:
        cell = queue.popleft()
        if cell in seen:
            continue
        seen.add(cell)
        if cell in level.area_of:
            if level.area_of[cell] not in found:
                found.append(level.area_of[cell])
            continue
        for step in level.board.steps(cell, PASSABLE):
            if step in walkable and step not in seen:
                queue.append(step)
    return found


def _entrance_cell(opening: Opening, area: set[Cell]) -> Cell:
    count = len(opening.edges)
    if count % 2 == 1:
        middle = opening.inside[count // 2]
        if middle in area:
            return middle
        if any(cell in area for cell in opening.inside):
            raise DesignError("the entrance area touches its opening but not the opening's middle")
        return middle
    west_or_north = opening.inside[count // 2 - 1]
    if west_or_north in area or not any(cell in area for cell in opening.inside):
        return west_or_north
    raise DesignError("the entrance area touches its opening but not the north or west cell at its middle")


def level_model(level: Resolved, frames: dict[int, tuple[int, int, int, int]]) -> Level:
    """The truth format's level, on its own grid.

    Args:
        level: The resolved level, with its transitions and passages.
        frames: Each level's grid on its board, by level number, to place destinations on their levels' grids.

    Returns:
        The level.
    """
    x0, y0, width, height = frames[level.spec.number]

    def place(cell: Cell, number: int | None = None) -> str:
        fx, fy, _width, _height = frames[level.spec.number if number is None else number]
        return cell_key((cell[0] - fx, cell[1] - fy))

    def letter(cell: Cell, side: str) -> str:
        other = neighbour(cell, side)
        if level.level_cells.get(cell) is None and level.level_cells.get(other) is None:
            return "-"
        found = level.level_letter(cell, side)
        if found == "-":
            raise DesignError(f"level edge {eid(cell, side)} touches the level's floor but has no reading")
        return found

    columns = range(x0, x0 + width)
    rows = range(y0, y0 + height)
    last_x, last_y = x0 + width - 1, y0 + height - 1
    return Level(
        number=level.spec.number,
        verdict="traced",
        panels=tuple(
            LevelPanel(panel=panel, origin=(origin[0] - x0, origin[1] - y0), rotation=level.board.turns[panel])
            for panel, origin in level.origins.items()
        ),
        cells=tuple("".join(level.level_cells.get((x, y), "#") for x in columns) for y in rows),
        edges=EdgeGrids(
            vertical=tuple(
                "".join(letter((x, y), "west") for x in columns) + letter((last_x, y), "east") for y in rows
            ),
            horizontal=(
                *tuple("".join(letter((x, y), "north") for x in columns) for y in rows),
                "".join(letter((x, last_y), "south") for x in columns),
            ),
        ),
        areas={key: Area(cells=tuple(place(cell) for cell in cells)) for key, cells in level.areas.items()},
        keys=tuple(
            PlacedKey(label=item.label, cell=place(item.cell), area=item.key, moved=item.moved) for item in level.placed
        ),
        transitions=tuple(
            transition.model_copy(
                update={
                    "cell": place(parse_cell(transition.cell)),
                    "to_cell": place(parse_cell(transition.to_cell), transition.to_level),
                }
            )
            for transition in level.transitions
        ),
        passages=tuple(
            passage.model_copy(
                update={
                    "cell": place(parse_cell(passage.cell)),
                    "to_cell": place(parse_cell(passage.to_cell), passage.to_level),
                }
            )
            for passage in level.passages
        ),
        locked_doors=tuple(f"{place((edge[0], edge[1]))}:{edge[2]}" for edge in level.locked),
    )


def frames_of(levels: list[Resolved]) -> dict[int, tuple[int, int, int, int]]:
    """Each level's grid on its board, by level number.

    Args:
        levels: The resolved levels.

    Returns:
        `(x, y, width, height)` for each level.
    """
    return {level.spec.number: level.frame or (0, 0, level.board.width, level.board.height) for level in levels}


def _groups(levels: Iterable[LevelSpec]) -> list[tuple[LevelSpec, ...]]:
    """The levels in survey order, those that share a panel gathered into one group at the first one's place."""
    groups: list[list[LevelSpec]] = []
    for level in levels:
        group = next((group for group in groups if any(set(level.panels) & set(other.panels) for other in group)), None)
        if group is None:
            groups.append([level])
        else:
            group.append(level)
    return [tuple(group) for group in groups]


def resolve_dungeon(spec: MapSpec) -> tuple[list[Resolved], Entrance | None]:
    """Work out every traced level of a map's dungeon, its links and passages, and its entrance.

    Args:
        spec: The map.

    Returns:
        The resolved traced levels in survey order, and the entrance on its level's grid, or `None` when every level
        falls back.
    """
    levels: list[Resolved] = []
    for group in _groups(level for level in spec.dungeon.levels if level.fallback is None):
        if len(group) == 1:
            levels.append(LevelRules(spec, group[0]).resolve())
        else:
            levels.extend(resolve_shared(spec, group))
    order_of = {level.number: index for index, level in enumerate(spec.dungeon.levels)}
    levels.sort(key=lambda level: order_of[level.spec.number])
    for level in levels:
        fold_passages(level)
    link_levels(levels)
    if not levels:
        return levels, None
    found = entrance(levels, spec.entrance_key)
    x0, y0, _width, _height = frames_of(levels)[found.level]
    cell = parse_cell(found.cell)
    return levels, found.model_copy(update={"cell": cell_key((cell[0] - x0, cell[1] - y0))})
