"""The synthetic maps for the other styles and the page-level features.

The dark-rock and gridded-cave styles, a 5-foot map, a page turned on its
side, a map with no compass and one using another page's legend, the page
manifest's hidden, spread, duplicate, and blanked images, and the maps the
stage must refuse or can't register. As in `_maps`, every answer is worked
out from the reading by the conventions, and the refused and unregistered
maps' levels fall back.
"""

from dataclasses import replace

from osrforge.contracts.stages import AreaConnection
from osrforge.tracing.synthetic import DamageSite
from osrforge.tracing.synthetic._cave import cave_plan
from osrforge.tracing.synthetic._plan import Ornament, Sketch
from osrforge.tracing.synthetic._spec import (
    AreaSpec,
    DungeonSpec,
    LevelSpec,
    MapSpec,
    PanelSpec,
    RegionSpec,
    SheetSpec,
)
from osrforge.tracing.truth import Compass, Legend, Page, PageImage, Scale

NO_COMPASS = Compass(north="top", printed=False)
NO_LEGEND = Legend(printed=False)


def sunken_temple() -> MapSpec:
    """Two dark-rock levels; the second page prints no compass and uses the first page's legend."""
    upper = Sketch(20, 14)
    upper.room("hall", 5, 4, 10, 6).mark("1", (8, 6))
    upper.run("a", (9, 10), (9, 13)).edge((9, 13), "south", "o").edge((9, 9), "south", "o")
    upper.room("shrine", 15, 5, 4, 4).edge((14, 6), "east", "d").mark("2", (16, 7))
    upper.symbol("stairs", (17, 5), (17, 6), direction="down", run="ns")
    upper.outline((0, 0, 20, 14))
    lower = Sketch(18, 12)
    lower.room("r3", 2, 2, 8, 6).water(4, 5, 3, 3).mark("3", (5, 3))
    lower.symbol("stairs", (2, 2), (2, 3), run="ns")
    lower.room("r4", 10, 2, 5, 5).edge((9, 3), "east", "l").mark("4", (12, 4))
    lower.outline((0, 0, 18, 12))
    upper_plan, lower_plan = upper.build(), lower.build()
    legend = ("stairs", "locked_door")
    regions = (
        RegionSpec("temple-1", 1, "sunken-temple-1.png", "dark_rock", upper_plan, "THE SUNKEN TEMPLE", legend=legend),
        RegionSpec(
            "temple-2",
            2,
            "sunken-temple-2.png",
            "dark_rock",
            lower_plan,
            "THE SUNKEN TEMPLE, BELOW",
            legend=legend,
            compass=NO_COMPASS,
            legend_from=1,
        ),
    )
    panels = (
        PanelSpec("temple-1", (("temple-1", (0, 0)),), upper_plan),
        PanelSpec("temple-2", (("temple-2", (0, 0)),), lower_plan),
    )
    first = (
        AreaSpec("1", "Pillared hall", "The passage from the surface opens into a long hall."),
        AreaSpec(
            "2",
            "Shrine",
            "Stairs in the shrine's corner go down to the flooded hall.",
            links=(AreaConnection(to_key="3", direction="down", via="stairs"),),
        ),
    )
    second = (
        AreaSpec("3", "Flooded hall", "A pool of still water fills the south side of the hall."),
        AreaSpec("4", "Reliquary", "An iron-bound door, locked, guards the reliquary."),
    )
    dungeon = DungeonSpec(
        "the-sunken-temple",
        "The sunken temple",
        (LevelSpec(1, first, ("temple-1",)), LevelSpec(2, second, ("temple-2",))),
    )
    return MapSpec(
        name="sunken-temple",
        title="The sunken temple",
        features=("legend_on_another_page", "no_compass"),
        dungeon=dungeon,
        regions=regions,
        panels=panels,
        damage=(DamageSite(change="key_moved", dungeon="the-sunken-temple", level=1, label="2", to_cell="7,6"),),
    )


