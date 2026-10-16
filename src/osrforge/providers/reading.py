"""The reading provider: Claude's role in the tracing stage, and the recordings tests replay.

The tracing stage asks Claude to read a map page whole, to judge the items
Clef is unsure of, and to pick the offset between two halves of a map. Each
of those is a *kind* of request ([`RequestKind`][osrforge.providers.reading.RequestKind]):
one system prompt, one output schema, one effort, and one `max_tokens`, fixed
for every request of that kind so that requests of a kind share prompt-cache
entries. A [`ReadingRequest`][osrforge.providers.reading.ReadingRequest] is
the kind plus its content, arranged for the cache: the shared parts first,
each ending at a cache breakpoint, and the crop and the question last.

A recording is one answer filed under its key, the hash of the request's
pixels and parameters, so tests replay it without calling the service.
[`RecordedReadingProvider`][osrforge.providers.reading.RecordedReadingProvider]
replays a directory of recordings and
[`RecordingReadingProvider`][osrforge.providers.reading.RecordingReadingProvider]
writes them. The live adapter is
[`ClaudeProvider`][osrforge.providers.claude.ClaudeProvider]. A recording of
a private corpus member's map may contain the module's text or picture, so
those recordings live in the owner's private corpus directory and never in
the repository; only the committed corpus members' and the synthetic maps'
recordings are committed.
"""

import base64
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from osrforge.errors import FixtureMissError, ProviderError, ReadingFailure, StaleRecordingError
from osrforge.providers.base import ImagePart, TextPart, ensure_schema, pixel_digest
from osrforge.versioning import SCHEMA_VERSION
from osrforge.workdir import write_json_artifact

__all__ = [
    "MAX_SHARED_PARTS",
    "MAX_SHARED_PART_BLOCKS",
    "THINKING",
    "Effort",
    "ReadingProvider",
    "ReadingRequest",
    "ReadingResponse",
    "ReadingUsage",
    "RecordedReadingProvider",
    "RecordingReadingProvider",
    "RequestKind",
    "Retry",
    "recording_filename",
    "request_body",
]

Effort = Literal["low", "medium", "high", "xhigh", "max"]
"""The API's effort setting: how much the model thinks before it answers."""

THINKING = "adaptive"
"""The one thinking setting every reading request sends.

Every request runs with the API's adaptive thinking, because Claude Opus 5.5
can't turn thinking off, and a change of thinking setting would break the
prompt cache. The setting is part of every recording key.
"""

MAX_SHARED_PARTS = 3
"""The most shared parts a request may have.

The API allows four cache breakpoints in one request. The system prompt
takes one, so a request has at most three shared parts.
"""

MAX_SHARED_PART_BLOCKS = 20
"""The most content parts one shared part may have.

The cache looks back twenty blocks from each breakpoint for the previous
one, so a shared part is a few blocks: the instructions as one text part,
or a page as one image part, never a long run of parts.
"""

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

_FAILED_STOP_REASONS = frozenset({"max_tokens", "refusal"})
"""The stop reasons that leave no answer to check."""

_ANSWERED = "end_turn"
"""The stop reason a recording of an answer carries."""


