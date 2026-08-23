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
    # No process-termination tool exists in JARVIS, so even under
    # full-exposure fallback the selector can never surface one — it
    # only ever exposes tools that are actually available.
    result = select(
        "Bu processi kapat."
    )

    assert "kill_process" not in result.names
    assert "terminate_process" not in result.names


def test_unresolved_intent_exposes_available_tools_for_the_model():
    # A phrasing the deterministic vocabulary does not recognize must
    # not leave the model blind; it sees the available inventory and
    # resolves intent itself. Destructive tools remain gated by the
    # permission engine at execution time, not by hiding them here.
    result = select(
        "Bunu tamamen sil."
    )

    assert result.reason == "intent_unresolved_full_exposure"
    assert result.names == frozenset(ALL_NAMES)


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
    # The launcher is not available, so even a clear launch intent can
    # only fall back to the available inventory \u2014 never invent a tool.
    result = ToolSchemaSelector().select(
        Request(
            "Chrome a\u00e7."
        ),
        available_names={
            "diagnostics_metrics",
        },
    )

    assert result.names.issubset({"diagnostics_metrics"})


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


def _select(text):
    from app.core.models import Request
    from app.tools.selection import ToolSchemaSelector

    available = {
        "spotify_play_pause", "spotify_play_track", "spotify_now_playing",
        "spotify_next_track", "spotify_previous_track",
        "spotify_create_playlist", "spotify_listening_stats",
        "whatsapp_read_chats", "whatsapp_open_chat",
        "whatsapp_send_message", "whatsapp_add_contact",
        "whatsapp_list_contacts",
        "create_reminder", "list_reminders", "cancel_reminder",
        "open_website", "open_web_search", "system_volume",
        "launch_windows_application",
    }
    return set(ToolSchemaSelector().select(Request(text), available_names=available).names)


def test_selector_exposes_spotify_tools() -> None:
    assert "spotify_next_track" in _select("Spotify'da sonraki şarkıya geç")
    assert "spotify_play_pause" in _select("Müziği başlat")
    assert _select("Spotify dinleme istatistiklerime bak") == {
        "spotify_listening_stats"
    }
    assert "spotify_create_playlist" in _select(
        "Spotify'da bana bir playlist oluştur"
    )


def test_selector_exposes_whatsapp_tools() -> None:
    send = _select("WhatsApp'tan Ali'ye mesaj gönder")
    assert "whatsapp_send_message" in send and len(send) <= 3
    assert _select("WhatsApp mesajlarımı oku") == {"whatsapp_read_chats"}
    assert "whatsapp_add_contact" in _select(
        "WhatsApp rehberine yeni kişi ekle"
    )


def test_selector_exposes_reminder_web_volume_tools() -> None:
    assert "create_reminder" in _select("Bana 10 dakika sonra çayı hatırlat")
    assert "cancel_reminder" in _select("Hatırlatıcıyı iptal et")
    assert "open_web_search" in _select("Google'da hava durumunu araştır")
    assert _select("Sesi biraz kıs") == {"system_volume"}


def test_selector_exposes_whatsapp_delegation_tools() -> None:
    from app.core.models import Request
    from app.tools.selection import ToolSchemaSelector

    available = {
        "whatsapp_delegate_chat", "whatsapp_stop_delegation",
        "whatsapp_delegation_status", "whatsapp_list_contacts",
        "whatsapp_send_message", "whatsapp_read_chats",
        "whatsapp_open_chat", "whatsapp_add_contact",
    }

    def pick(text):
        return set(
            ToolSchemaSelector()
            .select(Request(text), available_names=available)
            .names
        )

    assert "whatsapp_delegate_chat" in pick(
        "WhatsApp'ta Ali ile benim yerime konuş"
    )
    assert "whatsapp_delegate_chat" in pick(
        "WhatsApp'ta Ayşe'nin sorularını yanıtla"
    )


def test_selector_exposes_screen_watch_tools() -> None:
    from app.core.models import Request
    from app.tools.selection import ToolSchemaSelector

    available = {
        "watch_screen_start", "watch_screen_stop",
        "watch_screen_status", "diagnostics_health",
    }

    def pick(text):
        return set(
            ToolSchemaSelector()
            .select(Request(text), available_names=available)
            .names
        )

    assert "watch_screen_start" in pick("Ekranımı sürekli takip et")
    assert pick("Ekran izlemeyi durdur") == {"watch_screen_stop"}
