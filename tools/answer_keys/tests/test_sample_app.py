"""The owner's sample: its file formats, and the app that records verdicts on it.

The app's tests call the WSGI application directly, with no server and no network. The page's key presses are
checked by hand in a browser.
"""

import io
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from wsgiref.util import setup_testing_defaults

import pytest
from PIL import Image
from pydantic import ValidationError
from tools.answer_keys.sample import SampleCall, SampleSet, Verdict, Verdicts, verdicts_path
from tools.answer_keys.sample_app import make_app

CHUNK = "chunk: sample-app"


def call(index: int, **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"c{index}",
        "member": "jn1-chaotic-caves",
        "map": "p038.png",
        "style": "cave",
        "kind": "edge",
        "item": f"{index},4:east",
        "zoom": f"crops/c{index}-zoom.png",
        "wide": f"crops/c{index}-wide.png",
        "readings": [{"reader": "claude", "value": "w"}, {"reader": "classifier", "value": "o"}],
        "settled": "w",
        "adversarial": "w",
        "reason": "The wall's ink runs the length of the edge.",
    }
    return base | changes


def write_sample(directory: Path, count: int = 3) -> Path:
    """A sample of `count` edge calls, each with a zoomed and a wide crop of a different colour."""
    (directory / "crops").mkdir()
    for index in range(count):
        for name, size in (("zoom", 64), ("wide", 128)):
            Image.new("RGB", (size, size), (40 * index, 90, 200)).save(directory / "crops" / f"c{index}-{name}.png")
    sample = directory / "sample.json"
    sample.write_text(json.dumps({"calls": [call(index) for index in range(count)]}), encoding="utf-8")
    return sample


def request(
    app: Any, method: str, path: str, body: bytes = b"", content_type: str = "application/json"
) -> tuple[int, dict[str, str], bytes]:
    environ: dict[str, Any] = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = method
    environ["PATH_INFO"] = path
    environ["CONTENT_LENGTH"] = str(len(body))
    environ["CONTENT_TYPE"] = content_type
    environ["wsgi.input"] = io.BytesIO(body)
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        captured["status"] = int(status.split()[0])
        captured["headers"] = {name.lower(): value for name, value in headers}
        return lambda data: None

    chunks: Iterable[bytes] = app(environ, start_response)
    data = b"".join(chunks)
    return captured["status"], captured["headers"], data


