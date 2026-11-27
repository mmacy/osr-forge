"""The full run's scores mean what they say: level rows split shared panels, pools add up their members, and the bar
compares the stage with the classifier on the same levels and edges.

Nothing here sends a request or reads the home directory.
"""

import json
from pathlib import Path

import pytest
from tools.reading.full_members import Member, real_members, synthetic_members
from tools.reading.full_pools import (
    NO_LEVEL,
    bar_table,
    level_reading,
    member_by_style,
    panel_owners,
    panel_scores,
    pools,
    ten_foot_edges,
)
from tools.reading.full_report_check import check_report
from tools.reading.full_score import summary

from osrforge.tracing.fivefoot import convert_five_foot
from osrforge.tracing.score import _edge, _edge_cells, _grid_edges, edge_universe, score_trace
from osrforge.tracing.truth import GeometryTruth, Level

TOOL = Path(__file__).resolve().parent.parent
ROOT = TOOL.parent.parent
SYNTHETIC = ROOT / "tests" / "assets" / "synthetic-maps"


def _caves() -> Member:
    (member,) = real_members(
        [TOOL / "maps.json"], [TOOL / "pages.json"], [ROOT / "tools" / "answer_keys" / "keys.json"]
    )
    return member


def _fallen_back(document: GeometryTruth, dungeon_id: str, number: int) -> GeometryTruth:
    """The document with one level fallen back, its dungeon's entrance and every passage into it taken out."""

    def kept(level: Level, dungeon: str) -> Level:
        passages = tuple(p for p in level.passages if ((p.to_dungeon or dungeon), p.to_level) != (dungeon_id, number))
        return level.model_copy(update={"passages": passages})

    dungeons = tuple(
        dungeon.model_copy(
            update={
                "levels": tuple(
                    Level(number=level.number, verdict="fallback", reason="test")
                    if (dungeon.id, level.number) == (dungeon_id, number)
                    else kept(level, dungeon.id)
                    for level in dungeon.levels
                ),
                "entrance": None if dungeon.id == dungeon_id else dungeon.entrance,
            }
        )
        for dungeon in document.dungeons
    )
    return GeometryTruth.model_validate(document.model_copy(update={"dungeons": dungeons}).model_dump())


