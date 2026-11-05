"""Hand-made readings for the rules no committed synthetic map reaches, checked against what the conventions say.

Each reading is a small picture: `+` at the grid corners, `-` or `|` for a wall, `d`, `s`, `l`, `p`, or `n` for the
other edge letters, a space for an open edge, and `.`, `#`, or `~` for a cell. An edge that touches no floor reads
`-` whatever the picture shows.
"""

from typing import Literal

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


def panel(pid, rows, marks=(), outlines=(), symbols=(), feet: Literal[5, 10] = 10):
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


def edges_of(level):
    assert level.edges is not None
    return level.edges


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
the cut in row 4, and an open edge in row 5 below it. The prose joins the halls by stairs, so a link would make
transitions unless a passage pair across the cut already makes the connection."""


def two_level_fixture(rows, height):
    survey_index = survey("d", [(1, [("1", "West hall")]), (2, [("2", "East hall")])])
    contents = {
        ("d", 1): content("d", 1, [("1", "A hall.", [{"to_key": "2", "direction": "down", "via": "stairs"}])]),
        ("d", 2): content("d", 2, [("2", "A hall.", [{"to_key": "1", "direction": "up", "via": "stairs"}])]),
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
    stands on its edge; the open edges across the cut get pairs too, and the cut is a wall on both levels. The pairs
    make the prose's stairs connection, so it is no link and no transition stands anywhere."""
    result = two_level_fixture(TWO_LEVELS, 7)
    one, two = level_of(result, 1), level_of(result, 2)
    assert passages(one) == {("5,2", "east", 2, "6,2"), ("5,4", "east", 2, "6,4"), ("5,5", "east", 2, "6,5")}
    assert passages(two) == {("6,2", "west", 1, "5,2"), ("6,4", "west", 1, "5,4"), ("6,5", "west", 1, "5,5")}
    assert edges_of(one).at((5, 4), "east") == "w" and edges_of(two).at((6, 4), "west") == "w"
    assert "5,2" in one.areas["1"].cells and "6,2" in two.areas["2"].cells
    assert "5,4" in one.areas["1"].cells and "5,5" in one.areas["1"].cells
    assert one.transitions == () and two.transitions == ()


VESTIBULE = [*TWO_LEVELS[:10], "+-+-+-+-+-+-+-+-+-+-+-+-+", "|#|#|#|#|#|#|#|#|#|#|#|#|", "+-+-+-+-+-+-+-+-+-+-+-+-+"]
"""The same halls without the corridor below the door, so the cell beyond the door can be a vestibule."""


def test_a_vestibule_takes_the_side_opposite_the_door():
    """The cell beyond the door becomes the earlier level's corridor, the door stays, the pair goes on the free side
    opposite the door, the vestibule's other sides are walls, and the cell is rock on the later level. The pair through
    the vestibule makes the prose's stairs connection, so no transition stands anywhere."""
    result = two_level_fixture(VESTIBULE, 6)
    one, two = level_of(result, 1), level_of(result, 2)
    assert one.cells[4][6] == "." and two.cells[4][6] == "#"
    assert edges_of(one).at((5, 4), "east") == "d"
    assert passages(one) == {("6,4", "east", 2, "7,4"), ("5,2", "east", 2, "6,2")}
    assert passages(two) == {("7,4", "west", 1, "6,4"), ("6,2", "west", 1, "5,2")}
    assert edges_of(one).at((6, 4), "north") == "w" and edges_of(one).at((6, 4), "south") == "w"
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
    assert edges_of(level).at((2, 2), "south") == "d"
    assert edges_of(level).at((1, 2), "south") == "w" and edges_of(level).at((1, 1), "west") == "w"
    assert edges_of(level).at((2, 1), "west") == "o" and edges_of(level).at((2, 1), "east") == "w"
    assert level.cells[1][1] == "." and level.cells[2][2] == "."


def enclosed_entrance_fixture(keys, areas, marks):
    """One level drawn as `UNPRINTED`, a hall and a closet walled in on every side, so the level has no outer opening
    and no opening can lead an unprinted entrance key into unkeyed floor."""
    survey_index = survey("e", [(1, keys)])
    contents = {("e", 1): content("e", 1, areas)}
    outline = Outline(dungeon="e", level=1, rects=((0, 0, 8, 7),), seeds=("2,3",))
    truth = document([region("p", 8, 7)], [panel("p", UNPRINTED, marks, (outline,))], survey_index)
    return resolve_document(truth, survey_index, contents, mapread("e", [1]))