def post_verdict(app: Any, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    return request(app, "POST", "/api/verdict", json.dumps(payload).encode("utf-8"))


def test_sample_models_validate():
    SampleSet.model_validate({"calls": [call(0), call(1)]})
    with pytest.raises(ValidationError):
        SampleSet.model_validate({"calls": [call(0), call(0)]})
    with pytest.raises(ValidationError):
        SampleSet.model_validate({"calls": []})
    for bad in ("/etc/passwd", "../outside.png", "crops/../../outside.png", "crops\\c0.png", ""):
        with pytest.raises(ValidationError):
            SampleCall.model_validate(call(0, zoom=bad))
    with pytest.raises(ValidationError):
        SampleCall.model_validate(call(0, kind="area"))
    with pytest.raises(ValidationError):
        SampleCall.model_validate(call(0, readings=[]))
    with pytest.raises(ValidationError):
        SampleCall.model_validate(call(0, readings=[{"reader": "opus", "value": "w"}]))
    with pytest.raises(ValidationError):
        Verdict.model_validate({"call": "c0", "verdict": "maybe"})
    with pytest.raises(ValidationError):
        Verdicts.model_validate({"verdicts": [{"call": "c0", "verdict": "right"}, {"call": "c0", "verdict": "wrong"}]})


def test_verdicts_path_sits_beside_the_sample(tmp_path: Path):
    assert verdicts_path(tmp_path / "lairs.json") == tmp_path / "lairs.verdicts.json"


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_page_is_served(tmp_path: Path):
    app = make_app(write_sample(tmp_path))
    status, headers, body = request(app, "GET", "/")
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    page = body.decode("utf-8")
    assert "/api/sample" in page and "/api/verdict" in page


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_sample_comes_back_in_order_with_no_verdicts(tmp_path: Path):
    app = make_app(write_sample(tmp_path))
    status, headers, body = request(app, "GET", "/api/sample")
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    data = json.loads(body)
    assert [item["id"] for item in data["calls"]] == ["c0", "c1", "c2"]
    assert SampleCall.model_validate(data["calls"][1]) == SampleCall.model_validate(call(1))
    assert data["verdicts"] == {}


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_crop_the_sample_names_is_served(tmp_path: Path):
    app = make_app(write_sample(tmp_path))
    status, headers, body = request(app, "GET", "/crops/crops/c1-wide.png")
    assert status == 200
    assert headers["content-type"] == "image/png"
    assert body == (tmp_path / "crops" / "c1-wide.png").read_bytes()


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_no_other_file_is_served(tmp_path: Path):
    sample = write_sample(tmp_path)
    (tmp_path / "crops" / "unnamed.png").write_bytes((tmp_path / "crops" / "c0-zoom.png").read_bytes())
    (tmp_path.parent / "outside.png").write_bytes(b"secret")
    app = make_app(sample)
    for path in (
        "/crops/crops/unnamed.png",
        "/crops/sample.json",
        "/crops/../outside.png",
        "/crops/crops/../../outside.png",
        "/crops/" + str(tmp_path.parent / "outside.png"),
        "/nowhere",
    ):
        status, _, body = request(app, "GET", path)
        assert status == 404, path
        assert b"secret" not in body


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_verdict_is_written_and_read_back_on_the_next_start(tmp_path: Path):
    sample = write_sample(tmp_path)
    app = make_app(sample)
    status, _, body = post_verdict(app, {"call": "c1", "verdict": "wrong", "note": "That's a door, not a wall."})
    assert status == 200
    assert json.loads(body) == {"recorded": 1}
    written = Verdicts.model_validate_json(verdicts_path(sample).read_text(encoding="utf-8"))
    assert written.verdicts == (Verdict(call="c1", verdict="wrong", note="That's a door, not a wall."),)
    _, _, body = request(make_app(sample), "GET", "/api/sample")
    assert json.loads(body)["verdicts"] == {
        "c1": {"call": "c1", "verdict": "wrong", "note": "That's a door, not a wall."}
    }


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_later_verdict_replaces_an_earlier_one_and_the_file_keeps_the_sample_order(tmp_path: Path):
    sample = write_sample(tmp_path)
    app = make_app(sample)
    for payload in (
        {"call": "c2", "verdict": "unsure"},
        {"call": "c0", "verdict": "right"},
        {"call": "c2", "verdict": "right"},
    ):
        status, _, _ = post_verdict(app, payload)
        assert status == 200
    written = Verdicts.model_validate_json(verdicts_path(sample).read_text(encoding="utf-8"))
    assert [(verdict.call, verdict.verdict) for verdict in written.verdicts] == [("c0", "right"), ("c2", "right")]
    _, _, body = post_verdict(app, {"call": "c1", "verdict": "right"})
    assert json.loads(body) == {"recorded": 3}


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_bad_verdict_changes_nothing(tmp_path: Path):
    sample = write_sample(tmp_path)
    app = make_app(sample)
    assert post_verdict(app, {"call": "c0", "verdict": "right"})[0] == 200
    before = verdicts_path(sample).read_bytes()
    assert post_verdict(app, {"call": "c0", "verdict": "maybe"})[0] == 400
    assert post_verdict(app, {"call": "c0"})[0] == 400
    assert request(app, "POST", "/api/verdict", b"not json")[0] == 400
    assert post_verdict(app, {"call": "c9", "verdict": "right"})[0] == 404
    assert verdicts_path(sample).read_bytes() == before
    _, _, body = request(app, "GET", "/api/sample")
    assert json.loads(body)["verdicts"] == {"c0": {"call": "c0", "verdict": "right", "note": ""}}


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_an_invalid_verdicts_file_stops_the_start(tmp_path: Path):
    sample = write_sample(tmp_path)
    verdicts_path(sample).write_text('{"verdicts": [{"call": "c0", "verdict": "maybe"}]}', encoding="utf-8")
    with pytest.raises(ValidationError):
        make_app(sample)
