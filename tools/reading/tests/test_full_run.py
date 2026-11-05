"""The full run's parts, offline: stores and sources, the checks, per-level scoring, and a run on fake answers.

Nothing here sends a request or reads a ledger: answers come from the stores the tests fill, or from fakes made from
the answer keys.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tools.answer_keys.resolve import load_caches
from tools.reading.full_checks import check_document
from tools.reading.full_fakes import FakeClaude, FakeClef
from tools.reading.full_members import synthetic_members
from tools.reading.full_reading import Pick, key_page_answer
from tools.reading.full_run import level_documents, resolve_member, run, summary
from tools.reading.full_settings import load_settings
from tools.reading.full_store import ClaudeStore, ClefStore, Sources
from tools.reading.screen_wholemap import load_pages
from tools.reading.wholemap import check_whole_map

from osrforge.providers.decisions import DecisionRequest
from osrforge.tracing.score import score_trace
from osrforge.tracing.truth import EdgeGrids, load_geometry_truth

TOOL = Path(__file__).resolve().parent.parent
ROOT = TOOL.parent.parent
CAVES = ROOT / "tools" / "eval" / "corpus" / "jn1-chaotic-caves" / "geometry.json"
SYNTHETIC = ROOT / "tests" / "assets" / "synthetic-maps"

SETTINGS = {
    "claude": {
        "whole_page": {"model": "claude-opus-5-5", "effort": "high"},
        "items": {"model": "claude-opus-5-5", "effort": "medium", "items_per_request": 30},
        "offset_pick": {"model": "claude-sonnet-5-5", "effort": "low"},
        "cap": 350,
    },
    "clef": {"backend": "local", "model": "clef-flash", "cloud_fallback": {"model": "clef-flash", "spend_usd": 0.95}},
    "decision": {
        "passable_threshold": {"tsr_blue": 0.7, "black_white": 0.9, "cave": 0.9, "dark_rock": 0.8},
        "epsilon": 0.05,
    },
}


def _settings(tmp_path: Path, **changes: Any) -> Path:
    document = json.loads(json.dumps(SETTINGS))
    document["decision"].update(changes)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document))
    return path


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_the_settings_give_each_kind_its_model_and_refuse_an_epsilon_out_of_range(tmp_path: Path) -> None:
    settings = load_settings(_settings(tmp_path))
    assert (settings.offset_pick.model, settings.offset_pick.effort) == ("claude-sonnet-5-5", "low")
    assert settings.acceptance == pytest.approx(0.95) and settings.hosted_spend_usd == 0.95
    with pytest.raises(ValueError, match="epsilon"):
        load_settings(_settings(tmp_path, epsilon=0.6))


def test_a_stored_answer_is_reused_by_its_request_key_and_a_missing_one_is_counted(tmp_path: Path) -> None:
    request = DecisionRequest(
        tag="edge", image=_png(), state="s", question="q", options=(("open", None), ("wall", None)), model="clef-flash"
    )
    other = DecisionRequest(
        tag="edge", image=_png(), state="t", question="q", options=(("open", None), ("wall", None)), model="clef-flash"
    )
    store = ClefStore(tmp_path / "clef.jsonl")
    store.put(request.key(), "m", "0,0:west", {"open": 0.8, "wall": 0.2}, "test")
    sources = Sources(clef_store=store, claude_store=ClaudeStore(tmp_path / "claude"))
    assert sources.decide(request, "m", "0,0:west", "clef-edge") == {"open": 0.8, "wall": 0.2}
    assert sources.decide(other, "m", "1,0:west", "clef-edge") is None
    asked: list[DecisionRequest] = []

    def fake(asked_request: DecisionRequest, about: dict[str, Any]) -> dict[str, float]:
        asked.append(asked_request)
        return {"open": 0.1, "wall": 0.9}

    sources.clef, sources.clef_how = fake, "fake"
    assert sources.decide(other, "m", "1,0:west", "clef-edge") == {"open": 0.1, "wall": 0.9}
    assert sources.decide(other, "m", "1,0:west", "clef-edge") == {"open": 0.1, "wall": 0.9}
    assert len(asked) == 1
    assert ClefStore(tmp_path / "clef.jsonl").get(other.key()) == {"open": 0.1, "wall": 0.9}
    assert sources.tally.of("m")["clef-edge"] == {
        "stored": 2,
        "stored usd": 0.0,
        "missing": 1,
        "missing usd": 0.0,
        "fake": 1,
        "fake usd": 0.0,
    }


def test_the_keys_own_whole_page_answer_passes_the_whole_page_check() -> None:
    page = next(page for page in load_pages(TOOL / "pages.json") if page.id == "jn1-p021")
    from tools.reading.screen_wholemap import _page_regions, levels_on

    labels = [region for region, _rgb, _registration in _page_regions(page)]
    answer = key_page_answer(page, load_geometry_truth(CAVES))
    assert check_whole_map(answer, labels, levels_on(page)) == []
    assert {item["dungeon"] for item in answer["levels"]} == {"halfling-home", "cave-of-horrors"}


def test_one_level_is_scored_alone_on_its_part_of_a_shared_panel() -> None:
    key = load_geometry_truth(CAVES)
    trace, one = level_documents(key, key, "orc-lair-a", 1)
    score = score_trace(trace, one).after
    panel = key.panel("jn1-p038")
    rects = [r for o in panel.outlines if (o.dungeon, o.level) == ("orc-lair-a", 1) for r in o.rects]
    inside = {(x, y) for x0, y0, w, h in rects for x in range(x0, x0 + w) for y in range(y0, y0 + h)}
    floor = sum(panel.cells[y][x] == "." for x, y in inside)
    assert score.floor.key_cells == score.floor.both == floor
    assert score.areas.perfect and score.keys.right == score.keys.total > 0
    whole = score_trace(key, key).after
    assert 0 < score.edges.all.total < whole.edges.all.total


def test_the_checks_find_an_area_a_removed_door_cuts_off() -> None:
    directory = SYNTHETIC / "manor"
    answer = load_geometry_truth(directory / "geometry.json")
    _survey, contents, mapread = load_caches(directory)
    assert all(level.needs_no_repair for level in check_document(answer, contents, mapread))
    dungeon = answer.dungeons[0]
    level = dungeon.levels[0]
    assert level.edges is not None
    doors = [edge for edge in level.edges.positions() if level.edges.at(*edge) in "dls"]
    for door in doors:
        (x, y), side = door
        vertical = [list(row) for row in level.edges.vertical]
        horizontal = [list(row) for row in level.edges.horizontal]
        if side == "west":
            vertical[y][x] = "w"
        elif side == "east":
            vertical[y][x + 1] = "w"
        elif side == "north":
            horizontal[y][x] = "w"
        else:
            horizontal[y + 1][x] = "w"
        walled = level.model_copy(
            update={
                "edges": EdgeGrids(vertical=tuple(map("".join, vertical)), horizontal=tuple(map("".join, horizontal)))
            }
        )
        changed = answer.model_copy(update={"dungeons": (dungeon.model_copy(update={"levels": (walled,)}),)})
        checks = check_document(changed, contents, mapread)[0]
        if checks.unreachable:
            assert not checks.needs_no_repair
            return
    pytest.fail("no door of the manor gates an area")


def test_a_wrong_pick_is_an_offset_other_than_the_true_one() -> None:
    from tools.reading.full_members import HalvesSpec

    spec = HalvesSpec(id="p", first=Path("a"), second=Path("b"), truth="A", real=True)
    assert Pick(spec=spec, choice="B", truth="A", columns=(0, 1, 2), rows=0).wrong
    assert not Pick(spec=spec, choice="none", truth="A", columns=(0, 1, 2), rows=0).wrong
    assert Pick(spec=spec, choice="B", truth="A", columns=(0, 1, 2), rows=0).joined_columns == 1


def test_a_run_on_fake_answers_joins_the_fold_and_scores_every_family(tmp_path: Path) -> None:
    settings = load_settings(_settings(tmp_path))
    members = [member for member in synthetic_members(SYNTHETIC) if member.id == "synthetic/fold-joined"]
    maps = {run_map.uid: run_map.screen for member in members for run_map in member.maps}
    pages = {page.id: (page, member.document()) for member in members for page in member.pages}
    sources = Sources(
        clef_store=ClefStore(tmp_path / "clef.jsonl"),
        claude_store=ClaudeStore(tmp_path / "claude"),
        clef=FakeClef(maps),
        claude=FakeClaude(maps, pages),
        clef_how="fake",
        claude_how="fake",
    )
    result = run(members, [], settings, sources, stand_in=False)
    report = result.report
    assert report["members"]["synthetic/fold-joined"]["resolution"] == "resolved"
    assert report["halves"][0]["choice"] == report["halves"][0]["truth"]
    trace = result.traces["synthetic/fold-joined"]
    assert [panel.id for panel in trace.panels] == ["west+east"]
    (level,) = report["levels"]
    after = level["after"]
    assert after["halves"] == {"total": 1, "right": 1}
    assert after["floor"]["both"] >= 0.95 * after["floor"]["key"]
    assert after["edges"]["all"]["right"] >= 0.95 * after["edges"]["all"]["total"]
    assert set(after) == set(summary(score_trace(trace, members[0].document()).after))
    assert report["requests"]["reading-wholemap fake"]["requests"] == 2
    assert "needs_no_repair" in level and "key_checks" in level


def test_an_open_case_salvages_nothing_it_cannot_and_falls_back() -> None:
    directory = SYNTHETIC / "manor"
    answer = load_geometry_truth(directory / "geometry.json")
    survey, contents, mapread = load_caches(directory)
    panel = answer.panels[0]
    unmarked = answer.model_copy(update={"panels": (panel.model_copy(update={"marks": ()}),)})
    trace, how = resolve_member(unmarked, survey, contents, mapread)
    assert how.startswith("open case")
    assert all(level.verdict == "fallback" for dungeon in trace.dungeons for level in dungeon.levels)


def test_a_fault_picture_puts_the_trace_beside_it_with_its_cut_off_floor_marked() -> None:
    from PIL import Image
    from tools.reading.full_pictures import fault_picture

    key = load_geometry_truth(CAVES)
    _survey, contents, mapread = load_caches(ROOT / "tests" / "assets" / "chaotic-caves")
    checks = next(c for c in check_document(key, contents, mapread) if c.dungeon == "orc-lair-a")
    assert checks.cut_off
    with Image.open(ROOT / "tests" / "assets" / "chaotic-caves" / "maps" / "p038.png") as image:
        picture = fault_picture(image.convert("RGB"), key, "jn1-p038", "orc-lair-a", 1, checks)
    half = (picture.width - 20) // 2
    left = np.asarray(picture.crop((0, 40, half, picture.height)), dtype=int)
    right = np.asarray(picture.crop((half + 20, 40, picture.width, picture.height)), dtype=int)
    assert left.shape == right.shape
    cyan = (right[..., 0] < left[..., 0] - 40) & (right[..., 2] >= left[..., 2])
    assert cyan.sum() > 0


def test_claude_answers_only_for_the_owners_a_run_names(tmp_path: Path) -> None:
    from tools.reading.offsets import check_offset, offset_kind

    from osrforge.providers.base import TextPart
    from osrforge.providers.reading import ReadingRequest

    request = ReadingRequest(kind=offset_kind("low", "claude-sonnet-5-5"), shared=(), question=(TextPart(text="q"),))
    asked: list[str] = []

    def fake(request: ReadingRequest, check: Any, about: dict[str, Any]) -> dict[str, Any]:
        asked.append(about["pair"])
        return {"answer": {"choice": "A", "reason": ""}, "attempts": [], "cost_usd": 0.01}

    sources = Sources(
        clef_store=ClefStore(tmp_path / "clef.jsonl"),
        claude_store=ClaudeStore(tmp_path / "claude"),
        claude=fake,
        claude_how="fake",
        claude_owners={"kept"},
    )
    assert sources.read(request, check_offset, "other", {"pair": "other"}) is None
    assert sources.read(request, check_offset, "kept", {"pair": "kept"})["answer"]["choice"] == "A"
    assert sources.read(request, check_offset, "other", {"pair": "other"})["source"] == "fake"
    assert asked == ["kept"]
    assert sources.tally.of("other")["reading-offset"]["missing"] == 1
