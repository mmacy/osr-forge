"""The answer-key pipeline's parts that run without the network: tiling, crops, answer checks, the readings table,
the settler's rounds, the assembly, and the sample.

Every test builds its inputs by hand or from the committed synthetic maps. No test sends a request.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from tools.answer_keys import assemble, batches, letters, marks, reader, readings, samples, settler, tiles
from tools.answer_keys.ink import Levels, hatching, materials
from tools.answer_keys.keyspec import KeySpec, MapContext, load_key_specs
from tools.answer_keys.run import read_map
from tools.answer_keys.spec import load_specs

from osrforge.tracing.truth import EdgeGrids, load_geometry_truth

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
ASSETS = TOOL.parent.parent / "tests" / "assets"


# --- tiling and crops ---------------------------------------------------------------------------------------


def test_window_starts_cover_the_length_and_clamp_the_last_window():
    assert tiles.window_starts(56, 12) == [0, 12, 24, 36, 44]
    assert tiles.window_starts(80, 12) == [0, 12, 24, 36, 48, 60, 68]
    assert tiles.window_starts(24, 12) == [0, 12]
    assert tiles.window_starts(19, 12) == [0, 7]
    assert tiles.window_starts(12, 12) == [0]
    assert tiles.window_starts(9, 12) == [0]


def test_tile_covers_every_cell_and_keeps_full_windows():
    windows = tiles.tile(56, 80)
    assert len(windows) == 35
    covered = {(x, y) for w in windows for x in range(w.x0, w.x0 + w.columns) for y in range(w.y0, w.y0 + w.rows)}
    assert covered == {(x, y) for x in range(56) for y in range(80)}
    assert all(w.columns == 12 and w.rows == 12 for w in windows)
    small = tiles.tile(9, 20)
    assert small[0].columns == 9 and small[0].rows == 12 and len(small) == 2


def test_marks_windows_shift_half_a_window_and_still_cover_everything():
    windows = marks.marks_windows(56, 80)
    covered = {(x, y) for w in windows for x in range(w.x0, w.x0 + w.columns) for y in range(w.y0, w.y0 + w.rows)}
    assert covered == {(x, y) for x in range(56) for y in range(80)}
    assert any(w.x0 == 6 for w in windows)


@pytest.fixture(scope="module")
def manor_context():
    specs = {spec.id: spec for spec in load_specs(TOOL / "synthetic-maps.json")}
    spec = specs["manor"]
    rgb = np.asarray(Image.open(spec.image).convert("RGB"))
    fit, _reading = read_map(spec, rgb)
    key = KeySpec(
        id="manor",
        specs=TOOL / "synthetic-maps.json",
        member="synthetic/manor",
        style_note="x",
        work=Path("/nonexistent"),
        key=Path("/nonexistent/geometry.json"),
        levels=(("greywall-manor", 1),),
    )
    return MapContext(key=key, spec=spec, rgb=rgb, fit=fit)


def test_window_crop_has_the_window_at_the_stated_magnification(manor_context, tmp_path):
    ctx = manor_context
    window = tiles.Window(0, 0, 12, 12)
    out = tiles.render_window(ctx.rgb, ctx.fit, window, tmp_path / "w.png", cell_px=80)
    with Image.open(out) as image:
        # 12 cells plus a cell of margin each side, at 80 px, plus the gutter, within rounding.
        assert abs(image.width - (14 * 80 + 2 * tiles.GUTTER)) <= 4
        assert abs(image.height - (14 * 80 + 2 * tiles.GUTTER)) <= 4


def test_item_crops_box_the_item_and_fit_their_reach(manor_context, tmp_path):
    ctx = manor_context
    zoom = tiles.render_item(ctx.rgb, ctx.fit, (10, 3), "east", tmp_path / "z.png", 128, 1.5)
    wide = tiles.render_item(ctx.rgb, ctx.fit, (10, 3), None, tmp_path / "w.png", 48, 4.5)
    with Image.open(zoom) as image:
        assert abs(image.width - (3 * 128 + 2 * tiles.GUTTER)) <= 8
        pixels = np.asarray(image)
        assert ((pixels[..., 0] > 200) & (pixels[..., 1] < 60) & (pixels[..., 2] < 60)).sum() > 100
    with Image.open(wide) as image:
        assert abs(image.width - (9 * 48 + 2 * tiles.GUTTER)) <= 8


# --- the hatching rule -------------------------------------------------------------------------------------


def test_hatching_reads_the_synthetic_manor_outside_as_fill_and_leaves_the_rooms():
    rgb = np.asarray(Image.open(ASSETS / "synthetic-maps" / "manor" / "images" / "manor.png").convert("RGB"))
    levels = Levels(channel="gray", ink_max=60, paper_min=225, dark_max=65, fill=(120, 200), hatched=True)
    mats = materials(rgb, levels, 3)
    # The hatched outside, top left of the drawing, is fill; room 1's interior is paper.
    assert mats.fill[80:140, 40:100].mean() > 0.8
    assert mats.paper[400:430, 260:300].mean() > 0.75
    plain = materials(rgb, Levels(channel="gray", ink_max=60, paper_min=225, dark_max=65, fill=(120, 200)), 3)
    assert plain.fill[80:140, 40:100].mean() < 0.1
    mask = hatching(mats.ink)
    assert mask[80:140, 40:100].mean() > 0.8


# --- the window answer -------------------------------------------------------------------------------------


def _answer(columns=3, rows=2, **overrides):
    answer = {
        "cells": ["..#", ".#."][:rows],
        "vertical": ["wooo", "wwww"][:rows],
        "horizontal": ["www", "oww", "www"][: rows + 1],
        "partial": [],
        "marks": [],
        "symbols": [],
        "notes": "",
    }
    answer.update(overrides)
    return answer


def test_check_passes_a_well_formed_answer_and_names_each_fault():
    window = tiles.Window(5, 7, 3, 2)
    assert reader.check(_answer(), window) == []
    faults = reader.check(_answer(cells=["..#", ".#"]), window)
    assert any("cells string 1 has 2 letters" in fault for fault in faults)
    faults = reader.check(_answer(vertical=["wooo", "wwwx"]), window)
    assert any("outside" in fault and "x" in fault for fault in faults)
    faults = reader.check(_answer(horizontal=["www", "oww"]), window)
    assert any("horizontal has 2 strings but needs exactly 3" in fault for fault in faults)
    faults = reader.check(_answer(partial=["1,1"]), window)
    assert any("outside the window" in fault for fault in faults)
    faults = reader.check(_answer(marks=[{"text": "4", "cell": "6,8", "on_door": "x", "leader_to": None}]), window)
    assert any("on_door" in fault for fault in faults)
    faults = reader.check(
        _answer(symbols=[{"kind": "trap", "cells": ["5,7"], "edge": None, "direction": None}]), window
    )
    assert any("trap" in fault for fault in faults)
    faults = reader.check(
        _answer(symbols=[{"kind": "cave_mouth", "cells": [], "edge": "5,7:west", "direction": None}]), window
    )
    assert faults == []


def test_loose_edge_addresses_are_normalized_instead_of_refused():
    assert reader.normalize_edge("4,69 north") == "4,69:north"
    assert reader.normalize_edge("4,69:n") == "4,69:north"
    assert reader.normalize_edge("17,25,e") == "17,25:east"
    assert reader.normalize_edge("north") is None
    window = tiles.Window(5, 7, 3, 2)
    answer = _answer(
        marks=[{"text": "c", "cell": "5,7", "on_door": "5,7,w", "leader_to": None}],
        symbols=[{"kind": "cave_mouth", "cells": ["5,8"], "edge": "5,8 n", "direction": None}],
    )
    assert reader.check(answer, window) == []
    assert answer["marks"][0]["on_door"] == "5,7:west"
    assert answer["symbols"][0] == {"kind": "cave_mouth", "cells": [], "edge": "5,8:north", "direction": None}


def test_parse_turns_rows_into_absolute_cells_and_makes_rock_rock_edges_blank():
    window = tiles.Window(5, 7, 3, 2)
    parsed = reader.parse(_answer(), window)
    assert parsed.cells[(5, 7)] == "." and parsed.cells[(7, 7)] == "#" and parsed.cells[(6, 8)] == "#"
    assert parsed.edges[("v", 5, 7)] == "w"
    assert parsed.edges[("v", 6, 7)] == "o"
    assert parsed.edges[("h", 6, 8)] == "w"  # floor (6,7) over rock (6,8): read as written
    assert parsed.edges[("h", 5, 8)] == "o"
    assert parsed.edges[("v", 8, 7)] == "-"  # rock (7,7) against the window's edge, nothing beyond it


def test_retry_params_append_the_answer_and_the_faults():
    window = tiles.Window(0, 0, 3, 2)
    params = reader.window_params("AA==", window, "a map")
    retry = reader.retry_params(params, '{"cells": []}', ["cells has 0 strings but needs exactly 2"])
    assert len(retry["messages"]) == 3
    assert retry["messages"][1]["role"] == "assistant"
    assert "cells has 0 strings" in retry["messages"][2]["content"][0]["text"]
    assert retry["system"] == params["system"]


def test_shift_of_finds_a_window_read_one_row_down():
    from tools.answer_keys.windows import shift_of

    cells = (
        "#" * 12,
        "." * 12,
        "#" * 12,
        "." * 12,
        "." * 12,
        "#" * 12,
        "." * 12,
        "#" * 12,
        "." * 12,
        "#" * 12,
        "." * 12,
        "#" * 12,
    )
    window = tiles.Window(0, 0, 12, 12)
    exact = {
        "cells": list(cells),
        "vertical": ["w" * 13] * 12,
        "horizontal": ["w" * 12] * 13,
        "partial": [],
        "marks": [],
        "symbols": [],
        "notes": "",
    }
    assert shift_of(reader.parse(exact, window), cells) is None
    shifted = {**exact, "cells": ["#" * 12, *cells[:-1]]}
    assert shift_of(reader.parse(shifted, window), cells) == [0, -1]


# --- the ledger arithmetic ---------------------------------------------------------------------------------


def test_worst_case_and_batch_cost_use_half_price():
    png = Image.new("RGB", (750, 750), (255, 255, 255))
    import base64
    import io

    buffer = io.BytesIO()
    png.save(buffer, "PNG")
    data = base64.b64encode(buffer.getvalue()).decode()
    params = reader.window_params(data, tiles.Window(0, 0, 12, 12), "a map")
    tokens = batches.estimate_input_tokens(params)
    assert tokens > 750  # the image alone is 750 tokens
    worst = batches.worst_case(params)
    assert worst == round((tokens * 2.5e-6 + reader.MAX_TOKENS * 10e-6) * 0.5, 6)
    cost = batches.batch_cost(
        "claude-sonnet-5-5",
        {
            "input_tokens": 1000,
            "output_tokens": 2000,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 300,
        },
    )
    assert cost == round((1000 * 2e-6 + 2000 * 10e-6 + 500 * 2.5e-6 + 300 * 0.2e-6) * 0.5, 6)


class _FakeLedger:
    def __init__(self):
        self.entries = []
        self.reserved = []
        self.settled = []

    def open(self, entry):
        self.entries.append(entry)
        return len(self.entries) - 1

    def reserve(self, index, usd):
        from osrforge.providers.ledger import Reservation

        self.reserved.append((index, usd))
        return Reservation(index=index, usd=usd)

    def settle(self, reservation, usd):
        self.settled.append((reservation.index, reservation.usd, usd))


class _FakeBatches:
    def __init__(self, results):
        self._results = results
        self.created = []

    def create(self, requests):
        self.created.append(requests)

        class Batch:
            id = f"batch_{len(self.created)}"

        return Batch()

    def retrieve(self, batch_id):
        class Status:
            processing_status = "ended"

        return Status()

    def results(self, batch_id):
        return self._results


class _FakeClient:
    def __init__(self, results):
        self.messages = type("M", (), {})()
        self.messages.batches = _FakeBatches(results)


def _result(custom_id, text, errored=False):
    from types import SimpleNamespace

    if errored:
        return SimpleNamespace(
            custom_id=custom_id, result=SimpleNamespace(type="errored", model_dump=lambda mode: {"type": "errored"})
        )
    message = {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "end_turn",
    }
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(type="succeeded", message=SimpleNamespace(model_dump=lambda mode: message)),
    )


def _png_base64(size=(64, 64)):
    import base64
    import io

    buffer = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_batch_store_reserves_each_job_and_settles_from_the_answer(tmp_path):
    store = batches.BatchStore(tmp_path / "store")
    params = reader.window_params(_png_base64(), tiles.Window(0, 0, 3, 2), "a map")
    store.add_job("a", params, {"window": [0, 0, 3, 2]})
    store.add_job("b", params, {"window": [0, 0, 3, 2]})
    ledger = _FakeLedger()
    client = _FakeClient([_result("a", json.dumps(_answer())), _result("b", "", errored=True)])
    batch = store.submit(client, ledger, "test")
    assert batch == "batch_1"
    assert len(ledger.reserved) == 2 and store.pending() == []
    counts = store.collect(client, ledger)
    assert counts == {"open": 0, "answered": 1, "errored": 1}
    assert store.answer("a")["cost_usd"] == batches.batch_cost(
        "claude-sonnet-5-5", {"input_tokens": 10, "output_tokens": 5}
    )
    assert [s[2] for s in ledger.settled] == [store.answer("a")["cost_usd"], 0.0]
    # The errored job is pending again, so the next submit sends it.
    assert store.pending() == ["b"]


def test_submit_sends_parts_under_the_size_limit_and_releases_reservations_on_failure(tmp_path, monkeypatch):
    store = batches.BatchStore(tmp_path / "store")
    params = reader.window_params(_png_base64((400, 400)), tiles.Window(0, 0, 3, 2), "a map")
    for name in ("a", "b", "c"):
        store.add_job(name, params, {"window": [0, 0, 3, 2]})
    monkeypatch.setattr(batches, "BATCH_BYTES", (store.jobs / "a.json").stat().st_size + 1)
    ledger = _FakeLedger()
    client = _FakeClient([])
    assert store.submit(client, ledger, "test") == "batch_3"
    assert [len(r) for r in client.messages.batches.created] == [1, 1, 1]
    assert [e["label"] for e in ledger.entries] == ["test part 1", "test part 2", "test part 3"]

    # A job limit sends the first jobs and leaves the rest pending for a later call.
    store3 = batches.BatchStore(tmp_path / "store3")
    for name in ("a", "b", "c"):
        store3.add_job(name, params, {"window": [0, 0, 3, 2]})
    monkeypatch.setattr(batches, "BATCH_BYTES", 10**9)
    ledger3 = _FakeLedger()
    client3 = _FakeClient([])
    assert store3.submit(client3, ledger3, "test", limit=2) == "batch_1"
    assert [len(r) for r in client3.messages.batches.created] == [2]
    assert store3.pending() == ["c"]

    # A collect resends only the jobs it queued: the ids it names, never every pending job.
    store4 = batches.BatchStore(tmp_path / "store4")
    for name in ("a", "b", "c"):
        store4.add_job(name, params, {"window": [0, 0, 3, 2]})
    assert store4.added == ["a", "b", "c"]
    client4 = _FakeClient([])
    assert store4.submit(client4, _FakeLedger(), "test", ids=["b"]) == "batch_1"
    assert [len(r) for r in client4.messages.batches.created] == [1]
    assert store4.pending() == ["a", "c"]
    assert store4.submit(client4, _FakeLedger(), "test", ids=[]) is None

    class _Refusing:
        def create(self, requests):
            raise RuntimeError("413")

    store2 = batches.BatchStore(tmp_path / "store2")
    store2.add_job("a", params, {"window": [0, 0, 3, 2]})
    ledger2 = _FakeLedger()
    client2 = _FakeClient([])
    client2.messages.batches = _Refusing()
    with pytest.raises(RuntimeError):
        store2.submit(client2, ledger2, "test")
    assert ledger2.settled == [(0, ledger2.reserved[0][1], 0.0)]
    assert store2.pending() == ["a"]


# --- the readings table ------------------------------------------------------------------------------------


def test_edge_names_and_keys_round_trip():
    assert readings.edge_name(("v", 0, 3), 10, 10) == "0,3:west"
    assert readings.edge_name(("v", 10, 3), 10, 10) == "9,3:east"
    assert readings.edge_name(("h", 4, 0), 10, 10) == "4,0:north"
    assert readings.edge_name(("h", 4, 10), 10, 10) == "4,9:south"
    for name in ("0,3:west", "9,3:east", "4,0:north", "4,9:south"):
        assert readings.edge_name(readings.edge_key(name), 10, 10) == name


def test_frame_edges_make_the_classifier_abstain():
    assert readings._on_frame(("v", 0, 3), 10, 10)
    assert readings._on_frame(("v", 10, 3), 10, 10)
    assert readings._on_frame(("h", 4, 10), 10, 10)
    assert not readings._on_frame(("v", 5, 3), 10, 10)
    assert not readings._on_frame(("h", 4, 5), 10, 10)


def test_items_mark_disputes_between_the_readers():
    items = readings.Items(width=2, height=1)
    items.cells = {
        "0,0": {"classifier": ".", "claude": ["."], "status": "agreed"},
        "1,0": {"classifier": "#", "claude": ["."], "status": "disputed"},
    }
    assert items.disputed("cells") == ["1,0"]


def test_match_marks_pairs_the_same_text_within_two_cells():
    first = readings._dedupe_marks(
        [{"text": "12", "cell": "3,4"}, {"text": "12", "cell": "3,5"}, {"text": "7", "cell": "9,9"}]
    )
    second = readings._dedupe_marks([{"text": "12", "cell": "3,4"}, {"text": "8", "cell": "1,1"}])
    matched = readings._match_marks(first, second)
    by_text = {m["text"]: m for m in matched}
    assert by_text["12"]["status"] == "disputed"  # the first reading placed it in two cells
    assert by_text["7"]["status"] == "disputed" and by_text["7"]["reading2"] == []
    assert by_text["8"]["reading1"] == []
    agreed = readings._match_marks(
        readings._dedupe_marks([{"text": "a", "cell": "2,2"}]), readings._dedupe_marks([{"text": "A", "cell": "2,2"}])
    )
    assert agreed[0]["status"] == "agreed"


def test_edge_class_disputes_only_cross_class_clef_answers():
    assert readings._edge_class("o") == readings._edge_class("p")
    assert readings._edge_class("w") == readings._edge_class("n")
    assert readings._edge_class("d") != readings._edge_class("s")


# --- the settler -------------------------------------------------------------------------------------------


def test_settle_items_run_cells_first_then_edges_with_rock_sides_derived():
    items = readings.Items(width=2, height=2)
    items.cells = {
        "0,0": {"classifier": ".", "claude": ["."], "status": "agreed"},
        "1,0": {"classifier": "#", "claude": ["."], "status": "disputed"},
        "0,1": {"classifier": ".", "claude": ["."], "status": "agreed"},
        "1,1": {"classifier": "#", "claude": ["#"], "status": "agreed"},
    }
    items.edges = {
        "1,0:west": {"classifier": "w", "claude": ["o"], "status": "disputed"},
        "1,0:south": {"classifier": "-", "claude": ["w"], "status": "disputed"},
        "0,0:south": {"classifier": "o", "claude": ["w"], "clef": {"choice": "o", "p": 0.9}, "status": "disputed"},
    }
    first = settler.settle_items(items, None)
    assert [item["id"] for item in first] == ["c:1,0"]
    second = settler.settle_items(items, {"0,0": ".", "1,0": "#", "0,1": ".", "1,1": "#"})
    # 1,0 settled as rock: its west edge is floor-rock, a wall without asking; its south edge is rock-rock, nothing.
    assert [item["id"] for item in second] == ["e:0,0:south"]
    assert "Clef read open (p=0.9)" in second[0]["text"]
    third = settler.settle_items(items, {"0,0": ".", "1,0": ".", "0,1": ".", "1,1": "#"})
    assert [item["id"] for item in third] == ["e:1,0:west", "e:0,0:south"]
    # A window read on an edge between floor and rock is asked, because the key keeps the letter.
    items.edges["1,0:west"] = {"classifier": "w", "claude": ["n"], "status": "disputed"}
    fourth = settler.settle_items(items, {"0,0": ".", "1,0": "#", "0,1": ".", "1,1": "#"})
    assert [item["id"] for item in fourth] == ["e:1,0:west", "e:0,0:south"]


def test_check_answer_validates_each_kind():
    expected = [
        {"id": "c:1,0", "kind": "cell"},
        {"id": "e:0,0:south", "kind": "edge"},
        {"id": "m:3,4|0", "kind": "mark"},
        {"id": "s:5,5|0", "kind": "symbol"},
    ]
    good = {
        "items": [
            {"id": "c:1,0", "answer": "#", "reason": "r"},
            {"id": "e:0,0:south", "answer": "d", "reason": "r"},
            {"id": "m:3,4|0", "answer": "12@3,4", "reason": "r"},
            {"id": "s:5,5|0", "answer": "stairs@5,5;5,6|down", "reason": "r"},
        ]
    }
    assert settler.check_answer(good, expected, False) == []
    bad = {
        "items": [
            {"id": "c:1,0", "answer": "floor", "reason": "r"},
            {"id": "e:0,0:south", "answer": "x", "reason": "r"},
            {"id": "m:3,4|0", "answer": "12", "reason": "r"},
        ]
    }
    faults = settler.check_answer(bad, expected, False)
    assert len(faults) == 4  # three bad answers and one missing item
    adversarial = {"items": [dict(row, challenged=False) for row in good["items"]]}
    assert settler.check_answer(adversarial, expected, True) == []
    assert settler.check_answer(good, expected, True)


def test_final_values_take_the_resettled_value_over_the_settled_one(tmp_path):
    key = KeySpec(id="m", specs=tmp_path, member="x", style_note="x", work=tmp_path, key=tmp_path / "g.json")
    ctx = MapContext(key=key, spec=None, rgb=None, fit=None)  # type: ignore[arg-type]
    (tmp_path / "m" / "settle").mkdir(parents=True)
    (tmp_path / "m" / "adversary").mkdir(parents=True)
    (tmp_path / "m" / "resettle").mkdir(parents=True)
    settled = {
        "e:1,1:west": {
            "value": "w",
            "reason": "a",
            "job": "j",
            "kind": "edge",
            "readings": [["the classifier", "wall"]],
            "category": "settled",
        },
        "e:2,1:west": {
            "value": "o",
            "reason": "b",
            "job": "j",
            "kind": "edge",
            "readings": [["the classifier", "open"]],
            "category": "settled",
        },
    }
    adversary = {
        "e:1,1:west": {
            "value": "o",
            "reason": "c",
            "job": "k",
            "kind": "edge",
            "readings": [],
            "challenged": True,
            "recorded": "w (wall)",
            "category": "settled",
        },
        "e:2,1:west": {
            "value": "o",
            "reason": "d",
            "job": "k",
            "kind": "edge",
            "readings": [],
            "challenged": False,
            "recorded": "o (open)",
            "category": "settled",
        },
        "e:3,1:west": {
            "value": "w",
            "reason": "e",
            "job": "k",
            "kind": "edge",
            "readings": [],
            "challenged": True,
            "recorded": "o (open)",
            "category": "cave",
        },
    }
    resettled = {
        "e:1,1:west": {"value": "w", "reason": "f", "job": "r", "kind": "edge", "readings": []},
        "e:3,1:west": {"value": "w", "reason": "g", "job": "r", "kind": "edge", "readings": []},
    }
    (tmp_path / "m" / "settle" / "calls.json").write_text(json.dumps(settled))
    (tmp_path / "m" / "adversary" / "calls.json").write_text(json.dumps(adversary))
    (tmp_path / "m" / "resettle" / "calls.json").write_text(json.dumps(resettled))
    final = settler.final_values(ctx)
    assert final["e:1,1:west"]["value"] == "w" and final["e:1,1:west"]["challenged"] is True
    (tmp_path / "m" / "recheck").mkdir(parents=True)
    (tmp_path / "m" / "recheck" / "calls.json").write_text(
        json.dumps({"e:2,1:west": {"value": "d", "reason": "owner", "job": "q", "kind": "edge", "readings": []}})
    )
    rechecked = settler.final_values(ctx)
    assert rechecked["e:2,1:west"]["value"] == "d" and rechecked["e:2,1:west"]["before_recheck"] == "o"
    assert rechecked["e:1,1:west"]["value"] == "w"
    assert final["e:1,1:west"]["adversarial"] == "o" and final["e:1,1:west"]["settled"] == "w"
    assert final["e:2,1:west"]["value"] == "o" and final["e:2,1:west"]["challenged"] is False
    # An agreed edge the adversary challenged and the re-settling upheld the challenge on: the key changes.
    assert final["e:3,1:west"]["value"] == "w" and final["e:3,1:west"]["settled"] is None
    assert final["e:3,1:west"]["category"] == "cave"


# --- the assembly ------------------------------------------------------------------------------------------


def test_rects_cover_cells_exactly():
    cells = {(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (5, 1), (0, 2)}
    rects = assemble.rects_of(cells)
    covered = {(x, y) for rx, ry, w, h in rects for x in range(rx, rx + w) for y in range(ry, ry + h)}
    assert covered == cells
    assert (0, 0, 3, 2) in rects


def test_final_edges_derive_walls_and_blanks_from_the_settled_cells():
    items = readings.Items(width=2, height=1)
    items.cells = {
        "0,0": {"classifier": ".", "claude": ["."], "status": "agreed"},
        "1,0": {"classifier": ".", "claude": ["#"], "status": "disputed"},
    }
    items.edges = {
        "0,0:west": {"classifier": "w", "claude": ["w"], "status": "agreed"},
        "1,0:west": {"classifier": "o", "claude": ["o"], "status": "agreed"},
        "1,0:east": {"classifier": "w", "claude": ["w"], "status": "agreed"},
        "0,0:north": {"classifier": "w", "claude": ["w"], "status": "agreed"},
        "1,0:north": {"classifier": "w", "claude": ["w"], "status": "agreed"},
        "0,0:south": {"classifier": "w", "claude": ["w"], "status": "agreed"},
        "1,0:south": {"classifier": "w", "claude": ["w"], "status": "agreed"},
    }
    final = {"c:1,0": {"value": "#"}}
    log = {}
    cells = letters.final_cells(items, final, log)
    assert cells == [[".", "#"]]
    edges = letters.final_edges(items, cells, final, log)
    assert edges == EdgeGrids(vertical=("ww-",), horizontal=("w-", "w-"))
    assert log["edges_derived_as_walls"] == 1


def test_shell_dungeons_come_from_the_survey():
    specs = {spec.id: spec for spec in load_key_specs(TOOL / "keys.json")}
    dungeons = assemble._shell_dungeons(specs["jn1-p038"])
    assert len(dungeons) == 14
    assert all(level.verdict == "fallback" for dungeon in dungeons for level in dungeon.levels)


# --- the sample ----------------------------------------------------------------------------------------------


def test_sample_reading_values_use_the_truth_letters():
    assert samples._reading_value("the classifier", "wall") == "w"
    assert samples._reading_value("Clef", "open (p=0.8)") == "o"
    assert samples._reading_value("Claude's window read", "floor") == "."


# --- the synthetic answers as a smoke test of the trivial trace ----------------------------------------------


def test_trivial_trace_of_an_exact_answer_scores_perfect_on_the_reading_families():
    from tools.answer_keys.scoring import trivial_trace

    from osrforge.tracing.score import score_trace

    answer = load_geometry_truth(ASSETS / "synthetic-maps" / "manor" / "geometry.json")
    trace = trivial_trace(answer)
    score = score_trace(trace, answer)
    assert score.after.floor.perfect and score.after.edges.perfect and score.after.doors.perfect


def test_the_calls_around_a_cave_mouth_are_always_checked():
    items = readings.Items(width=12, height=12)
    items.symbols = [
        {
            "reading1": {"kind": "cave_mouth", "cells": [], "edge": "5,5:west", "direction": None},
            "reading2": None,
            "status": "disputed",
        },
        {"reading1": {"kind": "pillar", "cells": ["9,9"], "edge": None, "direction": None}, "reading2": None},
    ]
    cells = settler._mouth_cells(items)
    assert {"4,5", "5,5", "3,4", "6,6"} <= cells and "9,9" not in cells and "8,8" not in cells
    assert settler._touches("e:5,5:west", cells) and settler._touches("c:6,4", cells)
    assert settler._touches("s:5,5|0", cells) and not settler._touches("c:9,9", cells)
    assert not settler._touches("s:9,9|1", cells)


def test_slope_answers_are_checked_and_bound_an_outline():
    assert settler.check_slope("none", [0, 0, 5, 5]) is None
    assert settler.check_slope("2,0;2,1;3,2;3,3;3,4", [0, 0, 5, 5]) is None
    assert settler.check_slope("2,0;2,2", [0, 0, 5, 5]) == "cells 2,0 and 2,2 don't touch"
    assert settler.check_slope("2,0;9,1", [0, 0, 5, 5]) == "cells 2,0 and 9,1 don't touch"
    assert settler.check_slope("2,0", [0, 0, 5, 5]) is not None
    assert settler.check_slope("x;y", [0, 0, 5, 5]) is not None
    assert settler.slope_paths("2,0;2,1|4,4;4,3") == [[(2, 0), (2, 1)], [(4, 4), (4, 3)]]
    owned = {(x, y) for x in range(5) for y in range(5)}
    path = [(2, 0), (2, 1), (3, 2), (3, 3), (3, 4)]
    side = letters.slope_side(owned, [(0, 0)], [path])
    assert (0, 0) in side and (1, 4) in side and (2, 2) in side and (2, 0) in side and (3, 4) in side
    assert (4, 0) not in side and (3, 1) not in side and (4, 4) not in side
    # A slope cell that no edge joins to the ground, such as one beyond a wall, isn't the level's.
    walled = letters.slope_side(owned, [(0, 0)], [path], lambda a, b: a != (2, 1) and b != (2, 1))
    assert (2, 1) not in walled and (2, 0) in walled and (3, 2) in walled
    # No slope, or a seed off the outline, leaves the outline whole.
    assert letters.slope_side(owned, [(0, 0)], []) == owned
    assert letters.slope_side(owned, [(9, 9)], [path]) == owned
    # A slope cell joined to the ground only through other slope cells, as where the line runs along a corridor, is
    # the level's.
    corridor = letters.slope_side(owned, [(0, 0)], [path], lambda a, b: {a, b} != {(1, 0), (2, 0)})
    assert (2, 0) in corridor and (2, 1) in corridor
    # A line stopped short of the edge is carried on straight at both ends to the panel's edge.
    assert letters.extend_path([(2, 1), (2, 2)], 5, 5) == [(2, 1), (2, 2), (2, 0), (2, 3), (2, 4)]
    assert letters.extend_path([(1, 1), (2, 2)], 4, 4) == [(1, 1), (2, 2), (0, 0), (3, 3)]
    # An end whose last step runs along an edge of the line's box isn't carried on along that edge.
    assert letters.extend_path([(1, 3), (2, 2), (3, 2)], 6, 6, (1, 2, 4, 3)) == [(1, 3), (2, 2), (3, 2), (0, 4)]
    assert letters.extend_path([(2, 3), (2, 2)], 6, 6, (2, 2, 3, 3)) == [(2, 3), (2, 2)]
    assert letters.outline_box([[1, 2, 2, 1], [0, 3, 1, 2]]) == (0, 2, 3, 3)


# --- the recheck after the owner's sample --------------------------------------------------------------------


def test_whole_cell_floor_shares_follow_the_half_rule_on_the_synthetic_manor(manor_context):
    from tools.answer_keys.classify import cell_floor_shares
    from tools.answer_keys.run import map_materials

    ctx = manor_context
    shares = cell_floor_shares(map_materials(ctx.spec, ctx.rgb, ctx.fit), ctx.fit)
    answer = load_geometry_truth(ASSETS / "synthetic-maps" / "manor" / "geometry.json")
    exact = np.array([[letter == "." for letter in row] for row in answer.panels[0].cells])
    assert shares.shape == exact.shape
    assert ((shares >= 0.5) == exact).sum() >= exact.size - 2


def test_pixel_count_text_gives_the_cell_and_its_neighbours_in_percent():
    from tools.answer_keys.recheck import pixel_count_text

    shares = np.array([[1.0, 0.25, 0.0], [0.98, 0.454, 0.0], [0.33, 0.99, 0.53]])
    text = pixel_count_text(shares, (1, 1))
    assert text.startswith("45% of cell 1,1 is floor")
    assert text.endswith("from the north-west corner 0,0: 100 25 0, 98 45 0, 33 99 53")
    assert "off the map off the map off the map" in pixel_count_text(shares, (0, 0))


def test_a_recheck_sample_id_names_the_same_call():
    from tools.answer_keys.recheck import RECHECK_MARK, sample_call_id

    assert sample_call_id("m1:c:2,3") == ("m1", "c:2,3")
    assert sample_call_id(f"m1:c:2,3{RECHECK_MARK}") == ("m1", "c:2,3")
    assert sample_call_id(f"m:e:9,13:west{RECHECK_MARK}") == ("m", "e:9,13:west")
    assert sample_call_id("p:m:23,36|39") == ("p", "m:23,36|39")


def test_a_label_note_goes_with_the_edges_around_the_cells_its_readings_name():
    from tools.answer_keys.recheck import _label_edges

    call = {"readings": [["the first label read", "no such label"], ["the second label read", "T@5,6,5,7"]]}
    edges = _label_edges("m:5,6|3", call)
    assert {"5,6:west", "6,6:west", "5,6:north", "5,7:north", "5,7:west", "6,7:west", "5,8:north"} == set(edges)
    assert _label_edges("m:2,2|0", {}) == ["2,2:north", "2,2:west", "2,3:north", "3,2:west"]


def _owner_sample(tmp_path, verdict):
    from tools.answer_keys.sample import SampleCall, SampleSet

    call = SampleCall(
        id="m:c:1,1",
        member="x",
        map="m.png",
        panel="m",
        style="tsr_blue",
        kind="cell",
        category="diagonal",
        item="1,1",
        zoom="a.png",
        wide="b.png",
        readings=({"reader": "classifier", "value": "#"},),  # type: ignore[arg-type]
        settled="#",
    )
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps(SampleSet(calls=(call,)).model_dump(mode="json")))
    (tmp_path / "sample.verdicts.json").write_text(
        json.dumps({"verdicts": [{"call": "m:c:1,1", "settled": "#", "verdict": verdict, "note": "over half white"}]})
    )
    return sample


def test_the_owners_correction_of_a_wrong_call_is_final_over_the_recheck(tmp_path):
    from tools.answer_keys.recheck import changed_calls, record_corrections

    key = KeySpec(id="m", specs=tmp_path, member="x", style_note="x", work=tmp_path, key=tmp_path / "g.json")
    ctx = MapContext(key=key, spec=None, rgb=None, fit=None)  # type: ignore[arg-type]
    for stage in ("settle", "recheck"):
        (tmp_path / "m" / stage).mkdir(parents=True)
    settled = {"c:1,1": {"value": "#", "reason": "a", "job": "j", "kind": "cell", "readings": [], "category": "x"}}
    (tmp_path / "m" / "settle" / "calls.json").write_text(json.dumps(settled))
    (tmp_path / "m" / "recheck" / "calls.json").write_text(
        json.dumps({"c:1,1": {"value": "#", "reason": "b", "job": "q", "kind": "cell", "readings": []}})
    )
    assert changed_calls(ctx) == []
    with pytest.raises(ValueError, match="no wrong verdict"):
        record_corrections(ctx, _owner_sample(tmp_path, "unsure"), {"c:1,1": "."})
    sample = _owner_sample(tmp_path, "wrong")
    with pytest.raises(ValueError, match="isn't a cell letter"):
        record_corrections(ctx, sample, {"c:1,1": "d"})
    assert record_corrections(ctx, sample, {"c:1,1": "."}) == {"c:1,1": "."}
    final = settler.final_values(ctx)["c:1,1"]
    assert final["value"] == "." and final["before_owner"] == "#" and final["before_recheck"] == "#"
    assert final["owner_note"] == "over half white"
    assert changed_calls(ctx) == ["c:1,1"]


def test_the_recheck_prompt_doesnt_tell_the_settler_the_readers_disagree():
    assert "The readers disagree" in settler.SETTLE_SYSTEM
    assert "The readers disagree" not in settler.RECHECK_SYSTEM
    assert "checked afresh" in settler.RECHECK_SYSTEM
    for prompt in (settler.SETTLE_SYSTEM, settler.ADVERSARY_SYSTEM, settler.RECHECK_SYSTEM):
        assert "diagonal run of floor stay joined edge to edge" in prompt and "pixel count" in prompt


def test_a_slope_asked_again_supersedes_the_earlier_call():
    calls = {"l:0,0|a/1": {}, "l:0,0|a/1#2": {}, "l:5,5|b/1": {}, "l:9,9|c/1#2": {}, "l:9,9|c/1#10": {}}
    assert settler._superseded("l:0,0|a/1", calls)
    assert not settler._superseded("l:0,0|a/1#2", calls)
    assert not settler._superseded("l:5,5|b/1", calls)
    assert settler._superseded("l:9,9|c/1#2", calls) and not settler._superseded("l:9,9|c/1#10", calls)


def test_an_outline_item_lists_the_keys_printed_near_its_cell(tmp_path, monkeypatch):
    marks = {"a/1": [("1", "2,2"), ("2", "20,20")], "b/1": [("7", "5,3"), ("8", "4,4")]}
    monkeypatch.setattr(settler, "level_key_marks", lambda _ctx, _items: marks)
    text = settler.keys_near(None, None, (3, 3))  # type: ignore[arg-type]
    # Nearest first, and of equal distance the one first in north-then-west order.
    assert text == "1 at 2,2 (a/1); 8 at 4,4 (b/1); 7 at 5,3 (b/1)"
    assert settler.keys_near(None, None, (40, 40)) == "none within 8 cells"  # type: ignore[arg-type]
    item = {
        "id": "o:3,3|a/1",
        "text": "cell 3,3: which? Readings: the whole-map read read a.",
        "readings": [("x", "a")],
    }
    extended = settler.with_reading(item, "the survey's keys", text)
    assert extended["text"].endswith(f"Readings: x read a; the survey's keys read {text}.")


def test_a_job_that_supersedes_another_keeps_its_answers_over_every_collect(tmp_path):
    store = batches.BatchStore(tmp_path / "recheck")
    first = [{"id": "c:1,1", "kind": "cell", "readings": []}, {"id": "c:2,1", "kind": "cell", "readings": []}]
    store.add_job("m__recheck__000", {"model": "x"}, {"items": first})
    # The superseding job's id sorts first, so a collect reads its answer before the one it replaces.
    store.add_job("m__recheck_fix", {"model": "x"}, {"items": first[:1], "supersedes": ["m__recheck__000"]})
    store.answers.mkdir(parents=True)

    def answer(custom_id, rows):
        text = json.dumps({"items": [{"id": i, "answer": a, "reason": "r"} for i, a in rows]})
        message = {"content": [{"type": "text", "text": text}]}
        (store.answers / f"{custom_id}.json").write_text(json.dumps({"message": message, "stop_reason": "end_turn"}))

    answer("m__recheck__000", [("c:1,1", "."), ("c:2,1", "#")])
    answer("m__recheck_fix", [("c:1,1", "#")])
    calls: dict = {}
    for _ in range(3):
        settler._harvest(store, calls, False, {"calls": 0, "retries": 0})
        assert calls["c:1,1"]["value"] == "#" and calls["c:1,1"]["job"] == "m__recheck_fix"
        assert calls["c:2,1"]["value"] == "#" and calls["c:2,1"]["job"] == "m__recheck__000"


def test_a_call_on_an_edge_the_table_lacks_reaches_the_assembled_edges():
    items = readings.Items(width=2, height=1)
    items.cells = {
        "0,0": {"classifier": ".", "claude": ["."], "status": "agreed"},
        "1,0": {"classifier": "#", "claude": ["#"], "status": "agreed"},
    }
    final = {"c:1,0": {"value": "."}, "e:1,0:west": {"value": "d"}}
    log: dict = {}
    cells = letters.final_cells(items, final, log)
    edges = letters.final_edges(items, cells, final, log)
    assert cells == [[".", "."]]
    assert edges.at((0, 0), "east") == "d"
    assert edges.at((0, 0), "west") == "w" and edges.at((1, 0), "east") == "w"


def test_a_levels_printed_keys_count_once_and_only_for_its_own_level(tmp_path, monkeypatch):
    from osrforge.tracing.truth import Mark

    survey = {
        "dungeons": [
            {"id": "a", "levels": [{"number": 1, "areas": [{"key": "1"}, {"key": "2"}]}]},
            {"id": "b", "levels": [{"number": 1, "areas": [{"key": "3"}, {"key": "1"}]}]},
            {"id": "c", "levels": [{"number": 1, "areas": [{"key": "9"}]}]},
        ]
    }
    (tmp_path / "survey.json").write_text(json.dumps(survey))
    key = KeySpec(
        id="m",
        specs=tmp_path,
        member="x",
        style_note="x",
        work=tmp_path,
        key=tmp_path / "g.json",
        levels=(("a", 1), ("b", 1)),
        survey=tmp_path / "survey.json",
    )
    ctx = MapContext(key=key, spec=None, rgb=None, fit=None)  # type: ignore[arg-type]
    marks = [Mark(text="1", cell="0,0"), Mark(text="2/3", cell="1,1"), Mark(text="9", cell="2,2")]
    monkeypatch.setattr(assemble, "_final_marks", lambda *_args: marks)
    monkeypatch.setattr(readings, "load_items", lambda _ctx: None)
    # "1" names a key of both of the map's levels, so it counts for neither; "9" is a key of a level not on the map.
    assert letters.printed_key_cells(ctx, [], {}) == {"a/1": {(1, 1)}, "b/1": {(1, 1)}}
    assert letters.CROSSING == "opdls"


def test_a_cave_mouths_inner_cell_is_the_one_inside_the_levels_outline(monkeypatch):
    from osrforge.tracing.truth import Symbol

    symbols = [Symbol(kind="cave_mouth", edge="3,2:west"), Symbol(kind="pillar", cells=("1,1",))]
    monkeypatch.setattr(assemble, "_final_symbols", lambda *_args: symbols)
    monkeypatch.setattr(readings, "load_items", lambda _ctx: None)
    owned = {"a/1": {(0, 2), (1, 2), (2, 2)}, "b/1": {(3, 2), (4, 2)}, "c/1": {(2, 2), (3, 2)}}
    cells = [["."] * 5 for _ in range(4)]
    inner = letters.mouth_inner_cells(None, cells, {}, owned)  # type: ignore[arg-type]
    # The mouth between 2,2 and 3,2 is a's from inside 2,2 and b's from inside 3,2; c holds both sides.
    assert inner == {"a/1": [(2, 2)], "b/1": [(3, 2)]}


class _GridWorld:
    """Just enough of `world.World` for the join round's gap search, on one panel."""

    def __init__(self, rows: list[str], walls: set[tuple[int, int, str]] = frozenset()) -> None:  # type: ignore[assignment]
        self.rows = rows
        self.walls = set(walls)

    def across(self, cell, side):
        dx, dy = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}[side]
        x, y = cell[1] + dx, cell[2] + dy
        return ("p", x, y) if 0 <= y < len(self.rows) and 0 <= x < len(self.rows[0]) else None

    def edge(self, cell, side):
        other = self.across(cell, side)
        if (cell[1], cell[2], side) in self.walls or (
            other
            and (other[1], other[2], {"east": "west", "west": "east", "north": "south", "south": "north"}[side])
            in self.walls
        ):
            return "w"
        return "o" if other and self.is_floor(cell) and self.is_floor(other) else "w"

    def is_floor(self, cell):
        return self.rows[cell[2]][cell[1]] == "."

    def is_water(self, cell):
        return False

    def cell(self, cell):
        return (cell[1], cell[2])

    def order(self, cell):
        return (0, cell[2], cell[1])


