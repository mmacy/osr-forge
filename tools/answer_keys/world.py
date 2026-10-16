"""The map as the resolution rules walk it: every panel's cells and edges on one graph.

A *world cell* is `(panel id, x, y)`. Two halves of a map that stay apart are
linked cell by cell across the fold, and the rules treat each link as an open
edge, so a walk crosses it like any other. Everything here is geometry that
`docs/tracing-conventions.md` defines before ownership divides the floor among
levels: open spaces, open ground, walled rooms and structures, narrow runs,
one-cell necks, and the walks the rules measure distances with.
"""

import heapq
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from osrforge.tracing.fivefoot import convert_five_foot
from osrforge.tracing.truth import (
    GeometryTruth,
    HalfPair,
    PageSide,
    Panel,
    Region,
    order_key,
    parse_cell,
    parse_edge,
)

Cell = tuple[int, int]
WCell = tuple[str, int, int]
"""A world cell: the panel id and the cell on that panel's converted grid."""

EdgeId = tuple[str, str, int, int]
"""A canonical edge, `(panel, axis, line, index)`: `("v", x_line, y)` or `("h", x, y_line)`."""

SIDES: tuple[str, ...] = ("north", "east", "south", "west")
OPPOSITE: dict[str, str] = {"north": "south", "south": "north", "east": "west", "west": "east"}
DELTA: dict[str, tuple[int, int]] = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}

PASSABLE = "op"
DOORS = "dls"
BLOCKED = "wn-"


def step(cell: Cell, side: str) -> Cell:
    """The cell one step across a side."""
    dx, dy = DELTA[side]
    return (cell[0] + dx, cell[1] + dy)


def chebyshev(a: Cell, b: Cell) -> int:
    """The Chebyshev distance between two cells of one grid."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def edge_id(panel: str, cell: Cell, side: str) -> EdgeId:
    """The canonical id of one side of a cell, the same from either cell beside it."""
    x, y = cell
    if side == "west":
        return (panel, "v", x, y)
    if side == "east":
        return (panel, "v", x + 1, y)
    if side == "north":
        return (panel, "h", x, y)
    return (panel, "h", x, y + 1)


def edge_midpoint(cell: Cell, side: str) -> tuple[float, float]:
    """The midpoint of one side of a cell, in grid coordinates."""
    x, y = cell
    if side == "west":
        return (x, y + 0.5)
    if side == "east":
        return (x + 1, y + 0.5)
    if side == "north":
        return (x + 0.5, y)
    return (x + 0.5, y + 1)


def rect_contains(rect: tuple[int, int, int, int], cell: Cell) -> bool:
    """Whether a `[x, y, width, height]` rectangle contains a cell."""
    x, y, width, height = rect
    return x <= cell[0] < x + width and y <= cell[1] < y + height


class Grid:
    """One panel's reading in 10-foot cells, with its edges in a table the rules can change."""

    def __init__(self, panel: Panel, regions: dict[str, Region], north: PageSide, index: int) -> None:
        self.source = panel
        self.panel = convert_five_foot(panel).panel if panel.scale_feet == 5 else panel
        self.id = panel.id
        self.north = north
        self.index = index
        self.regions = [regions[part.region] for part in panel.regions]
        self.width = self.panel.width
        self.height = self.panel.height
        self.cells: list[list[str]] = [list(row) for row in self.panel.cells]
        self.edges: dict[EdgeId, str] = {}
        for cell, side in self.panel.edges.positions():
            self.edges[edge_id(self.id, cell, side)] = self.panel.edges.at(cell, side)
        self.cave_mouths: dict[EdgeId, tuple[Cell, str]] = {}
        for symbol in self.panel.symbols:
            if symbol.kind == "cave_mouth" and symbol.edge is not None:
                cell, side = parse_edge(symbol.edge)
                self.cave_mouths[edge_id(self.id, cell, side)] = (cell, side)
        self.fold_side: str | None = None
        """The side of this half's grid that faces the fold, when the half stays apart from its partner."""

    def inside(self, cell: Cell) -> bool:
        """Whether a cell lies on the grid."""
        return 0 <= cell[0] < self.width and 0 <= cell[1] < self.height

    def letter(self, cell: Cell) -> str:
        """The cell's letter, or `#` off the grid."""
        return self.cells[cell[1]][cell[0]] if self.inside(cell) else "#"

    def edge(self, cell: Cell, side: str) -> str:
        """The letter on one side of a cell, `-` off the grid."""
        return self.edges.get(edge_id(self.id, cell, side), "-")

    def set_edge(self, cell: Cell, side: str, letter: str) -> None:
        """Change one edge's letter."""
        self.edges[edge_id(self.id, cell, side)] = letter

    def order(self, point: tuple[float, float]) -> tuple[float, float]:
        """The north-then-west order key of a grid point, through the compass in force."""
        return order_key(point, self.north)

    def frame_top(self) -> tuple[int, float, float]:
        """Where the panel's frame sits on its page: the page, the top edge, and the left edge in pixels."""
        region = self.regions[0]
        registration = region.registration
        if registration is None:
            return (region.page, 0.0, 0.0)
        corner = registration.corners[0][0]
        return (region.page, corner[1], corner[0])


