"""The local Clef backend against a fake transport, and the setting that chooses a backend.

No test calls a server. `tools/live/clef_local_check.py` sends one request to a running one.
"""

import base64
import hashlib
import io
import json
import random
from collections.abc import Mapping
from pathlib import Path

import pytest
from PIL import Image

from osrforge.errors import ProviderError
from osrforge.providers import decisions
from osrforge.providers.clef import ClefProvider
from osrforge.providers.clef_local import (
    BACKEND_ENV,
    LOCAL_MODEL_ENV,
    LOCAL_URL_ENV,
    LocalClefProvider,
    LocalClefSettings,
    clef_from_env,
    local_request_body,
)
from osrforge.providers.decisions import (
    DecisionRequest,
    RecordedDecisionProvider,
    RecordingDecisionProvider,
    encode_request,
)

STATE = "Crop of a printed dungeon map in blue and white. A red box marks one cell edge."
QUESTION = "What is drawn on the cell edge inside the red box?"
OPTIONS = (
    ("open", "No line; the floor continues"),
    ("wall", "A solid wall line"),
    ("door", "A door symbol"),
    ("secret_door", "A secret door symbol, often an S"),
)
PROBABILITIES = {"door": 0.048903, "open": 0.614673, "secret_door": 0.203491, "wall": 0.132933}
"""Local clef-flash's answer to the Clef live check's crop on 2026-11-02."""


def png_bytes(seed: int, size: tuple[int, int] = (256, 256), noisy: bool = False) -> bytes:
    if noisy:
        image = Image.frombytes("RGB", size, random.Random(seed).randbytes(size[0] * size[1] * 3))
    else:
        image = Image.new("RGB", size, (255, 255, 255))
        for x in range(0, size[0], 8):
            for y in range(size[1]):
                image.putpixel((x, y), ((seed * 37) % 256, 0, 128))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=6)
    return buffer.getvalue()


def make_request(**overrides: object) -> DecisionRequest:
    fields: dict[str, object] = {
        "tag": "edge",
        "image": png_bytes(1),
        "state": STATE,
        "question": QUESTION,
        "options": OPTIONS,
        "model": "clef-flash",
    }
    fields.update(overrides)
    return DecisionRequest(**fields)  # type: ignore[arg-type]


def answer(probabilities: Mapping[str, float] = PROBABILITIES, model: str = "clef-flash", tokens: int = 263) -> bytes:
    top = max(probabilities, key=lambda option: probabilities[option])
    body = {
        "model": model,
        "answers": {
            "edge": {"type": "choice", "choice": top, "probabilities": dict(probabilities), "confidence": 0.25}
        },
        "usage": {"input_tokens": tokens, "output_tokens": 0},
        "prompt_eval_cached_count": 0,
    }
    return json.dumps(body).encode("utf-8")


class FakeTransport:
    """Answers each POST from a queue of `(status, body)` pairs or exceptions, recording what it was sent."""

    def __init__(self, answers: list[tuple[int, bytes] | Exception]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, body, dict(headers)))
        reply = self.answers.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def provider(
    answers: list[tuple[int, bytes] | Exception], settings: LocalClefSettings | None = None
) -> tuple[LocalClefProvider, FakeTransport, list[float]]:
    transport = FakeTransport(answers)
    sleeps: list[float] = []
    return (
        LocalClefProvider(settings or LocalClefSettings(), transport=transport, sleep=sleeps.append),
        transport,
        sleeps,
    )


def expected_body(request: DecisionRequest, image: bytes, server_model: str = "clef-flash") -> dict[str, object]:
    return {
        "model": server_model,
        "images": [base64.standard_b64encode(image).decode("ascii")],
        "state": request.state,
        "questions": {
            request.tag: {"type": "choice", "instructions": request.question, "criteria": dict(request.options)}
        },
    }


