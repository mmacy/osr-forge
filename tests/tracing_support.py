"""Hand-built geometry truth documents and the single-change copies the scorer tests use.

`demo_truth()` is one dungeon on three regions: a 6 by 5 panel with two rooms
and a corridor that runs off the west frame, a 3 by 3 panel linked to it by a
passage pair (the two are halves left apart), and a second level reached by
stairs. Every number the scorer tests assert is counted from the grids below.
"""

from collections.abc import Callable

from osrforge.tracing.synthetic import DamageSite
from osrforge.tracing.truth import (
    Area,
    Compass,
    Dungeon,
    EdgeGrids,
    Entrance,
    GeometryTruth,
    HalfPair,
    Legend,
    Level,
    LevelPanel,
    Mark,
    Outline,
    Page,
    PageImage,
    Panel,
    PanelRegion,
    Passage,
    PlacedKey,
    Region,
    Registration,
    Scale,
    Symbol,
    Transition,
    cell_key,
    parse_cell,
    parse_edge,
)


def affine_registration(
    columns: int, rows: int, pitch: float = 20.0, origin: tuple[float, float] = (10.0, 10.0)
) -> Registration:
    """A registration whose grid lines are `pitch` pixels apart, starting at `origin`."""
    return Registration(
        corners=tuple(
            tuple((origin[0] + i * pitch, origin[1] + j * pitch) for i in range(columns + 1)) for j in range(rows + 1)
        )
    )


def region(
    region_id: str,
    page: int,
    columns: int,
    rows: int,
    *,
    feet: int = 10,
    north: str = "top",
    cut_off: tuple[str, ...] = (),
    legend_symbols: tuple[str, ...] = ("stairs", "locked_door"),
) -> Region:
    """A registered TSR-blue region with a printed scale, compass, and legend."""
    return Region(
        id=region_id,
        page=page,
        image=f"{region_id}.png",
        style="tsr_blue",
        columns=columns,
        rows=rows,
        scale=Scale(feet=feet, printed=True, text=f"1 square = {feet} feet"),
        compass=Compass(north=north, printed=True),  # type: ignore[arg-type]
        legend=Legend(printed=True, symbols=legend_symbols),  # type: ignore[arg-type]
        cut_off=cut_off,  # type: ignore[arg-type]
        verdict="registered",
        registration=affine_registration(columns, rows),
    )


