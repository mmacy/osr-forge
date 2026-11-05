"""The stage-like window reading and the summary of stored outcomes, offline."""

import json
from pathlib import Path

import numpy as np
from tools.reading.maps import key_document, key_panel, load_maps
from tools.reading.screen_summary import summarise
from tools.reading.stage_reading import patched_window_panel, stage_cells, stage_letters
from tools.reading.traces import score_panel
from tools.reading.windows import Window

TOOL = Path(__file__).resolve().parent.parent
MAPS = {screen.id: screen for screen in load_maps(TOOL / "maps.json")}


def test_unsettled_cells_take_clef_and_the_rest_take_the_ink() -> None:
    panel = key_panel(MAPS["jn1-p047"])
    floors = np.ones((panel.height, panel.width))
    floors[0, 0] = 0.1
    window = [Window("jn1-p047", 0, 0, 2, 2)]
    cells = stage_cells(panel, floors, {(1, 1)}, {(1, 1): {"floor": 0.2, "water": 0.7, "rock": 0.1}}, window)
    assert cells[0][0] == "#" and cells[1][1] == "~" and cells[0][1] == "."
    assert cells[5][5] == panel.cells[5][5]


def test_edges_follow_the_threshold_between_floor_and_are_walls_against_rock() -> None:
    panel = key_panel(MAPS["jn1-p047"])
    cells = [["."] * panel.width for _ in range(panel.height)]
    cells[0][1] = "#"
    window = [Window("jn1-p047", 0, 0, 2, 2)]
    probabilities = {((1, 1), "west"): {"o": 0.7, "w": 0.3}, ((1, 0), "west"): {"o": 0.2, "w": 0.1, "d": 0.7}}
    letters = stage_letters(panel, cells, window, probabilities, lambda edge: 0.7, threshold=0.5)
    assert letters[((1, 1), "west")] == "o"
    assert letters[((0, 0), "west")] == "w"
    assert letters[((1, 0), "west")] == "d"
    assert letters[((1, 1), "north")] == "w"
    assert letters[((1, 0), "north")] == "-"
    strict = stage_letters(panel, cells, window, probabilities, lambda edge: 0.7, threshold=0.8)
    assert strict[((1, 1), "west")] == "w"


def test_the_key_put_back_into_its_windows_scores_perfect() -> None:
    screen = MAPS["jn1-p047"]
    panel = key_panel(screen)
    letters = {edge: panel.edges.at(*edge) for edge in panel.edges.positions()}
    cells = [list(row) for row in panel.cells]
    every, _whole = score_panel(key_document(screen.key), patched_window_panel(panel, cells, letters))
    assert every.edges.all.right == every.edges.all.total and every.floor.perfect


def test_the_summary_counts_items_and_offset_picks(tmp_path: Path) -> None:
    items = {
        "m": [
            {"cell": [0, 0], "side": "north", "key": "w", "clef": 0.4},
            {"cell": [1, 1], "side": None, "key": ".", "clef": 0.6},
        ]
    }
    (tmp_path / "items").mkdir()
    (tmp_path / "items" / "items.json").write_text(json.dumps(items))
    attempt = {
        "retry": False,
        "seconds": 1.0,
        "usage": {"output_tokens": 7},
        "cost_usd": 0.01,
        "answer": {},
        "error": None,
        "faults": [],
    }
    record = {"attempts": [attempt], "malformed_first": False, "cost_usd": 0.01}
    folder = tmp_path / "items" / "claude-opus-5-5" / "medium"
    folder.mkdir(parents=True)
    answer = {"answers": [{"item": 1, "value": "open"}, {"item": 2, "value": "floor"}]}
    (folder / "m.0.json").write_text(json.dumps(record | {"answer": answer}))
    offsets = tmp_path / "offset" / "claude-opus-5-5" / "medium"
    offsets.mkdir(parents=True)
    (offsets / "real.json").write_text(json.dumps(record | {"answer": {"choice": "A"}, "truth": "B", "real": True}))
    (offsets / "fold.json").write_text(json.dumps(record | {"answer": {"choice": "A"}, "truth": "A"}))
    summary = summarise(tmp_path)["claude-opus-5-5 medium"]
    assert summary["items"]["edges right"] == 0 and summary["items"]["cells right"] == 1
    assert summary["offset"]["real_right"] == "0/1" and summary["offset"]["real_wrong_pick"] == 1
    assert summary["offset"]["synthetic_right"] == "1/1"


def test_exported_requests_rebuild_with_their_keys_and_move_by_the_frame(tmp_path: Path) -> None:
    import io

    from PIL import Image
    from tools.reading.hosted_exported import exported_requests

    from osrforge.providers.decisions import DecisionRequest

    folder = tmp_path / "clef" / "requests"
    folder.mkdir(parents=True)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    (folder / "3-7_west.png").write_bytes(buffer.getvalue())
    options = [["open", "a grid line"], ["wall", "a heavy line"]]
    request = DecisionRequest(
        tag="edge",
        image=buffer.getvalue(),
        state="s",
        question="q",
        options=tuple((o, t) for o, t in options),
        model="clef",
    )
    lines = [
        {
            "item": "3,7:west",
            "kind": "edge",
            "png": "3-7_west.png",
            "state": "s",
            "question": "q",
            "options": options,
            "key": request.key(),
        },
        {
            "item": "3,1:west",
            "kind": "edge",
            "png": "3-7_west.png",
            "state": "s",
            "question": "q",
            "options": options,
            "key": "x",
        },
        {
            "item": "2,2",
            "kind": "cell",
            "png": "3-7_west.png",
            "state": "s",
            "question": "q",
            "options": options,
            "key": "y",
        },
    ]
    (folder / "requests.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    rebuilt = exported_requests(tmp_path, (0, 3))
    assert set(rebuilt) == {"3,4:west"}
    assert rebuilt["3,4:west"][0].key() == rebuilt["3,4:west"][1]
