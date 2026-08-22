from __future__ import annotations

from abc import ABC, abstractmethod

from app.vision.models import PixelImage, ScreenBounds, VisionSourceKind


class ImageSource(ABC):
    @property
    @abstractmethod
    def source_id(self) -> str: ...

    @property
    @abstractmethod
    def kind(self) -> VisionSourceKind: ...

    @abstractmethod
    def bounds(self) -> ScreenBounds: ...

    @abstractmethod
    async def capture(self, *, cancel_event=None) -> PixelImage: ...