def demo_truth() -> GeometryTruth:
    """The hand-built document.

    Panel `p1` (6 by 5):

    ```text
    ######
    #....#   room 1 is (1,1) (2,1) (1,2) (2,2); room 2 is (3,1) (4,1) (3,2) (4,2)
    #....#   a locked door joins them at (2,1):east
    .....#   corridor; it runs off the west frame at (0,3):west
    ######
    ```

    Room 1 opens on the corridor through a secret door at (1,2):south and a
    door at (2,2):south. Room 2 opens on it through a door at (4,2):south and
    has a wall at (3,2):south. Stairs down at (2,1) lead to level 2. Cell (3,3)
    is partly floor, and (4,3) is a cave cell. Panel `p1b` (3 by 3, floor at
    (1,1), room 4) sits two cells east of `p1` on the level grid, at origin
    (8, 0), and a passage pair joins (4,1) and (9,1). Level 2 is panel `p2`
    (3 by 3, floor at (1,1), room 3) with stairs up.
    """
    p1 = Panel(
        id="p1",
        regions=(PanelRegion(region="r1", origin=(0, 0)),),
        scale_feet=10,
        cells=("######", "#....#", "#....#", ".....#", "######"),
        edges=EdgeGrids(
            vertical=("-------", "-wolow-", "-wowow-", "ooooow-", "-------"),
            horizontal=("------", "-wwww-", "-oooo-", "wsdwd-", "wwwww-", "------"),
        ),
        partial=("3,3",),
        cave=("4,3",),
        marks=(Mark(text="1", cell="1,1"), Mark(text="2", cell="4,2")),
        outlines=(Outline(dungeon="demo", level=1, rects=((0, 0, 6, 5),), seeds=("1,1",)),),
        symbols=(Symbol(kind="stairs", cells=("2,1",), direction="down"),),
    )
    small_edges = EdgeGrids(vertical=("----", "-ww-", "----"), horizontal=("---", "-w-", "-w-", "---"))
    p1b = Panel(
        id="p1b",
        regions=(PanelRegion(region="r1b", origin=(0, 0)),),
        scale_feet=10,
        cells=("###", "#.#", "###"),
        edges=small_edges,
        marks=(Mark(text="4", cell="1,1"),),
        outlines=(Outline(dungeon="demo", level=1, rects=((0, 0, 3, 3),), seeds=("1,1",)),),
    )
    p2 = Panel(
        id="p2",
        regions=(PanelRegion(region="r2", origin=(0, 0)),),
        scale_feet=10,
        cells=("###", "#.#", "###"),
        edges=small_edges,
        marks=(Mark(text="3", cell="1,1"),),
        outlines=(Outline(dungeon="demo", level=2, rects=((0, 0, 3, 3),), seeds=("1,1",)),),
        symbols=(Symbol(kind="stairs", cells=("1,1",), direction="up"),),
    )
    level1 = Level(
        number=1,
        verdict="traced",
        panels=(LevelPanel(panel="p1", origin=(0, 0)), LevelPanel(panel="p1b", origin=(8, 0))),
        cells=("###########", "#....####.#", "#....######", ".....######", "###########"),
        edges=EdgeGrids(
            vertical=("------------", "-wolow---ww-", "-wowow------", "ooooow------", "------------"),
            horizontal=("-----------", "-wwww----w-", "-oooo----w-", "wsdwd------", "wwwww------", "-----------"),
        ),
        areas={
            "1": Area(cells=("1,1", "2,1", "1,2", "2,2")),
            "2": Area(cells=("3,1", "4,1", "3,2", "4,2")),
            "4": Area(cells=("9,1",)),
        },
        keys=(
            PlacedKey(label="1", cell="1,1", area="1"),
            PlacedKey(label="2", cell="4,2", area="2"),
            PlacedKey(label="4", cell="9,1", area="4"),
        ),
        transitions=(Transition(cell="2,1", kind="stairs_down", to_level=2, to_cell="1,1"),),
        passages=(
            Passage(cell="4,1", side="east", to_level=1, to_cell="9,1"),
            Passage(cell="9,1", side="west", to_level=1, to_cell="4,1"),
        ),
    )
    level2 = Level(
        number=2,
        verdict="traced",
        panels=(LevelPanel(panel="p2", origin=(0, 0)),),
        cells=("###", "#.#", "###"),
        edges=small_edges,
        areas={"3": Area(cells=("1,1",))},
        keys=(PlacedKey(label="3", cell="1,1", area="3"),),
        transitions=(Transition(cell="1,1", kind="stairs_up", to_level=1, to_cell="2,1"),),
    )
    return GeometryTruth(
        pages=(
            Page(number=1, images=(PageImage(file="r1.png"),)),
            Page(number=2, images=(PageImage(file="r1b.png"), PageImage(file="r2.png"))),
        ),
        regions=(
            region("r1", 1, 6, 5, cut_off=("right",)),
            region("r1b", 2, 3, 3, cut_off=("left",)),
            region("r2", 2, 3, 3),
        ),
        panels=(p1, p1b, p2),
        half_pairs=(HalfPair(regions=("r1", "r1b"), joined=False, rows=0),),
        dungeons=(Dungeon(id="demo", name="Demo", entrance=Entrance(level=1, cell="0,3"), levels=(level1, level2)),),
    )