def test_a_join_gap_is_the_cheapest_way_through_rock_and_walls():
    from tools.answer_keys import joins

    world = _GridWorld(["..#..", "#####"])
    floor = {("p", x, 0) for x in (0, 1, 3, 4)}
    one, other = {("p", 0, 0), ("p", 1, 0)}, {("p", 3, 0), ("p", 4, 0)}
    gap = joins._gap(world, ("d", 1), floor, one, other, ("1", "2"), "passage")
    assert (gap.cost, gap.cells, gap.edges) == (1, [(2, 0)], [])
    walled = _GridWorld(["....."], {(1, 0, "east")})
    floor = {("p", x, 0) for x in range(5)}
    gap = joins._gap(walled, ("d", 1), floor, {("p", 0, 0)}, {("p", 4, 0)}, ("1", "2"), "door")
    assert (gap.cost, gap.cells, gap.edges) == (1, [], ["2,0:west"])
    # Rock wider than the cap leaves the join as the key has it.
    far = _GridWorld(["." + "#" * 7 + "."])
    gap = joins._gap(far, ("d", 1), {("p", 0, 0), ("p", 8, 0)}, {("p", 0, 0)}, {("p", 8, 0)}, ("1", "2"), "passage")
    assert gap.cost is None and gap.cells == []


