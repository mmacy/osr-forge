"""The 5-foot conversion: phase choice, the cell and edge rules, narrow runs, and wall moves.

Chunk five-foot depends on the truth format alone. The scorer chunk depends on it.

Every expected grid below is worked by hand from "5-foot maps" in
`docs/tracing-conventions.md`. The counts that choose each phase are written
out in the docstrings so a reader can check them against the rule.
"""

import pytest

from osrforge.tracing.fivefoot import WallMove, convert_five_foot
from osrforge.tracing.truth import EdgeGrids, Mark, Outline, Panel, PanelRegion, Symbol
from tracing_support import five_foot_pair

pytestmark = pytest.mark.xfail(reason="chunk: five-foot", strict=True)


def panel(cells: tuple[str, ...], vertical: tuple[str, ...], horizontal: tuple[str, ...], **extra: object) -> Panel:
    return Panel(
        id="p",
        regions=(PanelRegion(region="r", origin=(0, 0)),),
        scale_feet=5,
        cells=cells,
        edges=EdgeGrids(vertical=vertical, horizontal=horizontal),
        **extra,  # type: ignore[arg-type]
    )


def test_two_rooms_with_a_door_on_one_segment():
    """A door and a wall on the two segments of one 10-foot edge make a door.

    Blocked segments on 10-foot lines: with `ox = 0` the lines x = 0, 2, 4 hold
    2 + 2 + 2 = 6, with `ox = 1` the lines x = 1, 3 hold 0. With `oy = 0` the
    lines y = 0, 2 hold 8, with `oy = 1` the line y = 1 holds 0. Phase (0, 0).
    """
    five, ten = five_foot_pair()
    result = convert_five_foot(five.panels[0])
    assert result.phase == (0, 0)
    assert result.moves == ()
    assert result.panel == ten.panels[0]


def test_ten_foot_panel_is_refused():
    _five, ten = five_foot_pair()
    with pytest.raises(ValueError):
        convert_five_foot(ten.panels[0])


def test_wall_moves_toward_the_larger_room_and_keeps_the_small_one():
    """Room A is 3 squares wide and room B 1 square wide, with their wall on x = 3.

    Phase: `ox = 0` lines x = 0 and 4 hold 2 + 2 = 4 against 2 for `ox = 1`;
    `oy = 0` holds 8 against 0. Phase (0, 0). The run on x = 3 lies between
    floor squares, so it moves. Moving east would leave room B with no cell, so
    rule 1 moves it west, which is also toward the larger room. Column 2 goes
    to room B's side, and both rooms become one cell.
    """
    result = convert_five_foot(panel(("....", "...."), ("wooww", "wooww"), ("wwww", "oooo", "wwww")))
    assert result.phase == (0, 0)
    assert result.panel.cells == ("..",)
    assert result.panel.edges == EdgeGrids(vertical=("www",), horizontal=("ww", "ww"))
    assert result.moves == (WallMove(axis="vertical", line=3, moved_to=2, start=0, end=2),)
    assert result.panel.partial == ()


def test_phase_tie_prefers_the_westernmost_line_and_the_cell_rule_absorbs_floor_rock_lines():
    """Two 2-square rooms, A on columns 0 and 1 and B on columns 3 and 4, with rock between and after them.

    Blocked segments: x = 0, 2, 3, and 5 hold 2 each, so `ox = 0` (lines 0, 2,
    4) and `ox = 1` (lines 1, 3, 5) tie at 4, and the tie goes to `ox = 0`.
    Rows: `oy = 0` holds 8. Phase (0, 0). Cell 1 covers a rock column and a
    floor column, so it is floor and partly floor, and so is cell 2. Room B's
    walls on x = 3 and x = 5 lie between floor and rock, so they are not runs
    and nothing moves. The east frame of cell 2 touches floor with no segment
    reading, so it is a wall.
    """
    result = convert_five_foot(panel(("..#..#", "..#..#"), ("wowwow-", "wowwow-"), ("ww-ww-", "oo-oo-", "ww-ww-")))
    assert result.phase == (0, 0)
    assert result.panel.cells == ("...",)
    assert result.panel.edges == EdgeGrids(vertical=("wwow",), horizontal=("www", "www"))
    assert result.panel.partial == ("1,0", "2,0")
    assert result.moves == ()


