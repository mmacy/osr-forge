"""The scorer: a perfect self-score, one family per single change, alignment, partly-floor cells, and repairs.

Every count below is taken from the grids in `tracing_support.demo_truth`.
Panel `p1` has 34 edges that touch floor (16 vertical, 18 horizontal), `p1b`
and `p2` have 4 each, so the universe is 42. Cave cell (4,3) touches 4 edges.
Partly-floor cell (3,3) touches 4 edges, one of them a cave edge. Floor cells:
13 on `p1`, 1 on `p1b`, 1 on `p2`.
"""

import pytest

from osrforge.tracing.score import FAMILIES, score_trace
from osrforge.tracing.synthetic import DamageSite
from osrforge.tracing.truth import DamagedEdge, GeometryTruth, Level, Region, Repair
from tracing_support import (
    TOUCHED_FAMILIES,
    apply_damage,
    demo_truth,
    drop_passages,
    five_foot_pair,
    rebuild,
    replace_level,
    rotated_pair,
    set_edge,
    shift_panel_east,
)

pytestmark = pytest.mark.xfail(reason="chunk: scorer", strict=True)


def test_own_answer_scores_perfect():
    truth = demo_truth()
    score = score_trace(truth, truth)
    assert score.after.imperfect_families() == ()
    assert score.after_whole.imperfect_families() == ()
    assert score.before is None
    assert score.before_whole is None


def test_own_answer_counts():
    truth = demo_truth()
    after = score_trace(truth, truth).after
    assert (after.floor.key_cells, after.floor.trace_cells, after.floor.both) == (15, 15, 15)
    assert after.floor.overlap == 1.0
    assert [area.key for area in after.areas.areas] == ["1", "2", "4", "3"]
    assert after.areas.mean_overlap == 1.0
    assert (after.keys.total, after.keys.right) == (4, 4)
    assert (after.joins.key_joins, after.joins.matched) == (1, 1)
    assert (after.doors.door.key, after.doors.door.trace, after.doors.door.matched) == (2, 2, 2)
    assert (after.doors.locked.key, after.doors.secret.key) == (1, 1)
    assert (after.edges.all.total, after.edges.all.right) == (42, 42)
    assert (after.edges.cave.total, after.edges.straight.total) == (4, 38)
    assert (after.reachability.total, after.reachability.right) == (1, 1)
    assert (after.entrance.total, after.transitions.total, after.passages.total) == (1, 2, 2)
    assert (after.halves.total, after.halves.right) == (1, 1)
    assert (after.verdicts.regions, after.verdicts.regions_right, after.verdicts.levels) == (3, 3, 2)
    assert (after.repairs.edges, after.repairs.per_level) == (0, {})


def test_partly_floor_cells_are_left_out_of_the_whole_cell_scores():
    truth = demo_truth()
    whole = score_trace(truth, truth).after_whole
    assert whole.floor.key_cells == 14
    assert (whole.edges.all.total, whole.edges.cave.total, whole.edges.straight.total) == (38, 3, 35)
    # A trace that reads the partly-floor cell as rock is wrong over every cell and right without it.
    p1 = truth.panel("p1")
    rows = list(p1.cells)
    rows[3] = rows[3][:3] + "#" + rows[3][4:]
    edges = p1.edges.with_letter((3, 3), "south", "-").with_letter((3, 3), "west", "w").with_letter((3, 3), "east", "w")
    trace = rebuild(
        truth.model_copy(
            update={"panels": (p1.model_copy(update={"cells": tuple(rows), "edges": edges}), *truth.panels[1:])}
        )
    )
    score = score_trace(trace, truth)
    assert "floor" in score.after.imperfect_families()
    assert "edges" in score.after.imperfect_families()
    assert score.after_whole.floor.perfect
    assert score.after_whole.edges.perfect


