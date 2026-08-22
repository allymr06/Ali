from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Event

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import Context, Request, Response
from app.research.models import ResearchReport
from app.ui.controller import AsyncRunner, DesktopController
from app.ui.models import ChatMessage


def application():
    return create_application(
        Settings(
            default_provider="mock",
            default_model="mock-model",
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
        )
    )


def test_desktop_snapshot_uses_live_application_state() -> None:
    app = application()
    app.memory_manager.remember("The user prefers concise reports.")
    app.task_manager.create("Verify the interface")

    snapshot = DesktopController(app).snapshot()

    assert snapshot.provider == "mock"
    assert snapshot.model == "mock-model"
    assert snapshot.memory_count == 1
    assert snapshot.task_count == 1
    assert snapshot.tool_count == snapshot.enabled_tools
    assert snapshot.voice_available is False
    assert snapshot.vision_available is False
    assert snapshot.research_available is False
    assert snapshot.windows_available is False


@pytest.mark.asyncio
async def test_desktop_command_enters_real_core_and_preserves_conversation() -> None:
    controller = DesktopController(application())

    message = await controller.submit_command("Hello JARVIS")

    assert message.role == "assistant"
    assert message.text == "Mock yanıtı: Hello JARVIS"
    assert [item.role for item in controller.state.messages] == ["user", "assistant"]
    assert controller.state.busy is False
    assert controller.state.status == "LOCAL CORE READY"


@pytest.mark.asyncio
async def test_desktop_command_maps_final_assurance_metadata() -> None:
    app = application()

    class AssuranceEngine:
        async def handle(self, request, context, **_kwargs):
            return Response(
                "Kanıtlı yanıt",
                request_id=request.request_id,
                metadata={
                    "reasoning_level": "high",
                    "assurance_level": "tool_verified",
                    "uncertainty_summary": "Sınırlı kapsam.",
                    "provider_metadata": {"private": "not-for-ui"},
                },
            )

    app.engine = AssuranceEngine()
    controller = DesktopController(app)

    message = await controller.submit_command("Doğrula")

    assert message.metadata == {
        "reasoning_level": "high",
        "assurance_level": "tool_verified",
        "uncertainty_summary": "Sınırlı kapsam.",
    }
    assert controller.state.messages[-1] == message


def test_controller_restores_persisted_assurance_metadata() -> None:
    app = application()
    context = Context()
    request = Request("Önceki soru")
    response = Response(
        "Önceki yanıt",
        request_id=request.request_id,
        metadata={
            "outcome": "completed",
            "provider": "gemini",
            "model": "gemini-test",
            "reasoning_level": "medium",
            "assurance_level": "research_supported",
            "uncertainty_summary": "Kaynak tarihi bilinmiyor.",
        },
    )
    app.conversation_engine.prepare_request(request, context)
    app.conversation_engine.complete_response(request, response, context)

    controller = DesktopController(app)

    assert controller.context.conversation_id == context.conversation_id
    assert controller.state.messages[-1].metadata == {
        "outcome": "completed",
        "provider": "gemini",
        "model": "gemini-test",
        "reasoning_level": "medium",
        "assurance_level": "research_supported",
        "uncertainty_summary": "Kaynak tarihi bilinmiyor.",
    }




@pytest.mark.asyncio
async def test_desktop_command_forwards_stream_updates(
) -> None:
    app = application()
    updates = []

    class StreamingEngine:
        async def handle(
            self,
            request,
            context,
            *,
            stream_callback=None,
            **_kwargs,
        ):
            assert request.text == "Merhaba"
            assert context is not None
            assert stream_callback is not None

            stream_callback("Mer")
            stream_callback("Merhaba")

            return Response(
                "Merhaba"
            )

    app.engine = StreamingEngine()

    controller = DesktopController(
        app
    )

    message = await controller.submit_command(
        "Merhaba",
        stream_callback=updates.append,
    )

    assert updates == [
        "Mer",
        "Merhaba",
    ]

    assert message.role == "assistant"
    assert message.text == "Merhaba"


def test_typewriter_progresses_without_dumping_large_chunk() -> None:
    from app.ui.desktop import next_typewriter_text

    target = "JARVIS " * 40
    rendered = next_typewriter_text("", target)

    assert rendered
    assert rendered != target
    assert target.startswith(rendered)

    for _ in range(100):
        rendered = next_typewriter_text(rendered, target)

    assert rendered == target


@pytest.mark.asyncio
async def test_desktop_provider_error_keeps_readable_turkish() -> None:
    app = application()

    class FailingEngine:
        async def handle(self, *_args, **_kwargs):
            raise RuntimeError("provider unavailable")

    app.engine = FailingEngine()
    controller = DesktopController(app)

    message = await controller.submit_command("Neşelendir beni")

    assert message.role == "system"
    assert message.text.startswith("İstek tamamlanamadı.")
    assert "oturumu korundu" in message.text


