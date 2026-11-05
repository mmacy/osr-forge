"""The screening's parts on the committed Chaotic Caves maps and on made-up answers, offline."""

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from tools.reading.answers import load_answers, passable_probability
from tools.reading.calibrate import Logistic, decision_errors, expected_calibration_error, held_out, ranked_share
from tools.reading.claude_run import Outcome, RequestCap, ask
from tools.reading.clefask import SEVEN, AnswerStore, Item, cell_crop, edge_crop, run_items
from tools.reading.edges import decide, measured_edges
from tools.reading.ink import INK_STYLES, MapInk
from tools.reading.items import AskedItem, check_items, items_kind, items_request
from tools.reading.maps import key_document, key_panel, key_region, load_maps, load_rgb
from tools.reading.marks import _label, find_marks
from tools.reading.offsets import HalfPairInput, join_picture, letters_for
from tools.reading.wholemap import PageRegion, check_whole_map, score_whole_map

from osrforge.providers.decisions import DecisionRequest, DecisionResponse
from osrforge.providers.reading import ReadingResponse, ReadingUsage
from osrforge.tracing.truth import parse_cell

TOOL = Path(__file__).resolve().parent.parent
MAPS = {screen.id: screen for screen in load_maps(TOOL / "maps.json")}


def _red_pixels(data: bytes) -> int:
    array = np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)
    return int(((array[..., 0] > 200) & (array[..., 1] < 60) & (array[..., 2] < 60)).sum())


def test_crops_have_the_plan_sizes_and_a_red_box() -> None:
    screen = MAPS["jn1-p047"]
    rgb, registration = load_rgb(screen.image), key_region(screen).registration
    assert registration is not None
    edge = edge_crop(rgb, registration, (5, 6), "north")
    cell = cell_crop(rgb, registration, (5, 6))
    assert Image.open(io.BytesIO(edge)).size == (256, 256)
    assert Image.open(io.BytesIO(cell)).size == (288, 288)
    assert _red_pixels(edge) > 300 and _red_pixels(cell) > 300


def test_the_seven_option_set_asks_the_same_seven_options_in_every_style() -> None:
    ids = {tuple(option for option, _text in options) for _state, options in SEVEN.values()}
    assert ids == {("open", "printed_open_line", "wall", "window", "door", "locked_door", "secret_door")}


class _FakeClef:
    def __init__(self) -> None:
        self.asked: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        self.asked.append(request)
        return DecisionResponse(
            probabilities={"open": 0.75, "wall": 0.25}, model_id="fake", input_tokens=1, format="png", body_sha256="0"
        )


def test_run_items_asks_only_items_without_an_answer(tmp_path: Path) -> None:
    store = AnswerStore(tmp_path / "answers.jsonl")
    store.append({"item": "0,0:north", "kind": "edge", "probabilities": {"open": 1.0}})
    provider = _FakeClef()
    items = [Item("edge", "0,0:north", (0, 0), "north"), Item("edge", "1,0:north", (1, 0), "north")]

    def request_for(item: Item) -> DecisionRequest:
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), "white").save(buffer, "PNG")
        return DecisionRequest(
            tag="edge", image=buffer.getvalue(), state="s", question="q", options=(("open", None), ("wall", None))
        )

    assert run_items(provider, store, items, request_for) == 1  # type: ignore[arg-type]
    assert store.answered() == {"0,0:north", "1,0:north"}
    assert len(provider.asked) == 1


