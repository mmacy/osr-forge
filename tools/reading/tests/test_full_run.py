"""The full run's parts, offline: stores and sources, costs, the checks, per-level scoring, pools, the bar, and runs
on fake answers.

Nothing here sends a request or reads a real ledger: answers come from the stores the tests fill, or from fakes made
from the answer keys, and every ledger is a temporary file.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tools.answer_keys.resolve import load_caches
from tools.reading.full_checks import check_document
from tools.reading.full_fakes import FakeClaude, FakeClef
from tools.reading.full_members import Member, real_members, synthetic_members
from tools.reading.full_reading import Pick, key_page_answer
from tools.reading.full_run import run
from tools.reading.full_score import level_documents, resolve_member, summary
from tools.reading.full_settings import load_settings
from tools.reading.full_store import ClaudeStore, ClefAnswer, ClefStore, Sources
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
        "cap": 480,
    },
    "clef": {"backend": "local", "model": "clef-flash", "cloud_fallback": {"model": "clef-flash", "spend_usd": 0.95}},
    "decision": {
        "passable_threshold": {"tsr_blue": 0.7, "black_white": 0.9, "cave": 0.9, "dark_rock": 0.8},
        "epsilon": 0.05,
    },
}


def _settings(tmp_path: Path, ledger: Path | None = None, **changes: Any) -> Path:
    document = json.loads(json.dumps(SETTINGS))
    document["decision"].update(changes)
    if ledger is not None:
        document["claude"]["ledger"] = str(ledger)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document))
    return path


def _png(shade: int = 255) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (shade, shade, shade)).save(buffer, "PNG")
    return buffer.getvalue()


def _request(state: str = "s") -> DecisionRequest:
    return DecisionRequest(
        tag="edge",
        image=_png(),
        state=state,
        question="q",
        options=(("open", None), ("wall", None)),
        model="clef-flash",
    )


def _members(*names: str) -> list[Member]:
    return [member for member in synthetic_members(SYNTHETIC) if member.id.split("/")[-1] in names]


def _fake_sources(tmp_path: Path, members: list[Member]) -> Sources:
    maps = {run_map.uid: run_map.screen for member in members for run_map in member.maps}
    pages = {page.id: (page, member.document()) for member in members for page in member.pages}
    return Sources(
        clef_store=ClefStore(tmp_path / "clef.jsonl"),
        claude_store=ClaudeStore(tmp_path / "claude"),
        clef=FakeClef(maps),
        claude=FakeClaude(maps, pages),
        claude_how="fake",
    )


def test_the_settings_give_each_kind_its_model_and_refuse_an_epsilon_out_of_range(tmp_path: Path) -> None:
    settings = load_settings(_settings(tmp_path, ledger=tmp_path / "ledger.json"))
    assert (settings.offset_pick.model, settings.offset_pick.effort) == ("claude-sonnet-5-5", "low")
    assert settings.cap == 480 and settings.hosted_spend_usd == 0.95 and settings.ledger == tmp_path / "ledger.json"
    with pytest.raises(ValueError, match="epsilon"):
        load_settings(_settings(tmp_path, epsilon=0.6))


def test_a_request_is_counted_once_by_its_key_and_reused_answers_keep_their_origin(tmp_path: Path) -> None:
    store = ClefStore(tmp_path / "clef.jsonl")
    seeded, other = _request("s"), _request("t")
    store.put(seeded.key(), "m", "0,0:west", {"open": 0.8, "wall": 0.2}, "screening")
    sources = Sources(clef_store=store, claude_store=ClaudeStore(tmp_path / "claude"))
    assert sources.decide(seeded, "m", "0,0:west", "clef-edge", {"edge": [[0, 0], "west"]}) == {
        "open": 0.8,
        "wall": 0.2,
    }
    assert sources.decide(other, "m", "1,0:west", "clef-edge", {"edge": [[1, 0], "west"]}) is None
    asked: list[DecisionRequest] = []

    def fake(request: DecisionRequest, about: dict[str, Any]) -> ClefAnswer:
        asked.append(request)
        return ClefAnswer({"open": 0.1, "wall": 0.9}, request, "local", 0.0)

    sources = Sources(clef_store=store, claude_store=ClaudeStore(tmp_path / "claude"), clef=fake)
    for owner in ("m", "n"):
        assert sources.decide(other, owner, "1,0:west", "clef-edge", {"edge": [[1, 0], "west"]}) == {
            "open": 0.1,
            "wall": 0.9,
        }
    assert sources.decide(seeded, "n", "0,0:west", "clef-edge", {"edge": [[0, 0], "west"]}) is not None
    assert len(asked) == 1
    assert ClefStore(tmp_path / "clef.jsonl").get(other.key()) == {"open": 0.1, "wall": 0.9}
    totals = sources.tally.totals()
    assert totals["clef-edge sent"] == {"requests": 1, "answers": 1, "usd": 0.0}
    assert totals["clef-edge screening"] == {"requests": 1, "answers": 1, "usd": 0.0}
    assert sources.tally.lookups[("n", "clef-edge")] == 2


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


def test_an_open_case_takes_out_the_level_it_names_and_the_rest_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.answer_keys.resolve as reference

    key = load_geometry_truth(CAVES)
    survey, contents, mapread = load_caches(ROOT / "tests" / "assets" / "chaotic-caves")
    real = reference.resolve
    seen: list[set[tuple[str, int]]] = []

    def resolve(document: Any, *caches: Any) -> Any:
        outlined = {(o.dungeon, o.level) for panel in document.panels for o in panel.outlines}
        seen.append(outlined)
        if ("orc-lair-a", 1) in outlined:
            raise reference.ConventionGap("letter 'A' at ('jn1-p038', 47, 64): ('orc-lair-a', 1)'s outline is open")
        return real(document, *caches)

    monkeypatch.setattr(reference, "resolve", resolve)
    trace, how = resolve_member(key, survey, contents, mapread)
    assert how.startswith("resolved with levels fallen back: orc-lair-a/1")
    assert len(seen) == 2 and ("orc-lair-a", 1) not in seen[1]
    verdicts = {(d.id, level.number): level.verdict for d in trace.dungeons for level in d.levels}
    assert verdicts[("orc-lair-a", 1)] == "fallback"
    assert verdicts[("orc-lair-b", 1)] == verdicts[("roslof-manor", 1)] == "traced"


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


def test_a_run_on_fake_answers_joins_the_fold_scores_every_family_and_adds_up_its_costs(tmp_path: Path) -> None:
    settings = load_settings(_settings(tmp_path))
    members = _members("fold-joined")
    sources = _fake_sources(tmp_path, members)
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
    assert level["complete"] and level["unjudged_items"] == level["walls_without_clef"] == 0
    costs = report["costs"]
    assert costs["rows_add_up"]
    fake = costs["requests"]["reading-wholemap fake"]
    assert fake["answers"] == 2
    claude = sum(v["requests"] for k, v in costs["requests"].items() if k.startswith("reading-"))
    on_rows = level["costs"]["claude"]["fake"]["requests"] + sum(
        row["claude"]["fake"]["requests"] for row in costs["named_rows"].values()
    )
    assert on_rows == claude


def test_a_twin_drawn_from_the_same_image_is_left_out_of_a_maps_calibration(tmp_path: Path) -> None:
    settings = load_settings(_settings(tmp_path))
    twins = _members("fold-apart", "fold-joined")
    result = run(twins, [], settings, _fake_sources(tmp_path, twins), stand_in=False)
    left_out = result.report["calibration"]["left_out"]
    assert left_out["synthetic/fold-joined/west"] == ["synthetic/fold-apart/west", "synthetic/fold-joined/west"]
    assert left_out["synthetic/fold-apart/east"] == ["synthetic/fold-apart/east", "synthetic/fold-joined/east"]
    # Turning the apart twin's answers upside down leaves the joined twin's calibration as it was, because the
    # twin's picture is the joined map's own and is left out of its fit. The other half's answers change it.
    from tools.reading.full_reading import calibrate

    readings = list(result.readings.values())
    joined = result.readings["synthetic/fold-joined/west"]
    before = dict(joined.passable)
    for uid, should_change in (("synthetic/fold-apart/west", False), ("synthetic/fold-apart/east", True)):
        twin = result.readings[uid]
        kept = dict(twin.clef)
        twin.clef = {edge: {"o": p.get("w", 0.0), "w": p.get("o", 0.0)} for edge, p in kept.items()}
        calibrate(readings)
        assert (joined.passable != before) == should_change
        twin.clef = kept
        calibrate(readings)


def test_a_pool_counts_a_panel_two_levels_share_once(tmp_path: Path) -> None:
    from tools.reading.traces import score_panel

    settings = load_settings(_settings(tmp_path))
    members = _members("undercroft")
    result = run(members, [], settings, _fake_sources(tmp_path, members), stand_in=False)
    report = result.report
    rows = [row["after"]["edges"]["all"]["total"] for row in report["levels"]]
    assert len(rows) == 2
    key = members[0].document()
    trace_panel = next(panel for panel in result.traces["synthetic/undercroft"].panels if panel.id == "undercroft")
    once = score_panel(key, trace_panel)[0].edges.all.total
    pool = report["pools"]["tsr_blue synthetic"]
    assert pool["after"]["edges"]["all"]["total"] == once < sum(rows)
    assert pool["levels"] == 2 and pool["after"]["keys"]["total"] == sum(
        r["after"]["keys"]["total"] for r in report["levels"]
    )


def test_a_pool_leaves_out_the_panels_of_a_level_that_fell_back_as_the_scorer_does(tmp_path: Path) -> None:
    settings = load_settings(_settings(tmp_path))
    members = _members("farmstead", "cellars")
    report = run(members, [], settings, _fake_sources(tmp_path, members), stand_in=False).report
    assert "fallen back" in report["members"]["synthetic/farmstead"]["resolution"]
    pool = report["pools"]["black_white synthetic"]
    for family, figure in (("edges", ("all", "total")), ("floor", ("key",))):
        whole = 0
        for member in ("synthetic/farmstead", "synthetic/cellars"):
            value = report["members"][member]["after"][family]
            for name in figure:
                value = value[name]
            whole += value
        pooled = pool["after"][family]
        for name in figure:
            pooled = pooled[name]
        assert pooled == whole > 0


def test_a_live_claude_run_refuses_a_ledger_other_than_the_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    from tools.reading.full_run import make_sources

    settings = load_settings(_settings(tmp_path, ledger=tmp_path / "settings-ledger.json"))
    monkeypatch.setenv("OSRFORGE_CLAUDE_LEDGER", str(tmp_path / "other-ledger.json"))
    args = argparse.Namespace(out=tmp_path / "out", claude="live", clef="none")
    with pytest.raises(SystemExit, match="must name the settings' ledger"):
        make_sources(args, settings, [])


def test_a_live_claude_run_stops_at_the_settings_cap_on_the_settings_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse
    from types import SimpleNamespace

    import tools.reading.claude_run as claude_run
    from tools.reading.full_run import make_sources
    from tools.reading.offsets import check_offset, offset_kind

    from osrforge.providers.base import TextPart
    from osrforge.providers.reading import ReadingRequest, ReadingResponse, ReadingUsage

    ledger = tmp_path / "ledger.json"
    held = [{"label": "reading-items medium", "cost_usd": 0.0}] * 479 + [{"label": "other", "cost_usd": 0.0}]
    ledger.write_text(json.dumps({"stop_usd": 100.0, "spent_usd": 0.0, "entries": held}))

    class Provider:
        settings = SimpleNamespace(ledger_path=ledger, model="claude-sonnet-5-5")

        def read(self, request: ReadingRequest) -> Any:
            document = json.loads(ledger.read_text())
            document["entries"].append({"label": "reading-offset low", "cost_usd": 0.001})
            ledger.write_text(json.dumps(document))
            return ReadingResponse(
                data={"choice": "A", "reason": ""}, usage=ReadingUsage(input_tokens=10, output_tokens=5), model_id="m"
            )

    monkeypatch.setattr(claude_run, "make_provider", lambda model: Provider())
    monkeypatch.setenv("OSRFORGE_CLAUDE_LEDGER", str(ledger))
    settings = load_settings(_settings(tmp_path, ledger=ledger))
    assert settings.cap == 480
    args = argparse.Namespace(out=tmp_path / "out", claude="live", clef="none")
    sources = make_sources(args, settings, [])
    kind = offset_kind("low", "claude-sonnet-5-5")
    first = ReadingRequest(kind=kind, shared=(), question=(TextPart(text="first"),))
    assert sources.read(first, check_offset, "pair", {"pair": "pair"}) is not None
    second = ReadingRequest(kind=kind, shared=(), question=(TextPart(text="second"),))
    with pytest.raises(SystemExit, match="cap of 480"):
        sources.read(second, check_offset, "pair", {"pair": "pair"})
    assert len(json.loads(ledger.read_text())["entries"]) == 481


def test_a_local_server_that_names_another_model_for_clef_flash_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.reading.full_run import clef_source

    monkeypatch.setenv("OSRFORGE_CLEF_LOCAL_MODEL", "some-other-model")
    settings = load_settings(_settings(tmp_path))
    with pytest.raises(SystemExit, match="unset OSRFORGE_CLEF_LOCAL_MODEL"):
        clef_source("local", settings)


def test_a_malformed_attempt_is_charged_what_the_ledger_settled_and_each_attempt_counts(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from tools.reading.claude_run import RequestCap, ask
    from tools.reading.offsets import check_offset, offset_kind

    from osrforge.errors import ReadingFailure
    from osrforge.providers.base import TextPart
    from osrforge.providers.reading import ReadingRequest

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"stop_usd": 1.0, "spent_usd": 0.0, "entries": []}))

    class Provider:
        settings = SimpleNamespace(ledger_path=ledger, model="claude-opus-5-5")

        def read(self, request: ReadingRequest) -> Any:
            document = json.loads(ledger.read_text())
            label = f"{request.kind.name} {request.kind.effort}"
            document["entries"].append({"label": label, "model": request.kind.model, "cost_usd": 0.0123})
            ledger.write_text(json.dumps(document))
            raise ReadingFailure("stopped at max_tokens", "max_tokens")

    request = ReadingRequest(kind=offset_kind("low", "claude-sonnet-5-5"), shared=(), question=(TextPart(text="q"),))
    outcome = ask(Provider(), request, check_offset, RequestCap(ledger, 10))  # type: ignore[arg-type]
    assert [attempt.cost_usd for attempt in outcome.attempts] == [0.0123, 0.0123]
    record = outcome.to_json()
    sources = Sources(
        clef_store=ClefStore(tmp_path / "clef.jsonl"),
        claude_store=ClaudeStore(tmp_path / "claude"),
        claude=lambda request, check, about: record,
    )
    sources.read(request, check_offset, "pair", {"pair": "pair", "truth": "A"})
    assert sources.tally.totals()["reading-offset sent"] == {"requests": 2, "answers": 1, "usd": 0.0246}


def test_a_question_local_clef_fails_on_goes_to_hosted_within_the_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.reading.clefask as clefask
    from tools.reading.full_run import clef_source

    import osrforge.providers.clef as hosted_module
    import osrforge.providers.clef_local as local_module
    from osrforge.errors import ProviderError

    class Local:
        def __init__(self, settings: Any) -> None:
            pass

        def decide(self, request: DecisionRequest) -> Any:
            raise ProviderError("the local server is down")

    made: list[Any] = []

    class Hosted:
        def __init__(self, settings: Any) -> None:
            made.append(settings)

        def decide(self, request: DecisionRequest) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(probabilities={"open": 0.3, "wall": 0.7}, input_tokens=1000)

    class HostedSettings:
        ledger_path = tmp_path / "clef-ledger.json"
        ledger_stop_usd = None

        @classmethod
        def from_env(cls) -> HostedSettings:
            return cls()

        def model_copy(self, update: dict[str, Any]) -> dict[str, Any]:
            return update

    spends: list[float] = []
    monkeypatch.setattr(local_module, "LocalClefProvider", Local)
    monkeypatch.setattr(hosted_module, "ClefProvider", Hosted)
    monkeypatch.setattr(hosted_module, "ClefSettings", HostedSettings)
    monkeypatch.setattr(clefask, "capped_stop", lambda path, stop, spend: spends.append(spend) or 5.0)
    monkeypatch.delenv("OSRFORGE_CLEF_LOCAL_MODEL", raising=False)
    settings = load_settings(_settings(tmp_path))
    sources = Sources(
        clef_store=ClefStore(tmp_path / "clef.jsonl"),
        claude_store=ClaudeStore(tmp_path / "claude"),
        clef=clef_source("local", settings),
    )
    request = _request()
    assert sources.decide(request, "m", "0,0:west", "clef-edge", {"edge": [[0, 0], "west"]}) == {
        "open": 0.3,
        "wall": 0.7,
    }
    assert spends == [0.95] and made == [{"run_label": "reading full run", "ledger_stop_usd": 5.0}]
    record = ClefStore(tmp_path / "clef.jsonl").record(request.key())
    assert record is not None and record["source"] == "hosted"
    assert record["usd"] == pytest.approx(hosted_module.cost_usd("clef-flash", 1000))
    with pytest.raises(ProviderError):
        clef_source("local-only", settings)(_request("t"), {})


def test_claude_answers_only_for_the_owners_a_run_names(tmp_path: Path) -> None:
    from tools.reading.offsets import check_offset, offset_kind

    from osrforge.providers.base import TextPart
    from osrforge.providers.reading import ReadingRequest

    request = ReadingRequest(kind=offset_kind("low", "claude-sonnet-5-5"), shared=(), question=(TextPart(text="q"),))
    asked: list[str] = []

    def fake(request: ReadingRequest, check: Any, about: dict[str, Any]) -> dict[str, Any]:
        asked.append(about["pair"])
        return {"answer": {"choice": "A", "reason": ""}, "attempts": [{}], "cost_usd": 0.01}

    sources = Sources(
        clef_store=ClefStore(tmp_path / "clef.jsonl"),
        claude_store=ClaudeStore(tmp_path / "claude"),
        claude=fake,
        claude_owners={"kept"},
    )
    assert sources.read(request, check_offset, "other", {"pair": "other"}) is None
    kept = sources.read(request, check_offset, "kept", {"pair": "kept"})
    assert kept is not None and kept["answer"]["choice"] == "A"
    assert asked == ["kept"]
    assert [entry.origin for entry in sources.tally.entries.values()] == ["missing"]


def test_the_bar_puts_the_stage_beside_the_classifier_per_style() -> None:
    from tools.reading.full_pools import bar_table

    members = real_members([TOOL / "maps.json"], [TOOL / "pages.json"], [ROOT / "tools" / "answer_keys" / "keys.json"])
    (member,) = members
    key = member.document()
    rows = []
    for dungeon in key.dungeons:
        for level in dungeon.levels:
            if level.verdict != "traced":
                continue
            trace, one = level_documents(key, key, dungeon.id, level.number)
            style = key.region(key.panel(level.panels[0].panel).regions[0].region).style
            after = summary(score_trace(trace, one).after)
            rows.append(
                {
                    "member": member.id,
                    "synthetic": False,
                    "dungeon": dungeon.id,
                    "level": level.number,
                    "style": style,
                    "after": after,
                }
            )
    theirs = {(row["member"], row["dungeon"], row["level"]): row["after"] for row in rows}
    table = bar_table(members, {member.id: key}, {member.id: key}, rows, theirs)
    assert set(table) == {"black_white", "cave", "dark_rock"}
    for row in table.values():
        assert row["stage"]["edges"]["right"] == row["stage"]["edges"]["total"] == row["classifier"]["edges"]["total"]
        assert row["stage"]["walls_rate"] == 0.0 and row["stage"]["joins"]["errors"] == 0
        assert row["passes"] == {"walls": True, "edges": False, "joins": False, "all": False}


def test_the_settlers_edges_come_from_the_answer_key_pass_on_the_keys_grid(tmp_path: Path) -> None:
    from tools.reading.full_pools import disputed_edges

    items = {
        "edges": {
            "3,7:west": {"status": "disputed"},
            "3,4:south": {"status": "disputed"},
            "5,5:north": {"status": "agreed"},
            "0,1:west": {"status": "disputed"},
        }
    }
    (tmp_path / "items.json").write_text(json.dumps(items))
    assert disputed_edges(tmp_path, (0, 3), 16, 16) == {((3, 4), "west"), ((3, 2), "north")}
