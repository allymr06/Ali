from __future__ import annotations

import asyncio

import pytest

from app.vision.capture import WindowsScreenSource
from app.vision.errors import VisionCaptureError, VisionInterrupted
from app.vision.models import PixelImage, ScreenBounds, VisionSourceKind


class Backend:
    def __init__(self, bounds=ScreenBounds(0, 0, 2, 2), result=None):
        self._bounds = bounds
        self.result = result or PixelImage(2, 2, bytearray([10] * 12))
        self.captured_bounds = None

    def bounds(self):
        return self._bounds

    def capture(self, bounds):
        self.captured_bounds = bounds
        return self.result


@pytest.mark.asyncio
async def test_windows_source_captures_through_bounded_backend() -> None:
    backend = Backend()
    source = WindowsScreenSource(backend=backend, max_width=10, max_height=10)

    result = await source.capture()

    assert result.width == 2
    assert backend.captured_bounds == ScreenBounds(0, 0, 2, 2)
    assert source.kind is VisionSourceKind.VIRTUAL_SCREEN


def test_windows_source_rejects_oversized_screen_before_capture() -> None:
    source = WindowsScreenSource(
        backend=Backend(ScreenBounds(0, 0, 100, 100)), max_pixels=100
    )
    with pytest.raises(VisionCaptureError, match="pixel count"):
        source.bounds()


@pytest.mark.asyncio
async def test_windows_source_honors_pre_capture_interruption() -> None:
    event = asyncio.Event()
    event.set()
    source = WindowsScreenSource(backend=Backend())
    with pytest.raises(VisionInterrupted):
        await source.capture(cancel_event=event)


@pytest.mark.asyncio
async def test_windows_source_rejects_invalid_backend_result() -> None:
    backend = Backend()
    backend.result = object()
    with pytest.raises(VisionCaptureError, match="invalid image"):
        await WindowsScreenSource(backend=backend).capture()
