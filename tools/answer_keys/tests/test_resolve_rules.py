"""Hand-made readings for the rules no committed synthetic map reaches, checked against what the conventions say.

Each reading is a small picture: `+` at the grid corners, `-` or `|` for a wall, `d`, `s`, `l`, `p`, or `n` for the
other edge letters, a space for an open edge, and `.`, `#`, or `~` for a cell. An edge that touches no floor reads
`-` whatever the picture shows.
"""

import pytest
from tools.answer_keys.resolve import Resolver, reading_of

from osrforge.contracts.stages import (
    AreaConnection,
    AreaContent,
    LevelContent,
    MapLevelReading,
    MapReading,
    SurveyArea,
    SurveyDungeon,
    SurveyIndex,
    SurveyLevel,
    TownInfo,
)
from osrforge.tracing.truth import (
    Compass,
    Dungeon,
    EdgeGrids,
    GeometryTruth,
    HalfPair,
    Legend,
    Level,
    Mark,
    Outline,
    Page,
    PageImage,
    Panel,
    PanelRegion,
    Region,
    Registration,
    Scale,
    Symbol,
)

EDGE_LETTERS = {"|": "w", "-": "w", " ": "-", "d": "d", "s": "s", "l": "l", "p": "p", "n": "n", "o": "o"}


def picture(rows):
    """Cells and edges from a picture."""
    lines = [row for row in rows if row.strip()]
    assert len({len(row) for row in lines}) == 1, [len(row) for row in lines]
    height = (len(lines) - 1) // 2
    width = (len(lines[0]) - 1) // 2
    cells = ["".join(lines[2 * j + 1][2 * i + 1] for i in range(width)) for j in range(height)]
    horizontal = [[EDGE_LETTERS[lines[2 * j][2 * i + 1]] for i in range(width)] for j in range(height + 1)]
    vertical = [[EDGE_LETTERS[lines[2 * j + 1][2 * i]] for i in range(width + 1)] for j in range(height)]

    def floor(i, j):
        return 0 <= i < width and 0 <= j < height and cells[j][i] != "#"

    for j in range(height):
        for i in range(width + 1):
            if not (floor(i - 1, j) or floor(i, j)):
                vertical[j][i] = "-"
            elif vertical[j][i] == "-":
                vertical[j][i] = "o"
    for j in range(height + 1):
        for i in range(width):
            if not (floor(i, j - 1) or floor(i, j)):
                horizontal[j][i] = "-"
            elif horizontal[j][i] == "-":
                horizontal[j][i] = "o"
    edges = EdgeGrids(
        vertical=tuple("".join(row) for row in vertical), horizontal=tuple("".join(row) for row in horizontal)
    )
    return tuple(cells), edges


def region(rid, width, height, page=1, feet=10, cut_off=(), symbols=()):
    corners = tuple(tuple((float(x * 10), float(y * 10)) for x in range(width + 1)) for y in range(height + 1))
    return Region(
        id=rid,
        page=page,
        image=f"{rid}.png",
        style="black_white",
        columns=width,
        rows=height,
        scale=Scale(feet=feet, printed=True),
        compass=Compass(north="top", printed=True),
        legend=Legend(printed=True, symbols=tuple(symbols)),
        cut_off=tuple(cut_off),
        verdict="registered",
        registration=Registration(corners=corners),
    )


def panel(pid, rows, marks=(), outlines=(), symbols=(), feet=10):
    cells, edges = picture(rows)
    return Panel(
        id=pid,
        regions=(PanelRegion(region=pid, origin=(0, 0)),),
        scale_feet=feet,
        cells=cells,
        edges=edges,
        marks=tuple(Mark(text=text, cell=cell, **extra) for text, cell, extra in marks),
        outlines=tuple(outlines),
        symbols=tuple(symbols),
    )


def survey(dungeon, levels):
    return SurveyIndex(
        title=dungeon,
        hooks=(),
        town=TownInfo(name="town", description=""),
        dungeons=(
            SurveyDungeon(
                id=dungeon,
                name=dungeon,
                levels=tuple(
                    SurveyLevel(
                        number=number,
                        map_pages=(1,),
                        areas=tuple(
                            SurveyArea(key=key, name=name, kind="room", source_pages=(1,)) for key, name in keys
                        ),
                    )
                    for number, keys in levels
                ),
            ),
        ),
        monster_names=(),
    )