def _canonical_hash(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _key_blocks(parts: Iterable[TextPart | ImagePart]) -> list[dict[str, str]]:
    return [{"text": part.text} if isinstance(part, TextPart) else {"image": pixel_digest(part.png)} for part in parts]


@dataclass(frozen=True)
class RequestKind:
    """One kind of request: what stays the same for every request of that kind.

    Attributes:
        name: A stable identifier like `whole_map` or `items`, used in
            recording filenames and ledger labels, never free prose.
        system: The system prompt. The provider marks it as cached.
        schema: The JSON Schema every answer of this kind must satisfy. The
            API enforces it (`output_config.format`), and the provider checks
            the answer against it again.
        effort: The effort for this kind.
        max_tokens: The most output tokens a request of this kind allows,
            with room above the longest answer measured for the kind.
    """

    name: str
    system: str
    schema: dict[str, object]
    effort: Effort
    max_tokens: int

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(f"request kind name must be a stable identifier ([A-Za-z0-9._-]+): {self.name!r}")
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be positive: {self.max_tokens}")


@dataclass(frozen=True)
class Retry:
    """The malformed answer and the fault message that a retry adds after the original request.

    Attributes:
        answer: The malformed answer's text, as the service returned it.
        fault: The message that names what was wrong with it.
    """

    answer: str
    fault: str


@dataclass(frozen=True)
class ReadingRequest:
    """One request to the reading provider.

    The provider renders it as one user message: every shared part in order,
    then the question, with a cache breakpoint on the last block of each
    shared part. A retry adds the malformed answer as an assistant message
    and the fault as a second user message after that.

    Attributes:
        kind: The kind of request.
        shared: The shared parts, in order: the instructions, the legend, the
            symbol vocabulary, and any worked examples first, then the page
            image when several requests are about one page. Each part is one
            or more content parts, at most
            [`MAX_SHARED_PART_BLOCKS`][osrforge.providers.reading.MAX_SHARED_PART_BLOCKS],
            and ends at a cache breakpoint. At most
            [`MAX_SHARED_PARTS`][osrforge.providers.reading.MAX_SHARED_PARTS].
            The cache stores a prefix of at least 512 tokens: the whole
            prompt up to a breakpoint, from the system prompt on, not each
            part on its own. The caller sees what was cached in the
            response's usage.
        question: The crop and the question, after the shared parts. At
            least one content part.
        retry: The malformed answer and the fault, when this request is the
            one retry of an earlier request.
    """

    kind: RequestKind
    shared: tuple[tuple[TextPart | ImagePart, ...], ...]
    question: tuple[TextPart | ImagePart, ...]
    retry: Retry | None = None

    def __post_init__(self) -> None:
        if len(self.shared) > MAX_SHARED_PARTS:
            raise ValueError(f"a request has at most {MAX_SHARED_PARTS} shared parts, not {len(self.shared)}")
        if any(len(part) == 0 for part in self.shared):
            raise ValueError("every shared part has at least one content part")
        if any(len(part) > MAX_SHARED_PART_BLOCKS for part in self.shared):
            raise ValueError(f"a shared part has at most {MAX_SHARED_PART_BLOCKS} content parts")
        if len(self.question) == 0:
            raise ValueError("the question has at least one content part")

    def key(self, model: str) -> str:
        """Return the recording key: the hash of the request's pixels and parameters.

        The key is the sha256 hex of the canonical JSON (sorted keys, compact
        separators, UTF-8) of the model, the system prompt, each shared part
        and the question as lists of blocks (a text part as its text, an image
        part as its [`pixel_digest`][osrforge.providers.base.pixel_digest]),
        the effort, [`THINKING`][osrforge.providers.reading.THINKING], the
        schema, `max_tokens`, and the retry's answer and fault when there is
        one. The shared parts stay grouped in that JSON, so moving a cache
        breakpoint changes the key.

        Args:
            model: The model id the request would be sent to.

        Returns:
            A 64-character sha256 hex digest.
        """
        identity = self._prefix_identity(model)
        identity["question"] = _key_blocks(self.question)
        identity["retry"] = None if self.retry is None else {"answer": self.retry.answer, "fault": self.retry.fault}
        return _canonical_hash(identity)

    def shared_key(self, model: str) -> str:
        """Return the hash of the request's cached prefix: the model, the kind, and the shared parts.

        Two requests with the same shared key read the same cache entries,
        so [`ReadingProvider.read_all`][osrforge.providers.reading.ReadingProvider.read_all]
        sends one of them first and the rest once that one's answer starts to
        arrive. The hash covers the model, the system prompt, the effort,
        [`THINKING`][osrforge.providers.reading.THINKING], the schema,
        `max_tokens`, and the shared parts the same way `key` does, and
        leaves out the question and the retry.

        Args:
            model: The model id the request would be sent to.

        Returns:
            A 64-character sha256 hex digest.
        """
        return _canonical_hash(self._prefix_identity(model))

    def _prefix_identity(self, model: str) -> dict[str, object]:
        return {
            "model": model,
            "system": self.kind.system,
            "effort": self.kind.effort,
            "thinking": THINKING,
            "schema": self.kind.schema,
            "max_tokens": self.kind.max_tokens,
            "shared": [_key_blocks(part) for part in self.shared],
        }


class ReadingUsage(BaseModel):
    """One answer's token usage, with the cache reads and writes the ledger prices separately."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    """Input tokens charged at the full price."""

    output_tokens: int = Field(default=0, ge=0)

    cache_read_tokens: int = Field(default=0, ge=0)
    """Input tokens read from the prompt cache."""

    cache_write_tokens: int = Field(default=0, ge=0)
    """Input tokens written to the prompt cache."""

    def __add__(self, other: ReadingUsage) -> ReadingUsage:
        return ReadingUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class ReadingResponse:
    """One answer that fits its kind's schema.

    Attributes:
        data: The parsed JSON, already validated against the kind's schema.
        usage: The answer's token usage.
        model_id: The model id the service returned.
    """

    data: object
    usage: ReadingUsage
    model_id: str


@runtime_checkable
class ReadingProvider(Protocol):
    """The reading role of the tracing stage, which Claude fills.

    Any object with these two methods is a reading provider. A provider owns
    its transport retries, its ledger, and its schema check: `read` returns
    data that fits the kind's schema or raises.
    """

    def read(self, request: ReadingRequest) -> ReadingResponse:
        """Send one request and return its answer.

        Args:
            request: The request.

        Returns:
            The schema-valid answer with its usage.

        Raises:
            ReadingFailure: If the answer stopped at `max_tokens` or with a
                refusal.
            SchemaValidationError: If the answer doesn't fit the kind's
                schema.
            LedgerStopError: If the request's reservation would pass the
                ledger's stop.
            ProviderError: On transport, auth, or rate-limit exhaustion.
        """
        ...

    def read_all(self, requests: Sequence[ReadingRequest]) -> list[ReadingResponse | Exception]:
        """Send several requests, ordered so each shared part is written to the cache once.

        For each distinct cached prefix
        ([`ReadingRequest.shared_key`][osrforge.providers.reading.ReadingRequest.shared_key]),
        the first request is sent alone, and the others wait until that
        request's answer starts to arrive. Requests whose every shared part
        is already in the cache go at the same time. A request that fails
        doesn't stop the others.

        Args:
            requests: The requests, in any order.

        Returns:
            One item per request, in the requests' order: its answer, or the
            exception `read` would have raised for it.
        """
        ...


def request_body(request: ReadingRequest, model: str) -> dict[str, Any]:
    """Build the exact body the provider sends for a request.

    The body has `model`, `max_tokens`, `system` as one text block with
    `cache_control`, `messages`, `thinking` as `{"type": "adaptive"}`, and
    `output_config` with the kind's effort and its schema as the JSON schema
    format. The first message is the user message: the blocks of each shared
    part in order, then the question's blocks. A text part is a text block
    and an image part is a base64 PNG image block. The last block of each
    shared part has `cache_control: {"type": "ephemeral"}`, and no block of
    the question does. A retry adds an assistant message with the malformed
    answer's text and a user message with the fault.

    Args:
        request: The request.
        model: The model id.

    Returns:
        The body, as the keyword arguments of the SDK's `messages.stream`.
    """
    return _body(request, model, lambda png: base64.standard_b64encode(png).decode("ascii"))


def _recorded_body(request: ReadingRequest, model: str) -> dict[str, Any]:
    """The request body as a recording stores it: each image's data replaced by its pixel digest."""
    return _body(request, model, pixel_digest)


def _body(request: ReadingRequest, model: str, image_data: Callable[[bytes], str]) -> dict[str, Any]:
    def block(part: TextPart | ImagePart) -> dict[str, Any]:
        if isinstance(part, TextPart):
            return {"type": "text", "text": part.text}
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_data(part.png)}}

    content: list[dict[str, Any]] = []
    for part in request.shared:
        blocks = [block(item) for item in part]
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
        content.extend(blocks)
    content.extend(block(item) for item in request.question)
    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
    if request.retry is not None:
        messages.append({"role": "assistant", "content": [{"type": "text", "text": request.retry.answer}]})
        messages.append({"role": "user", "content": [{"type": "text", "text": request.retry.fault}]})
    kind = request.kind
    return {
        "model": model,
        "max_tokens": kind.max_tokens,
        "system": [{"type": "text", "text": kind.system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        "thinking": {"type": THINKING},
        "output_config": {"effort": kind.effort, "format": {"type": "json_schema", "schema": kind.schema}},
    }


def recording_filename(kind: str, key: str) -> str:
    """Return the filename a recording is stored under: `<kind>.<key[:12]>.json`.

    Args:
        kind: The request kind's name.
        key: The recording key.

    Returns:
        The filename.
    """
    return f"{kind}.{key[:12]}.json"


class _RecordedResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data: object
    usage: ReadingUsage
    model_id: str
    stop_reason: str


class _Recording(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    key: str
    kind: str
    model: str
    request: dict[str, object]
    response: _RecordedResponse


class RecordedReadingProvider:
    """Replays recorded answers, so tests and continuous integration never call Claude.

    A recording file is JSON with `schema_version`, `key`, `kind`, `model`,
    `request`, and `response`. `request` is the exact body the live adapter
    sent ([`request_body`][osrforge.providers.reading.request_body]) with each
    image block's `data` replaced by the image's pixel digest, so a reader can
    see every parameter and the recording stays small. `response` has `data`,
    `usage`, `model_id`, and `stop_reason`. On replay, the provider builds
    the body the current code would send, in the same digest form, and
    compares it with the stored one, so a recording whose key still matches
    but whose body the code no longer builds fails as stale instead of
    answering.
    """

    def __init__(self, recordings: Path, model: str) -> None:
        """Bind to a directory of recordings made on one model.

        Args:
            recordings: The directory of `<kind>.<key[:12]>.json` files.
            model: The model id the recordings were made on. It is part of
                every key, so a request is replayed only against recordings of
                its own model.
        """
        self.recordings = recordings
        self.model = model

    def read(self, request: ReadingRequest) -> ReadingResponse:
        """Replay the recording whose key matches the request.

        The replayed data is checked against the kind's schema again, so a
        changed schema fails as a `SchemaValidationError`, not as a wrong
        answer.

        Args:
            request: The request to replay.

        Returns:
            The recorded answer.

        Raises:
            FixtureMissError: If no recording has the request's key.
            StaleRecordingError: If the stored body, with digests in place of
                image data, isn't the body the current code builds for the
                request.
            ReadingFailure: If the recording's `stop_reason` is `max_tokens`
                or `refusal`, as the live answer failed.
            SchemaValidationError: If the recorded data doesn't fit the
                kind's schema.
            ProviderError: If the recording's schema version isn't this
                package's.
        """
        key = request.key(self.model)
        kind = request.kind.name
        path = self.recordings / recording_filename(kind, key)
        if not path.is_file():
            raise FixtureMissError(
                f"no recording of the {kind!r} request with key {key} in {self.recordings} "
                f"(recorded kinds: {self._recorded_kinds() or 'none'})"
            )
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ProviderError(f"recording {path} isn't JSON: {error}") from error
        version: object = cast(dict[str, Any], raw).get("schema_version") if isinstance(raw, dict) else None
        if version != SCHEMA_VERSION:
            raise ProviderError(f"recording {path} has schema version {version}, expected {SCHEMA_VERSION}")
        try:
            recording = _Recording.model_validate(raw)
        except ValidationError as error:
            raise ProviderError(f"recording {path} is malformed: {error}") from error
        if recording.key != key:
            raise FixtureMissError(f"recording {path} has key {recording.key}, but the {kind!r} request's is {key}")
        if recording.request != json.loads(json.dumps(_recorded_body(request, self.model))):
            raise StaleRecordingError(
                f"recording {path} stores a body the current code no longer builds for the {kind!r} request; "
                "record it again"
            )
        response = recording.response
        if response.stop_reason in _FAILED_STOP_REASONS:
            raise ReadingFailure(
                f"the recorded answer to the {kind!r} request stopped with stop reason {response.stop_reason!r}",
                stop_reason=response.stop_reason,
            )
        ensure_schema(response.data, request.kind.schema, str(path))
        return ReadingResponse(data=response.data, usage=response.usage, model_id=response.model_id)

    def read_all(self, requests: Sequence[ReadingRequest]) -> list[ReadingResponse | Exception]:
        """Replay each request in order.

        Args:
            requests: The requests.

        Returns:
            One item per request: its recorded answer, or the exception
            `read` raises for it.
        """
        results: list[ReadingResponse | Exception] = []
        for request in requests:
            try:
                results.append(self.read(request))
            except Exception as error:
                results.append(error)
        return results

    def _recorded_kinds(self) -> list[str]:
        # Filenames are <kind>.<key[:12]>.json, and kind names may contain dots.
        return sorted({path.name.rsplit(".", 2)[0] for path in self.recordings.glob("*.json")})


class RecordingReadingProvider:
    """Passes each request to a live provider and writes its answer as a recording.

    A failed answer (`max_tokens` or a refusal) is recorded too, with its
    `stop_reason`, so a replay fails the same way. Writing is idempotent by
    key.
    """

    def __init__(self, inner: ReadingProvider, recordings: Path, model: str) -> None:
        """Wrap a live provider.

        Args:
            inner: The provider that answers.
            recordings: Where recordings land; created if missing.
            model: The model id `inner` sends to, for the keys.
        """
        self.inner = inner
        self.recordings = recordings
        self.model = model

    def read(self, request: ReadingRequest) -> ReadingResponse:
        """Read through the inner provider and record the exchange.

        Args:
            request: The request.

        Returns:
            The inner provider's answer, unchanged.
        """
        try:
            response = self.inner.read(request)
        except ReadingFailure as failure:
            self._record_failure(request, failure)
            raise
        self._record(request, response)
        return response

    def read_all(self, requests: Sequence[ReadingRequest]) -> list[ReadingResponse | Exception]:
        """Read all through the inner provider's `read_all` and record every exchange.

        Args:
            requests: The requests.

        Returns:
            The inner provider's items, unchanged.
        """
        items = self.inner.read_all(requests)
        for request, item in zip(requests, items, strict=True):
            if isinstance(item, ReadingResponse):
                self._record(request, item)
            elif isinstance(item, ReadingFailure):
                self._record_failure(request, item)
        return items

    def _record(self, request: ReadingRequest, response: ReadingResponse) -> None:
        self._write(
            request,
            _RecordedResponse(
                data=response.data, usage=response.usage, model_id=response.model_id, stop_reason=_ANSWERED
            ),
        )

    def _record_failure(self, request: ReadingRequest, failure: ReadingFailure) -> None:
        self._write(
            request,
            _RecordedResponse(data=None, usage=ReadingUsage(), model_id=self.model, stop_reason=failure.stop_reason),
        )

    def _write(self, request: ReadingRequest, response: _RecordedResponse) -> None:
        key = request.key(self.model)
        recording = _Recording(
            schema_version=SCHEMA_VERSION,
            key=key,
            kind=request.kind.name,
            model=self.model,
            request=_recorded_body(request, self.model),
            response=response,
        )
        self.recordings.mkdir(parents=True, exist_ok=True)
        write_json_artifact(self.recordings / recording_filename(request.kind.name, key), recording)
