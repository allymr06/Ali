"""Voiced narration of a lecture — "sesli anlatım" — with questions in between.

A narration script is built once per document: the model turns the pages
into short spoken segments that say what the material says (without a
model the pages are read as they stand, and the script says so). The
player speaks one segment at a time through the same audio path the
voice session uses, and the student can interrupt at any moment — with a
typed question, with the microphone, or at the checkpoints where JARVIS
itself asks whether anything was unclear. An answer is grounded in the
segment that was being narrated and the pages around it; then the
narration continues where it stopped, not from the beginning.

Nothing here decides anatomy: the script repeats the lecture, the answer
cites the lecture, and when the model is missing the player says so.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.time import utc_now
from app.medical.models import DocumentPage, DocumentStatus, StudyDocument
from app.medical.text import clean_lines

NARRATION_META_PREFIX = "narration:"
PAGES_PER_CALL = 6
CHARS_PER_CALL = 9_000
PAGE_CHARS = 2_400
SEGMENT_MAX_CHARS = 1_800
MATERIAL_SEGMENT_CHARS = 900
# Spoken at a time; an interruption loses at most this much of a segment.
CHUNK_CHARS = 220
CHECKPOINT_PROMPT = "Buraya kadar sorun var mı? Varsa şimdi sor; yoksa devam ediyorum."
CONTINUE_WORDS = frozenset({"yok", "hayır", "hayir", "devam", "devam et", "sorum yok", "yok devam", "geç", "gec", "tamam", "hayır yok", "yok hayır"})
NO_MODEL_ANSWER = "Şu an yanıt üretecek model bağlı değil; anlatıma devam ediyorum. Sorunu sonra yazılı olarak da sorabilirsin."
ANSWER_FAILED = "Sorunu şu an yanıtlayamadım; anlatıma devam ediyorum."
LOG_LIMIT = 12

NARRATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": 40,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 160},
                    "text": {"type": "string", "maxLength": SEGMENT_MAX_CHARS},
                    "page_from": {"type": "integer", "minimum": 1},
                    "page_to": {"type": "integer", "minimum": 1},
                },
                "required": ["title", "text", "page_from", "page_to"],
            },
        }
    },
    "required": ["segments"],
}

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class NarrationError(RuntimeError):
    """The narration could not be built or started."""


# ---------------------------------------------------------------------------
# script
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NarrationSegment:
    index: int
    title: str
    text: str
    page_from: int
    page_to: int
    source: str = "model"

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "title": self.title, "text": self.text, "page_from": self.page_from, "page_to": self.page_to, "source": self.source}


@dataclass(slots=True)
class NarrationScript:
    document_id: str
    title: str
    segments: list[NarrationSegment]
    generated_by: str
    created_at: str
    page_count: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "segments": [segment.to_dict() for segment in self.segments],
            "generated_by": self.generated_by,
            "created_at": self.created_at,
            "page_count": self.page_count,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NarrationScript":
        segments = [
            NarrationSegment(
                index=int(item.get("index", position)),
                title=str(item.get("title", "")),
                text=str(item.get("text", "")),
                page_from=int(item.get("page_from", 0) or 0),
                page_to=int(item.get("page_to", 0) or 0),
                source=str(item.get("source", "model")),
            )
            for position, item in enumerate(data.get("segments", []))
            if isinstance(item, dict)
        ]
        return cls(
            document_id=str(data.get("document_id", "")),
            title=str(data.get("title", "")),
            segments=segments,
            generated_by=str(data.get("generated_by", "model")),
            created_at=str(data.get("created_at", "")),
            page_count=int(data.get("page_count", 0) or 0),
            notes=[str(item) for item in data.get("notes", [])],
        )


def split_sentences(text: str) -> list[str]:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return []
    return [part.strip() for part in _SENTENCE_END.split(normalized) if part.strip()]


def split_into_chunks(text: str, *, limit: int = CHUNK_CHARS) -> list[str]:
    """Sentence groups short enough that stopping mid-segment loses little."""
    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
        while len(current) > limit * 2:
            cut = current.rfind(" ", 0, limit)
            if cut <= 0:
                break
            chunks.append(current[:cut])
            current = current[cut + 1 :]
    if current:
        chunks.append(current)
    return chunks


def page_narration_text(page: DocumentPage) -> str:
    body = " ".join(clean_lines(page.text))
    if page.visual_summary:
        body = (body + " " + page.visual_summary.replace("\n", " ")).strip()
    return body


def material_segments(pages: list[DocumentPage]) -> list[NarrationSegment]:
    """The pages read as they are: what a narration is without a model."""
    segments: list[NarrationSegment] = []
    for page in pages:
        body = page_narration_text(page)
        if len(body) < 30:
            continue
        if len(body) > MATERIAL_SEGMENT_CHARS:
            cut = body.rfind(". ", 0, MATERIAL_SEGMENT_CHARS)
            body = body[: cut + 1] if cut > 200 else body[:MATERIAL_SEGMENT_CHARS]
        title = (page.headings[0] if page.headings else "").strip() or f"Sayfa {page.page_number}"
        segments.append(NarrationSegment(index=len(segments), title=title[:160], text=body, page_from=page.page_number, page_to=page.page_number, source="material"))
    return segments


def is_continue_word(text: str) -> bool:
    folded = " ".join(str(text or "").casefold().replace(".", " ").replace(",", " ").split())
    return not folded or folded in CONTINUE_WORDS


class NarrationBuilder:
    """Builds and caches the script of a document."""

    def __init__(self, store: Any, model: Any) -> None:
        self._store = store
        self._model = model

    def cached(self, document: StudyDocument) -> NarrationScript | None:
        data = self._store.get_meta(NARRATION_META_PREFIX + document.document_id)
        if not isinstance(data, dict):
            return None
        script = NarrationScript.from_dict(data)
        if script.page_count != document.page_count or not script.segments:
            return None
        return script

    def save(self, script: NarrationScript) -> None:
        self._store.set_meta(NARRATION_META_PREFIX + script.document_id, script.to_dict())

    def forget(self, document_id: str) -> bool:
        return bool(self._store.delete_meta(NARRATION_META_PREFIX + document_id))

    async def build(self, document: StudyDocument, *, rebuild: bool = False) -> NarrationScript:
        if document.status != DocumentStatus.READY:
            raise NarrationError("Belge henüz işlenmedi; önce Kütüphane'den işle.")
        if not rebuild:
            cached = self.cached(document)
            if cached is not None:
                return cached
        pages = [page for page in self._store.get_pages(document.document_id) if page_narration_text(page)]
        if not pages:
            raise NarrationError("Belgede anlatılacak metin yok.")
        notes: list[str] = []
        if not self._model.available:
            segments = material_segments(pages)
            notes.append("Model bağlı olmadığı için sayfalar olduğu gibi okunuyor; açıklamalı anlatım için API bağlantısı gerekli.")
            generated_by = "material"
        else:
            segments, model_notes = await self._segments_from_model(document, pages)
            notes.extend(model_notes)
            generated_by = "model" if all(segment.source == "model" for segment in segments) else "mixed"
        if not segments:
            raise NarrationError("Anlatım metni oluşturulamadı.")
        for position, segment in enumerate(segments):
            segment.index = position
        script = NarrationScript(
            document_id=document.document_id,
            title=document.title,
            segments=segments,
            generated_by=generated_by,
            created_at=utc_now().isoformat(),
            page_count=document.page_count,
            notes=notes,
        )
        self.save(script)
        return script

    async def _segments_from_model(self, document: StudyDocument, pages: list[DocumentPage]) -> tuple[list[NarrationSegment], list[str]]:
        from app.medical.model import MedicalModelError
        from app.medical.prompts import PIPELINE_SYSTEM, narration_prompt

        segments: list[NarrationSegment] = []
        notes: list[str] = []
        failed_batches = 0
        for batch in self._batches(pages):
            texts = [(page.page_number, page_narration_text(page)[:PAGE_CHARS]) for page in batch]
            allowed = {page.page_number for page in batch}
            low, high = min(allowed), max(allowed)
            previous = [segment.title for segment in segments[-4:]]
            try:
                data = await self._model.structured(
                    "narration",
                    narration_prompt(document.title, document.subject, texts, previous),
                    NARRATION_SCHEMA,
                    system_prompt=PIPELINE_SYSTEM,
                    task_type="complex",
                )
            except MedicalModelError:
                failed_batches += 1
                segments.extend(material_segments(batch))
                continue
            produced = 0
            for item in data.get("segments", []):
                text = " ".join(str(item.get("text", "")).split())
                title = " ".join(str(item.get("title", "")).split())[:160]
                if len(text) < 20:
                    continue
                page_from = int(item.get("page_from") or low)
                page_to = int(item.get("page_to") or page_from)
                # A page the batch did not contain is not one the model read.
                page_from = min(max(page_from, low), high)
                page_to = min(max(page_to, page_from), high)
                segments.append(NarrationSegment(index=len(segments), title=title or f"Sayfa {page_from}", text=text[:SEGMENT_MAX_CHARS], page_from=page_from, page_to=page_to, source="model"))
                produced += 1
            if not produced:
                failed_batches += 1
                segments.extend(material_segments(batch))
        if failed_batches:
            notes.append(f"{failed_batches} bölüm için model anlatım üretemedi; o sayfalar olduğu gibi okunuyor.")
        return segments, notes

    @staticmethod
    def _batches(pages: list[DocumentPage]) -> list[list[DocumentPage]]:
        batches: list[list[DocumentPage]] = []
        current: list[DocumentPage] = []
        size = 0
        for page in pages:
            length = min(len(page_narration_text(page)), PAGE_CHARS)
            if current and (len(current) >= PAGES_PER_CALL or size + length > CHARS_PER_CALL):
                batches.append(current)
                current, size = [], 0
            current.append(page)
            size += length
        if current:
            batches.append(current)
        return batches


# ---------------------------------------------------------------------------
# speaking and listening
# ---------------------------------------------------------------------------


class Speaker(Protocol):
    async def speak(self, text: str, *, cancel_event: asyncio.Event) -> None: ...


class Listener(Protocol):
    async def listen(self, seconds: float, *, cancel_event: asyncio.Event) -> str | None: ...


Answerer = Callable[[str, NarrationSegment], Awaitable[str]]


class NarrationSpeaker:
    """Speaks through the voice stack.

    The local Windows voice carries a narration by default: it has no daily
    quota and never changes voice mid-lecture. The cloud voice is used only
    when asked for, and the first time it fails (quota, network) the rest of
    the narration continues locally with a note saying why.
    """

    def __init__(self, audio_output: Any, *, synthesizer: Any | None = None, prefer_cloud: bool = False, local_synthesize: Callable[[str], Awaitable[bytes | None]] | None = None) -> None:
        self._output = audio_output
        self._synthesizer = synthesizer if prefer_cloud else None
        self._local = local_synthesize
        self.source = "cloud" if self._synthesizer is not None else "local"
        self.notes: list[str] = []

    async def speak(self, text: str, *, cancel_event: asyncio.Event) -> None:
        from app.voice.errors import VoiceConfigurationError, VoiceProviderError
        from app.voice.models import AudioEncoding, SynthesizedSpeech

        speech = None
        if self._synthesizer is not None:
            try:
                speech = await self._synthesizer.synthesize(text)
            except (VoiceProviderError, VoiceConfigurationError) as exc:
                self._synthesizer = None
                self.source = "local"
                self.notes.append("Bulut ses kotası doldu; anlatım yerel sesle sürüyor." if getattr(exc, "quota", False) else "Bulut sesi kullanılamadı; anlatım yerel sesle sürüyor.")
        if speech is None:
            local = self._local
            if local is None:
                from app.voice.audio import synthesize_local_turkish

                local = synthesize_local_turkish
            data = await local(text)
            if not data:
                raise VoiceConfigurationError("Yerel Türkçe ses bulunamadı.")
            from app.voice.audio import pick_local_voice

            speech = SynthesizedSpeech(data, AudioEncoding.WAV, "windows-local", "winrt-speech", pick_local_voice(text)[0])
        await self._output.play(speech, cancel_event=cancel_event)


class NarrationListener:
    """One short microphone capture, transcribed; silence is None."""

    def __init__(self, audio_input: Any, recognizer: Any, *, language: str = "tr") -> None:
        self._input = audio_input
        self._recognizer = recognizer
        self._language = language

    async def listen(self, seconds: float, *, cancel_event: asyncio.Event) -> str | None:
        from app.voice.errors import VoiceError

        try:
            capture = await self._input.capture(max_duration_seconds=float(seconds), cancel_event=cancel_event)
            result = await self._recognizer.transcribe(capture, language=self._language)
        except VoiceError:
            return None
        except (ValueError, TypeError):
            return None
        text = str(getattr(result, "text", "") or "").strip()
        return text or None


# ---------------------------------------------------------------------------
# player
# ---------------------------------------------------------------------------


class NarrationPlayer:
    """Speaks a script chunk by chunk; every command lands between chunks.

    Commands arrive from the bridge thread and are applied on the player's
    loop, so the state machine is single-threaded: a command sets a flag
    and interrupts the chunk being spoken, and the loop reads the flags
    before it speaks the next one.
    """

    def __init__(
        self,
        *,
        speaker: Speaker,
        answerer: Answerer,
        emit: Callable[[dict[str, Any]], None],
        listener: Listener | None = None,
        checkpoint_every: int = 3,
        listen_seconds: float = 7.0,
        voice_label: str = "local",
    ) -> None:
        self._speaker = speaker
        self._answerer = answerer
        self._listener = listener
        self._emit = emit
        self._checkpoint_every = max(0, int(checkpoint_every))
        self._listen_seconds = float(listen_seconds)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._interrupt = asyncio.Event()
        self._resume = asyncio.Event()
        self._paused = False
        self._stopped = False
        self._jump = 0
        self._question: str | None = None
        self._listen_now = False
        self._checkpoints = True
        self.active = False
        self._state: dict[str, Any] = {"active": False, "status": "idle", "voice": voice_label, "log": []}

    # ---- state

    def state(self) -> dict[str, Any]:
        return {**self._state, "log": list(self._state.get("log", []))}

    def _set(self, **changes: Any) -> None:
        self._state.update(changes)
        self._state["active"] = self.active
        speaker_notes = getattr(self._speaker, "notes", None)
        if speaker_notes:
            self._state["voice_notes"] = list(speaker_notes)
            self._state["voice"] = getattr(self._speaker, "source", self._state.get("voice"))
        self._emit({"kind": "narration_state", **self.state()})

    # ---- commands (any thread)

    def command(self, name: str, payload: Any = None) -> bool:
        loop = self._loop
        if not self.active or loop is None:
            return False
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._apply(name, payload)
        else:
            loop.call_soon_threadsafe(self._apply, name, payload)
        return True

    def _apply(self, name: str, payload: Any) -> None:
        if name == "pause":
            self._paused = True
            self._resume.clear()
            self._interrupt.set()
        elif name == "resume":
            self._paused = False
            self._resume.set()
        elif name in {"next", "prev"}:
            self._jump = 1 if name == "next" else -1
            self._paused = False
            self._resume.set()
            self._interrupt.set()
        elif name == "ask":
            text = " ".join(str(payload or "").split())
            if text:
                self._question = text
                self._paused = False
                self._resume.set()
                self._interrupt.set()
        elif name == "listen":
            self._listen_now = True
            self._paused = False
            self._resume.set()
            self._interrupt.set()
        elif name == "checkpoints":
            self._checkpoints = bool(payload)
            self._state["checkpoints"] = self._checkpoints
        elif name == "stop":
            self._stopped = True
            self._paused = False
            self._resume.set()
            self._interrupt.set()

    # ---- playing

    async def play(self, script: NarrationScript, *, from_segment: int = 0, checkpoints: bool = True) -> dict[str, Any]:
        from app.voice.errors import VoiceInterrupted

        self._loop = asyncio.get_running_loop()
        self.active = True
        self._stopped = False
        self._paused = False
        self._jump = 0
        self._question = None
        self._listen_now = False
        self._checkpoints = bool(checkpoints) and self._listener is not None
        segments = script.segments
        position = min(max(int(from_segment or 0), 0), max(len(segments) - 1, 0))
        chunk_index = 0
        self._state.update({"document_id": script.document_id, "title": script.title, "segment_total": len(segments), "checkpoints": self._checkpoints, "generated_by": script.generated_by, "notes": list(script.notes), "question": None, "answer": None, "heard": None, "error": None, "log": []})
        try:
            while position < len(segments) and not self._stopped:
                segment = segments[position]
                chunks = split_into_chunks(segment.text)
                self._announce(segment, chunk_index, len(chunks))
                while chunk_index < len(chunks) and not self._stopped:
                    if self._question is not None:
                        await self._answer(segment)
                        if not self._stopped and not self._jump:
                            self._announce(segment, chunk_index, len(chunks))
                        continue
                    if self._listen_now:
                        self._listen_now = False
                        await self._checkpoint(segment, prompt=False)
                        if not self._stopped and not self._jump:
                            self._announce(segment, chunk_index, len(chunks))
                        continue
                    if self._jump:
                        break
                    if self._paused:
                        self._set(status="paused")
                        await self._resume.wait()
                        self._resume.clear()
                        if self._stopped or self._jump or self._question is not None or self._listen_now:
                            continue
                        self._announce(segment, chunk_index, len(chunks))
                    self._interrupt.clear()
                    try:
                        await self._speaker.speak(chunks[chunk_index], cancel_event=self._interrupt)
                    except VoiceInterrupted:
                        continue
                    except Exception as exc:  # the speakers are gone: say so and stop
                        self._stopped = True
                        self._state["error"] = f"Ses çalınamadı ({type(exc).__name__})."
                        break
                    chunk_index += 1
                    self._state["chunk"] = chunk_index
                if self._stopped:
                    break
                if self._jump:
                    position = min(max(position + self._jump, 0), len(segments) - 1) if len(segments) else 0
                    self._jump = 0
                    chunk_index = 0
                    continue
                position += 1
                chunk_index = 0
                if position < len(segments) and self._checkpoints and self._checkpoint_every and position % self._checkpoint_every == 0:
                    await self._checkpoint(segment, prompt=True)
        finally:
            self.active = False
            self._set(status="stopped" if self._stopped else "finished", segment_index=min(position, max(len(segments) - 1, 0)))
        return self.state()

    def _announce(self, segment: NarrationSegment, chunk_index: int, chunk_total: int) -> None:
        self._set(
            status="speaking",
            segment_index=segment.index,
            segment_title=segment.title,
            segment_text=segment.text,
            page_from=segment.page_from,
            page_to=segment.page_to,
            chunk=chunk_index,
            chunk_total=chunk_total,
        )

    async def _speak_quietly(self, text: str) -> None:
        """Speak a prompt or an answer; a command interrupting it is fine."""
        from app.voice.errors import VoiceInterrupted

        self._interrupt.clear()
        try:
            await self._speaker.speak(text, cancel_event=self._interrupt)
        except VoiceInterrupted:
            return
        except Exception as exc:
            self._state["error"] = f"Ses çalınamadı ({type(exc).__name__})."

    async def _answer(self, segment: NarrationSegment) -> None:
        question = self._question or ""
        self._question = None
        self._set(status="answering", question=question, answer=None)
        try:
            answer = " ".join(str(await self._answerer(question, segment)).split()) or ANSWER_FAILED
        except Exception:
            answer = ANSWER_FAILED
        log = list(self._state.get("log", []))
        log.append({"segment_index": segment.index, "segment_title": segment.title, "question": question, "answer": answer, "at": utc_now().isoformat()})
        self._set(status="answering", answer=answer, log=log[-LOG_LIMIT:])
        await self._speak_quietly(answer)

    async def _checkpoint(self, segment: NarrationSegment, *, prompt: bool) -> None:
        if self._listener is None:
            return
        self._set(status="listening", heard=None)
        if prompt:
            await self._speak_quietly(CHECKPOINT_PROMPT)
            if self._stopped or self._jump or self._question is not None:
                return
        self._interrupt.clear()
        try:
            heard = await self._listener.listen(self._listen_seconds, cancel_event=self._interrupt)
        except Exception:
            heard = None
        if self._stopped or self._jump or self._question is not None:
            return
        self._state["heard"] = heard or ""
        if heard and not is_continue_word(heard):
            self._question = heard
            await self._answer(segment)


# ---------------------------------------------------------------------------
# service: what the academy and the shell talk to
# ---------------------------------------------------------------------------


class NarrationService:
    def __init__(self, *, store: Any, model: Any, emit: Callable[[dict[str, Any]], None], checkpoint_every: int = 3, prefer_cloud: bool = False, listen_seconds: float = 7.0) -> None:
        self._store = store
        self._model = model
        self._emit = emit
        self._checkpoint_every = int(checkpoint_every)
        self._prefer_cloud = bool(prefer_cloud)
        self._listen_seconds = float(listen_seconds)
        self.builder = NarrationBuilder(store, model)
        self._player: NarrationPlayer | None = None
        self._last_state: dict[str, Any] = {"active": False, "status": "idle"}

    @property
    def active(self) -> bool:
        player = self._player
        return player is not None and player.active

    def state(self) -> dict[str, Any]:
        player = self._player
        if player is not None and player.active:
            return player.state()
        return {**self._last_state, "active": False}

    def command(self, name: str, payload: Any = None) -> dict[str, Any]:
        player = self._player
        if player is None or not player.active:
            raise NarrationError("Açık bir anlatım yok.")
        if name not in {"pause", "resume", "next", "prev", "ask", "listen", "stop", "checkpoints"}:
            raise NarrationError(f"Bilinmeyen anlatım komutu: {name}")
        if name == "ask" and not " ".join(str(payload or "").split()):
            raise NarrationError("Soru boş.")
        if name == "listen" and player._listener is None:
            raise NarrationError("Mikrofonla soru için sesli iletişim ayarlanmış olmalı.")
        player.command(name, payload)
        return player.state()

    async def script(self, document_id: str, *, rebuild: bool = False) -> dict[str, Any]:
        document = self._store.get_document(document_id)
        if document is None:
            raise NarrationError("Belge bulunamadı.")
        script = await self.builder.build(document, rebuild=rebuild)
        return script.to_dict()

    def cached_script(self, document_id: str) -> dict[str, Any] | None:
        document = self._store.get_document(document_id)
        if document is None:
            return None
        script = self.builder.cached(document)
        return script.to_dict() if script is not None else None

    async def play(self, document_id: str, *, voice: Any | None = None, from_segment: int = 0, checkpoints: bool = True, prefer_cloud: bool | None = None, speaker: Speaker | None = None, listener: Listener | None = None) -> dict[str, Any]:
        """Build (or load) the script and narrate it until it ends or is stopped."""
        if self.active:
            raise NarrationError("Zaten açık bir anlatım var; önce onu durdur.")
        document = self._store.get_document(document_id)
        if document is None:
            raise NarrationError("Belge bulunamadı.")
        if document.status != DocumentStatus.READY:
            raise NarrationError("Belge henüz işlenmedi; önce Kütüphane'den işle.")
        self._emit({"kind": "narration_state", "active": True, "status": "preparing", "document_id": document_id, "title": document.title})
        script = await self.builder.build(document)
        cloud = self._prefer_cloud if prefer_cloud is None else bool(prefer_cloud)
        if speaker is None:
            speaker = self._speaker_for(voice, prefer_cloud=cloud)
        if listener is None and voice is not None and getattr(voice, "audio_input", None) is not None and getattr(voice, "recognizer", None) is not None:
            listener = NarrationListener(voice.audio_input, voice.recognizer)
        player = NarrationPlayer(
            speaker=speaker,
            answerer=lambda question, segment: self.answer(question, segment, document),
            emit=self._emit,
            listener=listener,
            checkpoint_every=self._checkpoint_every,
            listen_seconds=self._listen_seconds,
            voice_label=getattr(speaker, "source", "local"),
        )
        self._player = player
        try:
            outcome = await player.play(script, from_segment=from_segment, checkpoints=checkpoints)
        finally:
            self._last_state = player.state()
            self._player = None
        return outcome

    def _speaker_for(self, voice: Any | None, *, prefer_cloud: bool) -> Speaker:
        output = getattr(voice, "audio_output", None) if voice is not None else None
        if output is None:
            if sys.platform != "win32":
                raise NarrationError("Sesli anlatım için ses çıkışı gerekli.")
            from app.voice.audio import WindowsWaveAudioOutput

            output = WindowsWaveAudioOutput()
        synthesizer = getattr(voice, "synthesizer", None) if voice is not None else None
        return NarrationSpeaker(output, synthesizer=synthesizer, prefer_cloud=prefer_cloud and synthesizer is not None)

    async def answer(self, question: str, segment: NarrationSegment, document: StudyDocument) -> str:
        """A short spoken answer grounded in the segment and its pages."""
        if not self._model.available:
            return NO_MODEL_ANSWER
        from app.medical.model import MedicalModelError
        from app.medical.prompts import narration_answer_prompt, narration_answer_system

        pages = self._store.get_pages(document.document_id, page_from=max(1, segment.page_from - 1), page_to=max(segment.page_to + 1, segment.page_from))
        evidence = [(page.page_number, page_narration_text(page)[:PAGE_CHARS]) for page in pages if page_narration_text(page)]
        try:
            text = await self._model.text(
                "narration_answer",
                narration_answer_prompt(document.title, segment.title, segment.text, question, evidence),
                system_prompt=narration_answer_system(document.subject),
                task_type="standard",
            )
        except MedicalModelError:
            return ANSWER_FAILED
        return " ".join(str(text or "").split()) or ANSWER_FAILED


__all__ = [
    "CHECKPOINT_PROMPT",
    "NARRATION_SCHEMA",
    "NarrationBuilder",
    "NarrationError",
    "NarrationListener",
    "NarrationPlayer",
    "NarrationScript",
    "NarrationSegment",
    "NarrationService",
    "NarrationSpeaker",
    "is_continue_word",
    "material_segments",
    "split_into_chunks",
]