DAMAGE = (
    DamageSite(change="wall_removed", panel="p1", edge="3,2:south"),
    DamageSite(change="key_moved", dungeon="demo", level=1, label="2", to_cell="4,3"),
    DamageSite(change="door_kind_changed", panel="p1", edge="2,2:south"),
    DamageSite(change="secret_door_removed", panel="p1", edge="1,2:south"),
    DamageSite(change="transition_moved", dungeon="demo", level=1, cell="2,1", to_cell="1,1"),
    DamageSite(change="passage_moved", dungeon="demo", level=1, cell="4,1", side="east", to_cell="4,2", to_side="east"),
    DamageSite(change="refusal_flipped", dungeon="demo", level=2),
)


@pytest.mark.parametrize("site", DAMAGE, ids=[site.change for site in DAMAGE])
def test_single_change_touches_one_family(site: DamageSite):
    truth = demo_truth()
    damaged = apply_damage(truth, site)
    score = score_trace(damaged, truth)
    assert score.after.imperfect_families() == TOUCHED_FAMILIES[site.change]
    assert score.after_whole.imperfect_families() == TOUCHED_FAMILIES[site.change]


def test_halves_offset_change_touches_the_halves_family():
    truth = demo_truth()
    joined = rebuild(
        truth.model_copy(
            update={"half_pairs": (truth.half_pairs[0].model_copy(update={"joined": True, "columns": 8}),)}
        )
    )
    assert score_trace(joined, joined).after.imperfect_families() == ()
    damaged = apply_damage(joined, DamageSite(change="halves_offset_changed", pair=0))
    score = score_trace(damaged, joined)
    assert score.after.imperfect_families() == ("halves",)
    assert (score.after.halves.total, score.after.halves.right) == (1, 0)
    apart = score_trace(truth, joined)
    assert apart.after.imperfect_families() == ("halves",)


def test_wall_read_as_open_is_counted_on_its_own():
    truth = demo_truth()
    score = score_trace(apply_damage(truth, DAMAGE[0]), truth)
    assert (score.after.edges.all.right, score.after.edges.all.walls_read_as_open) == (41, 1)
    assert score.after.edges.all.open_read_as_blocked == 0
    assert score.after.edges.straight.walls_read_as_open == 1


def test_door_kind_change_moves_counts_between_tallies():
    truth = demo_truth()
    doors = score_trace(apply_damage(truth, DAMAGE[2]), truth).after.doors
    assert (doors.door.key, doors.door.trace, doors.door.matched) == (2, 1, 1)
    assert (doors.secret.key, doors.secret.trace, doors.secret.matched) == (1, 2, 1)
    assert doors.door.recall == 0.5
    assert doors.secret.precision == 0.5
    assert doors.locked.perfect


def test_moved_key_is_named():
    truth = demo_truth()
    keys = score_trace(apply_damage(truth, DAMAGE[1]), truth).after.keys
    assert (keys.total, keys.right, keys.wrong) == (4, 3, ("demo/1/2",))


def test_lost_locked_door_breaks_the_join_between_the_rooms():
    truth = demo_truth()
    score = score_trace(set_edge(truth, "p1", "2,1:east", "w"), truth)
    assert score.after.imperfect_families() == ("joins", "doors")
    assert score.after.joins.missing == ("demo/1/1~2",)
    assert score.after.joins.extra == ()
    assert (score.after.doors.locked.key, score.after.doors.locked.trace) == (1, 0)


def test_dropped_passages_leave_an_area_unreachable():
    truth = demo_truth()
    score = score_trace(drop_passages(truth, "demo", 1), truth)
    assert score.after.imperfect_families() == ("reachability", "passages")
    assert (score.after.passages.total, score.after.passages.right, score.after.passages.extra) == (2, 0, 0)
    assert score.after.reachability.right == 0
    assert score.after.reachability.wrong[0].startswith("demo")


def test_alignment_finds_the_offset():
    truth = demo_truth()
    shifted = shift_panel_east(truth, "p1")
    score = score_trace(shifted, truth)
    assert score.after.imperfect_families() == ()
    assert score.after.edges.all.total == 42
    assert score.after.floor.both == 15
    assert score_trace(truth, shifted).after.imperfect_families() == ()


