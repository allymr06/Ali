"""Medical intent parsing and the study session: deterministic and offline.

These are the two halves of the Academy that must work without a
provider: the parser turns one sentence into a structured
``StudyCommand``, and ``SessionManager`` keeps only the constraints that
were actually stated. Everything here runs against the shipped
curriculum, terminology and concept data — no model, no network.
"""

from __future__ import annotations

import pytest

from app.medical.catalog import Curriculum
from app.medical.concepts import default_concept_graph
from app.medical.context import MAX_QUESTIONS, MIN_OPTIONS, SessionManager
from app.medical.intents import MedicalIntent, MedicalIntentParser, StudyCommand, describe_command
from app.medical.models import DepthLevel, KnowledgePriority, KnowledgeSource, StudyMode
from app.medical.store import MedicalStore
from app.medical.terminology import TerminologyIndex, load_anatomy_data

NON_MEDICAL = ("hava bugün nasıl", "spotify'da müzik aç", "dosyaları listele", "hafızamda ara: toplantı")
ARM = "anatomy.musculoskeletal.upper_limb.arm"


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return Curriculum()


@pytest.fixture(scope="module")
def parser(curriculum: Curriculum) -> MedicalIntentParser:
    """The real parser over the shipped data files."""
    structures, terms, _source = load_anatomy_data()
    concepts = default_concept_graph(structures)
    return MedicalIntentParser(curriculum, TerminologyIndex(structures, terms, concepts.all()), concepts)


@pytest.fixture()
def sessions(curriculum: Curriculum, tmp_path):
    store = MedicalStore(tmp_path / "medical.sqlite3")
    try:
        yield SessionManager(store, curriculum)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# the command list the Academy promises to understand
# ---------------------------------------------------------------------------


def test_turkish_study_commands_map_to_their_intents(parser) -> None:
    expected = [
        ("Scapulayı bana basit anlat", MedicalIntent.SIMPLIFY),
        ("Scapula hakkında kısa not çıkar", MedicalIntent.SHORT_NOTES),
        ("Bu PDF'nin 20-40. sayfalarını çalış", MedicalIntent.PDF_ANALYZE),
        ("Bu konudan 20 soru hazırla", MedicalIntent.EXAM_GENERATE),
        ("Hocanın attığım eski sorularına benzet", MedicalIntent.PROFESSOR_STYLE_EXAM),
        ("Anatomy Lab'de humerusu aç", MedicalIntent.ANATOMY_OPEN),
        ("Kasların origo insertio innervatio ve fonksiyonlarını göster", MedicalIntent.MUSCLE_TABLE),
        ("hiyalin kıkırdak ile fibrokartilajı karşılaştır", MedicalIntent.COMPARE),
    ]
    for text, intent in expected:
        command = parser.parse(text)
        assert command.medical is True, text
        assert command.intent == intent, f"{text} → {command.intent}"


def test_english_commands_resolve_their_subject_and_structure(parser) -> None:
    inhibition = parser.parse("Explain competitive inhibition.")
    assert inhibition.intent == MedicalIntent.EXPLAIN
    assert inhibition.subject == "biochemistry"
    assert "biochemistry.competitive_inhibition" in inhibition.concept_ids

    elbow = parser.parse("Which structures form the articulatio cubiti?")
    assert elbow.medical is True
    assert elbow.structure_ids == ["articulatio_cubiti"]
    assert elbow.subject == "anatomy"
    assert elbow.topic_id == "anatomy.musculoskeletal.upper_limb.elbow"


def test_stated_constraints_are_read_off_the_sentence(parser) -> None:
    pages = parser.parse("Bu PDF'nin 20-40. sayfalarını çalış")
    assert pages.page_range == (20, 40) and pages.current_document is True

    exam = parser.parse("Bu konudan 20 soru hazırla")
    assert exam.question_count == 20 and exam.option_count is None

    # A bare follow-up carrying a hard number is study-shaped on its own.
    options = parser.parse("5 şıklı olsun")
    assert options.option_count == 5 and options.medical is True
    assert parser.parse("Zorluk 4 olsun").difficulty == 4
    assert parser.parse("Scapulayı bana basit anlat").depth == DepthLevel.SIMPLE

    professor = parser.parse("Hocanın attığım eski sorularına benzet")
    assert professor.professor_style is True and professor.current_document is True

    # A muscle table is anatomy even when the sentence never names the subject.
    muscles = parser.parse("Kasların origo insertio innervatio ve fonksiyonlarını göster")
    assert muscles.subject == "anatomy"
    assert muscles.topic_id is None or muscles.topic_id.startswith("anatomy")


