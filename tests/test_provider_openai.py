from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.core.models import Context, Request
from app.providers.base import (
    ModelStreamChunk,
    ProviderAuthenticationError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from app.providers.openai import OpenAIProvider


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(
                response=response,
                error=error,
            )
        )


def make_response():
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Merhaba Ali",
                    tool_calls=[],
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    )


def make_settings():
    return Settings(
        default_model="test-model",
        provider_timeout_seconds=5.0,
        provider_max_retries=2,
    )


@pytest.mark.asyncio
async def test_openai_provider_identity():
    provider = OpenAIProvider(
        make_settings(),
        client=FakeClient(make_response()),
    )

    assert provider.name == "openai"
    assert provider.capabilities.text is True
    assert provider.capabilities.streaming is True
    assert provider.capabilities.tool_calling is True
    assert provider.capabilities.vision is True


@pytest.mark.asyncio
async def test_openai_provider_generates_response():
    client = FakeClient(make_response())

    provider = OpenAIProvider(
        make_settings(),
        client=client,
    )

    response = await provider.generate(
        Request("Merhaba JARVIS"),
        Context(),
    )

    assert response.text == "Merhaba Ali"
    assert response.provider == "openai"
    assert response.model == "test-model"
    assert response.finish_reason == "stop"
    assert response.usage["total_tokens"] == 15
    assert len(client.chat.completions.calls) == 1


@pytest.mark.asyncio
async def test_openai_provider_passes_system_prompt_and_memories():
    client = FakeClient(make_response())

    provider = OpenAIProvider(
        make_settings(),
        client=client,
    )

    context = Context(
        memories=["Ali JARVIS'i gelistiriyor."]
    )

    await provider.generate(
        Request("Nerede kald?k?"),
        context,
        system_prompt="Sen JARVIS'sin.",
    )

    call = client.chat.completions.calls[0]

    assert call["model"] == "test-model"
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "Sen JARVIS'sin."
    assert "Ali JARVIS'i gelistiriyor." in (
        call["messages"][1]["content"]
    )
    assert call["messages"][-1]["content"] == "Nerede kald?k?"


@pytest.mark.asyncio
async def test_openai_provider_authentication_error():
    error = RuntimeError("unauthorized")
    error.status_code = 401

    provider = OpenAIProvider(
        make_settings(),
        client=FakeClient(error=error),
    )

    with pytest.raises(ProviderAuthenticationError):
        await provider.generate(
            Request("test"),
            Context(),
        )


@pytest.mark.asyncio
async def test_openai_provider_rate_limit_error():
    error = RuntimeError("rate limited")
    error.status_code = 429

    provider = OpenAIProvider(
        Settings(
            default_model="test-model",
            provider_timeout_seconds=5.0,
            provider_max_retries=0,
        ),
        client=FakeClient(error=error),
    )

    with pytest.raises(ProviderRateLimitError):
        await provider.generate(
            Request("test"),
            Context(),
        )


@pytest.mark.asyncio
async def test_openai_provider_requires_client():
    provider = OpenAIProvider(make_settings())

    with pytest.raises(ProviderUnavailableError):
        await provider.generate(
            Request("test"),
            Context(),
        )

def test_openai_provider_creates_client_from_settings(monkeypatch) -> None:
    from app.config.settings import Settings
    from app.providers.openai import OpenAIProvider

    monkeypatch.setenv("JARVIS_API_KEY", "test-secret")
    monkeypatch.setenv("JARVIS_API_BASE_URL", "https://example.test/v1")

    provider = OpenAIProvider(Settings.from_environment())

    assert provider._client is not None
    assert provider._client.api_key == "test-secret"
    assert str(provider._client.base_url).rstrip("/") == "https://example.test/v1"


@pytest.mark.asyncio
async def test_openai_provider_generates_model_response(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.config.settings import Settings
    from app.providers.openai import OpenAIProvider

    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["model"] == "test-model"
            assert kwargs["messages"][-1]["role"] == "user"
            assert kwargs["messages"][-1]["content"] == "Merhaba JARVIS"

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Merhaba Ali.",
                            tool_calls=None,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=FakeCompletions()
            )

    monkeypatch.setenv("JARVIS_API_KEY", "test-secret")
    monkeypatch.setenv("JARVIS_DEFAULT_MODEL", "test-model")

    provider = OpenAIProvider(
        Settings.from_environment(),
        client=FakeClient(),
    )

    response = await provider.generate(
        Request("Merhaba JARVIS"),
        Context(),
    )

    assert response.text == "Merhaba Ali."
    assert response.provider == "openai"
    assert response.model == "test-model"
    assert response.finish_reason == "stop"
    assert response.usage["total_tokens"] == 15

@pytest.mark.asyncio
async def test_openai_provider_parses_tool_calls(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.config.settings import Settings
    from app.core.models import Context, Request
    from app.providers.openai import OpenAIProvider

    tool_call = SimpleNamespace(
        id="call_123",
        type="function",
        function=SimpleNamespace(
            name="get_weather",
            arguments='{"city":"Baku"}',
        ),
    )

    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["tools"] == [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather information.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string"},
                            },
                            "required": ["city"],
                        },
                    },
                }
            ]

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[tool_call],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage=None,
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=FakeCompletions()
            )

    monkeypatch.setenv("JARVIS_API_KEY", "test-secret")

    provider = OpenAIProvider(
        Settings.from_environment(),
        client=FakeClient(),
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                    },
                    "required": ["city"],
                },
            },
        }
    ]

    response = await provider.generate(
        Request("Baku'de hava nas?l?"),
        Context(),
        tools=tools,
    )

    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0]["id"] == "call_123"
    assert response.tool_calls[0]["type"] == "function"
    assert response.tool_calls[0]["function"]["name"] == "get_weather"
    assert response.tool_calls[0]["function"]["arguments"] == '{"city":"Baku"}'