def five_foot_pair() -> tuple[GeometryTruth, GeometryTruth]:
    """A key whose one panel is read in 5-foot squares, and the same map read in 10-foot cells.

    The 5-foot panel is 4 squares by 2: two rooms of 2 by 2 squares with a
    door on the top segment of the line between them and a wall on the bottom
    segment. Under phase (0, 0) it becomes 2 cells by 1 with a door between
    them, which is what the 10-foot document records.
    """
    five = Panel(
        id="p5",
        regions=(PanelRegion(region="r5", origin=(0, 0)),),
        scale_feet=5,
        cells=("....", "...."),
        edges=EdgeGrids(vertical=("wodow", "wowow"), horizontal=("wwww", "oooo", "wwww")),
        marks=(Mark(text="1", cell="0,0"), Mark(text="2", cell="2,0")),
        outlines=(Outline(dungeon="tiny", level=1, rects=((0, 0, 4, 2),), seeds=("0,1",)),),
    )
    ten = Panel(
        id="p5",
        regions=(PanelRegion(region="r5", origin=(0, 0)),),
        scale_feet=10,
        cells=("..",),
        edges=EdgeGrids(vertical=("wdw",), horizontal=("ww", "ww")),
        marks=(Mark(text="1", cell="0,0"), Mark(text="2", cell="1,0")),
        outlines=(Outline(dungeon="tiny", level=1, rects=((0, 0, 2, 1),), seeds=("0,0",)),),
    )
    level = Level(
        number=1,
        verdict="traced",
        panels=(LevelPanel(panel="p5", origin=(1, 1)),),
        cells=("####", "#..#", "####"),
        edges=EdgeGrids(vertical=("-----", "-wdw-", "-----"), horizontal=("----", "-ww-", "-ww-", "----")),
        areas={"1": Area(cells=("1,1",)), "2": Area(cells=("2,1",))},
        keys=(PlacedKey(label="1", cell="1,1", area="1"), PlacedKey(label="2", cell="2,1", area="2")),
    )

    def document(panel: Panel) -> GeometryTruth:
        return GeometryTruth(
            pages=(Page(number=1, images=(PageImage(file="r5.png"),)),),
            regions=(region("r5", 1, 4, 2, feet=5),),
            panels=(panel,),
            dungeons=(Dungeon(id="tiny", name="Tiny", entrance=Entrance(level=1, cell="1,1"), levels=(level,)),),
        )

    return document(five), document(ten)


def rebuild(truth: GeometryTruth) -> GeometryTruth:
    """Validate a document again after `model_copy` edits, which skip validation."""
    return GeometryTruth.model_validate(truth.model_dump(mode="json"))


def replace_panel(truth: GeometryTruth, panel: Panel) -> GeometryTruth:
    """A copy with one panel replaced, by id."""
    panels = tuple(panel if candidate.id == panel.id else candidate for candidate in truth.panels)
    return rebuild(truth.model_copy(update={"panels": panels}))


def replace_level(truth: GeometryTruth, dungeon_id: str, level: Level) -> GeometryTruth:
    """A copy with one level replaced, by dungeon id and number."""
    dungeons: list[Dungeon] = []
    for dungeon in truth.dungeons:
        if dungeon.id == dungeon_id:
            levels = tuple(level if candidate.number == level.number else candidate for candidate in dungeon.levels)
            dungeon = dungeon.model_copy(update={"levels": levels})
        dungeons.append(dungeon)
    return rebuild(truth.model_copy(update={"dungeons": tuple(dungeons)}))


def level_using(truth: GeometryTruth, panel_id: str) -> tuple[str, Level, tuple[int, int]]:
    """The dungeon id, level, and panel origin of the level that uses a panel."""
    for dungeon in truth.dungeons:
        for level in dungeon.levels:
            for part in level.panels:
                if part.panel == panel_id:
                    return dungeon.id, level, part.origin
    raise KeyError(panel_id)


def set_edge(truth: GeometryTruth, panel_id: str, edge: str, letter: str) -> GeometryTruth:
    """A copy with one edge changed in a panel's reading and in the grid of the level that uses the panel."""
    cell, side = parse_edge(edge)
    panel = truth.panel(panel_id)
    truth = replace_panel(truth, panel.model_copy(update={"edges": panel.edges.with_letter(cell, side, letter)}))
    dungeon_id, level, origin = level_using(truth, panel_id)
    assert level.edges is not None
    level_cell = (cell[0] + origin[0], cell[1] + origin[1])
    return replace_level(
        truth, dungeon_id, level.model_copy(update={"edges": level.edges.with_letter(level_cell, side, letter)})
    )