def test_row_phase_is_chosen_on_its_own():
    """A room on rows 1 and 2 of a 2 by 4 grid: `oy = 1` puts both of its walls on 10-foot lines.

    Rows: `oy = 0` (lines 0, 2, 4) holds 0, `oy = 1` (lines 1, 3) holds 4.
    Columns: `ox = 0` holds 4. Phase (0, 1). The result is 3 cells high: a rock
    cell for the half block above the room, the room, and a rock cell below.
    """
    result = convert_five_foot(
        panel(("##", "..", "..", "##"), ("---", "wow", "wow", "---"), ("--", "ww", "oo", "ww", "--"))
    )
    assert result.phase == (0, 1)
    assert result.panel.cells == ("#", ".", "#")
    assert result.panel.edges == EdgeGrids(vertical=("--", "ww", "--"), horizontal=("-", "w", "w", "-"))
    assert result.panel.partial == ()


def test_narrow_run_one_square_wide_stays_joined():
    """An L-shaped corridor one square wide: three squares east along row 1, then south down column 2.

    Blocked segments: x = 0 holds 1, x = 2 holds 2, x = 3 holds 3, so `ox = 0`
    and `ox = 1` tie at 3 and `ox = 0` wins; y = 1 holds 3, y = 2 holds 2,
    y = 4 holds 1, so `oy = 0` and `oy = 1` tie at 3 and `oy = 0` wins. Phase
    (0, 0). The corner block (columns 2 and 3, rows 0 and 1) holds one floor
    square of four, which the half rule would make rock, but the corridor is a
    narrow run, so the block is floor and its edges to the run's other cells
    are passable. Every block that mixes floor and rock is partly floor. The
    cave square (2, 3) makes cell (1, 1) a cave cell.
    """
    result = convert_five_foot(
        panel(
            ("####", "...#", "##.#", "##.#"),
            ("-----", "woow-", "--ww-", "--ww-"),
            ("----", "www-", "wwo-", "--o-", "--w-"),
            cave=("2,3",),
        )
    )
    assert result.phase == (0, 0)
    assert result.panel.cells == ("..", "#.")
    assert result.panel.edges == EdgeGrids(vertical=("wow", "-ww"), horizontal=("ww", "wo", "-w"))
    assert result.panel.partial == ("0,0", "1,0", "1,1")
    assert result.panel.cave == ("1,1",)
    assert result.moves == ()


def test_marks_symbols_and_outlines_convert_to_containing_cells():
    """Under phase (0, 0), square (2, 0) lies in cell (1, 0), and the 4 by 2 outline becomes 2 by 1."""
    five, ten = five_foot_pair()
    source = five.panels[0].model_copy(
        update={
            "marks": (Mark(text="1", cell="0,0"), Mark(text="2", cell="2,0", leader_to="3,1")),
            "outlines": (Outline(dungeon="tiny", level=1, rects=((1, 0, 2, 2),), seeds=("3,1",)),),
        }
    )
    result = convert_five_foot(Panel.model_validate(source.model_dump()))
    assert result.panel.marks == (Mark(text="1", cell="0,0"), Mark(text="2", cell="1,0", leader_to="1,0"))
    assert result.panel.outlines == (Outline(dungeon="tiny", level=1, rects=((0, 0, 2, 1),), seeds=("1,0",)),)
    assert result.panel.scale_feet == 10
    assert result.panel.regions == ten.panels[0].regions


def test_mark_on_an_unmoved_off_grid_segment_goes_to_its_floor_square_side():
    """Ruling A. A locked door on vertical line 1 at row 0 lies between floor (0,0) and water (1,0).

    Blocked segments: `ox = 0` lines x = 0 and 2 hold 1 + 2 = 3 against 2 on
    x = 1; `oy = 0` lines y = 0 and 2 hold 2 + 1 = 3 against 1 on y = 1. Phase
    (0, 0), one cell of 2 floor, 1 water, and 1 rock squares, so floor and
    partly floor. The door's segment isn't between two floor squares, so it
    isn't a run and doesn't move, and line 1 isn't a 10-foot line. Its floor
    square is (0,0), in cell (0,0), and the segment faces east from it, so the
    mark's edge is 0,0:east, not the west side it was named from.
    """
    result = convert_five_foot(
        panel(
            (".~", "#."),
            ("wlw", "-wd"),
            ("ww", "dp", "-w"),
            partial=("0,1",),
            marks=(Mark(text="D", cell="1,0", edge="1,0:west"),),
        )
    )
    assert result.phase == (0, 0)
    assert result.moves == ()
    assert result.panel.cells == (".",)
    assert result.panel.edges == EdgeGrids(vertical=("wd",), horizontal=("w", "w"))
    assert result.panel.partial == ("0,0",)
    assert result.panel.marks == (Mark(text="D", cell="0,0", edge="0,0:east"),)


