from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bootstrap import (
    create_application,
)
from app.config.provider_preferences import (
    DEFAULT_OLLAMA_MODEL,
    ProviderPreferences,
    ProviderPreferencesStore,
    validate_provider,
)
from app.config.settings import Settings
from app.core.models import (
    Context,
    Request,
)
from app.providers.ollama import (
    OllamaProvider,
)


class RecordingCompletions:
    def __init__(self) -> None:
        self.calls = []

    async def create(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        message = SimpleNamespace(
            content="local response",
            tool_calls=[],
        )

        choice = SimpleNamespace(
            message=message,
            finish_reason="stop",
        )

        return SimpleNamespace(
            choices=[choice],
            usage=None,
        )


class FakeClient:
    def __init__(self) -> None:
        self.completions = (
            RecordingCompletions()
        )

        self.chat = SimpleNamespace(
            completions=self.completions
        )


def test_ollama_provider_identity_and_capabilities(
) -> None:
    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=FakeClient(),
    )

    assert provider.name == "ollama"
    assert provider.provider_label == "Ollama"
    assert provider.is_configured is True

    capabilities = provider.capabilities

    assert capabilities.text is True
    assert capabilities.streaming is True
    assert capabilities.structured_output is True
    assert capabilities.tool_calling is True
    assert capabilities.vision is False


def test_ollama_is_not_automatic_fallback_unless_enabled(
) -> None:
    disabled = OllamaProvider(
        Settings(),
        client=FakeClient(),
    )

    enabled = OllamaProvider(
        Settings(
            ollama_enabled=True,
        ),
        client=FakeClient(),
    )

    assert disabled.is_configured is False
    assert enabled.is_configured is True


@pytest.mark.asyncio
async def test_ollama_generate_uses_provider_neutral_contract(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "status",
                "description": "status",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        }
    ]

    result = await provider.generate(
        Request("hello"),
        Context(),
        tools=tools,
    )

    assert result.provider == "ollama"
    assert result.model == DEFAULT_OLLAMA_MODEL
    assert result.text == "local response"

    call = client.completions.calls[0]

    assert call["model"] == DEFAULT_OLLAMA_MODEL
    assert call["tools"] == tools


