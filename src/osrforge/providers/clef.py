"""The Clef adapter: Cloudflare's decision model on Workers AI, over its REST endpoint.

It fills the tracing stage's decision role
([`DecisionProvider`][osrforge.providers.decisions.DecisionProvider]). Each
request is one HTTP POST to the model-in-path endpoint with the System One
body Clef extends: one image, the state, and one choice question. The image
goes as PNG, and a body over
[`JPEG_THRESHOLD_BYTES`][osrforge.providers.clef.JPEG_THRESHOLD_BYTES] is
rebuilt with the image as JPEG at quality 90. The adapter reserves each
request's estimated cost on the Clef ledger before sending and settles it
from the usage the answer reports. No vendor SDK: the transport is a
function the constructor takes, so tests give it a fake.
"""

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, SecretStr

from osrforge.providers.decisions import DecisionRequest, DecisionResponse, ImageFormat

__all__ = [
    "ACCOUNT_ENV",
    "ENDPOINT",
    "JPEG_QUALITY",
    "JPEG_THRESHOLD_BYTES",
    "LEDGER_ENV",
    "LEDGER_STOP_ENV",
    "PRICE_PER_TOKEN",
    "TOKEN_ENV",
    "ClefProvider",
    "ClefSettings",
    "Transport",
    "cost_usd",
    "encode_request",
    "estimate_cost",
    "estimate_tokens",
    "request_body",
    "wrangler_token",
]

ACCOUNT_ENV = "OSRFORGE_CLEF_ACCOUNT_ID"
TOKEN_ENV = "OSRFORGE_CLEF_TOKEN"
LEDGER_ENV = "OSRFORGE_CLEF_LEDGER"
LEDGER_STOP_ENV = "OSRFORGE_CLEF_LEDGER_STOP"

ENDPOINT = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/{model}"
"""The endpoint with the model in the path. The universal `/ai/run` endpoint rejects the body's `model` field."""

PRICE_PER_TOKEN: dict[str, float] = {"clef": 0.240e-6, "clef-flash": 0.090e-6}
"""List prices in dollars per input token. Clef charges no output tokens."""

JPEG_THRESHOLD_BYTES = 100_000
"""A request body over this many bytes is rebuilt with its image as JPEG.

Workers AI rejects bodies far smaller than its documented 13 MiB: the probe
saw rejections from about 512 KiB. A 2 by 2 cell crop at 128 pixels per cell
is about 32 KB as PNG and 20 KB as JPEG, so edge requests go as PNG and only
the larger crops are re-encoded.
"""

JPEG_QUALITY = 90

Transport = Callable[[str, bytes, Mapping[str, str]], tuple[int, bytes]]
"""Sends one POST, `(url, body, headers)`, and returns `(status, response body)`. May raise `OSError`."""


class ClefSettings(BaseModel):
    """The provider's settings: the account, the token, and the ledger.

    Examples:
        ```python
        from pathlib import Path

        from pydantic import SecretStr

        from osrforge.providers.clef import ClefProvider, ClefSettings, wrangler_token

        settings = ClefSettings(
            account_id="...", token=SecretStr(wrangler_token()), ledger_path=Path("clef-ledger.json")
        )
        provider = ClefProvider(settings)
        ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    """The Cloudflare account id in the endpoint's path."""

    token: SecretStr
    """The bearer token. Never written to a log, a recording, or an error message."""

    ledger_path: Path
    """The Clef ledger file."""

    ledger_stop_usd: float | None = None
    """The stop, or `None` to use the ledger file's own."""

    run_label: str = "osrforge"
    """The `what` of the ledger entry this provider's requests accrue to."""

    @classmethod
    def from_env(cls) -> ClefSettings:
        """Build settings from the environment and the wrangler login.

        Returns:
            Settings from `OSRFORGE_CLEF_ACCOUNT_ID` and `OSRFORGE_CLEF_LEDGER`
            (both required), `OSRFORGE_CLEF_LEDGER_STOP`, and the token from
            `OSRFORGE_CLEF_TOKEN` or, when that is unset, from
            [`wrangler_token`][osrforge.providers.clef.wrangler_token].

        Raises:
            ProviderError: If a required variable is missing or empty, the
                stop isn't a number, or no token can be found.
        """
        raise NotImplementedError("chunk: clef-provider")


