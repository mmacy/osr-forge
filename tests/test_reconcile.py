"""The shared reconciliation: the merge's full precedence matrix and the entrance selection."""

from osrforge.contracts.stages import MapLevelReading, SurveyDungeon
from osrforge.reconcile import ProseEdge, merge_level_edges, select_entrance

KEYS = ["1", "2", "3", "4a"]


def reading(
    proposals: list[tuple[str, str, str]],
    entrance_key: str | None = None,
    status: str = "read",
    unread_reason: str | None = None,
) -> MapLevelReading:
    return MapLevelReading.model_validate(
        {
            "dungeon_id": "lair",
            "level_number": 1,
            "map_pages": [4] if status == "read" else [],
            "status": status,
            "unread_reason": unread_reason,
            "proposals": [{"a": a, "b": b, "door": door} for a, b, door in proposals],
            "entrance_key": entrance_key,
        }
    )


def pair(*keys: str) -> frozenset[str]:
    return frozenset(keys)


class TestMergePrecedenceMatrix:
    def test_no_reading_is_the_prose_only_path(self):
        merged = merge_level_edges({pair("1", "2"): ProseEdge()}, None, KEYS)
        assert merged.adopted_doors == {}
        assert merged.map_only == ()
        assert merged.disputes == ()
        assert merged.dropped == ()

    def test_an_unread_reading_produces_no_adoptions_and_no_disagreements(self):
        # Bit-for-bit the prose-only path: even raw proposals riding an
        # unread cache (the empty_reading shape keeps the answer) assert nothing.
        unread = reading([("1", "2", "door")], status="unread", unread_reason="empty_reading")
        merged = merge_level_edges({pair("1", "3"): ProseEdge()}, unread, KEYS)
        assert merged == merge_level_edges({pair("1", "3"): ProseEdge()}, None, KEYS)

    def test_agreement_is_silent_on_edge_and_door(self):
        prose = {pair("1", "2"): ProseEdge(stated_via="door", door_kind="door")}
        merged = merge_level_edges(prose, reading([("1", "2", "door")]), KEYS)
        assert merged.disputes == ()
        assert merged.adopted_doors == {}
        assert merged.map_only == ()

    def test_map_door_none_agrees_silently_with_any_non_door_prose(self):
        # A proposed pair's door reading is complete, but "none" against
        # prose with no stated *door* is agreement — whatever the non-door
        # mechanism, the map's vocabulary doesn't carry mechanisms.
        for prose_fact in (ProseEdge(), ProseEdge(stated_via="stairs")):
            merged = merge_level_edges({pair("1", "2"): prose_fact}, reading([("1", "2", "none")]), KEYS)
            assert merged.disputes == ()
            assert merged.adopted_doors == {}

    def test_map_door_fills_a_prose_absence_flagged_as_adopted(self):
        prose = {pair("1", "2"): ProseEdge()}
        merged = merge_level_edges(prose, reading([("1", "2", "secret_door")]), KEYS)
        assert merged.adopted_doors == {pair("1", "2"): "secret_door"}
        assert merged.disputes == (
            ("1", "door 1–2 map-read secret_door; prose states no mechanism — adopted"),  # noqa: RUF001
        )

    def test_prose_stated_door_survives_a_map_none(self):
        prose = {pair("1", "2"): ProseEdge(stated_via="door", door_kind="door")}
        merged = merge_level_edges(prose, reading([("1", "2", "none")]), KEYS)
        assert merged.adopted_doors == {}
        assert merged.disputes == (("1", "door 1–2 prose-stated door; the map shows none — kept"),)  # noqa: RUF001

    def test_prose_door_kind_survives_a_map_kind_conflict(self):
        prose = {pair("1", "2"): ProseEdge(stated_via="secret_door", door_kind="secret_door")}
        merged = merge_level_edges(prose, reading([("1", "2", "door")]), KEYS)
        assert merged.adopted_doors == {}
        assert merged.disputes == (
            ("1", "door 1–2 prose-stated secret_door; map-read door — kept"),  # noqa: RUF001
        )

    def test_prose_non_door_mechanism_survives_a_map_door(self):
        prose = {pair("1", "2"): ProseEdge(stated_via="stairs")}
        merged = merge_level_edges(prose, reading([("1", "2", "door")]), KEYS)
        assert merged.adopted_doors == {}
        assert merged.disputes == (("1", "door 1–2 map-read door; prose states stairs — kept"),)  # noqa: RUF001

    def test_prose_edge_absent_from_a_read_map_is_kept_and_flagged(self):
        prose = {pair("1", "2"): ProseEdge()}
        merged = merge_level_edges(prose, reading([]), KEYS)
        assert merged.disputes == (
            ("1", "edge 1–2 prose-stated; the map shows no adjacency — kept"),  # noqa: RUF001
        )

    def test_map_only_pair_is_adopted_with_its_door_and_flagged(self):
        merged = merge_level_edges({}, reading([("2", "3", "door")]), KEYS)
        assert merged.map_only == (("2", "3", "door"),)
        assert merged.disputes == (
            ("2", "edge 2–3 map-proposed (door); prose states no connection — adopted"),  # noqa: RUF001
        )

    def test_map_only_pair_without_a_door_adopts_as_a_plain_edge(self):
        merged = merge_level_edges({}, reading([("2", "3", "none")]), KEYS)
        assert merged.map_only == (("2", "3", None),)
        assert merged.disputes == (
            ("2", "edge 2–3 map-proposed; prose states no connection — adopted"),  # noqa: RUF001
        )

    def test_endpoints_order_survey_first_whatever_the_proposal_order(self):
        merged = merge_level_edges({}, reading([("3", "2", "none")]), KEYS)
        assert merged.map_only == (("2", "3", None),)
        assert merged.disputes[0][0] == "2"

    def test_unresolvable_endpoint_drops_the_proposal_with_a_detail(self):
        merged = merge_level_edges({}, reading([("1", "x9", "door")]), KEYS)
        assert merged.map_only == ()
        assert merged.adopted_doors == {}
        assert merged.dropped == ("map names 'x9'; the survey does not",)

    def test_self_pair_drops_after_resolution(self):
        # "4A" slugs to "4a": the pair joins one key to itself.
        merged = merge_level_edges({}, reading([("4a", "4A", "none")]), KEYS)
        assert merged.map_only == ()
        assert merged.dropped == ("map joins '4a' to itself",)

    def test_endpoints_resolve_exactly_then_by_slug(self):
        prose = {pair("1", "4a"): ProseEdge()}
        merged = merge_level_edges(prose, reading([("1", "4A", "door")]), KEYS)
        assert merged.adopted_doors == {pair("1", "4a"): "door"}

    def test_duplicate_proposals_dedup_with_the_more_specific_door_winning(self):
        merged = merge_level_edges(
            {}, reading([("2", "3", "none"), ("3", "2", "door"), ("2", "3", "secret_door")]), KEYS
        )
        assert merged.map_only == (("2", "3", "secret_door"),)
        assert len(merged.disputes) == 1

    def test_duplicate_proposals_never_downgrade(self):
        prose = {pair("1", "2"): ProseEdge()}
        merged = merge_level_edges(prose, reading([("1", "2", "door"), ("1", "2", "none")]), KEYS)
        assert merged.adopted_doors == {pair("1", "2"): "door"}


