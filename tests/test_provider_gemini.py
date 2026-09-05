from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.core.models import Context, Request
from app.providers.base import ProviderRateLimitError
from app.providers.gemini import GeminiProvider


class FakeCompletions:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None) -> None:
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response=response, error=error)
        )


class FakeAsyncStream:
    def __init__(self, events) -> None:
        self._events = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration


def response(text: str = "Gemini response"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=[]),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(total_tokens=7),
    )


@pytest.mark.asyncio
async def test_gemini_provider_uses_gemini_identity_and_model() -> None:
    client = FakeClient(response())
    provider = GeminiProvider(
        Settings(gemini_model="gemini-test"), client=client
    )

    result = await provider.generate(Request("Hello"), Context())

    assert provider.name == "gemini"
    assert result.provider == "gemini"
    assert result.model == "gemini-test"
    assert result.text == "Gemini response"
    assert client.chat.completions.calls[0]["model"] == "gemini-test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "task_type", "expected"),
    [
        ("Merhaba", "simple", "minimal"),
        ("Bu mimariyi değerlendir", "complex", "medium"),
        ("Lütfen derin düşün", "simple", "high"),
    ],
)
async def test_gemini_auto_reasoning_follows_request_contract(
    text,
    task_type,
    expected,
) -> None:
    client = FakeClient(response())
    provider = GeminiProvider(
        Settings(
            gemini_model="gemini-3.5-flash-lite",
            gemini_reasoning_effort="auto",
        ),
        client=client,
    )
    request = Request(text, metadata={"task_type": task_type})

    result = await provider.generate(request, Context())

    assert client.chat.completions.calls[0]["reasoning_effort"] == expected
    assert request.metadata["_reasoning_level"] == expected
    assert result.metadata == {}


@pytest.mark.asyncio
async def test_gemini_prefers_semantic_complexity_over_agentic_route() -> None:
    client = FakeClient(response())
    provider = GeminiProvider(
        Settings(
            gemini_model="gemini-3.5-flash-lite",
            gemini_reasoning_effort="auto",
        ),
        client=client,
    )

    await provider.generate(
        Request(
            "Yalnızca hazırım yaz",
            metadata={
                "task_type": "agentic",
                "reasoning_task_type": "simple",
            },
        ),
        Context(),
    )

    assert client.chat.completions.calls[0]["reasoning_effort"] == "minimal"


@pytest.mark.asyncio
async def test_gemini_generate_and_stream_use_same_reasoning_level_and_hide_raw_thoughts(
) -> None:
    hidden = "private chain of thought"
    generate_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Görünür yanıt",
                    tool_calls=[],
                    thoughts=[hidden],
                    reasoning_content=hidden,
                ),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    stream_response = FakeAsyncStream(
        [
            SimpleNamespace(
                model="gemini-3.5-flash-lite",
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="Görünür yanıt",
                            tool_calls=[],
                            thoughts=[hidden],
                            reasoning_content=hidden,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )
        ]
    )
    settings = Settings(
        gemini_model="gemini-3.5-flash-lite",
        gemini_reasoning_effort="auto",
    )
    generate_client = FakeClient(generate_response)
    stream_client = FakeClient(stream_response)
    generate_provider = GeminiProvider(settings, client=generate_client)
    stream_provider = GeminiProvider(settings, client=stream_client)

    result = await generate_provider.generate(
        Request("Mimariyi değerlendir", metadata={"task_type": "complex"}),
        Context(),
    )
    chunks = [
        chunk
        async for chunk in stream_provider.stream(
            Request(
                "Mimariyi değerlendir",
                metadata={"task_type": "complex"},
            ),
            Context(),
        )
    ]

    assert generate_client.chat.completions.calls[0]["reasoning_effort"] == "medium"
    assert stream_client.chat.completions.calls[0]["reasoning_effort"] == "medium"
    assert result.text == "Görünür yanıt"
    assert result.metadata == {}
    assert not hasattr(result, "thoughts")
    assert not hasattr(result, "reasoning_content")
    assert [chunk.text for chunk in chunks] == ["Görünür yanıt"]
    assert all(chunk.metadata == {} for chunk in chunks)
    assert all(not hasattr(chunk, "thoughts") for chunk in chunks)
    assert all(not hasattr(chunk, "reasoning_content") for chunk in chunks)
    assert hidden not in repr(result)
    assert all(hidden not in repr(chunk) for chunk in chunks)


def test_gemini_provider_creates_compatible_client_from_settings() -> None:
    provider = GeminiProvider(
        Settings(
            gemini_api_key="gemini-secret",
            gemini_base_url="https://gemini.example/v1beta/openai/",
        )
    )

    assert provider._client is not None
    assert provider._client.api_key == "gemini-secret"
    assert str(provider._client.base_url).rstrip("/") == (
        "https://gemini.example/v1beta/openai"
    )


@pytest.mark.asyncio
async def test_gemini_errors_keep_provider_identity() -> None:
    error = RuntimeError("quota exhausted")
    error.status_code = 429
    provider = GeminiProvider(Settings(), client=FakeClient(error=error))

    with pytest.raises(ProviderRateLimitError) as captured:
        await provider.generate(Request("Hello"), Context())

    assert captured.value.provider == "gemini"
    assert "Gemini rate limit exceeded" in str(captured.value)


@pytest.mark.asyncio
async def test_gemini_replays_unsigned_tool_calls_with_the_skip_marker() -> None:
    client = FakeClient(response())
    provider = GeminiProvider(Settings(gemini_model="gemini-test"), client=client)
    context = Context()
    context.values["messages"] = [
        {"role": "user", "content": "sistem bilgisi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "deterministic-1",
                    "type": "function",
                    "function": {"name": "get_windows_system_info", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "other", "arguments": "{}"},
                    "extra_content": {"google": {"thought_signature": "real"}},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "deterministic-1", "content": "{}"},
    ]

    await provider.generate(Request("sistem bilgisi"), context)

    sent = client.chat.completions.calls[0]["messages"]
    assistant = next(m for m in sent if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": GeminiProvider.THOUGHT_SIGNATURE_SKIP}
    }
    assert assistant["tool_calls"][1]["extra_content"] == {
        "google": {"thought_signature": "real"}
    }
    # The stored conversation is not rewritten; only the wire copy is signed.
    assert "extra_content" not in context.values["messages"][1]["tool_calls"][0]


def test_gemini_signature_helper_keeps_existing_extra_content() -> None:
    signed = GeminiProvider._signed_tool_call(
        {"id": "x", "type": "function", "function": {"name": "f", "arguments": "{}"},
         "extra_content": {"vendor": 1, "google": {"other": True}}}
    )
    assert signed["extra_content"] == {
        "vendor": 1,
        "google": {"other": True, "thought_signature": GeminiProvider.THOUGHT_SIGNATURE_SKIP},
    }
    assert GeminiProvider._signed_tool_call("not a dict") == "not a dict"
