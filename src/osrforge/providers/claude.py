"""The Claude adapter: the only module allowed to import `anthropic`.

It fills the tracing stage's reading role
([`ReadingProvider`][osrforge.providers.reading.ReadingProvider]) through the
Messages API: it streams every response, sends images as base64 PNG, asks
for each answer as JSON that fits the kind's schema (`output_config.format`),
runs every request with adaptive thinking at the kind's effort, and marks a
cache breakpoint on the system prompt and at the end of each shared part. It
reserves each request's worst-case cost on the Claude ledger before sending
and settles it from the answer's usage. The body it sends is
[`request_body`][osrforge.providers.reading.request_body]. The server-side
refusal `fallbacks` parameter stays off, because a fallback would answer on
another model mid-run and change the recording key with it.
"""

import hashlib
import io
import json
import math
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import anthropic
import httpx2
from PIL import Image
from pydantic import BaseModel, ConfigDict, SecretStr

from osrforge.errors import ProviderError, ReadingFailure, SchemaValidationError
from osrforge.providers.base import ImagePart, TextPart, ensure_schema
from osrforge.providers.ledger import Ledger
from osrforge.providers.reading import THINKING, ReadingRequest, ReadingResponse, ReadingUsage, request_body

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
    "worst_case_usd",
]

API_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "OSRFORGE_CLAUDE_MODEL"
LEDGER_ENV = "OSRFORGE_CLAUDE_LEDGER"
LEDGER_STOP_ENV = "OSRFORGE_CLAUDE_LEDGER_STOP"

DEFAULT_MODEL = "claude-sonnet-5-5"
"""The model until the screening picks one: development runs on Sonnet."""

_ATTEMPTS = 5
"""The most attempts the provider makes at one request."""

_MAX_BACKOFF_SECONDS = 30.0

_RETRIED_STATUSES = frozenset({408, 409, 429})
"""The 4xx statuses the provider retries; it retries every 5xx too."""

_MAX_THREADS = 8
"""The most requests `read_all` has in flight at once."""

_FAILED_STOP_REASONS = frozenset({"max_tokens", "refusal"})

_MAX_IMAGE_EDGE = 2576
"""The longest edge, in pixels, that the priced models read an image at; a longer image is scaled down."""

_MAX_IMAGE_PIXELS = 3_750_000
"""The most pixels the priced models read an image at; a larger image is scaled down."""


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
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        ledger = os.environ.get(LEDGER_ENV, "").strip()
        missing = [name for name, value in ((API_KEY_ENV, api_key), (LEDGER_ENV, ledger)) if not value]
        if missing:
            raise ProviderError(f"missing required environment variable(s): {', '.join(missing)}")
        stop_text = os.environ.get(LEDGER_STOP_ENV, "").strip()
        stop: float | None = None
        if stop_text:
            try:
                stop = float(stop_text)
            except ValueError:
                stop = math.nan
            if not math.isfinite(stop) or stop < 0:
                raise ProviderError(f"{LEDGER_STOP_ENV} isn't a number of dollars: {stop_text!r}")
        return cls(
            api_key=SecretStr(api_key),
            ledger_path=Path(ledger).expanduser(),
            model=os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL,
            ledger_stop_usd=stop,
        )


def estimate_input_tokens(request: ReadingRequest) -> int:
    """Estimate a request's input tokens from above, for the reservation.

    The estimate is one token per UTF-8 byte of every text part, the system
    prompt, the retry's answer and fault, and the schema's compact JSON
    (sorted keys, compact separators), plus `ceil(width * height / 750)` for
    each image, plus 64. An image is counted at the size the API reads it:
    an image longer than 2,576 pixels on its long edge, or larger than 3.75
    megapixels, is scaled down to fit both limits with its proportions kept,
    and each side of the scaled size is rounded up. A token is never shorter
    than a byte, and the API charges an image about one token per 784 pixels
    of that size (about 4,784 tokens at the limit), so the estimate is an
    upper bound on what the answer's usage reports, and the
    over-reservation is small beside `max_tokens` at the output price.

    Args:
        request: The request.

    Returns:
        The estimate.
    """
    kind = request.kind
    texts = [kind.system, json.dumps(kind.schema, sort_keys=True, separators=(",", ":"))]
    image_tokens = 0
    for part in (*(item for shared in request.shared for item in shared), *request.question):
        if isinstance(part, TextPart):
            texts.append(part.text)
        else:
            width, height = _read_size(*_image_size(part))
            image_tokens += -(-(width * height) // 750)
    if request.retry is not None:
        texts.extend((request.retry.answer, request.retry.fault))
    return sum(len(text.encode("utf-8")) for text in texts) + image_tokens + 64


def _image_size(part: ImagePart) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(part.png)) as image:
            return image.size
    except (OSError, ValueError) as error:
        raise ValueError(f"an image part isn't an image Pillow can read: {error}") from error