def move_key(truth: GeometryTruth, dungeon_id: str, number: int, label: str, to_cell: str) -> GeometryTruth:
    """A copy with one placed key on another cell."""
    level = truth.level(dungeon_id, number)
    keys = tuple(
        placed.model_copy(update={"cell": to_cell}) if placed.label == label else placed for placed in level.keys
    )
    return replace_level(truth, dungeon_id, level.model_copy(update={"keys": keys}))


def move_transition(truth: GeometryTruth, dungeon_id: str, number: int, cell: str, to_cell: str) -> GeometryTruth:
    """A copy with the transition on one cell moved to another."""
    level = truth.level(dungeon_id, number)
    transitions = tuple(
        item.model_copy(update={"cell": to_cell}) if item.cell == cell else item for item in level.transitions
    )
    return replace_level(truth, dungeon_id, level.model_copy(update={"transitions": transitions}))


def move_passage(
    truth: GeometryTruth, dungeon_id: str, number: int, cell: str, side: str, to_cell: str, to_side: str
) -> GeometryTruth:
    """A copy with one passage on another cell and side."""
    level = truth.level(dungeon_id, number)
    passages = tuple(
        item.model_copy(update={"cell": to_cell, "side": to_side}) if (item.cell, item.side) == (cell, side) else item
        for item in level.passages
    )
    return replace_level(truth, dungeon_id, level.model_copy(update={"passages": passages}))


def drop_passages(truth: GeometryTruth, dungeon_id: str, number: int) -> GeometryTruth:
    """A copy with every passage of a level removed."""
    level = truth.level(dungeon_id, number)
    return replace_level(truth, dungeon_id, level.model_copy(update={"passages": ()}))


def change_half_offset(truth: GeometryTruth, pair: int) -> GeometryTruth:
    """A copy with one half pair's column offset one cell larger."""
    pairs = list(truth.half_pairs)
    current = pairs[pair]
    assert current.columns is not None, "only a joined pair has a column offset"
    pairs[pair] = current.model_copy(update={"columns": current.columns + 1})
    return rebuild(truth.model_copy(update={"half_pairs": tuple(pairs)}))


def flip_to_fallback(truth: GeometryTruth, dungeon_id: str, number: int) -> GeometryTruth:
    """A copy in which one traced level fell back to synthesis."""
    fallback = Level(number=number, verdict="fallback", reason="no floor left on its map")
    return replace_level(truth, dungeon_id, fallback)


def apply_damage(truth: GeometryTruth, site: DamageSite) -> GeometryTruth:
    """Apply one change to a document."""
    if site.change == "wall_removed":
        assert site.panel and site.edge
        return set_edge(truth, site.panel, site.edge, "o")
    if site.change == "door_kind_changed":
        assert site.panel and site.edge
        return set_edge(truth, site.panel, site.edge, "s")
    if site.change == "secret_door_removed":
        assert site.panel and site.edge
        return set_edge(truth, site.panel, site.edge, "w")
    if site.change == "halves_offset_changed":
        assert site.pair is not None
        return change_half_offset(truth, site.pair)
    assert site.dungeon and site.level
    if site.change == "key_moved":
        assert site.label and site.to_cell
        return move_key(truth, site.dungeon, site.level, site.label, site.to_cell)
    if site.change == "transition_moved":
        assert site.cell and site.to_cell
        return move_transition(truth, site.dungeon, site.level, site.cell, site.to_cell)
    if site.change == "passage_moved":
        assert site.cell and site.side and site.to_cell and site.to_side
        return move_passage(truth, site.dungeon, site.level, site.cell, site.side, site.to_cell, site.to_side)
    return flip_to_fallback(truth, site.dungeon, site.level)


TOUCHED_FAMILIES: dict[str, tuple[str, ...]] = {
    "wall_removed": ("edges",),
    "key_moved": ("keys",),
    "door_kind_changed": ("doors",),
    "secret_door_removed": ("doors",),
    "transition_moved": ("transitions",),
    "passage_moved": ("passages",),
    "halves_offset_changed": ("halves",),
    "refusal_flipped": ("verdicts",),
}
"""The one family each change touches when its site is chosen as `DamageSite` describes."""