def test_a_move_is_ruled_out_when_a_room_would_lose_a_cell_it_had():
    """Ruling B. Three one-square rooms, (1,0), (0,1), and (1,1), with rock at (0,0).

    Phase (0, 0): vertical lines 0 and 2 hold 3 against 2, horizontal lines 0
    and 2 hold 3 against 2. One cell covers all four squares. The runs are the
    window on horizontal line 1 at column 1, midpoint (1.5, 1), and the door
    on vertical line 1 at row 1, midpoint (1, 1.5), so the window moves first.
    No room has a cell before that move, so rule 1 rules nothing out, there
    is no narrow run, and rule 3 compares 1 square with 1, so rule 4 sends it
    south to line 2. Square (1,1) joins room (1,0), which now owns the cell.
    For the door, east would give (1,1) to room (0,1) and room (1,0) would
    lose its cell, so east is ruled out, and west, which gives (0,1) to room
    (1,0), is left. The door's letter and its mark go to line 0. The west
    edge reads - and d, so d; the north frame reads - and n, so n; line 2
    reads w and n, so n.
    """
    result = convert_five_foot(
        panel(
            ("#.", ".."),
            ("-ww", "wdw"),
            ("-n", "wn", "ww"),
            partial=("0,0", "1,1"),
            marks=(Mark(text="1", cell="0,1"), Mark(text="D", cell="1,1", edge="1,1:west")),
        )
    )
    assert result.phase == (0, 0)
    assert result.panel.cells == (".",)
    assert result.panel.edges == EdgeGrids(vertical=("dw",), horizontal=("n", "n"))
    assert result.moves == (
        WallMove(axis="horizontal", line=1, moved_to=2, start=1, end=2),
        WallMove(axis="vertical", line=1, moved_to=0, start=1, end=2),
    )
    assert sorted(result.panel.marks, key=lambda mark: mark.text) == [
        Mark(text="1", cell="0,0"),
        Mark(text="D", cell="0,0", edge="0,0:west"),
    ]
    assert result.panel.partial == ("0,0",)


def test_a_room_may_give_up_a_cell_while_it_keeps_another():
    """Rule 1 counts cells: a room that keeps a cell may give up another.

    Phase (0, 0): vertical lines 0, 2, and 4 hold 2 + 0 + 1 = 3 blocked segments
    against 0 + 2 = 2 on lines 1 and 3, and horizontal lines 0 and 2 hold 3 + 4 = 7
    against 1 on line 1. Square (3,1) is walled on every side, with a window on
    the east frame, so it is walled room R2. The other six squares join through
    open sides and the printed line, and the door on the west frame closes them,
    so they are walled room R1. Cell (0,0) has four R1 squares and cell (1,0) has
    R1 squares (2,0) and (2,1), so R1 has both cells and R2 has none.

    The one run is the wall on vertical line 3 at row 1, between (2,1) and (3,1).
    East gives (3,1) to R1, and R1 keeps both cells. West gives (2,1) to R2, so
    cell (1,0) becomes R2's, but R1 still has cell (0,0), and R2 had no cell to
    lose. Rule 1 leaves both directions, and so does rule 2, since the 2 by 3
    block of R1 squares is joined and has no narrow run. Rule 3 moves the run
    toward R1, six squares against one, so west to line 2. A cell-by-cell rule
    would rule west out and move it east, giving vertical edges d, o, w.

    Line 2 then reads o and w, so w. Line 0 reads w and d, so d. Line 4 reads -
    and n, so n. The north frame reads w and n, so n, then w and -, so w. Both
    cells are partly floor: (0,0) holds partly-floor square (0,0), and (1,0)
    mixes rock (3,0) with floor. The door mark stays on the frame line 0.
    """
    result = convert_five_foot(
        panel(
            ("...#", "...."),
            ("woow-", "doown"),
            ("wnw-", "opow", "wwww"),
            partial=("0,0", "2,1"),
            marks=(Mark(text="1", cell="1,1"), Mark(text="D", cell="0,1", edge="0,1:west")),
        )
    )
    assert result.phase == (0, 0)
    assert result.panel.cells == ("..",)
    assert result.panel.edges == EdgeGrids(vertical=("dwn",), horizontal=("nw", "ww"))
    assert result.moves == (WallMove(axis="vertical", line=3, moved_to=2, start=1, end=2),)
    assert result.panel.partial == ("0,0", "1,0")
    assert sorted(result.panel.marks, key=lambda mark: mark.text) == [
        Mark(text="1", cell="0,0"),
        Mark(text="D", cell="0,0", edge="0,0:west"),
    ]


