from __future__ import annotations

import asyncio
import ctypes
import os
from ctypes import wintypes

from app.core.time import utc_now
from app.vision.base import ImageSource
from app.vision.errors import VisionCaptureError, VisionInterrupted
from app.vision.models import PixelImage, ScreenBounds, VisionSourceKind


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


class WindowsScreenSource(ImageSource):
    """Native Win32 virtual-screen capture without shell execution or disk I/O."""

    _SRCCOPY = 0x00CC0020
    _CAPTUREBLT = 0x40000000
    _DIB_RGB_COLORS = 0

    def __init__(
        self,
        *,
        max_width: int = 7680,
        max_height: int = 4320,
        max_pixels: int = 20_000_000,
        backend=None,
    ) -> None:
        if min(max_width, max_height, max_pixels) < 1:
            raise ValueError("Screen capture limits must be positive.")
        if os.name != "nt" and backend is None:
            raise OSError("Windows screen capture requires Windows.")
        self._max_width = max_width
        self._max_height = max_height
        self._max_pixels = max_pixels
        self._backend = backend

    @property
    def source_id(self) -> str:
        return "windows-virtual-screen"

    @property
    def kind(self) -> VisionSourceKind:
        return VisionSourceKind.VIRTUAL_SCREEN

    def bounds(self) -> ScreenBounds:
        if self._backend is not None:
            bounds = self._backend.bounds()
        else:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            bounds = ScreenBounds(
                user32.GetSystemMetrics(76),
                user32.GetSystemMetrics(77),
                user32.GetSystemMetrics(78),
                user32.GetSystemMetrics(79),
            )
        self._validate_bounds(bounds)
        return bounds

    async def capture(self, *, cancel_event=None) -> PixelImage:
        if cancel_event is not None and cancel_event.is_set():
            raise VisionInterrupted("Screen capture interrupted.")
        bounds = self.bounds()
        try:
            image = await asyncio.to_thread(self._capture_sync, bounds)
        except VisionCaptureError:
            raise
        except Exception as exc:
            raise VisionCaptureError("Screen capture failed.") from exc
        if cancel_event is not None and cancel_event.is_set():
            image.clear()
            raise VisionInterrupted("Screen capture interrupted.")
        return image

    def _validate_bounds(self, bounds: ScreenBounds) -> None:
        if bounds.width > self._max_width or bounds.height > self._max_height:
            raise VisionCaptureError("Screen dimensions exceed configured limits.")
        if bounds.width * bounds.height > self._max_pixels:
            raise VisionCaptureError("Screen pixel count exceeds the configured limit.")

    def _capture_sync(self, bounds: ScreenBounds) -> PixelImage:
        if self._backend is not None:
            image = self._backend.capture(bounds)
            if not isinstance(image, PixelImage):
                raise VisionCaptureError("Screen backend returned an invalid image.")
            return image
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        handle = ctypes.c_void_p
        user32.GetDC.restype = handle
        user32.GetDC.argtypes = [handle]
        user32.ReleaseDC.argtypes = [handle, handle]
        gdi32.CreateCompatibleDC.restype = handle
        gdi32.CreateCompatibleDC.argtypes = [handle]
        gdi32.CreateCompatibleBitmap.restype = handle
        gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = handle
        gdi32.SelectObject.argtypes = [handle, handle]
        gdi32.DeleteObject.argtypes = [handle]
        gdi32.DeleteDC.argtypes = [handle]
        gdi32.BitBlt.argtypes = [
            handle,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            handle,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        gdi32.GetDIBits.argtypes = [
            handle,
            handle,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(_BitmapInfo),
            wintypes.UINT,
        ]
        screen_dc = user32.GetDC(None)
        memory_dc = bitmap = old_object = None
        if not screen_dc:
            raise VisionCaptureError("Windows screen device context is unavailable.")
        try:
            memory_dc = gdi32.CreateCompatibleDC(screen_dc)
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, bounds.width, bounds.height)
            if not memory_dc or not bitmap:
                raise VisionCaptureError("Windows capture resources are unavailable.")
            old_object = gdi32.SelectObject(memory_dc, bitmap)
            copied = gdi32.BitBlt(
                memory_dc,
                0,
                0,
                bounds.width,
                bounds.height,
                screen_dc,
                bounds.x,
                bounds.y,
                self._SRCCOPY | self._CAPTUREBLT,
            )
            if not copied:
                raise VisionCaptureError("Windows did not provide screen pixels.")
            info = _BitmapInfo()
            info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            info.bmiHeader.biWidth = bounds.width
            info.bmiHeader.biHeight = -bounds.height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            size = bounds.width * bounds.height * 4
            bgra = (ctypes.c_ubyte * size)()
            rows = gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                bounds.height,
                bgra,
                ctypes.byref(info),
                self._DIB_RGB_COLORS,
            )
            if rows != bounds.height:
                raise VisionCaptureError("Windows returned an incomplete screen image.")
            rgb = bytearray(bounds.width * bounds.height * 3)
            for source in range(0, size, 4):
                target = source // 4 * 3
                rgb[target : target + 3] = bytes(
                    (bgra[source + 2], bgra[source + 1], bgra[source])
                )
            return PixelImage(bounds.width, bounds.height, rgb, utc_now())
        finally:
            if old_object and memory_dc:
                gdi32.SelectObject(memory_dc, old_object)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory_dc:
                gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)
