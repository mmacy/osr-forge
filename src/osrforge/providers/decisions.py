"""The decision provider: Clef's role in the tracing stage.

Clef answers a fixed-choice question about one image with a probability for
each option. The tracing stage asks it about each edge that touches floor,
each cell the ink measurements don't settle, and each label-sized mark, one
question per request, with a red box on the crop marking the item. A
[`DecisionRequest`][osrforge.providers.decisions.DecisionRequest] is one such
question, and a [`DecisionResponse`][osrforge.providers.decisions.DecisionResponse]
is its probabilities. The live adapter is
[`ClefProvider`][osrforge.providers.clef.ClefProvider].

A recording is one answer filed under its key, the hash of the request's
pixels and parameters, so tests replay it without calling the service.
[`RecordedDecisionProvider`][osrforge.providers.decisions.RecordedDecisionProvider]
replays a directory of recordings and
[`RecordingDecisionProvider`][osrforge.providers.decisions.RecordingDecisionProvider]
writes them. Clef has no version pin, so before a sweep the
[`drift_check`][osrforge.providers.decisions.drift_check] sends a sample of
recorded requests again and reports every answer that moved. A recording of
a private corpus member's map may contain the module's picture, so those
recordings live in the owner's private corpus directory and never in the
repository; only the committed corpus members' and the synthetic maps'
recordings are committed.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "JPEG_QUALITY",
    "JPEG_THRESHOLD_BYTES",
    "ClefModel",
    "DecisionProvider",
    "DecisionRequest",
    "DecisionResponse",
    "Drift",
    "DriftKind",
    "ImageFormat",
    "RecordedDecisionProvider",
    "RecordingDecisionProvider",
    "drift_check",
    "drift_sample",
    "encode_request",
    "recording_filename",
    "request_body",
]

ClefModel = Literal["clef", "clef-flash"]
"""The two models Workers AI hosts: Clef, and its smaller sibling."""

ImageFormat = Literal["png", "jpeg"]
"""How a request's image was sent."""

_TAG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

JPEG_THRESHOLD_BYTES = 100_000
"""A request body over this many bytes is rebuilt with its image as JPEG. Part of every key.

Workers AI rejects bodies far smaller than its documented 13 MiB: the probe
saw rejections from about 512 KiB. A 2 by 2 cell crop at 128 pixels per cell
is about 32 KB as PNG and 20 KB as JPEG, so edge requests go as PNG and only
the larger crops are re-encoded.
"""

JPEG_QUALITY = 90
"""The JPEG quality a large crop is re-encoded at. Part of every key."""


@dataclass(frozen=True)
class DecisionRequest:
    """One question about one image.

    Attributes:
        tag: A stable identifier like `edge` or `cell`, never free prose.
            It names recording files and is the question's id in the
            request, which Clef reads along with the question.
        image: The crop, as PNG.
        state: The text the service reads before the question: the map
            style in words, and what the red box marks.
        question: The question, like "What is drawn on the cell edge inside
            the red box?".
        options: The options, each an id and a description (or `None` for an
            option whose id says enough), in the order they're given. Clef
            sorts options by id before it reads them, so the order doesn't
            change the answer, but it's part of the key.
        model: Which model answers.
    """

    tag: str
    image: bytes
    state: str
    question: str
    options: tuple[tuple[str, str | None], ...]
    model: ClefModel = "clef"

    def __post_init__(self) -> None:
        if not _TAG_PATTERN.match(self.tag):
            raise ValueError(f"request tag must be a stable identifier ([A-Za-z0-9._-]+): {self.tag!r}")
        ids = [option_id for option_id, _ in self.options]
        if len(ids) < 2:
            raise ValueError("a decision needs at least two options")
        if len(set(ids)) != len(ids):
            raise ValueError(f"option ids repeat: {ids}")

    def key(self) -> str:
        """Return the recording key: the hash of the request's pixels and parameters.

        The key is the sha256 hex of the canonical JSON (sorted keys, compact
        separators, UTF-8) of the model, the tag, the image's
        [`pixel_digest`][osrforge.providers.base.pixel_digest] (of the source
        PNG, whatever format is sent), the state, the question, the options
        as a list of `[id, description]` pairs, the format the image goes as
        (what [`encode_request`][osrforge.providers.decisions.encode_request]
        chooses), [`JPEG_QUALITY`][osrforge.providers.decisions.JPEG_QUALITY],
        and [`JPEG_THRESHOLD_BYTES`][osrforge.providers.decisions.JPEG_THRESHOLD_BYTES],
        so a change in how an image is sent changes the key.

        Returns:
            A 64-character sha256 hex digest.
        """
        raise NotImplementedError("chunk: clef-provider")


