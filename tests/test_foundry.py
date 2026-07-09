import json
from types import SimpleNamespace
from typing import Any, cast

import httpx
import openai
import pytest
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from osrforge.contracts.run import TokenUsage
from osrforge.errors import ProviderError, SchemaValidationError
from osrforge.providers.base import ImagePart, ModelProvider, ModelRequest, TextPart
from osrforge.providers.foundry import FoundryProvider, FoundrySettings

SETTINGS = FoundrySettings(endpoint="https://example.openai.azure.com", deployment="gpt-5-4", api_key="key")

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}


def make_request() -> ModelRequest:
    return ModelRequest(
        tag="probe.trivial",
        system="Answer with the module title.",
        parts=(TextPart(text="THE EXAMPLE BARROW, page 1"), ImagePart(png=b"\x89PNG-fake")),
        schema=SCHEMA,
    )


def completion(content: str, prompt_tokens: int = 100, completion_tokens: int = 10) -> ChatCompletion:
    return ChatCompletion(
        id="chatcmpl-1",
        created=0,
        model="gpt-5.4",
        object="chat.completion",
        choices=[
            Choice(finish_reason="stop", index=0, message=ChatCompletionMessage(role="assistant", content=content))
        ],
        usage=CompletionUsage(
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def status_error(status: int, headers: dict[str, str] | None = None) -> openai.APIStatusError:
    response = httpx.Response(status, headers=headers or {}, request=httpx.Request("POST", "https://example"))
    return openai.APIStatusError(f"http {status}", response=response, body=None)


class StubClient:
    """Duck-types the client.chat.completions.create slice FoundryProvider uses."""

    def __init__(self, results: list[object]):
        self.calls: list[dict[str, Any]] = []

        def create(**kwargs: object) -> ChatCompletion:
            self.calls.append(kwargs)
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return cast(ChatCompletion, result)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def make_provider(results: list[object]) -> tuple[FoundryProvider, StubClient, list[float]]:
    client = StubClient(results)
    sleeps: list[float] = []
    provider = FoundryProvider(SETTINGS, client=cast(openai.OpenAI, client), sleep=sleeps.append)
    return provider, client, sleeps


def test_request_mapping():
    provider, client, _ = make_provider([completion('{"title": "The Example Barrow"}')])
    provider.generate(make_request())

    call = client.calls[0]
    assert call["model"] == "gpt-5-4"
    messages = call["messages"]
    assert messages[0] == {"role": "system", "content": "Answer with the module title."}
    user_parts = messages[1]["content"]
    assert messages[1]["role"] == "user"
    assert user_parts[0] == {"type": "text", "text": "THE EXAMPLE BARROW, page 1"}
    assert user_parts[1]["type"] == "image_url"
    assert user_parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    response_format = call["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == SCHEMA
    assert response_format["json_schema"]["name"] == "probe_trivial"


def test_schema_name_truncated_to_service_limit():
    # The service caps json_schema.name at 64 characters; a long content-batch
    # tag (observed on a 52-character dungeon slug) must not 400.
    provider, client, _ = make_provider([completion('{"title": "x"}')])
    long_tag = "content.cave-of-secrets-and-undertemple-of-shigazilnizthrub.1.b01"
    request = ModelRequest(tag=long_tag, system="s", parts=(TextPart(text="t"),), schema=SCHEMA)
    provider.generate(request)
    name = client.calls[0]["response_format"]["json_schema"]["name"]
    assert len(name) <= 64


def test_response_parsing_and_usage():
    provider, _, _ = make_provider([completion('{"title": "The Example Barrow"}')])
    response = provider.generate(make_request())
    assert response.data == {"title": "The Example Barrow"}
    assert response.usage == TokenUsage(input_tokens=100, output_tokens=10)
    assert response.model_id == "gpt-5.4"


def test_schema_retry_reprompts_with_failure_then_succeeds():
    provider, client, _ = make_provider([completion('{"wrong": true}'), completion('{"title": "The Example Barrow"}')])
    response = provider.generate(make_request())
    assert response.data == {"title": "The Example Barrow"}
    # Usage accumulates across attempts.
    assert response.usage == TokenUsage(input_tokens=200, output_tokens=20)
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[2]["role"] == "assistant"
    assert retry_messages[2]["content"] == '{"wrong": true}'
    assert retry_messages[3]["role"] == "user"
    assert "invalid" in retry_messages[3]["content"]


def test_schema_retry_budget_is_three_attempts():
    provider, client, _ = make_provider([completion("not json at all")] * 3)
    with pytest.raises(SchemaValidationError):
        provider.generate(make_request())
    assert len(client.calls) == 3


def test_rate_limit_backoff_honors_retry_after():
    provider, client, sleeps = make_provider([status_error(429, {"retry-after": "7"}), completion('{"title": "x"}')])
    provider.generate(make_request())
    assert sleeps == [7.0]
    assert len(client.calls) == 2


def test_server_error_retries_with_exponential_backoff():
    provider, client, sleeps = make_provider([status_error(500), status_error(503), completion('{"title": "x"}')])
    provider.generate(make_request())
    assert sleeps == [1.0, 2.0]
    assert len(client.calls) == 3


def test_connection_error_retries():
    provider, _, sleeps = make_provider(
        [openai.APIConnectionError(request=httpx.Request("POST", "https://example")), completion('{"title": "x"}')]
    )
    provider.generate(make_request())
    assert sleeps == [1.0]


def test_other_api_errors_raise_immediately():
    error = openai.APIResponseValidationError(
        response=httpx.Response(200, request=httpx.Request("POST", "https://example")), body=None
    )
    provider, client, sleeps = make_provider([error])
    with pytest.raises(ProviderError):
        provider.generate(make_request())
    assert sleeps == []
    assert len(client.calls) == 1


def test_empty_choices_raises_provider_error():
    empty = completion('{"title": "x"}').model_copy(update={"choices": []})
    provider, _, _ = make_provider([empty])
    with pytest.raises(ProviderError, match="no choices"):
        provider.generate(make_request())


def test_auth_failure_raises_immediately():
    provider, client, sleeps = make_provider([status_error(401)])
    with pytest.raises(ProviderError):
        provider.generate(make_request())
    assert sleeps == []
    assert len(client.calls) == 1


def test_transport_budget_exhaustion_raises_provider_error():
    provider, client, _ = make_provider([status_error(429)] * 5)
    with pytest.raises(ProviderError, match="5 attempts"):
        provider.generate(make_request())
    assert len(client.calls) == 5


def test_provider_satisfies_the_protocol():
    provider, _, _ = make_provider([])
    assert isinstance(provider, ModelProvider)


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OSRFORGE_FOUNDRY_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("OSRFORGE_FOUNDRY_DEPLOYMENT", "gpt-5-4")
    monkeypatch.setenv("OSRFORGE_FOUNDRY_API_KEY", "sekrit")
    settings = FoundrySettings.from_env()
    assert settings.endpoint == "https://example.openai.azure.com"
    assert settings.deployment == "gpt-5-4"
    assert settings.api_key == "sekrit"


def test_settings_from_env_without_key_infers_entra(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OSRFORGE_FOUNDRY_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("OSRFORGE_FOUNDRY_DEPLOYMENT", "gpt-5-4")
    monkeypatch.delenv("OSRFORGE_FOUNDRY_API_KEY", raising=False)
    assert FoundrySettings.from_env().api_key is None


def test_settings_from_env_missing_required_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OSRFORGE_FOUNDRY_ENDPOINT", raising=False)
    monkeypatch.setenv("OSRFORGE_FOUNDRY_DEPLOYMENT", "gpt-5-4")
    with pytest.raises(ProviderError, match="OSRFORGE_FOUNDRY_ENDPOINT"):
        FoundrySettings.from_env()


def test_missing_azure_identity_names_the_extra(monkeypatch: pytest.MonkeyPatch):
    import sys

    monkeypatch.setitem(sys.modules, "azure.identity", None)
    entra_settings = FoundrySettings(endpoint="https://example.openai.azure.com", deployment="gpt-5-4")
    with pytest.raises(ProviderError, match=r"osr-forge\[entra\]"):
        FoundryProvider(entra_settings)


def test_entra_client_builds_without_io():
    entra_settings = FoundrySettings(endpoint="https://example.openai.azure.com", deployment="gpt-5-4")
    provider = FoundryProvider(entra_settings)
    assert provider.settings.api_key is None


def test_constructor_does_no_io_with_key_auth():
    provider = FoundryProvider(SETTINGS)
    assert provider.settings is SETTINGS


def test_reply_json_payload_matches_request(monkeypatch: pytest.MonkeyPatch):
    # The image data URL round-trips: decoding it yields the original PNG bytes.
    import base64

    provider, client, _ = make_provider([completion('{"title": "x"}')])
    provider.generate(make_request())
    url = client.calls[0]["messages"][1]["content"][1]["image_url"]["url"]
    encoded = url.removeprefix("data:image/png;base64,")
    assert base64.b64decode(encoded) == b"\x89PNG-fake"
    assert json.loads('{"title": "x"}')  # sanity: the stub reply was valid JSON