def wyrm_caves() -> MapSpec:
    """A gridded cave whose walls are drawn off the grid, with partly-floor cells and a door in a tunnel."""
    sketch = Sketch(27, 16)
    caves = (
        Ornament("cave", ((-1.0, 7.5), (4.0, 7.5)), 0.62),
        Ornament("cave", ((6.5, 7.5), (6.5, 7.5)), 3.3),
        Ornament("cave", ((9.0, 7.5), (18.5, 7.5)), 0.62),
        Ornament("cave", ((20.5, 6.5), (20.5, 6.5)), 2.7),
        Ornament("cave", ((20.5, 9.0), (20.5, 11.5)), 0.62),
        Ornament("cave", ((20.5, 13.0), (20.5, 13.0)), 2.2),
    )
    sketch.edge((0, 7), "west", "o").edge((13, 7), "east", "d")
    sketch.mark("1", (6, 7)).mark("2", (20, 6)).mark("3", (20, 13))
    sketch.outline((0, 0, 27, 16))
    plan = cave_plan(sketch, caves)
    region = RegionSpec("wyrm-caves", 1, "wyrm-caves.png", "cave", plan, "THE WYRM CAVES")
    panel = PanelSpec("wyrm-caves", (("wyrm-caves", (0, 0)),), plan)
    areas = (
        AreaSpec("1", "Mouth cavern", "The tunnel from the hillside opens into a round cavern.", "cave"),
        AreaSpec("2", "Long cavern", "A long cavern, its floor littered with bones.", "cave"),
        AreaSpec("3", "Wyrm's den", "A low den at the end of a winding tunnel.", "cave"),
    )
    dungeon = DungeonSpec("the-wyrm-caves", "The wyrm caves", (LevelSpec(1, areas, ("wyrm-caves",)),))
    return MapSpec(
        name="wyrm-caves",
        title="The wyrm caves",
        features=("corridor_off_frame",),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
        damage=(DamageSite(change="key_moved", dungeon="the-wyrm-caves", level=1, label="3", to_cell="8,8"),),
    )


def sideways_keep() -> MapSpec:
    """A TSR blue map printed on its side, its compass pointing to the left edge of the page."""
    upright = Sketch(18, 14)
    upright.run("a", (8, 10), (8, 13)).edge((8, 13), "south", "o").edge((8, 9), "south", "d")
    upright.room("hall", 4, 4, 9, 6).mark("1", (6, 6))
    upright.room("r2", 13, 4, 4, 4).edge((12, 5), "east", "d").mark("2", (14, 5))
    upright.room("r3", 4, 1, 4, 3).edge((5, 3), "south", "d").mark("3", (5, 2))
    upright.outline((0, 0, 18, 14))
    plan = upright.build().turned(270)
    region = RegionSpec(
        "sideways",
        1,
        "sideways-keep.jpg",
        "tsr_blue",
        plan,
        "THE SIDEWAYS KEEP",
        compass=Compass(north="left", printed=True),
    )
    panel = PanelSpec("sideways", (("sideways", (0, 0)),), plan)
    areas = (
        AreaSpec("1", "Great hall", "The passage from the gate ends at the hall's south door."),
        AreaSpec("2", "Armoury", "Empty weapon racks."),
        AreaSpec("3", "Chapel", "A plain stone altar."),
    )
    dungeon = DungeonSpec("the-sideways-keep", "The sideways keep", (LevelSpec(1, areas, ("sideways",)),))
    return MapSpec(
        name="sideways-keep",
        title="The sideways keep",
        features=("rotated_page", "corridor_off_frame"),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
        damage=(DamageSite(change="key_moved", dungeon="the-sideways-keep", level=1, label="3", to_cell="8,6"),),
    )


