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
    # No slope, or a seed off the outline, leaves the outline whole.
    assert letters.slope_side(owned, [(0, 0)], []) == owned
    assert letters.slope_side(owned, [(9, 9)], [path]) == owned
    # A line stopped short of the edge is carried on straight at both ends.
    assert letters.extend_path([(2, 1), (2, 2)], 5, 5) == [(2, 1), (2, 2), (2, 0), (2, 3), (2, 4)]
    assert letters.extend_path([(1, 1), (2, 2)], 4, 4) == [(1, 1), (2, 2), (0, 0), (3, 3)]
