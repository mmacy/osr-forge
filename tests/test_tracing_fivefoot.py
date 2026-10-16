"""The 5-foot conversion: phase choice, the cell and edge rules, narrow runs, and wall moves.

Chunk five-foot depends on the truth format alone. The scorer chunk depends on it.

Every expected grid below is worked by hand from "5-foot maps" in
`docs/tracing-conventions.md`. The counts that choose each phase are written
out in the docstrings so a reader can check them against the rule.
"""

import pytest

from osrforge.tracing.fivefoot import WallMove, convert_five_foot
from osrforge.tracing.truth import EdgeGrids, Mark, Outline, Panel, PanelRegion
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