class DecisionResponse(BaseModel):
    """One answer: a probability for every option.

    The provider computes `choice`, `confidence`, and `margin` from the
    probabilities and ignores the `confidence` field the service returns,
    whose formula isn't documented.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    probabilities: dict[str, float]
    """Each option id's probability. They sum to 1 within rounding."""

    model_id: str
    """The model the service says answered."""

    input_tokens: int = Field(ge=0)
    """The input tokens the service charged. Clef has no output tokens."""

    format: ImageFormat
    """How the image was sent: PNG, or JPEG when the PNG body was too large."""

    body_sha256: str
    """The sha256 of the exact request body sent, which the drift check compares with a recording's."""

    @model_validator(mode="after")
    def _probabilities_are_a_distribution(self) -> DecisionResponse:
        if len(self.probabilities) < 2:
            raise ValueError("a decision has at least two options")
        if any(p < 0.0 or p > 1.0 for p in self.probabilities.values()):
            raise ValueError(f"probabilities lie in [0, 1]: {self.probabilities}")
        total = sum(self.probabilities.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"probabilities sum to 1, not {total}")
        return self

    @property
    def choice(self) -> str:
        """The option with the highest probability, the first by id on a tie."""
        return min(self.probabilities, key=lambda option: (-self.probabilities[option], option))

    @property
    def confidence(self) -> float:
        """The top probability."""
        return self.probabilities[self.choice]

    @property
    def margin(self) -> float:
        """How far the top probability leads the second: the drift sample favours small margins."""
        ordered = sorted(self.probabilities.values(), reverse=True)
        return ordered[0] - ordered[1]


@runtime_checkable
class DecisionProvider(Protocol):
    """The decision role of the tracing stage, which Clef fills."""

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Send one question and return its probabilities.

        Args:
            request: The question.

        Returns:
            The answer.

        Raises:
            LedgerStopError: If the request's reservation would pass the
                ledger's stop.
            ProviderError: On transport, auth, or rate-limit exhaustion, or
                a rejection that stays after its retries.
        """
        ...


def request_body(request: DecisionRequest, image_format: ImageFormat) -> bytes:
    """Build the exact body for a request, with its image in the given format.

    The body is the canonical JSON (sorted keys, compact separators, UTF-8)
    of `{"model": request.model, "images": [{"content_type": "image/png" or
    "image/jpeg", "base64": ...}], "state": request.state, "questions":
    {request.tag: {"type": "choice", "instructions": request.question,
    "criteria": {option id: description}}}}`. The JPEG is the PNG's pixels
    saved by Pillow at [`JPEG_QUALITY`][osrforge.providers.decisions.JPEG_QUALITY].

    Args:
        request: The request.
        image_format: How to send the image.

    Returns:
        The body bytes.
    """
    raise NotImplementedError("chunk: clef-provider")


def encode_request(request: DecisionRequest) -> tuple[bytes, ImageFormat]:
    """Return the body the provider sends: PNG, or JPEG when the PNG body is too large.

    Args:
        request: The request.

    Returns:
        The PNG body and `"png"` when that body is at most
        [`JPEG_THRESHOLD_BYTES`][osrforge.providers.decisions.JPEG_THRESHOLD_BYTES]
        long, else the JPEG body and `"jpeg"`.
    """
    raise NotImplementedError("chunk: clef-provider")


def recording_filename(tag: str, key: str) -> str:
    """Return the filename a recording is stored under: `<tag>.<key[:12]>.json`.

    Args:
        tag: The request's tag.
        key: The recording key.

    Returns:
        The filename.
    """
    return f"{tag}.{key[:12]}.json"


class RecordedDecisionProvider:
    """Replays recorded answers, so tests and continuous integration never call Clef.

    A recording file is JSON with `schema_version`, `key`, `tag`, `request`,
    `body_bytes`, and `response`. `request` is `{"model", "state", "question",
    "options", "image": {"pixels": digest, "width", "height"}}`: the exact
    body's content with the image's data replaced by its pixel digest.
    `response` is the [`DecisionResponse`][osrforge.providers.decisions.DecisionResponse]
    as dumped, so it records the format the image went as and the sha256 of
    the exact body sent.
    """

    def __init__(self, recordings: Path) -> None:
        """Bind to a directory of recordings.

        Args:
            recordings: The directory of `<tag>.<key[:12]>.json` files.
        """
        self.recordings = recordings

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Replay the recording whose key matches the request.

        Args:
            request: The request to replay.

        Returns:
            The recorded answer.

        Raises:
            FixtureMissError: If no recording has the request's key.
            ProviderError: If the recording's schema version isn't this
                package's.
        """
        raise NotImplementedError("chunk: clef-provider")