def _read_size(width: int, height: int) -> tuple[int, int]:
    """Return the size the API reads an image at, each side rounded up so the area stays an upper bound."""
    scale = min(1.0, _MAX_IMAGE_EDGE / max(width, height), math.sqrt(_MAX_IMAGE_PIXELS / (width * height)))
    return math.ceil(width * scale), math.ceil(height * scale)


def _prices(model: str) -> ModelPrices:
    prices = PRICES.get(model)
    if prices is None:
        raise ProviderError(
            f"no prices for the model {model!r}, so the ledger can't reserve against it "
            f"(priced models: {', '.join(sorted(PRICES))})"
        )
    return prices


def worst_case_usd(model: str, request: ReadingRequest) -> float:
    """Return a request's worst-case cost: its input at the cache-write price and `max_tokens` at the output price.

    Args:
        model: The model id, a key of `PRICES`.
        request: The request.

    Returns:
        The worst-case cost in dollars, rounded to whole micro-dollars as
        the ledger keeps it.

    Raises:
        ProviderError: If the model has no prices.
    """
    prices = _prices(model)
    return round(estimate_input_tokens(request) * prices.cache_write + request.kind.max_tokens * prices.output, 6)


def cost_usd(model: str, usage: ReadingUsage) -> float:
    """Return what an answer cost at list price.

    Args:
        model: The model id, a key of `PRICES`.
        usage: The answer's usage.

    Returns:
        `input_tokens * input + output_tokens * output + cache_write_tokens * cache_write + cache_read_tokens *
        cache_read`, in dollars, rounded to whole micro-dollars as the ledger keeps it.

    Raises:
        ProviderError: If the model has no prices.
    """
    prices = _prices(model)
    return round(
        usage.input_tokens * prices.input
        + usage.output_tokens * prices.output
        + usage.cache_write_tokens * prices.cache_write
        + usage.cache_read_tokens * prices.cache_read,
        6,
    )


class ClaudeProvider:
    """Claude over the Messages API, with the ledger and the cache discipline.

    The constructor performs no I/O. Transport policy: at most five attempts
    per request, with exponential backoff bounded at 30 seconds, on 408,
    409, 429, every 5xx, and connection failures, including a transport
    failure while the stream is read. A `Retry-After` header sets the wait,
    within the same bound. An error event inside a stream is retried like a
    5xx; the SDK raises it with the stream's own status, which is 200. Any
    other 4xx, including an auth failure, raises at once. The attempts share
    the request's one reservation, settled once at the end, also when the
    answer can't be read. A malformed answer isn't re-prompted here, because
    the tracing stage makes the one retry with its own recording. No error
    message contains the API key.
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
        _prices(settings.model)
        self.settings = settings
        self._client: Any = (
            client
            if client is not None
            else anthropic.Anthropic(api_key=settings.api_key.get_secret_value(), max_retries=0)
        )
        self._sleep = sleep
        self._now = now if now is not None else _local_time
        self._ledger = Ledger(settings.ledger_path, cost_key="cost_usd", stop_usd=settings.ledger_stop_usd)

    def read(self, request: ReadingRequest) -> ReadingResponse:
        """Send one request and return its answer.

        Before sending, the provider reserves
        [`worst_case_usd`][osrforge.providers.claude.worst_case_usd] on a new
        ledger entry `{"t", "script": "osrforge", "label": "<kind> <effort>",
        "model"}` in one step
        ([`Ledger.open_reserved`][osrforge.providers.ledger.Ledger.open_reserved]),
        so a refusal writes nothing to the ledger. It streams the response,
        settles the reservation at
        [`cost_usd`][osrforge.providers.claude.cost_usd] of the answer's usage
        whether or not the answer is good (a request whose attempts all fail
        keeps its worst case), and then checks the answer: a
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
        return self._read(request, None)

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
        model = self.settings.model
        chains = [_cache_prefixes(request, model) for request in requests]
        results: list[ReadingResponse | Exception] = [ProviderError("the request didn't finish") for _ in requests]
        warm: set[tuple[str, ...]] = set()
        claimed: set[tuple[str, ...]] = set()
        waiting = list(range(len(requests)))
        in_flight = 0
        changed = threading.Condition()

        def run(index: int, cold: list[tuple[str, ...]]) -> None:
            nonlocal in_flight

            def first_event() -> None:
                with changed:
                    warm.update(cold)
                    changed.notify_all()

            try:
                results[index] = self._read(requests[index], first_event if cold else None)
            except Exception as error:
                results[index] = error
            finally:
                with changed:
                    # A request that failed before its first event leaves its parts cold for another to warm.
                    claimed.difference_update(cold)
                    in_flight -= 1
                    changed.notify_all()

        with ThreadPoolExecutor(max_workers=_MAX_THREADS) as pool, changed:
            while waiting or in_flight:
                for index in list(waiting):
                    cold = [prefix for prefix in chains[index] if prefix not in warm]
                    # Prefixes nest, so the first cold one is claimed whenever any of them is.
                    if cold and cold[0] in claimed:
                        continue
                    waiting.remove(index)
                    claimed.update(cold)
                    in_flight += 1
                    pool.submit(run, index, cold)
                changed.wait()
        return results

    def _read(self, request: ReadingRequest, on_first_event: Callable[[], None] | None) -> ReadingResponse:
        model = self.settings.model
        kind = request.kind
        entry = {"t": self._now(), "script": "osrforge", "label": f"{kind.name} {kind.effort}", "model": model}
        reservation = self._ledger.open_reserved(entry, worst_case_usd(model, request))
        try:
            message = self._send(request_body(request, model), kind.name, on_first_event)
            usage = _usage(message, kind.name)
        except BaseException as error:
            # Nothing says what the attempts cost, so the worst case stays on the ledger.
            try:
                self._ledger.settle(reservation, reservation.usd)
            except Exception as settle_error:
                error.add_note(
                    f"settling its ledger reservation failed too, so the entry stays pending: {settle_error}"
                )
            raise
        self._ledger.settle(reservation, cost_usd(model, usage))
        return _answer(request, message, usage)

    def _send(self, body: dict[str, Any], kind: str, on_first_event: Callable[[], None] | None) -> Any:
        last_error: anthropic.APIError | httpx2.TransportError | None = None
        for attempt in range(_ATTEMPTS):
            delay = min(2.0**attempt, _MAX_BACKOFF_SECONDS)
            try:
                with self._client.messages.stream(**body) as stream:
                    if on_first_event is not None:
                        for _ in stream:
                            on_first_event()
                            on_first_event = None
                            break
                    return stream.get_final_message()
            except (anthropic.APIConnectionError, httpx2.TransportError) as error:
                # The SDK wraps a transport failure while it sends the request, but one while it reads the
                # stream escapes as the httpx2 error itself.
                last_error = error
            except anthropic.APIStatusError as error:
                # The SDK raises an error event inside a stream with the stream's own status, which is below 400.
                status = error.status_code
                if status >= 400 and status < 500 and status not in _RETRIED_STATUSES:
                    raise ProviderError(self._redact(f"the {kind!r} request failed: {error}")) from error
                last_error = error
                retry_after = _retry_after_seconds(error)
                if retry_after is not None:
                    delay = min(retry_after, _MAX_BACKOFF_SECONDS)
            except anthropic.APIError as error:
                raise ProviderError(self._redact(f"the {kind!r} request failed: {error}")) from error
            if attempt + 1 < _ATTEMPTS:
                self._sleep(delay)
        raise ProviderError(
            self._redact(f"the {kind!r} request failed after {_ATTEMPTS} attempts: {last_error}")
        ) from last_error

    def _redact(self, text: str) -> str:
        secret = self.settings.api_key.get_secret_value()
        return text.replace(secret, "[the API key]") if secret else text