def content(dungeon, number, areas):
    return LevelContent(
        dungeon_id=dungeon,
        level_number=number,
        areas=tuple(
            AreaContent(
                key=key,
                description=description,
                encounters=(),
                treasure=(),
                features=(),
                connections=tuple(AreaConnection(**connection) for connection in connections),
                source_pages=(1,),
                confidence=1.0,
            )
            for key, description, connections in areas
        ),
    )


def mapread(dungeon, numbers):
    return MapReading(
        map_reading="read",
        levels=tuple(
            MapLevelReading(dungeon_id=dungeon, level_number=number, map_pages=(1,), status="read")
            for number in numbers
        ),
    )


def document(regions, panels, survey_index, pages=1, half_pairs=()):
    dungeon = survey_index.dungeons[0]
    return GeometryTruth(
        pages=tuple(
            Page(number=number, images=tuple(PageImage(file=r.image) for r in regions if r.page == number))
            for number in range(1, pages + 1)
        ),
        regions=tuple(regions),
        panels=tuple(panels),
        half_pairs=tuple(half_pairs),
        dungeons=(
            Dungeon(
                id=dungeon.id,
                name=dungeon.name,
                levels=tuple(Level(number=level.number, verdict="fallback", reason="x") for level in dungeon.levels),
            ),
        ),
    )


def resolve_document(truth, survey_index, contents, mapread_cache):
    return Resolver(reading_of(truth), survey_index, contents, mapread_cache).run()


def level_of(result, number):
    return next(level for level in result.dungeons[0].levels if level.number == number)


def passages(level):
    return {(p.cell, p.side, p.to_level, p.to_cell) for p in level.passages}


def transitions(level):
    return {(t.cell, t.kind, t.to_level, t.to_cell) for t in level.transitions}


TWO_LEVELS = [
    "+-+-+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+-+-+",
    "|#|. . . .|#|#|. . . .|#|",
    "+-+ + + + +-+-+ + + + +-+",
    "|#|. . . . . . . . . .|#|",
    "+-+ + + + +-+-+ + + + +-+",
    "|#|. . . .|#|#|. . . .|#|",
    "+-+ + + + +-+-+ + + + +-+",
    "|#|. . . . .d. . . . .|#|",
    "+-+-+-+-+-+ + +-+-+-+-+-+",
    "|#|#|#|#|#|. .|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+-+-+",
]
"""Two halls on one panel, level 1 west and level 2 east, joined by an open corridor edge in row 2, a door across
the cut in row 4, and an open edge in row 5 below it."""


def two_level_fixture(rows, height):
    survey_index = survey("d", [(1, [("1", "West hall")]), (2, [("2", "East hall")])])
    contents = {
        ("d", 1): content("d", 1, [("1", "A hall.", [{"to_key": "2", "direction": "east", "via": "door"}])]),
        ("d", 2): content("d", 2, [("2", "A hall.", [{"to_key": "1", "direction": "west", "via": "door"}])]),
    }
    outlines = (
        Outline(dungeon="d", level=1, rects=((0, 0, 6, height),), seeds=("2,2",)),
        Outline(dungeon="d", level=2, rects=((6, 0, 6, height),), seeds=("8,2",)),
    )
    truth = document(
        [region("p", 12, height)], [panel("p", rows, [("1", "2,2", {}), ("2", "8,2", {})], outlines)], survey_index
    )
    return resolve_document(truth, survey_index, contents, mapread("d", [1, 2]))


def test_a_door_on_a_cut_is_dropped_when_no_vestibule_fits():
    """Taking either cell beside the door would cut off the corridor cell below it, so the door is dropped and a pair
    stands on its edge; the open edges across the cut get pairs too, and the cut is a wall on both levels."""
    result = two_level_fixture(TWO_LEVELS, 7)
    one, two = level_of(result, 1), level_of(result, 2)
    assert passages(one) == {("5,2", "east", 2, "6,2"), ("5,4", "east", 2, "6,4"), ("5,5", "east", 2, "6,5")}
    assert passages(two) == {("6,2", "west", 1, "5,2"), ("6,4", "west", 1, "5,4"), ("6,5", "west", 1, "5,5")}
    assert one.edges.at((5, 4), "east") == "w" and two.edges.at((6, 4), "west") == "w"
    assert "5,2" in one.areas["1"].cells and "6,2" in two.areas["2"].cells
    assert "5,4" in one.areas["1"].cells and "5,5" in one.areas["1"].cells
    assert one.transitions == () and two.transitions == ()


VESTIBULE = [*TWO_LEVELS[:10], "+-+-+-+-+-+-+-+-+-+-+-+-+", "|#|#|#|#|#|#|#|#|#|#|#|#|", "+-+-+-+-+-+-+-+-+-+-+-+-+"]
"""The same halls without the corridor below the door, so the cell beyond the door can be a vestibule."""


