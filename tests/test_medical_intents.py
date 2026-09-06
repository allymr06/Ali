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

# The everyday half of the boundary, held as one table so the next word
# that collides with the medical vocabulary is caught by a list rather
# than by the user. Every entry is a request JARVIS itself must answer:
# Windows automation, files, timers, volume, music, reminders, coding and
# ordinary chat, in Turkish and in English.
EVERYDAY_REQUESTS = (
    # Turkish words the medical vocabulary starts inside
    "genel ayarları aç",
    "genellikle saat kaçta uyanırım",
    "geniş ekranda aç",
    "genç bir ses seç",
    "dizini listele",
    "indirilenler dizinini aç",
    "yeni bir dizi öner",
    "dizi izlemek için Netflix aç",
    "dizüstü bilgisayarı kapat",
    "dizüstünün pilini kontrol et",
    "dokümanı kaydet",
    "bu dokümanı yazdır",
    "dokunmatik yüzeyi devre dışı bırak",
    "kolay bir yol öner",
    "kolonya siparişi ver",
    "kol saatimi bul",
    "sınırlı yetki ver",
    "gün boyunca sessiz kal",
    "gramer hatalarını düzelt",
    "500 gram şeker ekle",
    "spor haberlerini aç",
    "spor müziği çal",
    "takvime ekleme yap",
    "listeye bir madde ekle",
    # "prof" inside an ordinary word
    "profil fotoğrafımı değiştir",
    "Chrome profilini aç",
    "profilimi güncelle",
    "kullanıcı profilini göster",
    "profesyonel bir e-posta yaz",
    # numbers that are not question, option or difficulty counts
    "kodda 3 sorun var, düzelt",
    "1 sorunum var",
    "testlerde 3 sorun çıktı, logları incele",
    "bana 2 seçenek sun",
    "bu filme 4/5 verdim",
    "4/5/2026 tarihli toplantıyı hatırlat",
    "wifi 3/5 sinyal gösteriyor",
    "sesi 20-40 arası ayarla",
    "ekran parlaklığını 30-60 arası yap",
    # Windows automation that happens to name a medical thing
    "masaüstündeki anatomi klasörünü aç",
    "kalp.pdf dosyasını aç",
    "histoloji sunumunu yazdır",
    "anatomi klasörünü sil",
    "anatomi notlarımı masaüstüne kaydet",
    "hücre adlı klasörü oluştur",
    "epitel.docx dosyasını masaüstünde bul",
    # plain assistant work
    "masaüstündeki proje klasörünü aç",
    "dosyaları listele",
    "hava bugün nasıl",
    "spotify'da müzik aç",
    "hafızamda ara: toplantı",
    "saat kaç",
    "yarın sabah 8'de alarm kur",
    "bilgisayarı yeniden başlat",
    "ses seviyesini yükselt",
    "not defterini aç",
    "toplantı için hatırlatıcı kur",
    "python'da bir liste nasıl sıralanır",
    "bu fonksiyonu refactor et",
    "git commit mesajı yaz",
    "klasördeki dosyaları say",
    "müzik çal",
    "sesi kapat",
    "e-postalarımı kontrol et",
    # English, where "exam" and "profile" live inside ordinary words
    "give me an example of a python decorator",
    "can you examine this log file",
    "show me examples of async code",
    "examine the diff",
    "for example, what does this function return",
    "create an example dockerfile",
    "make an example config file",
    "profile this script for me",
    "my profile picture",
    "give me 2 options for the deployment",
    "open the downloads folder",
    "set a timer for 10 minutes",
    "what is the weather today",
    "turn up the volume",
    "write a unit test for this function",
    "list the files in this directory",
)