def _flat(figures: dict, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in figures.items():
        if name == "mean_overlap" or value is None or isinstance(value, bool):
            continue
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{name}."))
        elif value:
            out[f"{prefix}{name}"] = value
    return out


def _added(*trees: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for tree in trees:
        for name, value in _flat(tree).items():
            out[name] = round(out.get(name, 0) + value, 6)
    return {name: value for name, value in out.items() if value}


def test_the_bar_charges_a_level_the_stage_fell_back_on_with_every_key_join_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.reading.full_pools as full_pools

    monkeypatch.setattr(full_pools, "disputed_edges", lambda work, frame, width, height: set())
    member = _caves()
    key = member.document()
    stage = _fallen_back(key, "hobgoblin-lair", 1)
    table = bar_table([member], {member.id: stage}, {member.id: key}, {member.id: key})
    cave = table["cave"]
    wanted = full_pools._joins(key.level("hobgoblin-lair", 1))
    assert wanted
    assert cave["stage"]["joins"]["errors"] == cave["stage"]["joins"]["missing"] == len(wanted)
    assert cave["stage"]["joins"]["key"] == cave["classifier"]["joins"]["key"]
    assert cave["classifier"]["joins"]["errors"] == 0
    assert cave["stage"]["fell_back"] == [f"{member.id} hobgoblin-lair/1"] and cave["classifier"]["fell_back"] == []
    assert not cave["passes"]["joins"]


def test_the_bar_leaves_out_a_member_without_classifier_readings_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.reading.full_pools as full_pools

    monkeypatch.setattr(full_pools, "disputed_edges", lambda work, frame, width, height: set())
    member = _caves()
    key = member.document()
    stage = _fallen_back(key, "hobgoblin-lair", 1)
    table = bar_table([member], {member.id: stage}, {member.id: None}, {member.id: None})
    assert set(table) == {"black_white", "cave", "dark_rock"}
    for row in table.values():
        assert row["left_out"] == [member.id]
        assert "joins" not in row["stage"] and "joins" not in row["classifier"]


def test_the_bar_gives_the_stages_joins_resolved_on_the_keys_reading_beside_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.reading.full_pools as full_pools
    from tools.reading.full_run import on_key_reading

    monkeypatch.setattr(full_pools, "disputed_edges", lambda work, frame, width, height: set())
    member = _caves()
    key = member.document()
    stage = _fallen_back(key, "hobgoblin-lair", 1)
    resolved = on_key_reading(member, {panel.id: panel for panel in stage.panels})
    assert resolved is not None
    table = bar_table([member], {member.id: stage}, {member.id: key}, {member.id: resolved})
    for row in table.values():
        like = row["stage_on_key_reading"]
        assert like["joins"]["key"] == row["stage"]["joins"]["key"]
    # The stage's cells and edges are the key's, so on the key's reading only the resolution's own departures from the
    # key remain, and the fallen-back level comes back.
    assert table["cave"]["stage_on_key_reading"]["fell_back"] == []
    assert table["cave"]["stage_on_key_reading"]["joins"]["errors"] < table["cave"]["stage"]["joins"]["errors"]
    assert on_key_reading(member, {}) is None


def test_the_rows_on_a_shared_panel_add_up_to_the_panel_and_a_boundary_edge_counts_for_its_west_or_north_level(
    tmp_path: Path,
) -> None:
    (member,) = [m for m in synthetic_members(SYNTHETIC) if m.id == "synthetic/old-stair"]
    key = member.document()
    users = {
        panel.id: [lv for d in key.dungeons for lv in d.levels if any(p.panel == panel.id for p in lv.panels)]
        for panel in key.panels
    }
    shared = next(panel_id for panel_id, levels in users.items() if len(levels) > 1)
    # A trace that reads one boundary edge wrong.
    panel = key.panel(shared)
    owners = panel_owners(key, shared)
    boundary = next(
        edge
        for edge in _grid_edges(panel.width, panel.height)
        if all(cell in owners for cell in _edge_cells(edge))
        and owners[_edge_cells(edge)[0]] != owners[_edge_cells(edge)[1]]
        and all(panel.cells[y][x] == "." for x, y in _edge_cells(edge))
    )
    axis, line, index = boundary
    letter = (
        "w"
        if (panel.edges.vertical if axis == "vertical" else panel.edges.horizontal)[
            index if axis == "vertical" else line
        ][line if axis == "vertical" else index]
        in "op"
        else "o"
    )
    rows = [list(row) for row in (panel.edges.vertical if axis == "vertical" else panel.edges.horizontal)]
    if axis == "vertical":
        rows[index][line] = letter
    else:
        rows[line][index] = letter
    edges = panel.edges.model_copy(update={axis: tuple("".join(row) for row in rows)})
    trace = key.model_copy(
        update={"panels": tuple(p.model_copy(update={"edges": edges}) if p.id == shared else p for p in key.panels)}
    )
    scores = panel_scores(trace, key)[shared]
    for part in ("after", "after_whole"):
        assert _added(*(owner[part] for owner in scores["parts"].values())) == _flat(scores[part])
    west_or_north = owners[_edge_cells(boundary)[0]]
    wrong = {
        owner: figures["after"]["edges"]["all"]["total"] - figures["after"]["edges"]["all"]["right"]
        for owner, figures in scores["parts"].items()
    }
    assert wrong[west_or_north] == 1 and sum(wrong.values()) == 1
    rows_total = sum(
        level_reading(panel_scores(trace, key), key, d.id, lv.number)["after"]["edges"]["all"]["total"]
        for d in key.dungeons
        for lv in d.levels
        if any(p.panel == shared for p in lv.panels)
    )
    assert (
        rows_total
        + scores["parts"].get(NO_LEVEL, {"after": {"edges": {"all": {"total": 0}}}})["after"]["edges"]["all"]["total"]
        == scores["after"]["edges"]["all"]["total"]
    )


def test_page_38s_lairs_own_their_floor_once_and_the_ravine_belongs_to_no_level() -> None:
    key = _caves().document()
    owners = panel_owners(key, "jn1-p038")
    panel = key.panel("jn1-p038")
    floor = {(x, y) for y, row in enumerate(panel.cells) for x, letter in enumerate(row) if letter == "."}
    scores = panel_scores(key, key)["jn1-p038"]
    lairs = [owner for owner in scores["parts"] if owner != NO_LEVEL]
    assert len(lairs) == 10
    owned = sum(scores["parts"][owner]["after"]["floor"]["key"] for owner in lairs)
    assert owned == len(floor & set(owners)) < len(floor)
    assert scores["parts"][NO_LEVEL]["after"]["floor"]["key"] == len(floor - set(owners))
    assert owned + scores["parts"][NO_LEVEL]["after"]["floor"]["key"] == scores["after"]["floor"]["key"]


def test_a_pool_counts_each_region_level_passage_and_dungeon_once_by_style() -> None:
    member = _caves()
    key = member.document()
    trace = _fallen_back(key, "orc-lair-a", 1)
    panels = panel_scores(trace, key)
    split = member_by_style(trace, key, panels)
    whole = summary(score_trace(trace, key).after)
    for family in whole:
        assert _added(*(styles["after"].get(family, {}) for styles in split.values())) == _flat(whole[family]), family
    cave_levels = sum(1 for d in key.dungeons for lv in d.levels if lv.panels and lv.panels[0].panel == "jn1-p038")
    assert split["cave"]["after"]["verdicts"]["levels"] == cave_levels == 10
    assert split["cave"]["after"]["verdicts"]["levels_right"] == 9
    assert whole["passages"]["total"] > 0
    assert (
        sum(styles["after"].get("passages", {}).get("total", 0) for styles in split.values())
        == whole["passages"]["total"]
    )
    pooled = pools([], {member.id: split}, {member.id: False})
    assert pooled["cave real"]["after"]["verdicts"] == split["cave"]["after"]["verdicts"]
    assert (
        sum(pool["after"].get("reachability", {}).get("total", 0) for pool in pooled.values())
        == whole["reachability"]["total"]
    )
    areas = pooled["cave real"]["after"]["areas"]
    assert 0 < areas["mean_overlap"] <= 1


def test_the_settled_edges_of_a_5_foot_map_are_counted_on_the_scorers_10_foot_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.reading.full_pools as full_pools

    member = _caves()
    key = member.document()
    panel = key.panel("jn1-p021-halfling")
    assert panel.scale_feet == 5
    every = set(panel.edges.positions())
    monkeypatch.setattr(full_pools, "disputed_edges", lambda work, frame, width, height: set(every))
    converted = convert_five_foot(panel).panel
    universe = {_edge(*edge) for edge in edge_universe(converted)}
    mapped = ten_foot_edges(panel, every)
    # Every edge of the scorer's universe that covers a 5-foot segment is settled when every segment is. The rest lie
    # on a frame line the conversion adds past the 5-foot grid.
    beyond = universe - mapped
    assert universe & mapped and all(
        line * 2 - 1 > panel.width or line * 2 - 1 > panel.height for _a, line, _i in beyond
    )
    assert len(edge_universe(panel)) > len(universe)
    table = bar_table([member], {member.id: key}, {member.id: key}, {member.id: key})
    row = table["black_white"]
    for reader in ("stage", "classifier"):
        assert row["disputed"][reader]["total"] <= row[reader]["edges"]["total"]
        assert row["disputed"][reader]["right"] == row["disputed"][reader]["total"]


def test_the_report_check_passes_a_run_on_fake_answers_and_fails_a_pool_that_does_not_add_up(tmp_path: Path) -> None:
    import io

    from tools.reading.full_run import run
    from tools.reading.full_settings import load_settings
    from tools.reading.tests.test_full_run import _fake_sources, _members, _settings

    members = _members("sunken-vaults", "cellars")
    report = run(
        members, [], load_settings(_settings(tmp_path)), _fake_sources(tmp_path, members), stand_in=False
    ).report
    report = json.loads(json.dumps(report))
    out = io.StringIO()
    assert check_report(report, out) == 0, out.getvalue()
    report["pools"]["black_white synthetic"]["after"]["verdicts"]["levels"] += 1
    out = io.StringIO()
    assert check_report(report, out) == 1
    assert "FAIL black_white synthetic: every family equals its members' (after)" in out.getvalue()


def test_a_milestone_picture_veils_the_cells_its_levels_row_does_not_score() -> None:
    import numpy as np
    from PIL import Image
    from tools.answer_keys.resolve import load_caches
    from tools.reading.full_checks import check_document
    from tools.reading.full_pictures import fault_picture

    key = _caves().document()
    _survey, contents, mapread = load_caches(ROOT / "tests" / "assets" / "chaotic-caves")
    checks = next(c for c in check_document(key, contents, mapread) if c.dungeon == "hobgoblin-lair")
    owned = [cell for cell, owner in panel_owners(key, "jn1-p038").items() if owner == "hobgoblin-lair/1"]
    with Image.open(ROOT / "tests" / "assets" / "chaotic-caves" / "maps" / "p038.png") as image:
        plain = fault_picture(image.convert("RGB"), key, "jn1-p038", "hobgoblin-lair", 1, checks)
        veiled = fault_picture(image.convert("RGB"), key, "jn1-p038", "hobgoblin-lair", 1, checks, owned)
    assert veiled.size == plain.size
    assert np.asarray(veiled, dtype=float).mean() > np.asarray(plain, dtype=float).mean() + 1