def test_a_vestibule_takes_the_side_opposite_the_door():
    """The cell beyond the door becomes the earlier level's corridor, the door stays, the pair goes on the free side
    opposite the door, the vestibule's other sides are walls, and the cell is rock on the later level."""
    result = two_level_fixture(VESTIBULE, 6)
    one, two = level_of(result, 1), level_of(result, 2)
    assert one.cells[4][6] == "." and two.cells[4][6] == "#"
    assert one.edges.at((5, 4), "east") == "d"
    assert passages(one) == {("6,4", "east", 2, "7,4"), ("5,2", "east", 2, "6,2")}
    assert passages(two) == {("7,4", "west", 1, "6,4"), ("6,2", "west", 1, "5,2")}
    assert one.edges.at((6, 4), "north") == "w" and one.edges.at((6, 4), "south") == "w"
    assert "6,4" not in one.areas["1"].cells
    assert one.transitions == () and two.transitions == ()


UNPRINTED = [
    "+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+",
    "|#|. . .|. . .|#|",
    "+-+ + + + + + +-+",
    "|#|. . .d. . .|#|",
    "+-+ + + + + + +-+",
    "|#|. . .|. . .|#|",
    "+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+",
]


def test_unprinted_keys_take_an_unlabelled_room_or_a_synthesized_area():
    """Key 2's prose door into area 1 gives it the unlabelled room beside it. Key 3 has no room left, so it gets a 2 by
    2 block of rock beside area 1 that touches no other floor, first by its north-west cell, with a synthesized door on
    the first shared edge in north-then-west order."""
    survey_index = survey("e", [(1, [("1", "Hall"), ("2", "Closet"), ("3", "Hidden cell")])])
    contents = {
        ("e", 1): content(
            "e",
            1,
            [
                ("1", "A hall.", [{"to_key": "2", "direction": "east", "via": "door"}]),
                ("2", "A closet.", [{"to_key": "1", "direction": "west", "via": "door"}]),
                ("3", "No room on the map.", [{"to_key": "1", "direction": "north", "via": "secret_door"}]),
            ],
        )
    }
    outline = Outline(dungeon="e", level=1, rects=((0, 0, 8, 7),), seeds=("2,3",))
    truth = document([region("p", 8, 7)], [panel("p", UNPRINTED, [("1", "2,3", {})], (outline,))], survey_index)
    result = resolve_document(truth, survey_index, contents, mapread("e", [1]))
    level = level_of(result, 1)
    assert level.panels[0].origin == (1, 1)
    assert set(level.areas["2"].cells) == {f"{x},{y}" for x in (5, 6, 7) for y in (3, 4, 5)}
    assert set(level.areas["3"].cells) == {"1,1", "2,1", "1,2", "2,2"} and level.areas["3"].synthesized
    assert level.edges.at((2, 2), "south") == "d"
    assert level.edges.at((1, 2), "south") == "w" and level.edges.at((1, 1), "west") == "w"
    assert level.edges.at((2, 1), "west") == "o" and level.edges.at((2, 1), "east") == "w"
    assert level.cells[1][1] == "." and level.cells[2][2] == "."


WEST_HALF = [
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
    "|#|. . . . . ",
    "+-+ + + + + +",
    "|#|. . . . . ",
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
    "|#|. . . . . ",
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
]
EAST_HALF = [
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
    " . . . . .|#|",
    "+ + + + + +-+",
    " . . . . .|#|",
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
]


def test_halves_apart_pair_stretches_by_overlap_and_leave_the_rest_at_the_fold():
    """The west half's two-row stretch pairs with the east half's by row, with a passage pair per row; its one-row
    stretch has no partner, so its fold edge keeps the reading's letter and gets no passage."""
    survey_index = survey("f", [(1, [("1", "West"), ("2", "East")])])
    contents = {("f", 1): content("f", 1, [("1", "West.", []), ("2", "East.", [])])}
    regions = [region("west", 6, 6, page=1, cut_off=("right",)), region("east", 6, 6, page=2, cut_off=("left",))]
    panels = [
        panel(
            "west",
            WEST_HALF,
            [("1", "2,1", {})],
            (Outline(dungeon="f", level=1, rects=((0, 0, 6, 6),), seeds=("2,1",)),),
        ),
        panel(
            "east",
            EAST_HALF,
            [("2", "2,1", {})],
            (Outline(dungeon="f", level=1, rects=((0, 0, 6, 6),), seeds=("2,1",)),),
        ),
    ]
    truth = document(
        regions, panels, survey_index, pages=2, half_pairs=[HalfPair(regions=("west", "east"), joined=False, rows=0)]
    )
    result = resolve_document(truth, survey_index, contents, mapread("f", [1]))
    level = level_of(result, 1)
    assert [(p.panel, p.origin) for p in level.panels] == [("west", (0, 0)), ("east", (8, 0))]
    assert passages(level) == {
        ("5,1", "east", 1, "8,1"),
        ("8,1", "west", 1, "5,1"),
        ("5,2", "east", 1, "8,2"),
        ("8,2", "west", 1, "5,2"),
    }
    assert level.edges.at((5, 1), "east") == "w" and level.edges.at((5, 4), "east") == "o"
    assert result.dungeons[0].entrance.cell == "1,1"