# The study half: the requests the Academy documents, plus the phrasings
# the reported misroutes were about. ``None`` means only that the turn
# must be claimed; the intent is asserted wherever it is documented.
STUDY_REQUESTS = (
    ("Scapulayı bana basit anlat", MedicalIntent.SIMPLIFY),
    ("Scapula hakkında kısa not çıkar", MedicalIntent.SHORT_NOTES),
    ("Bu PDF'nin 20-40. sayfalarını çalış", MedicalIntent.PDF_ANALYZE),
    ("Bu konudan 20 soru hazırla", MedicalIntent.EXAM_GENERATE),
    ("Bu konudan 20 soru hazırla, 5 şıklı, cevapları en sonda ver", MedicalIntent.EXAM_GENERATE),
    ("Hocanın attığım eski sorularına benzet", MedicalIntent.PROFESSOR_STYLE_EXAM),
    ("Anatomy Lab'de humerusu aç", MedicalIntent.ANATOMY_OPEN),
    ("Anatomy Lab'de scapulanın spina scapulae'sini işaretle", MedicalIntent.ANATOMY_HIGHLIGHT),
    ("Kasların origo insertio innervatio ve fonksiyonlarını göster", MedicalIntent.MUSCLE_TABLE),
    ("hiyalin kıkırdak ile fibrokartilajı karşılaştır", MedicalIntent.COMPARE),
    ("mitoz ve mayoz arasındaki farkı anlat", MedicalIntent.COMPARE),
    ("Explain competitive inhibition.", MedicalIntent.EXPLAIN),
    ("Which structures form the articulatio cubiti?", None),
    ("Scapulayı açıkla", MedicalIntent.EXPLAIN),
    ("Humerusu aç", MedicalIntent.ANATOMY_OPEN),
    ("Scapulayı göster", MedicalIntent.ANATOMY_OPEN),
    ("Scapula nedir", MedicalIntent.EXPLAIN),
    ("Scapulayı sade bir dille anlat", MedicalIntent.SIMPLIFY),
    ("sayfa 20-40 arası çalış", None),
    ("anatomi 20-40 sayfaları özetle", None),
    ("20-40 arası 5 soru hazırla", None),
    ("40-20 sayfaları arasını anlat", None),
    ("200 soru hazırla", MedicalIntent.EXAM_GENERATE),
    ("9 şıklı olsun", None),
    ("zorluk 4 olsun", None),
    ("beni sına", MedicalIntent.QUIZ),
    ("beni anatomiden sına", MedicalIntent.QUIZ),
    ("mikrobiyolojiden quiz yap", MedicalIntent.QUIZ),
    ("sözlü sınav yap", MedicalIntent.ORAL_EXAM),
    ("anatomiden 20 soru hazırla", MedicalIntent.EXAM_GENERATE),
    ("histoloji sınavına hazırlan", None),
    ("sınavdan önce hızlı tekrar yap", MedicalIntent.RAPID_REVIEW),
    ("yarın sınavım var, hızlı tekrar yap", MedicalIntent.RAPID_REVIEW),
    ("sınavdan önce yüksek verimli noktaları ver", MedicalIntent.HIGH_YIELD),
    ("scapula için sınavda çıkacak noktaları ver", MedicalIntent.HIGH_YIELD),
    ("hocanın soru tarzını analiz et", MedicalIntent.PROFESSOR_PROFILE),
    ("hocanın eski sorularını incele", MedicalIntent.PROFESSOR_PROFILE),
    ("glikoliz basamaklarını özetle", MedicalIntent.SUMMARIZE),
    ("biyokimya konularını özetle", MedicalIntent.SUMMARIZE),
    ("epitel dokusunu anlat", MedicalIntent.EXPLAIN),
    ("nefron yapısını açıkla", MedicalIntent.EXPLAIN),
    ("kalp nasıl çalışır", MedicalIntent.EXPLAIN),
    ("hücre zarından difüzyon nasıl olur", MedicalIntent.EXPLAIN),
    ("sinir hücresi nasıl çalışır", MedicalIntent.EXPLAIN),
    ("diz ligamentlerini anlat", MedicalIntent.EXPLAIN),
    ("kol kaslarını anlat", MedicalIntent.EXPLAIN),
    ("boyun anatomisini göster", None),
    ("gram boyama nedir", None),
    ("hemoglobin nedir", None),
    ("kromozom sayısı kaçtır", None),
)


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


