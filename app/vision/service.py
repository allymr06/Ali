from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections import deque
from datetime import timedelta

from app.core.models import Context, Request, RequestSource
from app.core.time import utc_now
from app.vision.base import ImageSource
from app.vision.consent import VisionConsentGate, VisionConsentGrant, VisionConsentRequest
from app.vision.errors import (
    VisionCaptureError,
    VisionConfigurationError,
    VisionConsentError,
    VisionInterrupted,
    VisionPrivacyError,
    VisionTimeoutError,
)
from app.vision.models import (
    ImageProvenance,
    RedactionRegion,
    VisionDetail,
    VisionSessionEvent,
    VisionSessionResult,
    VisionSessionState,
    new_vision_id,
)
from app.vision.privacy import ImageRedactor


class VisionService:
    """One-capture vision analysis with consent, redaction, and provenance."""

    def __init__(
        self,
        *,
        engine,
        source: ImageSource,
        consent_gate: VisionConsentGate,
        redactor: ImageRedactor | None = None,
        detail: VisionDetail = VisionDetail.HIGH,
        operation_timeout_seconds: float = 60.0,
        max_frame_age_seconds: float = 5.0,
        max_encoded_bytes: int = 20_000_000,
        redact_taskbar: bool = True,
        taskbar_height: int = 64,
        retain_last_image: bool = False,
        event_capacity: int = 100,
    ) -> None:
        if min(operation_timeout_seconds, max_frame_age_seconds) <= 0:
            raise ValueError("Vision time limits must be positive.")
        if max_encoded_bytes < 1 or taskbar_height < 0 or event_capacity < 1:
            raise ValueError("Vision size limits cannot be negative or zero.")
        self._engine = engine
        self._source = source
        self._consent_gate = consent_gate
        self._redactor = redactor or ImageRedactor()
        self._detail = detail
        self._timeout = operation_timeout_seconds
        self._max_age = max_frame_age_seconds
        self._max_encoded_bytes = max_encoded_bytes
        self._redact_taskbar = redact_taskbar
        self._taskbar_height = taskbar_height
        self._retain_last_image = retain_last_image
        self._events: deque[VisionSessionEvent] = deque(maxlen=event_capacity)
        self._interrupt_event: asyncio.Event | None = None
        self._active = False
        self._state = VisionSessionState.IDLE
        self._session_id = new_vision_id()
        self._consent_sessions: dict[object, object] = {}
        self.last_image: bytearray | None = None

    @property
    def state(self) -> VisionSessionState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def events(self) -> tuple[VisionSessionEvent, ...]:
        return tuple(self._events)

    def request_consent(
        self,
        purpose: str,
        *,
        redactions: tuple[RedactionRegion, ...] = (),
    ) -> VisionConsentRequest:
        if self._active:
            raise VisionConfigurationError("Cannot request consent during analysis.")
        request = self._consent_gate.request(
            purpose=purpose,
            source_kind=self._source.kind,
            source_id=self._source.source_id,
            redactions=tuple(redactions),
        )
        self._session_id = new_vision_id()
        self._consent_sessions[request.request_id] = self._session_id
        self._record(VisionSessionState.AWAITING_CONSENT)
        return request

    def approve_consent(self, request_id) -> VisionConsentGrant:
        return self._consent_gate.approve(request_id)

    def deny_consent(self, request_id) -> None:
        self._consent_gate.deny(request_id)
        self._session_id = self._consent_sessions.pop(request_id, self._session_id)
        self._record(VisionSessionState.DENIED)

    async def interrupt(self) -> bool:
        if not self._active or self._interrupt_event is None:
            return False
        self._interrupt_event.set()
        return True

    def clear_retained_image(self) -> bool:
        if self.last_image is None:
            return False
        self.last_image[:] = b"\x00" * len(self.last_image)
        self.last_image.clear()
        self.last_image = None
        return True

    async def analyze(
        self,
        purpose: str,
        grant: VisionConsentGrant,
        *,
        redactions: tuple[RedactionRegion, ...] = (),
        context: Context | None = None,
    ) -> VisionSessionResult:
        if self._active:
            raise VisionConfigurationError("Another vision session is already active.")
        self.clear_retained_image()
        self._active = True
        self._session_id = self._consent_sessions.pop(
            grant.request_id, new_vision_id()
        )
        self._interrupt_event = asyncio.Event()
        image = None
        encoded = None
        provenance = None
        try:
            consent_id = self._consent_gate.validate_and_consume(
                grant,
                purpose=purpose,
                source_kind=self._source.kind,
                source_id=self._source.source_id,
                redactions=tuple(redactions),
            )
            self._record(VisionSessionState.CAPTURING)
            image = await self._await(self._source.capture(cancel_event=self._interrupt_event))
            if utc_now() - image.captured_at > timedelta(seconds=self._max_age):
                self._record(VisionSessionState.STALE)
                return self._result(error_code="stale")
            original_sha = image.sha256

            self._record(VisionSessionState.REDACTING)
            applied = list(self._redactor.apply(image, tuple(redactions)))
            if self._redact_taskbar and self._taskbar_height:
                height = min(self._taskbar_height, image.height)
                taskbar = RedactionRegion(
                    0, image.height - height, image.width, height, "taskbar"
                )
                applied.extend(self._redactor.apply(image, (taskbar,)))
            encoded = image.to_png()
            if len(encoded) > self._max_encoded_bytes:
                raise VisionPrivacyError("Processed image exceeds the configured limit.")
            processed_sha = hashlib.sha256(encoded).hexdigest()
            provenance = ImageProvenance(
                frame_id=new_vision_id(),
                source_kind=self._source.kind,
                source_id=self._source.source_id,
                captured_at=image.captured_at,
                width=image.width,
                height=image.height,
                original_sha256=original_sha,
                processed_sha256=processed_sha,
                transformations=tuple(applied),
                consent_id=consent_id,
            )
            if utc_now() - image.captured_at > timedelta(seconds=self._max_age):
                self._record(VisionSessionState.STALE)
                return self._result(provenance=provenance, error_code="stale")

            self._record(VisionSessionState.ANALYZING)
            response = await self._await(
                self._engine.handle(
                    Request(
                        purpose,
                        source=RequestSource.VISION,
                        metadata={
                            "vision": True,
                            "images": [
                                {
                                    "data": encoded,
                                    "mime_type": "image/png",
                                    "detail": self._detail.value,
                                }
                            ],
                            "vision_provenance": {
                                "frame_id": str(provenance.frame_id),
                                "source_kind": provenance.source_kind.value,
                                "source_id": provenance.source_id,
                                "captured_at": provenance.captured_at.isoformat(),
                                "processed_sha256": provenance.processed_sha256,
                                "transformations": list(provenance.transformations),
                            },
                        },
                    ),
                    context,
                    cancel_event=self._interrupt_event,
                )
            )
            self._record(VisionSessionState.COMPLETED)
            if self._retain_last_image:
                self.last_image = bytearray(encoded)
            return self._result(
                response_text=response.text,
                provenance=provenance,
                metadata={
                    "provider": response.metadata.get("provider"),
                    "model": response.metadata.get("model"),
                },
            )
        except VisionInterrupted:
            self._record(VisionSessionState.INTERRUPTED)
            return self._result(provenance=provenance)
        except VisionConsentError:
            return self._failed("consent", provenance)
        except VisionCaptureError:
            return self._failed("capture", provenance)
        except VisionPrivacyError:
            return self._failed("privacy", provenance)
        except VisionTimeoutError:
            return self._failed("timeout", provenance)
        except asyncio.CancelledError:
            self._interrupt_event.set()
            self._record(VisionSessionState.INTERRUPTED)
            raise
        except Exception:
            return self._failed("unexpected", provenance)
        finally:
            if image is not None:
                image.clear()
            if encoded is not None:
                encoded[:] = b"\x00" * len(encoded)
                encoded.clear()
            self._active = False
            self._interrupt_event = None

    def _record(self, state: VisionSessionState, detail: str | None = None) -> None:
        self._state = state
        self._events.append(VisionSessionEvent(self._session_id, state, detail=detail))

    def _failed(self, code: str, provenance=None) -> VisionSessionResult:
        self._record(VisionSessionState.FAILED, code)
        return self._result(provenance=provenance, error_code=code)

    def _result(
        self,
        *,
        response_text: str | None = None,
        provenance=None,
        error_code: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> VisionSessionResult:
        return VisionSessionResult(
            self._session_id,
            self._state,
            response_text=response_text,
            provenance=provenance,
            error_code=error_code,
            metadata=dict(metadata or {}),
        )

    async def _await(self, operation):
        operation_task = asyncio.create_task(operation)
        interrupt_task = asyncio.create_task(self._interrupt_event.wait())
        try:
            done, _ = await asyncio.wait(
                {operation_task, interrupt_task},
                timeout=self._timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if interrupt_task in done and self._interrupt_event.is_set():
                operation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await operation_task
                raise VisionInterrupted("Vision operation interrupted.")
            if operation_task in done:
                return operation_task.result()
            operation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation_task
            raise VisionTimeoutError("Vision operation timed out.")
        finally:
            if not operation_task.done():
                operation_task.cancel()
            interrupt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await interrupt_task