def cellars() -> MapSpec:
    """A black-and-white map on a 5-foot grid, with rooms two and three squares wide."""
    sketch = Sketch(24, 18)
    sketch.room("c", 10, 12, 2, 6).edges("o", ((10, 17), "south"), ((11, 17), "south"))
    sketch.room("a", 6, 6, 10, 6).edges("o", ((10, 11), "south"), ((11, 11), "south")).mark("1", (9, 8))
    sketch.room("b", 3, 6, 3, 6).edge((5, 8), "east", "d").mark("2", (4, 8))
    sketch.room("cc", 16, 6, 2, 6).edge((15, 9), "east", "d").mark("3", (16, 8))
    sketch.room("d", 9, 3, 3, 3).edge((10, 5), "south", "d").mark("4", (10, 4))
    sketch.outline((0, 0, 24, 18))
    plan = sketch.build()
    region = RegionSpec(
        "cellars",
        1,
        "cellars.png",
        "black_white",
        plan,
        "THE CELLARS",
        scale=Scale(feet=5, printed=True, text="1 SQUARE = 5 FEET"),
    )
    panel = PanelSpec("cellars", (("cellars", (0, 0)),), plan, scale_feet=5)
    areas = (
        AreaSpec("1", "Wine cellar", "Racks of empty bottles fill the cellar."),
        AreaSpec("2", "Cold store", "A narrow room, three squares wide, kept cold by the stone."),
        AreaSpec("3", "Coal hole", "A slot of a room two squares wide, black with coal dust."),
        AreaSpec("4", "Steward's room", "A small square room with a desk."),
    )
    dungeon = DungeonSpec("the-cellars", "The cellars", (LevelSpec(1, areas, ("cellars",)),))
    return MapSpec(
        name="cellars",
        title="The cellars",
        features=("five_foot_rooms_two_and_three_wide",),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
    )


def scattered_pages() -> MapSpec:
    """A module whose pages hide a draft under a map, spread a map, print a map twice, and blank a page."""
    first = Sketch(16, 12)
    first.run("a", (0, 6), (3, 6)).edge((0, 6), "west", "o").edge((3, 6), "east", "d")
    first.room("r1", 4, 3, 7, 7).mark("1", (6, 5))
    first.symbol("stairs", (9, 4), (9, 5), direction="down", run="ns")
    first.outline((0, 0, 16, 12))
    spread = Sketch(40, 12)
    spread.room("r2", 2, 3, 7, 6).water(5, 7, 3, 2).mark("2", (5, 4))
    spread.symbol("stairs", (3, 4), (3, 5), run="ns")
    spread.run("b", (9, 5), (29, 5)).edge((8, 5), "east", "d").edge((29, 5), "east", "d")
    spread.room("r3", 30, 2, 7, 7).mark("3", (33, 5))
    spread.outline((0, 0, 40, 12))
    first_plan, spread_plan = first.build(), spread.build()
    regions = (
        RegionSpec(
            "first", 1, "scattered-first.png", "black_white", first_plan, "THE SCATTERED PAGES", legend=("stairs",)
        ),
        RegionSpec(
            "spread", 2, "scattered-spread.png", "black_white", spread_plan, "THE LONG GALLERY", legend=("stairs",)
        ),
    )
    panels = (
        PanelSpec("first", (("first", (0, 0)),), first_plan),
        PanelSpec("spread", (("spread", (0, 0)),), spread_plan),
    )
    sheets = (
        SheetSpec(
            "draft",
            1,
            "scattered-draft.png",
            "black_white",
            "plan",
            "DRAFT",
            plan=replace(first_plan, marks=()),
        ),
        SheetSpec(
            "art",
            5,
            "scattered-art.png",
            "outdoor",
            "outdoor",
            "THE ROAD TO THE GALLERY",
            scale=Scale(feet=10, printed=False),
        ),
    )
    pages = (
        Page(
            number=1, images=(PageImage(file="scattered-draft.png", hidden=True), PageImage(file="scattered-first.png"))
        ),
        Page(number=2, images=(PageImage(file="scattered-spread.png"),), spread_with=3),
        Page(number=3, images=(PageImage(file="scattered-spread.png"),), spread_with=2),
        Page(number=4, images=(PageImage(file="scattered-first.png"),)),
        Page(number=5, images=(PageImage(file="scattered-art.png"),), blanked=True),
    )
    upper = (
        AreaSpec(
            "1",
            "Archive",
            "Stairs in the archive go down to the long gallery.",
            links=(AreaConnection(to_key="2", direction="down", via="stairs"),),
        ),
    )
    lower = (
        AreaSpec("2", "Gallery stair", "The stairs from the archive come down beside a shallow pool."),
        AreaSpec("3", "Far gallery", "The long passage ends in a square gallery."),
    )
    dungeon = DungeonSpec(
        "the-scattered-pages",
        "The scattered pages",
        (LevelSpec(1, upper, ("first",), pages=(1, 4)), LevelSpec(2, lower, ("spread",), pages=(2, 3))),
    )
    return MapSpec(
        name="scattered-pages",
        title="The scattered pages",
        features=("image_hidden_under_shown", "page_spread", "page_duplicate_image", "page_blanked"),
        dungeon=dungeon,
        regions=regions,
        panels=panels,
        sheets=sheets,
        pages=pages,
    )


