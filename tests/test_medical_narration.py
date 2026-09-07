"""Sesli anlatım: a lecture read aloud, interrupted by questions, resumed where it stopped.

The script repeats the lecture (or, without a model, reads it as it stands
and says so); the player speaks chunk by chunk so that a command lands
within a sentence or two; a question pauses the reading, is answered from
the segment being narrated, and the reading goes on from the same chunk.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.medical.academy import create_medical_academy
from app.medical.narration import (
    CHECKPOINT_PROMPT,
    NO_MODEL_ANSWER,
    NarrationBuilder,
    NarrationError,
    NarrationPlayer,
    NarrationScript,
    NarrationSegment,
    NarrationService,
    NarrationSpeaker,
    is_continue_word,
    material_segments,
    split_into_chunks,
)
from app.medical.models import DocumentPage, DocumentStatus
from app.voice.errors import VoiceInterrupted, VoiceProviderError
from app.voice.models import AudioEncoding, SynthesizedSpeech

LECTURE = (
    "Scapula omuz kusagindaki yassi ucgen kemiktir. Cavitas glenoidalis humerus basi ile eklem yapar. "
    "Acromion clavicula ile eklemlesir ve omuz catisini olusturur. Spina scapulae arka yuzu iki fossaya ayirir.\n"
)


class Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class Gateway:
    """Replies from a script and records every prompt."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def generate(self, request, context, **kwargs):
        self.calls.append({"prompt": request.text, "metadata": dict(request.metadata), "kwargs": kwargs})
        if not self.replies:
            raise AssertionError("the narration asked for more replies than the test scripted")
        return Reply(self.replies.pop(0))