def test_anatomy_lab_commands_separate_opening_from_highlighting(parser) -> None:
    opened = parser.parse("Anatomy Lab'de humerusu aç")
    assert opened.intent == MedicalIntent.ANATOMY_OPEN
    assert opened.structure_ids == ["humerus"] and opened.confidence == "high"

    marked = parser.parse("Anatomy Lab'de scapulanın spina scapulae'sini işaretle")
    assert marked.intent == MedicalIntent.ANATOMY_HIGHLIGHT
    assert marked.structure_ids == ["scapula"]
    assert marked.landmark_ids == ["scapula.spina_scapulae"]


def test_asking_for_an_explanation_never_opens_the_lab(parser) -> None:
    # "açıkla" begins with the two-letter verb "aç", so matching the open
    # verbs by prefix used to open the model instead of explaining it.
    for text in ("Scapulayı açıkla", "Humerusu açıkla", "Scapulayı biraz daha açıkla"):
        command = parser.parse(text)
        assert command.intent == MedicalIntent.EXPLAIN, text
        assert command.medical is True and command.structure_ids, text

    # The lab commands that trap used to swallow still open the model.
    assert parser.parse("Anatomy Lab'de humerusu aç").intent == MedicalIntent.ANATOMY_OPEN
    assert parser.parse("Humerusu aç").intent == MedicalIntent.ANATOMY_OPEN
    assert parser.parse("Scapulayı göster").intent == MedicalIntent.ANATOMY_OPEN


# ---------------------------------------------------------------------------
# the contextual boundary: follow-ups in, everyday commands out
# ---------------------------------------------------------------------------


def test_context_decides_whether_a_bare_follow_up_is_study_talk(parser) -> None:
    follow_ups = (
        "Bu slaytla kendi bilgini karşılaştır",
        "Cevapları en sonda ver",
        "Aynı soruları kopyalama",
        "Sadece yanlış yaptığım konulardan soru sor",
    )
    for text in follow_ups:  # no medical word, no recent study activity
        alone = parser.parse(text)
        assert alone.medical is False, text
        assert alone.intent == MedicalIntent.NONE, text

    slide = parser.parse("Bu slaytla kendi bilgini karşılaştır", contextual=True)
    assert slide.intent == MedicalIntent.PDF_COMPARE and slide.current_document is True
    assert parser.parse("Cevapları en sonda ver", contextual=True).answers_at_end is True
    assert parser.parse("Aynı soruları kopyalama", contextual=True).no_copy is True
    weak = parser.parse("Sadece yanlış yaptığım konulardan soru sor", contextual=True)
    assert weak.intent == MedicalIntent.EXAM_GENERATE and weak.wrong_only is True

    # Typed inside the Academy, the same sentence needs no other signal.
    forced = parser.parse("Bu slaytla kendi bilgini karşılaştır", forced=True)
    assert forced.intent == MedicalIntent.PDF_COMPARE and forced.confidence == "high"


def test_sadece_filters_the_questions_instead_of_simplifying_them(parser, sessions) -> None:
    # "sadece" (only) begins with "sade" (plain); the simple depth it used
    # to state was then persisted into the session by apply_command.
    weak = parser.parse("Sadece yanlış yaptığım konulardan soru sor", contextual=True)
    assert weak.intent == MedicalIntent.EXAM_GENERATE and weak.wrong_only is True
    assert weak.depth is None
    assert sessions.apply_command(weak).depth == DepthLevel.STANDARD

    # Asking for plain language still asks for it.
    plain = parser.parse("Scapulayı sade bir dille anlat")
    assert plain.intent == MedicalIntent.SIMPLIFY and plain.depth == DepthLevel.SIMPLE
    assert parser.parse("Scapulayı sadeleştirerek anlat").depth == DepthLevel.SIMPLE


def test_everyday_commands_are_never_medical(parser, sessions) -> None:
    sessions.start_chat_quiz(["q1"], mode="quiz")
    quiz_session = sessions.get()
    for text in NON_MEDICAL:
        commands = (parser.parse(text), parser.parse(text, contextual=True), parser.parse(text, session=quiz_session))
        for command in commands:  # context and a running quiz must not drag them in
            assert command.medical is False, text
            assert command.intent == MedicalIntent.NONE, text
            assert command.answer_key is None and command.label == "Tıp dışı", text
    assert parser.is_medical("hava bugün nasıl") is False
    assert parser.is_medical("Scapula nedir") is True


