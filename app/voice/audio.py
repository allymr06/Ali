from __future__ import annotations

import asyncio
import importlib
import math
import os
import sys
from array import array
from datetime import timedelta
from typing import Any

from app.core.time import utc_now
from app.voice.base import AudioInput, AudioOutput
from app.voice.errors import (
    VoiceConfigurationError,
    VoiceDeviceError,
    VoiceInterrupted,
    VoiceNoSpeech,
)
from app.voice.models import (
    AudioCapture,
    AudioDevice,
    AudioDeviceKind,
    AudioEncoding,
    SynthesizedSpeech,
)


def pcm16_rms(data: bytes) -> int:
    """
    Calculate RMS energy for little-endian signed PCM16.

    This intentionally avoids external VAD dependencies so the
    microphone path remains small and deterministic.
    """
    usable = len(data) - (len(data) % 2)

    if usable < 2:
        return 0

    samples = array("h")
    samples.frombytes(data[:usable])

    if sys.byteorder != "little":
        samples.byteswap()

    if not samples:
        return 0

    squared = sum(
        int(sample) * int(sample)
        for sample in samples
    )

    return int(
        math.sqrt(
            squared / len(samples)
        )
    )


class SoundDeviceAudioInput(AudioInput):
    """
    Optional sounddevice-backed PCM microphone capture.

    Capture ends on whichever happens first:

    - cancellation
    - maximum duration
    - no speech before the start timeout
    - trailing silence after detected speech
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        channels: int = 1,
        device_id: str | None = None,
        chunk_milliseconds: int = 50,
        vad_enabled: bool = True,
        silence_threshold_rms: int = 350,
        min_speech_seconds: float = 0.15,
        trailing_silence_seconds: float = 0.65,
        start_timeout_seconds: float = 5.0,
        module: Any | None = None,
    ) -> None:
        if (
            sample_rate < 8_000
            or sample_rate > 192_000
        ):
            raise ValueError(
                "Voice sample rate must be between "
                "8000 and 192000."
            )

        if channels not in {1, 2}:
            raise ValueError(
                "Voice capture supports one or two channels."
            )

        if (
            chunk_milliseconds < 10
            or chunk_milliseconds > 500
        ):
            raise ValueError(
                "Audio chunks must be between "
                "10 and 500 milliseconds."
            )

        if not 1 <= silence_threshold_rms <= 32767:
            raise ValueError(
                "Silence threshold must be between "
                "1 and 32767."
            )

        if min_speech_seconds <= 0:
            raise ValueError(
                "Minimum speech duration must be positive."
            )

        if trailing_silence_seconds <= 0:
            raise ValueError(
                "Trailing silence duration must be positive."
            )

        if start_timeout_seconds <= 0:
            raise ValueError(
                "Voice start timeout must be positive."
            )

        self.sample_rate = sample_rate
        self.channels = channels
        self.device_id = device_id

        self.chunk_frames = max(
            1,
            sample_rate
            * chunk_milliseconds
            // 1000,
        )

        self.vad_enabled = vad_enabled
        self.silence_threshold_rms = (
            silence_threshold_rms
        )
        self.min_speech_seconds = (
            min_speech_seconds
        )
        self.trailing_silence_seconds = (
            trailing_silence_seconds
        )
        self.start_timeout_seconds = (
            start_timeout_seconds
        )

        self._module = module

    def _sounddevice(self):
        if self._module is not None:
            return self._module

        try:
            return importlib.import_module(
                "sounddevice"
            )
        except ImportError as exc:
            raise VoiceConfigurationError(
                "Microphone capture requires the "
                "optional sounddevice package."
            ) from exc

    def list_devices(
        self,
    ) -> tuple[AudioDevice, ...]:
        sounddevice = self._sounddevice()

        try:
            raw_devices = (
                sounddevice.query_devices()
            )
            default_input = (
                sounddevice.default.device[0]
            )
        except Exception as exc:
            raise VoiceDeviceError(
                "Audio input devices could not "
                "be enumerated."
            ) from exc

        devices = []

        for index, item in enumerate(
            raw_devices
        ):
            channels = int(
                item.get(
                    "max_input_channels",
                    0,
                )
            )

            if channels < 1:
                continue

            devices.append(
                AudioDevice(
                    device_id=str(index),
                    name=str(
                        item.get(
                            "name",
                            f"Input {index}",
                        )
                    ),
                    kind=(
                        AudioDeviceKind.INPUT
                    ),
                    is_default=(
                        index == default_input
                    ),
                    channels=channels,
                    sample_rate=int(
                        item.get(
                            "default_samplerate",
                            self.sample_rate,
                        )
                    ),
                )
            )

        return tuple(devices)

    async def capture(
        self,
        *,
        max_duration_seconds: float,
        cancel_event=None,
    ) -> AudioCapture:
        if (
            max_duration_seconds <= 0
            or max_duration_seconds > 300
        ):
            raise ValueError(
                "Capture duration must be "
                "between 0 and 300 seconds."
            )

        sounddevice = self._sounddevice()

        maximum_frames = int(
            self.sample_rate
            * max_duration_seconds
        )

        start_timeout_frames = min(
            maximum_frames,
            max(
                1,
                int(
                    self.sample_rate
                    * self.start_timeout_seconds
                ),
            ),
        )

        minimum_speech_frames = max(
            1,
            int(
                self.sample_rate
                * self.min_speech_seconds
            ),
        )

        trailing_silence_frames = max(
            1,
            int(
                self.sample_rate
                * self.trailing_silence_seconds
            ),
        )

        captured = bytearray()
        started_at = utc_now()
        stream = None

        speech_started = False
        voiced_frames = 0
        silence_frames = 0
        frames_read = 0

        try:
            stream = (
                sounddevice.RawInputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="int16",
                    device=(
                        int(self.device_id)
                        if self.device_id
                        is not None
                        else None
                    ),
                    blocksize=(
                        self.chunk_frames
                    ),
                )
            )

            await asyncio.to_thread(
                stream.start
            )

            while (
                frames_read
                < maximum_frames
            ):
                if (
                    cancel_event is not None
                    and cancel_event.is_set()
                ):
                    raise VoiceInterrupted(
                        "Microphone capture "
                        "interrupted."
                    )

                frames = min(
                    self.chunk_frames,
                    maximum_frames
                    - frames_read,
                )

                data, overflowed = (
                    await asyncio.to_thread(
                        stream.read,
                        frames,
                    )
                )

                if overflowed:
                    raise VoiceDeviceError(
                        "Microphone input "
                        "overflowed."
                    )

                chunk = bytes(data)

                captured.extend(chunk)

                frames_read += frames

                if not self.vad_enabled:
                    continue

                energy = pcm16_rms(
                    chunk
                )

                if (
                    energy
                    >= self.silence_threshold_rms
                ):
                    speech_started = True
                    voiced_frames += frames
                    silence_frames = 0

                elif speech_started:
                    silence_frames += frames

                elif (
                    frames_read
                    >= start_timeout_frames
                ):
                    raise VoiceNoSpeech(
                        "No speech detected "
                        "before the listening "
                        "timeout."
                    )

                if (
                    speech_started
                    and voiced_frames
                    >= minimum_speech_frames
                    and silence_frames
                    >= trailing_silence_frames
                ):
                    break

        except VoiceInterrupted:
            raise

        except VoiceNoSpeech:
            raise

        except VoiceDeviceError:
            raise

        except Exception as exc:
            raise VoiceDeviceError(
                "Microphone capture failed."
            ) from exc

        finally:
            if stream is not None:
                try:
                    await asyncio.to_thread(
                        stream.stop
                    )
                except Exception:
                    pass

                try:
                    await asyncio.to_thread(
                        stream.close
                    )
                except Exception:
                    pass

        if not captured:
            raise VoiceDeviceError(
                "Microphone produced no audio."
            )

        finished_at = utc_now()

        expected_minimum = (
            started_at
            + timedelta(0)
        )

        if (
            finished_at
            < expected_minimum
        ):
            finished_at = (
                expected_minimum
            )

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
    """
    Built-in Windows WAV playback
    with cooperative stop support.
    """

    def __init__(
        self,
        *,
        module: Any | None = None,
    ) -> None:
        if (
            os.name != "nt"
            and module is None
        ):
            raise OSError(
                "Windows audio output "
                "requires Windows."
            )

        self._module = module

    def _winsound(self):
        return (
            self._module
            or importlib.import_module(
                "winsound"
            )
        )

    def list_devices(
        self,
    ) -> tuple[AudioDevice, ...]:
        return (
            AudioDevice(
                device_id=(
                    "windows-default-output"
                ),
                name=(
                    "Windows default "
                    "audio output"
                ),
                kind=(
                    AudioDeviceKind.OUTPUT
                ),
                is_default=True,
            ),
        )

    async def play(
        self,
        speech: SynthesizedSpeech,
        *,
        cancel_event=None,
    ) -> None:
        if (
            speech.encoding
            is not AudioEncoding.WAV
        ):
            raise VoiceConfigurationError(
                "Windows playback "
                "requires WAV speech."
            )

        winsound = self._winsound()

        if (
            cancel_event is not None
            and cancel_event.is_set()
        ):
            raise VoiceInterrupted(
                "Speech playback "
                "interrupted."
            )

        # Synchronous playback is winsound's default; the Win32
        # SND_SYNC flag is 0 and the module exposes no constant for it.
        playback = asyncio.create_task(
            asyncio.to_thread(
                winsound.PlaySound,
                speech.data,
                winsound.SND_MEMORY,
            )
        )

        interruption = (
            asyncio.create_task(
                cancel_event.wait()
            )
            if cancel_event is not None
            else None
        )

        try:
            waiters = {playback}

            if (
                interruption
                is not None
            ):
                waiters.add(
                    interruption
                )

            done, _ = (
                await asyncio.wait(
                    waiters,
                    return_when=(
                        asyncio.FIRST_COMPLETED
                    ),
                )
            )

            if playback in done:
                playback.result()
                return

            await self.stop()

            raise VoiceInterrupted(
                "Speech playback "
                "interrupted."
            )

        except VoiceInterrupted:
            raise

        except Exception as exc:
            raise VoiceDeviceError(
                "Speech playback failed."
            ) from exc

        finally:
            if (
                interruption
                is not None
            ):
                interruption.cancel()

            if not playback.done():
                playback.cancel()

    async def stop(self) -> None:
        winsound = self._winsound()

        await asyncio.to_thread(
            winsound.PlaySound,
            None,
            winsound.SND_PURGE,
        )


def _render_earcon_wav(
    *,
    start_hz: float,
    end_hz: float,
    duration_ms: int,
    volume: float = 0.32,
    sample_rate: int = 24_000,
) -> bytes:
    """Render a short HUD-style chirp as WAV bytes.

    A frequency sweep with a soft second harmonic and raised-cosine
    fade reads as a clean sci-fi interface blip without any audio
    assets shipping with the application.
    """
    import io
    import struct
    import wave

    frame_count = int(sample_rate * duration_ms / 1000)
    samples = array("h")
    for index in range(frame_count):
        progress = index / max(1, frame_count - 1)
        frequency = start_hz + (end_hz - start_hz) * progress
        phase = 2 * math.pi * frequency * index / sample_rate
        envelope = 0.5 * (1 - math.cos(2 * math.pi * min(progress, 1.0)))
        value = (
            math.sin(phase)
            + 0.35 * math.sin(2 * phase + math.pi / 4)
        ) * envelope * volume
        samples.append(int(max(-1.0, min(1.0, value)) * 32767))

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


class VoiceEarcons:
    """Distinct open/close interface sounds for voice mode.

    Playback is asynchronous and fire-and-forget so the earcon never
    delays the voice pipeline it decorates.
    """

    def __init__(self, module: Any | None = None) -> None:
        self._module = module
        self._open_wav = _render_earcon_wav(
            start_hz=520.0, end_hz=1040.0, duration_ms=170
        )
        self._close_wav = _render_earcon_wav(
            start_hz=880.0, end_hz=392.0, duration_ms=210
        )

    def _winsound(self):
        return self._module or importlib.import_module("winsound")

    def _play(self, data: bytes) -> None:
        try:
            winsound = self._winsound()
            winsound.PlaySound(
                data,
                winsound.SND_MEMORY | winsound.SND_ASYNC,
            )
        except (ImportError, RuntimeError, OSError):
            # A missing audio device must never break voice mode.
            pass

    def play_open(self) -> None:
        self._play(self._open_wav)

    def play_close(self) -> None:
        self._play(self._close_wav)


async def speak_text_with_windows_sapi(
    text: str,
    *,
    timeout_seconds: float = 45.0,
) -> bool:
    """Best-effort local speech through the built-in Windows SAPI voice.

    Used only when cloud synthesis is unavailable (for example an
    exhausted provider quota) so JARVIS always answers audibly. A
    Turkish voice is selected when one is installed. Returns True only
    when the voice actually spoke.
    """
    if sys.platform != "win32":
        return False
    normalized = text.strip()
    if not normalized:
        return False

    import base64 as _base64
    import subprocess

    encoded = _base64.b64encode(
        normalized.encode("utf-8")
    ).decode("ascii")
    script = (
        "$t=[Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{encoded}'));"
        "$v=New-Object -ComObject SAPI.SpVoice;"
        "$tr=@($v.GetVoices() | Where-Object {"
        " $_.GetAttribute('Language') -eq '41F' }) |"
        " Select-Object -First 1;"
        "if($tr){$v.Voice=$tr};"
        "[void]$v.Speak($t)"
    )

    def run() -> bool:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            timeout=timeout_seconds,
            creationflags=getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            ),
        )
        return completed.returncode == 0

    try:
        return await asyncio.to_thread(run)
    except Exception:
        return False
