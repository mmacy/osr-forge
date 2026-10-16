"""The Clef adapter: Cloudflare's decision model on Workers AI, over its REST endpoint.

It fills the tracing stage's decision role
([`DecisionProvider`][osrforge.providers.decisions.DecisionProvider]). Each
request is one HTTP POST to the model-in-path endpoint with the System One
body Clef extends: one image, the state, and one choice question. The body is
[`encode_request`][osrforge.providers.decisions.encode_request]: the image
as PNG, or as JPEG when the PNG body is too large. The adapter reserves each
request's estimated cost on the Clef ledger before sending and settles it
from the usage the answer reports. No vendor SDK: the transport is a
function the constructor takes, so tests give it a fake. A recording of a
private corpus member's map stays in the owner's private corpus directory,
never in the repository.
"""

import hashlib
import http.client
import io
import json
import math
import os
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import cast

from PIL import Image
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from osrforge.errors import ClefAuthError, ProviderError
from osrforge.providers.decisions import DecisionRequest, DecisionResponse, ImageFormat, encode_request
from osrforge.providers.ledger import Ledger, Reservation

__all__ = [
    "ACCOUNT_ENV",
    "ENDPOINT",
    "LEDGER_ENV",
    "LEDGER_STOP_ENV",
    "PRICE_PER_TOKEN",
    "TOKEN_ENV",
    "ClefProvider",
    "ClefSettings",
    "Transport",
    "cost_usd",
    "estimate_cost",
    "estimate_tokens",
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

Transport = Callable[[str, bytes, Mapping[str, str]], tuple[int, bytes]]
"""Sends one POST, `(url, body, headers)`, and returns `(status, response body)`. May raise `OSError`."""

_ATTEMPTS = 5
"""The most attempts the provider makes at one request, whatever fails."""

_ATTEMPTS_AFTER_413 = 3
"""The most attempts that may follow a 413."""

_MAX_BACKOFF_SECONDS = 30.0

_RETRIED_STATUSES = frozenset({408, 409, 429})
"""The 4xx statuses the provider retries besides 413; it retries every 5xx too."""

_TIMEOUT_SECONDS = 120.0

_SERVICE_MESSAGE_CHARS = 500
"""The most characters of a response body an error message quotes when the body isn't the JSON envelope."""


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
        account_id = os.environ.get(ACCOUNT_ENV, "").strip()
        ledger = os.environ.get(LEDGER_ENV, "").strip()
        missing = [name for name, value in ((ACCOUNT_ENV, account_id), (LEDGER_ENV, ledger)) if not value]
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
        token = os.environ.get(TOKEN_ENV, "").strip() or wrangler_token()
        return cls(
            account_id=account_id,
            token=SecretStr(token),
            ledger_path=Path(ledger).expanduser(),
            ledger_stop_usd=stop,
        )


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
        ProviderError: If the file is missing or can't be read, isn't
            UTF-8, isn't valid TOML, or has no `oauth_token`. The message
            names the path, never any of the file's values.
    """
    if config_path is None:
        config_dir = "Library/Preferences/.wrangler/config" if sys.platform == "darwin" else ".config/.wrangler/config"
        config_path = Path.home() / config_dir / "default.toml"
    try:
        text = config_path.read_bytes()
    except OSError as error:
        raise ProviderError(
            f"can't read the wrangler login {config_path} ({error.strerror or type(error).__name__}); "
            f"run `wrangler login` or set {TOKEN_ENV}"
        ) from error
    try:
        config = tomllib.loads(text.decode("utf-8"))
    except UnicodeDecodeError, tomllib.TOMLDecodeError:
        # The parser's message can quote the file, so it stays out of this one.
        raise ProviderError(f"the wrangler login {config_path} isn't valid TOML") from None
    token = config.get("oauth_token")
    if not isinstance(token, str) or not token.strip():
        raise ProviderError(f"the wrangler login {config_path} has no oauth_token; run `wrangler login`")
    return token.strip()


def estimate_tokens(request: DecisionRequest) -> int:
    """Estimate a request's input tokens, with the probe's formula.

    The image's tokens are `ceil(width / 32) * ceil(height / 32)`, and when
    `width * height < 65536` they become `max(that, 64) * 2`, because the
    service scales a small image up and the probe allowed for the rounding;
    then 4 are added. To that the estimate adds `len(json.dumps(state)) / 2`
    and `len(json.dumps(questions)) / 1.5`, where `questions` is the body's
    questions mapping and `json.dumps` uses its default separators, and 200
    for the prompt the service wraps around them. The sum is rounded up.

    Args:
        request: The request.

    Returns:
        The estimate.
    """
    with Image.open(io.BytesIO(request.image)) as image:
        width, height = image.size
    image_tokens = math.ceil(width / 32) * math.ceil(height / 32)
    if width * height < 65536:
        image_tokens = max(image_tokens, 64) * 2
    image_tokens += 4
    questions = {request.tag: {"type": "choice", "instructions": request.question, "criteria": dict(request.options)}}
    return math.ceil(image_tokens + len(json.dumps(request.state)) / 2 + len(json.dumps(questions)) / 1.5 + 200)


def estimate_cost(request: DecisionRequest) -> float:
    """Return a request's worst-case cost: its token estimate at list price, times 1.5.

    Args:
        request: The request.

    Returns:
        The estimate in dollars.
    """
    return estimate_tokens(request) * _price(request.model) * 1.5


def _price(model: str) -> float:
    price = PRICE_PER_TOKEN.get(model)
    if price is None:
        raise ProviderError(
            f"no price for the model {model!r}, so the ledger can't charge it "
            f"(priced models: {', '.join(sorted(PRICE_PER_TOKEN))})"
        )
    return price


def cost_usd(model: str, input_tokens: int, output_tokens: int = 0) -> float:
    """Return what an answer cost at list price.

    Clef reports no output tokens today. Any it reports are charged at the
    input price, as the probe charged them.

    Args:
        model: `clef` or `clef-flash`.
        input_tokens: The input tokens the usage reports.
        output_tokens: The output tokens the usage reports.

    Returns:
        `(input_tokens + output_tokens)` at the model's price, in dollars.

    Raises:
        ProviderError: If the model has no price.
    """
    return (input_tokens + output_tokens) * _price(model)


class ClefProvider:
    """Clef on Workers AI, with the ledger and the retries the service needs.

    The constructor performs no I/O. Transport policy: at most five attempts
    per request in all, whatever mix of failures uses them. A 413, which the
    service returns when it estimates a body past a token limit, is retried
    with backoff, because the probe saw a 413 at a limit far below the
    model's pass on retry, but no more than three of the five attempts may
    follow a 413. 408, 409, 429, every 5xx, and connection failures get
    bounded exponential backoff within the same five attempts. A 401 raises
    [`ClefAuthError`][osrforge.errors.ClefAuthError], whose message tells
    the user to run `wrangler login`, and any other 4xx raises at once. The
    attempts share the request's one reservation, settled once at the end.
    An error message names the status, the body's size, and the service's
    message, and never the token.
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
        self._transport: Transport = transport if transport is not None else _urllib_transport
        self._sleep = sleep
        self._today = today if today is not None else _local_date
        self._ledger = Ledger(
            settings.ledger_path, cost_key="usd", stop_usd=settings.ledger_stop_usd, count_key="requests"
        )
        self._entry: int | None = None
        self._entry_lock = threading.Lock()

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Send one question and return its probabilities.

        The first call opens this provider's ledger entry `{"date", "what":
        settings.run_label}`, and every call reserves
        [`estimate_cost`][osrforge.providers.clef.estimate_cost] against it
        before sending and settles at
        [`cost_usd`][osrforge.providers.clef.cost_usd] of the usage's input
        and output tokens after. A request whose sending was attempted is
        settled at its estimate when no answer it can read arrives, also when no attempt
        reached the service, so the ledger errs toward overcharging. If a
        settle fails, the entry stays pending at the estimate. The POST goes
        to [`ENDPOINT`][osrforge.providers.clef.ENDPOINT] with an
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
            ClefAuthError: If the service answers 401.
            ProviderError: On transport or rate-limit exhaustion, a 413
                that stays after its retries, another 4xx, an answer the
                adapter can't read, or a settle of the ledger that fails
                after an answer.
        """
        body, image_format = encode_request(request)
        reservation = self._reserve(estimate_cost(request))
        try:
            answer = self._send(request, body)
            response, output_tokens = _response(request, answer, image_format, hashlib.sha256(body).hexdigest())
        except BaseException as error:
            # Sending was attempted, and no answer says what it cost, so the estimate stays on the ledger.
            try:
                self._ledger.settle(reservation, reservation.usd)
            except Exception as settle_error:
                error.add_note(
                    f"settling its ledger reservation failed too, so the entry stays pending: {settle_error}"
                )
            raise
        try:
            self._ledger.settle(reservation, cost_usd(request.model, response.input_tokens, output_tokens))
        except Exception as error:
            raise ProviderError(
                f"the {request.tag!r} decision was answered, but settling its ledger reservation failed, "
                f"so the entry stays pending at the estimate: {error}"
            ) from error
        return response

    def _reserve(self, usd: float) -> Reservation:
        """Reserve against this provider's ledger entry, opening the entry on the first request."""
        with self._entry_lock:
            if self._entry is None:
                reservation = self._ledger.open_reserved({"date": self._today(), "what": self.settings.run_label}, usd)
                self._entry = reservation.index
                return reservation
            entry = self._entry
        return self._ledger.reserve(entry, usd)

    def _send(self, request: DecisionRequest, body: bytes) -> bytes:
        """POST the body under the transport policy and return the 200 answer's bytes."""
        url = ENDPOINT.format(account_id=self.settings.account_id, model=request.model)
        headers = {
            "Authorization": f"Bearer {self.settings.token.get_secret_value()}",
            "Content-Type": "application/json",
        }
        after_413 = 0
        failure = ""
        last_error: OSError | None = None
        for attempt in range(_ATTEMPTS):
            if attempt > 0:
                self._sleep(min(2.0**attempt, _MAX_BACKOFF_SECONDS))
            try:
                status, answer = self._transport(url, body, headers)
            except OSError as error:
                last_error = error
                failure = f"the connection failed: {error}"
                continue
            last_error = None
            if status == 200:
                return answer
            failure = self._describe(request, status, body, answer)
            if status == 401:
                raise ClefAuthError(f"{failure}; the token was refused, so run `wrangler login` and try again")
            if status == 413:
                if after_413 == _ATTEMPTS_AFTER_413:
                    raise ProviderError(f"{failure}; it stayed after {_ATTEMPTS_AFTER_413} retries")
                after_413 += 1
                continue
            if status in _RETRIED_STATUSES or 500 <= status < 600:
                continue
            raise ProviderError(failure)
        raise ProviderError(
            self._redact(f"the {request.tag!r} decision failed after {_ATTEMPTS} attempts; the last: {failure}")
        ) from last_error

    def _describe(self, request: DecisionRequest, status: int, body: bytes, answer: bytes) -> str:
        return self._redact(
            f"the {request.tag!r} decision to {request.model} failed with HTTP {status} "
            f"for a body of {len(body)} bytes: {_service_message(answer)}"
        )

    def _redact(self, text: str) -> str:
        secret = self.settings.token.get_secret_value()
        return text.replace(secret, "[the token]") if secret else text


def _local_date() -> str:
    return date.today().isoformat()


def _urllib_transport(url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
    """Send one POST with the standard library's HTTP client, returning an error status as an answer."""
    sent = urllib.request.Request(url, data=body, headers={"User-Agent": "osr-forge", **headers}, method="POST")
    try:
        with urllib.request.urlopen(sent, timeout=_TIMEOUT_SECONDS) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()
    except http.client.HTTPException as error:
        # A connection dropped mid-answer surfaces as an HTTPException, which isn't an OSError.
        raise OSError(f"{type(error).__name__}: {error}") from error


def _service_message(answer: bytes) -> str:
    """Return the service's error messages from an envelope, or the start of the body when it isn't one."""
    try:
        envelope: object = json.loads(answer)
    except ValueError:
        envelope = None
    errors = _member(envelope, "errors")
    if isinstance(errors, list):
        messages = [str(message) for item in cast(list[object], errors) if (message := _member(item, "message"))]
        if messages:
            return " | ".join(messages)
    text = answer.decode("utf-8", errors="replace").strip()
    if not text:
        return "(an empty body)"
    return text[:_SERVICE_MESSAGE_CHARS] + ("..." if len(text) > _SERVICE_MESSAGE_CHARS else "")


def _object(value: object) -> dict[str, object] | None:
    """Return `value` as a JSON object, or `None` when it isn't one."""
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _member(value: object, key: str) -> object:
    """Return `value[key]` when `value` is a JSON object, else `None`."""
    found = _object(value)
    return None if found is None else found.get(key)


def _response(
    request: DecisionRequest, answer: bytes, image_format: ImageFormat, body_sha256: str
) -> tuple[DecisionResponse, int]:
    """Read the envelope's `result` into a response, and return it with the usage's output tokens."""
    where = f"the answer to the {request.tag!r} decision"
    try:
        envelope: object = json.loads(answer)
    except ValueError as error:
        raise ProviderError(f"{where} isn't JSON: {error}") from error
    result = _object(_member(envelope, "result"))
    if result is None:
        raise ProviderError(f"{where} has no result object")
    probabilities = _object(_member(_member(result.get("answers"), request.tag), "probabilities"))
    if probabilities is None:
        raise ProviderError(f"{where} has no probabilities for the question {request.tag!r}")
    given = sorted(probabilities)
    options = sorted(option_id for option_id, _ in request.options)
    if given != options:
        raise ProviderError(f"{where} gives probabilities for {given}, not for the options {options}")
    usage = _object(result.get("usage"))
    if usage is None:
        raise ProviderError(f"{where} has no usage")
    output_tokens = usage.get("output_tokens") or 0
    if not isinstance(output_tokens, int) or isinstance(output_tokens, bool) or output_tokens < 0:
        raise ProviderError(f"{where} reports output tokens that aren't a count: {output_tokens!r}")
    try:
        response = DecisionResponse.model_validate(
            {
                "probabilities": probabilities,
                "model_id": result.get("model"),
                "input_tokens": usage.get("input_tokens"),
                "format": image_format,
                "body_sha256": body_sha256,
            }
        )
    except ValidationError as error:
        raise ProviderError(f"{where} can't be read: {error}") from error
    return response, output_tokens
