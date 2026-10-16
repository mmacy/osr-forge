"""The reading provider: request keys, the request body, the Claude adapter, and the recordings tests replay.

No test here calls the service. The Claude adapter runs against a fake client that answers like the SDK's
`messages.stream`, and the live check that proves the cache sharing is `tools/live/claude_cache_check.py`. The
Clef provider chunk (`tests/test_providers_decisions.py`) starts after this chunk is implemented, because it uses
`pixel_digest`, the ledger, and the errors this chunk builds.
"""

import base64
import io
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from anthropic import APIConnectionError
from PIL import Image
from pydantic import SecretStr

from conftest import fabricate_workdir
from osrforge.contracts.run import RequestUsage, Stage
from osrforge.errors import (
    FixtureMissError,
    LedgerStopError,
    ProviderError,
    ReadingFailure,
    SchemaValidationError,
    StaleRecordingError,
)
from osrforge.providers.base import ImagePart, TextPart, pixel_digest
from osrforge.providers.claude import (
    PRICES,
    ClaudeProvider,
    ClaudeSettings,
    cost_usd,
    estimate_input_tokens,
    worst_case_usd,
)
from osrforge.providers.reading import (
    ReadingProvider,
    ReadingRequest,
    ReadingResponse,
    ReadingUsage,
    RecordedReadingProvider,
    RecordingReadingProvider,
    RequestKind,
    Retry,
    recording_filename,
    request_body,
)
from osrforge.workdir import track_stage

CHUNK = "chunk: claude-provider"
MODEL = "claude-sonnet-5-5"

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"rows": {"type": "array", "items": {"type": "string"}}},
    "required": ["rows"],
    "additionalProperties": False,
}

KIND = RequestKind(name="probe", system="You read printed maps.", schema=SCHEMA, effort="low", max_tokens=2000)
ANSWER = {"rows": ["F", "R"]}


def png_bytes(seed: int, size: tuple[int, int] = (16, 16), compress_level: int = 6) -> bytes:
    """A PNG whose pixels depend on the seed, so two seeds are two pictures."""
    image = Image.new("RGB", size)
    pixels = [
        ((seed * 37 + x * 7 + y * 13) % 256, (seed * 11 + x) % 256, (seed * 5 + y) % 256)
        for y in range(size[1])
        for x in range(size[0])
    ]
    image.putdata(pixels)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=compress_level)
    return buffer.getvalue()


def reencoded(png: bytes) -> bytes:
    """The same pixels in different PNG bytes."""
    with Image.open(io.BytesIO(png)) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "PNG", compress_level=0)
    other = buffer.getvalue()
    assert other != png
    return other


INSTRUCTIONS = TextPart(text="Instructions: read the window.")
PAGE_A = ImagePart(png=png_bytes(1))
PAGE_B = ImagePart(png=png_bytes(2))
CROP = ImagePart(png=png_bytes(3))
QUESTION = TextPart(text="What is on the edge in the red box?")


def make_request(
    kind: RequestKind = KIND,
    shared: tuple[tuple[TextPart | ImagePart, ...], ...] = ((INSTRUCTIONS,), (PAGE_A,)),
    question: tuple[TextPart | ImagePart, ...] = (CROP, QUESTION),
    retry: Retry | None = None,
) -> ReadingRequest:
    return ReadingRequest(kind=kind, shared=shared, question=question, retry=retry)