TOP = [
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
    "|#|. . . .|#|",
    "+-+ + + + +-+",
    "|#|. . . .|#|",
    "+-+ + + + +-+",
    "|#|. . . .|#|",
    "+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+",
]
LOW = [
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|. . . . . .|#|#|",
    "+-+-+ + + + + + +-+-+",
    "|#|#|. . . . . .|#|#|",
    "+-+-+ + + + + + +-+-+",
    "|#|#|. . . . . .|#|#|",
    "+-+-+ + + + + + +-+-+",
    "|#|#|. . . . . .|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
]
"""A 10-foot room with a trapdoor and a slide, and a 5-foot room with a ladder, as two panels of one level."""


def test_links_between_panels_stand_on_symbols_of_their_own_means():
    """The trapdoor link stands on the trapdoor symbol only and the chute link on the slide only, each with one
    transition at the start, landing on the target area's first cell since the lower room has no symbol of that
    means. The stairs link from the lower room stands on its ladder; with no direction and none recorded, the end on
    the panel placed second is `stairs_up`, and its partner lands on the upper room's first cell as `stairs_down`."""
    survey_index = survey("g", [(1, [("1", "Upper"), ("2", "Lower")])])
    contents = {
        ("g", 1): content(
            "g",
            1,
            [
                (
                    "1",
                    "Upper.",
                    [
                        {"to_key": "2", "direction": "down", "via": "trapdoor"},
                        {"to_key": "2", "direction": "down", "via": "chute"},
                    ],
                ),
                ("2", "Lower.", [{"to_key": "1", "direction": "up", "via": "stairs"}]),
            ],
        )
    }
    regions = [
        region("top", 6, 5, symbols=("trapdoor", "slide")),
        region("low", 10, 8, feet=5, symbols=("ladder",)),
    ]
    panels = [
        panel(
            "top",
            TOP,
            [("1", "2,2", {})],
            (Outline(dungeon="g", level=1, rects=((0, 0, 6, 5),), seeds=("2,2",)),),
            (
                Symbol(kind="trapdoor", cells=("4,1",), direction="down"),
                Symbol(kind="slide", cells=("4,3",), direction="down"),
            ),
        ),
        panel(
            "low",
            LOW,
            [("2", "3,3", {})],
            (Outline(dungeon="g", level=1, rects=((0, 0, 10, 8),), seeds=("3,3",)),),
            (Symbol(kind="ladder", cells=("6,3",)),),
            feet=5,
        ),
    ]
    truth = document(regions, panels, survey_index)
    result = resolve_document(truth, survey_index, contents, mapread("g", [1]))
    level = level_of(result, 1)
    assert [(p.panel, p.origin) for p in level.panels] == [("top", (0, 0)), ("low", (8, 0))]
    assert set(level.areas["2"].cells) == {f"{x},{y}" for x in (9, 10, 11) for y in (1, 2)}
    assert transitions(level) == {
        ("4,1", "trapdoor", 1, "9,1"),
        ("4,3", "chute", 1, "9,1"),
        ("11,1", "stairs_up", 1, "1,1"),
        ("1,1", "stairs_down", 1, "11,1"),
    }


