from __future__ import annotations

import asyncio
import importlib
import math
import os
import sys
from array import array
from collections.abc import Callable
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


# RMS of 16-bit PCM that maps to a full-scale level indicator; ordinary
# speech near a microphone sits between roughly 1000 and 8000.
LEVEL_FULL_SCALE_RMS = 6000


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
        # Optional observer for the live input level (0.0-1.0), called
        # once per captured chunk. Purely informational: it never
        # influences voice activity detection.
        self.level_callback: Callable[[float], None] | None = None

        self._module = module

    def _report_level(self, energy: int) -> None:
        callback = self.level_callback
        if callback is None:
            return
        # Square root spreads quiet speech over the visible range.
        level = min(1.0, energy / LEVEL_FULL_SCALE_RMS) ** 0.5
        try:
            callback(level)
        except Exception:
            pass

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

                if (
                    not self.vad_enabled
                    and self.level_callback is None
                ):
                    continue

                energy = pcm16_rms(
                    chunk
                )

                self._report_level(energy)

                if not self.vad_enabled:
                    continue

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


# WinRT synthesis to a WAV file using the Turkish neural voice. WinRT
# exposes "Microsoft Tolga" (tr-TR, male) which the classic SAPI voice
# list does not, so Turkish text stays intelligible offline. Awaiting
# WinRT IAsyncOperation from Windows PowerShell needs the AsTask bridge.
_LOCAL_TTS_SCRIPT = r"""
param([string]$In, [string]$Out, [string]$VoiceName, [string]$LangPrefix)
$ErrorActionPreference = 'Stop'
$text = [System.IO.File]::ReadAllText($In, [System.Text.Encoding]::UTF8)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
[Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
    Where-Object { $_.DisplayName -eq $VoiceName } | Select-Object -First 1
if (-not $voice -and $LangPrefix) {
    $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
        Where-Object { $_.Language -like "$LangPrefix*" -and $_.Gender -eq 'Male' } |
        Select-Object -First 1
    if (-not $voice) {
        $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
            Where-Object { $_.Language -like "$LangPrefix*" } | Select-Object -First 1
    }
}
if (-not $voice) {
    $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
        Where-Object { $_.Language -like 'tr*' } | Select-Object -First 1
}
if ($voice) { $synth.Voice = $voice }
$stream = Await ($synth.SynthesizeTextToStreamAsync($text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
$size = $stream.Size
$reader = New-Object Windows.Storage.Streams.DataReader($stream)
Await ($reader.LoadAsync($size)) ([uint32]) | Out-Null
$bytes = New-Object byte[] $size
$reader.ReadBytes($bytes)
[System.IO.File]::WriteAllBytes($Out, $bytes)
"""


# Model replies in Turkish reliably contain Turkish-specific letters;
# a reply with none of them and none of these everyday words is
# English and deserves an English local voice instead of Tolga
# spelling it out phonetically.
_TURKISH_LETTERS = set("çğıöşüÇĞİÖŞÜ")
_TURKISH_WORDS = frozenset(
    {
        "ve", "bir", "bu", "ben", "sen", "evet", "ne", "gibi",
        "daha", "sonra", "var", "yok", "tamam", "ama", "ile",
        "merhaba", "efendim", "nasıl", "iyi", "olarak", "hazır",
    }
)


def pick_local_voice(text: str) -> tuple[str, str]:
    """Choose (voice display name, language prefix) for local speech."""
    if any(char in _TURKISH_LETTERS for char in text):
        return "Microsoft Tolga", "tr"
    words = [w.strip(".,!?;:'\"()") for w in text.casefold().split()]
    if any(word in _TURKISH_WORDS for word in words if word):
        return "Microsoft Tolga", "tr"
    return "Microsoft David", "en"


async def synthesize_local_turkish(
    text: str,
    *,
    voice_name: str | None = None,
    timeout_seconds: float = 30.0,
) -> bytes | None:
    """Synthesize speech offline to WAV bytes via WinRT.

    Despite the historical name this is the bilingual local fallback:
    the voice follows the text's language unless one is forced.
    Returns WAV bytes on success so the caller can play them through the
    normal (interruptible, verified) audio path, or None when no local
    voice is available.
    """
    if sys.platform != "win32":
        return None
    normalized = text.strip()
    if not normalized:
        return None
    if voice_name is None:
        voice_name, language_prefix = pick_local_voice(normalized)
    else:
        language_prefix = "tr"

    import subprocess
    import tempfile

    def run() -> bytes | None:
        temp_dir = tempfile.mkdtemp(prefix="jarvis_tts_")
        script_path = os.path.join(temp_dir, "synth.ps1")
        in_path = os.path.join(temp_dir, "in.txt")
        out_path = os.path.join(temp_dir, "out.wav")
        try:
            with open(script_path, "w", encoding="utf-8") as handle:
                handle.write(_LOCAL_TTS_SCRIPT)
            with open(in_path, "w", encoding="utf-8") as handle:
                handle.write(normalized)
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    script_path,
                    "-In",
                    in_path,
                    "-Out",
                    out_path,
                    "-VoiceName",
                    voice_name,
                    "-LangPrefix",
                    language_prefix,
                ],
                capture_output=True,
                timeout=timeout_seconds,
                creationflags=getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                ),
            )
            if completed.returncode != 0 or not os.path.exists(
                out_path
            ):
                return None
            with open(out_path, "rb") as handle:
                data = handle.read()
            return data or None
        finally:
            for path in (script_path, in_path, out_path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            try:
                os.rmdir(temp_dir)
            except OSError:
                pass

    try:
        return await asyncio.to_thread(run)
    except Exception:
        return None