def test_a_block_is_joined_when_its_squares_connect_through_three_passable_edges():
    """Ruling C. A 2 by 3 room whose inner edges are o, p, o down the middle and s, o and o, d across.

    Phase: vertical lines 0 and 2 hold 6 against 0 on line 1; horizontal lines
    0 and 2 hold 2 + 1 = 3, lines 1 and 3 hold 1 + 2 = 3, and the tie goes to
    `oy = 0`. Both 2 by 2 blocks have three passable inner edges, so their
    squares are connected and they are joined blocks, and the room has no
    narrow run. The one run is the secret door on horizontal line 1 at column
    0. The room keeps both of its cells either way, rule 2 has no run to
    break, and rule 3 compares the same 6 squares with themselves, so rule 4
    sends it south to line 2, where s comes before the d beside it. The mark
    on the door goes with it to 0,0:south, and the cave mouth on the frame
    keeps its side. The cell of rows 2 and 3 covers two squares off the grid,
    so it is partly floor.
    """
    result = convert_five_foot(
        panel(
            ("..", "..", ".."),
            ("wod", "wpw", "wow"),
            ("ww", "so", "od", "ww"),
            marks=(
                Mark(text="1", cell="0,2"),
                Mark(text="2", cell="1,1"),
                Mark(text="D", cell="0,1", edge="0,1:north"),
            ),
            symbols=(Symbol(kind="cave_mouth", edge="0,0:west"),),
        )
    )
    assert result.phase == (0, 0)
    assert result.panel.cells == (".", ".")
    assert result.panel.edges == EdgeGrids(vertical=("wd", "ww"), horizontal=("w", "s", "w"))
    assert result.moves == (WallMove(axis="horizontal", line=1, moved_to=2, start=0, end=1),)
    assert sorted(result.panel.marks, key=lambda mark: mark.text) == [
        Mark(text="1", cell="0,1"),
        Mark(text="2", cell="0,0"),
        Mark(text="D", cell="0,0", edge="0,0:south"),
    ]
    assert result.panel.symbols == (Symbol(kind="cave_mouth", edge="0,0:west"),)
    assert result.panel.partial == ("0,1",)


def test_runs_with_one_midpoint_move_horizontal_first():
    """Ruling D. Four one-square spaces, every inner side blocked, with (0,0) open ground.

    Phase (0, 0): every line holds 2 blocked segments, and the frame lines
    give phase 0 four against two on each axis. The open-ground rectangle
    (0,0,1,1) holds only square (0,0), so its space is open ground and the
    other three are walled rooms. The vertical run (s, n on line 1) and the
    horizontal run (l, w on line 1) share the midpoint (1, 1), so the
    horizontal run goes first. No room has a cell, rule 3 compares 1 + 1
    squares with 1 + 1, and rule 4 sends it south to line 2. Row 1 joins the
    north: (0,1) joins the open ground and (1,1) joins room (1,0), which owns
    the cell. For the vertical run, east would take column 1 from room (1,0),
    so west is left. Line 0 takes s and n, so s; line 2 reads w and n, so n;
    horizontal line 2 takes l and w, so l. With the vertical run first the
    letters would be n and s across and l and w down.
    """
    result = convert_five_foot(
        panel(
            ("..", ".."),
            ("nsw", "wnn"),
            ("ww", "lw", "ww"),
            partial=("0,1", "1,0", "1,1"),
            marks=(Mark(text="1", cell="1,0"), Mark(text="2", cell="0,0"), Mark(text="3", cell="0,1", leader_to="1,0")),
            outlines=(
                Outline(dungeon="d", level=1, rects=((0, 0, 2, 2),), seeds=("0,1",), open_ground=((0, 0, 1, 1),)),
            ),
        )
    )
    assert result.phase == (0, 0)
    assert result.panel.cells == (".",)
    assert result.panel.edges == EdgeGrids(vertical=("sn",), horizontal=("w", "l"))
    assert result.moves == (
        WallMove(axis="horizontal", line=1, moved_to=2, start=0, end=2),
        WallMove(axis="vertical", line=1, moved_to=0, start=0, end=2),
    )
    assert sorted(result.panel.marks, key=lambda mark: mark.text) == [
        Mark(text="1", cell="0,0"),
        Mark(text="2", cell="0,0"),
        Mark(text="3", cell="0,0", leader_to="0,0"),
    ]
    assert result.panel.partial == ("0,0",)