def test_an_unprinted_entrance_key_with_no_opening_takes_the_unlabelled_room_beside_its_connected_area():
    """The level has no outer opening, so the Entrance is placed as any other unprinted key: its prose door into area 1
    gives it the unlabelled closet beside the hall. With no outer opening, the entrance is the area's first cell."""
    result = enclosed_entrance_fixture(
        [("1", "Hall"), ("2", "Entrance")],
        [("1", "A hall.", []), ("2", "The way in.", [{"to_key": "1", "direction": "west", "via": "door"}])],
        [("1", "2,3", {})],
    )
    level = level_of(result, 1)
    x0, y0 = level.panels[0].origin
    assert set(level.areas["2"].cells) == {f"{x0 + x},{y0 + y}" for x in (4, 5, 6) for y in (2, 3, 4)}
    assert not level.areas["2"].synthesized
    entrance = result.dungeons[0].entrance
    assert entrance is not None and (entrance.level, entrance.cell) == (1, f"{x0 + 4},{y0 + 2}")


def test_an_unprinted_entrance_key_with_no_opening_and_no_free_room_becomes_a_synthesized_area():
    """The closet is keyed 2, so no unlabelled walled room stands beside area 1, and the Entrance becomes a 2 by 2
    block of rock beside the hall that touches no other floor, first by its north-west cell, with a synthesized door
    on the first shared edge. With no outer opening, the entrance is that block's first cell."""
    result = enclosed_entrance_fixture(
        [("1", "Hall"), ("2", "Closet"), ("3", "Entrance")],
        [
            ("1", "A hall.", []),
            ("2", "A closet.", []),
            ("3", "The way in.", [{"to_key": "1", "direction": "north", "via": "secret_door"}]),
        ],
        [("1", "2,3", {}), ("2", "5,3", {})],
    )
    level = level_of(result, 1)
    x0, y0 = level.panels[0].origin
    assert set(level.areas["3"].cells) == {f"{x0 + x},{y0 + y}" for x in (0, 1) for y in (0, 1)}
    assert level.areas["3"].synthesized
    assert edges_of(level).at((x0 + 1, y0 + 1), "south") == "d"
    entrance = result.dungeons[0].entrance
    assert entrance is not None and (entrance.level, entrance.cell) == (1, f"{x0},{y0}")


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
    assert edges_of(level).at((5, 1), "east") == "w" and edges_of(level).at((5, 4), "east") == "o"
    entrance = result.dungeons[0].entrance
    assert entrance is not None and entrance.cell == "1,1"


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


