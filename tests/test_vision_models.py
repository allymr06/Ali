from __future__ import annotations

import struct
import zlib

import pytest

from app.vision.errors import VisionPrivacyError
from app.vision.models import PixelImage, RedactionRegion, ScreenBounds
from app.vision.privacy import ImageRedactor


def test_pixel_image_encodes_valid_rgb_png_and_clears() -> None:
    image = PixelImage(2, 1, bytearray([255, 0, 0, 0, 255, 0]))

    encoded = image.to_png()

    assert encoded.startswith(b"\x89PNG\r\n\x1a\n")
    ihdr_length = struct.unpack(">I", encoded[8:12])[0]
    assert ihdr_length == 13
    assert struct.unpack(">II", encoded[16:24]) == (2, 1)
    idat = encoded.find(b"IDAT")
    length = struct.unpack(">I", encoded[idat - 4 : idat])[0]
    assert zlib.decompress(encoded[idat + 4 : idat + 4 + length]) == (
        b"\x00\xff\x00\x00\x00\xff\x00"
    )
    assert len(image.sha256) == 64
    image.clear()
    assert image.pixels == bytearray()


def test_image_and_region_models_reject_invalid_dimensions() -> None:
    with pytest.raises(ValueError, match="byte length"):
        PixelImage(2, 2, bytearray(3))
    with pytest.raises(ValueError, match="positive dimensions"):
        ScreenBounds(0, 0, 0, 1)
    with pytest.raises(ValueError, match="positive and in-frame"):
        RedactionRegion(-1, 0, 1, 1)


def test_redactor_blackens_only_requested_pixels() -> None:
    image = PixelImage(3, 2, bytearray([255] * 18))

    transformations = ImageRedactor().apply(
        image, (RedactionRegion(1, 0, 1, 2, "password"),)
    )

    assert transformations == ("redacted:password",)
    assert image.pixels == bytearray(
        [255, 255, 255, 0, 0, 0, 255, 255, 255] * 2
    )


def test_redactor_fails_closed_outside_image() -> None:
    image = PixelImage(2, 2, bytearray([255] * 12))
    with pytest.raises(VisionPrivacyError, match="exceeds"):
        ImageRedactor().apply(image, (RedactionRegion(1, 1, 2, 1),))