def _map_cells(cells: tuple[str, ...], shift: Callable[[tuple[int, int]], tuple[int, int]]) -> tuple[str, ...]:
    return tuple(cell_key(shift(parse_cell(cell))) for cell in cells)


def shift_panel_east(truth: GeometryTruth, panel_id: str) -> GeometryTruth:
    """A copy in which a panel's registration found one more column at its west edge.

    The panel and its region grow one column of rock at the west, so every
    reading moves one cell east. The level that uses the panel grows one
    column of rock at the west too, so its cells, areas, keys, transitions,
    passages, entrance, and other panels' origins all move one cell east.
    The scorer's alignment undoes the shift, so the copy scores the same as
    the original.
    """

    def east(cell: tuple[int, int]) -> tuple[int, int]:
        return (cell[0] + 1, cell[1])

    def widen(edges: EdgeGrids, cells: tuple[str, ...]) -> tuple[tuple[str, ...], EdgeGrids]:
        new_cells = tuple("#" + row for row in cells)
        vertical = tuple("-" + row for row in edges.vertical)
        horizontal = tuple("-" + row for row in edges.horizontal)
        return new_cells, EdgeGrids(vertical=vertical, horizontal=horizontal)

    panel = truth.panel(panel_id)
    cells, edges = widen(panel.edges, panel.cells)
    new_panel = panel.model_copy(
        update={
            "cells": cells,
            "edges": edges,
            "partial": _map_cells(panel.partial, east),
            "cave": _map_cells(panel.cave, east),
            "marks": tuple(
                mark.model_copy(update={"cell": cell_key(east(parse_cell(mark.cell)))}) for mark in panel.marks
            ),
            "outlines": tuple(
                outline.model_copy(
                    update={
                        "rects": tuple((x + 1, y, w, h) for x, y, w, h in outline.rects),
                        "seeds": _map_cells(outline.seeds, east),
                    }
                )
                for outline in panel.outlines
            ),
            "symbols": tuple(
                symbol.model_copy(update={"cells": _map_cells(symbol.cells, east)}) for symbol in panel.symbols
            ),
        }
    )
    truth = replace_panel(truth, new_panel)
    old_region = truth.region(panel.regions[0].region)
    new_region = old_region.model_copy(
        update={
            "columns": old_region.columns + 1,
            "registration": affine_registration(old_region.columns + 1, old_region.rows),
        }
    )
    regions = tuple(new_region if candidate.id == old_region.id else candidate for candidate in truth.regions)
    truth = rebuild(truth.model_copy(update={"regions": regions}))

    dungeon_id, level, _origin = level_using(truth, panel_id)
    assert level.edges is not None
    level_cells, level_edges = widen(level.edges, level.cells)
    new_level = level.model_copy(
        update={
            "cells": level_cells,
            "edges": level_edges,
            "panels": tuple(
                part
                if part.panel == panel_id
                else part.model_copy(update={"origin": (part.origin[0] + 1, part.origin[1])})
                for part in level.panels
            ),
            "areas": {
                key: area.model_copy(update={"cells": _map_cells(area.cells, east)})
                for key, area in level.areas.items()
            },
            "keys": tuple(
                placed.model_copy(update={"cell": cell_key(east(parse_cell(placed.cell)))}) for placed in level.keys
            ),
            "transitions": tuple(
                item.model_copy(update={"cell": cell_key(east(parse_cell(item.cell)))}) for item in level.transitions
            ),
            "passages": tuple(
                item.model_copy(
                    update={
                        "cell": cell_key(east(parse_cell(item.cell))),
                        "to_cell": cell_key(east(parse_cell(item.to_cell)))
                        if item.to_level == level.number
                        else item.to_cell,
                    }
                )
                for item in level.passages
            ),
        }
    )
    dungeon = next(candidate for candidate in truth.dungeons if candidate.id == dungeon_id)
    entrance = dungeon.entrance
    if entrance is not None and entrance.level == level.number:
        entrance = entrance.model_copy(update={"cell": cell_key(east(parse_cell(entrance.cell)))})
    levels: list[Level] = []
    for other in dungeon.levels:
        if other.number == level.number:
            levels.append(new_level)
            continue
        transitions = tuple(
            item.model_copy(update={"to_cell": cell_key(east(parse_cell(item.to_cell)))})
            if item.to_dungeon is None and item.to_level == level.number
            else item
            for item in other.transitions
        )
        levels.append(other.model_copy(update={"transitions": transitions}))
    shifted = dungeon.model_copy(update={"entrance": entrance, "levels": tuple(levels)})
    dungeons = tuple(shifted if candidate.id == dungeon_id else candidate for candidate in truth.dungeons)
    return rebuild(truth.model_copy(update={"dungeons": dungeons}))