@pytest.mark.parametrize("text", EVERYDAY_REQUESTS)
def test_an_everyday_request_never_reaches_the_academy(parser, text: str) -> None:
    """The whole boundary in one table.

    A claimed turn loses the assistant's own tools, so a false positive
    here breaks Windows automation, files and ordinary chat. The table is
    the guard: prefix matching used to hand 52 of these 79 requests to the
    Academy — "genel" through "gen", "dizin" through "diz", "doküman"
    through "doku", "profil" through "prof", "example" through "exam".
    """
    command = parser.parse(text)

    assert command.medical is False, f"{text} → {command.intent} ({command.reasons})"
    assert command.intent == MedicalIntent.NONE
    assert command.confidence == "low"


@pytest.mark.parametrize(("text", "intent"), STUDY_REQUESTS)
def test_a_documented_study_request_still_reaches_the_academy(parser, text: str, intent) -> None:
    """The other half of the same boundary: tightening the vocabulary must
    not cost the Academy the requests it is for."""
    command = parser.parse(text)

    assert command.medical is True, text
    if intent is not None:
        assert command.intent == intent, f"{text} → {command.intent}"


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


def test_a_medical_word_that_is_also_an_everyday_word_needs_corroboration(parser) -> None:
    """"spor", "gram", "diz" and "kol" are medical words and ordinary
    words at once. One of them alone is not a study request; a second
    signal in the same sentence is what makes it one."""
    for text in ("spor haberlerini aç", "500 gram şeker ekle", "kol saatimi bul", "dizüstünün pilini kontrol et"):
        assert parser.parse(text).medical is False, text

    # Corroborated by a second everyday medical word, by a real medical
    # word, and by a named subject.
    assert parser.parse("kol kaslarını anlat").medical is True
    assert parser.parse("sinir hücresi nasıl çalışır").medical is True
    assert parser.parse("boyun anatomisini göster").medical is True
    # Inside the Academy the same sentence needs nothing else.
    assert parser.parse("spor nedir", forced=True).medical is True


def test_recent_study_activity_does_not_corroborate_an_everyday_word(parser, sessions) -> None:
    """``contextual`` is for study-shaped follow-ups. "dizini listele" is a
    plain system command whichever screen the student was on a minute ago,
    and it must keep its filesystem tool during a study session."""
    sessions.start_chat_quiz(["q1"], mode="quiz")
    quiz_session = sessions.get()
    everyday = (
        "dizini listele", "yeni bir dizi öner", "kol saatimi bul", "gün boyunca sessiz kal",
        "500 gram şeker ekle", "spor haberlerini aç", "takvime ekleme yap",
        "dizüstünün pilini kontrol et", "profil fotoğrafımı değiştir",
    )
    for text in everyday:
        for command in (parser.parse(text, contextual=True), parser.parse(text, session=quiz_session)):
            assert command.medical is False, text
            assert command.intent == MedicalIntent.NONE, text


def test_a_medical_word_is_matched_whole_and_not_as_a_prefix(parser) -> None:
    """"gen" opens genel/genç/geniş/generate and "doku" opens doküman;
    only a real Turkish suffix may follow the word itself."""
    for text in ("genel ayarları aç", "geniş ekranda aç", "dokümanı kaydet", "kolay bir yol öner", "sınırlı yetki ver"):
        command = parser.parse(text)
        assert command.medical is False, text

    # The words themselves, inflected, are still found — with a second
    # signal to corroborate them.
    assert parser.parse("genleri ve kromozomları anlat").medical is True
    assert parser.parse("epitel dokusunu anlat").medical is True


def test_prof_is_read_as_a_title_not_as_the_start_of_profil(parser) -> None:
    """"prof" used to match profil / profile / profesyonel, and a profile
    photo request was answered with the Academy's professor-profile line
    instead of being sent to the model at all."""
    for text in ("profil fotoğrafımı değiştir", "Chrome profilini aç", "profile this script for me", "profesyonel bir e-posta yaz"):
        command = parser.parse(text)
        assert command.medical is False, text
        assert command.intent != MedicalIntent.PROFESSOR_PROFILE, text

    # The title still counts, with or without its dot.
    assert parser.parse("Prof. Ahmet'in tarzını öğren").intent == MedicalIntent.PROFESSOR_PROFILE
    assert parser.parse("hocanın tarzını analiz et").intent == MedicalIntent.PROFESSOR_PROFILE