def _local_time() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def _retry_after_seconds(error: anthropic.APIStatusError) -> float | None:
    headers = error.response.headers
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            return max(0.0, float(value) * scale)
        except ValueError:
            pass
        if scale == 1.0:
            try:
                target = parsedate_to_datetime(value)
            except TypeError, ValueError:
                return None
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            return max(0.0, (target - datetime.now(UTC)).total_seconds())
    return None


def _usage(message: Any, kind: str) -> ReadingUsage:
    usage = getattr(message, "usage", None)
    if usage is None:
        raise ProviderError(f"the answer to the {kind!r} request has no usage")
    return ReadingUsage(
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
    )


def _answer(request: ReadingRequest, message: Any, usage: ReadingUsage) -> ReadingResponse:
    kind = request.kind.name
    stop_reason = message.stop_reason
    if stop_reason in _FAILED_STOP_REASONS:
        category = getattr(getattr(message, "stop_details", None), "category", None)
        detail = f" (category {category})" if category else ""
        raise ReadingFailure(f"the {kind!r} request stopped with stop reason {stop_reason!r}{detail}", stop_reason)
    text = next((block.text for block in message.content if block.type == "text"), None)
    if text is None:
        raise SchemaValidationError(f"the answer to the {kind!r} request has no text block")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise SchemaValidationError(f"the answer to the {kind!r} request isn't JSON: {error}") from error
    ensure_schema(data, request.kind.schema, kind)
    return ReadingResponse(data=data, usage=usage, model_id=message.model)


def _cache_prefixes(request: ReadingRequest, model: str) -> list[tuple[str, ...]]:
    """Name a request's cached prefixes: the system prompt, then each shared part added in turn.

    The cache matches the bytes a request sends, so a prefix is named by hashes of those bytes, not of
    the pixels, and each name extends the one before it.
    """
    kind = request.kind
    head = {
        "model": model,
        "system": kind.system,
        "effort": kind.effort,
        "thinking": THINKING,
        "schema": kind.schema,
        "max_tokens": kind.max_tokens,
    }
    prefixes: list[tuple[str, ...]] = [(_hash(head),)]
    for part in request.shared:
        blocks = [
            ["text", item.text] if isinstance(item, TextPart) else ["image", hashlib.sha256(item.png).hexdigest()]
            for item in part
        ]
        prefixes.append((*prefixes[-1], _hash(blocks)))
    return prefixes


def _hash(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