# ---------------------------------------------------------------------------
# the active chat quiz
# ---------------------------------------------------------------------------


def test_active_quiz_reads_bare_answers_and_navigation(parser, sessions) -> None:
    sessions.update({"subject": "anatomy", "topic_id": ARM})
    sessions.start_chat_quiz(["q1", "q2"], mode="quiz")
    session = sessions.get()

    answer = parser.parse("B", session=session)
    assert answer.intent == MedicalIntent.ANSWER
    assert answer.answer_key == "B" and answer.confidence == "high"
    assert answer.subject == "anatomy"  # the answer inherits the session it belongs to
    assert parser.parse("cevap: e", session=session).answer_key == "E"
    assert parser.parse("(a)", session=session).answer_key == "A"
    assert parser.parse("sonraki soru", session=session).intent == MedicalIntent.NEXT_QUESTION
    assert parser.parse("neden B yanlış", session=session).intent == MedicalIntent.WHY_WRONG

    # Only A–E are answers; anything else falls through to ordinary parsing.
    assert parser.parse("F", session=session).intent == MedicalIntent.NONE
    assert parser.parse("Scapula nedir", session=session).intent == MedicalIntent.EXPLAIN


def test_quiz_replies_are_inert_when_no_quiz_is_running(parser, sessions) -> None:
    sessions.start_chat_quiz(["q1"], mode="quiz")
    sessions.stop_chat_quiz()
    idle = sessions.get()
    assert idle.chat_quiz == {}

    quiz_only = (MedicalIntent.ANSWER, MedicalIntent.NEXT_QUESTION, MedicalIntent.WHY_WRONG)
    for text in ("B", "sonraki soru", "neden B yanlış"):
        for command in (parser.parse(text, session=idle), parser.parse(text, session=idle, contextual=True)):
            assert command.answer_key is None, text
            assert command.intent not in quiz_only, text

    # An empty question list must not leave the quiz "active".
    assert sessions.start_chat_quiz([], mode="quiz").chat_quiz["active"] is False


# ---------------------------------------------------------------------------
# boundaries and invalid input
# ---------------------------------------------------------------------------


def test_numeric_constraints_are_clamped_to_the_supported_range(parser) -> None:
    assert parser.parse("200 soru hazırla").question_count == 100
    assert parser.parse("0 soru hazırla").question_count == 1
    assert parser.parse("9 şıklı olsun").option_count == 6
    assert parser.parse("1 şıklı olsun").option_count == MIN_OPTIONS
    assert parser.parse("zorluk 9 olsun").difficulty == 5
    assert parser.parse("zorluk 0 olsun").difficulty == 1
    # A page range stated backwards is still read low-to-high.
    assert parser.parse("sayfa 40 ile 20 arası").page_range == (20, 40)


def test_blank_input_stays_a_neutral_command(parser) -> None:
    for text in ("", "   ", "\n\t"):
        command = parser.parse(text)
        assert command.intent == MedicalIntent.NONE
        assert command.medical is False and command.confidence == "low"
        assert command.terms == [] and command.reasons == [] and command.text == text

    payload = parser.parse("Bu konudan 20 soru hazırla, 5 şıklı, cevapları en sonda ver, tek tek sor").to_dict()
    assert payload["intent"] == MedicalIntent.EXAM_GENERATE and payload["label"] == "Sınav üretimi"
    assert payload["question_count"] == 20 and payload["option_count"] == 5
    assert payload["flags"]["answers_at_end"] is True and payload["flags"]["one_at_a_time"] is True
    assert payload["flags"]["wrong_only"] is False


def test_describe_command_summarises_only_what_was_stated(parser) -> None:
    exam = describe_command(parser.parse("Bu konudan 20 soru hazırla, 5 şıklı, zorluk 4"))
    assert exam == "Sınav üretimi · 20 soru · 5 şık · zorluk 4"
    assert describe_command(parser.parse("Bu PDF'nin 20-40. sayfalarını çalış")) == "Belge analizi · s. 20–40"
    assert describe_command(parser.parse("Anatomy Lab'de humerusu aç")) == "Anatomi Lab'de aç · anatomy · humerus"
    assert describe_command(StudyCommand()) == "Tıp dışı"


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