# ---------------------------------------------------------------- the pixel digest and the keys


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_pixel_digest_hashes_pixels_not_bytes():
    png = png_bytes(1)
    digest = pixel_digest(png)
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    assert pixel_digest(reencoded(png)) == digest
    assert pixel_digest(png_bytes(2)) != digest
    with pytest.raises(ValueError):
        pixel_digest(b"not an image")

    # The digest covers alpha: an opaque RGBA copy matches the RGB original, and a change of alpha differs.
    with Image.open(io.BytesIO(png)) as image:
        opaque = image.convert("RGBA")
    buffer = io.BytesIO()
    opaque.save(buffer, "PNG")
    assert pixel_digest(buffer.getvalue()) == digest
    translucent = opaque.copy()
    pixel = opaque.getpixel((0, 0))
    assert isinstance(pixel, tuple)
    translucent.putpixel((0, 0), (pixel[0], pixel[1], pixel[2], 128))
    buffer = io.BytesIO()
    translucent.save(buffer, "PNG")
    assert pixel_digest(buffer.getvalue()) != digest


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_key_is_a_stable_hash_of_pixels_and_parameters():
    key = make_request().key(MODEL)
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)
    assert make_request().key(MODEL) == key
    same_pixels = make_request(shared=((INSTRUCTIONS,), (ImagePart(png=reencoded(PAGE_A.png)),)))
    assert same_pixels.key(MODEL) == key
    other_pixels = make_request(shared=((INSTRUCTIONS,), (PAGE_B,)))
    assert other_pixels.key(MODEL) != key


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_key_changes_with_every_parameter_the_plan_names():
    base = make_request()
    key = base.key(MODEL)
    variants = {
        "model": base.key("claude-opus-5-5"),
        "system": make_request(kind=RequestKind("probe", "Other.", SCHEMA, "low", 2000)).key(MODEL),
        "message text": make_request(question=(CROP, TextPart(text="Other question."))).key(MODEL),
        "breakpoints": make_request(shared=((INSTRUCTIONS, PAGE_A),)).key(MODEL),
        "effort": make_request(kind=RequestKind("probe", KIND.system, SCHEMA, "high", 2000)).key(MODEL),
        "schema": make_request(kind=RequestKind("probe", KIND.system, {"type": "object"}, "low", 2000)).key(MODEL),
        "max_tokens": make_request(kind=RequestKind("probe", KIND.system, SCHEMA, "low", 4000)).key(MODEL),
        "retry": make_request(retry=Retry(answer='{"rows": []}', fault="2 rows expected")).key(MODEL),
    }
    for name, other in variants.items():
        assert other != key, name
    assert len(set(variants.values())) == len(variants)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_shared_key_covers_the_cached_prefix_only():
    first = make_request(question=(CROP, QUESTION))
    second = make_request(question=(ImagePart(png=png_bytes(9)), TextPart(text="Another item.")))
    assert first.shared_key(MODEL) == second.shared_key(MODEL)
    assert first.shared_key(MODEL) != first.key(MODEL)
    assert make_request(shared=((INSTRUCTIONS,), (PAGE_B,))).shared_key(MODEL) != first.shared_key(MODEL)
    assert make_request(shared=((INSTRUCTIONS,),)).shared_key(MODEL) != first.shared_key(MODEL)
    assert first.shared_key("claude-opus-5-5") != first.shared_key(MODEL)
    retried = make_request(retry=Retry(answer="{}", fault="rows missing"))
    assert retried.shared_key(MODEL) == first.shared_key(MODEL)


def test_request_validation():
    with pytest.raises(ValueError):
        make_request(shared=((INSTRUCTIONS,), (PAGE_A,), (PAGE_B,), (CROP,)))
    with pytest.raises(ValueError):
        make_request(shared=((INSTRUCTIONS,), ()))
    with pytest.raises(ValueError):
        make_request(shared=((INSTRUCTIONS,) * 21,))
    make_request(shared=((INSTRUCTIONS,) * 20,))
    with pytest.raises(ValueError):
        make_request(question=())
    with pytest.raises(ValueError):
        RequestKind(name="free prose", system="s", schema=SCHEMA, effort="low", max_tokens=10)
    with pytest.raises(ValueError):
        RequestKind(name="probe", system="s", schema=SCHEMA, effort="low", max_tokens=0)
    make_request(shared=())


def test_reading_usage_adds_every_field():
    total = ReadingUsage(input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_write_tokens=4) + ReadingUsage(
        input_tokens=10, output_tokens=20, cache_read_tokens=30, cache_write_tokens=40
    )
    assert total == ReadingUsage(input_tokens=11, output_tokens=22, cache_read_tokens=33, cache_write_tokens=44)