def test_exam_is_read_as_a_word_not_as_the_start_of_example(parser) -> None:
    """An English coding request carrying "example" or "examine" used to
    be claimed as an exam request and answered without a model call."""
    for text in ("give me an example of a python decorator", "can you examine this log file", "show me examples of async code"):
        command = parser.parse(text)
        assert command.medical is False, text

    assert parser.parse("give me 20 exam questions about the scapula").medical is True
    assert parser.parse("quiz me on the scapula").medical is True


def test_naming_a_medical_thing_does_not_take_a_windows_command(parser) -> None:
    """A file, a folder or the desktop makes the turn Core's: the Academy
    may narrow the exposed tools, never remove the ones the request needs.
    Opening the anatomy folder is opening a folder."""
    for text in ("masaüstündeki anatomi klasörünü aç", "kalp.pdf dosyasını aç", "anatomi klasörünü sil", "histoloji sunumunu yazdır"):
        command = parser.parse(text)
        assert command.medical is False, text

    # A stated study scope is still the Academy's, folder or no folder.
    assert parser.parse("anatomi klasöründeki pdf'ten 20 soru hazırla").medical is True
    # And so is anything typed inside the Academy itself.
    assert parser.parse("anatomi klasörünü aç", forced=True).medical is True


def test_a_number_only_counts_when_it_carries_a_study_unit(parser) -> None:
    """"3 sorun" is three problems, not three questions; "2 seçenek" is a
    pair of choices; "4/5" is a rating, a date or a signal strength."""
    for text in ("kodda 3 sorun var, düzelt", "1 sorunum var", "bana 2 seçenek sun", "bu filme 4/5 verdim", "wifi 3/5 sinyal gösteriyor"):
        command = parser.parse(text)
        assert command.medical is False, text
        assert command.question_count is None and command.option_count is None, text
        assert command.difficulty is None, text

    # The study units themselves are unchanged.
    counted = parser.parse("20 soru hazırla, 5 şıklı, zorluk 4")
    assert (counted.question_count, counted.option_count, counted.difficulty) == (20, 5, 4)
    assert parser.parse("30 sorudan oluşsun").question_count == 30
    # "seçenek" still states the option count once the turn is medical.
    assert parser.parse("scapula sorularında 4 seçenek olsun").option_count == 4


def test_the_word_sinav_does_not_swallow_rapid_review_and_high_yield(parser) -> None:
    """"sınavdan önce" and "sınavda çıkacak" are the rapid-review and
    high-yield vocabularies' own phrasings: the exam branch used to claim
    them because the sentence also said "sınav" and carried a verb."""
    assert parser.parse("sınavdan önce hızlı tekrar yap").intent == MedicalIntent.RAPID_REVIEW
    assert parser.parse("yarın sınavım var, hızlı tekrar yap").intent == MedicalIntent.RAPID_REVIEW
    assert parser.parse("sınavdan önce yüksek verimli noktaları ver").intent == MedicalIntent.HIGH_YIELD
    assert parser.parse("scapula için sınavda çıkacak noktaları ver").intent == MedicalIntent.HIGH_YIELD

    # A stated count still asks for a paper, not a revision sheet.
    exam = parser.parse("sınavdan önce 20 soru hazırla")
    assert exam.intent == MedicalIntent.EXAM_GENERATE and exam.question_count == 20


def test_analysing_the_professors_questions_builds_a_profile(parser) -> None:
    """Analysing the professor's questions is how the profile is built;
    the word "soru" in the sentence used to route it to exam generation."""
    for text in ("hocanın soru tarzını analiz et", "hocanın eski sorularını incele", "hocanın soru kalıplarını öğren"):
        assert parser.parse(text).intent == MedicalIntent.PROFESSOR_PROFILE, text

    # Asking for a paper in that style still generates one.
    assert parser.parse("Hocanın attığım eski sorularına benzet").intent == MedicalIntent.PROFESSOR_STYLE_EXAM
    assert parser.parse("hocanın tarzında 20 soru hazırla").intent == MedicalIntent.PROFESSOR_STYLE_EXAM


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


