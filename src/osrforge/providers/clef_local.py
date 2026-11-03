"""The local Clef backend: a decision model on a System One server on this machine, such as Ollama.

It fills the tracing stage's decision role
([`DecisionProvider`][osrforge.providers.decisions.DecisionProvider]), as the
hosted adapter in `osrforge.providers.clef` does, and costs nothing, so it
keeps no ledger. A setting chooses between the two backends
([`clef_from_env`][osrforge.providers.clef_local.clef_from_env]).

Each request is one HTTP POST to `<base_url>/v1/systemone` with a JSON body.
The body is the hosted body of
[`request_body`][osrforge.providers.decisions.request_body] with two
differences that Ollama's System One endpoint needs: `model` is the server's
name for the model, and `images` is a list of plain base64 strings rather
than `{content_type, base64}` objects. The image goes as PNG, or as JPEG
when the hosted body would be too large, by the same rule as
[`encode_request`][osrforge.providers.decisions.encode_request], so both
backends see the same pixels. The server answers with
`{"model", "answers": {<tag>: {"probabilities": {...}, ...}}, "usage":
{"input_tokens", ...}}` at the top level, with no envelope.

A response's `model_id` is `local:` followed by the model the server says
answered, so a recording shows which backend made it. Recordings from the
two backends belong in different directories, because the recording key
names the model and not the backend.

No vendor SDK: the transport is a function the constructor takes, so tests
give it a fake.
"""

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from osrforge.errors import ProviderError
from osrforge.providers.clef import ClefProvider, ClefSettings, Transport
from osrforge.providers.decisions import (
    ClefModel,
    DecisionRequest,
    DecisionResponse,
    ImageFormat,
    encode_request,
    request_body,
)

__all__ = [
    "BACKEND_ENV",
    "LOCAL_MODEL_ENV",
    "LOCAL_URL_ENV",
    "Backend",
    "LocalClefProvider",
    "LocalClefSettings",
    "clef_from_env",
    "local_request_body",
]

BACKEND_ENV = "OSRFORGE_CLEF_BACKEND"
"""`hosted` (the default when unset or empty) or `local`."""

LOCAL_URL_ENV = "OSRFORGE_CLEF_LOCAL_URL"
"""The local server's API root. The default is `http://localhost:11434`, Ollama's."""

LOCAL_MODEL_ENV = "OSRFORGE_CLEF_LOCAL_MODEL"
"""The server's name for Clef-flash. The default is `clef-flash`, Ollama's."""

Backend = Literal["hosted", "local"]

_DEFAULT_URL = "http://localhost:11434"

_ATTEMPTS = 3
"""The most attempts the provider makes at one request."""

_BACKOFF_SECONDS = 0.5
"""The wait before the second attempt. Each later wait doubles it."""

_SERVICE_MESSAGE_CHARS = 500
"""The most characters of a response body an error message quotes."""


class LocalClefSettings(BaseModel):
    """Where the local server is and what it calls each model.

    Examples:
        ```python
        from osrforge.providers.clef_local import LocalClefProvider, LocalClefSettings

        provider = LocalClefProvider(LocalClefSettings())
        ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = _DEFAULT_URL
    """The server's API root, with no trailing slash. The provider appends `/v1/systemone`."""

    models: Mapping[ClefModel, str] = Field(default_factory=lambda: {"clef-flash": "clef-flash"})
    """The server's name for each model a request may name. A request for a model not in this map is refused before
    anything is sent."""

    timeout_seconds: float = Field(default=300.0, gt=0)
    """How long one attempt may take. The first request after the server loads the model can take seconds."""

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @classmethod
    def from_env(cls) -> LocalClefSettings:
        """Build settings from `OSRFORGE_CLEF_LOCAL_URL` and `OSRFORGE_CLEF_LOCAL_MODEL`, each optional.

        Returns:
            The settings, with a trailing slash stripped from the URL. When `OSRFORGE_CLEF_LOCAL_MODEL` is set, it
            names the server's model for `clef-flash`.
        """
        base_url = os.environ.get(LOCAL_URL_ENV, "").strip() or _DEFAULT_URL
        model = os.environ.get(LOCAL_MODEL_ENV, "").strip() or "clef-flash"
        return cls(base_url=base_url, models={"clef-flash": model})


