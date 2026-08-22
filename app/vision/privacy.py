from __future__ import annotations

from app.vision.errors import VisionPrivacyError
from app.vision.models import PixelImage, RedactionRegion


class ImageRedactor:
    """Applies irreversible black-box redaction directly to RGB pixels."""

    def apply(
        self,
        image: PixelImage,
        regions: tuple[RedactionRegion, ...],
    ) -> tuple[str, ...]:
        transformations = []
        for region in regions:
            if region.x + region.width > image.width or region.y + region.height > image.height:
                raise VisionPrivacyError("A redaction region exceeds image bounds.")
            black_row = b"\x00" * (region.width * 3)
            for row in range(region.y, region.y + region.height):
                start = (row * image.width + region.x) * 3
                image.pixels[start : start + len(black_row)] = black_row
            transformations.append(f"redacted:{region.label.strip()}")
        return tuple(transformations)