class FakeSpeaker:
    """Speaks instantly unless gated; records chunks and interruptions."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.gate: asyncio.Event | None = None
        self.notes: list[str] = []
        self.source = "fake"

    async def speak(self, text: str, *, cancel_event: asyncio.Event) -> None:
        self.spoken.append(text)
        if self.gate is not None:
            waiter = asyncio.create_task(cancel_event.wait())
            gate = asyncio.create_task(self.gate.wait())
            done, _ = await asyncio.wait({waiter, gate}, return_when=asyncio.FIRST_COMPLETED)
            for task in (waiter, gate):
                if not task.done():
                    task.cancel()
            if waiter in done:
                raise VoiceInterrupted("interrupted")


class FakeListener:
    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def listen(self, seconds: float, *, cancel_event: asyncio.Event) -> str | None:
        self.calls += 1
        return self.answers.pop(0) if self.answers else None


def script_of(*texts: str) -> NarrationScript:
    return NarrationScript(
        document_id="doc-1",
        title="Scapula",
        segments=[NarrationSegment(index=i, title=f"Bölüm {i + 1}", text=text, page_from=i + 1, page_to=i + 1) for i, text in enumerate(texts)],
        generated_by="model",
        created_at="2026-09-07T00:00:00",
        page_count=len(texts),
    )


async def answerer(question: str, segment: NarrationSegment) -> str:
    return f"Yanıt: {question} → {segment.title}"


def make_player(speaker: FakeSpeaker, events: list, **overrides) -> NarrationPlayer:
    options = {"speaker": speaker, "answerer": answerer, "emit": events.append, "checkpoint_every": 0}
    options.update(overrides)
    return NarrationPlayer(**options)


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------


def test_chunks_follow_sentences_and_never_exceed_twice_the_limit() -> None:
    text = "Birinci cümle. İkinci cümle biraz daha uzun! Üçüncü cümle? Dördüncü."
    chunks = split_into_chunks(text, limit=30)
    assert " ".join(chunks) == text
    assert all(len(chunk) <= 60 for chunk in chunks) and len(chunks) >= 3
    assert split_into_chunks("") == []
    long = "kelime " * 80
    assert all(len(chunk) <= 60 for chunk in split_into_chunks(long, limit=30))


def test_continue_words_and_material_segments() -> None:
    assert is_continue_word("yok") and is_continue_word("Hayır, devam.") is False or is_continue_word("Hayır")
    assert is_continue_word("") and is_continue_word("devam et") and not is_continue_word("sinaps nedir")
    pages = [
        DocumentPage(document_id="d", page_number=1, text="BAŞLIK\n" + LECTURE, headings=["BAŞLIK"]),
        DocumentPage(document_id="d", page_number=2, text="kısa"),
        DocumentPage(document_id="d", page_number=3, text="", visual_summary="Şekil (diagram): scapula arkadan görünüm, spina scapulae etiketli."),
    ]
    segments = material_segments(pages)
    assert [segment.page_from for segment in segments] == [1, 3]
    assert segments[0].title == "BAŞLIK" and segments[0].source == "material"
    assert "Şekil" in segments[1].text and segments[1].title == "Sayfa 3"


# ---------------------------------------------------------------------------
# the script
# ---------------------------------------------------------------------------


@pytest.fixture()
def academy_with(tmp_path):
    built = []

    def factory(gateway=None):
        academy = create_medical_academy(settings=SimpleNamespace(medical_directory=str(tmp_path / f"m{len(built)}"), medical_office_conversion=False), provider_gateway=gateway)
        built.append(academy)
        return academy

    yield factory
    for academy in built:
        academy.close()


def ready_document(academy, text: str = LECTURE * 3):
    document, _ = academy.import_text_document(text, title="Scapula dersi", subject="anatomy")
    processed = academy.pipeline.process(document.document_id)
    assert processed.status == DocumentStatus.READY
    return processed


def test_without_a_model_the_script_reads_the_pages_and_says_so(academy_with) -> None:
    academy = academy_with(None)
    document = ready_document(academy)
    script = asyncio.run(academy.narration.script(document.document_id))
    assert script["generated_by"] == "material" and script["segments"]
    assert all(segment["source"] == "material" for segment in script["segments"])
    assert any("Model bağlı olmadığı" in note for note in script["notes"])
    # Cached under the document; forgotten with it.
    assert academy.narration.cached_script(document.document_id)["created_at"] == script["created_at"]
    assert academy.delete_document(document.document_id) is True
    assert academy.narration.cached_script(document.document_id) is None
    with pytest.raises(NarrationError, match="Belge bulunamadı"):
        asyncio.run(academy.narration.script("nope"))


def test_the_model_script_is_validated_clamped_and_cached(academy_with) -> None:
    reply = json.dumps({"segments": [
        {"title": "Scapula", "text": "Scapula omuz kuşağının yassı üçgen kemiğidir; cavitas glenoidalis humerus başı ile eklem yapar.", "page_from": 1, "page_to": 1},
        {"title": "Boş", "text": "", "page_from": 1, "page_to": 1},
        {"title": "Uydurma sayfa", "text": "Acromion clavicula ile eklemleşir ve omuz çatısını oluşturur.", "page_from": 40, "page_to": 90},
    ]})
    gateway = Gateway(reply)
    academy = academy_with(gateway)
    document = ready_document(academy)
    script = asyncio.run(academy.narration.script(document.document_id))
    assert script["generated_by"] == "model" and len(script["segments"]) == 2
    assert script["segments"][0]["index"] == 0 and script["segments"][1]["index"] == 1
    assert script["segments"][1]["page_from"] == script["segments"][1]["page_to"] == document.page_count, "a page the batch did not contain is clamped to the batch"
    assert gateway.calls[0]["kwargs"]["task_type"] == "complex"
    assert "Page 1" in gateway.calls[0]["prompt"]
    # The second call is served from the cache: no new model call.
    again = asyncio.run(academy.narration.script(document.document_id))
    assert again["created_at"] == script["created_at"] and len(gateway.calls) == 1
    # An unprocessed document has no script.
    pending, _ = academy.import_text_document("x" * 200, title="Bekleyen", subject="anatomy")
    with pytest.raises(NarrationError, match="işlenmedi"):
        asyncio.run(academy.narration.script(pending.document_id))


def test_a_failed_batch_falls_back_to_the_material_and_the_note_says_so(academy_with) -> None:
    gateway = Gateway("not json at all", "still not json")  # one repair round, then failure
    academy = academy_with(gateway)
    document = ready_document(academy)
    script = asyncio.run(academy.narration.script(document.document_id))
    assert script["generated_by"] == "mixed" or script["generated_by"] == "material"
    assert any(segment["source"] == "material" for segment in script["segments"])
    assert any("model anlatım üretemedi" in note for note in script["notes"])


# ---------------------------------------------------------------------------
# the player
# ---------------------------------------------------------------------------


def test_a_narration_speaks_every_chunk_in_order_and_finishes() -> None:
    speaker = FakeSpeaker()
    events: list[dict] = []
    player = make_player(speaker, events)
    script = script_of("Birinci bölüm. Devamı.", "İkinci bölüm.")

    outcome = asyncio.run(player.play(script))

    assert outcome["status"] == "finished" and outcome["active"] is False
    assert speaker.spoken == ["Birinci bölüm. Devamı.", "İkinci bölüm."]
    statuses = [event["status"] for event in events]
    assert statuses[0] == "speaking" and statuses[-1] == "finished"
    assert events[0]["segment_title"] == "Bölüm 1" and events[0]["segment_total"] == 2
    assert all(event["kind"] == "narration_state" for event in events)


def test_pause_resume_next_prev_and_stop_land_between_chunks() -> None:
    async def run() -> tuple[dict, FakeSpeaker, list[dict]]:
        speaker = FakeSpeaker()
        speaker.gate = asyncio.Event()
        events: list[dict] = []
        player = make_player(speaker, events)
        script = script_of("Bir. İki. Üç.", "Dört.", "Beş.")
        task = asyncio.create_task(player.play(script))
        await asyncio.sleep(0.02)
        assert player.command("pause") is True
        await asyncio.sleep(0.02)
        assert events[-1]["status"] == "paused"
        assert player.command("resume") is True
        await asyncio.sleep(0.02)
        assert events[-1]["status"] == "speaking"
        player.command("next")
        await asyncio.sleep(0.02)
        assert events[-1]["segment_index"] == 1
        player.command("prev")
        await asyncio.sleep(0.02)
        assert events[-1]["segment_index"] == 0 and events[-1]["chunk"] == 0
        player.command("stop")
        outcome = await task
        return outcome, speaker, events

    outcome, speaker, events = asyncio.run(run())
    assert outcome["status"] == "stopped"
    # After the stop nothing more is spoken and commands are refused.
    assert not any(event["status"] == "finished" for event in events)
    assert speaker.spoken[0] == "Bir. İki. Üç."


def test_a_question_interrupts_is_answered_and_the_same_chunk_resumes() -> None:
    async def run():
        speaker = FakeSpeaker()
        speaker.gate = asyncio.Event()
        events: list[dict] = []
        player = make_player(speaker, events)
        script = script_of("Bir. İki.", "Üç.")
        task = asyncio.create_task(player.play(script))
        await asyncio.sleep(0.02)
        player.command("ask", "Sinaps nedir?")
        await asyncio.sleep(0.05)
        assert speaker.spoken[-1] == "Yanıt: Sinaps nedir? → Bölüm 1"
        answering = [event for event in events if event["status"] == "answering"]
        assert answering and answering[-1]["question"] == "Sinaps nedir?" and answering[-1]["answer"].startswith("Yanıt")
        assert answering[-1]["log"][-1]["segment_index"] == 0
        speaker.gate.set()
        outcome = await task
        return outcome, speaker

    outcome, speaker = asyncio.run(run())
    assert outcome["status"] == "finished"
    # The interrupted chunk was spoken again after the answer, then the rest.
    assert speaker.spoken == ["Bir. İki.", "Yanıt: Sinaps nedir? → Bölüm 1", "Bir. İki.", "Üç."]
    assert outcome["log"][0]["question"] == "Sinaps nedir?"


def test_checkpoints_ask_listen_and_continue_on_silence_or_answer_a_question() -> None:
    speaker = FakeSpeaker()
    events: list[dict] = []
    listener = FakeListener(None, "Acromion ne işe yarar?")
    player = make_player(speaker, events, listener=listener, checkpoint_every=1)
    script = script_of("Bir.", "İki.", "Üç.")

    outcome = asyncio.run(player.play(script, checkpoints=True))

    assert outcome["status"] == "finished" and listener.calls == 2
    assert speaker.spoken == ["Bir.", CHECKPOINT_PROMPT, "İki.", CHECKPOINT_PROMPT, "Yanıt: Acromion ne işe yarar? → Bölüm 2", "Üç."]
    listening = [event for event in events if event["status"] == "listening"]
    assert len(listening) >= 2
    assert outcome["log"][0]["segment_title"] == "Bölüm 2"

    # Without a listener the checkpoints are silently off.
    quiet = make_player(FakeSpeaker(), [], checkpoint_every=1)
    assert asyncio.run(quiet.play(script, checkpoints=True))["checkpoints"] is False


def test_a_broken_speaker_stops_the_narration_with_a_named_error() -> None:
    class Broken:
        notes: list[str] = []
        source = "local"

        async def speak(self, text, *, cancel_event):
            raise RuntimeError("no device")

    events: list[dict] = []
    player = NarrationPlayer(speaker=Broken(), answerer=answerer, emit=events.append)
    outcome = asyncio.run(player.play(script_of("Bir.")))
    assert outcome["status"] == "stopped" and outcome["error"] == "Ses çalınamadı (RuntimeError)."


# ---------------------------------------------------------------------------
# the speaker and the service
# ---------------------------------------------------------------------------


class Output:
    def __init__(self) -> None:
        self.played: list[SynthesizedSpeech] = []

    async def play(self, speech, *, cancel_event=None):
        self.played.append(speech)


class QuotaSynthesizer:
    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, text):
        self.calls += 1
        error = VoiceProviderError("429 RESOURCE_EXHAUSTED")
        error.quota = True
        raise error


class CloudSynthesizer:
    async def synthesize(self, text):
        return SynthesizedSpeech(b"RIFFcloud", AudioEncoding.WAV, "gemini", "tts", "Kore")


async def local(text: str) -> bytes:
    return b"RIFFlocal" + text.encode("utf-8")


def test_the_speaker_uses_the_local_voice_by_default_and_falls_back_from_a_spent_cloud_quota() -> None:
    output = Output()
    speaker = NarrationSpeaker(output, synthesizer=CloudSynthesizer(), prefer_cloud=False, local_synthesize=local)
    asyncio.run(speaker.speak("Merhaba.", cancel_event=asyncio.Event()))
    assert speaker.source == "local" and output.played[0].provider == "windows-local"

    quota = QuotaSynthesizer()
    output = Output()
    cloud = NarrationSpeaker(output, synthesizer=quota, prefer_cloud=True, local_synthesize=local)
    assert cloud.source == "cloud"
    asyncio.run(cloud.speak("Bir.", cancel_event=asyncio.Event()))
    asyncio.run(cloud.speak("İki.", cancel_event=asyncio.Event()))
    assert cloud.source == "local" and quota.calls == 1, "after the quota error the cloud is not asked again"
    assert [speech.provider for speech in output.played] == ["windows-local", "windows-local"]
    assert cloud.notes == ["Bulut ses kotası doldu; anlatım yerel sesle sürüyor."]

    fine = NarrationSpeaker(Output(), synthesizer=CloudSynthesizer(), prefer_cloud=True, local_synthesize=local)
    asyncio.run(fine.speak("Üç.", cancel_event=asyncio.Event()))
    assert fine.source == "cloud" and fine.notes == []


def test_the_service_plays_a_document_answers_without_a_model_honestly_and_refuses_a_second_narration(academy_with) -> None:
    academy = academy_with(None)
    document = ready_document(academy)
    events: list[dict] = []
    academy.subscribe(events.append)
    speaker = FakeSpeaker()
    service: NarrationService = academy.narration

    async def run():
        speaker.gate = asyncio.Event()
        task = asyncio.create_task(service.play(document.document_id, speaker=speaker, checkpoints=False))
        await asyncio.sleep(0.05)
        assert service.active is True and service.state()["status"] == "speaking"
        with pytest.raises(NarrationError, match="Zaten açık"):
            await service.play(document.document_id, speaker=FakeSpeaker())
        service.command("ask", "Cavitas glenoidalis nedir?")
        await asyncio.sleep(0.05)
        with pytest.raises(NarrationError, match="Soru boş"):
            service.command("ask", "   ")
        with pytest.raises(NarrationError, match="Mikrofonla"):
            service.command("listen")
        with pytest.raises(NarrationError, match="Bilinmeyen"):
            service.command("dance")
        service.command("stop")
        return await task

    outcome = asyncio.run(run())
    assert outcome["status"] == "stopped"
    assert outcome["log"][0]["answer"] == NO_MODEL_ANSWER
    assert service.active is False and service.state()["status"] == "stopped"
    kinds = [event["kind"] for event in events]
    assert "narration_state" in kinds and events[[event["kind"] for event in events].index("narration_state")]["status"] == "preparing"
    with pytest.raises(NarrationError, match="Açık bir anlatım yok"):
        service.command("pause")
    with pytest.raises(NarrationError, match="Belge bulunamadı"):
        asyncio.run(service.play("nope", speaker=FakeSpeaker()))


def test_an_answer_is_grounded_in_the_segment_and_its_pages(academy_with) -> None:
    gateway = Gateway("Cavitas glenoidalis, scapulanın humerus başı ile eklem yapan çukurudur.")
    academy = academy_with(gateway)
    document = ready_document(academy)
    segment = NarrationSegment(index=0, title="Scapula", text="Scapula omuz kuşağının kemiğidir.", page_from=1, page_to=1)
    answer = asyncio.run(academy.narration.answer("Cavitas glenoidalis nedir?", segment, document))
    assert answer.startswith("Cavitas glenoidalis")
    call = gateway.calls[0]
    assert "Cavitas glenoidalis nedir?" in call["prompt"] and "Scapula omuz kuşağının kemiğidir." in call["prompt"]
    assert "Page 1" in call["prompt"] and call["kwargs"]["task_type"] == "standard"
    assert "spoken answer" in call["kwargs"]["system_prompt"]