# ---------------------------------------------------------------- the request body


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_request_body_places_a_breakpoint_after_the_system_prompt_and_each_shared_part():
    body = request_body(make_request(), MODEL)
    assert body["model"] == MODEL
    assert body["max_tokens"] == 2000
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert body["system"] == [{"type": "text", "text": KIND.system, "cache_control": {"type": "ephemeral"}}]
    assert len(body["messages"]) == 1
    message = body["messages"][0]
    assert message["role"] == "user"
    blocks = message["content"]
    assert [block["type"] for block in blocks] == ["text", "image", "image", "text"]
    assert blocks[0]["text"] == INSTRUCTIONS.text
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(PAGE_A.png).decode("ascii"),
    }
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert base64.standard_b64decode(blocks[2]["source"]["data"]) == CROP.png
    assert "cache_control" not in blocks[2]
    assert blocks[3] == {"type": "text", "text": QUESTION.text}


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_request_body_marks_only_the_last_block_of_a_shared_part():
    body = request_body(make_request(shared=((INSTRUCTIONS, PAGE_A),)), MODEL)
    blocks = body["messages"][0]["content"]
    assert "cache_control" not in blocks[0]
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    no_shared = request_body(make_request(shared=()), MODEL)
    assert not any("cache_control" in block for block in no_shared["messages"][0]["content"])
    assert no_shared["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_request_body_of_a_retry_follows_the_original_with_the_answer_and_the_fault():
    retry = Retry(answer='{"rows": ["F"]}', fault="2 rows expected, 1 given")
    body = request_body(make_request(retry=retry), MODEL)
    original = request_body(make_request(), MODEL)
    assert body["messages"][0] == original["messages"][0]
    assert body["messages"][1] == {"role": "assistant", "content": [{"type": "text", "text": retry.answer}]}
    assert body["messages"][2] == {"role": "user", "content": [{"type": "text", "text": retry.fault}]}
    assert len(body["messages"]) == 3


# ---------------------------------------------------------------- prices


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_cost_matches_two_requests_the_model_comparison_paid_for():
    """The rows are from `~/.re-pod/map-plan/model-compare/results.jsonl`: Sonnet high W1 and Opus low W1."""
    sonnet = ReadingUsage(input_tokens=1665, output_tokens=4161, cache_read_tokens=1841, cache_write_tokens=0)
    assert cost_usd("claude-sonnet-5-5", sonnet) == pytest.approx(0.045308, abs=1e-6)
    opus = ReadingUsage(input_tokens=1665, output_tokens=493, cache_read_tokens=0, cache_write_tokens=1841)
    assert cost_usd("claude-opus-5-5", opus) == pytest.approx(0.025725, abs=1e-6)
    with pytest.raises(ProviderError):
        cost_usd("claude-haiku-5-5", sonnet)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_reservation_is_the_input_at_the_cache_write_price_and_max_tokens_at_the_output_price():
    kind = RequestKind(name="probe", system="abc", schema={"type": "object"}, effort="low", max_tokens=1000)
    request = ReadingRequest(
        kind=kind, shared=((ImagePart(png=png_bytes(1, (100, 20))),),), question=(TextPart(text="hello world"),)
    )
    schema_bytes = len(json.dumps({"type": "object"}, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    # One token per UTF-8 byte of the system prompt ("abc") and the text ("hello world"), the schema's bytes,
    # ceil(100 * 20 / 750) for the image, and 64.
    expected_tokens = 3 + 11 + schema_bytes + -(-(100 * 20) // 750) + 64
    assert estimate_input_tokens(request) == expected_tokens
    prices = PRICES[MODEL]
    assert worst_case_usd(MODEL, request) == pytest.approx(expected_tokens * prices.cache_write + 1000 * prices.output)
    with pytest.raises(ProviderError):
        worst_case_usd("claude-haiku-5-5", request)


# ---------------------------------------------------------------- the adapter against a fake client


class FakeStream:
    """One streamed response: a first event after a short wait, then the final message.

    Like the SDK's stream, it has one iterator, so `get_final_message` after a
    partial iteration resumes the same stream instead of starting it again.
    """

    def __init__(self, client: FakeClient, index: int, message: Any) -> None:
        self.client = client
        self.index = index
        self.message = message
        self._events = self._generate()

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _generate(self):
        time.sleep(0.05)
        self.client.log("first_event", self.index)
        yield SimpleNamespace(type="message_start")
        yield SimpleNamespace(type="message_stop")

    def __iter__(self):
        return self._events

    def get_final_message(self) -> Any:
        for _ in self._events:
            pass
        self.client.log("final", self.index)
        return self.message


def final_message(
    data: object = ANSWER,
    stop_reason: str = "end_turn",
    input_tokens: int = 1665,
    output_tokens: int = 479,
    cache_write: int = 0,
    cache_read: int = 1841,
    model: str = MODEL,
) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(data))],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_write,
            cache_read_input_tokens=cache_read,
        ),
        stop_reason=stop_reason,
        model=model,
    )