@dataclass
class FoldLink:
    """Two fold-column cells of halves that stay apart, linked as if by an open edge."""

    first: WCell
    second: WCell


@dataclass(eq=False)
class Space:
    """An open space: floor cells joined across edges that aren't walls, windows, or doors."""

    cells: frozenset[WCell]
    enclosed: bool
    first: WCell
    index: int
    open_ground: set[WCell] = field(default_factory=set)

    @property
    def is_room(self) -> bool:
        """A walled room: enclosed and not open ground."""
        return self.enclosed and not self.open_ground


class World:
    """Every panel's grid, the fold links between halves left apart, and the walks over them."""

    def __init__(self, truth: GeometryTruth) -> None:
        regions = {region.id: region for region in truth.regions}
        grids: list[Grid] = []
        for panel in truth.panels:
            if any(regions[part.region].verdict != "registered" for part in panel.regions):
                continue
            north = truth.north_in_force(panel.regions[0].region)
            grids.append(Grid(panel, regions, north, 0))
        grids.sort(key=lambda grid: grid.frame_top())
        for index, grid in enumerate(grids):
            grid.index = index
        self.grids: dict[str, Grid] = {grid.id: grid for grid in grids}
        self.order_ids: list[str] = [grid.id for grid in grids]
        self.fold: dict[tuple[WCell, str], WCell] = {}
        self.fold_links: list[FoldLink] = []
        self.half_pairs: list[HalfPair] = list(truth.half_pairs)
        for pair in truth.half_pairs:
            if not pair.joined:
                self._link_halves(pair, regions)
        self._spaces: list[Space] | None = None
        self.crossings: set[WCell] = set()
        self._find_crossings()
        self._necks: set[WCell] | None = None

    def _panel_of_region(self, region_id: str) -> Grid:
        for grid in self.grids.values():
            if any(part.region == region_id for part in grid.source.regions):
                return grid
        raise KeyError(region_id)

    def _link_halves(self, pair: HalfPair, regions: dict[str, Region]) -> None:
        first, second = (self._panel_of_region(region_id) for region_id in pair.regions)
        first_region, second_region = regions[pair.regions[0]], regions[pair.regions[1]]
        if "right" in first_region.cut_off and "left" in second_region.cut_off:
            first.fold_side, second.fold_side = "east", "west"
        elif "left" in first_region.cut_off and "right" in second_region.cut_off:
            first.fold_side, second.fold_side = "west", "east"
        else:
            return
        stretches_first = self._stretches(first)
        stretches_second = self._stretches(second)
        candidates: list[tuple[int, int, int, int, int]] = []
        for i, (a_start, a_end) in enumerate(stretches_first):
            for j, (b_start, b_end) in enumerate(stretches_second):
                shifted_start, shifted_end = b_start + pair.rows, b_end + pair.rows
                overlap = max(0, min(a_end, shifted_end) - max(a_start, shifted_start) + 1)
                candidates.append((-overlap, min(a_start, shifted_start), a_start, shifted_start, i * 10000 + j))
        candidates.sort()
        used_first: set[int] = set()
        used_second: set[int] = set()
        column_first = first.width - 1 if first.fold_side == "east" else 0
        column_second = second.width - 1 if second.fold_side == "east" else 0
        for _overlap, _north, _a, _b, code in candidates:
            i, j = divmod(code, 10000)
            if i in used_first or j in used_second:
                continue
            used_first.add(i)
            used_second.add(j)
            a_start, a_end = stretches_first[i]
            b_start, b_end = stretches_second[j]
            rows_a = list(range(a_start, a_end + 1))
            rows_b = list(range(b_start, b_end + 1))
            shared = [y for y in rows_a if y - pair.rows in rows_b]
            pairs = [(y, y - pair.rows) for y in shared] if shared else list(zip(rows_a, rows_b, strict=False))
            for ya, yb in pairs:
                a: WCell = (first.id, column_first, ya)
                b: WCell = (second.id, column_second, yb)
                self.fold[(a, first.fold_side)] = b
                self.fold[(b, second.fold_side)] = a
                self.fold_links.append(FoldLink(a, b))

    @staticmethod
    def _stretches(grid: Grid) -> list[tuple[int, int]]:
        column = grid.width - 1 if grid.fold_side == "east" else 0
        stretches: list[tuple[int, int]] = []
        y = 0
        while y < grid.height:
            if grid.letter((column, y)) != ".":
                y += 1
                continue
            start = y
            while (
                y + 1 < grid.height
                and grid.letter((column, y + 1)) == "."
                and grid.edge((column, y), "south") in PASSABLE
            ):
                y += 1
            stretches.append((start, y))
            y += 1
        return stretches

    def _find_crossings(self) -> None:
        for grid in self.grids.values():
            footings: list[Cell] = []
            for symbol in grid.panel.symbols:
                if symbol.kind == "bridge":
                    for text in symbol.cells:
                        cell = parse_cell(text)
                        if grid.letter(cell) == "~":
                            self.crossings.add((grid.id, *cell))
                elif symbol.kind == "footing":
                    footings.extend(parse_cell(text) for text in symbol.cells)
            for a in footings:
                for b in footings:
                    if a == b:
                        continue
                    between: list[Cell] = []
                    if a[1] == b[1] and a[0] < b[0]:
                        between = [(x, a[1]) for x in range(a[0] + 1, b[0])]
                    elif a[0] == b[0] and a[1] < b[1]:
                        between = [(a[0], y) for y in range(a[1] + 1, b[1])]
                    if between and all(grid.letter(cell) == "~" for cell in between):
                        self.crossings.update((grid.id, *cell) for cell in between)

    def grid(self, wcell: WCell) -> Grid:
        """The grid a world cell lies on."""
        return self.grids[wcell[0]]

    def cell(self, wcell: WCell) -> Cell:
        """The panel cell of a world cell."""
        return (wcell[1], wcell[2])

    def letter(self, wcell: WCell) -> str:
        """The cell's letter, with crossings read as floor."""
        if wcell in self.crossings:
            return "."
        return self.grid(wcell).letter(self.cell(wcell))

    def is_floor(self, wcell: WCell) -> bool:
        """Whether a world cell is floor, crossings included."""
        return self.letter(wcell) == "."

    def is_water(self, wcell: WCell) -> bool:
        """Whether a world cell is water that isn't a crossing."""
        return self.letter(wcell) == "~"

    def edge(self, wcell: WCell, side: str) -> str:
        """The letter on one side of a world cell; a fold link reads as open."""
        if (wcell, side) in self.fold:
            return "o"
        return self.grid(wcell).edge(self.cell(wcell), side)

    def across(self, wcell: WCell, side: str) -> WCell | None:
        """The cell one step across a side, through a fold link when there is one, or `None` off the grid."""
        linked = self.fold.get((wcell, side))
        if linked is not None:
            return linked
        grid = self.grid(wcell)
        cell = step(self.cell(wcell), side)
        if not grid.inside(cell):
            return None
        return (grid.id, *cell)

    def all_cells(self) -> Iterable[WCell]:
        """Every world cell, panel by panel in page order."""
        for panel_id in self.order_ids:
            grid = self.grids[panel_id]
            for y in range(grid.height):
                for x in range(grid.width):
                    yield (panel_id, x, y)

    def floor_cells(self) -> list[WCell]:
        """Every floor cell, crossings included."""
        return [wcell for wcell in self.all_cells() if self.is_floor(wcell)]

    def order(self, wcell: WCell) -> tuple[int, float, float]:
        """The north-then-west order key of a world cell: its panel's page order, then its place after the turn."""
        grid = self.grid(wcell)
        x, y = self.cell(wcell)
        return (grid.index, *grid.order((x + 0.5, y + 0.5)))

    def edge_order(self, wcell: WCell, side: str) -> tuple[int, float, float]:
        """The north-then-west order key of one side of a world cell."""
        grid = self.grid(wcell)
        return (grid.index, *grid.order(edge_midpoint(self.cell(wcell), side)))

    def first(self, cells: Iterable[WCell]) -> WCell:
        """The first of some cells in north-then-west order."""
        return min(cells, key=self.order)

    def is_cave_mouth(self, wcell: WCell, side: str) -> bool:
        """Whether a cave mouth stands on one side of a world cell."""
        if (wcell, side) in self.fold:
            return False
        return edge_id(wcell[0], self.cell(wcell), side) in self.grid(wcell).cave_mouths

    def cave_mouth_edges(self) -> list[tuple[WCell, str]]:
        """Every cave mouth as the cell and side it was recorded on, in north-then-west order."""
        found = [(grid.id, cell, side) for grid in self.grids.values() for cell, side in grid.cave_mouths.values()]
        edges = [((panel, *cell), side) for panel, cell, side in found]
        return sorted(edges, key=lambda item: self.edge_order(*item))

    def necks(self) -> set[WCell]:
        """The one-cell necks: floor cells with floor on two opposite sides and none on the other two."""
        if self._necks is None:
            self._necks = set()
            for wcell in self.floor_cells():
                floor = {
                    side: (lambda n: n is not None and self.is_floor(n))(self.across(wcell, side)) for side in SIDES
                }
                if (floor["north"] and floor["south"] and not floor["east"] and not floor["west"]) or (
                    floor["east"] and floor["west"] and not floor["north"] and not floor["south"]
                ):
                    self._necks.add(wcell)
        return self._necks

    def walk(
        self,
        sources: Iterable[WCell],
        crosses: Callable[[WCell, str, str, WCell], bool],
        allowed: Callable[[WCell], bool] | None = None,
    ) -> dict[WCell, int]:
        """Breadth-first walking distances from some cells.

        Args:
            sources: The cells the walk starts from, at distance 0.
            crosses: Whether a step from a cell across a side with a letter onto the next cell is taken.
            allowed: Cells the walk may enter, or every cell when `None`.

        Returns:
            Each reached cell's distance.
        """
        distance: dict[WCell, int] = {}
        queue: deque[WCell] = deque()
        for source in sources:
            if source not in distance and (allowed is None or allowed(source)):
                distance[source] = 0
                queue.append(source)
        while queue:
            wcell = queue.popleft()
            for side in SIDES:
                nxt = self.across(wcell, side)
                if nxt is None or nxt in distance or (allowed is not None and not allowed(nxt)):
                    continue
                if crosses(wcell, side, self.edge(wcell, side), nxt):
                    distance[nxt] = distance[wcell] + 1
                    queue.append(nxt)
        return distance

    def walking(self, wcell: WCell, side: str, letter: str, nxt: WCell) -> bool:
        """The ordinary walk: across passable edges and doors, onto floor."""
        return letter in PASSABLE or letter in DOORS

    def walking_floor(self, wcell: WCell, side: str, letter: str, nxt: WCell) -> bool:
        """The ordinary walk restricted to floor cells."""
        return (letter in PASSABLE or letter in DOORS) and self.is_floor(nxt)

    def open_floor(self, wcell: WCell, side: str, letter: str, nxt: WCell) -> bool:
        """A walk across passable edges only, onto floor."""
        return letter in PASSABLE and self.is_floor(nxt)

    def shortest_path(
        self, start: WCell, goal: WCell, crosses: Callable[[WCell, str, str, WCell], bool]
    ) -> list[WCell]:
        """The shortest walk between two cells, equally short walks compared cell by cell in north-then-west order.

        Returns:
            The cells of the walk from `start` to `goal`, or an empty list when the goal can't be reached.
        """
        back = self.walk([goal], lambda a, side, letter, b: crosses(b, OPPOSITE[side], letter, a))
        if start not in back:
            return []
        path = [start]
        current = start
        while current != goal:
            options: list[WCell] = []
            for side in SIDES:
                nxt = self.across(current, side)
                if nxt is None or nxt not in back or back[nxt] != back[current] - 1:
                    continue
                if crosses(current, side, self.edge(current, side), nxt):
                    options.append(nxt)
            current = min(options, key=self.order)
            path.append(current)
        return path

    def dijkstra(
        self, sources: list[tuple[WCell, tuple[int, int, float, float]]], cost: Callable[[WCell, str, str, WCell], int]
    ) -> dict[WCell, tuple[int, tuple[int, int, float, float]]]:
        """Cheapest walks from ranked sources, over every cell.

        Args:
            sources: Cells with a rank; a cheaper walk wins, then a lower rank.
            cost: The cost of a step from a cell across a side with a letter onto the next cell.

        Returns:
            For each cell, the cost and the rank of the source that reaches it most cheaply.
        """
        best: dict[WCell, tuple[int, tuple[int, int, float, float]]] = {}
        heap: list[tuple[int, tuple[int, int, float, float], WCell]] = []
        for wcell, rank in sources:
            heapq.heappush(heap, (0, rank, wcell))
        while heap:
            total, rank, wcell = heapq.heappop(heap)
            if wcell in best and best[wcell] <= (total, rank):
                continue
            best[wcell] = (total, rank)
            for side in SIDES:
                nxt = self.across(wcell, side)
                if nxt is None:
                    continue
                stepcost = cost(wcell, side, self.edge(wcell, side), nxt)
                candidate = (total + stepcost, rank)
                if nxt not in best or candidate < best[nxt]:
                    heapq.heappush(heap, (total + stepcost, rank, nxt))
        return best

    def spaces(self) -> list[Space]:
        """The open spaces, in order of their first cells."""
        if self._spaces is None:
            seen: set[WCell] = set()
            found: list[Space] = []
            for start in self.floor_cells():
                if start in seen:
                    continue
                reached = self.walk([start], self.open_floor)
                cells = frozenset(reached)
                seen |= cells
                enclosed = True
                for wcell in cells:
                    for side in SIDES:
                        nxt = self.across(wcell, side)
                        if nxt in cells:
                            continue
                        if self.edge(wcell, side) not in "wndls":
                            enclosed = False
                found.append(Space(cells, enclosed, self.first(cells), len(found)))
            found.sort(key=lambda space: self.order(space.first))
            for index, space in enumerate(found):
                space.index = index
            self._spaces = found
        return self._spaces

    def space_of(self) -> dict[WCell, Space]:
        """Each floor cell's open space."""
        return {wcell: space for space in self.spaces() for wcell in space.cells}

    def mark_open_ground(self) -> None:
        """Find open ground from the outlines' open-ground rectangles and record it on the spaces."""
        spaces = self.spaces()
        for grid in self.grids.values():
            for outline in grid.panel.outlines:
                for rect in outline.open_ground:
                    best: Space | None = None
                    best_count = 0
                    for space in spaces:
                        count = sum(
                            1
                            for wcell in space.cells
                            if wcell[0] == grid.id and rect_contains(rect, (wcell[1], wcell[2]))
                        )
                        if count > best_count:
                            best, best_count = space, count
                        elif (
                            count == best_count
                            and count > 0
                            and best is not None
                            and self.page_order(space.first) < self.page_order(best.first)
                        ):
                            best = space
                    if best is not None:
                        best.open_ground |= {
                            wcell
                            for wcell in best.cells
                            if wcell[0] == grid.id and rect_contains(rect, (wcell[1], wcell[2]))
                        }

    def page_order(self, wcell: WCell) -> tuple[int, int, int]:
        """Page order: by panel, then row from the top, then column from the left."""
        return (self.grid(wcell).index, wcell[2], wcell[1])

    def open_ground(self) -> set[WCell]:
        """Every open-ground cell."""
        return {wcell for space in self.spaces() for wcell in space.open_ground}

    def rooms(self) -> list[Space]:
        """The walled rooms."""
        return [space for space in self.spaces() if space.is_room]

    def structures(self) -> list[list[Space]]:
        """The walled structures: rooms joined by doors with no corridor between them, each in space order."""
        rooms = self.rooms()
        by_cell = {wcell: room for room in rooms for wcell in room.cells}
        parent = {room.index: room.index for room in rooms}

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        for room in rooms:
            for wcell in room.cells:
                for side in SIDES:
                    nxt = self.across(wcell, side)
                    if nxt is None or nxt not in by_cell or by_cell[nxt] is room:
                        continue
                    if self.edge(wcell, side) in DOORS:
                        parent[find(room.index)] = find(by_cell[nxt].index)
        groups: dict[int, list[Space]] = {}
        for room in rooms:
            groups.setdefault(find(room.index), []).append(room)
        return [sorted(group, key=lambda room: room.index) for group in groups.values()]

    def narrow_runs(self) -> list[list[WCell]]:
        """The narrow runs: chains of at least two floor cells one cell wide, each in order along the chain."""
        floor = set(self.floor_cells())
        blocked: set[WCell] = set()
        for wcell in floor:
            panel, x, y = wcell
            block = [wcell, (panel, x + 1, y), (panel, x, y + 1), (panel, x + 1, y + 1)]
            if not all(c in floor for c in block):
                continue
            links = 0
            if self.edge(block[0], "east") in PASSABLE:
                links += 1
            if self.edge(block[0], "south") in PASSABLE:
                links += 1
            if self.edge(block[1], "south") in PASSABLE:
                links += 1
            if self.edge(block[2], "east") in PASSABLE:
                links += 1
            if links >= 3:
                blocked.update(block)

        def passable_neighbours(wcell: WCell) -> list[WCell]:
            out: list[WCell] = []
            for side in SIDES:
                nxt = self.across(wcell, side)
                if nxt is not None and nxt in floor and self.edge(wcell, side) in PASSABLE:
                    out.append(nxt)
            return out

        eligible = {wcell for wcell in floor if wcell not in blocked and len(passable_neighbours(wcell)) <= 2}
        seen: set[WCell] = set()
        runs: list[list[WCell]] = []
        for start in sorted(eligible, key=self.order):
            if start in seen:
                continue
            component = self.walk(
                [start],
                lambda a, side, letter, b: letter == "o" and b in eligible,
            )
            seen |= set(component)
            if len(component) >= 2:
                runs.append(sorted(component, key=self.order))
        return runs

    def within(self, wcell: WCell, distance: int) -> list[WCell]:
        """The cells of the same panel within a Chebyshev distance, in north-then-west order."""
        grid = self.grid(wcell)
        x, y = self.cell(wcell)
        cells = [
            (grid.id, i, j)
            for j in range(y - distance, y + distance + 1)
            for i in range(x - distance, x + distance + 1)
            if grid.inside((i, j))
        ]
        return sorted(cells, key=self.order)

    def nearest(self, wcell: WCell, candidates: Iterable[WCell]) -> WCell | None:
        """The nearest candidate on the same panel by Chebyshev distance, ties in north-then-west order."""
        cell = self.cell(wcell)
        same = [c for c in candidates if c[0] == wcell[0]]
        if not same:
            return None
        return min(same, key=lambda c: (chebyshev(cell, self.cell(c)), self.order(c)))