def test_answers_move_onto_the_key_grid_by_the_frame(tmp_path: Path) -> None:
    path = tmp_path / "answers.jsonl"
    lines = [
        {"item": "3,7:west", "kind": "edge", "probabilities": {"open": 0.9, "wall": 0.1}},
        {"item": "3,4:south", "kind": "edge", "probabilities": {"open": 0.2, "printed_open_line": 0.3, "wall": 0.5}},
        {"item": "3,1:north", "kind": "edge", "probabilities": {"open": 1.0}},
        {"item": "2,6", "kind": "cell", "probabilities": {"floor": 1.0}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    answers = load_answers(path, (0, 3), width=16, height=16)
    assert set(answers.edges) == {((3, 4), "west"), ((3, 2), "north")}
    assert answers.edges[((3, 2), "north")] == {"o": 0.2, "p": 0.3, "w": 0.5}
    assert passable_probability(answers.edges[((3, 2), "north")]) == pytest.approx(0.5)
    assert answers.cells == {(2, 3): {"floor": 1.0}}


def test_decide_takes_a_blocked_letter_below_the_threshold() -> None:
    probabilities = {"o": 0.5, "p": 0.1, "w": 0.15, "d": 0.25}
    assert decide(probabilities, 0.6, 0.5) == "o"
    assert decide(probabilities, 0.6, 0.7) == "d"


def test_logistic_and_held_out_separate_a_clean_signal() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(400, 1))
    y = (x[:, 0] + rng.normal(scale=0.2, size=400) > 0).astype(float)
    model = Logistic().fit(x, y)
    assert ((model.predict(x) >= 0.5) == y.astype(bool)).mean() > 0.9
    p = held_out(x, y, ["a" if i % 2 else "b" for i in range(400)])
    assert sum(decision_errors(y, p, 0.5)) < 40
    assert expected_calibration_error(y, p) < 0.1


def test_ranked_share_counts_the_decisions_before_the_first_wrong_one() -> None:
    y = np.array([1, 0, 1, 0])
    p = np.array([0.99, 0.02, 0.4, 0.6])
    assert ranked_share(y, p, 0.5) == (2, 4)


def test_the_ink_reads_the_upper_floor_and_its_walls() -> None:
    screen = MAPS["jn1-p047"]
    panel = key_panel(screen)
    registration = key_region(screen).registration
    assert registration is not None
    ink = MapInk(load_rgb(screen.image), registration, INK_STYLES["black_white"])
    floors = ink.floor_fractions()
    key_floor = np.array([[letter == "." for letter in row] for row in panel.cells])
    assert ((floors >= 0.5) == key_floor).mean() > 0.99
    heavy = {"o": [], "w": []}
    for edge in measured_edges(panel):
        letter = panel.edges.at(*edge)
        if letter in heavy:
            heavy[letter].append(ink.edge(*edge, floors).heavy)
    assert np.mean(heavy["w"]) > 0.7 > 0.1 > np.mean(heavy["o"])


def _answer_from_key(map_id: str) -> tuple[dict[str, Any], list[PageRegion]]:
    screen = MAPS[map_id]
    key = key_document(screen.key)
    region = key.region(map_id)
    panel = key_panel(screen)
    regions = [PageRegion("R1", map_id, region.columns, region.rows)]
    answer = {
        "regions": [
            {
                "region": "R1",
                "style": region.style,
                "plan_view": True,
                "scale_text": "1 square = 10 feet" if region.scale.printed else None,
                "feet_per_square": region.scale.feet,
                "compass_printed": region.compass.printed,
                "north": region.compass.north,
                "compass_angle": None,
                "cut_off": list(region.cut_off),
            }
        ],
        "legend_printed": region.legend.printed,
        "legend_symbols": list(region.legend.symbols),
        "levels": [
            {
                "dungeon": outline.dungeon,
                "level": outline.level,
                "region": "R1",
                "rects": [list(rect) for rect in outline.rects],
                "seeds": [list(parse_cell(seed)) for seed in outline.seeds],
                "open_ground": [list(rect) for rect in outline.open_ground],
            }
            for outline in panel.outlines
        ],
        "symbols": [
            {
                "region": "R1",
                "kind": symbol.kind,
                "cells": [list(parse_cell(cell)) for cell in symbol.cells],
                "side": "none",
                "direction": symbol.direction or "none",
            }
            for symbol in panel.symbols
            if symbol.kind != "cave_mouth"
        ],
        "notes": "",
    }
    return answer, regions


def test_a_whole_map_answer_made_from_the_key_checks_clean_and_scores_perfect() -> None:
    answer, regions = _answer_from_key("jn1-p046")
    levels = [(level["dungeon"], level["level"], []) for level in answer["levels"]]
    assert check_whole_map(answer, regions, levels) == []
    score = score_whole_map(answer, key_document(MAPS["jn1-p046"].key), regions, None)
    assert list(score["verdicts"]["wrong"]) == []
    assert all(level["outline_iou"] == 1.0 for level in score["levels"].values())
    assert score["symbols"]["jn1-p046"]["found"] == score["symbols"]["jn1-p046"]["key"]


def test_the_whole_map_check_names_a_missing_level_and_a_cell_off_the_grid() -> None:
    answer, regions = _answer_from_key("jn1-p047")
    levels = [(level["dungeon"], level["level"], []) for level in answer["levels"]] + [("ghost", 1, [])]
    answer["symbols"].append({"region": "R1", "kind": "pit", "cells": [[99, 0]], "side": "none", "direction": "none"})
    faults = check_whole_map(answer, regions, levels)
    assert any("ghost/1" in fault for fault in faults)
    assert any("[99, 0]" in fault for fault in faults)


def test_the_item_check_wants_each_item_once_with_a_reading_of_its_kind() -> None:
    items = [AskedItem("m", (0, 0), "north", "w"), AskedItem("m", (1, 1), None, ".")]
    assert check_items({"answers": [{"item": 1, "value": "wall"}, {"item": 2, "value": "floor"}]}, items) == []
    faults = check_items({"answers": [{"item": 1, "value": "floor"}, {"item": 3, "value": "rock"}]}, items)
    assert len(faults) == 3


def test_an_item_request_shares_the_page_and_asks_two_crops_an_item() -> None:
    screen = MAPS["jn1-p047"]
    rgb = load_rgb(screen.image)
    registration = key_region(screen).registration
    assert registration is not None
    items = [AskedItem("jn1-p047", (5, 6), "north", "o"), AskedItem("jn1-p047", (5, 6), None, ".")]
    request = items_request(items_kind("low"), rgb, rgb, registration, "black_white", items)
    assert len(request.shared) == 1
    assert sum(part.__class__.__name__ == "ImagePart" for part in request.question) == 4


class _FakeClaude:
    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.requests: list[Any] = []

        class Settings:
            model = "claude-sonnet-5-5"

        self.settings = Settings()

    def read(self, request: Any) -> ReadingResponse:
        self.requests.append(request)
        usage = ReadingUsage(input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0)
        return ReadingResponse(data=self.answers[len(self.requests) - 1], usage=usage, model_id="claude-sonnet-5-5")


def _cap(tmp_path: Path, entries: int, cap: int) -> RequestCap:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"entries": [{"label": "reading-x low", "cost_usd": 0.0}] * entries}))
    return RequestCap(ledger, cap)


