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
[`ClaudeProvider`][osrforge.providers.claude.ClaudeProvider].
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from osrforge.providers.base import ImagePart, TextPart

__all__ = [
    "MAX_SHARED_PARTS",
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

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


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
            or more content parts and ends at a cache breakpoint. At most
            [`MAX_SHARED_PARTS`][osrforge.providers.reading.MAX_SHARED_PARTS].
            A shared part needs at least 512 tokens to be cached, which the
            caller sees in the response's usage.
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
        raise NotImplementedError("chunk: claude-provider")

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
        raise NotImplementedError("chunk: claude-provider")


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


def recording_filename(kind: str, key: str) -> str:
    """Return the filename a recording is stored under: `<kind>.<key[:12]>.json`.

    Args:
        kind: The request kind's name.
        key: The recording key.

    Returns:
        The filename.
    """
    return f"{kind}.{key[:12]}.json"


class RecordedReadingProvider:
    """Replays recorded answers, so tests and continuous integration never call Claude.

    A recording file is JSON with `schema_version`, `key`, `kind`, `model`,
    `request`, and `response`. `request` is the exact body the live adapter
    sent ([`request_body`][osrforge.providers.claude.request_body]) with each
    image block's `data` replaced by the image's pixel digest, so a reader can
    see every parameter and the recording stays small. `response` has `data`,
    `usage`, `model_id`, and `stop_reason`.
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
            ReadingFailure: If the recording's `stop_reason` is `max_tokens`
                or `refusal`, as the live answer failed.
            SchemaValidationError: If the recorded data doesn't fit the
                kind's schema.
            ProviderError: If the recording's schema version isn't this
                package's.
        """
        raise NotImplementedError("chunk: claude-provider")

    def read_all(self, requests: Sequence[ReadingRequest]) -> list[ReadingResponse | Exception]:
        """Replay each request in order.

        Args:
            requests: The requests.

        Returns:
            One item per request: its recorded answer, or the exception
            `read` raises for it.
        """
        raise NotImplementedError("chunk: claude-provider")


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
        raise NotImplementedError("chunk: claude-provider")

    def read_all(self, requests: Sequence[ReadingRequest]) -> list[ReadingResponse | Exception]:
        """Read all through the inner provider's `read_all` and record every exchange.

        Args:
            requests: The requests.

        Returns:
            The inner provider's items, unchanged.
        """
        raise NotImplementedError("chunk: claude-provider")
