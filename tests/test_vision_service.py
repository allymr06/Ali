from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.core.models import RequestSource, Response
from app.core.time import utc_now
from app.providers.base import AIProvider, ModelCapabilities, ModelResponse
from app.vision.consent import VisionConsentGate
from app.vision.models import (
    PixelImage,
    RedactionRegion,
    ScreenBounds,
    VisionDetail,
    VisionSessionState,
    VisionSourceKind,
)
from app.vision.service import VisionService


class FakeSource:
    source_id = "test-screen"
    kind = VisionSourceKind.VIRTUAL_SCREEN

    def __init__(self, *, gate=None, captured_at=None):
        self.gate = gate
        self.captured_at = captured_at
        self.images = []

    def bounds(self):
        return ScreenBounds(0, 0, 4, 3)

    async def capture(self, *, cancel_event=None):
        if self.gate:
            await self.gate.wait()
        image = PixelImage(
            4,
            3,
            bytearray([255] * 36),
            self.captured_at or utc_now(),
        )
        self.images.append(image)
        return image


class FakeEngine:
    def __init__(self, *, gate=None):
        self.gate = gate
        self.snapshots = []

    async def handle(self, request, context=None, *, cancel_event=None):
        image = request.metadata["images"][0]
        self.snapshots.append(
            {
                "text": request.text,
                "source": request.source,
                "data": bytes(image["data"]),
                "mime": image["mime_type"],
                "detail": image["detail"],
                "provenance": dict(request.metadata["vision_provenance"]),
            }
        )
        if self.gate:
            await self.gate.wait()
        return Response(
            "The screen contains a test pattern.",
            request_id=request.request_id,
            metadata={"provider": "vision-test", "model": "vision-1"},
        )


def make_service(**overrides):
    values = {
        "engine": FakeEngine(),
        "source": FakeSource(),
        "consent_gate": VisionConsentGate(),
        "detail": VisionDetail.HIGH,
        "operation_timeout_seconds": 1,
        "max_frame_age_seconds": 1,
        "max_encoded_bytes": 100_000,
        "redact_taskbar": True,
        "taskbar_height": 1,
    }
    values.update(overrides)
    return VisionService(**values), values


def approve(service, purpose="Describe the screen", redactions=()):
    request = service.request_consent(purpose, redactions=redactions)
    return service.approve_consent(request.request_id)


@pytest.mark.asyncio
async def test_complete_vision_turn_redacts_routes_and_records_provenance() -> None:
    service, parts = make_service()
    redactions = (RedactionRegion(0, 0, 1, 1, "secret"),)
    grant = approve(service, redactions=redactions)

    result = await service.analyze(
        "Describe the screen", grant, redactions=redactions
    )

    assert result.state is VisionSessionState.COMPLETED
    assert result.response_text == "The screen contains a test pattern."
    assert result.metadata == {"provider": "vision-test", "model": "vision-1"}
    assert result.provenance.source_id == "test-screen"
    assert result.provenance.transformations == (
        "redacted:secret",
        "redacted:taskbar",
    )
    snapshot = parts["engine"].snapshots[0]
    assert snapshot["source"] is RequestSource.VISION
    assert snapshot["mime"] == "image/png"
    assert snapshot["detail"] == "high"
    assert snapshot["data"].startswith(b"\x89PNG")
    assert all(image.pixels == bytearray() for image in parts["source"].images)
    assert [event.state for event in service.events[-4:]] == [
        VisionSessionState.CAPTURING,
        VisionSessionState.REDACTING,
        VisionSessionState.ANALYZING,
        VisionSessionState.COMPLETED,
    ]
    assert all(event.session_id == result.session_id for event in service.events)


@pytest.mark.asyncio
async def test_analysis_requires_exact_one_use_consent() -> None:
    service, parts = make_service()
    grant = approve(service)
    result = await service.analyze("Changed purpose", grant)
    assert result.state is VisionSessionState.FAILED
    assert result.error_code == "consent"
    assert parts["source"].images == []


@pytest.mark.asyncio
async def test_stale_frame_is_rejected_before_provider() -> None:
    source = FakeSource(captured_at=utc_now() - timedelta(seconds=10))
    service, parts = make_service(source=source)
    result = await service.analyze("Describe", approve(service, "Describe"))
    assert result.state is VisionSessionState.STALE
    assert result.error_code == "stale"
    assert parts["engine"].snapshots == []


@pytest.mark.asyncio
async def test_out_of_bounds_redaction_fails_before_provider() -> None:
    service, parts = make_service()
    region = (RedactionRegion(3, 2, 2, 1),)
    result = await service.analyze(
        "Describe", approve(service, "Describe", region), redactions=region
    )
    assert result.error_code == "privacy"
    assert parts["engine"].snapshots == []


@pytest.mark.asyncio
async def test_capture_timeout_is_bounded_and_classified() -> None:
    service, _ = make_service(
        source=FakeSource(gate=asyncio.Event()),
        operation_timeout_seconds=0.01,
    )
    result = await service.analyze("Describe", approve(service, "Describe"))
    assert result.state is VisionSessionState.FAILED
    assert result.error_code == "timeout"


@pytest.mark.asyncio
async def test_active_analysis_can_be_interrupted() -> None:
    gate = asyncio.Event()
    service, _ = make_service(engine=FakeEngine(gate=gate))
    task = asyncio.create_task(
        service.analyze("Describe", approve(service, "Describe"))
    )
    for _ in range(100):
        if service.state is VisionSessionState.ANALYZING:
            break
        await asyncio.sleep(0)
    assert await service.interrupt() is True
    assert (await task).state is VisionSessionState.INTERRUPTED


@pytest.mark.asyncio
async def test_explicit_retention_can_be_cleared_and_is_replaced_safely() -> None:
    service, _ = make_service(retain_last_image=True)
    await service.analyze("One", approve(service, "One"))
    first = service.last_image
    assert first
    await service.analyze("Two", approve(service, "Two"))
    assert first == bytearray()
    assert service.last_image
    assert service.clear_retained_image() is True
    assert service.last_image is None


class InspectingVisionProvider(AIProvider):
    def __init__(self):
        self.requests = []

    @property
    def name(self):
        return "gemini"

    @property
    def capabilities(self):
        return ModelCapabilities(text=True, vision=True, tool_calling=True)

    async def generate(self, request, context, **kwargs):
        self.requests.append((request, kwargs))
        return ModelResponse("Real Core saw the image.", kwargs["model"], self.name)


@pytest.mark.asyncio
async def test_vision_vertical_slice_runs_through_real_core_and_router() -> None:
    from app.bootstrap import create_application
    from app.config.provider_preferences import DEFAULT_GEMINI_MODEL
    from app.config.settings import Settings

    application = create_application(
        Settings(
            default_provider="mock",
            default_model="mock-model",
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            windows_integrations_enabled=False,
        )
    )
    provider = InspectingVisionProvider()
    application.provider_registry.unregister("gemini")
    application.provider_registry.register(provider)
    service, _ = make_service(engine=application.engine)

    result = await service.analyze("Describe", approve(service, "Describe"))

    assert result.state is VisionSessionState.COMPLETED
    assert result.response_text == "Real Core saw the image."
    assert provider.requests[0][0].metadata["vision"] is True
    assert provider.requests[0][1]["model"] == DEFAULT_GEMINI_MODEL
