"""The decision provider: Clef's role in the tracing stage.

Clef answers a fixed-choice question about one image with a probability for
each option. The tracing stage asks it about each edge that touches floor,
each cell the ink measurements don't settle, and each label-sized mark, one
question per request, with a red box on the crop marking the item. A
[`DecisionRequest`][osrforge.providers.decisions.DecisionRequest] is one such
question, and a [`DecisionResponse`][osrforge.providers.decisions.DecisionResponse]
is its probabilities. The live adapter is `ClefProvider` in
`osrforge.providers.clef`.
"""

import re
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ClefModel",
    "DecisionProvider",
    "DecisionRequest",
    "DecisionResponse",
    "ImageFormat",
]

ClefModel = Literal["clef", "clef-flash"]
"""The two models Workers AI hosts: Clef, and its smaller sibling."""

ImageFormat = Literal["png", "jpeg"]
"""How a request's image was sent."""

_TAG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class DecisionRequest:
    """One question about one image.

    Attributes:
        tag: A stable identifier like `edge` or `cell`, used in recording
            filenames, never free prose.
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
        separators, UTF-8) of the model, the image's
        [`pixel_digest`][osrforge.providers.base.pixel_digest], the state,
        the question, and the options as a list of `[id, description]`
        pairs. The format the image was sent in isn't part of the key: the
        same crop sent as PNG or as JPEG is the same request.

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
