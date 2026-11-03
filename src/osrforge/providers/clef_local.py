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

from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from osrforge.providers.clef import ClefProvider, Transport
from osrforge.providers.decisions import ClefModel, DecisionRequest, DecisionResponse, ImageFormat

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


class LocalClefSettings(BaseModel):
    """Where the local server is and what it calls each model.

    Examples:
        ```python
        from osrforge.providers.clef_local import LocalClefProvider, LocalClefSettings

        provider = LocalClefProvider(LocalClefSettings())
        ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = "http://localhost:11434"
    """The server's API root, with no trailing slash. The provider appends `/v1/systemone`."""

    models: Mapping[ClefModel, str] = Field(default_factory=lambda: {"clef-flash": "clef-flash"})
    """The server's name for each model a request may name. A request for a model not in this map is refused before
    anything is sent."""

    timeout_seconds: float = Field(default=300.0, gt=0)
    """How long one attempt may take. The first request after the server loads the model can take seconds."""

    @classmethod
    def from_env(cls) -> LocalClefSettings:
        """Build settings from `OSRFORGE_CLEF_LOCAL_URL` and `OSRFORGE_CLEF_LOCAL_MODEL`, each optional.

        Returns:
            The settings, with a trailing slash stripped from the URL. When `OSRFORGE_CLEF_LOCAL_MODEL` is set, it
            names the server's model for `clef-flash`.
        """
        raise NotImplementedError("chunk: clef-local")


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
    raise NotImplementedError("chunk: clef-local")


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
        raise NotImplementedError("chunk: clef-local")

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
        raise NotImplementedError("chunk: clef-local")


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
    raise NotImplementedError("chunk: clef-local")