def test_a_move_is_ruled_out_when_a_segment_would_land_inside_a_narrow_run():
    """Ruling E. Four rooms: (0,0), (1,0), and (0,1) alone, and (1,1) with (2,1), which is a narrow run.

    Phase (0, 0): vertical lines 0 and 2 hold 3 and lines 1 and 3 hold 3, so
    the tie goes to `ox = 0`; horizontal lines 0 and 2 hold 5 against 3. The
    runs on vertical line 1 (d, s) and horizontal line 1 (s, w) share the
    midpoint (1, 1), so the horizontal run goes first. No room has a cell,
    and rule 3 compares 1 + 1 squares north with 1 + 2 south, so it moves
    south to line 2, and row 1 joins the north. Now rooms (0,0) and (1,0)
    each hold two squares of the one full cell, and for the vertical run
    either direction takes that cell from one of them, so rule 1 rules both
    out. Moving east would land the s on vertical line 2 at row 1, between
    (1,1) and (2,1), the narrow run's consecutive squares, so rule 2 rules
    east out and west is left. Line 0 takes d and s, so s. The edge on line 2
    reads w and o, but the run's cells (1,1) and (2,1) are consecutive, so it
    is forced passable, and the east cell is floor for the same reason though
    it holds one floor square. That cell belongs to no walled room, so no wall
    is kept between the cells.
    """
    result = convert_five_foot(
        panel(
            ("..#", "..."),
            ("wdw-", "wsow"),
            ("dw-", "swn", "dww"),
            partial=("0,0", "0,1"),
            marks=(Mark(text="1", cell="1,0", leader_to="2,1"),),
        )
    )
    assert result.phase == (0, 0)
    assert result.panel.cells == ("..",)
    assert result.panel.edges == EdgeGrids(vertical=("sow",), horizontal=("dw", "sw"))
    assert result.moves == (
        WallMove(axis="horizontal", line=1, moved_to=2, start=0, end=2),
        WallMove(axis="vertical", line=1, moved_to=0, start=0, end=2),
    )
    assert result.panel.marks == (Mark(text="1", cell="0,0", leader_to="1,0"),)
    assert result.panel.partial == ("0,0", "1,0")


def test_open_ground_is_the_space_with_the_most_squares_in_its_rectangle():
    """The open-ground rule. Room A is square (0,0). Space B is (1,0) and (1,1), joined by a printed line.

    B is enclosed (its west side at row 1 is a locked door), and the
    open-ground rectangle (1,0,1,2) is column 1, where B has 2 squares and A
    none, so B is open ground and not a walled room. Phase (0, 0): vertical
    lines 0 and 2 hold 4 against 2, horizontal lines 0 and 2 hold 4 against
    1. The one run is the wall on vertical line 1 at row 0. A has one square
    and no cell, and B isn't a room, so rule 1 rules nothing out; B's squares
    join across a printed line, so there is no narrow run; rule 3 compares
    A's 1 square with B's 2 and moves the run east to line 2. If B counted as
    a walled room it would own the cell and the run would go west. The door
    mark on 1,1:west sits between water and floor on a segment that doesn't
    move, so it goes to the floor square's cell on the side the segment faces
    from (1,1): 0,0:west. The cell is partly floor and a cave cell.
    """
    result = convert_five_foot(
        panel(
            ("..", "~."),
            ("www", "wlw"),
            ("ww", "wp", "dw"),
            cave=("0,1", "1,0"),
            marks=(
                Mark(text="1", cell="1,0"),
                Mark(text="2", cell="1,0"),
                Mark(text="3", cell="0,0", leader_to="0,0"),
                Mark(text="D", cell="1,1", edge="1,1:west"),
            ),
            outlines=(
                Outline(dungeon="d", level=1, rects=((0, 0, 2, 2),), seeds=("0,0",), open_ground=((1, 0, 1, 2),)),
            ),
        )
    )
    assert result.phase == (0, 0)
    assert result.panel.cells == (".",)
    assert result.panel.edges == EdgeGrids(vertical=("ww",), horizontal=("w", "d"))
    assert result.moves == (WallMove(axis="vertical", line=1, moved_to=2, start=0, end=1),)
    assert sorted(result.panel.marks, key=lambda mark: mark.text) == [
        Mark(text="1", cell="0,0"),
        Mark(text="2", cell="0,0"),
        Mark(text="3", cell="0,0", leader_to="0,0"),
        Mark(text="D", cell="0,0", edge="0,0:west"),
    ]
    assert result.panel.partial == ("0,0",)
    assert result.panel.cave == ("0,0",)
