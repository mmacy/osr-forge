"""The decision provider: request keys, the Clef adapter, its recordings, and the drift check.

No test here calls the service. The Clef adapter runs against a fake transport, and the live check that proves one
request is answered and charged is `tools/live/clef_check.py`. This chunk starts after the Claude provider chunk is
implemented: it uses `pixel_digest`, the ledger, and the errors that chunk builds.
"""

import base64
import hashlib
import io
import json
import math
import random
from collections.abc import Mapping
from pathlib import Path

import pytest
from PIL import Image
from pydantic import SecretStr, ValidationError

from osrforge.errors import ClefAuthError, FixtureMissError, LedgerStopError, ProviderError
from osrforge.providers import decisions
from osrforge.providers.base import pixel_digest
from osrforge.providers.clef import (
    PRICE_PER_TOKEN,
    ClefProvider,
    ClefSettings,
    cost_usd,
    estimate_cost,
    estimate_tokens,
    wrangler_token,
)
from osrforge.providers.decisions import (
    JPEG_THRESHOLD_BYTES,
    DecisionProvider,
    DecisionRequest,
    DecisionResponse,
    Drift,
    RecordedDecisionProvider,
    RecordingDecisionProvider,
    drift_check,
    drift_sample,
    encode_request,
    recording_filename,
    request_body,
)

CHUNK = "chunk: clef-provider"
TOKEN = "wrangler-oauth-token-secret"

STATE = "Crop of a printed dungeon map. White squares are floor; solid blue is rock. A red box marks one cell edge."
QUESTION = "What is drawn on the cell edge inside the red box?"
OPTIONS = (
    ("open", "No line; the floor continues"),
    ("wall", "A solid wall line"),
    ("door", "A door symbol"),
    ("secret_door", "A secret door symbol, often an S"),
)
PROBABILITIES = {"door": 0.9504, "open": 0.0125, "secret_door": 0.0211, "wall": 0.016}
"""A real Clef answer from the probe (`~/.re-pod/map-plan/clef/probe/out/H2.jsonl`, S1 edge 3,2|4,2)."""


def png_bytes(seed: int, size: tuple[int, int] = (256, 256), noisy: bool = False) -> bytes:
    image = Image.new("RGB", size, (255, 255, 255))
    if noisy:
        # Random bytes don't compress, so a noisy 512 by 512 crop's PNG body passes the JPEG threshold.
        image = Image.frombytes("RGB", size, random.Random(seed).randbytes(size[0] * size[1] * 3))
    else:
        for x in range(0, size[0], 8):
            for y in range(size[1]):
                image.putpixel((x, y), ((seed * 37) % 256, 0, 128))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=6)
    return buffer.getvalue()


def reencoded(png: bytes) -> bytes:
    with Image.open(io.BytesIO(png)) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "PNG", compress_level=0)
    other = buffer.getvalue()
    assert other != png
    return other


CROP = png_bytes(1)


def make_request(**overrides) -> DecisionRequest:
    fields = {"tag": "edge", "image": CROP, "state": STATE, "question": QUESTION, "options": OPTIONS, "model": "clef"}
    fields.update(overrides)
    return DecisionRequest(**fields)


# ---------------------------------------------------------------- the request and the response


def test_request_validation():
    with pytest.raises(ValueError):
        make_request(tag="free prose")
    with pytest.raises(ValueError):
        make_request(options=(("only", None),))
    with pytest.raises(ValueError):
        make_request(options=(("a", None), ("a", "again")))
    make_request(options=(("a", None), ("b", None)), model="clef-flash")