@pytest.mark.asyncio
async def test_desktop_accepts_a_new_command_after_provider_failure() -> None:
    app = application()

    class RecoveringEngine:
        calls = 0

        async def handle(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary provider failure")
            return Response("Toparlandım.")

    app.engine = RecoveringEngine()
    controller = DesktopController(app)

    failed = await controller.submit_command("İlk istek")
    recovered = await controller.submit_command("Tekrar dene")

    assert failed.role == "system"
    assert recovered == ChatMessage("assistant", "Toparlandım.")
    assert controller.state.busy is False
    assert controller.state.status == "LOCAL CORE READY"

@pytest.mark.asyncio
async def test_desktop_command_rejects_empty_text() -> None:
    controller = DesktopController(application())
    with pytest.raises(ValueError, match="empty"):
        await controller.submit_command(" ")


@pytest.mark.asyncio
async def test_optional_capabilities_fail_closed_when_disabled() -> None:
    controller = DesktopController(application())
    with pytest.raises(RuntimeError, match="Voice"):
        await controller.run_voice()
    with pytest.raises(RuntimeError, match="Vision"):
        await controller.run_vision("purpose")
    with pytest.raises(RuntimeError, match="research"):
        await controller.run_research("question")


@dataclass
class FakeVoice:
    async def run_once(self, _context):
        return type("VoiceResult", (), {"response_text": "spoken", "state": None})()


@dataclass
class FakeResearch:
    def research(self, query, max_sources):
        return ResearchReport(query, (), (), (f"bounded to {max_sources}",), ())


class FakeVision:
    def request_consent(self, purpose):
        assert purpose == "inspect"
        return type("Consent", (), {"request_id": "request"})()

    def approve_consent(self, request_id):
        assert request_id == "request"
        return "grant"

    async def analyze(self, purpose, grant, *, context):
        assert purpose == "inspect"
        assert grant == "grant"
        assert context is not None
        return type(
            "VisionResult",
            (),
            {"response": Response("visible result"), "error_code": None, "state": None},
        )()


@pytest.mark.asyncio
async def test_controller_bridges_enabled_voice_research_and_vision() -> None:
    app = application()
    app.voice = FakeVoice()
    app.research = FakeResearch()
    app.vision = FakeVision()
    controller = DesktopController(app)

    assert await controller.run_voice() == "spoken"
    report = await controller.run_research("question", max_sources=3)
    assert report["uncertainties"] == ["bounded to 3"]
    assert await controller.run_vision("inspect") == "visible result"


@pytest.mark.asyncio
async def test_voice_messages_can_be_marshaled_to_the_ui_thread() -> None:
    app = application()
    app.voice = FakeVoice()
    controller = DesktopController(app)
    updates: list[ChatMessage] = []

    result = await controller.run_voice(
        message_callback=updates.append,
        manage_state=False,
    )

    assert result == "spoken"
    assert updates == [ChatMessage("assistant", "spoken")]
    assert controller.state.messages == []


@pytest.mark.asyncio
async def test_voice_uses_an_independent_context_and_history() -> None:
    app = application()

    class ContextVoice:
        context = None

        async def run_once(self, context):
            self.context = context
            return type(
                "VoiceResult",
                (),
                {
                    "transcript": "Sesli soru",
                    "response_text": "Sesli yanıt",
                    "state": None,
                },
            )()

    voice = ContextVoice()
    app.voice = voice
    controller = DesktopController(app)
    await controller.submit_command("Metin sorusu")
    text_messages = list(controller.state.messages)

    await controller.run_voice()

    assert voice.context is controller.voice_context
    assert voice.context is not controller.context
    assert controller.state.messages == text_messages
    assert controller.state.voice_messages == [
        ChatMessage("user", "Sesli soru"),
        ChatMessage("assistant", "Sesli yanıt"),
    ]


def test_async_runner_executes_and_closes_once() -> None:
    async def value():
        return 42

    runner = AsyncRunner()
    assert runner.submit(value()).result(timeout=2) == 42
    runner.close()
    runner.close()


def test_async_runner_cancels_pending_work_before_loop_shutdown() -> None:
    started = Event()
    finalized = Event()

    async def pending() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            finalized.set()

    runner = AsyncRunner()
    runner.submit(pending())
    assert started.wait(timeout=1)

    runner.close()

    assert finalized.wait(timeout=1)
    assert not runner._thread.is_alive()


@pytest.mark.asyncio
async def test_desktop_command_can_leave_state_updates_to_ui_thread() -> None:
    controller = DesktopController(application())

    message = await controller.submit_command(
        "Merhaba",
        manage_state=False,
    )

    assert message.role == "assistant"
    assert controller.state.messages == []
    assert controller.state.busy is False
    assert controller.state.status == "LOCAL CORE READY"


def test_desktop_module_import_does_not_create_a_window() -> None:
    from app.ui.desktop import (
        DISPLAY_MODEL_NAME,
        NAVIGATION,
        SHORTCUTS,
        DesktopWindow,
        enable_high_dpi_rendering,
        localize_token,
    )

    assert DesktopWindow is not None
    assert DISPLAY_MODEL_NAME == "JARVIS 0.2"
    assert callable(enable_high_dpi_rendering)
    assert localize_token("running") == "ÇALIŞIYOR"
    assert tuple(item[0] for item in NAVIGATION) == tuple(__import__("app.ui.models", fromlist=["UIScreen"]).UIScreen)
    assert ("Enter", "Komutu gönder") in SHORTCUTS


def test_controller_can_replace_and_close_live_application() -> None:
    first = application()
    second = application()
    controller = DesktopController(first)

    controller.replace_application(second)

    assert controller.application is second
    assert controller.context.values == {}
    controller.close()