def dungeon(levels: dict[int, list[str]], name: str = "Lair") -> SurveyDungeon:
    return SurveyDungeon.model_validate(
        {
            "id": "lair",
            "name": name,
            "levels": [
                {
                    "number": number,
                    "map_pages": [],
                    "areas": [
                        {"key": key, "name": key, "source_label": None, "kind": "room", "source_pages": []}
                        for key in keys
                    ],
                }
                for number, keys in levels.items()
            ],
        }
    )


class TestSelectEntrance:
    def test_no_readings_falls_back_to_the_positional_heuristic(self):
        selection = select_entrance(dungeon({1: ["1", "2"], 2: ["9"]}), {})
        assert (selection.level_number, selection.area_key) == (1, "1")
        assert selection.provenance == "heuristic"
        assert selection.dispute is None
        assert selection.dropped == ()

    def test_the_heuristic_skips_empty_levels(self):
        selection = select_entrance(dungeon({1: [], 2: ["9", "10"]}), {})
        assert (selection.level_number, selection.area_key) == (2, "9")

    def test_a_dungeon_with_no_areas_selects_nothing(self):
        selection = select_entrance(dungeon({1: []}), {})
        assert selection.level_number is None and selection.area_key is None
        assert selection.provenance == "heuristic"

    def test_a_resolvable_map_proposal_wins_with_a_dispute_when_it_differs(self):
        readings = {1: reading([], entrance_key="2")}
        selection = select_entrance(dungeon({1: ["1", "2"]}), readings)
        assert (selection.level_number, selection.area_key) == (1, "2")
        assert selection.provenance == "map"
        assert selection.dispute == "entrance map 2, survey-order 1"

    def test_an_agreeing_map_proposal_carries_no_dispute(self):
        readings = {1: reading([], entrance_key="1")}
        selection = select_entrance(dungeon({1: ["1", "2"]}), readings)
        assert selection.provenance == "map"
        assert selection.dispute is None

    def test_the_map_candidate_is_the_first_in_level_number_order(self):
        readings = {1: reading([], entrance_key="1"), 2: reading([], entrance_key="9")}
        selection = select_entrance(dungeon({2: ["9"], 1: ["1"]}), readings)
        assert (selection.level_number, selection.area_key) == (1, "1")
        # The secondary resolvable proposal is neither adopted nor silent.
        assert selection.dropped == ((2, "entrance also proposed 2/9"),)

    def test_an_unresolvable_proposal_drops_and_the_heuristic_stands(self):
        readings = {1: reading([], entrance_key="gatehouse")}
        selection = select_entrance(dungeon({1: ["1", "2"]}), readings)
        assert selection.provenance == "heuristic"
        assert (selection.level_number, selection.area_key) == (1, "1")
        assert selection.dropped == ((1, "map names 'gatehouse'; the survey does not"),)

    def test_an_unread_level_proposes_nothing(self):
        readings = {1: reading([], entrance_key="2", status="unread", unread_reason="empty_reading")}
        selection = select_entrance(dungeon({1: ["1", "2"]}), readings)
        assert selection.provenance == "heuristic"
        assert selection.dropped == ()

    def test_entrance_keys_resolve_by_slug(self):
        readings = {1: reading([], entrance_key="Room 2")}
        selection = select_entrance(dungeon({1: ["1", "room-2"]}), readings)
        assert selection.area_key == "room-2"
        assert selection.provenance == "map"