def wrangler_token(config_path: Path | None = None) -> str:
    """Return the OAuth token of the wrangler login.

    `wrangler login` stores its token as `oauth_token` in
    `default.toml` under wrangler's config directory:
    `~/Library/Preferences/.wrangler/config/` on macOS and
    `~/.config/.wrangler/config/` elsewhere.

    Args:
        config_path: The TOML file, or `None` for the platform's default.

    Returns:
        The token.

    Raises:
        ProviderError: If the file is missing or has no `oauth_token`. The
            message names the path, never any of the file's values.
    """
    raise NotImplementedError("chunk: clef-provider")


def request_body(request: DecisionRequest, image_format: ImageFormat) -> bytes:
    """Build the exact body for a request, with its image in the given format.

    The body is the canonical JSON (sorted keys, compact separators, UTF-8)
    of `{"model": request.model, "images": [{"content_type": "image/png" or
    "image/jpeg", "base64": ...}], "state": request.state, "questions":
    {request.tag: {"type": "choice", "instructions": request.question,
    "criteria": {option id: description}}}}`. The JPEG is the PNG's pixels
    saved by Pillow at [`JPEG_QUALITY`][osrforge.providers.clef.JPEG_QUALITY].

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
        [`JPEG_THRESHOLD_BYTES`][osrforge.providers.clef.JPEG_THRESHOLD_BYTES]
        long, else the JPEG body and `"jpeg"`.
    """
    raise NotImplementedError("chunk: clef-provider")


def estimate_tokens(request: DecisionRequest) -> int:
    """Estimate a request's input tokens from above, for the reservation.

    The estimate is the image's tokens, `max(ceil(width / 32) * ceil(height /
    32), 64) + 4` (the service scales an image smaller than 256 by 256 up to
    it), plus `ceil(bytes / 2)` of the state, the question, and the options'
    compact JSON in UTF-8, plus 200 for the prompt the service wraps around
    them.

    Args:
        request: The request.

    Returns:
        The estimate.
    """
    raise NotImplementedError("chunk: clef-provider")


def estimate_cost(request: DecisionRequest) -> float:
    """Return a request's worst-case cost: its token estimate at list price, times 1.5.

    Args:
        request: The request.

    Returns:
        The estimate in dollars.
    """
    raise NotImplementedError("chunk: clef-provider")


def cost_usd(model: str, input_tokens: int) -> float:
    """Return what an answer cost at list price.

    Args:
        model: `clef` or `clef-flash`.
        input_tokens: The input tokens the usage reports.

    Returns:
        The cost in dollars.

    Raises:
        ProviderError: If the model has no price.
    """
    raise NotImplementedError("chunk: clef-provider")


class ClefProvider:
    """Clef on Workers AI, with the ledger and the retries the service needs.

    The constructor performs no I/O. Transport policy: a 413, which the
    service returns when it estimates a body past a token limit, is retried
    up to three more times with backoff, because the probe saw a 413 at a
    limit far below the model's pass on retry; 429, 5xx, and connection
    failures get bounded exponential backoff over at most five attempts;
    auth and other 4xx failures raise at once. An error message names the
    status, the body's size, and the service's message, and never the token.
    """

    def __init__(
        self,
        settings: ClefSettings,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], str] | None = None,
    ) -> None:
        """Bind to an account and a ledger.

        Args:
            settings: The settings.
            transport: The function that sends one POST, injectable for
                tests. When omitted, the adapter uses the standard library's
                HTTP client with a 120-second timeout.
            sleep: The backoff sleeper, injectable for tests.
            today: Gives the ledger entry's `date` as `YYYY-MM-DD`;
                injectable for tests.
        """
        self.settings = settings
        self._transport = transport
        self._sleep = sleep
        self._today = today

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Send one question and return its probabilities.

        The first call opens this provider's ledger entry `{"date", "what":
        settings.run_label}`, and every call reserves
        [`estimate_cost`][osrforge.providers.clef.estimate_cost] against it
        before sending and settles at
        [`cost_usd`][osrforge.providers.clef.cost_usd] of the usage after. A
        request that fails after it was sent is settled at its estimate. The
        POST goes to [`ENDPOINT`][osrforge.providers.clef.ENDPOINT] with an
        `Authorization: Bearer` header and a JSON content type, and the
        answer is read from the envelope's `result`: its `model`, the one
        answer's `probabilities`, and `usage.input_tokens`.

        Args:
            request: The question.

        Returns:
            The answer, with the format the image went as and the sha256 of
            the body sent.

        Raises:
            LedgerStopError: If the reservation would pass the stop.
            ProviderError: On transport, auth, or rate-limit exhaustion, a
                413 that stays after its retries, or an answer the adapter
                can't read.
        """
        raise NotImplementedError("chunk: clef-provider")
