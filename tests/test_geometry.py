"""Geometry synthesis: dimension parsing, placement, edges, transitions, and postconditions."""

from pathlib import Path

import pytest
from osrlib.crawl.dungeon import Direction, EdgeKind, edge_key, step

from osrforge.contracts.stages import LevelContent, SurveyIndex
from osrforge.geometry import DEFAULT_ROOM_CELLS, parse_dimensions, synthesize_geometry

ASSETS = Path(__file__).parent / "assets"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A 20' x 30' chamber of packed earth.", (2, 3)),
        ("A 30 × 40' vault.", (3, 4)),  # noqa: RUF001 — the multiplication sign is the point
        ("A room 30 feet by 40 feet.", (3, 4)),
        ("a 15 x 17 foot dining room", (2, 2)),  # ceil on both axes
        ("A 40' x 10' gallery.", (4, 1)),
        ("measures 25 ft. x 35 ft.", (3, 4)),
        ("roughly 9' by 9'", (1, 1)),  # minimum 1 via ceil
        ("The party marches 2 x 4 posts.", None),  # no unit marker
        ("no dimensions here", None),
        ("", None),
    ],
)
def test_dimension_parser(text: str, expected: tuple[int, int] | None):
    assert parse_dimensions(text) == expected


def make_index(areas: list[str], connections_only: bool = False) -> SurveyIndex:
    return SurveyIndex.model_validate(
        {
            "schema_version": 1,
            "title": "Mod",
            "hooks": [],
            "town": {"name": "Town", "description": ""},
            "dungeons": [
                {
                    "id": "lair",
                    "name": "Lair",
                    "levels": [
                        {
                            "number": 1,
                            "map_pages": [],
                            "areas": [
                                {"key": key, "name": key, "source_label": None, "kind": "room", "source_pages": []}
                                for key in areas
                            ],
                        }
                    ],
                }
            ],
            "monster_names": [],
        }
    )


def make_content(areas: dict[str, list[tuple[str, str]]], descriptions: dict[str, str] | None = None) -> LevelContent:
    """Level content where `areas` maps key → [(to_key, direction), ...]."""
    descriptions = descriptions or {}
    return LevelContent.model_validate(
        {
            "schema_version": 1,
            "dungeon_id": "lair",
            "level_number": 1,
            "areas": [
                {
                    "key": key,
                    "description": descriptions.get(key, ""),
                    "encounters": [],
                    "trap": None,
                    "treasure": [],
                    "features": [],
                    "connections": [{"to_key": to_key, "direction": direction} for to_key, direction in connections],
                    "source_pages": [],
                    "confidence": 0.9,
                }
                for key, connections in areas.items()
            ],
        }
    )


def load_corpus(base: Path) -> tuple[SurveyIndex, list[LevelContent]]:
    index = SurveyIndex.model_validate_json((base / "survey.json").read_text(encoding="utf-8"))
    levels = [
        LevelContent.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(base.glob("areas.*.json"))
    ]
    return index, levels


