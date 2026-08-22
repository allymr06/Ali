from __future__ import annotations

import asyncio

from app.core.engine import CoreEngine
from app.core.models import (
    Context,
    Request,
    ToolDefinition,
)
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
)
from app.providers.gateway import ProviderGateway
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor
from app.tools.selection import ToolSchemaSelector


ALL_NAMES = {
    "list_memories",
    "search_memories",
    "forget_memory",
    "delete_memory",
    "list_windows_applications",
    "list_windows_processes",
    "get_windows_system_info",
    "launch_windows_application",
    "list_tasks",
    "get_task",
    "pause_task",
    "resume_task",
    "cancel_task",
    "diagnostics_health",
    "diagnostics_events",
    "diagnostics_metrics",
    "list_allowed_file_roots",
    "list_directory",
    "read_text_file",
    "write_text_file",
    "create_directory",
    "copy_file",
    "move_file",
    "read_windows_clipboard",
    "write_windows_clipboard",
    "clear_windows_clipboard",
    "list_allowed_windows",
    "activate_allowed_window",
    "minimize_allowed_window",
    "restore_allowed_window",
}


def select(text: str):
    return ToolSchemaSelector().select(
        Request(text),
        available_names=set(
            ALL_NAMES
        ),
    )


def test_launch_exposes_only_launcher():
    result = select(
        "Chrome a\u00e7."
    )

    assert result.names == frozenset(
        {
            "launch_windows_application",
        }
    )


def test_pause_task_exposes_only_pause():
    result = select(
        "G\u00f6revi duraklat."
    )

    assert result.names == frozenset(
        {
            "pause_task",
        }
    )


def test_delete_memory_exposes_only_delete():
    result = select(
        "Bu haf\u0131za kayd\u0131n\u0131 sil."
    )

    assert result.names == frozenset(
        {
            "delete_memory",
        }
    )


def test_metrics_exposes_only_metrics():
    result = select(
        "JARVIS metriklerini g\u00f6ster."
    )

    assert result.names == frozenset(
        {
            "diagnostics_metrics",
        }
    )


def test_process_termination_does_not_invent_tool():
    result = select(
        "Bu processi kapat."
    )

    assert result.names == frozenset()


def test_unknown_destructive_request_fails_closed():
    result = select(
        "Bunu tamamen sil."
    )

    assert result.names == frozenset()


def test_bounded_file_write_exposes_root_lookup_and_write_only():
    result = select("notlar dosyasına yaz")
    assert result.names == frozenset(
        {"list_allowed_file_roots", "write_text_file"}
    )


def test_clipboard_clear_exposes_only_confirmed_clear_tool():
    result = select("panoyu temizle")
    assert result.names == frozenset({"clear_windows_clipboard"})


def test_window_minimize_exposes_lookup_and_bounded_action():
    result = select("pencereyi küçült")
    assert result.names == frozenset(
        {"list_allowed_windows", "minimize_allowed_window"}
    )


def test_selector_never_expands_available_tools():
    result = ToolSchemaSelector().select(
        Request(
            "Chrome a\u00e7."
        ),
        available_names={
            "diagnostics_metrics",
        },
    )

    assert result.names == frozenset()


class CapturingProvider(AIProvider):
    def __init__(self, name: str = "gemini"):
        self._name = name
        self.calls = []

    @property
    def name(self):
        return self._name

    @property
    def capabilities(self):
        return ModelCapabilities(
            text=True,
            streaming=True,
            structured_output=True,
            tool_calling=True,
            vision=False,
        )

    @property
    def is_configured(self):
        return True

    async def generate(
        self,
        request,
        context,
        *,
        model=None,
        system_prompt=None,
        tools=None,
        response_format=None,
    ):
        self.calls.append(
            {
                "model": model,
                "tools": tools,
            }
        )

        return ModelResponse(
            text="ok",
            model=(
                model
                or "gemini-3.5-flash-lite"
            ),
            provider=self._name,
        )


def test_core_sends_only_relevant_launch_tool_to_provider():
    provider = CapturingProvider()

    registry = ProviderRegistry(
        default_provider="gemini",
    )
    registry.register(provider)

    executor = ToolExecutor()

    def launch(application: str):
        return {
            "application": application,
        }

    def delete_memory(memory_id: str):
        return {
            "memory_id": memory_id,
        }

    executor.register(
        ToolDefinition(
            name=(
                "launch_windows_application"
            ),
            description="Launch app.",
        ),
        launch,
    )

    executor.register(
        ToolDefinition(
            name="delete_memory",
            description="Delete memory.",
        ),
        delete_memory,
    )

    engine = CoreEngine(
        provider_registry=registry,
        memory_manager=MemoryManager(
            InMemoryStore()
        ),
        tool_executor=executor,
        provider_gateway=ProviderGateway(
            registry,
            max_retries=0,
            fallback_enabled=False,
        ),
        tool_schema_selector=(
            ToolSchemaSelector()
        ),
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Chrome a\u00e7."
            ),
            Context(),
        )
    )

    assert len(provider.calls) == 1

    tools = provider.calls[0]["tools"]

    assert tools is not None
    assert len(tools) == 1

    assert (
        tools[0]["function"]["name"]
        == "launch_windows_application"
    )

    assert (
        response.metadata[
            "tool_schema_count_before"
        ]
        == 2
    )

    assert (
        response.metadata[
            "tool_schema_count_after"
        ]
        == 1
    )

    assert (
        response.metadata[
            "tool_schema_selection_names"
        ]
        == [
            "launch_windows_application",
        ]
    )


def test_core_sends_only_relevant_file_tools_to_gemini():
    provider = CapturingProvider("gemini")

    registry = ProviderRegistry(
        default_provider="gemini",
    )
    registry.register(provider)

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="list_allowed_file_roots",
            description="List roots.",
        ),
        lambda: [],
    )
    executor.register(
        ToolDefinition(
            name="write_text_file",
            description="Write text.",
        ),
        lambda root_id, relative_path, content: {
            "root_id": root_id,
            "relative_path": relative_path,
        },
    )
    executor.register(
        ToolDefinition(
            name="diagnostics_metrics",
            description="Read metrics.",
        ),
        lambda: {},
    )

    engine = CoreEngine(
        provider_registry=registry,
        memory_manager=MemoryManager(InMemoryStore()),
        tool_executor=executor,
        provider_gateway=ProviderGateway(
            registry,
            max_retries=0,
            fallback_enabled=False,
        ),
        tool_schema_selector=ToolSchemaSelector(),
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "İzinli test klasöründe phase8-onay.txt "
                "dosyasına sadece TEST OK yaz."
            ),
            Context(),
        )
    )

    tools = provider.calls[0]["tools"]
    assert tools is not None
    assert {
        item["function"]["name"]
        for item in tools
    } == {
        "list_allowed_file_roots",
        "write_text_file",
    }
    assert response.metadata["tool_schema_count_before"] == 3
    assert response.metadata["tool_schema_count_after"] == 2