def test_response_derives_choice_confidence_and_margin():
    response = DecisionResponse(
        probabilities=PROBABILITIES, model_id="clef", input_tokens=308, format="png", body_sha256="ab"
    )
    assert response.choice == "door"
    assert response.confidence == pytest.approx(0.9504)
    assert response.margin == pytest.approx(0.9504 - 0.0211)
    tie = DecisionResponse(
        probabilities={"wall": 0.5, "open": 0.5}, model_id="clef", input_tokens=1, format="png", body_sha256="ab"
    )
    assert tie.choice == "open" and tie.margin == 0.0
    with pytest.raises(ValidationError):
        DecisionResponse(
            probabilities={"wall": 0.7, "open": 0.7}, model_id="clef", input_tokens=1, format="png", body_sha256="ab"
        )
    with pytest.raises(ValidationError):
        DecisionResponse(probabilities={"wall": 1.0}, model_id="clef", input_tokens=1, format="png", body_sha256="ab")


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_key_hashes_pixels_every_parameter_and_how_the_image_is_sent(monkeypatch: pytest.MonkeyPatch):
    key = make_request().key()
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)
    assert make_request(image=reencoded(CROP)).key() == key
    variants = {
        "pixels": make_request(image=png_bytes(2)).key(),
        "model": make_request(model="clef-flash").key(),
        "tag": make_request(tag="cell").key(),
        "state": make_request(state="Other state.").key(),
        "question": make_request(question="Other question?").key(),
        "options": make_request(options=OPTIONS[:3]).key(),
        "description": make_request(options=(("open", "Other words"), *OPTIONS[1:])).key(),
    }
    for name, other in variants.items():
        assert other != key, name
    assert len(set(variants.values())) == len(variants)

    # How the image is sent is part of the key, read from the module's constants when the key is computed.
    large = make_request(image=png_bytes(3, (512, 512), noisy=True))
    large_key = large.key()
    monkeypatch.setattr(decisions, "JPEG_QUALITY", 75)
    assert large.key() != large_key
    assert make_request().key() != key
    monkeypatch.setattr(decisions, "JPEG_QUALITY", 90)
    monkeypatch.setattr(decisions, "JPEG_THRESHOLD_BYTES", 10_000_000)
    assert large.key() != large_key
    assert make_request().key() != key