def local_request_body(request: DecisionRequest, image_format: ImageFormat, server_model: str) -> bytes:
    """Build the exact body for the local server.

    The body is the canonical JSON (sorted keys, compact separators, UTF-8)
    of `{"model": server_model, "images": [<base64 of the image>], "state":
    request.state, "questions": {request.tag: {"type": "choice",
    "instructions": request.question, "criteria": {option id:
    description}}}}`. The image is the request's PNG, or for `"jpeg"` the
    PNG's pixels saved as the hosted body saves them.

    Args:
        request: The request.
        image_format: How to send the image.
        server_model: The server's name for the request's model.

    Returns:
        The body bytes.
    """
    hosted = cast(dict[str, object], json.loads(request_body(request, image_format)))
    images = cast(list[dict[str, str]], hosted["images"])
    body = {**hosted, "model": server_model, "images": [image["base64"] for image in images]}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class LocalClefProvider:
    """Clef on a local System One server.

    The constructor performs no I/O. Transport policy: at most three attempts
    per request. A connection failure and a 5xx are retried with a short
    backoff, and any other status that isn't 200 raises at once. An error
    names the server's URL, the status or the connection failure, and the
    server's message, so a server that isn't running says so plainly.
    """

    def __init__(
        self,
        settings: LocalClefSettings,
        transport: Transport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """Bind to a server.

        Args:
            settings: The settings.
            transport: The function that sends one POST, injectable for
                tests. When omitted, the provider uses the standard library's
                HTTP client with the settings' timeout.
            sleep: The backoff sleeper, injectable for tests. When omitted,
                `time.sleep`.
        """
        self.settings = settings
        self._transport: Transport = transport if transport is not None else _urllib_transport(settings.timeout_seconds)
        self._sleep = sleep if sleep is not None else time.sleep

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Send one question and return its probabilities.

        The image goes as PNG unless
        [`encode_request`][osrforge.providers.decisions.encode_request] would
        send the hosted body as JPEG. The POST goes to `<base_url>/v1/systemone`
        with a JSON content type and no authorization. The answer's
        probabilities must cover exactly the request's options. The response's
        `model_id` is `local:` and the model the server names, `input_tokens`
        is the usage's, `format` is how the image went, and `body_sha256` is
        the sha256 of the body sent.

        Args:
            request: The question.

        Returns:
            The answer.

        Raises:
            ProviderError: If the request names a model the settings don't
                map, the server can't be reached or keeps failing, it answers
                with another status, or its answer can't be read.
        """
        server_model = self.settings.models.get(request.model)
        if server_model is None:
            raise ProviderError(
                f"the settings map no server model for {request.model!r}, so the {request.tag!r} decision wasn't sent "
                f"(mapped models: {', '.join(sorted(self.settings.models))})"
            )
        _, image_format = encode_request(request)
        body = local_request_body(request, image_format, server_model)
        answer = self._send(request, body)
        return _response(request, answer, image_format, hashlib.sha256(body).hexdigest())

    def _send(self, request: DecisionRequest, body: bytes) -> bytes:
        """POST the body under the transport policy and return the 200 answer's bytes."""
        url = f"{self.settings.base_url}/v1/systemone"
        headers = {"Content-Type": "application/json"}
        failure = ""
        last_error: OSError | None = None
        for attempt in range(_ATTEMPTS):
            if attempt > 0:
                self._sleep(_BACKOFF_SECONDS * 2.0 ** (attempt - 1))
            try:
                status, answer = self._transport(url, body, headers)
            except TimeoutError as error:
                raise ProviderError(
                    f"the {request.tag!r} decision to {url} timed out after {self.settings.timeout_seconds:g} seconds "
                    f"({error}); the server may be busy or hung"
                ) from error
            except OSError as error:
                last_error = error
                failure = f"the connection failed: {error}"
                continue
            last_error = None
            if status == 200:
                return answer
            failure = f"HTTP {status}: {_service_message(answer)}"
            if 500 <= status < 600:
                continue
            raise ProviderError(f"the {request.tag!r} decision to {url} failed with {failure}")
        raise ProviderError(
            f"the {request.tag!r} decision to {url} failed after {_ATTEMPTS} attempts; the last: {failure}. "
            "Is the server running?"
        ) from last_error


def clef_from_env() -> ClefProvider | LocalClefProvider:
    """Build the decision provider the environment chooses.

    `OSRFORGE_CLEF_BACKEND` chooses the backend: `hosted`, the default when
    it's unset or empty, gives a [`ClefProvider`][osrforge.providers.clef.ClefProvider]
    from [`ClefSettings.from_env`][osrforge.providers.clef.ClefSettings.from_env],
    and `local` gives a
    [`LocalClefProvider`][osrforge.providers.clef_local.LocalClefProvider]
    from [`LocalClefSettings.from_env`][osrforge.providers.clef_local.LocalClefSettings.from_env],
    which needs no account, token, or ledger.

    Returns:
        The provider.

    Raises:
        ProviderError: If the backend is another word, or the hosted
            backend's settings are missing.
    """
    backend = os.environ.get(BACKEND_ENV, "").strip() or "hosted"
    if backend == "hosted":
        return ClefProvider(ClefSettings.from_env())
    if backend == "local":
        return LocalClefProvider(LocalClefSettings.from_env())
    raise ProviderError(f"{BACKEND_ENV} must be 'hosted' or 'local', not {backend!r}")


def _urllib_transport(timeout_seconds: float) -> Transport:
    """Return a transport that sends one POST with the standard library's HTTP client."""

    def send(url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        sent = urllib.request.Request(url, data=body, headers={"User-Agent": "osr-forge", **headers}, method="POST")
        try:
            with urllib.request.urlopen(sent, timeout=timeout_seconds) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()
        except urllib.error.URLError as error:
            # A timeout while connecting arrives wrapped, and the provider treats it as the timeout it is.
            if isinstance(error.reason, TimeoutError):
                raise error.reason from error
            raise
        except http.client.HTTPException as error:
            # A connection dropped mid-answer surfaces as an HTTPException, which isn't an OSError.
            raise OSError(f"{type(error).__name__}: {error}") from error

    return send


def _service_message(answer: bytes) -> str:
    """Return the server's `error` text, or the start of the body when it isn't a JSON error."""
    try:
        parsed: object = json.loads(answer)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        message = cast(dict[str, object], parsed).get("error")
        if isinstance(message, str) and message:
            return message
    text = answer.decode("utf-8", errors="replace").strip()
    if not text:
        return "(an empty body)"
    return text[:_SERVICE_MESSAGE_CHARS] + ("..." if len(text) > _SERVICE_MESSAGE_CHARS else "")


def _response(request: DecisionRequest, answer: bytes, image_format: ImageFormat, body_sha256: str) -> DecisionResponse:
    """Read the server's top-level answer into a response."""
    where = f"the answer to the {request.tag!r} decision"
    try:
        parsed: object = json.loads(answer)
    except ValueError as error:
        raise ProviderError(f"{where} isn't JSON: {error}") from error
    result = _object(parsed)
    if result is None:
        raise ProviderError(f"{where} isn't a JSON object")
    answers = _object(result.get("answers"))
    question = None if answers is None else _object(answers.get(request.tag))
    probabilities = None if question is None else _object(question.get("probabilities"))
    if probabilities is None:
        raise ProviderError(f"{where} has no probabilities for the question {request.tag!r}")
    given = sorted(probabilities)
    options = sorted(option_id for option_id, _ in request.options)
    if given != options:
        raise ProviderError(f"{where} gives probabilities for {given}, not for the options {options}")
    usage = _object(result.get("usage"))
    if usage is None:
        raise ProviderError(f"{where} has no usage")
    model = result.get("model")
    if not isinstance(model, str) or not model:
        raise ProviderError(f"{where} doesn't name the model that answered")
    input_tokens = usage.get("input_tokens")
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool) or input_tokens < 0:
        raise ProviderError(f"{where} reports input tokens that aren't a count: {input_tokens!r}")
    try:
        return DecisionResponse.model_validate(
            {
                "probabilities": probabilities,
                "model_id": f"local:{model}",
                "input_tokens": input_tokens,
                "format": image_format,
                "body_sha256": body_sha256,
            }
        )
    except ValidationError as error:
        problems = "; ".join(str(problem["msg"]) for problem in error.errors())
        raise ProviderError(f"{where} can't be read: {problems}") from error


def _object(value: object) -> dict[str, object] | None:
    """Return `value` as a JSON object, or `None` when it isn't one."""
    return cast(dict[str, object], value) if isinstance(value, dict) else None