def greyrock_country() -> MapSpec:
    """A module whose maps the stage refuses: outdoor, hex, town, a quarter mile to the square, isometric, side view."""
    sheets = (
        SheetSpec(
            "wilds",
            1,
            "greyrock-wilds.png",
            "outdoor",
            "outdoor",
            "THE GREYROCK WILDS",
            verdict="refused",
            reason="an outdoor map",
            scale=Scale(feet=None, printed=True, text="1 INCH = 2 MILES"),
            legend=NO_LEGEND,
        ),
        SheetSpec(
            "hexes",
            2,
            "greyrock-hexes.png",
            "hex",
            "hex",
            "THE BORDER HEXES",
            verdict="refused",
            reason="a hex map",
            scale=Scale(feet=None, printed=True, text="1 HEX = 6 MILES"),
            legend=NO_LEGEND,
        ),
        SheetSpec(
            "village",
            3,
            "greyrock-village.png",
            "town",
            "town",
            "GREYROCK VILLAGE",
            verdict="refused",
            reason="a town map",
            scale=Scale(feet=None, printed=True, text="1 INCH = 100 FEET"),
            legend=NO_LEGEND,
        ),
        SheetSpec(
            "valley",
            4,
            "greyrock-valley.png",
            "black_white",
            "quarter_mile",
            "THE TOWER VALLEY",
            verdict="refused",
            reason="a scale of a quarter mile to the square",
            columns=26,
            rows=16,
            scale=Scale(feet=None, printed=True, text="1 SQUARE = 1/4 MILE"),
            legend=NO_LEGEND,
        ),
        SheetSpec(
            "tower",
            5,
            "greyrock-tower.png",
            "isometric",
            "isometric",
            "GREYROCK TOWER",
            verdict="refused",
            reason="an isometric map",
            legend=NO_LEGEND,
        ),
        SheetSpec(
            "section",
            6,
            "greyrock-section.png",
            "side_view",
            "side_view",
            "GREYROCK TOWER IN SECTION",
            verdict="refused",
            reason="a side view",
            compass=NO_COMPASS,
            legend=NO_LEGEND,
        ),
    )
    levels = (
        (("w1", "Hollowmere", "A village at the road's end."), ("w2", "Greyrock", "The tower on the ridge.")),
        (("h1", "Ford hex", "A ford across the river."), ("h2", "Barrow hex", "Old barrows in the hills.")),
        (("v1", "Inn", "The Wayfarer's inn."), ("v2", "Temple", "A small stone temple.")),
        (("r1", "Ruin", "A ruined watchtower in the valley."),),
        (
            ("1", "Entry hall", "The tower's ground floor."),
            ("2", "Guard room", "Beside the entry hall."),
            ("3", "Store", "Behind the guard room."),
        ),
        (("c1", "Cellar", "Below the tower."), ("c2", "Crypt", "Below the cellar.")),
    )
    reasons = (
        "its map is an outdoor map, which the stage refuses",
        "its map is a hex map, which the stage refuses",
        "its map is a town map, which the stage refuses",
        "its map is a quarter mile to the square, a scale the stage refuses",
        "its map is an isometric view, which the stage refuses",
        "its map is a side view, which the stage refuses",
    )
    dungeon = DungeonSpec(
        "greyrock-tower",
        "Greyrock tower",
        tuple(
            LevelSpec(
                number,
                tuple(AreaSpec(key, name, description) for key, name, description in areas),
                (),
                fallback=reason,
                pages=(number,),
            )
            for number, (areas, reason) in enumerate(zip(levels, reasons, strict=True), start=1)
        ),
    )
    return MapSpec(
        name="greyrock-country",
        title="Greyrock country",
        features=(
            "refuse_hex",
            "refuse_isometric",
            "refuse_side_view",
            "refuse_outdoor",
            "refuse_town",
            "refuse_quarter_mile",
        ),
        dungeon=dungeon,
        regions=(),
        panels=(),
        sheets=sheets,
    )


