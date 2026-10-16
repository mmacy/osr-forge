"""The synthetic maps this generator draws: the TSR blue and black-and-white maps and the features they show.

Each function returns one map's spec. The answers aren't here: they're worked
out from each map's reading by [`resolve_dungeon`][osrforge.tracing.synthetic._resolve.resolve_dungeon].
Coordinates in a sketch are cells of its own grid, `(x, y)` from the north-west,
in 5-foot squares for a 5-foot region. A damage site's level cell is on the
level's grid, which adds a margin of one cell where a level's floor reaches
the frame.
"""

from collections.abc import Callable

from osrforge.contracts.stages import AreaConnection
from osrforge.tracing.synthetic import DamageSite, Feature
from osrforge.tracing.synthetic._plan import Plan, Sketch
from osrforge.tracing.synthetic._spec import (
    AreaSpec,
    DungeonSpec,
    LevelSpec,
    MapSpec,
    OutlineSpec,
    PanelSpec,
    RegionSpec,
)
from osrforge.tracing.synthetic._style_maps import STYLE_MAPS
from osrforge.tracing.truth import Compass, HalfPair, Scale, Style, SymbolKind


def _single(
    name: str, title: str, plan: Plan, style: Style, legend: tuple[SymbolKind, ...] = ()
) -> tuple[RegionSpec, PanelSpec]:
    extension = "jpg" if style == "tsr_blue" else "png"
    region = RegionSpec(
        id=name,
        page=1,
        image=f"{name}.{extension}",
        style=style,
        plan=plan,
        title=title.upper(),
        legend=legend,
    )
    return region, PanelSpec(id=name, regions=((name, (0, 0)),), plan=plan)


def crypt() -> MapSpec:
    """A crypt: an open hall with three keys, pillars, and steps, a jail row, a dashed circle, and a partition."""
    sketch = Sketch(24, 19)
    sketch.run("a", (0, 10), (3, 10)).edge((0, 10), "west", "o").edge((3, 10), "east", "d")
    sketch.room("closet", 2, 9, 1, 1).edge((2, 9), "south", "d").mark("5", (2, 9))
    sketch.room("hall", 4, 6, 10, 9).mark("1", (6, 8)).mark("2", (6, 12)).mark("3", (11, 10))
    for cell in ((8, 8), (9, 8), (8, 12), (9, 12)):
        sketch.symbol("pillar", cell)
    sketch.symbol("steps", (12, 9), (12, 10), (12, 11), run="ew")
    sketch.run("j", (8, 5), (8, 3), (0, 3)).edge((8, 5), "south", "d").edge((0, 3), "west", "o")
    for x in range(4, 8):
        sketch.room(f"cell{x}", x, 2, 1, 1).edge((x, 2), "south", "d")
    sketch.mark("4", (4, 2))
    sketch.room("circle", 14, 6, 6, 6).edge((13, 7), "east", "d").mark("6", (15, 7))
    rim = [((16, 8), "north"), ((17, 8), "north"), ((16, 9), "south"), ((17, 9), "south")]
    rim += [((16, 8), "west"), ((16, 9), "west"), ((17, 8), "east"), ((17, 9), "east")]
    sketch.edges("p", *rim, style="hidden").ornament("dashed_circle", (17.0, 9.0), size=1.12)
    sketch.room("shrine", 20, 6, 4, 4).edge((19, 7), "east", "d").edge((19, 9), "east", "s").mark("9", (22, 7))
    sketch.room("split", 14, 13, 8, 5).edge((13, 14), "east", "d")
    sketch.edges("p", *[((17, y), "east") for y in range(13, 18)], style="dashdot")
    sketch.mark("7", (15, 15)).mark("8", (20, 15))
    sketch.outline((0, 0, 24, 19))
    plan = sketch.build()
    region, panel = _single("crypt", "The crypt of the grey abbot", plan, "tsr_blue", ("pillar", "steps"))
    areas = (
        AreaSpec(
            "1", "North end of the great hall", "Four pillars hold up a vaulted roof over the north end of the hall."
        ),
        AreaSpec("2", "South end of the great hall", "Bones lie in heaps between the southern pillars."),
        AreaSpec("3", "Dais", "Three steps climb to a stone dais against the east wall."),
        AreaSpec("4", "Cells", "A row of four cells with iron doors opens off the passage."),
        AreaSpec("5", "Closet", "A narrow closet holds rotten brooms."),
        AreaSpec("6", "Summoning room", "A circle of dashes is painted on the floor around a black stain."),
        AreaSpec("7", "West vestry", "Robes hang on pegs along the west wall."),
        AreaSpec("8", "East vestry", "A broken censer lies on the floor."),
        AreaSpec("9", "Shrine", "An altar of grey stone fills the shrine."),
    )
    dungeon = DungeonSpec("crypt", "The crypt of the grey abbot", (LevelSpec(1, areas, ("crypt",)),))
    return MapSpec(
        name="crypt",
        title="The crypt of the grey abbot",
        features=(
            "open_hall_several_keys",
            "labelled_one_cell_hall",
            "dashed_circle_in_room",
            "jail_cells_row_one_key",
            "dash_dot_partition",
            "pillars",
            "steps_inside_level",
            "corridor_off_frame",
        ),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
        damage=(
            DamageSite(change="wall_removed", panel="crypt", edge="5,2:east"),
            DamageSite(change="door_kind_changed", panel="crypt", edge="13,7:east"),
            DamageSite(change="secret_door_removed", panel="crypt", edge="19,9:east"),
            DamageSite(change="key_moved", dungeon="crypt", level=1, label="9", to_cell="2,10"),
        ),
    )