@pytest.mark.asyncio
async def test_openai_provider_includes_context_messages() -> None:
    client = FakeClient(make_response())

    provider = OpenAIProvider(
        make_settings(),
        client=client,
    )

    context = Context(
        values={
            "messages": [
                {
                    "role": "tool",
                    "tool_call_id": "call_123",
                    "content": "Baku: sunny",
                }
            ]
        }
    )

    await provider.generate(
        Request("Baku'de hava nas?l?"),
        context,
    )

    messages = client.chat.completions.calls[0]["messages"]

    assert any(
        message["role"] == "tool"
        and message["tool_call_id"] == "call_123"
        and message["content"] == "Baku: sunny"
        for message in messages
    )

@pytest.mark.asyncio
async def test_openai_provider_preserves_assistant_tool_calls_message() -> None:
    client = FakeClient(make_response())

    provider = OpenAIProvider(
        make_settings(),
        client=client,
    )

    context = Context(
        values={
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":"Baku"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_123",
                    "content": "Baku: sunny",
                },
            ]
        }
    )

    await provider.generate(
        Request("Baku'de hava nas?l?"),
        context,
    )

    messages = client.chat.completions.calls[0]["messages"]

    assert any(
        message["role"] == "assistant"
        and message["tool_calls"][0]["id"] == "call_123"
        for message in messages
    )

    assert any(
        message["role"] == "tool"
        and message["tool_call_id"] == "call_123"
        and message["content"] == "Baku: sunny"
        for message in messages
    )

@pytest.mark.asyncio
async def test_openai_provider_preserves_multiple_tool_calls_in_message() -> None:
    client = FakeClient(make_response())

    provider = OpenAIProvider(
        make_settings(),
        client=client,
    )

    context = Context(
        values={
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_weather",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":"Baku"}',
                            },
                        },
                        {
                            "id": "call_time",
                            "type": "function",
                            "function": {
                                "name": "get_time",
                                "arguments": '{"city":"Baku"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_weather",
                    "content": "Baku: sunny",
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_time",
                    "content": "Baku: 12:00",
                },
            ],
        },
    )

    await provider.generate(
        Request("Baku hava ve saat bilgisi"),
        context,
    )

    messages = client.chat.completions.calls[0]["messages"]

    assistant_messages = [
        message
        for message in messages
        if message.get("role") == "assistant"
    ]

    assert len(assistant_messages) == 1

    assistant_message = assistant_messages[0]

    assert len(
        assistant_message["tool_calls"]
    ) == 2

    assert (
        assistant_message["tool_calls"][0]["id"]
        == "call_weather"
    )

    assert (
        assistant_message["tool_calls"][0]["function"]["name"]
        == "get_weather"
    )

    assert (
        assistant_message["tool_calls"][1]["id"]
        == "call_time"
    )

    assert (
        assistant_message["tool_calls"][1]["function"]["name"]
        == "get_time"
    )

    assert any(
        message.get("role") == "tool"
        and message.get("tool_call_id")
        == "call_weather"
        and message.get("content")
        == "Baku: sunny"
        for message in messages
    )

    assert any(
        message.get("role") == "tool"
        and message.get("tool_call_id")
        == "call_time"
        and message.get("content")
        == "Baku: 12:00"
        for message in messages
    )


@pytest.mark.asyncio
async def test_openai_provider_streams_normalized_chunks() -> None:
    chunks = [
        SimpleNamespace(
            model="stream-model",
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="Mer", tool_calls=[]),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            model="stream-model",
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="haba", tool_calls=[]),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=2,
                completion_tokens=1,
                total_tokens=3,
            ),
        ),
    ]

    class FakeStream:
        def __aiter__(self):
            self._iterator = iter(chunks)
            return self

        async def __anext__(self):
            try:
                return next(self._iterator)
            except StopIteration:
                raise StopAsyncIteration

    class StreamingCompletions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return FakeStream()

    completions = StreamingCompletions()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    provider = OpenAIProvider(make_settings(), client=client)

    received = [
        chunk
        async for chunk in provider.stream(Request("hello"), Context())
    ]

    assert all(isinstance(chunk, ModelStreamChunk) for chunk in received)
    assert "".join(chunk.text for chunk in received) == "Merhaba"
    assert received[-1].usage["total_tokens"] == 3
    assert completions.calls[0]["stream"] is True
    assert completions.calls[0]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_openai_provider_rejects_response_without_choices() -> None:
    client = FakeClient(SimpleNamespace(choices=[], usage=None))
    provider = OpenAIProvider(make_settings(), client=client)

    with pytest.raises(ProviderInvalidResponseError):
        await provider.generate(Request("hello"), Context())


@pytest.mark.asyncio
async def test_openai_provider_passes_response_format() -> None:
    client = FakeClient(make_response())
    provider = OpenAIProvider(make_settings(), client=client)
    response_format = {"type": "json_object"}

    await provider.generate(
        Request("json"),
        Context(),
        response_format=response_format,
    )

    assert client.chat.completions.calls[0]["response_format"] == response_format


@pytest.mark.asyncio
async def test_openai_provider_appends_current_user_to_plain_history() -> None:
    client = FakeClient(make_response())
    provider = OpenAIProvider(make_settings(), client=client)
    context = Context(
        values={
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "answer"},
            ]
        }
    )

    await provider.generate(Request("second"), context)

    assert client.chat.completions.calls[0]["messages"][-1] == {
        "role": "user",
        "content": "second",
    }
