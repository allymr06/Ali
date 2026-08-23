from __future__ import annotations

import asyncio

import pytest

from app.core.models import RiskLevel, ToolExecutionStatus
from app.tools.executor import ToolExecutor
from app.vision.watcher import (
    ScreenWatcher,
    frame_signature,
    signature_distance,
)


def make_frame(width, height, value):
    return bytearray([value, value, value, 255] * (width * height))


class FakeImage:
    def __init__(self, width, height, value):
        self.width = width
        self.height = height
        self.pixels = make_frame(width, height, value)


class FakeSource:
    def __init__(self, values):
        self.values = list(values)
        self.captures = 0

    async def capture(self, **kwargs):
        self.captures += 1
        value = (
            self.values.pop(0) if self.values else 0
        )
        return FakeImage(48, 48, value)


class FakeVision:
    def __init__(self):
        self.analyses = 0

    def request_consent(self, purpose):
        from types import SimpleNamespace

        return SimpleNamespace(request_id="req")

    def approve_consent(self, request_id):
        from types import SimpleNamespace

        return SimpleNamespace(request_id=request_id)

    async def analyze(self, purpose, grant, **kwargs):
        from types import SimpleNamespace

        self.analyses += 1
        return SimpleNamespace(
            state=SimpleNamespace(value="completed"),
            response_text=f"gözlem {self.analyses}",
        )


# ----------------------------------------------------------- signatures


def test_signature_is_stable_and_cheap() -> None:
    frame = make_frame(64, 64, 100)
    first = frame_signature(frame, 64, 64)
    second = frame_signature(frame, 64, 64)
    assert first == second
    assert len(first) == 144
    assert signature_distance(first, second) == 0.0


def test_signature_detects_real_change() -> None:
    dark = frame_signature(make_frame(64, 64, 20), 64, 64)
    bright = frame_signature(make_frame(64, 64, 200), 64, 64)
    assert signature_distance(dark, bright) > 100.0


def test_signature_handles_degenerate_input() -> None:
    assert frame_signature(b"", 0, 0) == ()
    assert signature_distance((), (1, 2)) == 255.0


# -------------------------------------------------------------- watcher


@pytest.mark.asyncio
async def test_watcher_analyzes_only_on_change() -> None:
    # Same value repeated: only the first frame triggers analysis.
    source = FakeSource([50, 50, 50, 50])
    vision = FakeVision()
    watcher = ScreenWatcher(vision=vision, source=source)

    started = await watcher.start(
        "ekranı izle", interval_seconds=0.5, max_frames=4
    )
    assert started.status is ToolExecutionStatus.SUCCESS

    for _ in range(200):
        await asyncio.sleep(0.01)
        if watcher._state.frames_seen >= 4:
            break

    assert vision.analyses == 1  # first frame only
    watcher.stop()


@pytest.mark.asyncio
async def test_watcher_reports_each_distinct_change() -> None:
    source = FakeSource([10, 200, 10, 200])
    vision = FakeVision()
    watcher = ScreenWatcher(vision=vision, source=source)

    await watcher.start(
        "izle", interval_seconds=0.5, sensitivity=5.0, max_frames=4
    )
    for _ in range(200):
        await asyncio.sleep(0.01)
        if watcher._state.frames_seen >= 4:
            break

    assert vision.analyses == 4
    status = watcher.status()
    assert status.data["analyses"] == 4
    assert status.data["observations"]
    watcher.stop()


@pytest.mark.asyncio
async def test_watcher_lifecycle_and_single_instance() -> None:
    watcher = ScreenWatcher(
        vision=FakeVision(), source=FakeSource([1] * 20)
    )
    assert watcher.status().data["active"] is False

    await watcher.start("izle", interval_seconds=0.5)
    assert watcher.active is True

    second = await watcher.start("tekrar")
    assert second.status is ToolExecutionStatus.BLOCKED

    stopped = watcher.stop()
    assert stopped.status is ToolExecutionStatus.SUCCESS
    assert watcher.active is False


@pytest.mark.asyncio
async def test_watcher_blocks_when_vision_disabled() -> None:
    watcher = ScreenWatcher(vision=None, source=None)
    result = await watcher.start("izle")
    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == "vision_disabled"


@pytest.mark.asyncio
async def test_watcher_releases_frames_after_signature() -> None:
    source = FakeSource([7] * 3)
    watcher = ScreenWatcher(vision=FakeVision(), source=source)
    await watcher.start("izle", interval_seconds=0.5, max_frames=2)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if watcher._state.frames_seen >= 2:
            break
    watcher.stop()
    # Frames are captured then discarded, never accumulated.
    assert source.captures >= 2


def test_watch_start_requires_confirmation() -> None:
    executor = ToolExecutor()
    ScreenWatcher(
        vision=FakeVision(), source=FakeSource([])
    ).register_tools(executor)
    definition = executor.get("watch_screen_start").definition
    assert definition.risk_level is RiskLevel.MEDIUM
    assert definition.requires_confirmation is True