def test_update_validates_before_it_applies(sessions) -> None:
    session, problems = sessions.update({"topic_id": ARM})
    assert problems == []
    assert session.subject == "anatomy" and session.recent_topics == [ARM]

    session, problems = sessions.update({"subject": "astroloji", "topic_id": "anatomy.yok", "bilinmeyen": 1, "document_ids": "tek"})
    assert problems == [
        "Bilinmeyen ders: astroloji", "Bilinmeyen konu: anatomy.yok",
        "Bilinmeyen alan: bilinmeyen", "document_ids bir liste olmalı.",
    ]
    assert session.subject == "anatomy" and session.topic_id == ARM and session.document_ids == []
    assert sessions.update("bozuk")[1] == ["Geçersiz oturum güncellemesi."]

    # Switching subject drops a topic that no longer belongs to it.
    switched, _problems = sessions.update({"subject": "histology"})
    assert switched.subject == "histology" and switched.topic_id is None


def test_update_reports_a_choice_the_backend_does_not_know(sessions) -> None:
    session, problems = sessions.update(
        {"mode": "sihir", "depth": "derin", "knowledge_source": "internet", "knowledge_priority": "acele"}
    )
    assert problems == [
        "Bilinmeyen mod: sihir", "Bilinmeyen derinlik: derin",
        "Bilinmeyen bilgi kaynağı: internet", "Bilinmeyen bilgi önceliği: acele",
    ]
    assert session.mode == StudyMode.TEACH and session.depth == DepthLevel.STANDARD
    assert session.knowledge_source == KnowledgeSource.COURSE_AND_JARVIS
    assert session.knowledge_priority == KnowledgePriority.BALANCED

    applied, problems = sessions.update({"mode": "quiz", "depth": "exam"})
    assert problems == [] and applied.mode == StudyMode.QUIZ and applied.depth == DepthLevel.EXAM

    # A cleared box states nothing, so it is not a wrong answer either.
    kept, problems = sessions.update({"mode": None, "depth": ""})
    assert problems == [] and kept.mode == StudyMode.QUIZ and kept.depth == DepthLevel.EXAM


def test_update_clamps_numbers_and_swaps_an_inverted_page_range(sessions) -> None:
    session, problems = sessions.update(
        {"difficulty": 99, "question_count": 999, "option_count": 1, "page_from": 90, "page_to": 10, "document_ids": ["d1", "   ", "d2"]}
    )
    # A number pulled to the nearest limit is applied, but never silently.
    assert problems == [
        "Geçersiz zorluk: 99 (1-5 aralığında olmalı)",
        "Geçersiz soru sayısı: 999 (1-60 aralığında olmalı)",
        "Geçersiz şık sayısı: 1 (2-6 aralığında olmalı)",
    ]
    assert session.difficulty == 5 and session.question_count == MAX_QUESTIONS
    assert session.option_count == MIN_OPTIONS
    assert (session.page_from, session.page_to) == (10, 90)
    assert session.document_ids == ["d1", "d2"]

    # An unparsable number keeps the value the session already had, and says so.
    kept, problems = sessions.update({"difficulty": "abc"})
    assert kept.difficulty == 5
    assert problems == ["Geçersiz zorluk: abc (sayı olmalı)"]

    # An emptied number box changes nothing and reports nothing.
    untouched, problems = sessions.update({"question_count": None, "page_from": ""})
    assert problems == []
    assert untouched.question_count == MAX_QUESTIONS and untouched.page_from == 10


def test_apply_command_persists_stated_constraints_and_difficulty_nudges(sessions, parser) -> None:
    command = parser.parse("Bu konudan 20 soru hazırla, 5 şıklı, zorluk 4, cevapları en sonda ver", contextual=True)
    session = sessions.apply_command(command)
    assert (session.question_count, session.option_count, session.difficulty) == (20, 5, 4)
    # The next request inherits them from storage, not from the sentence.
    assert sessions.get().question_count == 20 and sessions.get().difficulty == 4

    # A topic the curriculum does not know is never written to the session.
    stray = sessions.apply_command(StudyCommand(subject="histology", topic_id="histology.yok"))
    assert stray.subject == "histology" and stray.topic_id is None

    sessions.update({"difficulty": 3})
    assert sessions.apply_command(StudyCommand(harder=True)).difficulty == 4
    assert sessions.apply_command(StudyCommand(easier=True)).difficulty == 3
    sessions.update({"difficulty": 5})
    assert sessions.apply_command(StudyCommand(harder=True)).difficulty == 5
    sessions.update({"difficulty": 1})
    assert sessions.apply_command(StudyCommand(easier=True)).difficulty == 1
    # An explicit difficulty wins over a nudge in the same command.
    assert sessions.apply_command(StudyCommand(difficulty=2, harder=True)).difficulty == 2


