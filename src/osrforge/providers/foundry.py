"""The Azure AI Foundry adapter — the only module allowed to import `openai`/`azure.identity`.

Minimal but real: the phase 0 capability spike drives it live and doubles as
its integration test; phase 1 hardens whatever the spike proves shaky. Two
choices here are provisional until the spike pins them in
`docs/foundry-capabilities.md`: the API surface (the `AzureOpenAI` client's
api-version dialect, versus the newer `/openai/v1` base-URL surface) and
whether the deployment honors native JSON-schema response format. The
validate-and-retry loop makes the adapter correct either way — the provider
owns schema enforcement, and the pipeline never sees invalid `data`.
"""

import base64
import json
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import openai
from pydantic import BaseModel, ConfigDict

from osrforge.contracts.run import TokenUsage
from osrforge.errors import ProviderError, SchemaValidationError
from osrforge.providers.base import ModelRequest, ModelResponse, TextPart, ensure_schema

__all__ = ["FoundryProvider", "FoundrySettings"]

_ENDPOINT_ENV = "OSRFORGE_FOUNDRY_ENDPOINT"
_DEPLOYMENT_ENV = "OSRFORGE_FOUNDRY_DEPLOYMENT"
_API_KEY_ENV = "OSRFORGE_FOUNDRY_API_KEY"

_ENTRA_SCOPE = "https://cognitiveservices.azure.com/.default"

_SCHEMA_ATTEMPTS = 3
"""Schema enforcement budget: at most two re-prompts, three attempts total."""

_TRANSPORT_ATTEMPTS = 5
"""Bounded backoff budget for 429/5xx/connection failures, per API call."""

_MAX_BACKOFF_SECONDS = 30.0


