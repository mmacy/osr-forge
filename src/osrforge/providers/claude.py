"""The Claude adapter: the only module allowed to import `anthropic`.

It fills the tracing stage's reading role
([`ReadingProvider`][osrforge.providers.reading.ReadingProvider]) through the
Messages API: it streams every response, sends images as base64 PNG, asks
for each answer as JSON that fits the kind's schema (`output_config.format`),
runs every request with adaptive thinking at the kind's effort, and marks a
cache breakpoint on the system prompt and at the end of each shared part. It
reserves each request's worst-case cost on the Claude ledger before sending
and settles it from the answer's usage.
"""

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr

from osrforge.providers.reading import ReadingRequest, ReadingResponse, ReadingUsage

__all__ = [
    "API_KEY_ENV",
    "LEDGER_ENV",
    "LEDGER_STOP_ENV",
    "MODEL_ENV",
    "PRICES",
    "ClaudeProvider",
    "ClaudeSettings",
    "ModelPrices",
    "cost_usd",
    "estimate_input_tokens",
    "request_body",
    "worst_case_usd",
]

API_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "OSRFORGE_CLAUDE_MODEL"
LEDGER_ENV = "OSRFORGE_CLAUDE_LEDGER"
LEDGER_STOP_ENV = "OSRFORGE_CLAUDE_LEDGER_STOP"

DEFAULT_MODEL = "claude-sonnet-5-5"
"""The model until the screening picks one: development runs on Sonnet."""


class ModelPrices(BaseModel):
    """One model's list prices in dollars per token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: float
    output: float
    cache_write: float
    """The price of writing a token to the cache with the five-minute lifetime."""

    cache_read: float


PRICES: dict[str, ModelPrices] = {
    "claude-sonnet-5-5": ModelPrices(input=2e-6, output=10e-6, cache_write=2.5e-6, cache_read=0.2e-6),
    "claude-opus-5-5": ModelPrices(input=4e-6, output=20e-6, cache_write=5e-6, cache_read=0.2e-6),
}
"""The list prices of the models the provider accepts, in dollars per token.