@pytest.mark.asyncio
async def test_ollama_does_not_add_finalization_prompt_before_tool_result(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    await provider.generate(
        Request("hello"),
        Context(),
        system_prompt="Original system prompt.",
    )

    call = client.completions.calls[0]

    assert call["messages"][0] == {
        "role": "system",
        "content": "Original system prompt.",
    }

    assert (
        OllamaProvider._TOOL_FINALIZATION_PROMPT
        not in call["messages"][0]["content"]
    )

    assert "max_tokens" not in call


@pytest.mark.asyncio
async def test_ollama_adds_concise_prompt_after_tool_result_without_token_cap(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    context = Context()

    context.values["messages"] = [
        {
            "role": "user",
            "content": "Inspect the system.",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "status",
                        "arguments": "{}",
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "{'status': 'ok'}",
        },
    ]

    await provider.generate(
        Request("Inspect the system."),
        context,
        system_prompt="Original system prompt.",
    )

    call = client.completions.calls[0]

    system_messages = [
        message["content"]
        for message in call["messages"]
        if message.get("role") == "system"
    ]

    assert len(system_messages) == 1
    assert system_messages[0].startswith(
        "Original system prompt."
    )

    assert (
        OllamaProvider._TOOL_FINALIZATION_PROMPT
        in system_messages[0]
    )

    assert (
        "If another tool is required"
        in system_messages[0]
    )

    assert "max_tokens" not in call


@pytest.mark.asyncio
async def test_ollama_compacts_windows_system_info_for_model_only(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    raw_data = {
        "system": "Windows",
        "release": "11",
        "version": "10.0.26200",
        "machine": "AMD64",
        "processor": "Intel64 Family 6",
        "hostname": "test-host",
        "logical_cpu_count": 8,
        "memory_total_bytes": 8415662080,
        "memory_available_bytes": 1932735283,
        "memory_total_gib": 7.84,
        "memory_available_gib": 1.8,
        "system_drive": "C:\\",
        "disk_total_bytes": 126709653504,
        "disk_free_bytes": 11338713600,
        "disk_total_gib": 118.01,
        "disk_free_gib": 10.56,
    }

    original_content = str(raw_data)

    context = Context()

    context.values["messages"] = [
        {
            "role": "user",
            "content": "Inspect the system.",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "system-call",
                    "type": "function",
                    "function": {
                        "name": "get_windows_system_info",
                        "arguments": "{}",
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "system-call",
            "content": original_content,
        },
    ]

    await provider.generate(
        Request("Inspect the system."),
        context,
    )

    call = client.completions.calls[0]

    tool_message = next(
        message
        for message in call["messages"]
        if message.get("role") == "tool"
    )

    content = tool_message["content"]

    assert content == (
        "CANONICAL_SYSTEM_REPORT\n"
        "Windows release: 11\n"
        "Windows version: 10.0.26200\n"
        "Logical CPU count: 8\n"
        "Total memory: 7.84 GiB\n"
        "Available memory: 1.8 GiB\n"
        "Total system-drive disk space: 118.01 GiB\n"
        "Free system-drive disk space: 10.56 GiB"
    )

    # Raw and ambiguous values must not reach the local model.
    assert "memory_total_bytes" not in content
    assert "memory_available_bytes" not in content
    assert "disk_total_bytes" not in content
    assert "disk_free_bytes" not in content

    assert "processor" not in content.lower()
    assert "hostname" not in content.lower()
    assert "machine" not in content.lower()

    # Provider transformation is model-only.
    # Persisted conversation/audit data stays untouched.
    assert (
        context.values["messages"][2]["content"]
        == original_content
    )


@pytest.mark.asyncio
async def test_ollama_does_not_compact_unrelated_tool_results(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    original_content = (
        "{'status': 'ok', 'value': 123}"
    )

    context = Context()

    context.values["messages"] = [
        {
            "role": "user",
            "content": "Check status.",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "other-call",
                    "type": "function",
                    "function": {
                        "name": "status",
                        "arguments": "{}",
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "other-call",
            "content": original_content,
        },
    ]

    await provider.generate(
        Request("Check status."),
        context,
    )

    call = client.completions.calls[0]

    tool_message = next(
        message
        for message in call["messages"]
        if message.get("role") == "tool"
    )

    assert (
        tool_message["content"]
        == original_content
    )


@pytest.mark.asyncio
async def test_ollama_leaves_malformed_system_info_result_unchanged(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    original_content = "not a valid mapping"

    context = Context()

    context.values["messages"] = [
        {
            "role": "user",
            "content": "Inspect the system.",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "system-call",
                    "type": "function",
                    "function": {
                        "name": "get_windows_system_info",
                        "arguments": "{}",
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "system-call",
            "content": original_content,
        },
    ]

    await provider.generate(
        Request("Inspect the system."),
        context,
    )

    call = client.completions.calls[0]

    tool_message = next(
        message
        for message in call["messages"]
        if message.get("role") == "tool"
    )

    assert (
        tool_message["content"]
        == original_content
    )


@pytest.mark.asyncio
async def test_ollama_ignores_historical_tool_result_for_new_request(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    context = Context()

    context.values["messages"] = [
        {
            "role": "user",
            "content": "Old request.",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "old-call",
                    "type": "function",
                    "function": {
                        "name": "status",
                        "arguments": "{}",
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "old-call",
            "content": "{'status': 'ok'}",
        },
        {
            "role": "assistant",
            "content": "Old response.",
        },
        {
            "role": "user",
            "content": "New request.",
        },
    ]

    await provider.generate(
        Request("New request."),
        context,
        system_prompt="Original system prompt.",
    )

    call = client.completions.calls[0]

    system_messages = [
        message["content"]
        for message in call["messages"]
        if message.get("role") == "system"
    ]

    assert system_messages == [
        "Original system prompt."
    ]

    assert (
        OllamaProvider._TOOL_FINALIZATION_PROMPT
        not in system_messages[0]
    )



@pytest.mark.asyncio
async def test_ollama_uses_verified_system_info_for_final_text(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    request = Request(
        "Inspect the system."
    )

    raw_data = {
        "release": "11",
        "version": "10.0.26200",
        "logical_cpu_count": 8,
        "memory_total_gib": 7.84,
        "memory_available_gib": 1.43,
        "disk_total_gib": 118.01,
        "disk_free_gib": 7.95,
        "memory_total_bytes": 8415662080,
        "disk_total_bytes": 126709653504,
    }

    original_content = str(raw_data)

    context = Context()

    context.values["messages"] = [
        {
            "role": "user",
            "content": request.text,
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "system-call",
                    "type": "function",
                    "function": {
                        "name": "get_windows_system_info",
                        "arguments": "{}",
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "system-call",
            "content": original_content,
        },
    ]

    result = await provider.generate(
        request,
        context,
    )

    assert result.text == (
        "Windows release: 11\n"
        "Windows version: 10.0.26200\n"
        "Logical CPU count: 8\n"
        "Total memory: 7.84 GiB\n"
        "Available memory: 1.43 GiB\n"
        "Total system-drive disk space: 118.01 GiB\n"
        "Free system-drive disk space: 7.95 GiB"
    )

    assert result.finish_reason == "stop"
    assert result.tool_calls == []

    assert (
        result.metadata[
            "deterministic_finalization"
        ]
        == "get_windows_system_info"
    )

    assert (
        context.values["messages"][2]["content"]
        == original_content
    )


@pytest.mark.asyncio
async def test_ollama_does_not_suppress_additional_tool_call(
) -> None:
    client = FakeClient()

    async def create_with_tool_call(
        **kwargs,
    ):
        client.completions.calls.append(
            kwargs
        )

        function = SimpleNamespace(
            name="status",
            arguments="{}",
        )

        tool_call = SimpleNamespace(
            id="next-call",
            type="function",
            function=function,
        )

        message = SimpleNamespace(
            content="",
            tool_calls=[tool_call],
        )

        choice = SimpleNamespace(
            message=message,
            finish_reason="tool_calls",
        )

        return SimpleNamespace(
            choices=[choice],
            usage=None,
        )

    client.completions.create = (
        create_with_tool_call
    )

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    request = Request(
        "Inspect the system."
    )

    raw_data = {
        "release": "11",
        "version": "10.0.26200",
        "logical_cpu_count": 8,
        "memory_total_gib": 7.84,
        "memory_available_gib": 1.43,
        "disk_total_gib": 118.01,
        "disk_free_gib": 7.95,
    }

    context = Context()

    context.values["messages"] = [
        {
            "role": "user",
            "content": request.text,
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "system-call",
                    "type": "function",
                    "function": {
                        "name": "get_windows_system_info",
                        "arguments": "{}",
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "system-call",
            "content": str(raw_data),
        },
    ]

    result = await provider.generate(
        request,
        context,
    )

    assert result.text == ""
    assert result.finish_reason == "tool_calls"

    assert result.tool_calls == [
        {
            "id": "next-call",
            "type": "function",
            "function": {
                "name": "status",
                "arguments": "{}",
            },
        }
    ]

    assert (
        "deterministic_finalization"
        not in result.metadata
    )


@pytest.mark.asyncio
async def test_ollama_does_not_override_multi_tool_final_answer(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    request = Request(
        "Inspect the system and status."
    )

    raw_data = {
        "release": "11",
        "version": "10.0.26200",
        "logical_cpu_count": 8,
        "memory_total_gib": 7.84,
        "memory_available_gib": 1.43,
        "disk_total_gib": 118.01,
        "disk_free_gib": 7.95,
    }

    context = Context()

    context.values["messages"] = [
        {
            "role": "user",
            "content": request.text,
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "system-call",
                    "type": "function",
                    "function": {
                        "name": "get_windows_system_info",
                        "arguments": "{}",
                    },
                },
                {
                    "id": "status-call",
                    "type": "function",
                    "function": {
                        "name": "status",
                        "arguments": "{}",
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "system-call",
            "content": str(raw_data),
        },
        {
            "role": "tool",
            "tool_call_id": "status-call",
            "content": "{'status': 'ok'}",
        },
    ]

    result = await provider.generate(
        request,
        context,
    )

    assert result.text == "local response"

    assert (
        "deterministic_finalization"
        not in result.metadata
    )


def test_provider_preferences_accept_ollama(
    tmp_path,
) -> None:
    assert validate_provider("ollama") == "ollama"

    store = ProviderPreferencesStore(
        tmp_path / "settings.json"
    )

    store.save(
        ProviderPreferences(
            provider="ollama",
            model=DEFAULT_OLLAMA_MODEL,
        )
    )

    loaded = store.load()

    assert loaded.provider == "ollama"
    assert loaded.model == DEFAULT_OLLAMA_MODEL


def test_ollama_environment_settings(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "JARVIS_OLLAMA_MODEL",
        "llama3.2:latest",
    )

    monkeypatch.setenv(
        "JARVIS_OLLAMA_BASE_URL",
        "http://localhost:11434/v1/",
    )

    monkeypatch.setenv(
        "JARVIS_OLLAMA_ENABLED",
        "true",
    )

    settings = Settings.from_environment()

    assert settings.ollama_model == "llama3.2:latest"

    assert (
        settings.ollama_base_url
        == "http://localhost:11434/v1/"
    )

    assert settings.ollama_enabled is True


def test_bootstrap_registers_ollama_without_network_call(
) -> None:
    application = create_application(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
            ollama_enabled=True,
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            conversation_database_path=None,
        )
    )

    try:
        assert application.provider_registry.contains(
            "ollama"
        )

        assert (
            application.provider_registry
            .get_default()
            .name
            == "ollama"
        )

        assert application.model_catalog.contains(
            "ollama",
            DEFAULT_OLLAMA_MODEL,
        )

        provider = (
            application.provider_registry
            .get("ollama")
        )

        assert provider.is_configured is True

    finally:
        application.close()