def warrens() -> MapSpec:
    """Warrens whose keys are printed off the floor, twice, stacked, with a leader line, and as letters."""
    sketch = Sketch(26, 17)
    sketch.room("r1", 8, 7, 7, 5).mark("1", (11, 9))
    sketch.run("a", (11, 12), (11, 16)).edge((11, 11), "south", "d").edge((11, 16), "south", "o")
    sketch.room("r2", 3, 7, 5, 5).edge((7, 9), "east", "d").mark("2", (5, 9))
    sketch.room("alcove", 1, 9, 2, 1).edge((2, 9), "east", "d").mark("2a", (1, 9))
    sketch.room("t1", 8, 5, 2, 2).edge((8, 6), "south", "d").mark("4", (8, 5))
    sketch.room("t2", 13, 5, 2, 2).edge((14, 6), "south", "d").mark("4", (14, 5))
    sketch.room("r3", 15, 8, 2, 2).edge((14, 9), "east", "d").mark("3", (17, 7))
    sketch.run("b", (15, 10), (19, 10), (19, 6)).edge((14, 10), "east", "d")
    sketch.room("r5", 18, 2, 5, 4).edge((19, 6), "north", "d").mark("B/5", (20, 3))
    sketch.room("r6", 23, 3, 1, 2).edge((22, 3), "east", "d").mark("6", (23, 1), leader_to=(23, 3))
    sketch.room("r7", 3, 3, 5, 4).edge((5, 6), "south", "d").mark("7", (4, 4)).mark("c", (6, 5))
    sketch.room("r8", 8, 1, 5, 3).edge((7, 3), "east", "d").mark("8", (9, 2)).mark("e", (11, 2))
    sketch.room("r9", 13, 12, 6, 4).edge((13, 11), "south", "d").mark("9", (16, 13))
    sketch.outline((0, 0, 26, 17))
    plan = sketch.build()
    region, panel = _single("warrens", "The warrens", plan, "black_white")
    areas = (
        AreaSpec("1", "Meeting hall", "Benches face a low stage in the middle of the hall."),
        AreaSpec("2", "Store", "Sacks of grain fill the store, and a small alcove holds a barrel of oil."),
        AreaSpec("3", "Den", "A nest of rags fills the den."),
        AreaSpec("4", "Twin guardrooms", "Two small guardrooms each hold a cot and a spear rack."),
        AreaSpec("5", "Kitchen", "A cold hearth and a long table."),
        AreaSpec("6", "Pantry", "Shelves of empty jars."),
        AreaSpec("7", "Chapel", 'A cracked altar marked "c" stands in the corner.'),
        AreaSpec("8", "Treasury", 'An iron chest (marked "e") sits under a high slit in the wall.'),
        AreaSpec("9", "Workshop", 'A lever on the wall opens the chest marked "e" in another room.'),
    )
    dungeon = DungeonSpec("the-warrens", "The warrens", (LevelSpec(1, areas, ("warrens",)),))
    return MapSpec(
        name="warrens",
        title="The warrens",
        features=(
            "key_off_floor",
            "key_printed_twice",
            "key_with_leader_line",
            "letter_alone",
            "letter_under_two_keys",
            "letter_suffix",
            "corridor_off_frame",
        ),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
        damage=(
            DamageSite(change="key_moved", dungeon="the-warrens", level=1, label="7", to_cell="5,8"),
            DamageSite(change="wall_removed", panel="warrens", edge="4,6:south"),
            DamageSite(change="door_kind_changed", panel="warrens", edge="22,3:east"),
        ),
    )


def ravine_lair() -> MapSpec:
    """A lair with an open and a walled cave mouth onto a ravine, keys printed outside both, and a keyed pool."""
    sketch = Sketch(26, 18)
    sketch.room("ravine", 2, 12, 22, 4)
    sketch.room("a", 4, 6, 6, 5).run("a", (6, 11)).edge((6, 11), "south", "o")
    sketch.symbol("cave_mouth", edge=((6, 11), "south"))
    sketch.room("b", 15, 6, 6, 5).run("b", (17, 11))
    sketch.symbol("cave_mouth", edge=((17, 11), "south"))
    sketch.room("c", 9, 1, 8, 4).water(12, 3, 3, 2).mark("4", (10, 2)).mark("3", (13, 3))
    sketch.run("k", (5, 5), (5, 3), (8, 3)).edge((5, 5), "south", "d").edge((8, 3), "east", "d")
    sketch.run("m", (17, 3), (19, 3), (19, 5)).edge((16, 3), "east", "d").edge((19, 5), "south", "d")
    sketch.mark("1", (6, 13)).mark("2", (17, 13)).mark("K", (12, 14))
    sketch.outline((0, 0, 26, 12))
    sketch.ornament("track", (2.2, 14.6), (4.0, 13.6), (5.6, 13.2), (6.5, 12.3))
    sketch.ornament("stream", (2.1, 15.5), (8.0, 15.2), (14.0, 15.6), (19.0, 15.3), (23.9, 15.5))
    sketch.ornament("contour", (8.2, 12.2), (10.0, 13.0), (13.0, 12.8), (15.6, 12.2))
    sketch.ornament("contour", (19.0, 13.8), (21.0, 13.0), (23.8, 12.6))
    plan = sketch.build()
    region, panel = _single("ravine-lair", "Lair K, the ravine caves", plan, "tsr_blue", ("cave_mouth",))
    areas = (
        AreaSpec("1", "Mouth cave", "The open mouth of the lair looks out over the ravine.", "cave"),
        AreaSpec("2", "Walled cave", "A dry stone wall closes this cave from the ravine.", "cave"),
        AreaSpec("3", "Pool", "Black water fills a pool in the floor.", "cave"),
        AreaSpec("4", "Inner cave", "Bedding lies along the walls around the pool.", "cave"),
    )
    dungeon = DungeonSpec("lair-k", "Lair K", (LevelSpec(1, areas, ("ravine-lair",)),))
    return MapSpec(
        name="ravine-lair",
        title="Lair K, the ravine caves",
        features=(
            "keys_on_water",
            "key_outside_open_cave_mouth",
            "key_outside_walled_cave_mouth",
            "level_with_two_cave_mouths",
            "letter_lair_label_outside",
            "overlay_contours_tracks_stream",
        ),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
        damage=(
            DamageSite(change="key_moved", dungeon="lair-k", level=1, label="4", to_cell="5,4"),
            DamageSite(change="door_kind_changed", panel="ravine-lair", edge="5,5:south"),
        ),
    )