def test_a_dungeons_letter_at_a_cave_mouth_puts_the_mouths_inner_cell_in_its_outline(tmp_path, monkeypatch):
    from osrforge.tracing.truth import Mark, Symbol

    survey = {
        "dungeons": [
            {"id": "orc-b", "name": "Orc Lair B", "levels": [{"number": 1, "areas": [{"key": "11"}]}]},
            {"id": "cave", "name": "Cave of Horrors", "levels": [{"number": 1, "areas": [{"key": "c1"}]}]},
        ]
    }
    (tmp_path / "survey.json").write_text(json.dumps(survey))
    key = KeySpec(
        id="m",
        specs=tmp_path,
        member="x",
        style_note="x",
        work=tmp_path,
        key=tmp_path / "g.json",
        levels=(("orc-b", 1), ("cave", 1)),
        survey=tmp_path / "survey.json",
    )
    ctx = MapContext(key=key, spec=None, rgb=None, fit=None)  # type: ignore[arg-type]
    symbols = [Symbol(kind="cave_mouth", edge="5,5:west"), Symbol(kind="cave_mouth", edge="1,1:north")]
    # "b" names a word of Orc Lair B, ignoring case; it stands east of the mouth between 4,5 and 5,5, so the inner
    # cell is 4,5. "of" is a word of Cave of Horrors but no letter; "C" names no word.
    marks = [Mark(text="b", cell="6,5"), Mark(text="of", cell="1,2"), Mark(text="C", cell="1,0")]
    monkeypatch.setattr(assemble, "_final_symbols", lambda *_args: symbols)
    monkeypatch.setattr(assemble, "_final_marks", lambda *_args: marks)
    monkeypatch.setattr(readings, "load_items", lambda _ctx: None)
    cells = [["."] * 8 for _ in range(8)]
    assert letters.lettered_mouths(ctx, cells, {}) == {"orc-b/1": {(4, 5)}}