def test_resolve_overlays_the_command_on_the_stored_session(sessions, parser) -> None:
    sessions.update({"subject": "anatomy", "topic_id": ARM, "question_count": 10, "difficulty": 3, "page_from": 1, "page_to": 5})
    base = sessions.resolve()
    assert (base.question_count, base.difficulty, base.page_from, base.page_to) == (10, 3, 1, 5)
    assert base.spoken is False

    command = parser.parse("Bu PDF'nin 20-40. sayfalarından 200 soru hazırla, 9 şıklı, zorluk 9", contextual=True)
    context = sessions.resolve(command, spoken=True)
    # The command's numbers win, but the context's own limits still bind.
    assert context.question_count == MAX_QUESTIONS
    assert context.option_count == 6 and context.difficulty == 5
    assert (context.page_from, context.page_to) == (20, 40) and context.spoken is True
    # What the command never mentioned still comes from the session.
    assert context.subject == "anatomy" and context.topic_id == ARM
    # Resolving is a read: the stored session is untouched.
    assert sessions.get().question_count == 10
    assert sessions.resolve().to_dict()["page_from"] == 1


def test_chat_quiz_state_starts_updates_and_stops(sessions) -> None:
    assert sessions.chat_quiz_state() == {}

    started = sessions.start_chat_quiz(["q1", "q2"], mode="quiz", exam_id="e1")
    assert started.chat_quiz["active"] is True
    assert started.chat_quiz["question_ids"] == ["q1", "q2"]
    assert started.chat_quiz["last_question_id"] == "q1"
    assert started.chat_quiz["index"] == 0 and started.chat_quiz["exam_id"] == "e1"

    sessions.update_chat_quiz(index=1, last_answer="B")
    state = sessions.chat_quiz_state()
    assert state["index"] == 1 and state["last_answer"] == "B"
    assert state["question_ids"] == ["q1", "q2"]
    state["index"] = 99  # the reader hands out a copy, not the stored dict
    assert sessions.chat_quiz_state()["index"] == 1
    assert sessions.stop_chat_quiz().chat_quiz == {}


def test_describe_labels_the_session_in_turkish(sessions) -> None:
    empty = sessions.describe()["labels"]
    assert empty["subject"] == "Ders seçilmedi" and empty["topic"] == "Konu seçilmedi"
    assert empty["mode"] == "Öğret" and empty["depth"] == "Standart"

    sessions.update({"topic_id": ARM, "mode": "quiz", "depth": "exam"})
    described = sessions.describe()
    assert described["labels"]["subject"] == "Anatomi"
    assert described["labels"]["topic"].startswith("Anatomi › Hareket sistemi")
    assert described["labels"]["mode"] == "Quiz" and described["labels"]["depth"] == "Sınav modu"
    assert described["topic_id"] == ARM
    assert {"value": "simple", "label": "Basit"} in described["options"]["depths"]
    assert len(described["options"]["subjects"]) == 7


@pytest.mark.parametrize(
    "text",
    [
        "sesi 20-40 arası ayarla",
        "ekran parlaklığını 30-60 arası yap",
        "10-20 arası dosyaları sil",
        "ses seviyesini 15-25 arasında tut",
    ],
)
def test_a_bare_number_pair_alone_is_not_a_study_scope(parser, text: str) -> None:
    """Volumes, brightness and file counts wear the same shape as a page
    range; taking them would hand ordinary Windows commands to the Academy."""
    command = parser.parse(text)

    assert command.medical is False
    assert command.page_range is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sayfa 20-40 arası çalış", (20, 40)),
        ("anatomi 20-40 sayfaları özetle", (20, 40)),
        ("20-40 arası 5 soru hazırla", (20, 40)),
        ("40-20 sayfaları arasını anlat", (20, 40)),
    ],
)
def test_a_page_range_is_kept_when_the_sentence_means_pages(parser, text: str, expected) -> None:
    command = parser.parse(text)

    assert command.medical is True
    assert command.page_range == expected