def cave_entrance() -> MapSpec:
    """A cave whose entrance chamber lies inside a cave mouth, under a key the map doesn't print."""
    sketch = Sketch(22, 13)
    sketch.room("hillside", 0, 0, 3, 13)
    for y in range(13):
        sketch.edge((0, y), "west", "o")
    for x in range(3):
        sketch.edge((x, 0), "north", "o").edge((x, 12), "south", "o")
    sketch.room("chamber", 4, 5, 4, 3).run("chamber", (3, 6)).edge((3, 6), "west", "o")
    sketch.symbol("cave_mouth", edge=((3, 6), "west"))
    sketch.run("p", (8, 6), (11, 6)).edge((7, 6), "east", "d").edge((11, 6), "east", "d")
    sketch.room("r2", 12, 4, 5, 5).mark("2", (14, 6))
    sketch.room("r3", 17, 4, 4, 5).edge((16, 6), "east", "l").mark("3", (19, 6))
    sketch.room("r4", 17, 9, 4, 3).edge((19, 8), "south", "s").mark("4", (18, 10))
    sketch.outline((3, 0, 19, 13))
    sketch.ornament("track", (0.1, 9.6), (1.2, 8.4), (2.2, 6.6), (2.9, 6.5))
    sketch.ornament("contour", (0.2, 2.6), (1.4, 3.4), (2.8, 3.0))
    plan = sketch.build()
    region, panel = _single("cave-entrance", "The hollow hill", plan, "tsr_blue", ("cave_mouth", "locked_door"))
    areas = (
        AreaSpec(
            "1",
            "Entrance",
            "Daylight reaches a few paces into the cave, where a door opens on a passage to the hall.",
            "cave",
            (AreaConnection(to_key="2", direction="east", via="door"),),
        ),
        AreaSpec("2", "Hall", "A rough hall with a locked door in its east wall."),
        AreaSpec("3", "Vault", "Empty racks line the vault."),
        AreaSpec("4", "Hidden room", "Dust lies thick on a stone chest."),
    )
    dungeon = DungeonSpec("the-hollow-hill", "The hollow hill", (LevelSpec(1, areas, ("cave-entrance",)),))
    return MapSpec(
        name="cave-entrance",
        title="The hollow hill",
        features=("entrance_chamber_in_cave_mouth_unprinted_key",),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
        damage=(DamageSite(change="key_moved", dungeon="the-hollow-hill", level=1, label="3", to_cell="13,5"),),
    )


def old_stair() -> MapSpec:
    """Two levels on two pages: a wide stairway as the only way in, and two staircases serving one link."""
    upper = Sketch(20, 14)
    upper.room("h", 8, 0, 3, 2).room("h", 5, 2, 9, 5).mark("1", (9, 4))
    upper.edges("o", ((8, 0), "north"), ((9, 0), "north"), ((10, 0), "north"))
    upper.symbol("stairs", (8, 0), (9, 0), (10, 0), (8, 1), (9, 1), (10, 1), direction="up", run="ns")
    upper.run("c", (9, 7), (9, 9)).edge((9, 6), "south", "d").edge((9, 9), "south", "d")
    upper.room("r2", 6, 10, 7, 3).mark("2", (9, 11))
    upper.symbol("stairs", (6, 11), (6, 12), direction="down", run="ns")
    upper.symbol("stairs", (12, 11), (12, 12), direction="down", run="ns")
    upper.outline((0, 0, 20, 14))
    lower = Sketch(16, 10)
    lower.room("r3", 2, 2, 9, 5).water(5, 5, 3, 2).mark("3", (6, 3))
    lower.symbol("stairs", (2, 3), (2, 4), run="ns")
    lower.symbol("stairs", (10, 3), (10, 4), run="ns")
    lower.room("r4", 11, 2, 3, 5).edge((10, 5), "east", "d").mark("4", (12, 4))
    lower.outline((0, 0, 16, 10))
    upper_plan, lower_plan = upper.build(), lower.build()
    regions = (
        RegionSpec("upper", 1, "old-stair-1.jpg", "tsr_blue", upper_plan, "THE OLD STAIR, LEVEL 1", legend=("stairs",)),
        RegionSpec("lower", 2, "old-stair-2.jpg", "tsr_blue", lower_plan, "THE OLD STAIR, LEVEL 2", legend=("stairs",)),
    )
    panels = (
        PanelSpec("upper", (("upper", (0, 0)),), upper_plan),
        PanelSpec("lower", (("lower", (0, 0)),), lower_plan),
    )
    first = (
        AreaSpec("1", "Stair hall", "A broad stair comes down from the hillside into this hall."),
        AreaSpec(
            "2",
            "Landing",
            "Two flights of stairs lead down from the landing to the lower hall.",
            links=(AreaConnection(to_key="3", direction="down", via="stairs"),),
        ),
    )
    second = (
        AreaSpec(
            "3",
            "Lower hall",
            "The two flights from the landing arrive at either end of this hall, above a dark pool.",
            links=(AreaConnection(to_key="2", direction="up", via="stairs"),),
        ),
        AreaSpec("4", "Side room", "A heap of broken crates."),
    )
    dungeon = DungeonSpec(
        "the-old-stair", "The old stair", (LevelSpec(1, first, ("upper",)), LevelSpec(2, second, ("lower",)))
    )
    return MapSpec(
        name="old-stair",
        title="The old stair",
        features=(
            "stairs_with_direction",
            "stairs_without_direction",
            "two_staircases_one_link",
            "wide_open_stairway_only_way_in",
        ),
        dungeon=dungeon,
        regions=regions,
        panels=panels,
        damage=(
            DamageSite(change="transition_moved", dungeon="the-old-stair", level=1, cell="12,12", to_cell="9,12"),
            DamageSite(change="refusal_flipped", dungeon="the-old-stair", level=2),
        ),
    )