def test_the_body_is_the_system_one_body_with_plain_image_strings():
    request = make_request()
    body = local_request_body(request, "png", "clef-flash")
    assert json.loads(body) == expected_body(request, request.image)
    assert body == json.dumps(json.loads(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    renamed = json.loads(local_request_body(request, "png", "my-clef"))
    assert renamed["model"] == "my-clef"


def test_decide_posts_to_the_local_server_and_returns_the_probabilities():
    decider, transport, _ = provider([(200, answer())])
    request = make_request()
    response = decider.decide(request)
    assert response.probabilities == PROBABILITIES
    assert response.choice == "open"
    assert response.model_id == "local:clef-flash"
    assert response.input_tokens == 263
    assert response.format == "png"
    url, body, headers = transport.calls[0]
    assert url == "http://localhost:11434/v1/systemone"
    assert json.loads(body) == expected_body(request, request.image)
    assert response.body_sha256 == hashlib.sha256(body).hexdigest()
    assert headers["Content-Type"] == "application/json"
    assert not any(name.lower() == "authorization" for name in headers)


def test_a_large_crop_goes_as_jpeg_as_it_would_to_the_hosted_service():
    request = make_request(image=png_bytes(7, (512, 512), noisy=True))
    hosted_body, hosted_format = encode_request(request)
    assert hosted_format == "jpeg"
    decider, transport, _ = provider([(200, answer())])
    response = decider.decide(request)
    assert response.format == "jpeg"
    sent = json.loads(transport.calls[0][1])["images"][0]
    assert sent == json.loads(hosted_body)["images"][0]["base64"]


def test_a_model_the_settings_dont_map_is_refused_before_sending():
    decider, transport, _ = provider([(200, answer())])
    with pytest.raises(ProviderError) as excinfo:
        decider.decide(make_request(model="clef"))
    assert "clef" in str(excinfo.value)
    assert transport.calls == []
    mapped = LocalClefSettings(base_url="http://127.0.0.1:9000", models={"clef": "clef-27b", "clef-flash": "flash"})
    decider, transport, _ = provider([(200, answer(model="clef-27b"))], mapped)
    assert decider.decide(make_request(model="clef")).model_id == "local:clef-27b"
    url, body, _ = transport.calls[0]
    assert url == "http://127.0.0.1:9000/v1/systemone"
    assert json.loads(body)["model"] == "clef-27b"


def test_connection_failures_and_5xx_are_retried_three_times_and_other_statuses_are_not():
    decider, transport, sleeps = provider([OSError("refused"), (503, b"loading"), (200, answer())])
    assert decider.decide(make_request()).choice == "open"
    assert len(transport.calls) == 3
    assert len(sleeps) == 2

    decider, transport, _ = provider([OSError("connection refused")] * 3)
    with pytest.raises(ProviderError) as excinfo:
        decider.decide(make_request())
    assert len(transport.calls) == 3
    assert "http://localhost:11434" in str(excinfo.value)

    message = b'{"error":"json: cannot unmarshal object into Go struct field SystemOneRequest.images"}'
    decider, transport, _ = provider([(400, message)])
    with pytest.raises(ProviderError) as excinfo:
        decider.decide(make_request())
    assert len(transport.calls) == 1
    assert "400" in str(excinfo.value) and "cannot unmarshal" in str(excinfo.value)


def test_an_answer_that_cant_be_read_is_a_provider_error():
    for reply in (
        b"not json",
        json.dumps({"model": "clef-flash", "answers": {}, "usage": {"input_tokens": 1}}).encode(),
        answer({"open": 0.5, "wall": 0.5}),
        json.dumps({"model": "clef-flash", "answers": {"edge": {"probabilities": PROBABILITIES}}}).encode(),
    ):
        decider, _, _ = provider([(200, reply)])
        with pytest.raises(ProviderError):
            decider.decide(make_request())


def test_a_local_answer_records_and_replays_with_its_backend_named(tmp_path: Path):
    decider, _, _ = provider([(200, answer())])
    request = make_request()
    recorded = RecordingDecisionProvider(decider, tmp_path).decide(request)
    replayed = RecordedDecisionProvider(tmp_path).decide(request)
    assert replayed == recorded
    assert replayed.model_id == "local:clef-flash"


def test_the_environment_chooses_the_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in (BACKEND_ENV, LOCAL_URL_ENV, LOCAL_MODEL_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OSRFORGE_CLEF_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("OSRFORGE_CLEF_LEDGER", str(tmp_path / "clef-ledger.json"))
    monkeypatch.setenv("OSRFORGE_CLEF_TOKEN", "not-a-real-token")
    assert isinstance(clef_from_env(), ClefProvider)
    monkeypatch.setenv(BACKEND_ENV, "hosted")
    assert isinstance(clef_from_env(), ClefProvider)

    monkeypatch.setenv(BACKEND_ENV, "local")
    for name in ("OSRFORGE_CLEF_ACCOUNT_ID", "OSRFORGE_CLEF_LEDGER", "OSRFORGE_CLEF_TOKEN"):
        monkeypatch.delenv(name)
    local = clef_from_env()
    assert isinstance(local, LocalClefProvider)
    assert LocalClefSettings.from_env() == LocalClefSettings()

    monkeypatch.setenv(LOCAL_URL_ENV, "http://127.0.0.1:9000/")
    monkeypatch.setenv(LOCAL_MODEL_ENV, "clef-flash:latest")
    settings = LocalClefSettings.from_env()
    assert settings.base_url == "http://127.0.0.1:9000"
    assert dict(settings.models) == {"clef-flash": "clef-flash:latest"}

    monkeypatch.setenv(BACKEND_ENV, "cloud")
    with pytest.raises(ProviderError):
        clef_from_env()


def test_the_local_backend_is_a_decision_provider():
    assert issubclass(LocalClefProvider, decisions.DecisionProvider)


def test_a_timeout_raises_at_once_and_names_the_timeout_and_the_url():
    decider, transport, sleeps = provider([TimeoutError("timed out"), (200, answer())])
    with pytest.raises(ProviderError) as excinfo:
        decider.decide(make_request())
    assert len(transport.calls) == 1
    assert sleeps == []
    assert "timed out after 300 seconds" in str(excinfo.value)
    assert "http://localhost:11434/v1/systemone" in str(excinfo.value)


def test_a_trailing_slash_on_the_base_url_is_stripped():
    settings = LocalClefSettings(base_url="http://h:9000/")
    assert settings.base_url == "http://h:9000"
    decider, transport, _ = provider([(200, answer())], settings)
    decider.decide(make_request())
    assert transport.calls[0][0] == "http://h:9000/v1/systemone"


def test_an_answer_without_input_tokens_gives_a_one_line_error():
    reply = json.dumps({"model": "clef-flash", "answers": {"edge": {"probabilities": PROBABILITIES}}, "usage": {}})
    decider, _, _ = provider([(200, reply.encode())])
    with pytest.raises(ProviderError) as excinfo:
        decider.decide(make_request())
    assert "input tokens" in str(excinfo.value)
    assert "\n" not in str(excinfo.value)