def test_supersession_runs_through_a_chain_of_jobs(tmp_path):
    store = batches.BatchStore(tmp_path / "recheck")
    item = [{"id": "c:1,1", "kind": "cell", "readings": []}]
    store.add_job("m__a", {"model": "x"}, {"items": item})
    store.add_job("m__c", {"model": "x"}, {"items": item, "supersedes": ["m__b"]})
    store.add_job("m__b", {"model": "x"}, {"items": item, "supersedes": ["m__a"]})
    store.answers.mkdir(parents=True)
    for custom_id, value in (("m__a", "#"), ("m__b", "."), ("m__c", "~")):
        text = json.dumps({"items": [{"id": "c:1,1", "answer": value, "reason": "r"}]})
        message = {"content": [{"type": "text", "text": text}]}
        (store.answers / f"{custom_id}.json").write_text(json.dumps({"message": message, "stop_reason": "end_turn"}))
    calls: dict = {}
    settler._harvest(store, calls, False, {"calls": 0, "retries": 0})
    assert calls["c:1,1"]["value"] == "~"
    again = {"calls": 0, "retries": 0}
    settler._harvest(store, calls, False, again)
    assert again["calls"] == 0 and calls["c:1,1"]["value"] == "~"


# --- passages narrower than a cell ---------------------------------------------------------------------------------