def farmstead() -> MapSpec:
    """A farmstead of buildings on open ground, with a doorless tree house and a moated house with a bridge."""
    sketch = Sketch(28, 22)
    sketch.room("yard", 0, 0, 28, 22)
    sketch.room("house", 3, 3, 6, 5).edge((5, 7), "south", "d").mark("1", (5, 5))
    sketch.edges("n", ((3, 4), "west"), ((3, 5), "west"), ((6, 3), "north"))
    sketch.room("barn", 12, 2, 6, 5).edge((14, 6), "south", "d").edge((13, 3), "east", "w").mark("2", (14, 4))
    sketch.room("tree", 21, 3, 2, 2).mark("3", (21, 3)).ornament("tree", (22.0, 4.0), size=1.6)
    sketch.room("shed", 3, 12, 2, 2).edge((3, 12), "north", "d").mark("5", (3, 13))
    sketch.room("smokehouse", 7, 12, 2, 2).edge((8, 12), "north", "d").mark("5/6", (5, 12))
    sketch.water(16, 11, 8, 8).room("island", 17, 12, 6, 6).room("moated", 18, 13, 4, 4)
    sketch.edge((19, 16), "south", "d").edge((20, 13), "north", "d").mark("4", (20, 13), edge="north")
    sketch.symbol("bridge", (19, 18), run="ns")
    for x in range(28):
        sketch.edge((x, 0), "north", "o").edge((x, 21), "south", "o")
    for y in range(22):
        sketch.edge((0, y), "west", "o").edge((27, y), "east", "o")
    sketch.outline((0, 0, 28, 22)).ground((0, 0, 28, 22), (17, 12, 6, 6))
    sketch.ornament("stream", (0.1, 9.4), (6.0, 10.2), (11.0, 9.6), (17.0, 9.9), (23.0, 9.3), (27.9, 9.7))
    sketch.ornament("track", (19.5, 21.9), (19.5, 20.6), (19.5, 19.1))
    sketch.ornament("track", (5.5, 8.1), (6.0, 10.6), (10.0, 11.0), (14.5, 9.5), (14.5, 7.1))
    sketch.ornament("contour", (0.2, 16.0), (4.0, 17.2), (8.0, 16.4), (13.6, 18.0))
    sketch.ornament("contour", (0.2, 19.2), (5.0, 20.4), (10.0, 19.6), (14.6, 21.8))
    plan = sketch.build()
    region, panel = _single("farmstead", "Hobb's farmstead", plan, "black_white", ("bridge",))
    areas = (
        AreaSpec("1", "Farmhouse", "A low farmhouse with shuttered windows."),
        AreaSpec("2", "Barn", "Stalls line the barn's west wall."),
        AreaSpec("3", "Tree house", "A platform with a roof sits in the branches of an old oak."),
        AreaSpec("4", "Moated house", "A narrow house stands on an island inside a moat."),
        AreaSpec("5", "Shed", "Tools hang on the walls of a small shed."),
        AreaSpec("6", "Smokehouse", "Hams hang from the rafters."),
    )
    dungeon = DungeonSpec("hobbs-farmstead", "Hobb's farmstead", (LevelSpec(1, areas, ("farmstead",)),))
    return MapSpec(
        name="farmstead",
        title="Hobb's farmstead",
        features=(
            "farmstead_open_ground",
            "tree_house_no_door",
            "keys_stacked",
            "moated_house_bridge",
            "key_at_rear_door",
            "windows",
            "overlay_contours_tracks_stream",
        ),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
        damage=(
            DamageSite(change="wall_removed", panel="farmstead", edge="13,3:east"),
            DamageSite(change="key_moved", dungeon="hobbs-farmstead", level=1, label="2", to_cell="10,12"),
        ),
    )


def manor() -> MapSpec:
    """A manor with a walled courtyard, and a gateway with no door between two towers."""
    sketch = Sketch(22, 18)
    sketch.room("hall", 7, 11, 8, 4).room("hall", 10, 15, 2, 3).mark("1", (10, 12))
    sketch.edges("o", ((10, 17), "south"), ((11, 17), "south"))
    sketch.room("tw", 7, 15, 3, 3).edge((8, 15), "north", "d").mark("2", (8, 16))
    sketch.room("te", 12, 15, 3, 3).edge((13, 15), "north", "d").mark("3", (13, 16))
    sketch.room("n4", 7, 2, 8, 3).edge((10, 4), "south", "d").mark("4", (10, 3))
    sketch.edges("n", ((9, 2), "north"), ((12, 2), "north"))
    sketch.room("w5", 5, 5, 4, 4).edge((8, 6), "east", "d").edge((8, 8), "east", "n").mark("5", (6, 6))
    sketch.room("e6", 13, 5, 4, 4).edge((13, 7), "west", "d").edge((13, 5), "west", "n").mark("6", (15, 6))
    sketch.room("court", 9, 5, 4, 4).ground((9, 5, 4, 4))
    sketch.room("s7", 9, 9, 4, 2).edge((10, 9), "north", "d").edge((11, 10), "south", "d").mark("7", (11, 9))
    sketch.outline((0, 0, 22, 18))
    plan = sketch.build()
    region, panel = _single("manor", "Greywall manor", plan, "black_white")
    areas = (
        AreaSpec("1", "Gate hall", "The gateway between the towers opens straight into the hall."),
        AreaSpec("2", "West tower", "A guard's bunk and a crossbow rack."),
        AreaSpec("3", "East tower", "A table with cards left mid-game."),
        AreaSpec("4", "Great chamber", "Tall windows look north over the hills."),
        AreaSpec("5", "Library", "A window looks into the courtyard."),
        AreaSpec("6", "Solar", "A window looks into the courtyard."),
        AreaSpec("7", "Passage room", "Doors lead to the courtyard and to the gate hall."),
    )
    dungeon = DungeonSpec("greywall-manor", "Greywall manor", (LevelSpec(1, areas, ("manor",)),))
    return MapSpec(
        name="manor",
        title="Greywall manor",
        features=("walled_courtyard", "gateway_no_door_between_towers", "windows"),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
        damage=(
            DamageSite(change="door_kind_changed", panel="manor", edge="13,7:west"),
            DamageSite(change="key_moved", dungeon="greywall-manor", level=1, label="6", to_cell="10,6"),
        ),
    )