def test_a_malformed_answer_is_asked_once_more_with_its_faults(tmp_path: Path) -> None:
    items = [AskedItem("m", (0, 0), "north", "w")]
    provider = _FakeClaude([{"answers": []}, {"answers": [{"item": 1, "value": "wall"}]}])
    screen = MAPS["jn1-p047"]
    rgb = load_rgb(screen.image)
    registration = key_region(screen).registration
    assert registration is not None
    request = items_request(items_kind("low"), rgb, rgb, registration, "black_white", items)
    outcome: Outcome = ask(provider, request, lambda answer: check_items(answer, items), _cap(tmp_path, 0, 5))  # type: ignore[arg-type]
    assert outcome.malformed_first and outcome.answer == {"answers": [{"item": 1, "value": "wall"}]}
    assert provider.requests[1].retry is not None and "item 1" in provider.requests[1].retry.fault


def test_the_cap_refuses_a_request_past_it(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _cap(tmp_path, 5, 5).check()


def test_join_pictures_widen_with_the_gap_and_letters_name_the_truth() -> None:
    first = np.full((60, 100, 3), 255, dtype=np.uint8)
    corners = np.stack(np.meshgrid(np.arange(0, 101, 10.0), np.arange(0, 61, 10.0)), axis=-1)
    pair = HalfPairInput(first, corners, first, corners, rows=0, gaps=(0.2, 1.2, 2.2))
    widths = [join_picture(pair, gap, "A").width for gap in pair.gaps]
    assert widths[1] - widths[0] == widths[2] - widths[1] == 10
    assert letters_for((3, 4, 5), 4) == "B" and letters_for((3, 4, 5), 9) == "none"


def test_labelling_finds_eight_connected_strokes() -> None:
    mask = np.zeros((6, 6), dtype=bool)
    mask[0, 0] = mask[1, 1] = True
    mask[4, 4] = mask[4, 5] = True
    _labels, count = _label(mask)
    assert count == 2


def test_the_marks_include_the_upper_floors_room_keys() -> None:
    screen = MAPS["jn1-p047"]
    registration = key_region(screen).registration
    assert registration is not None
    ink = MapInk(load_rgb(screen.image), registration, INK_STYLES["black_white"])
    found = [mark.cell for mark in find_marks(ink.darkness, ink.fill, np.array(registration.corners))]
    keys = [parse_cell(mark.cell) for mark in key_panel(screen).marks if mark.text.isdigit()]
    near = [k for k in keys if any(max(abs(k[0] - f[0]), abs(k[1] - f[1])) <= 1 for f in found)]
    assert len(near) == len(keys) == 9