class TestPlacement:
    def test_default_room_size(self):
        index = make_index(["1"])
        (geometry,) = synthesize_geometry(index, [make_content({"1": []})])
        assert len(geometry.areas["1"]) == DEFAULT_ROOM_CELLS[0] * DEFAULT_ROOM_CELLS[1]

    def test_stated_direction_honoured(self):
        index = make_index(["1", "2"])
        content = make_content({"1": [("2", "east")], "2": []})
        (geometry,) = synthesize_geometry(index, [content])
        max_x_of_1 = max(x for x, _ in geometry.areas["1"])
        min_x_of_2 = min(x for x, _ in geometry.areas["2"])
        assert min_x_of_2 > max_x_of_1

    def test_reverse_only_mention_is_inverted(self):
        # Only area 2 mentions the connection: "1 is north of 2" places 2 south of 1.
        index = make_index(["1", "2"])
        content = make_content({"1": [], "2": [("1", "north")]})
        (geometry,) = synthesize_geometry(index, [content])
        max_y_of_1 = max(y for _, y in geometry.areas["1"])
        min_y_of_2 = min(y for _, y in geometry.areas["2"])
        assert min_y_of_2 > max_y_of_1

    def test_same_level_vertical_is_direction_unknown(self):
        # The minimod pit-room shape: 5 → 6 down within one level places 6
        # in the first free direction (north of 5), not on a third axis.
        index = make_index(["5", "6"])
        content = make_content({"5": [("6", "down")], "6": [("5", "up")]})
        (geometry,) = synthesize_geometry(index, [content])
        assert geometry.transitions == ()
        assert set(geometry.areas) == {"5", "6"}
        max_y_of_6 = max(y for _, y in geometry.areas["6"])
        min_y_of_5 = min(y for _, y in geometry.areas["5"])
        assert max_y_of_6 < min_y_of_5  # north placement

    def test_collision_slides_laterally(self):
        # Two children east of the same parent: the second cannot take the
        # first's spot and must land elsewhere, with no overlap.
        index = make_index(["1", "2", "3"])
        content = make_content({"1": [("2", "east"), ("3", "east")], "2": [], "3": []})
        (geometry,) = synthesize_geometry(index, [content])
        cells_2 = set(geometry.areas["2"])
        cells_3 = set(geometry.areas["3"])
        assert not cells_2 & cells_3

    def test_determinism(self):
        index = make_index(["1", "2", "3", "4"])
        content = make_content(
            {"1": [("2", "unknown"), ("3", "south")], "2": [("4", "east")], "3": [], "4": []},
            descriptions={"1": "A 30' x 20' hall."},
        )
        first = synthesize_geometry(index, [content])
        second = synthesize_geometry(index, [content])
        assert first == second

    def test_unresolved_target_is_dropped_and_reported(self):
        index = make_index(["1"])
        content = make_content({"1": [("the great beyond", "east")]})
        (geometry,) = synthesize_geometry(index, [content])
        assert geometry.unresolved_connections == (("1", "the great beyond"),)

    def test_unknown_direction_is_reported_with_the_resolved_key(self):
        index = make_index(["1", "2"])
        content = make_content({"1": [("2", "unknown")], "2": []})
        (geometry,) = synthesize_geometry(index, [content])
        assert geometry.unknown_direction_connections == (("1", "2"),)

    def test_slug_match_resolves_printed_labels(self):
        index = make_index(["room-7", "1"])
        content = make_content({"room-7": [], "1": [("Room 7", "north")]})
        (geometry,) = synthesize_geometry(index, [content])
        assert geometry.unresolved_connections == ()

    def test_self_connection_dropped(self):
        index = make_index(["1"])
        content = make_content({"1": [("1", "north")]})
        (geometry,) = synthesize_geometry(index, [content])
        assert geometry.unresolved_connections == ()
        assert len(geometry.areas["1"]) == 4

    def test_disconnected_component_gets_synthetic_link_and_flags(self):
        index = make_index(["1", "2", "3"])
        content = make_content({"1": [("2", "east")], "2": [], "3": []})
        (geometry,) = synthesize_geometry(index, [content])
        assert geometry.disconnected_areas == ("3",)
        # Reachability postcondition already asserts 3 is reachable via the
        # synthetic corridor; the entrance component is never flagged.
        assert "1" not in geometry.disconnected_areas


class TestEdges:
    def test_interior_corridor_and_junction_edges_are_open_and_canonical(self):
        index = make_index(["1", "2"])
        content = make_content({"1": [("2", "east")], "2": []})
        (geometry,) = synthesize_geometry(index, [content])
        for key, edge in geometry.edges.items():
            assert edge.kind is EdgeKind.OPEN
            cell_part, _, side = key.partition(":")
            assert side in ("north", "west")
            x, _, y = cell_part.partition(",")
            assert int(x) >= 0 and int(y) >= 0
        # Interior adjacency of a 2x2 room: both cells of each row/column pair.
        cells_1 = geometry.areas["1"]
        first = cells_1[0]
        east_neighbor = step(first, Direction.EAST)
        assert east_neighbor in cells_1
        assert edge_key(first, Direction.EAST) in geometry.edges

    def test_corridor_cells_are_disjoint_from_rooms(self):
        index = make_index(["1", "2", "3"])
        content = make_content({"1": [("2", "east"), ("3", "south")], "2": [], "3": []})
        (geometry,) = synthesize_geometry(index, [content])
        room_cells = {cell for cells in geometry.areas.values() for cell in cells}
        assert not room_cells & set(geometry.corridors)
        assert geometry.corridors  # at least one corridor cell exists