TWO_ROOMS = [
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
"""Two rooms on one panel with rock between them, one on each of two levels, and no symbol anywhere."""


def two_rooms_fixture(via, symbols=(), to_key="2", direction="down", lower_connections=()):
    survey_index = survey("k", [(1, [("1", "Upper")]), (2, [("2", "Lower")])])
    contents = {
        ("k", 1): content("k", 1, [("1", "Upper.", [{"to_key": to_key, "direction": direction, "via": via}])]),
        ("k", 2): content("k", 2, [("2", "Lower.", list(lower_connections))]),
    }
    outlines = (
        Outline(dungeon="k", level=1, rects=((0, 0, 3, 4),), seeds=("1,1",)),
        Outline(dungeon="k", level=2, rects=((3, 0, 4, 4),), seeds=("4,1",)),
    )
    truth = document(
        [region("p", 7, 4)],
        [panel("p", TWO_ROOMS, [("1", "1,1", {}), ("2", "4,1", {})], outlines, symbols)],
        survey_index,
    )
    return resolve_document(truth, survey_index, contents, mapread("k", [1, 2]))


def test_a_link_by_a_door_to_another_level_makes_no_transition():
    """A prose door to an area on another level that no passage pair makes is flagged, not realized. The same reading
    by stairs makes a transition at each area's first cell, since neither level has a symbol, so the door's emptiness
    is the rule for its means and not an absence of links."""
    by_door = two_rooms_fixture("door")
    assert level_of(by_door, 1).transitions == () and level_of(by_door, 2).transitions == ()
    by_stairs = two_rooms_fixture("stairs")
    assert transitions(level_of(by_stairs, 1)) == {("1,1", "stairs_down", 2, "4,1")}
    assert transitions(level_of(by_stairs, 2)) == {("4,1", "stairs_up", 1, "1,1")}


def test_a_link_with_no_means_takes_the_means_of_the_one_kind_of_symbol_beside_its_area():
    """The prose names no means (its `via` is `passage`), and the only link symbol beside the upper room is a stair,
    so the link is by stairs: it stands on the stair, going down, and its other end lands on the lower room's first
    cell, since the lower level has no stair."""
    result = two_rooms_fixture("passage", (Symbol(kind="stairs", cells=("2,2",), direction="down"),))
    assert transitions(level_of(result, 1)) == {("2,2", "stairs_down", 2, "4,1")}
    assert transitions(level_of(result, 2)) == {("4,1", "stairs_up", 1, "2,2")}


def test_a_link_with_no_means_and_no_symbol_makes_no_transition():
    """The prose names no means and no link symbol touches or adjoins the upper room, so the link makes no
    transition. The same reading by stairs makes one, so this is the rule and not an absence of links."""
    result = two_rooms_fixture("passage")
    assert level_of(result, 1).transitions == () and level_of(result, 2).transitions == ()
    by_stairs = two_rooms_fixture("stairs")
    assert transitions(level_of(by_stairs, 1)) == {("1,1", "stairs_down", 2, "4,1")}


def test_a_link_with_no_means_and_symbols_of_two_means_makes_no_transition():
    """The prose names no means, and a stair and a trapdoor both touch the upper room, neither recording a
    direction, so the link makes no transition. With the trapdoor named in the prose instead, the link is by
    trapdoor and stands on it."""
    symbols = (Symbol(kind="stairs", cells=("2,2",)), Symbol(kind="trapdoor", cells=("1,2",)))
    result = two_rooms_fixture("passage", symbols)
    assert level_of(result, 1).transitions == () and level_of(result, 2).transitions == ()
    by_trapdoor = two_rooms_fixture("trapdoor", symbols)
    assert transitions(level_of(by_trapdoor, 1)) == {("1,2", "trapdoor", 2, "4,1")}
    assert level_of(by_trapdoor, 2).transitions == ()


def test_two_means_beside_an_area_where_the_directed_symbol_decides():
    """The prose names no means and the link goes down, as stated. A stair recording down and a trapdoor recording
    nothing both touch the upper room, so only the stair counts: the link is by stairs and stands on it."""
    symbols = (Symbol(kind="stairs", cells=("2,2",), direction="down"), Symbol(kind="trapdoor", cells=("1,2",)))
    result = two_rooms_fixture("passage", symbols)
    assert transitions(level_of(result, 1)) == {("2,2", "stairs_down", 2, "4,1")}
    assert transitions(level_of(result, 2)) == {("4,1", "stairs_up", 1, "2,2")}


def test_a_stated_up_toward_a_larger_level_number_gives_stairs_up():
    """The prose decides which way a level lies: a connection stated up to level 2 goes up, whatever the numbers
    say, so its stairs are stairs_up at the upper room and stairs_down at the lower room's end."""
    result = two_rooms_fixture("stairs", direction="up")
    assert transitions(level_of(result, 1)) == {("1,1", "stairs_up", 2, "4,1")}
    assert transitions(level_of(result, 2)) == {("4,1", "stairs_down", 1, "1,1")}


def test_a_trapdoor_stated_from_both_ends_starts_where_it_goes_down():
    """A trapdoor stated up from the upper room and down from the lower room starts at the lower room, though
    level 1 is above by number, so its one transition stands on the lower room and leads to the upper."""
    lower = [{"to_key": "1", "direction": "down", "via": "trapdoor"}]
    result = two_rooms_fixture("trapdoor", direction="up", lower_connections=lower)
    assert level_of(result, 1).transitions == ()
    assert transitions(level_of(result, 2)) == {("4,1", "trapdoor", 1, "1,1")}


@pytest.mark.parametrize("direction", ["east", "unknown"])
def test_an_unstated_direction_falls_back_to_the_level_numbers(direction):
    """A connection that states neither up nor down goes down to the greater level number, so the stairs from the
    upper room to level 2 are stairs_down there and stairs_up at the lower room's end."""
    result = two_rooms_fixture("stairs", direction=direction)
    assert transitions(level_of(result, 1)) == {("1,1", "stairs_down", 2, "4,1")}
    assert transitions(level_of(result, 2)) == {("4,1", "stairs_up", 1, "1,1")}


@pytest.mark.parametrize("to_key", ["Level 2", "second level", "2 LEVEL", "level Second"])
def test_a_level_shaped_to_key_is_a_connection_to_that_level(to_key):
    """A `to_key` that matches no key but reads as a level, in either word order and any case, is a connection to
    that level. With no symbol of its means on the lower level, the link's target end is the first cell of the
    level's first area, as for a link whose target area has no symbol."""
    result = two_rooms_fixture("stairs", to_key=to_key)
    assert transitions(level_of(result, 1)) == {("1,1", "stairs_down", 2, "4,1")}
    assert transitions(level_of(result, 2)) == {("4,1", "stairs_up", 1, "1,1")}


@pytest.mark.parametrize("to_key", ["the cellar", "level", "level eleventh", "2", "deep level 2"])
def test_a_to_key_that_matches_nothing_and_reads_as_no_level_makes_no_link(to_key):
    """A `to_key` that matches no key and isn't the word level with a number or an ordinal from first to tenth
    leads nowhere, so no transition stands. The bare `2` matches the lower room's key and is the control."""
    result = two_rooms_fixture("stairs", to_key=to_key)
    expected = {("1,1", "stairs_down", 2, "4,1")} if to_key == "2" else set()
    assert transitions(level_of(result, 1)) == expected


def test_a_level_only_link_lands_on_an_unused_symbol_of_its_means():
    """A link to a level with no target area ends on a symbol of its means on that level that no other link's end
    stands on, the first in north-then-west order: here the lower room's stair, which no link uses, rather than the
    room's first cell. The level's first area stands in for the target area, so on the two-cell stair the end takes
    the cell farthest by Chebyshev distance from that area's first cell, (5,1) rather than (4,1), which is the first
    cell itself. The stair's end leads back to the upper room."""
    result = two_rooms_fixture("stairs", (Symbol(kind="stairs", cells=("4,1", "5,1")),), to_key="Level 2")
    assert transitions(level_of(result, 1)) == {("1,1", "stairs_down", 2, "5,1")}
    assert transitions(level_of(result, 2)) == {("5,1", "stairs_up", 1, "1,1")}


def test_a_level_only_link_with_no_symbol_of_its_means_lands_on_the_first_area():
    """With a trapdoor on the lower level but none of the link's means, the stairs link to that level ends on the
    first cell of the level's first area."""
    result = two_rooms_fixture("stairs", (Symbol(kind="trapdoor", cells=("5,2",)),), to_key="Level 2")
    assert transitions(level_of(result, 1)) == {("1,1", "stairs_down", 2, "4,1")}
    assert transitions(level_of(result, 2)) == {("4,1", "stairs_up", 1, "1,1")}


GATEHOUSE = [
    "+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+",
    "|. . . .|#|#|#|",
    "+ + + + +-+-+-+",
    " . . . .|#|#|#|",
    "+ + + + +-+-+-+",
    "|. . . .|#|#|#|",
    "+-+d+-+-+-+-+-+",
    "|. . . .|#|#|#|",
    "+ + + + +-+-+-+",
    " . . . .|#|#|#|",
    "+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+",
]
"""A hall above a lower room, joined by a door, each running off the map's west frame: the hall in row 2, nearer the
hall's first cell, and the lower room in row 5. Rock fills the three columns east of them."""


def gatehouse_fixture(lower_key):
    lower = [("3", "Guard room")] if lower_key else []
    survey_index = survey("g", [(1, [("1", "Hall"), ("2", "Entrance"), *lower])])
    areas = [
        ("1", "A hall.", [{"to_key": "2", "direction": "south", "via": "door"}]),
        ("2", "The way in.", [{"to_key": "1", "direction": "north", "via": "door"}]),
    ]
    marks = [("1", "2,2", {})]
    if lower_key:
        areas.append(("3", "A guard room.", []))
        marks.append(("3", "2,4", {}))
    contents = {("g", 1): content("g", 1, areas)}
    outline = Outline(dungeon="g", level=1, rects=((0, 0, 7, 7),), seeds=("2,2",))
    truth = document([region("p", 7, 7)], [panel("p", GATEHOUSE, marks, (outline,))], survey_index)
    return truth, survey_index, contents


def test_an_unprinted_entrance_key_takes_the_nearest_opening_into_unkeyed_floor():
    """The unprinted key titled Entrance connects to area 1. The hall's own opening is nearer area 1's first cell, but
    it leads into area 1, a keyed area, so the key takes the unkeyed floor of the lower room's opening, up to the
    door."""
    truth, survey_index, contents = gatehouse_fixture(lower_key=False)
    result = resolve_document(truth, survey_index, contents, mapread("g", [1]))
    level = level_of(result, 1)
    x0, y0 = level.panels[0].origin
    assert set(level.areas["1"].cells) == {f"{x0 + x},{y0 + y}" for x in range(4) for y in (1, 2, 3)}
    assert set(level.areas["2"].cells) == {f"{x0 + x},{y0 + y}" for x in range(4) for y in (4, 5)}


def test_an_unprinted_entrance_key_with_no_opening_into_unkeyed_floor_becomes_a_synthesized_area():
    """With key 3 printed in the lower room, both openings lead into keyed areas, so the Entrance is placed as any
    other unprinted key. No unlabelled walled room stands beside area 1, so it becomes a synthesized area: the first
    2 by 2 block of rock beside the hall that touches no other floor, joined to it by a door on their first shared
    edge. The area touches no opening, and no walk reaches its rock from either opening, so the entrance is the cell
    just inside the first opening in north-then-west order, the hall's."""
    truth, survey_index, contents = gatehouse_fixture(lower_key=True)
    result = resolve_document(truth, survey_index, contents, mapread("g", [1]))
    level = level_of(result, 1)
    x0, y0 = level.panels[0].origin
    assert set(level.areas["2"].cells) == {f"{x0 + x},{y0 + y}" for x in (4, 5) for y in (0, 1)}
    assert level.areas["2"].synthesized
    assert edges_of(level).at((x0 + 4, y0 + 1), "west") == "d"
    entrance = result.dungeons[0].entrance
    assert entrance is not None and (entrance.level, entrance.cell) == (1, f"{x0},{y0 + 2}")


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
    assert level.cells[7][5] == "." and level.cells[7][4] == "~"


ROOMS_ACROSS_WATER = [
    "+-+-+-+-+-+",
    "|. . . . .|",
    "+-+-+ +-+-+",
    "|~ ~ ~ ~ ~|",
    "+-+-+ +-+-+",
    "|. . . . .|",
    "+-+-+-+-+-+",
]
"""Two walled rooms, each opening at column 2 onto a channel of water between them."""


@pytest.mark.parametrize("kind", ["footing", "steps"])
def test_the_key_measure_crosses_water_only_where_resolution_made_a_crossing(kind):
    """With footings on both banks the water cell between them is a crossing, and the scorer's walk joins the two
    rooms; with steps, which never make a crossing, the water stays water and the walk never enters it."""
    from tools.answer_keys.assemble import entrance_reach, prose_joins

    survey_index = survey("w", [(1, [("1", "North room"), ("2", "South room")])])
    contents = {
        ("w", 1): content(
            "w",
            1,
            [
                ("1", "A room.", [{"to_key": "2", "direction": "south", "via": "passage"}]),
                ("2", "A room.", [{"to_key": "1", "direction": "north", "via": "passage"}]),
            ],
        )
    }
    outline = Outline(dungeon="w", level=1, rects=((0, 0, 5, 3),), seeds=("0,0",))
    marks = [("1", "0,0", {}), ("2", "4,2", {})]
    symbols = (Symbol(kind=kind, cells=("2,0",)), Symbol(kind=kind, cells=("2,2",)))
    truth = document([region("p", 5, 3)], [panel("p", ROOMS_ACROSS_WATER, marks, (outline,), symbols)], survey_index)
    result = resolve_document(truth, survey_index, contents, mapread("w", [1]))
    level = level_of(result, 1)
    x0, y0 = level.panels[0].origin
    if kind == "footing":
        assert level.cells[y0 + 1][x0 + 2] == "."
        assert prose_joins(result, contents) == ["w/1: 1 of 1 prose joins made"]
        assert entrance_reach(result) == ["w/1: 11 of 11 floor cells reached, 4 water cells; every area reached"]
    else:
        assert level.cells[y0 + 1][x0 + 2] == "~"
        assert prose_joins(result, contents) == ["w/1: 0 of 1 prose joins made, not made: 1-2"]
        assert entrance_reach(result) == ["w/1: 5 of 10 floor cells reached, 5 water cells; areas not reached: 2"]


DUNGEONS_OPEN = [
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|. . . . . . . .|#|",
    "+-+ + + + + + + + +-+",
    "|#|. . . .|. . . .|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
]
"""The caves' hall in the west and the keep's hall in the east, their floors meeting across an open edge in row 1."""

DUNGEONS_DOOR = [
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|. . . .|. . . .|#|",
    "+-+ + + + +-+ + + +-+",
    "|#|. . . .d. . . .|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+",
]
"""The same two halls, walled apart, with a door between them in row 2."""


def two_dungeons_fixture(rows):
    """Two dungeons of one level each on one panel: the keep first in survey order and drawn east, the caves second
    and drawn west. Both levels are number 1 and both halls are keyed 1, so only the dungeons' survey order tells the
    two levels apart."""
    survey_index = SurveyIndex(
        title="borderland",
        hooks=(),
        town=TownInfo(name="town", description=""),
        dungeons=tuple(
            SurveyDungeon(
                id=dungeon,
                name=dungeon,
                levels=(
                    SurveyLevel(
                        number=1,
                        map_pages=(1,),
                        areas=(SurveyArea(key="1", name="Hall", kind="room", source_pages=(1,)),),
                    ),
                ),
            )
            for dungeon in ("keep", "caves")
        ),
        monster_names=(),
    )
    contents = {(dungeon, 1): content(dungeon, 1, [("1", "A hall.", [])]) for dungeon in ("keep", "caves")}
    mapread_cache = MapReading(
        map_reading="read",
        levels=tuple(
            MapLevelReading(dungeon_id=dungeon, level_number=1, map_pages=(1,), status="read")
            for dungeon in ("keep", "caves")
        ),
    )
    outlines = (
        Outline(dungeon="caves", level=1, rects=((0, 0, 5, 4),), seeds=("2,1",)),
        Outline(dungeon="keep", level=1, rects=((5, 0, 5, 4),), seeds=("7,1",)),
    )
    truth = GeometryTruth(
        pages=(Page(number=1, images=(PageImage(file="p.png"),)),),
        regions=(region("p", 10, 4),),
        panels=(panel("p", rows, [("1", "2,1", {}), ("1", "7,1", {})], outlines),),
        dungeons=tuple(
            Dungeon(id=dungeon, name=dungeon, levels=(Level(number=1, verdict="fallback", reason="x"),))
            for dungeon in ("keep", "caves")
        ),
    )
    result = resolve_document(truth, survey_index, contents, mapread_cache)
    return result.level("keep", 1), result.level("caves", 1)


def dungeon_passages(level):
    return {(p.cell, p.side, p.to_level, p.to_cell, p.to_dungeon) for p in level.passages}


def test_two_dungeons_meeting_across_an_open_edge_are_cut_with_a_pair_naming_the_other_dungeon():
    """The halls' floors meet in the drawing, so the two dungeons' levels are cut as two levels are: the open edge
    between them is a wall on both, with a pair of passages across it, each naming the other dungeon."""
    keep, caves = two_dungeons_fixture(DUNGEONS_OPEN)
    assert set(keep.areas["1"].cells) == {f"{x},{y}" for x in (5, 6, 7, 8) for y in (1, 2)}
    assert set(caves.areas["1"].cells) == {f"{x},{y}" for x in (1, 2, 3, 4) for y in (1, 2)}
    assert dungeon_passages(keep) == {("5,1", "west", 1, "4,1", "caves")}
    assert dungeon_passages(caves) == {("4,1", "east", 1, "5,1", "keep")}
    assert edges_of(keep).at((5, 1), "west") == "w" and edges_of(caves).at((4, 1), "east") == "w"
    assert keep.cells[1][4] == "#" and caves.cells[1][5] == "#"


def test_two_dungeons_meeting_at_a_door_give_a_vestibule_on_the_later_dungeons_side():
    """The keep comes first in survey order, so the cell beyond the door on the caves' side becomes the keep's
    corridor, the door stays on the keep, and the pair goes on the vestibule's side opposite the door, each passage
    naming the other dungeon. The cell is rock on the caves' level and leaves the caves' hall."""
    keep, caves = two_dungeons_fixture(DUNGEONS_DOOR)
    assert keep.cells[2][4] == "." and caves.cells[2][4] == "#"
    assert edges_of(keep).at((4, 2), "east") == "d"
    assert edges_of(keep).at((4, 2), "north") == "w" and edges_of(keep).at((4, 2), "south") == "w"
    assert dungeon_passages(keep) == {("4,2", "west", 1, "3,2", "caves")}
    assert dungeon_passages(caves) == {("3,2", "east", 1, "4,2", "keep")}
    assert "4,2" not in caves.areas["1"].cells and "4,2" not in keep.areas["1"].cells


@pytest.mark.parametrize("rows", [TWO_LEVELS, VESTIBULE, UNPRINTED, GATEHOUSE, YARD])
def test_pictures_parse_to_consistent_panels(rows):
    cells, edges = picture(rows)
    assert len(cells) == edges.height and len(cells[0]) == edges.width


SHARED_CORRIDOR = [
    "+-+-+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+-+-+",
    "|#|. . . .|#|#|. . . .|#|",
    "+-+ + + + +-+-+ + + + +-+",
    " . . . . . . . . . . .|#|",
    "+-+ + + + +-+-+ + + + +-+",
    "|#|. . . .|#|#|. . . .|#|",
    "+-+-+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+-+-+",
]
"""Two halls in one open space, level 1 west and level 2 east, joined by the open corridor of row 2, which runs off
the frame at the west. Only the east hall prints a key; the west hall's key is an unprinted Entrance."""


def test_an_area_takes_only_its_own_levels_floor():
    """The one open space has one printed key, on level 2, so without the owner rule it would take the west hall
    too and leave level 1's entrance key no unkeyed floor behind its opening."""
    survey_index = survey("d", [(1, [("entrance", "Entrance")]), (2, [("2", "East hall")])])
    contents = {
        ("d", 1): content("d", 1, [("entrance", "The way in.", [])]),
        ("d", 2): content("d", 2, [("2", "A hall.", [])]),
    }
    outlines = (
        Outline(dungeon="d", level=1, rects=((0, 0, 6, 5),), seeds=("2,2",)),
        Outline(dungeon="d", level=2, rects=((6, 0, 6, 5),), seeds=("8,2",)),
    )
    truth = document([region("p", 12, 5)], [panel("p", SHARED_CORRIDOR, [("2", "8,2", {})], outlines)], survey_index)
    result = resolve_document(truth, survey_index, contents, mapread("d", [1, 2]))
    one, two = level_of(result, 1), level_of(result, 2)
    # Level 1's grid gains a column of rock west of the floor that runs off the frame, so its cells sit one
    # column east of the panel's; level 2's grid is the panel's.
    assert {"1,2", "2,1", "5,3"} <= set(one.areas["entrance"].cells)
    assert all(int(cell.split(",")[0]) <= 6 for cell in one.areas["entrance"].cells)
    assert {"7,1", "10,3"} <= set(two.areas["2"].cells)
    assert all(int(cell.split(",")[0]) >= 6 for cell in two.areas["2"].cells)
    assert passages(one) == {("6,2", "east", 2, "6,2")} and passages(two) == {("6,2", "west", 1, "6,2")}


LAIR_TUNNEL = [
    "+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+",
    "|#|#|. . . . .|#|#|",
    "+-+-+ + + + + +-+-+",
    "|#|#|. . . . .|#|#|",
    "+-+-+-+-+d+-+-+-+-+",
    "|#|#|#|#|.|#|#|#|#|",
    "+-+-+-+-+ +-+-+-+-+",
    "|#|#|#|#|.|#|#|#|#|",
    "+-+-+-+-+ +-+-+-+-+",
    "|#|#|#|#|.|#|#|#|#|",
    "+-+-+-+-+ +-+-+-+-+",
    "|#|. . . . . . .|#|",
    "+-+ + + + + + + +-+",
    "|#|. . . . . . .|#|",
    "+-+-+-+-+-+-+-+-+-+",
]
"""A lair's den, a door, and a three-cell tunnel to a cave mouth on the tunnel's south end, with the outside below."""

LAIR_CHAMBER = [
    *LAIR_TUNNEL[:7],
    "|#|#|#|. . .|#|#|#|",
    "+-+-+-+ + + +-+-+-+",
    "|#|#|#|. . .|#|#|#|",
    "+-+-+-+ + + +-+-+-+",
    "|#|#|#|. . .|#|#|#|",
    *LAIR_TUNNEL[12:],
]
"""The same lair with a 3 by 3 chamber inside the mouth in place of the tunnel."""


def lair_fixture(rows, marks):
    """One dungeon, Lair A, whose only level has an unprinted Entrance and a den keyed 2, with a cave mouth on the
    south edge of cell (4,5). Its outline stops above the outside floor in rows 6 and 7."""
    survey_index = survey("lair-a", [(1, [("1", "Entrance"), ("2", "Den")])])
    survey_index = survey_index.model_copy(
        update={"dungeons": (survey_index.dungeons[0].model_copy(update={"name": "Lair A"}),)}
    )
    contents = {("lair-a", 1): content("lair-a", 1, [("1", "The way in.", []), ("2", "A den.", [])])}
    outline = Outline(dungeon="lair-a", level=1, rects=((0, 0, 9, 6),), seeds=("3,1",))
    mouth = Symbol(kind="cave_mouth", edge="4,5:south")
    truth = document(
        [region("p", 9, 8, symbols=("cave_mouth",))],
        [panel("p", rows, marks, (outline,), (mouth,))],
        survey_index,
    )
    return resolve_document(truth, survey_index, contents, mapread("lair-a", [1]))


def placed_keys(level):
    x0, y0 = level.panels[0].origin
    return {(k.label, k.cell, k.area, k.moved) for k in level.keys}, (x0, y0)


def test_a_dungeon_letter_beside_a_cave_mouth_prints_the_unprinted_entrance_key_inside_the_mouth():
    """The letter A is a whole word of Lair A and stands two cells from the mouth, and the map prints no entrance key,
    so the letter counts as the Entrance's print. Printed on the outside, the key moves just inside the mouth and takes
    the tunnel it stands in, which the rule for an unprinted entrance key couldn't give it: the tunnel is an unlabelled
    narrow run, so no outer opening led into unkeyed floor. The entrance stands on that cell."""
    result = lair_fixture(LAIR_TUNNEL, [("2", "3,1", {}), ("A", "6,7", {})])
    level = level_of(result, 1)
    keys, (x0, y0) = placed_keys(level)
    assert keys == {("2", f"{x0 + 3},{y0 + 1}", "2", False), ("A", f"{x0 + 4},{y0 + 5}", "1", True)}
    assert set(level.areas["1"].cells) == {f"{x0 + 4},{y0 + y}" for y in (3, 4, 5)}
    entrance = result.dungeons[0].entrance
    assert entrance is not None and (entrance.level, entrance.cell) == (1, f"{x0 + 4},{y0 + 5}")


def test_a_dungeon_letter_beside_a_cave_mouth_stays_a_label_when_the_entrance_key_is_printed():
    """With the Entrance printed in the tunnel, the letter marks the mouth but prints nothing, so it is no placed key
    and the printed key stays where it is."""
    result = lair_fixture(LAIR_TUNNEL, [("1", "4,4", {}), ("2", "3,1", {}), ("A", "6,7", {})])
    level = level_of(result, 1)
    keys, (x0, y0) = placed_keys(level)
    assert keys == {("1", f"{x0 + 4},{y0 + 4}", "1", False), ("2", f"{x0 + 3},{y0 + 1}", "2", False)}
    assert set(level.areas["1"].cells) == {f"{x0 + 4},{y0 + y}" for y in (3, 4, 5)}


@pytest.mark.parametrize(("cell", "printed"), [("6,7", True), ("7,7", False)])
def test_a_dungeon_letter_more_than_two_cells_from_any_cave_mouth_stays_a_label(cell, printed):
    """At (7,7) the letter is three cells from both of the mouth's cells, so it stays a label, and the unprinted
    Entrance takes the chamber through the rule for an unprinted entrance key. At (6,7), two cells away, the same
    letter is the Entrance's print, placed just inside the mouth, and the area is the same chamber."""
    result = lair_fixture(LAIR_CHAMBER, [("2", "3,1", {}), ("A", cell, {})])
    level = level_of(result, 1)
    keys, (x0, y0) = placed_keys(level)
    expected = {("2", f"{x0 + 3},{y0 + 1}", "2", False)}
    if printed:
        expected.add(("A", f"{x0 + 4},{y0 + 5}", "1", True))
    assert keys == expected
    assert set(level.areas["1"].cells) == {f"{x0 + x},{y0 + y}" for x in (3, 4, 5) for y in (3, 4, 5)}


LAIR_BESIDE_CAVES = [
    "+-+-+-+-+-+-+-+-+-+-+-+",
    "|#|#|#|#|#|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+-+",
    "|#|. . .|#|#|#|#|#|#|#|",
    "+-+ + + +-+-+-+-+-+-+-+",
    "|#|. . .|#|#|#|#|#|#|#|",
    "+-+-+d+-+-+-+-+-+-+-+-+",
    "|#|#|.|#|#|#|. . . .|#|",
    "+-+-+ +-+-+-+ + + + +-+",
    "|#|#|.|#|#|#|. . . .|#|",
    "+-+-+ +-+-+-+ + + + +-+",
    "|#|#|. . . . . . . .|#|",
    "+-+-+ +-+-+-+-+-+-+-+-+",
    "|#|. . . .|#|#|#|#|#|#|",
    "+-+ + + + +-+-+-+-+-+-+",
    "|#|. . . .|#|#|#|#|#|#|",
    "+-+-+-+-+-+-+-+-+-+-+-+",
]
"""Lair A's den and tunnel in the west, ending at a cave mouth on the south edge of (2,5), and the caves' hall in the
east, whose corridor runs west along row 5 to the tunnel's end. The outside lies below the mouth."""


def test_a_dungeon_letter_takes_its_mouth_whose_inner_cell_another_levels_outline_covers():
    """The caves' outline also covers the mouth's inner cell (2,5), and the caves' key reaches it first in the first
    partition, so without the letter the mouth would be the caves'. The letter A, a word of Lair A only, marks the mouth
    as the lair's, whose outline holds (2,5), and prints the lair's Entrance there, so the cell, its area, and the
    entrance are the lair's and the cell is rock on the caves' level."""
    dungeons = (("caves", "Caves", [("1", "Hall")]), ("lair-a", "Lair A", [("1", "Entrance"), ("2", "Den")]))
    survey_index = SurveyIndex(
        title="borderland",
        hooks=(),
        town=TownInfo(name="town", description=""),
        dungeons=tuple(
            SurveyDungeon(
                id=dungeon,
                name=name,
                levels=(
                    SurveyLevel(
                        number=1,
                        map_pages=(1,),
                        areas=tuple(
                            SurveyArea(key=key, name=title, kind="cave", source_pages=(1,)) for key, title in keys
                        ),
                    ),
                ),
            )
            for dungeon, name, keys in dungeons
        ),
        monster_names=(),
    )
    contents = {
        (dungeon, 1): content(dungeon, 1, [(key, "A cave.", []) for key, _title in keys])
        for dungeon, _n, keys in dungeons
    }
    mapread_cache = MapReading(
        map_reading="read",
        levels=tuple(
            MapLevelReading(dungeon_id=dungeon, level_number=1, map_pages=(1,), status="read")
            for dungeon, _name, _keys in dungeons
        ),
    )
    outlines = (
        Outline(dungeon="caves", level=1, rects=((2, 5, 9, 1), (5, 0, 6, 6)), seeds=("7,4",)),
        Outline(dungeon="lair-a", level=1, rects=((0, 0, 4, 5), (0, 5, 3, 1)), seeds=("1,1",)),
    )
    marks = [("1", "7,4", {}), ("2", "1,1", {}), ("A", "4,7", {})]
    mouth = Symbol(kind="cave_mouth", edge="2,5:south")
    truth = GeometryTruth(
        pages=(Page(number=1, images=(PageImage(file="p.png"),)),),
        regions=(region("p", 11, 8, symbols=("cave_mouth",)),),
        panels=(panel("p", LAIR_BESIDE_CAVES, marks, outlines, (mouth,)),),
        dungeons=tuple(
            Dungeon(id=dungeon, name=name, levels=(Level(number=1, verdict="fallback", reason="x"),))
            for dungeon, name, _keys in dungeons
        ),
    )
    result = resolve_document(truth, survey_index, contents, mapread_cache)
    lair, caves = result.level("lair-a", 1), result.level("caves", 1)
    keys, (x0, y0) = placed_keys(lair)
    assert ("A", f"{x0 + 2},{y0 + 5}", "1", True) in keys
    assert f"{x0 + 2},{y0 + 5}" in lair.areas["1"].cells
    entrance = next(dungeon for dungeon in result.dungeons if dungeon.id == "lair-a").entrance
    assert entrance is not None and (entrance.level, entrance.cell) == (1, f"{x0 + 2},{y0 + 5}")
    cx, cy = caves.panels[0].origin
    assert caves.cells[cy + 5][cx + 2] == "#"