def rotated_pair() -> tuple[GeometryTruth, GeometryTruth]:
    """A key whose one region has north at the left edge of the page, and a trace that read one more column.

    The key's panel, in page orientation, is 3 squares by 2:

    ```text
    ..#
    .##
    ```

    Its north is the page's left edge, so the level turns it 90 degrees
    clockwise: page cell `(x, y)` lands at `(1 - y, x)`, and with the level's
    one-cell margin at origin `(1, 1)`. The level is 4 by 5:

    ```text
    ####
    #..#    (1,1) is page (0,1), (2,1) is page (0,0)
    ##.#    (2,2) is page (1,0)
    ####
    ####
    ```

    The trace's registration found one more column at the page's west edge,
    so its panel is 4 by 2 with a column of rock first, and on its level every
    cell lies one row farther south. The scorer's alignment undoes that.
    """
    key_panel = Panel(
        id="pl",
        regions=(PanelRegion(region="rl", origin=(0, 0)),),
        scale_feet=10,
        cells=("..#", ".##"),
        edges=EdgeGrids(vertical=("wow-", "ww--"), horizontal=("ww-", "ow-", "w--")),
        marks=(Mark(text="1", cell="0,0"),),
        outlines=(Outline(dungeon="turn", level=1, rects=((0, 0, 3, 2),), seeds=("0,0",)),),
    )
    key_level = Level(
        number=1,
        verdict="traced",
        panels=(LevelPanel(panel="pl", origin=(1, 1), rotation=90),),
        cells=("####", "#..#", "##.#", "####", "####"),
        edges=EdgeGrids(
            vertical=("-----", "-wow-", "--ww-", "-----", "-----"),
            horizontal=("----", "-ww-", "-wo-", "--w-", "----", "----"),
        ),
        areas={"1": Area(cells=("1,1", "2,1", "2,2"))},
        keys=(PlacedKey(label="1", cell="2,1", area="1"),),
    )
    trace_panel = Panel(
        id="pl",
        regions=(PanelRegion(region="rl", origin=(0, 0)),),
        scale_feet=10,
        cells=("#..#", "#.##"),
        edges=EdgeGrids(vertical=("-wow-", "-ww--"), horizontal=("-ww-", "-ow-", "-w--")),
        marks=(Mark(text="1", cell="1,0"),),
        outlines=(Outline(dungeon="turn", level=1, rects=((1, 0, 3, 2),), seeds=("1,0",)),),
    )
    trace_level = Level(
        number=1,
        verdict="traced",
        panels=(LevelPanel(panel="pl", origin=(1, 1), rotation=90),),
        cells=("####", "####", "#..#", "##.#", "####", "####"),
        edges=EdgeGrids(
            vertical=("-----", "-----", "-wow-", "--ww-", "-----", "-----"),
            horizontal=("----", "----", "-ww-", "-wo-", "--w-", "----", "----"),
        ),
        areas={"1": Area(cells=("1,2", "2,2", "2,3"))},
        keys=(PlacedKey(label="1", cell="2,2", area="1"),),
    )

    def document(columns: int, panel: Panel, level: Level, entrance: str) -> GeometryTruth:
        return GeometryTruth(
            pages=(Page(number=1, images=(PageImage(file="rl.png"),)),),
            regions=(region("rl", 1, columns, 2, north="left"),),
            panels=(panel,),
            dungeons=(Dungeon(id="turn", name="Turn", entrance=Entrance(level=1, cell=entrance), levels=(level,)),),
        )

    return document(3, key_panel, key_level, "1,1"), document(4, trace_panel, trace_level, "1,2")