def test_five_foot_key_is_converted_before_scoring():
    five, ten = five_foot_pair()
    assert score_trace(ten, five).after.imperfect_families() == ()
    assert score_trace(five, ten).after.imperfect_families() == ()
    assert score_trace(ten, five).after.edges.all.total == 7


def refused_variant() -> GeometryTruth:
    """The demo with region `r2` refused as a hex map and level 2 fallen back."""
    truth = demo_truth()
    refused = Region(
        id="r2",
        page=2,
        image="r2.png",
        style="hex",
        columns=0,
        rows=0,
        scale=truth.region("r2").scale,
        compass=truth.region("r2").compass,
        legend=truth.region("r2").legend,
        verdict="refused",
        reason="style: hex",
    )
    regions = tuple(refused if region.id == "r2" else region for region in truth.regions)
    panels = tuple(panel for panel in truth.panels if panel.id != "p2")
    fallback = Level(number=2, verdict="fallback", reason="map style refused")
    dungeon = truth.dungeons[0]
    levels = tuple(fallback if level.number == 2 else level for level in dungeon.levels)
    dungeons = (dungeon.model_copy(update={"levels": levels}),)
    return rebuild(truth.model_copy(update={"regions": regions, "panels": panels, "dungeons": dungeons}))


def test_refusals_score_in_the_verdicts_family_only():
    key = refused_variant()
    assert score_trace(key, key).after.imperfect_families() == ()
    traced = demo_truth()
    score = score_trace(traced, key)
    assert score.after.imperfect_families() == ("verdicts",)
    assert (score.after.verdicts.regions_right, score.after.verdicts.levels_right) == (2, 1)
    assert score.after.transitions.total == 1
    assert len(score.after.verdicts.wrong) == 2


def test_region_facts_count_in_the_verdicts_family():
    truth = demo_truth()
    r1 = truth.region("r1")
    turned = r1.model_copy(update={"compass": r1.compass.model_copy(update={"north": "left"})})
    trace = rebuild(truth.model_copy(update={"regions": (turned, *truth.regions[1:])}))
    score = score_trace(trace, truth)
    assert score.after.imperfect_families() == ("verdicts",)
    assert score.after.verdicts.regions_right == 2


def test_before_and_after_repair():
    key = demo_truth()
    read_as_wall = set_edge(key, "p1", "4,2:south", "w")
    level = read_as_wall.level("demo", 1)
    assert level.edges is not None
    repaired_level = level.model_copy(update={"edges": level.edges.with_letter((4, 2), "south", "d")})
    after = replace_level(read_as_wall, "demo", repaired_level)
    trace = rebuild(
        after.model_copy(
            update={
                "before_repair": read_as_wall,
                "repairs": (
                    Repair(
                        dungeon="demo",
                        level=1,
                        fault="area_unreachable",
                        edges=(DamagedEdge(edge="4,2:south", before="w", after="d"),),
                        cost=1.0,
                    ),
                ),
            }
        )
    )
    score = score_trace(trace, key)
    assert score.before is not None
    assert score.before_whole is not None
    assert score.after.imperfect_families() == ("doors",)
    assert score.before.imperfect_families() == ("doors",)
    assert (score.after.repairs.per_level, score.after.repairs.edges, score.after.repairs.edges_matching) == (
        {"demo/1": 1},
        1,
        1,
    )
    assert (score.before.repairs.edges, score.before.repairs.per_level) == (0, {})
    assert set(FAMILIES) <= set(score.after.model_dump())


def test_turned_panel_is_mapped_through_its_rotation():
    """A panel with north at the page's left edge sits turned on its level, and an extra page column is a level row."""
    key, trace = rotated_pair()
    assert score_trace(key, key).after.imperfect_families() == ()
    score = score_trace(trace, key)
    assert score.after.imperfect_families() == ()
    assert (score.after.floor.key_cells, score.after.floor.both) == (3, 3)
    assert score.after.edges.all.total == 10
    assert (score.after.keys.total, score.after.keys.right) == (1, 1)
    assert score_trace(key, trace).after.imperfect_families() == ()