def _fold_plan() -> Plan:
    sketch = Sketch(20, 11)
    sketch.run("a", (0, 5), (2, 5)).edge((0, 5), "west", "o").edge((2, 5), "east", "d")
    sketch.room("r1", 3, 3, 5, 5).mark("1", (5, 5))
    sketch.run("b", (8, 5), (11, 5)).edge((7, 5), "east", "d").edge((11, 5), "east", "d")
    sketch.room("r2", 12, 3, 5, 5).mark("2", (14, 5))
    sketch.outline((0, 0, 20, 11))
    return sketch.build()


def _fold_regions(plan: Plan) -> tuple[RegionSpec, RegionSpec]:
    return (
        RegionSpec("west", 1, "fold-west.jpg", "tsr_blue", plan.columns(0, 10), "THE FOLD, WEST", ("right",)),
        RegionSpec("east", 2, "fold-east.jpg", "tsr_blue", plan.columns(10, 20), "THE FOLD, EAST", ("left",)),
    )


_FOLD_AREAS = (
    AreaSpec("1", "West room", "A corridor leads east from the door."),
    AreaSpec("2", "East room", "A corridor leads west from the door."),
)


def fold_joined() -> MapSpec:
    """A map printed in two halves that the answer joins with no columns between them."""
    plan = _fold_plan()
    west, east = _fold_regions(plan)
    panel = PanelSpec("west+east", (("west", (0, 0)), ("east", (10, 0))), plan)
    dungeon = DungeonSpec("the-fold-joined", "The fold", (LevelSpec(1, _FOLD_AREAS, ("west+east",)),))
    return MapSpec(
        name="fold-joined",
        title="The fold, joined",
        features=("corridor_off_frame",),
        dungeon=dungeon,
        regions=(west, east),
        panels=(panel,),
        half_pairs=(HalfPair(regions=("west", "east"), joined=True, rows=0, columns=0),),
        damage=(DamageSite(change="halves_offset_changed", pair=0),),
    )


def fold_apart() -> MapSpec:
    """The same two halves left apart, linked by a pair of passages."""
    plan = _fold_plan()
    west, east = _fold_regions(plan)
    panels = (PanelSpec("west", (("west", (0, 0)),), west.plan), PanelSpec("east", (("east", (0, 0)),), east.plan))
    dungeon = DungeonSpec("the-fold-apart", "The fold", (LevelSpec(1, _FOLD_AREAS, ("west", "east")),))
    return MapSpec(
        name="fold-apart",
        title="The fold, apart",
        features=("corridor_off_frame",),
        dungeon=dungeon,
        regions=(west, east),
        panels=panels,
        half_pairs=(HalfPair(regions=("west", "east"), joined=False, rows=0),),
        damage=(
            DamageSite(
                change="passage_moved",
                dungeon="the-fold-apart",
                level=1,
                cell="10,5",
                side="east",
                to_cell="9,5",
                to_side="north",
            ),
        ),
    )


def _ways(*links: tuple[str, str, str]) -> tuple[AreaConnection, ...]:
    """Prose connections, each as the target key, the compass direction, and the means."""
    return tuple(AreaConnection(to_key=key, direction=direction, via=via) for key, direction, via in links)  # type: ignore[arg-type]


def undercroft() -> MapSpec:
    """Two levels on one page whose floors meet at doors and along a corridor.

    They meet at a door between two corridors, at a door between two rooms,
    and where the cut crosses a corridor at its bend.
    """
    sketch = Sketch(26, 19)
    sketch.run("in", (0, 8), (2, 8)).edge((0, 8), "west", "o").edge((2, 8), "east", "d")
    sketch.room("r1", 3, 5, 6, 7).mark("1", (5, 8))
    sketch.room("r5", 2, 1, 6, 4).edge((5, 4), "south", "d").mark("2", (4, 2))
    sketch.run("c", (9, 6), (18, 6)).edge((8, 6), "east", "d").edge((13, 6), "east", "d").edge((18, 6), "east", "d")
    sketch.room("r2", 19, 3, 6, 6).mark("5", (21, 5))
    sketch.room("r3", 3, 12, 6, 4).edge((6, 11), "south", "d").mark("6", (4, 14))
    sketch.run("b", (9, 10), (13, 10), (13, 13)).edge((8, 10), "east", "d").edge((13, 13), "south", "d")
    sketch.room("r4", 11, 14, 6, 4).mark("7", (15, 17))
    plan = sketch.build()
    region = RegionSpec("undercroft", 1, "undercroft.jpg", "tsr_blue", plan, "THE UNDERCROFT, LEVELS 1 AND 2")
    panel = PanelSpec(
        "undercroft",
        (("undercroft", (0, 0)),),
        plan,
        outlines=(
            OutlineSpec(1, ((0, 0, 26, 19),), ((5, 7),)),
            OutlineSpec(2, ((0, 0, 26, 19),), ((22, 6),)),
        ),
    )
    upper = (
        AreaSpec(
            "1",
            "Pillared crypt",
            "Doors lead east toward the lower vaults and south into the bone store.",
            links=_ways(("5", "east", "door"), ("6", "south", "door"), ("7", "east", "passage")),
        ),
        AreaSpec("2", "Sexton's room", "A cot and a lantern hook."),
    )
    lower = (
        AreaSpec(
            "5",
            "Lower vault",
            "A corridor with a door halfway along leads west to the crypt.",
            links=_ways(("1", "west", "door")),
        ),
        AreaSpec("6", "Bone store", "Bones are stacked to the ceiling.", links=_ways(("1", "north", "door"))),
        AreaSpec(
            "7", "Well room", "A passage climbs north and west to the crypt.", links=_ways(("1", "north", "passage"))
        ),
    )
    dungeon = DungeonSpec(
        "the-undercroft",
        "The undercroft",
        (LevelSpec(1, upper, ("undercroft",)), LevelSpec(2, lower, ("undercroft",))),
    )
    return MapSpec(
        name="undercroft",
        title="The undercroft",
        features=(
            "several_levels_one_page",
            "two_levels_joined_by_door",
            "two_levels_joined_by_open_floor",
            "levels_meet_at_door",
            "corridor_bends_across_cut",
            "passage_at_floor_edge",
            "corridor_off_frame",
        ),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
    )


