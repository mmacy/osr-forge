"""The answer-key classifier on the committed Chaotic Caves maps: the grid fits, the readings, and the settled windows.

Each test reads a map once (cached per session), so the whole module runs in about ten seconds.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from tools.answer_keys.reading import load_reading, save_reading, to_panel, to_region
from tools.answer_keys.run import read_map
from tools.answer_keys.score import load_settled, score_window
from tools.answer_keys.spec import load_specs

from osrforge.tracing.truth import Panel, Region

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
SPECS = {spec.id: spec for spec in load_specs(TOOL / "maps.json")}
SETTLED = sorted((TOOL / "settled").glob("*.json"))

EXPECTED_PITCH = {
    "jn1-p038": (30.0, 31.5),
    "jn1-p046": (60.0, 62.0),
    "jn1-p047": (74.0, 76.0),
    "jn1-p021-halfling": (43.5, 45.0),
    "jn1-p021-cave": (43.5, 45.0),
}

EDGE_FLOOR = {
    "jn1-p038_10-56": 0.97,
    "jn1-p038_26-26": 0.97,
    "jn1-p038_32-8": 0.99,
    "jn1-p046_2-31": 0.98,
    "jn1-p047_2-4": 0.97,
    "jn1-p021-halfling_20-4": 0.99,
    "jn1-p021-cave_14-2": 0.99,
    "jn1-p021-cave_24-10": 0.99,
}
"""The share of settled edges each window's reading must get right as passable or blocked."""


@pytest.fixture(scope="session")
def readings():
    out = {}
    for map_id, spec in SPECS.items():
        rgb = np.asarray(Image.open(spec.image).convert("RGB"))
        out[map_id] = read_map(spec, rgb)
    return out


@pytest.mark.parametrize("map_id", sorted(SPECS))
def test_grid_fit_meets_the_registration_figures(readings, map_id):
    fit, _reading = readings[map_id]
    low, high = EXPECTED_PITCH[map_id]
    assert low <= fit.x_axis.pitch <= high
    assert low <= fit.y_axis.pitch <= high
    for axis in (fit.x_axis, fit.y_axis):
        assert axis.rms_px / axis.pitch < 0.05
        assert axis.max_px / axis.pitch < 0.1
        assert axis.within_tenth == 1.0


@pytest.mark.parametrize("map_id", sorted(SPECS))
def test_reading_loads_through_the_truth_models(readings, map_id, tmp_path):
    fit, reading = readings[map_id]
    spec = SPECS[map_id]
    region = to_region(spec, fit)
    panel = to_panel(spec, reading)
    assert isinstance(region, Region) and isinstance(panel, Panel)
    assert (panel.width, panel.height) == (fit.columns, fit.rows)
    assert region.registration is not None
    assert (region.registration.columns, region.registration.rows) == (fit.columns, fit.rows)
    path = tmp_path / f"{map_id}.json"
    save_reading(path, spec, fit, reading)
    loaded = load_reading(path)
    assert loaded.panel == panel
    assert loaded.cell_confidence.shape == (fit.rows, fit.columns)
    assert loaded.vertical_confidence.shape == (fit.rows, fit.columns + 1)
    assert loaded.horizontal_confidence.shape == (fit.rows + 1, fit.columns)
    assert float(loaded.cell_confidence.min()) >= 0.5


@pytest.mark.parametrize("settled_path", SETTLED, ids=[path.stem for path in SETTLED])
def test_reading_matches_the_settled_window(readings, settled_path):
    settled = load_settled(settled_path)
    _fit, reading = readings[settled.map]
    panel = to_panel(SPECS[settled.map], reading)
    scores = score_window(panel, settled)
    tally = scores["all"]
    assert tally["edges_rate"] is not None
    assert tally["edges_rate"] >= EDGE_FLOOR[settled_path.stem], tally["differences"]
    assert tally["cells_rate"] >= 0.97, tally["differences"]


def test_settled_windows_name_their_crops():
    for path in SETTLED:
        document = json.loads(path.read_text())
        assert document["crop"], path
        assert document["checked"], path
        for item, crop in document["checked"].items():
            assert crop.endswith(".png"), (path, item)
