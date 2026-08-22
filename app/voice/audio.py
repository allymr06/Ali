from __future__ import annotations

import asyncio
import importlib
import os
from datetime import timedelta
from typing import Any

from app.core.time import utc_now
from app.voice.base import AudioInput, AudioOutput
from app.voice.errors import VoiceConfigurationError, VoiceDeviceError, VoiceInterrupted
from app.voice.models import (
    AudioCapture,
    AudioDevice,
    AudioDeviceKind,
    AudioEncoding,
    SynthesizedSpeech,
)


class SoundDeviceAudioInput(AudioInput):
    """Optional sounddevice-backed bounded PCM microphone capture."""

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        channels: int = 1,
        device_id: str | None = None,
        chunk_milliseconds: int = 50,
        module: Any | None = None,
    ) -> None:
        if sample_rate < 8_000 or sample_rate > 192_000:
            raise ValueError("Voice sample rate must be between 8000 and 192000.")
        if channels not in {1, 2}:
            raise ValueError("Voice capture supports one or two channels.")
        if chunk_milliseconds < 10 or chunk_milliseconds > 500:
            raise ValueError("Audio chunks must be between 10 and 500 milliseconds.")
        self.sample_rate = sample_rate
        self.channels = channels
        self.device_id = device_id
        self.chunk_frames = max(1, sample_rate * chunk_milliseconds // 1000)
        self._module = module

    def _sounddevice(self):
        if self._module is not None:
            return self._module
        try:
            return importlib.import_module("sounddevice")
        except ImportError as exc:
            raise VoiceConfigurationError(
                "Microphone capture requires the optional sounddevice package."
            ) from exc

    def list_devices(self) -> tuple[AudioDevice, ...]:
        sounddevice = self._sounddevice()
        try:
            raw_devices = sounddevice.query_devices()
            default_input = sounddevice.default.device[0]
        except Exception as exc:
            raise VoiceDeviceError("Audio input devices could not be enumerated.") from exc
        devices = []
        for index, item in enumerate(raw_devices):
            channels = int(item.get("max_input_channels", 0))
            if channels < 1:
                continue
            devices.append(
                AudioDevice(
                    device_id=str(index),
                    name=str(item.get("name", f"Input {index}")),
                    kind=AudioDeviceKind.INPUT,
                    is_default=index == default_input,
                    channels=channels,
                    sample_rate=int(item.get("default_samplerate", self.sample_rate)),
                )
            )
        return tuple(devices)

    async def capture(
        self,
        *,
        max_duration_seconds: float,
        cancel_event=None,
    ) -> AudioCapture:
        if max_duration_seconds <= 0 or max_duration_seconds > 300:
            raise ValueError("Capture duration must be between 0 and 300 seconds.")
        sounddevice = self._sounddevice()
        maximum_frames = int(self.sample_rate * max_duration_seconds)
        captured = bytearray()
        started_at = utc_now()
        stream = None
        try:
            stream = sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                device=(int(self.device_id) if self.device_id is not None else None),
                blocksize=self.chunk_frames,
            )
            await asyncio.to_thread(stream.start)
            frames_read = 0
            while frames_read < maximum_frames:
                if cancel_event is not None and cancel_event.is_set():
                    raise VoiceInterrupted("Microphone capture interrupted.")
                frames = min(self.chunk_frames, maximum_frames - frames_read)
                data, overflowed = await asyncio.to_thread(stream.read, frames)
                if overflowed:
                    raise VoiceDeviceError("Microphone input overflowed.")
                captured.extend(bytes(data))
                frames_read += frames
        except VoiceInterrupted:
            raise
        except VoiceDeviceError:
            raise
        except Exception as exc:
            raise VoiceDeviceError("Microphone capture failed.") from exc
        finally:
            if stream is not None:
                try:
                    await asyncio.to_thread(stream.stop)
                except Exception:
                    pass
                try:
                    await asyncio.to_thread(stream.close)
                except Exception:
                    pass
        if not captured:
            raise VoiceDeviceError("Microphone produced no audio.")
        finished_at = utc_now()
        expected_minimum = started_at + timedelta(0)
        if finished_at < expected_minimum:
            finished_at = expected_minimum
        return AudioCapture(
            data=captured,
            sample_rate=self.sample_rate,
            channels=self.channels,
            encoding=AudioEncoding.PCM16,
            device_id=self.device_id,
            started_at=started_at,
            finished_at=finished_at,
        )


class WindowsWaveAudioOutput(AudioOutput):
    """Built-in Windows WAV playback with cooperative stop support."""

    def __init__(self, *, module: Any | None = None) -> None:
        if os.name != "nt" and module is None:
            raise OSError("Windows audio output requires Windows.")
        self._module = module

    def _winsound(self):
        return self._module or importlib.import_module("winsound")

    def list_devices(self) -> tuple[AudioDevice, ...]:
        return (
            AudioDevice(
                device_id="windows-default-output",
                name="Windows default audio output",
                kind=AudioDeviceKind.OUTPUT,
                is_default=True,
            ),
        )

    async def play(self, speech: SynthesizedSpeech, *, cancel_event=None) -> None:
        if speech.encoding is not AudioEncoding.WAV:
            raise VoiceConfigurationError("Windows playback requires WAV speech.")
        winsound = self._winsound()
        if cancel_event is not None and cancel_event.is_set():
            raise VoiceInterrupted("Speech playback interrupted.")
        playback = asyncio.create_task(
            asyncio.to_thread(
                winsound.PlaySound,
                speech.data,
                winsound.SND_MEMORY | winsound.SND_SYNC,
            )
        )
        interruption = (
            asyncio.create_task(cancel_event.wait())
            if cancel_event is not None
            else None
        )
        try:
            waiters = {playback}
            if interruption is not None:
                waiters.add(interruption)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if playback in done:
                playback.result()
                return
            await self.stop()
            raise VoiceInterrupted("Speech playback interrupted.")
        except VoiceInterrupted:
            raise
        except Exception as exc:
            raise VoiceDeviceError("Speech playback failed.") from exc
        finally:
            if interruption is not None:
                interruption.cancel()
            if not playback.done():
                playback.cancel()

    async def stop(self) -> None:
        winsound = self._winsound()
        await asyncio.to_thread(winsound.PlaySound, None, winsound.SND_PURGE)