class FoundrySettings(BaseModel):
    """Connection settings for an Azure AI Foundry deployment.

    Auth mode is inferred: a key present means key auth; absent means Entra ID
    via `DefaultAzureCredential` (which needs the `osr-forge[entra]` extra).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str
    deployment: str
    api_key: str | None = None
    api_version: str = "2024-10-21"

    @classmethod
    def from_env(cls) -> FoundrySettings:
        """Build settings from the `OSRFORGE_FOUNDRY_*` environment variables.

        The `OSRFORGE_` prefix (not `AZURE_OPENAI_*`) avoids colliding with
        other tools' env conventions; the README documents the mapping.

        Returns:
            Settings from `OSRFORGE_FOUNDRY_ENDPOINT`,
            `OSRFORGE_FOUNDRY_DEPLOYMENT`, and (optionally)
            `OSRFORGE_FOUNDRY_API_KEY`.

        Raises:
            ProviderError: If a required variable is missing or empty.
        """
        endpoint = os.environ.get(_ENDPOINT_ENV)
        deployment = os.environ.get(_DEPLOYMENT_ENV)
        if not endpoint or not deployment:
            missing = [name for name, value in ((_ENDPOINT_ENV, endpoint), (_DEPLOYMENT_ENV, deployment)) if not value]
            raise ProviderError(f"missing required environment variable(s): {', '.join(missing)}")
        return cls(endpoint=endpoint, deployment=deployment, api_key=os.environ.get(_API_KEY_ENV) or None)


def _build_client(settings: FoundrySettings) -> openai.OpenAI:
    if settings.api_key is not None:
        return openai.AzureOpenAI(
            azure_endpoint=settings.endpoint,
            api_key=settings.api_key,
            api_version=settings.api_version,
            max_retries=0,
        )
    try:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError as error:
        raise ProviderError(
            "Entra ID auth needs the azure-identity package — install the osr-forge[entra] extra, "
            f"or set {_API_KEY_ENV} for key auth"
        ) from error
    return openai.AzureOpenAI(
        azure_endpoint=settings.endpoint,
        azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), _ENTRA_SCOPE),
        api_version=settings.api_version,
        max_retries=0,
    )


def _retry_after_seconds(error: openai.APIStatusError) -> float | None:
    value = error.response.headers.get("retry-after")
    if value is None:
        return None
    if re.fullmatch(r"\d+", value):
        return float(value)
    try:
        target = parsedate_to_datetime(value)
    except ValueError:
        return None
    return max(0.0, (target - datetime.now(UTC)).total_seconds())


def _content_parts(request: ModelRequest) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    for part in request.parts:
        if isinstance(part, TextPart):
            parts.append({"type": "text", "text": part.text})
        else:
            encoded = base64.b64encode(part.png).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
    return parts


def _response_format(request: ModelRequest) -> dict[str, object]:
    name = re.sub(r"[^A-Za-z0-9_-]", "_", request.tag)
    return {"type": "json_schema", "json_schema": {"name": name, "schema": request.schema}}


class FoundryProvider:
    """Azure AI Foundry, over the OpenAI-compatible chat surface.

    The constructor performs no I/O. Transport policy: bounded exponential
    backoff on 429/5xx and connection failures, honoring `Retry-After`; auth
    and other 4xx failures raise immediately. Images ship as base64 PNG data
    URLs.
    """

    def __init__(
        self,
        settings: FoundrySettings,
        client: openai.OpenAI | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Bind to a deployment.

        Args:
            settings: The connection settings.
            client: An injectable OpenAI-compatible client, for tests. When
                omitted, a real `AzureOpenAI` client is built from `settings`
                (which may raise [`ProviderError`][osrforge.errors.ProviderError]
                if Entra auth is inferred and `azure-identity` is missing).
            sleep: The backoff sleeper, injectable for tests.
        """
        self.settings = settings
        self._client = client if client is not None else _build_client(settings)
        self._sleep = sleep

    def _create_completion(self, messages: list[dict[str, object]], request: ModelRequest) -> Any:
        last_error: openai.APIError | None = None
        for attempt in range(_TRANSPORT_ATTEMPTS):
            try:
                return self._client.chat.completions.create(
                    model=self.settings.deployment,
                    messages=messages,  # pyright: ignore[reportArgumentType]
                    response_format=_response_format(request),  # pyright: ignore[reportArgumentType]
                )
            except openai.APIConnectionError as error:
                last_error = error
                delay = min(2.0**attempt, _MAX_BACKOFF_SECONDS)
            except openai.APIStatusError as error:
                if error.status_code != 429 and error.status_code < 500:
                    raise ProviderError(f"Foundry request {request.tag!r} failed: {error}") from error
                last_error = error
                retry_after = _retry_after_seconds(error)
                delay = retry_after if retry_after is not None else min(2.0**attempt, _MAX_BACKOFF_SECONDS)
            if attempt + 1 < _TRANSPORT_ATTEMPTS:
                self._sleep(delay)
        raise ProviderError(
            f"Foundry request {request.tag!r} failed after {_TRANSPORT_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Run one structured-output completion against the deployment.

        Native JSON-schema response format is requested, and the reply is
        validated against `request.schema` regardless; an invalid reply is
        re-prompted with the validation errors appended, at most twice.

        Args:
            request: The request.

        Returns:
            The parsed, schema-validated response.

        Raises:
            ProviderError: On transport, auth, or rate-limit exhaustion.
            SchemaValidationError: If no schema-valid reply was obtained within
                three attempts.
        """
        messages: list[dict[str, object]] = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": _content_parts(request)},
        ]
        usage = TokenUsage()
        model_id = ""
        failure = ""
        for _ in range(_SCHEMA_ATTEMPTS):
            completion = self._create_completion(messages, request)
            if completion.usage is not None:
                usage = usage + TokenUsage(
                    input_tokens=completion.usage.prompt_tokens,
                    output_tokens=completion.usage.completion_tokens,
                )
            model_id = completion.model
            content = completion.choices[0].message.content or ""
            try:
                data: object = json.loads(content)
            except json.JSONDecodeError as error:
                failure = f"reply was not valid JSON: {error}"
            else:
                try:
                    ensure_schema(data, request.schema, request.tag)
                except SchemaValidationError as error:
                    failure = str(error)
                else:
                    return ModelResponse(data=data, usage=usage, model_id=model_id)
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"That response was invalid: {failure}\n"
                    "Reply again with only a JSON document that satisfies the required schema.",
                },
            ]
        raise SchemaValidationError(
            f"no schema-valid response for {request.tag!r} after {_SCHEMA_ATTEMPTS} attempts; last failure: {failure}"
        )
