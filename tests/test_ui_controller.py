from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import Response
from app.research.models import ResearchReport
from app.ui.controller import AsyncRunner, DesktopController


def application():
    return create_application(
        Settings(
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


def test_async_runner_executes_and_closes_once() -> None:
    async def value():
        return 42

    runner = AsyncRunner()
    assert runner.submit(value()).result(timeout=2) == 42
    runner.close()
    runner.close()


def test_desktop_module_import_does_not_create_a_window() -> None:
    from app.ui.desktop import NAVIGATION, SHORTCUTS, DesktopWindow

    assert DesktopWindow is not None
    assert tuple(item[0] for item in NAVIGATION) == tuple(__import__("app.ui.models", fromlist=["UIScreen"]).UIScreen)
    assert ("Enter", "Send the prompt from the command composer") in SHORTCUTS


def test_controller_can_replace_and_close_live_application() -> None:
    first = application()
    second = application()
    controller = DesktopController(first)

    controller.replace_application(second)

    assert controller.application is second
    assert controller.context.values == {}
    controller.close()
