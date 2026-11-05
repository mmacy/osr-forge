"""The figures behind the README's findings, on the committed Chaotic Caves keys and made-up answers, offline."""

import json
from pathlib import Path

import numpy as np
from tools.reading.answers import Answers
from tools.reading.maps import key_panel, load_maps
from tools.reading.marks import mark_reading, one_mark_per_key
from tools.reading.screen_backends import backend_tally, fewest_open_blocked
from tools.reading.screen_cells import clef_tally, ink_tally
from tools.reading.screen_letters import answered_windows, letter_tally
from tools.reading.screen_summary import ledger_counts, summarise
from tools.reading.windows import Window, window_edges

from osrforge.tracing.truth import Panel, parse_cell

TOOL = Path(__file__).resolve().parent.parent
MAPS = {screen.id: screen for screen in load_maps(TOOL / "maps.json")}


def _key_answers(panel: Panel, edges: list[tuple[tuple[int, int], str]]) -> Answers:
    return Answers(edges={edge: {panel.edges.at(*edge): 1.0} for edge in edges}, cells={})


def _key_floors(panel: Panel) -> np.ndarray:
    return np.array([[1.0 if cell == "." else 0.0 for cell in row] for row in panel.cells])


def test_the_letters_count_each_wrong_answer_by_what_it_names_instead() -> None:
    panel = key_panel(MAPS["jn1-p047"])
    edges = window_edges(panel, [Window("jn1-p047", 0, 0, 12, 12)])
    key = _key_answers(panel, edges)
    door = next(edge for edge in edges if panel.edges.at(*edge) == "d")
    opened = Answers(edges=key.edges | {door: {"o": 0.9, "d": 0.1}}, cells={})
    tallies = letter_tally(panel, {"key": key, "opened": opened}, edges)
    assert tallies["key"]["edges"] == len(edges)
    assert all(tallies["key"][f"{letter} right"] == tallies["key"][f"{letter} edges"] for letter in "owdn")
    assert tallies["key"]["walls read as open"] == 0 and tallies["key"]["open read as blocked"] == 0
    assert tallies["opened"]["d read as o"] == 1 and tallies["opened"]["walls read as open"] == 1
    assert tallies["opened"]["d right"] == tallies["key"]["d right"] - 1


def test_a_window_counts_only_when_every_source_answered_all_of_it() -> None:
    panel = key_panel(MAPS["jn1-p047"])
    left, right = Window("jn1-p047", 0, 0, 8, 16), Window("jn1-p047", 8, 0, 8, 16)
    full = _key_answers(panel, window_edges(panel, [left, right]))
    missing = next(iter(window_edges(panel, [right])))
    partial = Answers(edges={edge: p for edge, p in full.edges.items() if edge != missing}, cells={})
    assert answered_windows(panel, [left, right], {"full": full, "partial": partial}) == [left]


def test_the_ink_tally_counts_a_settled_error_until_the_cell_is_left_unsettled() -> None:
    panel = key_panel(MAPS["jn1-p046"])
    floors = _key_floors(panel)
    tally = ink_tally(panel, floors, set())
    assert tally["whole cells"] + tally["partial cells"] == panel.width * panel.height
    assert tally["whole right"] == tally["whole cells"] and tally["whole settled wrong"] == 0
    assert tally["water cells"] == 82 and tally["water unsettled"] == 0
    partial = {parse_cell(text) for text in panel.partial}
    x, y = next(
        (x, y)
        for y in range(panel.height)
        for x in range(panel.width)
        if panel.cells[y][x] == "." and (x, y) not in partial
    )
    floors[y, x] = 0.2
    assert ink_tally(panel, floors, set())["whole settled wrong"] == 1
    unsettled = ink_tally(panel, floors, {(x, y)})
    assert unsettled["whole settled wrong"] == 0 and unsettled["whole unsettled"] == 1


def test_the_cell_tally_names_symbols_water_and_floor_called_a_symbol() -> None:
    panel = key_panel(MAPS["jn1-p046"])
    symbols = {parse_cell(cell): symbol.kind for symbol in panel.symbols for cell in symbol.cells}
    stairs = next(cell for cell, kind in symbols.items() if kind == "stairs")
    cells = [(x, y) for y in range(panel.height) for x in range(panel.width)]
    floor = next(cell for cell in cells if panel.cells[cell[1]][cell[0]] == "." and cell not in symbols)
    water = next(cell for cell in cells if panel.cells[cell[1]][cell[0]] == "~")
    rock = next(cell for cell in cells if panel.cells[cell[1]][cell[0]] not in ".~")
    answers = {stairs: {"stairs": 0.8, "floor": 0.2}, floor: {"pillar": 0.6, "floor": 0.4}, water: {"rock": 1.0}}
    tally = clef_tally(panel, answers | {rock: {"rock": 1.0}}, _key_floors(panel))
    assert tally["asked"] == 4
    assert tally["floor or not right, Clef"] == 4 and tally["floor or not right, the ink"] == 4
    assert (
        tally["stairs cells"] == 1 and tally["stairs named its kind"] == 1 and tally["symbol cells named a symbol"] == 1
    )
    assert tally["plain floor called a symbol"] == 1
    assert tally["water cells"] == 1 and tally["water read as water"] == 0