def test_the_widest_white_path_measures_a_passage_in_any_direction():
    from tools.answer_keys.passages import clearance, widest_path

    band = np.zeros((40, 60), dtype=bool)
    band[10:15, :] = True
    assert clearance(band)[12, 30] == 3.0  # five samples wide: 2 * 3 - 1
    rows, columns = np.mgrid[0:60, 0:60]
    diagonal = np.abs(rows - columns) / np.sqrt(2) <= 3.0
    assert abs((2 * clearance(diagonal)[30, 30] - 1) - 6.36) < 0.5
    # Two rooms joined by a neck three samples wide and one seven wide: the widest path keeps clearance 4.
    rooms = np.zeros((30, 60), dtype=bool)
    rooms[:, :15] = rooms[:, 45:] = True
    rooms[4:7, 15:45] = True
    rooms[18:25, 15:45] = True
    left, right = np.zeros_like(rooms), np.zeros_like(rooms)
    left[:, :15] = right[:, 45:] = True
    depth, path = widest_path(rooms, left & rooms, right & rooms)
    assert depth == 4.0 and all(18 <= y < 25 for y, x in path if 15 <= x < 45)
    rooms[18:25, 30] = rooms[4:7, 30] = False
    assert widest_path(rooms, left & rooms, right & rooms) == (0, [])