class FakeClient:
    """Answers `messages.stream(**body)`, logging every step.

    A list of answers is a queue, consumed in the order requests arrive, for tests that send one request at a
    time. A mapping answers each request by the text of its last content block (the question), so a test of
    `read_all` doesn't depend on the order threads start their streams.
    """

    def __init__(self, answers: list[Any] | dict[str, Any]) -> None:
        self.queue = list(answers) if isinstance(answers, list) else []
        self.by_question = dict(answers) if isinstance(answers, dict) else {}
        self.bodies: list[dict[str, Any]] = []
        self.events: list[tuple[str, int]] = []
        self.lock = threading.Lock()
        self.messages = SimpleNamespace(stream=self.stream)

    def log(self, event: str, index: int) -> None:
        with self.lock:
            self.events.append((event, index))

    def stream(self, **body: Any) -> FakeStream:
        with self.lock:
            index = len(self.bodies)
            self.bodies.append(body)
            if self.by_question:
                answer = self.by_question[body["messages"][0]["content"][-1]["text"]]
            else:
                answer = self.queue.pop(0)
            self.events.append(("start", index))
        if isinstance(answer, Exception):
            raise answer
        return FakeStream(self, index, answer)


def ledger_file(tmp_path: Path, spent: float = 0.0, stop: float = 160.0) -> Path:
    path = tmp_path / "claude-ledger.json"
    path.write_text(
        json.dumps({"stop_usd": stop, "limit_usd": 200.0, "spent_usd": spent, "entries": []}), encoding="utf-8"
    )
    return path


def provider(
    tmp_path: Path, answers: list[Any] | dict[str, Any], stop: float = 160.0, spent: float = 0.0
) -> tuple[ClaudeProvider, FakeClient, Path]:
    client = FakeClient(answers)
    path = ledger_file(tmp_path, spent=spent, stop=stop)
    settings = ClaudeSettings(api_key=SecretStr("sk-ant-secret-key"), model=MODEL, ledger_path=path)
    return (
        ClaudeProvider(settings, client=client, sleep=lambda _: None, now=lambda: "2026-10-15T09:00:00"),
        client,
        path,
    )


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_read_returns_the_answer_with_its_cache_use_and_settles_the_ledger(tmp_path: Path):
    reader, client, path = provider(tmp_path, [final_message(cache_write=0, cache_read=1841)])
    response = reader.read(make_request())
    assert response.data == ANSWER
    assert response.model_id == MODEL
    assert response.usage == ReadingUsage(
        input_tokens=1665, output_tokens=479, cache_read_tokens=1841, cache_write_tokens=0
    )
    assert client.bodies == [request_body(make_request(), MODEL)]

    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert len(ledger["entries"]) == 1
    entry = ledger["entries"][0]
    assert entry == {
        "t": "2026-10-15T09:00:00",
        "script": "osrforge",
        "label": "probe low",
        "model": MODEL,
        "cost_usd": pytest.approx(cost_usd(MODEL, response.usage)),
    }
    assert ledger["spent_usd"] == pytest.approx(cost_usd(MODEL, response.usage))


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_read_reserves_the_worst_case_and_refuses_past_the_stop(tmp_path: Path):
    request = make_request()
    worst = worst_case_usd(MODEL, request)
    reader, client, path = provider(tmp_path, [final_message()], stop=1.0, spent=1.0 - worst / 2)
    before = path.read_bytes()
    with pytest.raises(LedgerStopError):
        reader.read(request)
    assert client.bodies == []
    assert path.read_bytes() == before

    reader, client, _ = provider(tmp_path, [final_message()], stop=1.0, spent=1.0 - worst)
    reader.read(request)
    assert len(client.bodies) == 1