def sunken_vaults() -> MapSpec:
    """Two levels on one page that meet at a locked secret door and at a door where no vestibule fits.

    The prose locks the secret door. The other door opens on a one-cell
    closet from a cell with a second door, so neither side can be its
    vestibule.
    """
    sketch = Sketch(22, 16)
    sketch.run("in", (0, 7), (2, 7)).edge((0, 7), "west", "o").edge((2, 7), "east", "d")
    sketch.room("r1", 3, 4, 7, 7).mark("1", (5, 6))
    sketch.room("r3", 10, 5, 6, 5).edge((9, 7), "east", "s").mark("3", (13, 6))
    sketch.room("r4", 10, 10, 1, 1).edge((9, 10), "east", "d").mark("4", (10, 10))
    sketch.room("r2", 6, 11, 4, 3).edge((9, 10), "south", "d").mark("2", (7, 12))
    plan = sketch.build()
    region = RegionSpec(
        "sunken-vaults", 1, "sunken-vaults.png", "black_white", plan, "THE SUNKEN VAULTS, LEVELS 1 AND 2"
    )
    panel = PanelSpec(
        "sunken-vaults",
        (("sunken-vaults", (0, 0)),),
        plan,
        outlines=(
            OutlineSpec(1, ((0, 0, 22, 16),), ((4, 8),)),
            OutlineSpec(2, ((0, 0, 22, 16),), ((14, 8),)),
        ),
    )
    locked = AreaConnection(to_key="3", direction="east", via="secret_door", door_locked=True)
    upper = (
        AreaSpec(
            "1",
            "Hall of urns",
            "A locked secret door in the east wall opens on the vault. A door in the south-east corner opens on a "
            "closet, and another on the guard post.",
            links=(locked, *_ways(("4", "east", "door"))),
        ),
        AreaSpec("2", "Guard post", "A stool and a rusted bell."),
    )
    lower = (
        AreaSpec(
            "3",
            "Drowned vault",
            "Water stands ankle deep. A locked secret door leads west to the hall of urns.",
            links=(AreaConnection(to_key="1", direction="west", via="secret_door", door_locked=True),),
        ),
        AreaSpec("4", "Closet", "Rotten sacks fill a narrow closet.", links=_ways(("1", "west", "door"))),
    )
    dungeon = DungeonSpec(
        "the-sunken-vaults",
        "The sunken vaults",
        (LevelSpec(1, upper, ("sunken-vaults",)), LevelSpec(2, lower, ("sunken-vaults",))),
    )
    return MapSpec(
        name="sunken-vaults",
        title="The sunken vaults",
        features=("several_levels_one_page", "levels_meet_at_locked_secret_door", "door_no_vestibule_fits"),
        dungeon=dungeon,
        regions=(region,),
        panels=(panel,),
    )


_GATE_FOLD = 10
"""The column of the gatehouse that the fold cuts through, which neither half registers."""


def _gate_plan() -> Plan:
    sketch = Sketch(21, 18)
    sketch.room("hall", 4, 1, 13, 3).mark("1", (5, 2))
    sketch.room("north", 7, 4, 7, 3).edge((12, 3), "south", "d").mark("2", (12, 5))
    sketch.run("in", (0, 5), (6, 5)).edge((0, 5), "west", "o").edge((6, 5), "east", "d")
    sketch.room("south", 7, 7, 7, 3).edge((9, 6), "south", "d").mark("3", (8, 8))
    sketch.room("west", 4, 11, 6, 3).run("west", (10, 12), (11, 12)).mark("4", (6, 12))
    sketch.run("dead", (5, 14), (5, 16), (10, 16)).edge((5, 13), "south", "d")
    sketch.room("east", 12, 10, 6, 5).edge((11, 12), "east", "d").edge((12, 9), "south", "d").mark("5", (15, 12))
    sketch.outline((0, 0, 21, 18))
    return sketch.build()


def _gate_regions(plan: Plan) -> tuple[RegionSpec, RegionSpec]:
    """The two halves: the west prints the legend and the compass, the east prints neither."""
    west = RegionSpec(
        "gate-west",
        1,
        "gatehouse-west.jpg",
        "tsr_blue",
        plan.columns(0, _GATE_FOLD),
        "THE GATEHOUSE, WEST",
        ("right",),
        fold=plan.columns(0, _GATE_FOLD + 1),
        fold_shown=0.3,
    )
    east = RegionSpec(
        "gate-east",
        2,
        "gatehouse-east.jpg",
        "tsr_blue",
        plan.columns(_GATE_FOLD + 1, plan.width),
        "THE GATEHOUSE, EAST",
        ("left",),
        compass=Compass(north="top", printed=False),
        legend_from=1,
        fold=plan.columns(_GATE_FOLD, plan.width),
        fold_shown=0.3,
    )
    return west, east