def test_a_link_by_a_door_to_another_level_makes_no_transition():
    """A prose door to an area on another level that no passage pair makes is flagged, not realized."""
    rows = [
        "+-+-+-+-+-+-+-+",
        "|#|#|#|#|#|#|#|",
        "+-+-+-+-+-+-+-+",
        "|#|. .|#|. .|#|",
        "+-+ + +-+ + +-+",
        "|#|. .|#|. .|#|",
        "+-+-+-+-+-+-+-+",
        "|#|#|#|#|#|#|#|",
        "+-+-+-+-+-+-+-+",
    ]
    survey_index = survey("k", [(1, [("1", "Upper")]), (2, [("2", "Lower")])])
    contents = {
        ("k", 1): content("k", 1, [("1", "Upper.", [{"to_key": "2", "direction": "down", "via": "door"}])]),
        ("k", 2): content("k", 2, [("2", "Lower.", [])]),
    }
    outlines = (
        Outline(dungeon="k", level=1, rects=((0, 0, 3, 5),), seeds=("1,1",)),
        Outline(dungeon="k", level=2, rects=((3, 0, 4, 5),), seeds=("4,1",)),
    )
    truth = document(
        [region("p", 7, 5)], [panel("p", rows, [("1", "1,1", {}), ("2", "4,1", {})], outlines)], survey_index
    )
    result = resolve_document(truth, survey_index, contents, mapread("k", [1, 2]))
    assert level_of(result, 1).transitions == () and level_of(result, 2).transitions == ()


YARD = [
    "+ + + + + + + + + + + + +",
    " . . . . . . . . . . . . ",
    "+ + + + + + + + + + + + +",
    " . . . . . . . . . . . . ",
    "+ + + +-+-+-+-+-+-+ + + +",
    " . . .|. . .|. . .|. . . ",
    "+ + + + + + + + + + + + +",
    " . . .|. . .d. . .|. . . ",
    "+ + + + + + + + + + + + +",
    " . . .|. . .|. . .|. . . ",
    "+ + + +-+-+d+-+-+-+ + + +",
    " . . . . . . . . . . . . ",
    "+ + + + + + + + + + + + +",
    " . . . ~ ~ ~ ~ ~ ~ . . . ",
    "+ + + + + + + + + + + + +",
    " . . . . . . . . . . . . ",
    "+ + + + + + + + + + + + +",
]
"""Open ground with a cottage and a byre side by side, a door between them, and water with footings on both banks."""


def test_keys_on_open_ground_on_an_inner_door_and_footings_across_water():
    """Key 2 is printed on the inner door between the cottage and the byre; both spaces hold no other key, so it goes
    to the side first in north-then-west order, the cottage. The cottage and the byre are one walled structure, joined
    by that door, and it has key 2, so key 3 on the open ground beside the byre takes no structure: it takes the
    open-ground floor within two cells of its label, as key 1 does, the cells they both reach going to the nearer by
    walking distance. The byre, an unlabelled walled room, joins area 2 through their door. The water cell between the
    two footings becomes floor."""
    survey_index = survey("h", [(1, [("1", "Yard shrine"), ("2", "Cottage"), ("3", "Byre")])])
    contents = {("h", 1): content("h", 1, [("1", "A shrine.", []), ("2", "A cottage.", []), ("3", "A byre.", [])])}
    outline = Outline(dungeon="h", level=1, rects=((0, 0, 12, 8),), seeds=("1,1",), open_ground=((0, 0, 12, 8),))
    marks = [("1", "10,1", {}), ("2", "6,3", {"edge": "6,3:west"}), ("3", "7,1", {})]
    symbols = (Symbol(kind="footing", cells=("4,5",)), Symbol(kind="footing", cells=("4,7",)))
    truth = document([region("p", 12, 8)], [panel("p", YARD, marks, (outline,), symbols)], survey_index)
    result = resolve_document(truth, survey_index, contents, mapread("h", [1]))
    level = level_of(result, 1)
    keys = {(k.label, k.cell, k.area, k.moved) for k in level.keys}
    assert keys == {("1", "11,2", "1", False), ("2", "6,4", "2", True), ("3", "8,2", "3", False)}
    assert set(level.areas["2"].cells) == {f"{x},{y}" for x in (4, 5, 6, 7, 8, 9) for y in (3, 4, 5)}
    assert set(level.areas["3"].cells) == {f"{x},{y}" for x in (6, 7, 8, 9) for y in (1, 2)}
    assert set(level.areas["1"].cells) == {f"{x},{y}" for x in (10, 11, 12) for y in (1, 2, 3, 4)}
    assert "8,4" not in level.areas["3"].cells and "8,4" not in level.areas["1"].cells
    assert level.cells[7][5] == "." and level.cells[7][4] == "~"


@pytest.mark.parametrize("rows", [TWO_LEVELS, VESTIBULE, UNPRINTED, YARD])
def test_pictures_parse_to_consistent_panels(rows):
    cells, edges = picture(rows)
    assert len(cells) == edges.height and len(cells[0]) == edges.width