# ---------------------------------------------------------------------------
# the second round: what a pre-merge audit measured as newly claimed or newly lost
# ---------------------------------------------------------------------------

AUDITED_EVERYDAY = (
    # "question" is ordinary English and never opens a study turn by itself
    "I have a question about this code",
    "quick question: how do I restart the service",
    "answer my questions",
    "skip this question",
    # words that only demote a term match; their bare tokens are not evidence
    "the base line of the chart",
    "set the angle and the base",
    "align the head and the base of the column",
    "what is the base url for this line of code",
    # the generic option and difficulty spellings are assistant vocabulary
    "bana 2 seçenek sun",
    "seviye 3 olsun",
    # the right-hand boundary that separates a count from a problem report
    "3 sorun var",
    "1 sorunum var",
)

AUDITED_STUDY = (
    ("hocanın sorularını analiz edip 20 soru hazırla", MedicalIntent.PROFESSOR_STYLE_EXAM),
    ("anatomiden en önemli konulardan soru hazırla", MedicalIntent.EXAM_GENERATE),
    ("sınavda çıkacak anatomi sorularını hazırla", MedicalIntent.EXAM_GENERATE),
    ("kritik noktalardan anatomi sorusu hazırla", MedicalIntent.EXAM_GENERATE),
    ("sınavdan önce hızlı tekrar yap", MedicalIntent.RAPID_REVIEW),
    ("yarın sınavım var, hızlı tekrar yap", MedicalIntent.RAPID_REVIEW),
    ("sınavda çıkacak noktaları ver", MedicalIntent.HIGH_YIELD),
)

AUDITED_COUNTS = (
    ("20 soruyu hazırla", 20),
    ("ilk 5 soruyu göster", 5),
    ("20 soruya cevap ver", 20),
    ("5 sorumu kontrol et", 5),
    ("10 soruluk sınav hazırla", 10),
    ("3 sorun var", None),
    ("1 sorunum var", None),
)


@pytest.mark.parametrize("text", AUDITED_EVERYDAY)
def test_the_second_round_of_false_positives_stays_closed(parser, text: str) -> None:
    assert parser.parse(text).medical is False


@pytest.mark.parametrize(("text", "intent"), AUDITED_STUDY)
def test_a_revision_request_and_a_question_request_stay_apart(parser, text: str, intent: str) -> None:
    """"Sınav" can place a revision in time; only "soru" asks for items."""
    command = parser.parse(text)

    assert command.medical is True
    assert command.intent == intent


@pytest.mark.parametrize(("text", "count"), AUDITED_COUNTS)
def test_a_stated_count_survives_its_turkish_case_ending(parser, text: str, count) -> None:
    assert parser.parse(text, contextual=True).question_count == count


@pytest.mark.parametrize("text", ("4 seçenekli olsun", "5 seçenek olsun", "seviye 3 olsun"))
def test_the_generic_follow_up_shorthand_needs_a_session_behind_it(parser, text: str) -> None:
    """Cold it is assistant vocabulary; mid-session it is the documented shorthand."""
    assert parser.parse(text).medical is False
    assert parser.parse(text, contextual=True).medical is True


@pytest.mark.parametrize("text", ("5 şıklı olsun", "zorluk 4 olsun"))
def test_a_stated_option_count_or_difficulty_still_claims_a_cold_turn(parser, text: str) -> None:
    assert parser.parse(text).medical is True


@pytest.mark.parametrize(
    "text",
    ("what is a spore", "explain gram staining", "how do cells divide", "what are epithelial cells"),
)
def test_an_english_question_about_a_medical_word_still_reaches_the_academy(parser, text: str) -> None:
    """English medical vocabulary is thin in the stem list, so a question about
    one of the everyday-shaped words is what corroborates it."""
    assert parser.parse(text).medical is True