def lost_maps() -> MapSpec:
    """Two maps registration must reject: one drawn with no grid, and one whose grid is warped."""
    warped = Sketch(24, 16)
    warped.run("a", (0, 8), (4, 8)).edge((0, 8), "west", "o").edge((4, 8), "east", "d")
    warped.room("r1", 5, 4, 8, 8).mark("6", (8, 7))
    warped.room("r2", 14, 3, 7, 5).edge((13, 5), "east", "d").mark("7", (17, 5))
    warped.room("r3", 14, 9, 7, 5).edge((13, 10), "east", "d").mark("8", (17, 11))
    warped.outline((0, 0, 24, 16))
    warped_plan = warped.build()
    sheets = (
        SheetSpec(
            "sketch",
            1,
            "lost-sketch.png",
            "no_grid",
            "no_grid",
            "THE LOST CRYPT, A SKETCH",
            verdict="unregistered",
            reason="the map has no grid",
            scale=Scale(feet=10, printed=False),
            legend=NO_LEGEND,
        ),
        SheetSpec(
            "warped",
            2,
            "lost-warped.png",
            "black_white",
            "warped",
            "THE LOST CRYPT, BELOW",
            verdict="unregistered",
            reason="the map's grid is warped",
            columns=warped_plan.width,
            rows=warped_plan.height,
            legend=NO_LEGEND,
            plan=warped_plan,
        ),
    )
    first = (
        AreaSpec("1", "Antechamber", "The first room of the crypt."),
        AreaSpec("2", "Ossuary", "Bones stacked to the ceiling."),
        AreaSpec("3", "Chapel", "A chapel east of the ossuary."),
        AreaSpec("4", "Embalming room", "Tables and dried herbs."),
        AreaSpec("5", "Bell room", "A cracked bell hangs here."),
    )
    second = (
        AreaSpec("6", "Lower vault", "A vault below the ossuary."),
        AreaSpec("7", "Tomb", "A sealed tomb."),
        AreaSpec("8", "Charnel room", "Bones heaped against the walls."),
    )
    dungeon = DungeonSpec(
        "the-lost-crypt",
        "The lost crypt",
        (
            LevelSpec(1, first, (), fallback="its map has no grid to register", pages=(1,)),
            LevelSpec(2, second, (), fallback="its map's grid is warped past registering", pages=(2,)),
        ),
    )
    return MapSpec(
        name="lost-maps",
        title="The lost maps",
        features=("reject_no_grid", "reject_warped"),
        dungeon=dungeon,
        regions=(),
        panels=(),
        sheets=sheets,
    )


STYLE_MAPS = (sunken_temple, wyrm_caves, sideways_keep, cellars, scattered_pages, greyrock_country, lost_maps)
"""The maps for the other styles and the page-level features."""
