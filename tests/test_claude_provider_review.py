"""The Claude adapter's review fixes: a bounded Retry-After wait, one settle on every path, a transport failure while
a stream is read, and images counted at the size the API reads them.

No test here calls the service. The adapter runs against a fake client shaped like the SDK's `messages.stream`.
"""

import io
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from PIL import Image
from pydantic import SecretStr

from osrforge.errors import ProviderError
from osrforge.providers.base import ImagePart, TextPart
from osrforge.providers.claude import ClaudeProvider, ClaudeSettings, estimate_input_tokens, worst_case_usd
from osrforge.providers.reading import ReadingRequest, RequestKind

MODEL = "claude-sonnet-5-5"
URL = "https://api.anthropic.com/v1/messages"
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"rows": {"type": "array", "items": {"type": "string"}}},
    "required": ["rows"],
    "additionalProperties": False,
}
KIND = RequestKind(name="probe", system="You read printed maps.", schema=SCHEMA, effort="low", max_tokens=2000)
REQUEST = ReadingRequest(
    kind=KIND, shared=((TextPart(text="Instructions: read the window."),),), question=(TextPart(text="What is it?"),)
)
USAGE = SimpleNamespace(input_tokens=100, output_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def final_message(usage: object = USAGE) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps({"rows": ["F"]}))],
        usage=usage,
        stop_reason="end_turn",
        model=MODEL,
    )


class FakeStream:
    """A streamed response that returns its final message, or raises its error while it is read."""

    def __init__(self, final: Any = None, error: Exception | None = None) -> None:
        self.final = final
        self.error = error

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_final_message(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.final


class FakeClient:
    """Answers `messages.stream` from a queue: an exception raised as the request is sent, or a stream."""

    def __init__(self, answers: list[Exception | FakeStream], on_send: Callable[[], None] | None = None) -> None:
        self.answers = list(answers)
        self.on_send = on_send
        self.sent = 0
        self.messages = SimpleNamespace(stream=self.stream)

    def stream(self, **_: Any) -> FakeStream:
        self.sent += 1
        if self.on_send is not None:
            self.on_send()
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def make_provider(
    tmp_path: Path, answers: list[Exception | FakeStream], on_send: Callable[[], None] | None = None
) -> tuple[ClaudeProvider, FakeClient, Path, list[float]]:
    path = tmp_path / "claude-ledger.json"
    path.write_text(
        json.dumps({"stop_usd": 160.0, "limit_usd": 200.0, "spent_usd": 0.0, "entries": []}), encoding="utf-8"
    )
    client = FakeClient(answers, on_send)
    sleeps: list[float] = []
    settings = ClaudeSettings(api_key=SecretStr("sk-ant-secret-key"), model=MODEL, ledger_path=path)
    reader = ClaudeProvider(settings, client=client, sleep=sleeps.append, now=lambda: "2026-10-16T09:00:00")
    return reader, client, path, sleeps


def status_error(error: type[anthropic.APIStatusError], status: int, headers: dict[str, str]) -> Exception:
    response = httpx2.Response(status, headers=headers, request=httpx2.Request("POST", URL))
    return error(f"Error code: {status}", response=response, body=None)


def only_entry(path: Path) -> dict[str, Any]:
    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert len(ledger["entries"]) == 1
    assert ledger["spent_usd"] == ledger["entries"][0]["cost_usd"]
    return ledger["entries"][0]


def test_a_retry_after_wait_is_bounded_at_thirty_seconds(tmp_path: Path):
    answers: list[Exception | FakeStream] = [
        status_error(anthropic.RateLimitError, 429, {"retry-after": "3600"}),
        status_error(anthropic.RateLimitError, 429, {"retry-after-ms": "7200000"}),
        status_error(anthropic.RateLimitError, 429, {"retry-after": "2"}),
        FakeStream(final_message()),
    ]
    reader, client, _, sleeps = make_provider(tmp_path, answers)
    assert reader.read(REQUEST).data == {"rows": ["F"]}
    assert client.sent == 4
    assert sleeps == [30.0, 30.0, 2.0]


def test_a_failed_settle_leaves_the_request_error_in_front_with_the_settle_failure_noted(tmp_path: Path):
    def break_the_ledger() -> None:
        (tmp_path / "claude-ledger.json").write_text("not json", encoding="utf-8")

    reader, _, _, _ = make_provider(tmp_path, [status_error(anthropic.BadRequestError, 400, {})], break_the_ledger)
    with pytest.raises(ProviderError) as excinfo:
        reader.read(REQUEST)
    assert "'probe' request failed" in str(excinfo.value) and "400" in str(excinfo.value)
    notes = " ".join(getattr(excinfo.value, "__notes__", []))
    assert "settling its ledger reservation failed too" in notes and "isn't JSON" in notes


def test_an_answer_without_usage_is_settled_at_the_worst_case(tmp_path: Path):
    reader, _, path, _ = make_provider(tmp_path, [FakeStream(final_message(usage=None))])
    with pytest.raises(ProviderError, match="no usage"):
        reader.read(REQUEST)
    entry = only_entry(path)
    assert "pending" not in entry
    assert entry["cost_usd"] == worst_case_usd(MODEL, REQUEST)


def test_a_transport_failure_while_the_stream_is_read_is_retried(tmp_path: Path):
    dropped = FakeStream(error=httpx2.RemoteProtocolError("peer closed connection without sending complete message"))
    reader, client, path, sleeps = make_provider(tmp_path, [dropped, FakeStream(final_message())])
    assert reader.read(REQUEST).data == {"rows": ["F"]}
    assert client.sent == 2 and sleeps == [1.0]
    assert "pending" not in only_entry(path)

    failures: list[Exception | FakeStream] = [FakeStream(error=httpx2.ReadError("connection reset")) for _ in range(5)]
    (tmp_path / "again").mkdir()
    reader, client, path, _ = make_provider(tmp_path / "again", failures)
    with pytest.raises(ProviderError, match="after 5 attempts"):
        reader.read(REQUEST)
    assert client.sent == 5
    entry = only_entry(path)
    assert "pending" not in entry and entry["cost_usd"] == worst_case_usd(MODEL, REQUEST)


def png(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", size).save(buffer, "PNG")
    return buffer.getvalue()


def image_tokens(size: tuple[int, int]) -> int:
    """The estimate's tokens for one image: the request's other inputs are a one-byte system prompt and the schema."""
    kind = RequestKind(name="probe", system="s", schema={"type": "object"}, effort="low", max_tokens=10)
    request = ReadingRequest(kind=kind, shared=(), question=(ImagePart(png=png(size)),))
    return estimate_input_tokens(request) - len("s") - len('{"type":"object"}') - 64


def test_a_large_image_is_counted_at_the_size_the_api_reads_it():
    # Within both limits, an image is counted whole.
    assert image_tokens((100, 20)) == 3
    assert image_tokens((2576, 1400)) == -(-(2576 * 1400) // 750)
    # A 5000 by 4000 scan is read at about 3.75 megapixels, which the API charges about 4,784 tokens. Counted whole,
    # it would be 26,667.
    scan = image_tokens((5000, 4000))
    assert 4784 <= scan <= 5100
    # A long strip is scaled down to 2,576 pixels on its long edge.
    strip = image_tokens((10000, 100))
    assert -(-(2576 * 25) // 750) <= strip <= -(-(2576 * 26) // 750)