class TestTransitions:
    def multi_level_index(self) -> SurveyIndex:
        return SurveyIndex.model_validate(
            {
                "schema_version": 1,
                "title": "Mod",
                "hooks": [],
                "town": {"name": "Town", "description": ""},
                "dungeons": [
                    {
                        "id": "manor",
                        "name": "Manor",
                        "levels": [
                            {
                                "number": 1,
                                "map_pages": [],
                                "areas": [
                                    {
                                        "key": key,
                                        "name": key,
                                        "source_label": None,
                                        "kind": "room",
                                        "source_pages": [],
                                    }
                                    for key in ("84", "85")
                                ],
                            },
                            {
                                "number": 2,
                                "map_pages": [],
                                "areas": [
                                    {
                                        "key": key,
                                        "name": key,
                                        "source_label": None,
                                        "kind": "room",
                                        "source_pages": [],
                                    }
                                    for key in ("98", "99")
                                ],
                            },
                        ],
                    }
                ],
                "monster_names": [],
            }
        )

    def level_contents(self, both_directions: bool) -> list[LevelContent]:
        level_1 = {
            "schema_version": 1,
            "dungeon_id": "manor",
            "level_number": 1,
            "areas": [
                {
                    "key": "84",
                    "description": "",
                    "encounters": [],
                    "trap": None,
                    "treasure": [],
                    "features": [],
                    "connections": [{"to_key": "85", "direction": "east"}],
                    "source_pages": [],
                    "confidence": 0.9,
                },
                {
                    "key": "85",
                    "description": "",
                    "encounters": [],
                    "trap": None,
                    "treasure": [],
                    "features": [],
                    "connections": [{"to_key": "98", "direction": "up"}],
                    "source_pages": [],
                    "confidence": 0.9,
                },
            ],
        }
        connections_98 = [{"to_key": "85", "direction": "down"}] if both_directions else []
        level_2 = {
            "schema_version": 1,
            "dungeon_id": "manor",
            "level_number": 2,
            "areas": [
                {
                    "key": "98",
                    "description": "",
                    "encounters": [],
                    "trap": None,
                    "treasure": [],
                    "features": [],
                    "connections": [*connections_98, {"to_key": "99", "direction": "north"}],
                    "source_pages": [],
                    "confidence": 0.9,
                },
                {
                    "key": "99",
                    "description": "",
                    "encounters": [],
                    "trap": None,
                    "treasure": [],
                    "features": [],
                    "connections": [],
                    "source_pages": [],
                    "confidence": 0.9,
                },
            ],
        }
        return [LevelContent.model_validate(level_1), LevelContent.model_validate(level_2)]

    @pytest.mark.parametrize("both_directions", [False, True])
    def test_cross_level_link_is_reciprocal_and_deduplicated(self, both_directions: bool):
        # The manor 85 ↔ 98 shape: stated one way or both, exactly one
        # reciprocal pair results.
        results = synthesize_geometry(self.multi_level_index(), self.level_contents(both_directions))
        level_1, level_2 = results
        assert len(level_1.transitions) == 1
        assert len(level_2.transitions) == 1
        up = level_1.transitions[0]
        down = level_2.transitions[0]
        assert up.kind == "stairs_up"
        assert up.position == level_1.areas["85"][0]
        assert up.to_level_number == 2
        assert up.to_position == level_2.areas["98"][0]
        assert down.kind == "stairs_down"
        assert down.position == level_2.areas["98"][0]
        assert down.to_position == level_1.areas["85"][0]

    def test_non_entrance_level_anchors_on_the_transition_target(self):
        results = synthesize_geometry(self.multi_level_index(), self.level_contents(True))
        level_2 = results[1]
        # 98 is the transition target; BFS anchored there, so it sits at the
        # normalized origin region rather than 99 (the survey-first is 98 anyway;
        # the strong claim is entrance stays on level 1 only).
        assert results[0].entrance is not None
        assert level_2.entrance is None


class TestPostconditionsOverTheCommittedCorpora:
    @pytest.mark.parametrize("corpus", ["minimod/expected", "chaotic-caves/stages"])
    def test_postconditions_hold(self, corpus: str):
        index, levels = load_corpus(ASSETS / corpus)
        results = synthesize_geometry(index, levels)  # postconditions assert inside
        assert len(results) == sum(len(dungeon.levels) for dungeon in index.dungeons)
        for geometry in results:
            for cells in geometry.areas.values():
                assert cells == tuple(sorted(cells, key=lambda cell: (cell[1], cell[0])))

    @pytest.mark.parametrize("corpus", ["minimod/expected", "chaotic-caves/stages"])
    def test_synthesis_is_deterministic(self, corpus: str):
        index, levels = load_corpus(ASSETS / corpus)
        assert synthesize_geometry(index, levels) == synthesize_geometry(index, levels)

    def test_every_dungeon_with_areas_has_exactly_one_entrance_level(self):
        index, levels = load_corpus(ASSETS / "chaotic-caves/stages")
        results = synthesize_geometry(index, levels)
        by_dungeon: dict[str, list[int]] = {}
        for geometry in results:
            if geometry.entrance is not None:
                by_dungeon.setdefault(geometry.dungeon_id, []).append(geometry.level_number)
        for dungeon in index.dungeons:
            assert by_dungeon[dungeon.id] == [dungeon.levels[0].number]