def test_fewest_open_blocked_takes_the_best_threshold_within_the_walls_allowed() -> None:
    passable = np.array([True, True, True, False, False])
    p = np.array([0.9, 0.6, 0.3, 0.7, 0.2])
    assert fewest_open_blocked(passable, p, 0) == 2
    assert fewest_open_blocked(passable, p, 1) == 0


def test_backends_agree_on_every_edge_but_the_one_they_answer_differently() -> None:
    panel = key_panel(MAPS["jn1-p047"])
    edges = window_edges(panel, [Window("jn1-p047", 0, 0, 16, 16)])
    key = _key_answers(panel, edges)
    wall = next(edge for edge in edges if panel.edges.at(*edge) == "w")
    other = Answers(edges=key.edges | {wall: {"o": 1.0}}, cells={})
    agree, letters, rows = backend_tally(panel, {"a": key, "b": other})
    assert agree["a = b edges"] == len(edges) and agree["a = b"] == len(edges) - 1
    assert letters["b"]["w right"] == letters["a"]["w right"] - 1
    assert len(rows["a"]) == len(rows["b"]) and all(passable == (p == 1.0) for passable, p in rows["a"])


def test_a_marks_reading_is_its_most_probable_key_or_else_its_letter() -> None:
    chunks = [{"key:1": 0.3, "none": 0.7}, {"key:2": 0.6, "none": 0.4}]
    assert mark_reading(chunks, None) == ("key:2", 0.6)
    assert mark_reading([{"key:1": 0.2, "none": 0.8}], {"letter:a": 0.7, "none": 0.3}) == ("letter:a", 0.7)
    assert mark_reading([{"key:1": 0.2, "none": 0.8}], None) == ("none", 0.0)


def test_a_key_keeps_only_its_most_probable_mark() -> None:
    readings = {
        0: ("key:12", 0.6),
        1: ("key:12", 0.9),
        2: ("key:7", 0.5),
        3: ("letter:a", 0.8),
        4: ("none", 0.0),
        5: ("key:7", 0.5),
    }
    assert one_mark_per_key(readings) == {0: "none", 1: "key:12", 2: "key:7", 3: "letter:a", 4: "none", 5: "none"}


def test_the_summary_scores_the_first_ten_items_the_choosing_decision_and_symbols_by_kind(tmp_path: Path) -> None:
    items = {
        "m": [
            {"cell": [0, 0], "side": "north", "key": "w", "clef": 0.4},
            {"cell": [1, 1], "side": None, "key": ".", "clef": 0.6},
        ]
    }
    (tmp_path / "items").mkdir()
    (tmp_path / "items" / "items.json").write_text(json.dumps(items))
    attempt = {"retry": False, "seconds": 1.0, "usage": {"output_tokens": 7}, "cost_usd": 0.01, "answer": {}}
    record = {"attempts": [attempt], "malformed_first": False, "cost_usd": 0.01}
    folder = tmp_path / "items" / "claude-opus-5-5" / "medium"
    folder.mkdir(parents=True)
    answer = {"answers": [{"item": 1, "value": "open"}, {"item": 2, "value": "floor"}]}
    (folder / "m.0.json").write_text(json.dumps(record | {"answer": answer}))
    pages = tmp_path / "wholemap" / "claude-opus-5-5" / "medium"
    pages.mkdir(parents=True)
    (pages / "p.json").write_text(json.dumps(record | {"answer": {}}))
    by_kind = {"stairs": [2, 2], "pit": [1, 0]}
    symbols = {"r": {"key": 3, "found": 2, "read": 2, "extra": 1, "direction_right": 2, "by_kind": by_kind}}
    (pages / "p.score.json").write_text(json.dumps({"regions": {}, "levels": {}, "symbols": symbols}))
    summary = summarise(tmp_path)
    assert summary["claude-opus-5-5 medium"]["items"]["first ten right"] == 1
    assert summary["calibrated Clef and ink"]["items"] == {
        "edges": 1,
        "edges right": 1,
        "cells": 1,
        "cells right": 1,
        "first ten": 2,
        "first ten right": 2,
    }
    wholemap = summary["claude-opus-5-5 medium"]["wholemap"]
    assert wholemap["symbols_found"] == "2/3" and wholemap["symbols_by_kind"] == {"pit": "0/1", "stairs": "2/2"}


def test_the_ledger_count_takes_the_reading_entries_and_the_ones_charged_nothing(tmp_path: Path) -> None:
    entries = [
        {"label": "reading-items medium", "cost_usd": 0.01},
        {"label": "reading-wholemap low", "cost_usd": 0.0},
        {"label": "answer-keys", "cost_usd": 0.0},
    ]
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"stop_usd": 1.0, "spent_usd": 0.01, "entries": entries}))
    assert ledger_counts(ledger) == {"entries": 2, "charged nothing": 1}
