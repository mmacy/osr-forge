"""The synthetic maps this generator draws: the TSR blue and black-and-white maps and the features they show.

Each function returns one map's spec. The answers aren't here: they're worked
out from each map's reading by [`resolve_dungeon`][osrforge.tracing.synthetic._resolve.resolve_dungeon].
Coordinates in a sketch are cells of its own grid, `(x, y)` from the north-west.
A damage site's level cell is on the level's grid, which adds a margin of one
cell where a level's floor reaches the frame.
"""

from collections.abc import Callable

from osrforge.contracts.stages import AreaConnection
from osrforge.tracing.synthetic import DamageSite
from osrforge.tracing.synthetic._plan import Plan, Sketch
from osrforge.tracing.synthetic._spec import AreaSpec, DungeonSpec, LevelSpec, MapSpec, PanelSpec, RegionSpec
from osrforge.tracing.truth import HalfPair, Style, SymbolKind


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
    sketch.room("r6", 23, 3, 1, 2).edge((22, 3), "east", "d").mark("6", (25, 1), leader_to=(23, 3))
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
            "keys_stacked",
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
    sketch = Sketch(20, 13)
    sketch.room("chamber", 0, 5, 4, 3).edge((0, 6), "west", "o").symbol("cave_mouth", edge=((0, 6), "west"))
    sketch.run("p", (4, 6), (7, 6)).edge((3, 6), "east", "d").edge((7, 6), "east", "d")
    sketch.room("r2", 8, 4, 5, 5).mark("2", (10, 6))
    sketch.room("r3", 13, 4, 4, 5).edge((12, 6), "east", "l").mark("3", (15, 6))
    sketch.room("r4", 13, 9, 4, 3).edge((15, 8), "south", "s").mark("4", (14, 10))
    sketch.outline((0, 0, 20, 13))
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
        damage=(DamageSite(change="key_moved", dungeon="the-hollow-hill", level=1, label="3", to_cell="10,5"),),
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
            "3", "Lower hall", "The two flights from the landing arrive at either end of this hall, above a dark pool."
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
    sketch.ornament("track", (5.5, 8.1), (6.5, 11.2), (11.0, 12.0), (14.5, 9.5), (14.5, 7.1))
    sketch.ornament("contour", (0.2, 14.0), (4.0, 15.2), (8.0, 14.4), (13.6, 16.0))
    sketch.ornament("contour", (0.2, 18.2), (5.0, 19.6), (10.0, 18.6), (14.6, 21.8))
    plan = sketch.build()
    region, panel = _single("farmstead", "Hobb's farmstead", plan, "black_white", ("bridge",))
    areas = (
        AreaSpec("1", "Farmhouse", "A low farmhouse with shuttered windows."),
        AreaSpec("2", "Barn", "Stalls line the barn's west wall."),
        AreaSpec("3", "Tree house", "A platform with a roof sits in the branches of an old oak."),
        AreaSpec("4", "Moated house", "A narrow house stands on an island inside a moat."),
    )
    dungeon = DungeonSpec("hobbs-farmstead", "Hobb's farmstead", (LevelSpec(1, areas, ("farmstead",)),))
    return MapSpec(
        name="farmstead",
        title="Hobb's farmstead",
        features=(
            "farmstead_open_ground",
            "tree_house_no_door",
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
)
"""Every map this generator draws."""