_GATE_AREAS = (
    AreaSpec("1", "Gate hall", "The hall runs the width of the gatehouse."),
    AreaSpec("2", "Porter's room", "A door in the south wall leads to the guardroom."),
    AreaSpec("3", "Guardroom", "Racks of pikes line the walls."),
    AreaSpec("4", "Stable", "Stalls open on a short passage to the east."),
    AreaSpec("5", "Tack room", "Saddles hang from pegs."),
)

_GATE_FEATURES: tuple[Feature, ...] = ("halves_gap_at_fold", "halves_legend_compass_one_half", "corridor_off_frame")


def gatehouse_joined() -> MapSpec:
    """A map printed in two halves whose fold cuts through a column, joined with that column between them."""
    plan = _gate_plan()
    west, east = _gate_regions(plan)
    panel = PanelSpec("gate-west+gate-east", (("gate-west", (0, 0)), ("gate-east", (_GATE_FOLD + 1, 0))), plan)
    dungeon = DungeonSpec("the-gatehouse-joined", "The gatehouse", (LevelSpec(1, _GATE_AREAS, (panel.id,)),))
    return MapSpec(
        name="gatehouse-joined",
        title="The gatehouse, joined",
        features=("halves_joined_at_offset", *_GATE_FEATURES),
        dungeon=dungeon,
        regions=(west, east),
        panels=(panel,),
        half_pairs=(HalfPair(regions=("gate-west", "gate-east"), joined=True, rows=0, columns=1),),
    )


def gatehouse_apart() -> MapSpec:
    """The same two halves left apart: the column the fold cuts is lost, and passages link the fold columns."""
    plan = _gate_plan()
    west, east = _gate_regions(plan)
    panels = (
        PanelSpec("gate-west", (("gate-west", (0, 0)),), west.plan),
        PanelSpec("gate-east", (("gate-east", (0, 0)),), east.plan),
    )
    dungeon = DungeonSpec(
        "the-gatehouse-apart", "The gatehouse", (LevelSpec(1, _GATE_AREAS, ("gate-west", "gate-east")),)
    )
    return MapSpec(
        name="gatehouse-apart",
        title="The gatehouse, apart",
        features=(
            "halves_apart_unequal_stretches",
            "halves_apart_door_ends_stretch",
            "halves_apart_stretch_without_partner",
            *_GATE_FEATURES,
        ),
        dungeon=dungeon,
        regions=(west, east),
        panels=panels,
        half_pairs=(HalfPair(regions=("gate-west", "gate-east"), joined=False, rows=0),),
    )


def watchtower() -> MapSpec:
    """One level drawn as two panels at different scales on one page.

    The tower is drawn at 10 feet and its cellar beside it at 5 feet. A
    trapdoor drops into the cellar and a ladder climbs back up.
    """
    tower = Sketch(15, 12)
    tower.run("in", (0, 4), (1, 4)).edge((0, 4), "west", "o").edge((1, 4), "east", "d")
    tower.room("guard", 2, 2, 6, 5).mark("1", (5, 4)).symbol("ladder", (3, 2), run="ns")
    tower.room("store", 8, 2, 5, 5).edge((7, 4), "east", "d").mark("2", (10, 3)).symbol("trapdoor", (11, 5))
    tower.room("watch", 2, 7, 11, 3).edge((4, 6), "south", "d").mark("3", (8, 8))
    tower.outline((0, 0, 15, 12))
    cellar = Sketch(14, 12)
    cellar.room("deep", 2, 2, 6, 8).mark("4", (4, 5))
    cellar.room("well", 8, 2, 4, 4).edge((7, 3), "east", "d").mark("5", (8, 4)).symbol("ladder", (10, 2), (10, 3))
    cellar.water(10, 4, 2, 2)
    cellar.outline((0, 0, 14, 12))
    tower_plan, cellar_plan = tower.build(), cellar.build()
    legend: tuple[SymbolKind, ...] = ("ladder", "trapdoor")
    regions = (
        RegionSpec("tower", 1, "watchtower.png", "black_white", tower_plan, "THE WATCHTOWER", legend=legend),
        RegionSpec(
            "cellar",
            1,
            "watchtower.png",
            "black_white",
            cellar_plan,
            "THE CELLAR",
            legend=legend,
            scale=Scale(feet=5, printed=True, text="1 SQUARE = 5 FEET"),
            at=(float(tower_plan.width) + 2.0, 0.0),
        ),
    )
    panels = (
        PanelSpec("tower", (("tower", (0, 0)),), tower_plan),
        PanelSpec("cellar", (("cellar", (0, 0)),), cellar_plan, scale_feet=5),
    )
    areas = (
        AreaSpec(
            "1",
            "Guardroom",
            "A ladder in the north-west corner leads down to the well room in the cellar.",
            links=_ways(("5", "down", "stairs")),
        ),
        AreaSpec(
            "2",
            "Store",
            "A trapdoor in the floor drops into the deep cellar.",
            links=_ways(("4", "down", "trapdoor")),
        ),
        AreaSpec("3", "Watch room", "Arrow slits look out on the road."),
        AreaSpec("4", "Deep cellar", "Casks of sour wine."),
        AreaSpec(
            "5", "Well room", "A ladder beside the well climbs to the guardroom.", links=_ways(("1", "up", "stairs"))
        ),
    )
    dungeon = DungeonSpec("the-watchtower", "The watchtower", (LevelSpec(1, areas, ("tower", "cellar")),))
    return MapSpec(
        name="watchtower",
        title="The watchtower",
        features=("panels_two_scales_one_page", "panels_trapdoor", "corridor_off_frame"),
        dungeon=dungeon,
        regions=regions,
        panels=panels,
    )


