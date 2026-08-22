from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from app.core.time import utc_now


class VisionSourceKind(str, Enum):
    VIRTUAL_SCREEN = "virtual_screen"
    DISPLAY = "display"
    WINDOW = "window"
    IMAGE = "image"


class VisionDetail(str, Enum):
    LOW = "low"
    HIGH = "high"
    ORIGINAL = "original"
    AUTO = "auto"


class VisionSessionState(str, Enum):
    IDLE = "idle"
    AWAITING_CONSENT = "awaiting_consent"
    CAPTURING = "capturing"
    REDACTING = "redacting"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    DENIED = "denied"
    STALE = "stale"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ScreenBounds:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("Screen bounds must have positive dimensions.")


@dataclass(frozen=True, slots=True)
class RedactionRegion:
    x: int
    y: int
    width: int
    height: int
    label: str = "sensitive"

    def __post_init__(self) -> None:
        if min(self.x, self.y) < 0 or self.width < 1 or self.height < 1:
            raise ValueError("Redaction regions must be positive and in-frame.")
        if not self.label.strip():
            raise ValueError("Redaction label cannot be empty.")

    def canonical(self) -> tuple[int, int, int, int, str]:
        return self.x, self.y, self.width, self.height, self.label.strip()


@dataclass(slots=True)
class PixelImage:
    width: int
    height: int
    pixels: bytearray
    captured_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("Image dimensions must be positive.")
        if isinstance(self.pixels, bytes):
            self.pixels = bytearray(self.pixels)
        if not isinstance(self.pixels, bytearray):
            raise TypeError("Image pixels must be mutable bytes.")
        if len(self.pixels) != self.width * self.height * 3:
            raise ValueError("RGB image byte length does not match its dimensions.")
        if self.captured_at.tzinfo is None:
            raise ValueError("Image capture time must be timezone-aware.")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pixels).hexdigest()

    def clear(self) -> None:
        self.pixels[:] = b"\x00" * len(self.pixels)
        self.pixels.clear()

    def to_png(self) -> bytearray:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        stride = self.width * 3
        raw = b"".join(
            b"\x00" + bytes(self.pixels[offset : offset + stride])
            for offset in range(0, len(self.pixels), stride)
        )
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return bytearray(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, level=6))
            + chunk(b"IEND", b"")
        )


@dataclass(frozen=True, slots=True)
class ImageProvenance:
    frame_id: UUID
    source_kind: VisionSourceKind
    source_id: str
    captured_at: datetime
    width: int
    height: int
    original_sha256: str
    processed_sha256: str
    transformations: tuple[str, ...]
    consent_id: UUID


@dataclass(frozen=True, slots=True)
class VisionSessionEvent:
    session_id: UUID
    state: VisionSessionState
    created_at: datetime = field(default_factory=utc_now)
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class VisionSessionResult:
    session_id: UUID
    state: VisionSessionState
    response_text: str | None = None
    provenance: ImageProvenance | None = None
    error_code: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def new_vision_id() -> UUID:
    return uuid4()
