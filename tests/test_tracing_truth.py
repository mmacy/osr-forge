"""The geometry truth format: addresses, letter grids, the validators, and the JSON round trip."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from osrforge.tracing.truth import (
    Compass,
    EdgeGrids,
    Entrance,
    GeometryTruth,
    Level,
    LevelPanel,
    Panel,
    PanelRegion,
    Passage,
    Registration,
    Symbol,
    cell_key,
    edge_key,
    load_geometry_truth,
    order_key,
    parse_cell,
    parse_edge,
    rotate_cell,
    rotate_side,
    rotated_size,
    rotation_for,
    save_geometry_truth,
)
from tracing_support import demo_truth, five_foot_pair, rotated_pair, set_edge, shift_panel_east


def test_addresses_round_trip():
    assert parse_cell("3,4") == (3, 4)
    assert cell_key((3, 4)) == "3,4"
    assert parse_edge("3,4:east") == ((3, 4), "east")
    assert edge_key((3, 4), "east") == "3,4:east"
    with pytest.raises(ValueError):
        parse_cell("3;4")
    with pytest.raises(ValueError):
        parse_edge("3,4:up")


def test_edge_grids_address_each_side():
    grids = EdgeGrids(vertical=("wow", "wdw"), horizontal=("ww", "oo", "ww"))
    assert grids.width == 2
    assert grids.height == 2
    assert grids.at((0, 0), "west") == "w"
    assert grids.at((0, 0), "east") == "o"
    assert grids.at((1, 0), "east") == "w"
    assert grids.at((0, 0), "north") == "w"
    assert grids.at((0, 0), "south") == "o"
    assert grids.at((0, 1), "south") == "w"
    assert grids.at((0, 1), "east") == "d"
    changed = grids.with_letter((0, 0), "east", "s")
    assert changed.at((0, 0), "east") == "s"
    assert changed.at((1, 0), "west") == "s"
    assert grids.at((0, 0), "east") == "o"
    assert len(list(grids.positions())) == 12


def test_edge_grids_reject_wrong_shapes_and_letters():
    with pytest.raises(ValidationError):
        EdgeGrids(vertical=("wow",), horizontal=("ww", "ww", "ww"))
    with pytest.raises(ValidationError):
        EdgeGrids(vertical=("wox",), horizontal=("ww", "ww"))
    with pytest.raises(ValidationError):
        EdgeGrids(vertical=("wow", "wo"), horizontal=("ww", "ww", "ww"))


def test_order_key_follows_the_compass():
    north_west, north_east, south_west = (0.5, 0.5), (5.5, 0.5), (0.5, 5.5)
    assert sorted([south_west, north_east, north_west], key=order_key) == [north_west, north_east, south_west]
    # With north at the left edge of the page, west is the bottom, so the south-west point comes before the north-west.
    assert sorted([north_west, north_east, south_west], key=lambda p: order_key(p, "left")) == [
        south_west,
        north_west,
        north_east,
    ]
    assert sorted([north_west, north_east, south_west], key=lambda p: order_key(p, "right")) == [
        north_east,
        north_west,
        south_west,
    ]
    assert sorted([north_west, north_east, south_west], key=lambda p: order_key(p, "bottom")) == [
        south_west,
        north_east,
        north_west,
    ]


def test_registration_interpolates_within_cells():
    registration = Registration(corners=(((0.0, 0.0), (10.0, 0.0)), ((0.0, 10.0), (12.0, 10.0))))
    assert registration.columns == 1
    assert registration.rows == 1
    assert registration.pixel((0.5, 0.0)) == (5.0, 0.0)
    assert registration.pixel((1.0, 1.0)) == (12.0, 10.0)
    assert registration.pixel((0.5, 0.5)) == (5.5, 5.0)


def test_demo_document_validates_and_round_trips(tmp_path: Path):
    truth = demo_truth()
    path = tmp_path / "geometry.json"
    save_geometry_truth(path, truth)
    assert load_geometry_truth(path) == truth
    save_geometry_truth(tmp_path / "again.json", truth)
    assert (tmp_path / "again.json").read_bytes() == path.read_bytes()
    assert truth.region("r1").registration is not None
    assert truth.panel("p1").width == 6
    assert truth.level("demo", 2).areas == {"3": truth.level("demo", 2).areas["3"]}
    with pytest.raises(KeyError):
        truth.level("demo", 3)


def test_five_foot_documents_validate():
    five, ten = five_foot_pair()
    assert five.panels[0].scale_feet == 5
    assert ten.panels[0].scale_feet == 10


def test_edge_without_a_reading_beside_floor_is_rejected():
    panel = demo_truth().panel("p1")
    with pytest.raises(ValidationError, match="must have a reading"):
        Panel.model_validate(
            panel.model_copy(update={"edges": panel.edges.with_letter((1, 1), "west", "-")}).model_dump()
        )
    with pytest.raises(ValidationError, match="must be '-'"):
        Panel.model_validate(
            panel.model_copy(update={"edges": panel.edges.with_letter((0, 0), "north", "w")}).model_dump()
        )


def test_passage_side_must_be_a_wall():
    truth = demo_truth()
    level = truth.level("demo", 1)
    assert level.edges is not None
    opened = level.model_copy(update={"edges": level.edges.with_letter((4, 1), "east", "o")})
    with pytest.raises(ValidationError, match="isn't a wall"):
        Level.model_validate(opened.model_dump())
    with pytest.raises(ValidationError, match="isn't a wall"):
        Level.model_validate(
            level.model_copy(
                update={"passages": (Passage(cell="1,1", side="east", to_level=1, to_cell="9,1"),)}
            ).model_dump()
        )


def test_references_must_resolve():
    truth = demo_truth()
    p1 = truth.panel("p1")
    with pytest.raises(ValidationError, match="names region"):
        GeometryTruth.model_validate(
            truth.model_copy(
                update={
                    "panels": (
                        p1.model_copy(update={"regions": (PanelRegion(region="nope", origin=(0, 0)),)}),
                        *truth.panels[1:],
                    )
                }
            ).model_dump()
        )
    level = truth.level("demo", 1)
    with pytest.raises(ValidationError, match="names area"):
        Level.model_validate(
            level.model_copy(update={"keys": (level.keys[0].model_copy(update={"area": "9"}),)}).model_dump()
        )
    with pytest.raises(ValidationError, match="entrance names level"):
        GeometryTruth.model_validate(
            truth.model_copy(
                update={"dungeons": (truth.dungeons[0].model_copy(update={"entrance": Entrance(level=3, cell="0,3")}),)}
            ).model_dump()
        )


def test_fallback_level_has_no_grid():
    with pytest.raises(ValidationError, match="reason is present exactly"):
        Level(number=1, verdict="fallback")
    with pytest.raises(ValidationError, match="has no grid"):
        Level(number=1, verdict="fallback", reason="no map page", cells=("#",))
    assert Level(number=1, verdict="fallback", reason="no map page").width == 0


def test_symbol_placement():
    assert Symbol(kind="cave_mouth", edge="1,1:north").cells == ()
    with pytest.raises(ValidationError):
        Symbol(kind="cave_mouth", cells=("1,1",))
    with pytest.raises(ValidationError):
        Symbol(kind="pillar", edge="1,1:north")
    with pytest.raises(ValidationError):
        Symbol(kind="stairs", cells=("1,1",), direction="north")  # type: ignore[arg-type]


def test_region_verdict_fields_agree():
    region = demo_truth().region("r1")
    with pytest.raises(ValidationError, match="exactly when the verdict"):
        region.model_validate(region.model_copy(update={"verdict": "refused"}).model_dump())
    with pytest.raises(ValidationError, match="must be 7 by 5"):
        region.model_validate(region.model_copy(update={"columns": 7}).model_dump())


def test_support_helpers_keep_documents_valid():
    truth = demo_truth()
    changed = set_edge(truth, "p1", "3,2:south", "o")
    assert changed.panel("p1").edges.at((3, 2), "south") == "o"
    level_edges = changed.level("demo", 1).edges
    assert level_edges is not None
    assert level_edges.at((3, 2), "south") == "o"
    shifted = shift_panel_east(truth, "p1")
    assert shifted.panel("p1").width == 7
    assert shifted.level("demo", 1).width == 12
    assert shifted.level("demo", 1).areas["4"].cells == ("10,1",)
    assert shifted.dungeons[0].entrance is not None
    assert shifted.dungeons[0].entrance.cell == "1,3"
    assert shifted.level("demo", 2).transitions[0].to_cell == "3,1"


def test_rotation_helpers_follow_the_conventions():
    assert [rotation_for(side) for side in ("top", "left", "bottom", "right")] == [0, 90, 180, 270]
    # A 3 by 2 panel turned 90 degrees clockwise: page (x, y) lands at (1 - y, x).
    assert rotate_cell((0, 1), (3, 2), 90) == (0, 0)
    assert rotate_cell((0, 0), (3, 2), 90) == (1, 0)
    assert rotate_cell((1, 0), (3, 2), 90) == (1, 1)
    assert rotate_cell((2, 0), (3, 2), 90) == (1, 2)
    assert rotate_cell((0, 0), (3, 2), 180) == (2, 1)
    assert rotate_cell((0, 0), (3, 2), 270) == (0, 2)
    assert rotate_cell((2, 1), (3, 2), 0) == (2, 1)
    assert rotated_size((3, 2), 90) == (2, 3)
    assert rotated_size((3, 2), 180) == (3, 2)
    assert rotate_side("north", 90) == "east"
    assert rotate_side("west", 90) == "north"
    assert rotate_side("east", 270) == "north"
    assert rotate_side("south", 180) == "north"


def test_compass_angle_rounds_to_the_nearest_quarter_turn():
    assert Compass(north="left", printed=True, angle=250).north == "left"
    assert Compass(north="top", printed=True, angle=45).angle == 45
    assert Compass(north="right", printed=True, angle=46).north == "right"
    assert Compass(north="top", printed=True, angle=350).north == "top"
    with pytest.raises(ValidationError, match="nearest quarter turn"):
        Compass(north="right", printed=True, angle=45)
    with pytest.raises(ValidationError, match="printed compass"):
        Compass(north="top", printed=False, angle=10)
    with pytest.raises(ValidationError, match="under 360"):
        Compass(north="top", printed=True, angle=360)


def test_level_panel_turn_must_match_the_compass():
    truth = demo_truth()
    level = truth.level("demo", 1)
    turned = level.model_copy(
        update={"panels": (LevelPanel(panel="p1", origin=(0, 0), rotation=90), *level.panels[1:])}
    )
    dungeons = (truth.dungeons[0].model_copy(update={"levels": (turned, truth.dungeons[0].levels[1])}),)
    with pytest.raises(ValidationError, match="needs 0"):
        GeometryTruth.model_validate(truth.model_copy(update={"dungeons": dungeons}).model_dump())


def test_rotated_pair_agrees_with_the_helpers():
    """Every cell and edge of the key's level is the panel's, turned 90 degrees and moved to the origin."""
    for truth in rotated_pair():
        panel = truth.panel("pl")
        level = truth.level("turn", 1)
        assert level.edges is not None
        part = level.panels[0]
        for y in range(panel.height):
            for x in range(panel.width):
                turned = rotate_cell((x, y), (panel.width, panel.height), part.rotation)
                lx, ly = turned[0] + part.origin[0], turned[1] + part.origin[1]
                assert level.cells[ly][lx] == panel.cells[y][x], (x, y)
                for side in ("north", "east", "south", "west"):
                    assert level.edges.at((lx, ly), rotate_side(side, part.rotation)) == panel.edges.at((x, y), side), (
                        x,
                        y,
                        side,
                    )
        assert rotated_size((panel.width, panel.height), part.rotation) == (2, panel.width)