def mill() -> MapSpec:
    """One level drawn as two panels on two pages, joined by a ladder and by a grain slide.

    The loft is on the first page and the ground floor on the second.
    """
    loft = Sketch(14, 9)
    loft.room("loft", 0, 2, 7, 5).edge((0, 4), "west", "o").mark("4", (3, 4)).symbol("ladder", (5, 5), run="ns")
    loft.room("hopper", 7, 2, 4, 3).edge((6, 3), "east", "d").mark("5", (8, 3)).symbol("slide", (9, 3), (10, 3))
    loft.outline((0, 0, 14, 9))
    ground = Sketch(16, 11)
    ground.run("in", (0, 4), (1, 4)).edge((0, 4), "west", "o").edge((1, 4), "east", "o")
    ground.room("mill", 2, 2, 7, 6).mark("1", (5, 4)).symbol("ladder", (7, 6), run="ns")
    ground.room("bins", 9, 2, 5, 4).edge((8, 3), "east", "d").mark("2", (11, 3))
    ground.room("office", 9, 6, 5, 3).edge((8, 7), "east", "d").mark("3", (11, 7))
    ground.outline((0, 0, 16, 11))
    loft_plan, ground_plan = loft.build(), ground.build()
    legend: tuple[SymbolKind, ...] = ("ladder", "slide")
    regions = (
        RegionSpec("loft", 1, "mill-1.jpg", "tsr_blue", loft_plan, "THE MILL, LOFT", legend=legend),
        RegionSpec("ground", 2, "mill-2.jpg", "tsr_blue", ground_plan, "THE MILL, GROUND FLOOR", legend=legend),
    )
    panels = (
        PanelSpec("loft", (("loft", (0, 0)),), loft_plan),
        PanelSpec("ground", (("ground", (0, 0)),), ground_plan),
    )
    areas = (
        AreaSpec(
            "1",
            "Mill room",
            "The millstones stand idle. A ladder climbs to the loft.",
            links=_ways(("4", "up", "stairs")),
        ),
        AreaSpec("2", "Grain bins", "The slide from the hopper empties into the bins."),
        AreaSpec("3", "Miller's office", "A ledger lies open on the desk."),
        AreaSpec(
            "4",
            "Loft",
            "A hoist door opens on empty air. A ladder leads down to the mill room.",
            links=_ways(("1", "down", "stairs")),
        ),
        AreaSpec(
            "5",
            "Hopper",
            "A grain slide drops to the bins below.",
            links=_ways(("2", "down", "chute")),
        ),
    )
    dungeon = DungeonSpec("the-mill", "The mill", (LevelSpec(1, areas, ("loft", "ground")),))
    return MapSpec(
        name="mill",
        title="The mill",
        features=("panels_two_pages_ladder", "panels_slide", "corridor_off_frame"),
        dungeon=dungeon,
        regions=regions,
        panels=panels,
    )


def shrine() -> MapSpec:
    """A level whose stair room leads down to a grotto drawn as an inset inside the map's frame."""
    sketch = Sketch(24, 16)
    sketch.room("nave", 2, 2, 8, 8).mark("1", (5, 5)).run("in", (5, 10), (5, 15)).edge((5, 15), "south", "o")
    sketch.room("vestry", 10, 2, 4, 4).edge((9, 3), "east", "d").mark("2", (11, 3))
    sketch.room("stair", 10, 7, 4, 3).edge((9, 8), "east", "d").mark("3", (11, 8)).symbol("stairs", (12, 8), (13, 8))
    sketch.outline((0, 0, 24, 16))
    grotto = Sketch(6, 5)
    grotto.room("grotto", 1, 1, 4, 3).water(3, 2, 2, 1).mark("4", (2, 3)).symbol("stairs", (1, 1), run="ew")
    grotto.outline((0, 0, 6, 5))
    plan, inset = sketch.build(), grotto.build()
    legend: tuple[SymbolKind, ...] = ("stairs",)
    regions = (
        RegionSpec("shrine", 1, "shrine.jpg", "tsr_blue", plan, "THE SHRINE OF THE DROWNED", legend=legend),
        RegionSpec("grotto", 1, "shrine.jpg", "tsr_blue", inset, "THE GROTTO", legend=legend, at=(16.0, 9.0)),
    )
    panels = (
        PanelSpec("shrine", (("shrine", (0, 0)),), plan),
        PanelSpec("grotto", (("grotto", (0, 0)),), inset),
    )
    areas = (
        AreaSpec("1", "Nave", "Broken pews face an empty plinth."),
        AreaSpec("2", "Vestry", "Mouldering vestments hang on hooks."),
        AreaSpec(
            "3",
            "Stair room",
            "A stair winds down to the grotto.",
            links=_ways(("4", "down", "stairs")),
        ),
        AreaSpec("4", "Grotto", "A still pool fills half the grotto."),
    )
    dungeon = DungeonSpec("the-shrine", "The shrine of the drowned", (LevelSpec(1, areas, ("shrine", "grotto")),))
    return MapSpec(
        name="shrine",
        title="The shrine of the drowned",
        features=("inset", "corridor_off_frame"),
        dungeon=dungeon,
        regions=regions,
        panels=panels,
    )


MAPS: tuple[Callable[[], MapSpec], ...] = (
    crypt,
    warrens,
    ravine_lair,
    cave_entrance,
    old_stair,
    farmstead,
    manor,
    fold_joined,
    fold_apart,
    undercroft,
    sunken_vaults,
    gatehouse_joined,
    gatehouse_apart,
    watchtower,
    mill,
    shrine,
    *STYLE_MAPS,
)
"""Every map this generator draws."""