A model missing here can't be reserved against the ledger, so the provider
refuses it.
"""


class ClaudeSettings(BaseModel):
    """The provider's settings: the key, the model, and the ledger.

    Examples:
        ```python
        from pathlib import Path

        from pydantic import SecretStr

        from osrforge.providers.claude import ClaudeProvider, ClaudeSettings

        settings = ClaudeSettings(api_key=SecretStr("sk-..."), ledger_path=Path("claude-ledger.json"))
        provider = ClaudeProvider(settings)
        ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr
    """The API key. Never written to a log, a recording, or an error message."""

    ledger_path: Path
    """The Claude ledger file."""

    model: str = DEFAULT_MODEL
    """The model id. One model serves every kind of request. It must be in `PRICES`."""

    ledger_stop_usd: float | None = None
    """The stop, or `None` to use the ledger file's own."""

    @classmethod
    def from_env(cls) -> ClaudeSettings:
        """Build settings from the environment.

        Returns:
            Settings from `ANTHROPIC_API_KEY` and `OSRFORGE_CLAUDE_LEDGER`
            (both required), `OSRFORGE_CLAUDE_MODEL`, and
            `OSRFORGE_CLAUDE_LEDGER_STOP`.

        Raises:
            ProviderError: If a required variable is missing or empty, or the
                stop isn't a number.
        """
        raise NotImplementedError("chunk: claude-provider")


def estimate_input_tokens(request: ReadingRequest) -> int:
    """Estimate a request's input tokens from above, for the reservation.

    The estimate is the sum over every text part, the system prompt, the
    retry's answer and fault, and the schema's compact JSON of
    `ceil(bytes / 3)` in UTF-8, plus `ceil(width * height / 750)` for each
    image, plus 64. It counts every token at the uncached price, so it's an
    upper bound on what the answer's usage reports.

    Args:
        request: The request.

    Returns:
        The estimate.
    """
    raise NotImplementedError("chunk: claude-provider")


def worst_case_usd(model: str, request: ReadingRequest) -> float:
    """Return a request's worst-case cost: its input at the cache-write price and `max_tokens` at the output price.

    Args:
        model: The model id, a key of `PRICES`.
        request: The request.

    Returns:
        The worst-case cost in dollars.

    Raises:
        ProviderError: If the model has no prices.
    """
    raise NotImplementedError("chunk: claude-provider")


def cost_usd(model: str, usage: ReadingUsage) -> float:
    """Return what an answer cost at list price.

    Args:
        model: The model id, a key of `PRICES`.
        usage: The answer's usage.

    Returns:
        `input_tokens * input + output_tokens * output + cache_write_tokens * cache_write + cache_read_tokens *
        cache_read`, in dollars.

    Raises:
        ProviderError: If the model has no prices.
    """
    raise NotImplementedError("chunk: claude-provider")


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
        The body, as the keyword arguments of `messages.stream`.
    """
    raise NotImplementedError("chunk: claude-provider")


class ClaudeProvider:
    """Claude over the Messages API, with the ledger and the cache discipline.

    The constructor performs no I/O. Transport policy: bounded exponential
    backoff on 429, 5xx, and connection failures, honouring `Retry-After`;
    auth and other 4xx failures raise at once. Each request is one API call:
    a malformed answer isn't re-prompted here, because the tracing stage
    makes the one retry with its own recording.
    """

    def __init__(
        self,
        settings: ClaudeSettings,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], str] | None = None,
    ) -> None:
        """Bind to a model and a ledger.

        Args:
            settings: The settings.
            client: An injectable client with the `anthropic.Anthropic`
                interface, for tests. When omitted, a real client is built
                from the settings with the SDK's retries off, so the
                provider's own backoff is the only retry.
            sleep: The backoff sleeper, injectable for tests.
            now: Gives the ledger entry's `t` as local time
                `YYYY-MM-DDTHH:MM:SS`; injectable for tests.

        Raises:
            ProviderError: If the settings' model has no prices.
        """
        self.settings = settings
        self._client = client
        self._sleep = sleep
        self._now = now

    def read(self, request: ReadingRequest) -> ReadingResponse:
        """Send one request and return its answer.

        Before sending, the provider opens a ledger entry `{"t", "script":
        "osrforge", "label": "<kind> <effort>", "model"}` and reserves
        [`worst_case_usd`][osrforge.providers.claude.worst_case_usd] against
        it. It streams the response, settles the reservation at
        [`cost_usd`][osrforge.providers.claude.cost_usd] of the answer's usage
        whether or not the answer is good, and then checks the answer: a
        `stop_reason` of `max_tokens` or `refusal` is a
        [`ReadingFailure`][osrforge.errors.ReadingFailure], and the first text
        block's JSON is checked against the kind's schema.

        Args:
            request: The request.

        Returns:
            The answer, with `cache_read_tokens` and `cache_write_tokens`
            from the usage's `cache_read_input_tokens` and
            `cache_creation_input_tokens`.

        Raises:
            ReadingFailure: If the answer stopped at `max_tokens` or with a
                refusal.
            SchemaValidationError: If the answer doesn't fit the kind's
                schema.
            LedgerStopError: If the reservation would pass the stop.
            ProviderError: On transport, auth, or rate-limit exhaustion.
        """
        raise NotImplementedError("chunk: claude-provider")

    def read_all(self, requests: Sequence[ReadingRequest]) -> list[ReadingResponse | Exception]:
        """Send several requests, each shared part written to the cache by one request before the rest read it.

        Requests are sent on threads. A request is sent when every shared
        part it contains is warm, or when it is the one request chosen to
        warm the first of its parts that isn't. A part becomes warm when the
        first event of that request's stream arrives. So the first request
        of a run goes alone, and requests about different pages that share
        the same instructions go one after the other at first and then
        together.

        Args:
            requests: The requests.

        Returns:
            One item per request, in order: its answer, or the exception
            `read` raised for it.
        """
        raise NotImplementedError("chunk: claude-provider")
