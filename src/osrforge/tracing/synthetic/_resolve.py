"""A synthetic map's answer, worked out from its reading by the rules in `docs/tracing-conventions.md`.

The generator never writes an area, a placed key, an entrance, a transition,
or a passage by hand. It applies the conventions to the reading it drew, so
the answer is the one the reference resolution script must reach. It applies
only the rules its maps exercise: a level's panels are 10-foot panels that no
compass turns, one outline each, and a pair of halves left apart is the only
way two panels of a level meet. Where a map would need a rule this module
doesn't apply, or reaches a case the conventions leave open, it raises
[`DesignError`][osrforge.tracing.synthetic._resolve.DesignError] rather than
choose, so a map can't quietly carry an answer the rules don't give.

Everything here works on the level's grid: each panel's reading is copied to
its place there first. The panels aren't turned, so north-then-west order on
the level is the order the rules ask for, and page order within a panel is the
same order.
"""

import itertools
import re
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Literal

from osrforge.contracts.stages import AreaConnection
from osrforge.tracing.synthetic._plan import SIDES, Plan, canonical, neighbour
from osrforge.tracing.synthetic._spec import AreaSpec, LevelSpec, MapSpec, PanelSpec, RegionSpec
from osrforge.tracing.truth import (
    Area,
    EdgeGrids,
    Entrance,
    HalfPair,
    Level,
    LevelPanel,
    Mark,
    Passage,
    PlacedKey,
    Side,
    Symbol,
    Transition,
    cell_key,
    parse_cell,
    parse_edge,
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
    area_of: dict[Cell, str] = field(default_factory=dict[Cell, str])

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
    """The rules for one level, up to its areas and doors."""

    def __init__(self, spec: MapSpec, level: LevelSpec) -> None:
        """Copy the level's panels onto its grid and find what the drawing shows.

        Args:
            spec: The map.
            level: The level.
        """
        self.spec = spec
        self.level = level
        self.keys: dict[str, AreaSpec] = {area.key: area for area in level.areas}
        self.board, self.origins = _board(spec, level)
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
        if "/" in text:
            if text.lower() in self.keys or text.replace("/", "").lower() in self.keys:
                raise DesignError(f"stacked mark {text!r} matches a key as a whole, which this generator doesn't use")
            parts = text.split("/")
        else:
            parts = [text]
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
        settled = [cell for cell in self.walkable if cell not in self.ground and cell not in self.crossing]
        for mark, cell, edge, leader in board.marks:
            for label, kind in self.labels(mark):
                if kind == "letter":
                    letters.append((label, cell))
                    continue
                key = label.lower() if kind == "key" else label[:-1].lower()
                target = self._place(cell, edge, leader, settled)
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

    def _place(self, cell: Cell, edge: EdgeId | None, leader: Cell | None, settled: list[Cell]) -> Cell:
        board = self.board
        on_rock = not (board.floor(cell) or board.water(cell))
        if leader is not None:
            if not on_rock or self.nearest_floor(cell, settled) != leader:
                raise DesignError(f"the leader line from {cell} doesn't run from rock to the nearest floor cell")
            return leader
        if edge is not None:
            if board.edges.get(edge, "-") not in DOORS:
                raise DesignError(f"a key printed on edge {edge} isn't on a door")
            if not any(edge in opening.edges for opening in self.openings):
                raise DesignError(f"a key printed on the inner door {edge} isn't resolved by this generator")
            return self.nearest_floor(cell, settled)
        if board.floor(cell) and cell in board.owned:
            if cell in self.ground:
                raise DesignError(f"a key printed on open ground at {cell} isn't resolved by this generator")
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
        for the walk, and a cell at the same distance from two keys goes to the
        key whose placed cell is first in north-then-west order.

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
        best: dict[Cell, tuple[int, tuple[int, int], str]] = {}
        for key, start in sources:
            for cell, distance in _floor_bfs(self.board, [start], "o", space).items():
                rank = (distance, cell_order(start), key)
                if cell in best and best[cell][:2] == rank[:2] and best[cell][2] != key:
                    raise DesignError(f"cell {cell} is equally near keys {best[cell][2]} and {key} on one cell")
                if cell not in best or rank < best[cell]:
                    best[cell] = rank
        return {cell: rank[2] for cell, rank in best.items() if cell in cells}

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
        for item in placed:
            if item.cell in self.ground:
                raise DesignError(f"key {item.label!r} stands on open ground")
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
        parent = {index: index for index in self.walled}

        def find(index: int) -> int:
            while parent[index] != index:
                index = parent[index]
            return index

        for index in self.walled:
            for cell in self.spaces[index]:
                for side in SIDES:
                    other = self.space_of.get(neighbour(cell, side))
                    if other is not None and other != index and other in parent and board.letter(cell, side) in DOORS:
                        parent[find(index)] = find(other)
        groups: dict[int, list[Cell]] = {}
        for index in self.walled:
            groups.setdefault(find(index), []).extend(self.spaces[index])
        doors: dict[EdgeId, str] = {}
        first = next((area.key for area in self.level.areas if any(item.key == area.key for item in placed)), None)
        for cells in groups.values():
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
        return Resolved(
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


def _owned(plan: Plan) -> set[Cell]:
    owned: set[Cell] = set()
    for x, y, w, h in plan.rects:
        for cy in range(y, y + h):
            for cx in range(x, x + w):
                if plan.cell((cx, cy)) in ".~":
                    owned.add((cx, cy))
    return owned


def _board(spec: MapSpec, level: LevelSpec) -> tuple[Board, dict[str, Cell]]:
    panels = [next(panel for panel in spec.panels if panel.id == name) for name in level.panels]
    starts: list[Cell] = []
    for index, panel in enumerate(panels):
        if index == 0:
            starts.append((0, 0))
            continue
        before = panels[index - 1]
        pair = _apart_pair(spec, before, panel)
        x = starts[-1][0] + before.plan.width + 2
        y = starts[-1][1] + pair.rows if pair is not None else 0
        starts.append((x, y))
    xs: list[int] = []
    ys: list[int] = []
    for panel, (px, py) in zip(panels, starts, strict=True):
        xs += [px, px + panel.plan.width - 1]
        ys += [py, py + panel.plan.height - 1]
        for x, y in _owned(panel.plan):
            xs += [px + x - 1, px + x + 1]
            ys += [py + y - 1, py + y + 1]
    shift = (-min(xs), -min(ys))
    board = Board(width=max(xs) - min(xs) + 1, height=max(ys) - min(ys) + 1)
    origins: dict[str, Cell] = {}
    regions = {region.id: region for region in spec.regions}
    for panel, (px, py) in zip(panels, starts, strict=True):
        ox, oy = px + shift[0], py + shift[1]
        origins[panel.id] = (ox, oy)
        plan = panel.plan
        for y in range(plan.height):
            for x in range(plan.width):
                board.cells[(ox + x, oy + y)] = plan.cells[y][x]
                for side in SIDES:
                    board.edges[eid((ox + x, oy + y), side)] = plan.letter((x, y), side)
                    if not (
                        0 <= neighbour((x, y), side)[0] < plan.width and 0 <= neighbour((x, y), side)[1] < plan.height
                    ):
                        board.frame[eid((ox + x, oy + y), side)] = False
        board.owned |= {(ox + x, oy + y) for x, y in _owned(plan)}
        board.ground_rects += [(ox + x, oy + y, w, h) for x, y, w, h in plan.open_ground]
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


# The dungeon: links, passages, and the entrance.


def _target(levels: list[Resolved], own: Resolved, link: AreaConnection) -> tuple[Resolved, str]:
    if link.to_key is None:
        raise DesignError("a link without a key isn't resolved by this generator")
    for candidate in [own, *[level for level in levels if level is not own]]:
        if any(area.key == link.to_key for area in candidate.spec.areas):
            return candidate, link.to_key
    raise DesignError(f"link target {link.to_key!r} is no surveyed key")


def _ends(level: Resolved, key: str, directions: tuple[str | None, ...]) -> list[tuple[Cell, str]]:
    cells = set(level.areas[key])
    first = min(cells, key=cell_order)
    ends: list[tuple[Cell, str]] = []
    for symbol, symbol_cells, _edge in level.board.symbols:
        if symbol.kind not in LINK_KINDS or symbol.direction not in directions:
            continue
        touches = any(cell in cells or any(neighbour(cell, side) in cells for side in SIDES) for cell in symbol_cells)
        if touches:
            end = min(symbol_cells, key=lambda cell: (-chebyshev(cell, first), cell_order(cell)))
            ends.append((end, symbol.kind))
    return sorted(ends, key=lambda item: cell_order(item[0]))


def link_levels(levels: list[Resolved]) -> None:
    """Realize each prose link to another level as transitions, as Links says.

    Args:
        levels: The dungeon's resolved levels, in survey order.
    """
    for level in levels:
        for area in level.spec.areas:
            for link in area.links:
                target, key = _target(levels, level, link)
                if target is level:
                    continue
                way = "down" if target.spec.number > level.spec.number else "up"
                back = "up" if way == "down" else "down"
                starts = _ends(level, area.key, (way, None))
                finishes = _ends(target, key, (back, None))
                if not starts or len(finishes) > len(starts):
                    raise DesignError(f"link from {area.key!r} to {key!r} needs a symbol at each end for every end")
                target_first = min(target.areas[key], key=cell_order)
                for index, (start, kind) in enumerate(starts):
                    finish = finishes[index][0] if index < len(finishes) else target_first
                    if kind in ("stairs", "ladder"):
                        level.transitions.append(
                            Transition(
                                cell=cell_key(start),
                                kind=_STAIRS[way],
                                to_level=target.spec.number,
                                to_cell=cell_key(finish),
                            )
                        )
                        if index < len(finishes):
                            target.transitions.append(
                                Transition(
                                    cell=cell_key(finish),
                                    kind=_STAIRS[back],
                                    to_level=level.spec.number,
                                    to_cell=cell_key(start),
                                )
                            )
                    else:
                        kind_name = "trapdoor" if kind in ("trapdoor", "hinged_floor") else "chute"
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


def level_model(level: Resolved) -> Level:
    """The truth format's level.

    Args:
        level: The resolved level, with its transitions and passages.

    Returns:
        The level.
    """
    board = level.board
    rows = tuple("".join(level.level_cells.get((x, y), "#") for x in range(board.width)) for y in range(board.height))

    def letter(cell: Cell, side: str) -> str:
        other = neighbour(cell, side)
        if level.level_cells.get(cell) is None and level.level_cells.get(other) is None:
            return "-"
        found = level.level_letter(cell, side)
        if found == "-":
            raise DesignError(f"level edge {eid(cell, side)} touches the level's floor but has no reading")
        return found

    vertical = tuple(
        "".join(letter((x, y), "west") for x in range(board.width)) + letter((board.width - 1, y), "east")
        for y in range(board.height)
    )
    horizontal = (
        *tuple("".join(letter((x, y), "north") for x in range(board.width)) for y in range(board.height)),
        "".join(letter((x, board.height - 1), "south") for x in range(board.width)),
    )
    return Level(
        number=level.spec.number,
        verdict="traced",
        panels=tuple(LevelPanel(panel=panel, origin=origin, rotation=0) for panel, origin in level.origins.items()),
        cells=rows,
        edges=EdgeGrids(vertical=vertical, horizontal=horizontal),
        areas={key: Area(cells=tuple(cell_key(cell) for cell in cells)) for key, cells in level.areas.items()},
        keys=tuple(
            PlacedKey(label=item.label, cell=cell_key(item.cell), area=item.key, moved=item.moved)
            for item in level.placed
        ),
        transitions=tuple(level.transitions),
        passages=tuple(level.passages),
    )


def resolve_dungeon(spec: MapSpec) -> tuple[list[Resolved], Entrance]:
    """Work out every level of a map's dungeon, its links and passages, and its entrance.

    Args:
        spec: The map.

    Returns:
        The resolved levels in survey order, and the entrance.
    """
    levels = [LevelRules(spec, level).resolve() for level in spec.dungeon.levels]
    for level in levels:
        fold_passages(level)
    link_levels(levels)
    return levels, entrance(levels, spec.entrance_key)