class RecordingDecisionProvider:
    """Passes each request to a live provider and writes its answer as a recording.

    Writing is idempotent by key.
    """

    def __init__(self, inner: DecisionProvider, recordings: Path) -> None:
        """Wrap a live provider.

        Args:
            inner: The provider that answers.
            recordings: Where recordings land; created if missing.
        """
        self.inner = inner
        self.recordings = recordings

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Decide through the inner provider and record the exchange.

        Args:
            request: The request.

        Returns:
            The inner provider's answer, unchanged.
        """
        raise NotImplementedError("chunk: clef-provider")


DriftKind = Literal["body", "choice", "probability"]
"""What moved: the body sent, the top answer, or one option's probability."""


@dataclass(frozen=True)
class Drift:
    """One way a recorded answer no longer holds.

    Attributes:
        tag: The request's tag.
        key: The recording key.
        kind: What moved.
        reason: The recorded and the new values, in words.
    """

    tag: str
    key: str
    kind: DriftKind
    reason: str


def drift_check(
    recordings: Path,
    provider: DecisionProvider,
    requests: Sequence[DecisionRequest],
    tolerance: float = 0.03,
) -> tuple[Drift, ...]:
    """Send recorded requests again and report every answer that moved.

    Each request is sent through `provider`, and its answer is compared with
    the recording under the request's key. A body drift is reported when the
    body the provider sent doesn't have the recording's `body_sha256`, so
    the request wasn't resent exactly. The probabilities are compared
    whether or not the body matched: a choice drift when the top answer
    changed, and a probability drift when any option's probability moved by
    more than `tolerance`. One request can yield several drifts. A sweep
    stops when this returns anything.

    Args:
        recordings: The recordings directory.
        provider: The live provider.
        requests: The sample to resend, each with a recording, as
            [`drift_sample`][osrforge.providers.decisions.drift_sample]
            chooses them.
        tolerance: How far a probability may move.

    Returns:
        The drifts, in the requests' order and then body, choice,
        probability; empty when nothing moved.

    Raises:
        FixtureMissError: If a request has no recording.
    """
    raise NotImplementedError("chunk: clef-provider")


def drift_sample(recordings: Path, requests: Sequence[DecisionRequest], size: int = 50) -> tuple[DecisionRequest, ...]:
    """Choose the requests the drift check resends from those that have recordings.

    A recording holds no image, so the caller supplies the requests: the
    sweep's planned requests, which it builds from the maps. Among those
    with a recording, half of the sample (rounded down) is the ones whose
    recorded margins are smallest, where the top answer led the next by the
    least, because those are the answers a changed model moves first. The
    rest are taken at even intervals through the remaining recorded
    requests in key order. When at most `size` requests have recordings, the
    sample is all of them.

    Args:
        recordings: The recordings directory.
        requests: The requests to choose from.
        size: The sample size.

    Returns:
        The chosen requests, sorted by key.
    """
    raise NotImplementedError("chunk: clef-provider")
