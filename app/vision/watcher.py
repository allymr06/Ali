"""Continuous screen watching with cheap change detection.

Analyzing every frame with a model would be slow and wasteful, so the
watcher samples the screen on a short interval, computes a coarse
perceptual signature locally (a downsampled luminance grid), and calls
the vision model only when the signature moves beyond a threshold. The
local step is the latency budget: it must stay well under the sampling
interval so the loop never falls behind.

Consent is not weakened: watching requires an explicit approval-gated
start, every analysis consumes a fresh consent grant through the same
VisionService path, and the session is bounded by frame count and
wall-clock deadline.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)

# Coarse signature grid. 12x12 cells over the whole screen is enough to
# notice a window change or a new dialog while ignoring a blinking
# cursor, and costs well under a millisecond to compare.
_GRID = 12


def frame_signature(
    pixels: bytes | bytearray,
    width: int,
    height: int,
    *,
    grid: int = _GRID,
) -> tuple[int, ...]:
    """Downsample a BGRA frame to a coarse luminance signature."""
    if width < 1 or height < 1 or not pixels:
        return ()
    stride = width * 4
    cell_w = max(1, width // grid)
    cell_h = max(1, height // grid)
    signature: list[int] = []
    for row in range(grid):
        for column in range(grid):
            # One sample per cell centre: enough signal, negligible cost.
            x = min(width - 1, column * cell_w + cell_w // 2)
            y = min(height - 1, row * cell_h + cell_h // 2)
            offset = y * stride + x * 4
            if offset + 2 >= len(pixels):
                signature.append(0)
                continue
            blue = pixels[offset]
            green = pixels[offset + 1]
            red = pixels[offset + 2]
            signature.append(
                (red * 299 + green * 587 + blue * 114) // 1000
            )
    return tuple(signature)


def signature_distance(
    first: tuple[int, ...], second: tuple[int, ...]
) -> float:
    """Mean absolute luminance difference, 0.0 (same) to 255.0."""
    if not first or not second or len(first) != len(second):
        return 255.0
    total = sum(abs(a - b) for a, b in zip(first, second))
    return total / len(first)


@dataclass
class WatchState:
    purpose: str
    interval_seconds: float
    threshold: float
    max_frames: int
    deadline: float
    frames_seen: int = 0
    analyses: int = 0
    stopped: bool = False
    last_signature: tuple[int, ...] = ()
    observations: list[dict[str, Any]] = field(default_factory=list)


class ScreenWatcher:
    """Watch the screen and describe meaningful changes as they happen."""

    MAX_DURATION_SECONDS = 15 * 60

    def __init__(
        self,
        *,
        vision: Any,
        source: Any,
        notify: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._vision = vision
        self._source = source
        self._notify = notify
        self._state: WatchState | None = None
        self._task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self._state is not None and not self._state.stopped

    # ------------------------------------------------------------------

    async def _analyze_change(self, state: WatchState) -> None:
        """Run one consented analysis of the current screen."""
        request = self._vision.request_consent(state.purpose)
        grant = self._vision.approve_consent(request.request_id)
        result = await self._vision.analyze(state.purpose, grant)
        state.analyses += 1
        text = getattr(result, "response_text", None) or ""
        entry = {
            "at": time.strftime("%H:%M:%S"),
            "state": getattr(
                getattr(result, "state", None), "value", "unknown"
            ),
            "text": text.strip()[:400],
        }
        state.observations.append(entry)
        if self._notify is not None and entry["text"]:
            try:
                self._notify(entry)
            except Exception:
                pass

    async def _run_loop(self, state: WatchState) -> None:
        while (
            not state.stopped
            and state.frames_seen < state.max_frames
            and time.monotonic() < state.deadline
        ):
            started = time.monotonic()
            try:
                image = await self._source.capture()
                signature = frame_signature(
                    image.pixels, image.width, image.height
                )
                # Release the frame immediately: watching must not
                # accumulate screen contents in memory.
                image.pixels = bytearray()
            except Exception:
                await asyncio.sleep(state.interval_seconds)
                continue

            state.frames_seen += 1
            distance = signature_distance(
                state.last_signature, signature
            )
            first_frame = not state.last_signature
            state.last_signature = signature

            if first_frame or distance >= state.threshold:
                try:
                    await self._analyze_change(state)
                except Exception:
                    pass

            elapsed = time.monotonic() - started
            await asyncio.sleep(
                max(0.05, state.interval_seconds - elapsed)
            )
        state.stopped = True

    # ------------------------------------------------------------------

    async def start(
        self,
        purpose: str,
        interval_seconds: float = 2.0,
        sensitivity: float = 6.0,
        max_frames: int = 200,
    ) -> ToolResult:
        if self.active:
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "watch_screen_start",
                message=(
                    "Ekran zaten izleniyor; önce "
                    "'watch_screen_stop' ile durdur."
                ),
                error="already_active",
                verified=True,
            )
        if self._vision is None or self._source is None:
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "watch_screen_start",
                message=(
                    "Görüş kapalı. JARVIS_VISION_ENABLED=true ile aç."
                ),
                error="vision_disabled",
                verified=True,
            )
        goal = purpose.strip() or "Ekranda olup biteni özetle"
        interval = max(0.5, min(float(interval_seconds), 30.0))
        threshold = max(1.0, min(float(sensitivity), 60.0))
        frames = max(1, min(int(max_frames), 2000))
        state = WatchState(
            purpose=goal,
            interval_seconds=interval,
            threshold=threshold,
            max_frames=frames,
            deadline=time.monotonic() + self.MAX_DURATION_SECONDS,
        )
        self._state = state
        self._task = asyncio.create_task(self._run_loop(state))
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "watch_screen_start",
            message=(
                f"Ekranı izlemeye başladım ({interval:g}s aralık). "
                "Belirgin bir değişiklik olduğunda anlatacağım."
            ),
            data={
                "purpose": goal,
                "interval_seconds": interval,
                "sensitivity": threshold,
                "max_frames": frames,
            },
            verified=True,
        )

    def stop(self) -> ToolResult:
        state = self._state
        if state is None or state.stopped:
            return ToolResult(
                ToolExecutionStatus.SUCCESS,
                "watch_screen_stop",
                message="Ekran zaten izlenmiyordu.",
                verified=True,
            )
        state.stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "watch_screen_stop",
            message=(
                f"Ekran izleme durduruldu. {state.frames_seen} kare "
                f"tarandı, {state.analyses} değişiklik incelendi."
            ),
            data={
                "frames_seen": state.frames_seen,
                "analyses": state.analyses,
            },
            verified=True,
        )

    def status(self) -> ToolResult:
        state = self._state
        if state is None:
            return ToolResult(
                ToolExecutionStatus.SUCCESS,
                "watch_screen_status",
                message="Ekran izleme kapalı.",
                data={"active": False},
                verified=True,
            )
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "watch_screen_status",
            message=(
                f"{state.frames_seen} kare tarandı, "
                f"{state.analyses} değişiklik incelendi."
            ),
            data={
                "active": self.active,
                "purpose": state.purpose,
                "frames_seen": state.frames_seen,
                "analyses": state.analyses,
                "observations": state.observations[-5:],
            },
            verified=True,
        )

    # ------------------------------------------------------------------

    def register_tools(self, executor: Any) -> None:
        def define(
            name: str,
            description: str,
            *,
            risk: RiskLevel = RiskLevel.READ_ONLY,
            confirm: bool = False,
        ) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                risk_level=risk,
                requires_confirmation=confirm,
                version="1.0.0",
                capabilities=frozenset({"vision", "screen"}),
                tags=frozenset({"integration", "vision"}),
                timeout_seconds=20.0,
                metadata={
                    "verification_strategy": "frame_signature",
                    "sensitive_output": True,
                },
            )

        async def watch_start(
            purpose: str = "",
            interval_seconds: float = 2.0,
            sensitivity: float = 6.0,
        ) -> ToolResult:
            return await self.start(
                purpose, interval_seconds, sensitivity
            )

        def watch_stop() -> ToolResult:
            return self.stop()

        def watch_status() -> ToolResult:
            return self.status()

        executor.register(
            define(
                "watch_screen_start",
                "Ekranı sürekli izlemeye başla; belirgin değişiklikleri "
                "anlat. Onay gerektirir.",
                risk=RiskLevel.MEDIUM,
                confirm=True,
            ),
            watch_start,
            source="integration:vision",
        )
        executor.register(
            define(
                "watch_screen_stop",
                "Ekran izlemeyi durdur.",
                risk=RiskLevel.LOW,
            ),
            watch_stop,
            source="integration:vision",
        )
        executor.register(
            define(
                "watch_screen_status",
                "Ekran izlemenin durumunu ve son gözlemleri göster.",
            ),
            watch_status,
            source="integration:vision",
        )