# ---------------------------------------------------------------- the body, the format, and the prices


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_request_body_is_the_canonical_system_one_body():
    body = request_body(make_request(), "png")
    assert body == json.dumps(json.loads(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    parsed = json.loads(body)
    assert parsed["model"] == "clef"
    assert parsed["state"] == STATE
    assert parsed["questions"] == {
        "edge": {"type": "choice", "instructions": QUESTION, "criteria": dict(OPTIONS)},
    }
    assert len(parsed["images"]) == 1
    assert parsed["images"][0]["content_type"] == "image/png"
    assert base64.standard_b64decode(parsed["images"][0]["base64"]) == CROP
    assert request_body(make_request(), "png") == body


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_small_crop_goes_as_png_and_a_large_body_is_rebuilt_as_jpeg():
    body, image_format = encode_request(make_request())
    assert image_format == "png" and len(body) <= JPEG_THRESHOLD_BYTES
    assert body == request_body(make_request(), "png")

    large = make_request(image=png_bytes(3, (512, 512), noisy=True))
    assert len(request_body(large, "png")) > JPEG_THRESHOLD_BYTES
    body, image_format = encode_request(large)
    assert image_format == "jpeg"
    assert body == request_body(large, "jpeg")
    assert len(body) < len(request_body(large, "png"))
    parsed = json.loads(body)
    assert parsed["images"][0]["content_type"] == "image/jpeg"
    with Image.open(io.BytesIO(base64.standard_b64decode(parsed["images"][0]["base64"]))) as jpeg:
        assert jpeg.format == "JPEG" and jpeg.size == (512, 512)


def probe_estimate(width: int, height: int, state: str, questions: dict) -> int:
    """The probe's formula (`~/.re-pod/map-plan/clef/probe/scripts/clef.py`, `image_tokens_est` and `estimate`)."""
    image = math.ceil(width / 32) * math.ceil(height / 32)
    if width * height < 65536:
        image = max(image, 64) * 2
    image += 4
    return math.ceil(image + len(json.dumps(state)) / 2 + len(json.dumps(questions)) / 1.5 + 200)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_estimate_follows_the_probe_formula():
    """The probe's one-edge requests on a 256 by 256 crop cost 303 to 308 input tokens, under this estimate."""
    request = make_request()
    questions = json.loads(request_body(request, "png"))["questions"]
    tokens = estimate_tokens(request)
    assert tokens == probe_estimate(256, 256, STATE, questions)
    assert 308 <= tokens <= 4 * 308
    assert estimate_cost(request) == pytest.approx(tokens * PRICE_PER_TOKEN["clef"] * 1.5)
    assert estimate_cost(make_request(model="clef-flash")) == pytest.approx(
        tokens * PRICE_PER_TOKEN["clef-flash"] * 1.5
    )
    assert estimate_tokens(make_request(image=png_bytes(3, (1056, 864)))) == probe_estimate(1056, 864, STATE, questions)
    assert estimate_tokens(make_request(image=png_bytes(3, (128, 128)))) == probe_estimate(128, 128, STATE, questions)
    assert cost_usd("clef", 308) == pytest.approx(308 * 0.240e-6)
    assert cost_usd("clef-flash", 300, 8) == pytest.approx(308 * 0.090e-6)
    with pytest.raises(ProviderError):
        cost_usd("jev", 1)


# ---------------------------------------------------------------- the adapter against a fake transport


def envelope(
    probabilities: Mapping[str, float] = PROBABILITIES, model: str = "clef", tokens: int = 308, output: int = 0
) -> tuple[int, bytes]:
    top = max(probabilities, key=lambda option: probabilities[option])
    result = {
        "model": model,
        "answers": {
            "edge": {"type": "choice", "choice": top, "probabilities": dict(probabilities), "confidence": 0.87}
        },
        "usage": {"input_tokens": tokens, "output_tokens": output},
    }
    return 200, json.dumps({"result": result, "success": True, "errors": [], "messages": []}).encode("utf-8")


def rejection(limit: int, estimated: int) -> bytes:
    message = (
        f"AiError: Ai: The estimated number of input and maximum output tokens ({estimated}) exceeded this model "
        f"context window limit ({limit}). (70c144fe-cb11-4a51-a40b-a112ce696bbe)"
    )
    return json.dumps(
        {"errors": [{"message": message, "code": 5021}], "success": False, "result": {}, "messages": []}
    ).encode()


class FakeTransport:
    """Answers each POST from a queue of `(status, body)` pairs or exceptions, recording what it was sent."""

    def __init__(self, answers: list[tuple[int, bytes] | Exception]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, body, dict(headers)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def ledger_file(tmp_path: Path, spent: float = 0.0, stop: float = 8.0) -> Path:
    path = tmp_path / "clef-ledger.json"
    content = {
        "stop_usd": stop,
        "limit_usd": 10.0,
        "spent_usd": spent,
        "entries": [{"date": "2026-10-08", "what": "Clef probe on Workers AI", "requests": 2474, "usd": spent}],
    }
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return path


def provider(
    tmp_path: Path, answers: list[tuple[int, bytes] | Exception], stop: float = 8.0, spent: float = 0.168562
) -> tuple[ClefProvider, FakeTransport, Path]:
    transport = FakeTransport(answers)
    path = ledger_file(tmp_path, spent=spent, stop=stop)
    settings = ClefSettings(
        account_id="acct123", token=SecretStr(TOKEN), ledger_path=path, run_label="osrforge test run"
    )
    sleeps: list[float] = []
    decider = ClefProvider(settings, transport=transport, sleep=sleeps.append, today=lambda: "2026-10-15")
    return decider, transport, path


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_decide_posts_the_body_and_returns_the_probabilities_charged_to_the_ledger(tmp_path: Path):
    decider, transport, path = provider(tmp_path, [envelope(), envelope(tokens=310, output=5)])
    response = decider.decide(make_request())
    assert response.probabilities == PROBABILITIES
    assert response.choice == "door"
    assert response.model_id == "clef"
    assert response.input_tokens == 308
    assert response.format == "png"

    url, body, headers = transport.calls[0]
    assert url == "https://api.cloudflare.com/client/v4/accounts/acct123/ai/run/@cf/cloudflare/clef"
    assert body == request_body(make_request(), "png")
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Content-Type"] == "application/json"
    assert response.body_sha256 == hashlib.sha256(body).hexdigest()

    decider.decide(make_request(image=png_bytes(2)))
    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert len(ledger["entries"]) == 2
    assert ledger["entries"][0]["requests"] == 2474
    entry = ledger["entries"][1]
    assert entry == {
        "date": "2026-10-15",
        "what": "osrforge test run",
        "requests": 2,
        "usd": pytest.approx(cost_usd("clef", 308) + cost_usd("clef", 310, 5), abs=1e-6),
    }
    assert ledger["spent_usd"] == pytest.approx(0.168562 + entry["usd"])
    assert "pending" not in entry


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_decide_refuses_past_the_stop_without_sending(tmp_path: Path):
    request = make_request()
    estimate = estimate_cost(request)
    decider, transport, path = provider(tmp_path, [envelope()], stop=1.0, spent=1.0 - estimate / 2)
    before = path.read_bytes()
    with pytest.raises(LedgerStopError):
        decider.decide(request)
    assert transport.calls == []
    assert path.read_bytes() == before


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_rejection_that_reports_a_token_limit_is_retried(tmp_path: Path):
    decider, transport, _ = provider(
        tmp_path, [(413, rejection(24000, 52428)), (413, rejection(24000, 52428)), envelope()]
    )
    response = decider.decide(make_request())
    assert response.choice == "door"
    assert len(transport.calls) == 3
    assert transport.calls[0][1] == transport.calls[2][1]


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_rejection_that_stays_is_a_provider_error_naming_the_size_and_never_the_token(tmp_path: Path):
    decider, transport, path = provider(tmp_path, [(413, rejection(65536, 215942))] * 4)
    with pytest.raises(ProviderError) as excinfo:
        decider.decide(make_request())
    message = str(excinfo.value)
    assert len(transport.calls) == 4
    assert "413" in message and str(len(transport.calls[0][1])) in message and "65536" in message
    assert TOKEN not in message
    assert TOKEN not in repr(decider.settings)
    ledger = json.loads(path.read_text(encoding="utf-8"))
    # Sent and rejected: settled at the estimate, like the probe's ledger counted its rejections.
    assert ledger["entries"][1]["usd"] == pytest.approx(estimate_cost(make_request()), abs=1e-6)
    assert ledger["entries"][1]["requests"] == 1
    assert "pending" not in ledger["entries"][1]


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_rate_limits_and_connection_failures_are_retried_and_auth_failures_are_not(tmp_path: Path):
    decider, transport, _ = provider(tmp_path, [(429, b"slow down"), OSError("reset"), (503, b"busy"), envelope()])
    assert decider.decide(make_request()).choice == "door"
    assert len(transport.calls) == 4

    decider, transport, _ = provider(tmp_path, [(401, b'{"errors":[{"message":"unauthorized"}]}')])
    with pytest.raises(ClefAuthError) as auth:
        decider.decide(make_request())
    assert len(transport.calls) == 1 and "401" in str(auth.value) and "wrangler login" in str(auth.value)
    assert TOKEN not in str(auth.value)

    decider, transport, _ = provider(tmp_path, [(403, b"forbidden")])
    with pytest.raises(ProviderError) as excinfo:
        decider.decide(make_request())
    assert len(transport.calls) == 1 and "403" in str(excinfo.value)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_large_crop_is_sent_as_jpeg_and_the_response_says_so(tmp_path: Path):
    decider, transport, _ = provider(tmp_path, [envelope()])
    response = decider.decide(make_request(image=png_bytes(3, (512, 512), noisy=True)))
    assert response.format == "jpeg"
    assert json.loads(transport.calls[0][1])["images"][0]["content_type"] == "image/jpeg"


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_token_comes_from_the_wrangler_login_or_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "default.toml"
    config.write_text(f'oauth_token = "{TOKEN}"\nrefresh_token = "r"\nscopes = ["ai:write"]\n', encoding="utf-8")
    assert wrangler_token(config) == TOKEN
    with pytest.raises(ProviderError) as excinfo:
        wrangler_token(tmp_path / "absent.toml")
    assert "absent.toml" in str(excinfo.value)
    (tmp_path / "bare.toml").write_text('scopes = ["ai:write"]\n', encoding="utf-8")
    with pytest.raises(ProviderError):
        wrangler_token(tmp_path / "bare.toml")

    for name in (
        "OSRFORGE_CLEF_ACCOUNT_ID",
        "OSRFORGE_CLEF_TOKEN",
        "OSRFORGE_CLEF_LEDGER",
        "OSRFORGE_CLEF_LEDGER_STOP",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ProviderError):
        ClefSettings.from_env()
    monkeypatch.setenv("OSRFORGE_CLEF_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("OSRFORGE_CLEF_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("OSRFORGE_CLEF_TOKEN", TOKEN)
    monkeypatch.setenv("OSRFORGE_CLEF_LEDGER_STOP", "2.5")
    settings = ClefSettings.from_env()
    assert settings.account_id == "acct123"
    assert settings.token.get_secret_value() == TOKEN
    assert settings.ledger_path == tmp_path / "ledger.json"
    assert settings.ledger_stop_usd == 2.5
    assert TOKEN not in repr(settings)


# ---------------------------------------------------------------- recordings


class StubDecider:
    """A decision provider that answers from a table keyed by request key, for the recorder and the drift check."""

    def __init__(self, answers: Mapping[str, DecisionResponse]) -> None:
        self.answers = dict(answers)
        self.requests: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        self.requests.append(request)
        return self.answers[request.key()]


def response_for(request: DecisionRequest, probabilities: Mapping[str, float] = PROBABILITIES) -> DecisionResponse:
    body, image_format = encode_request(request)
    return DecisionResponse(
        probabilities=dict(probabilities),
        model_id=request.model,
        input_tokens=308,
        format=image_format,
        body_sha256=hashlib.sha256(body).hexdigest(),
    )


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_record_then_replay_round_trip(tmp_path: Path):
    request = make_request()
    response = response_for(request)
    recorder = RecordingDecisionProvider(StubDecider({request.key(): response}), tmp_path)
    assert recorder.decide(request) == response

    path = tmp_path / recording_filename("edge", request.key())
    assert path.is_file()
    recording = json.loads(path.read_text(encoding="utf-8"))
    assert recording["key"] == request.key()
    assert recording["tag"] == "edge"
    assert recording["request"] == {
        "model": "clef",
        "state": STATE,
        "question": QUESTION,
        "options": [list(option) for option in OPTIONS],
        "image": {"pixels": pixel_digest(CROP), "width": 256, "height": 256},
    }
    assert recording["body_bytes"] == len(encode_request(request)[0])
    assert recording["response"] == response.model_dump()
    assert recording["response"]["format"] == "png"
    text = path.read_text(encoding="utf-8")
    assert base64.standard_b64encode(CROP).decode("ascii") not in text

    replayed = RecordedDecisionProvider(tmp_path)
    assert replayed.decide(request) == response
    assert replayed.decide(make_request(image=reencoded(CROP))) == response
    with pytest.raises(FixtureMissError) as miss:
        replayed.decide(make_request(image=png_bytes(5)))
    assert "edge" in str(miss.value) and str(tmp_path) in str(miss.value)
    assert isinstance(replayed, DecisionProvider) and isinstance(recorder, DecisionProvider)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_recording_through_the_adapter_records_the_format_and_the_body_hash(tmp_path: Path):
    decider, transport, _ = provider(tmp_path, [envelope()])
    request = make_request(image=png_bytes(3, (512, 512), noisy=True))
    response = RecordingDecisionProvider(decider, tmp_path / "recordings").decide(request)
    recording = json.loads(
        (tmp_path / "recordings" / recording_filename("edge", request.key())).read_text(encoding="utf-8")
    )
    assert recording["response"]["format"] == "jpeg"
    assert (
        recording["response"]["body_sha256"]
        == hashlib.sha256(transport.calls[0][1]).hexdigest()
        == response.body_sha256
    )


# ---------------------------------------------------------------- the drift check


def recorded_set(tmp_path: Path) -> tuple[list[DecisionRequest], dict[str, DecisionResponse]]:
    requests = [make_request(image=png_bytes(seed)) for seed in (11, 12, 13)]
    answers = {
        requests[0].key(): response_for(requests[0]),
        requests[1].key(): response_for(requests[1], {"door": 0.05, "open": 0.05, "secret_door": 0.10, "wall": 0.80}),
        requests[2].key(): response_for(requests[2], {"door": 0.46, "open": 0.44, "secret_door": 0.05, "wall": 0.05}),
    }
    recorder = RecordingDecisionProvider(StubDecider(answers), tmp_path)
    for request in requests:
        recorder.decide(request)
    return requests, answers


def alter(tmp_path: Path, request: DecisionRequest, **changes: object) -> None:
    path = tmp_path / recording_filename("edge", request.key())
    recording = json.loads(path.read_text(encoding="utf-8"))
    recording["response"].update(changes)
    path.write_text(json.dumps(recording), encoding="utf-8")


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_drift_check_passes_when_the_service_still_gives_the_recorded_answers(tmp_path: Path):
    requests, answers = recorded_set(tmp_path)
    live = StubDecider(answers)
    assert drift_check(tmp_path, live, requests) == ()
    assert [r.key() for r in live.requests] == [r.key() for r in requests]


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_drift_check_stops_on_one_deliberately_altered_recording(tmp_path: Path):
    requests, answers = recorded_set(tmp_path)
    moved = {"door": 0.05 + 0.05, "open": 0.05, "secret_door": 0.10, "wall": 0.80 - 0.05}
    alter(tmp_path, requests[1], probabilities=moved)
    drifts = drift_check(tmp_path, StubDecider(answers), requests)
    assert len(drifts) == 1
    drift = drifts[0]
    assert isinstance(drift, Drift)
    assert (drift.tag, drift.key, drift.kind) == ("edge", requests[1].key(), "probability")
    assert "0.05" in drift.reason


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_drift_check_tolerates_small_moves_and_catches_a_changed_top_answer_or_body(tmp_path: Path):
    requests, answers = recorded_set(tmp_path)
    nudged = {"door": 0.05 + 0.02, "open": 0.05, "secret_door": 0.10, "wall": 0.80 - 0.02}
    alter(tmp_path, requests[1], probabilities=nudged)
    assert drift_check(tmp_path, StubDecider(answers), requests) == ()
    assert len(drift_check(tmp_path, StubDecider(answers), requests, tolerance=0.01)) == 1

    flipped = {"door": 0.44, "open": 0.46, "secret_door": 0.05, "wall": 0.05}
    alter(tmp_path, requests[2], probabilities=flipped)
    drifts = drift_check(tmp_path, StubDecider(answers), requests)
    assert [(d.key, d.kind) for d in drifts] == [(requests[2].key(), "choice")]
    assert "door" in drifts[0].reason and "open" in drifts[0].reason

    # A body that no longer hashes to the recording's is its own kind of drift, and the probabilities are still
    # compared: here they match, so the body drift stands alone.
    alter(tmp_path, requests[2], probabilities=answers[requests[2].key()].probabilities, body_sha256="00" * 32)
    drifts = drift_check(tmp_path, StubDecider(answers), requests)
    assert [(d.key, d.kind) for d in drifts] == [(requests[2].key(), "body")]

    alter(tmp_path, requests[2], probabilities=flipped, body_sha256="00" * 32)
    drifts = drift_check(tmp_path, StubDecider(answers), requests)
    assert [(d.key, d.kind) for d in drifts] == [(requests[2].key(), "body"), (requests[2].key(), "choice")]

    with pytest.raises(FixtureMissError):
        drift_check(tmp_path, StubDecider(answers), [make_request(image=png_bytes(99))])


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_drift_sample_favours_small_margins_and_is_deterministic(tmp_path: Path):
    requests = [make_request(image=png_bytes(100 + i)) for i in range(60)]
    answers: dict[str, DecisionResponse] = {}
    for i, request in enumerate(requests):
        top = 0.5 + i * 0.008  # margins grow with i: request 0 has the smallest
        answers[request.key()] = response_for(
            request, {"wall": top, "open": 1 - top - 0.02, "door": 0.01, "secret_door": 0.01}
        )
    recorder = RecordingDecisionProvider(StubDecider(answers), tmp_path)
    for request in requests:
        recorder.decide(request)

    unrecorded = [make_request(image=png_bytes(300 + i)) for i in range(5)]
    candidates = unrecorded + requests
    sample = drift_sample(tmp_path, candidates, size=20)
    keys = [request.key() for request in sample]
    assert len(sample) == 20 and keys == sorted(keys)
    assert not {request.key() for request in unrecorded} & set(keys)
    smallest = {requests[i].key() for i in range(10)}
    assert smallest <= set(keys)
    assert drift_sample(tmp_path, candidates, size=20) == sample
    assert len(drift_sample(tmp_path, candidates, size=100)) == 60
    assert drift_sample(tmp_path, unrecorded, size=20) == ()