@pytest.mark.xfail(reason=CHUNK, strict=True)
@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_a_stop_at_max_tokens_or_a_refusal_is_a_failure_that_is_still_paid_for(tmp_path: Path, stop_reason: str):
    reader, _, path = provider(tmp_path, [final_message(stop_reason=stop_reason)])
    with pytest.raises(ReadingFailure) as excinfo:
        reader.read(make_request())
    assert excinfo.value.stop_reason == stop_reason
    assert "probe" in str(excinfo.value) and stop_reason in str(excinfo.value)
    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert ledger["entries"][0]["cost_usd"] == pytest.approx(
        cost_usd(MODEL, ReadingUsage(input_tokens=1665, output_tokens=479, cache_read_tokens=1841))
    )
    assert "pending" not in ledger["entries"][0]


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_an_answer_that_does_not_fit_the_schema_is_a_schema_error(tmp_path: Path):
    reader, _, path = provider(tmp_path, [final_message(data={"rows": "not a list"})])
    with pytest.raises(SchemaValidationError):
        reader.read(make_request())
    assert "pending" not in json.loads(path.read_text(encoding="utf-8"))["entries"][0]


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_connection_failures_are_retried_then_raised_without_the_key(tmp_path: Path):
    failure = APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    reader, client, path = provider(tmp_path, [failure] * 5)
    with pytest.raises(ProviderError) as excinfo:
        reader.read(make_request())
    assert len(client.bodies) == 5
    assert "sk-ant-secret-key" not in str(excinfo.value)
    assert "sk-ant-secret-key" not in repr(reader.settings)
    ledger = json.loads(path.read_text(encoding="utf-8"))
    # Nothing arrived, so the worst case stays on the ledger.
    assert ledger["entries"][0]["cost_usd"] == pytest.approx(worst_case_usd(MODEL, make_request()))


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_read_all_sends_the_first_request_of_each_shared_part_alone(tmp_path: Path):
    """Two pages with three items each share the instructions: one request warms each part before the rest read it."""
    requests = [
        make_request(
            shared=((INSTRUCTIONS,), (page,)),
            question=(ImagePart(png=png_bytes(10 + 3 * page_index + i)), TextPart(text=f"Item {3 * page_index + i}")),
        )
        for page_index, page in enumerate((PAGE_A, PAGE_B))
        for i in range(3)
    ]
    answers = {f"Item {i}": final_message(data={"rows": [f"Item {i}"]}) for i in range(6)}
    reader, client, _ = provider(tmp_path, answers)
    responses = reader.read_all(requests)
    assert [r.data for r in responses if isinstance(r, ReadingResponse)] == [{"rows": [f"Item {i}"]} for i in range(6)]

    order = {event: i for i, event in enumerate(client.events)}
    sent = {i: body for i, body in enumerate(client.bodies)}

    def contains(body: dict[str, Any], part: ImagePart | TextPart) -> bool:
        wanted = part.text if isinstance(part, TextPart) else base64.standard_b64encode(part.png).decode("ascii")
        for block in body["messages"][0]["content"]:
            if "cache_control" not in block:
                continue
            value = block["text"] if block["type"] == "text" else block["source"]["data"]
            if value == wanted:
                return True
        return False

    for part in (INSTRUCTIONS, PAGE_A, PAGE_B):
        holders = sorted((i for i, body in sent.items() if contains(body, part)), key=lambda i: order[("start", i)])
        assert len(holders) == (6 if part is INSTRUCTIONS else 3)
        first = holders[0]
        for other in holders[1:]:
            assert order[("first_event", first)] < order[("start", other)], (part, first, other)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_read_all_returns_each_failure_in_its_place(tmp_path: Path):
    requests = [make_request(question=(ImagePart(png=png_bytes(20 + i)), TextPart(text=f"Item {i}"))) for i in range(3)]
    answers = {"Item 0": final_message(), "Item 1": final_message(stop_reason="max_tokens"), "Item 2": final_message()}
    reader, _, _ = provider(tmp_path, answers)
    results = reader.read_all(requests)
    assert isinstance(results[0], ReadingResponse) and isinstance(results[2], ReadingResponse)
    assert isinstance(results[1], ReadingFailure)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_settings_from_env_need_the_key_and_the_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in ("ANTHROPIC_API_KEY", "OSRFORGE_CLAUDE_LEDGER", "OSRFORGE_CLAUDE_MODEL", "OSRFORGE_CLAUDE_LEDGER_STOP"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ProviderError):
        ClaudeSettings.from_env()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-key")
    monkeypatch.setenv("OSRFORGE_CLAUDE_LEDGER", str(tmp_path / "ledger.json"))
    settings = ClaudeSettings.from_env()
    assert settings.api_key.get_secret_value() == "sk-ant-secret-key"
    assert settings.ledger_path == tmp_path / "ledger.json"
    assert settings.model in PRICES
    assert settings.ledger_stop_usd is None
    monkeypatch.setenv("OSRFORGE_CLAUDE_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("OSRFORGE_CLAUDE_LEDGER_STOP", "12.5")
    settings = ClaudeSettings.from_env()
    assert (settings.model, settings.ledger_stop_usd) == ("claude-opus-5-5", 12.5)
    assert "sk-ant-secret-key" not in repr(settings)


# ---------------------------------------------------------------- recordings


class StubReader:
    """A reading provider that answers from a queue, for the recorder."""

    def __init__(self, responses: list[ReadingResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[ReadingRequest] = []

    def read(self, request: ReadingRequest) -> ReadingResponse:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def read_all(self, requests) -> list[ReadingResponse | Exception]:
        results: list[ReadingResponse | Exception] = []
        for request in requests:
            try:
                results.append(self.read(request))
            except Exception as error:
                results.append(error)
        return results


RESPONSE = ReadingResponse(
    data=ANSWER, usage=ReadingUsage(input_tokens=100, output_tokens=10, cache_read_tokens=50), model_id=MODEL
)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_record_then_replay_round_trip(tmp_path: Path):
    request = make_request()
    recorder = RecordingReadingProvider(StubReader([RESPONSE]), tmp_path, MODEL)
    assert recorder.read(request) == RESPONSE

    path = tmp_path / recording_filename("probe", request.key(MODEL))
    assert path.is_file()
    recording = json.loads(path.read_text(encoding="utf-8"))
    assert recording["key"] == request.key(MODEL)
    assert recording["kind"] == "probe"
    assert recording["model"] == MODEL
    assert recording["response"]["data"] == ANSWER
    assert recording["response"]["stop_reason"] == "end_turn"
    assert recording["response"]["usage"] == RESPONSE.usage.model_dump()

    # The body is the exact body, with each image's data replaced by its pixel digest.
    text = path.read_text(encoding="utf-8")
    assert base64.standard_b64encode(PAGE_A.png).decode("ascii") not in text
    assert pixel_digest(PAGE_A.png) in text and pixel_digest(CROP.png) in text
    body = recording["request"]
    expected = request_body(request, MODEL)
    for block in expected["messages"][0]["content"]:
        if block["type"] == "image":
            block["source"]["data"] = pixel_digest(base64.standard_b64decode(block["source"]["data"]))
    assert body == expected

    replayed = RecordedReadingProvider(tmp_path, MODEL)
    assert replayed.read(request) == RESPONSE
    same_pixels = make_request(shared=((INSTRUCTIONS,), (ImagePart(png=reencoded(PAGE_A.png)),)))
    assert replayed.read(same_pixels) == RESPONSE
    assert replayed.read_all([request, same_pixels]) == [RESPONSE, RESPONSE]


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_replay_misses_and_failures(tmp_path: Path):
    request = make_request()
    failure = ReadingFailure("probe stopped at max_tokens", stop_reason="max_tokens")
    recorder = RecordingReadingProvider(StubReader([failure]), tmp_path, MODEL)
    with pytest.raises(ReadingFailure):
        recorder.read(request)
    replayed = RecordedReadingProvider(tmp_path, MODEL)
    with pytest.raises(ReadingFailure) as excinfo:
        replayed.read(request)
    assert excinfo.value.stop_reason == "max_tokens"

    with pytest.raises(FixtureMissError) as miss:
        replayed.read(make_request(question=(CROP, TextPart(text="Unrecorded."))))
    assert "probe" in str(miss.value) and str(tmp_path) in str(miss.value)
    with pytest.raises(FixtureMissError):
        RecordedReadingProvider(tmp_path, "claude-opus-5-5").read(request)

    results = replayed.read_all([request, make_request(question=(CROP, TextPart(text="Unrecorded.")))])
    assert isinstance(results[0], ReadingFailure) and isinstance(results[1], FixtureMissError)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_replay_revalidates_against_the_kind_schema(tmp_path: Path):
    request = make_request()
    RecordingReadingProvider(StubReader([RESPONSE]), tmp_path, MODEL).read(request)
    path = tmp_path / recording_filename("probe", request.key(MODEL))
    recording = json.loads(path.read_text(encoding="utf-8"))
    recording["response"]["data"] = {"rows": "not a list"}
    path.write_text(json.dumps(recording), encoding="utf-8")
    with pytest.raises(SchemaValidationError):
        RecordedReadingProvider(tmp_path, MODEL).read(request)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_replay_refuses_a_recording_whose_body_the_code_no_longer_builds(tmp_path: Path):
    """The key still matches, so only the stored body shows that the request's rendering changed."""
    request = make_request()
    RecordingReadingProvider(StubReader([RESPONSE]), tmp_path, MODEL).read(request)
    path = tmp_path / recording_filename("probe", request.key(MODEL))
    recording = json.loads(path.read_text(encoding="utf-8"))
    recording["request"]["system"][0]["text"] = "Rendered differently."
    path.write_text(json.dumps(recording), encoding="utf-8")
    with pytest.raises(StaleRecordingError):
        RecordedReadingProvider(tmp_path, MODEL).read(request)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_recording_through_the_adapter_stores_what_the_adapter_sent(tmp_path: Path):
    reader, client, _ = provider(tmp_path, [final_message(cache_write=1841, cache_read=0)])
    recorder = RecordingReadingProvider(reader, tmp_path / "recordings", MODEL)
    response = recorder.read(make_request())
    assert response.usage.cache_write_tokens == 1841
    recording = json.loads(
        (tmp_path / "recordings" / recording_filename("probe", make_request().key(MODEL))).read_text(encoding="utf-8")
    )
    assert recording["request"]["output_config"] == client.bodies[0]["output_config"]
    assert recording["response"]["usage"]["cache_write_tokens"] == 1841


def test_providers_satisfy_the_protocol(tmp_path: Path):
    assert isinstance(StubReader([]), ReadingProvider)
    assert isinstance(RecordedReadingProvider(tmp_path, MODEL), ReadingProvider)
    assert isinstance(RecordingReadingProvider(StubReader([]), tmp_path, MODEL), ReadingProvider)


# ---------------------------------------------------------------- run.json


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_run_json_records_each_request_with_its_cache_use(tmp_path: Path):
    """The stage name is a stand-in: the tracing stage's own name arrives with the wiring chunk."""
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
    first = RequestUsage(
        stage=Stage.MAPREAD, kind="probe", model_id=MODEL, input_tokens=100, output_tokens=10, cache_write_tokens=600
    )
    second = RequestUsage(
        stage=Stage.MAPREAD, kind="probe", model_id=MODEL, input_tokens=100, output_tokens=12, cache_read_tokens=600
    )
    with track_stage(workdir, Stage.MAPREAD) as tracker:
        tracker.add_request(first)
        tracker.add_request(second)
    run = workdir.read_run()
    assert run.requests == (first, second)
    assert run.stages[Stage.MAPREAD].status == "completed"

    with pytest.raises(RuntimeError), track_stage(workdir, Stage.MAPREAD) as tracker:
        tracker.add_request(first)
        raise RuntimeError("boom")
    assert workdir.read_run().requests == (first, second, first)
