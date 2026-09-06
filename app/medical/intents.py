"""Deterministic medical intent parsing.

Natural requests such as "Bu PDF'nin 20–40. sayfalarını çalış", "Bu
konudan 20 soru hazırla, 5 şıklı, cevapları en sonda ver" or "Anatomy
Lab'de humerusu aç" become a structured ``StudyCommand`` without a model
call. The parser only decides *what kind* of study action is wanted and
which constraints were stated; explaining, generating and grading happen
elsewhere. Anything it cannot classify stays a general medical question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.medical.catalog import Curriculum
from app.medical.concepts import ConceptGraph
from app.medical.models import DepthLevel, StudySession
from app.medical.terminology import TerminologyIndex, TermMatch
from app.medical.text import fold, has_stem, parse_page_range, tokens


class MedicalIntent(StrEnum):
    EXPLAIN = "medical.explain"
    SIMPLIFY = "medical.simplify"
    SUMMARIZE = "medical.summarize"
    SHORT_NOTES = "medical.short_notes"
    HIGH_YIELD = "medical.high_yield"
    COMPARE = "medical.compare"
    RAPID_REVIEW = "medical.rapid_review"
    TERMINOLOGY = "medical.terminology"
    MUSCLE_TABLE = "medical.muscle_table"
    PDF_ANALYZE = "medical.pdf_analyze"
    PDF_COMPARE = "medical.pdf_compare"
    QUIZ = "medical.quiz"
    ORAL_EXAM = "medical.oral_exam"
    EXAM_GENERATE = "medical.exam_generate"
    ANSWER = "medical.exam_answer"
    NEXT_QUESTION = "medical.next_question"
    WHY_WRONG = "medical.why_wrong"
    PROFESSOR_PROFILE = "medical.professor_profile"
    PROFESSOR_STYLE_EXAM = "medical.professor_style_exam"
    REVIEW_WEAKNESS = "medical.review_weakness"
    ANATOMY_OPEN = "medical.anatomy.open"
    ANATOMY_HIGHLIGHT = "medical.anatomy.highlight"
    ANATOMY_QUIZ = "medical.anatomy.quiz"
    GENERAL = "medical.general"
    NONE = "none"


INTENT_LABELS_TR: dict[str, str] = {
    MedicalIntent.EXPLAIN: "Açıklama",
    MedicalIntent.SIMPLIFY: "Basit anlatım",
    MedicalIntent.SUMMARIZE: "Özet",
    MedicalIntent.SHORT_NOTES: "Kısa not",
    MedicalIntent.HIGH_YIELD: "Yüksek verim",
    MedicalIntent.COMPARE: "Karşılaştırma",
    MedicalIntent.RAPID_REVIEW: "Hızlı tekrar",
    MedicalIntent.TERMINOLOGY: "Terim açıklaması",
    MedicalIntent.MUSCLE_TABLE: "Kas tablosu",
    MedicalIntent.PDF_ANALYZE: "Belge analizi",
    MedicalIntent.PDF_COMPARE: "Belge–bilgi karşılaştırması",
    MedicalIntent.QUIZ: "Quiz",
    MedicalIntent.ORAL_EXAM: "Sözlü sınav",
    MedicalIntent.EXAM_GENERATE: "Sınav üretimi",
    MedicalIntent.ANSWER: "Cevap",
    MedicalIntent.NEXT_QUESTION: "Sonraki soru",
    MedicalIntent.WHY_WRONG: "Neden yanlış",
    MedicalIntent.PROFESSOR_PROFILE: "Hoca tarzı profili",
    MedicalIntent.PROFESSOR_STYLE_EXAM: "Hoca tarzında sınav",
    MedicalIntent.REVIEW_WEAKNESS: "Zayıf alan tekrarı",
    MedicalIntent.ANATOMY_OPEN: "Anatomi Lab'de aç",
    MedicalIntent.ANATOMY_HIGHLIGHT: "Yapıyı işaretle",
    MedicalIntent.ANATOMY_QUIZ: "Anatomi quizi",
    MedicalIntent.GENERAL: "Tıp sorusu",
    MedicalIntent.NONE: "Tıp dışı",
}

# Vocabulary. Every tuple is matched as a token prefix on folded text, so
# Turkish suffixes do not matter ("hazirla", "hazirlar misin", ...).
_GENERATE = ("hazirla", "olustur", "uret", "yaz", "cikar", "generate", "create", "make", "prepare", "build", "yap", "kur", "ver", "give", "want", "need", "istiyorum", "isterim", "lazim", "gerek")
_QUESTION_WORDS = ("soru", "sinav", "test", "exam", "question", "quiz", "mcq")
_EXPLAIN = ("anlat", "acikla", "explain", "ogret", "teach", "nedir", "nasil", "neden", "niye", "what", "how", "why", "which", "hangi", "describe", "tanimla", "tell")
_QUIZ_TOKENS = frozenset({"sina", "sinar", "sinasana", "sinayabilir", "sinat", "sinamani", "sinayalim"})
_STUDY_SHAPED = ("soru", "sinav", "exam", "quiz", "question", "test", "cevap", "answer", "sik", "secenek", "option", "pdf", "slayt", "slide", "ders", "lecture", "hoca", "profesor", "professor", "konu", "topic", "sayfa", "page", "not", "notes", "zorluk", "difficulty", "tekrar", "review", "calis", "study")
_SIMPLIFY = ("basit", "basitce", "simple", "simply", "kolayca", "anlasilir", "simplify", "plain", "cocuga", "sadelestir")
# Matched as whole words: "sadece" (only) starts with the prefix "sade"
# and would otherwise force every "sadece ..." request to be simplified.
_SIMPLIFY_WORDS = frozenset({"sade"})
_NOTES = ("not", "notes", "notlar", "revision")
_SUMMARY = ("ozet", "summar")
_HIGH_YIELD = ("yuksek verim", "high yield", "high-yield", "en onemli", "onemli nokta", "kritik nokta", "sinavda cikacak", "puf nokta")
_RAPID = ("hizli tekrar", "rapid review", "son tekrar", "cabuk tekrar", "sinavdan once", "quick review")
_COMPARE = ("karsilastir", "compare", "fark", "versus", "vs", "ayirt", "ayir", "benzerlik")
_PAGE_WORDS = ("sayfa", "page", "pp.", "pp ")
_PDF = ("pdf", "slayt", "slide", "belge", "dosya", "ders notu", "ders notlari", "dokuman", "document", "materyal", "material", "sunum", "lecture", "kitap", "chapter", "bolum")
_ANALYZE = ("analiz", "incele", "calis", "oku", "tara", "cikar", "islet", "process", "analy", "index", "dizinle")
_COMPARE_KNOWLEDGE = ("kendi bilgi", "standart bilgi", "tip bilgisi", "kitap bilgisi", "guncel bilgi", "medical knowledge", "eksik", "yanlis yer", "hatali", "tutarsiz", "celisk", "misleading", "incorrect")
_PROFESSOR = ("hoca", "profesor", "professor", "ogretim uyesi", "prof")
_STYLE = ("tarz", "stil", "style", "benzet", "benzer", "gibi", "kalip", "pattern", "profil", "profile", "nasil sor", "soru tipi")
_WEAK = ("zayif", "weak", "yanlis yaptigim", "yanlis cevapladigim", "yanlis bildigim", "eksik oldugum", "zorlandigim", "got wrong", "wrong ones", "bilemedigim")
_ORAL = ("sozlu", "oral")
_QUIZ = ("quiz", "beni sina", "sina beni", "soru sor", "sor bana", "test et", "quiz me", "ask me")
_ONE_AT_A_TIME = ("tek tek", "birer birer", "one at a time", "sirayla", "one by one")
_ANSWERS_AT_END = ("sonda", "sonunda", "en sonda", "at the end", "cevaplari gosterme", "cevabi gosterme", "hemen gosterme", "bitince goster")
_ANATOMY_LAB = ("anatomy lab", "anatomi lab", "anatomi laboratuvar", "3d", "3 boyutlu", "modeli", "model", "labda", "lab de", "laboratuvar")
_OPEN = ("open", "goster", "show", "getir", "yukle", "load")
# Matched as whole words: as a prefix, "aç" swallows "açıkla" (explain),
# "açık" and "acaba", turning explanations into Anatomy Lab commands.
_OPEN_WORDS = frozenset({"ac", "acar", "acin", "aciniz", "acsana", "acalim", "acabilir", "aciver"})
_HIGHLIGHT = ("isaretle", "vurgula", "highlight", "etiketle", "label", "belirt", "renklendir")
_LANDMARK_WORDS = ("landmark", "isaret", "yapi", "structure", "cikinti", "nokta")
_TERMINOLOGY = ("ne demek", "anlami", "anlamina", "terim", "translate", "cevir", "latince", "latin", "meaning", "define", "tanim")
_MUSCLE_FIELDS = ("origo", "insertio", "innervasyon", "innervatio", "fonksiyon", "functio", "origin", "insertion", "innervation", "action")
_TABLE = ("tablo", "table", "listele", "goster", "cikar", "ver")
_DETAILED = ("detayli", "ayrintili", "derinlemesine", "detailed", "in depth", "kapsamli")
_EXAM_FOCUS = ("sinav odakli", "sinav icin", "exam mode", "sinav modu", "sinava yonelik")
_NEXT = ("sonraki soru", "sıradaki soru", "siradaki", "next question", "devam et", "bir sonraki", "baska soru", "next", "devam")
_WHY_WRONG = ("neden yanlis", "niye yanlis", "why is", "why was", "neden dogru", "acikla", "neden b", "neden a", "neden c", "neden d", "neden e")
_NO_COPY = ("kopyalama", "kopya", "birebir", "aynisi olmasin", "ayni sorular", "do not copy", "don't copy", "not copy", "yeni sorular")
_HARDER = ("daha zor", "harder", "zorlastir")
_EASIER = ("daha kolay", "easier", "kolaylastir")
_TIMED = ("sureli", "zamanli", "timed", "kronometre", "sure tut")
_IMMEDIATE = ("hemen goster", "aninda", "immediate", "her sorudan sonra")
_MEDICAL_STEMS = (
    "kemik", "kemig", "eklem", "sinir", "damar", "arter", "hucre", "doku", "enzim",
    "protein", "bakteri", "virus", "mantar", "metabol", "hormon", "reseptor", "membran",
    "potansiyel", "difuzyon", "osmoz", "mitoz", "mayoz", "kromozom", "epitel", "kikirdak",
    "organel", "mitokondri", "glikoliz", "krebs", "anatomi", "fizyoloj", "histoloj",
    "biyokimya", "biyofizik", "mikrobiyoloj", "biyoloj", "tendon", "ligament", "fasya",
    "innervasyon", "origo", "insertio", "kasilma", "nefron", "alveol", "hemoglobin",
    "antikor", "antijen", "patojen", "enfeksiyon", "tibbi", "medikal", "medical",
    "muscle", "bone", "joint", "nerve", "cell", "tissue", "enzyme", "bacteria", "virus",
    "cartilage", "vertebra", "omurga", "kalp", "akciger", "bobrek", "karaciger", "beyin",
    "omuz", "dirsek", "kalca", "diz", "bilek", "uyluk", "kol", "bacak", "boyun", "gogus",
    "karin", "pelvis", "kafatas", "kranyum", "sindirim", "solunum", "dolasim", "bosaltim",
    "endokrin", "lenf", "immun", "bagisiklik", "genetik", "dna", "rna", "gen", "nukleotid",
    "amino", "lipid", "karbonhidrat", "vitamin", "glukoz", "insulin", "atp", "iyon",
    "sodyum", "potasyum", "kalsiyum", "gram", "spor", "kapsul", "flagel", "plazmid",
)
_MEDICAL_EXACT = frozenset({"kas", "kaslar", "kasi", "kasin", "kasini", "kaslari", "kaslarin", "kan", "hasta"})
_SUBJECT_NAMES = ("anatomi", "anatomy", "histoloj", "histolog", "mikrobiyoloj", "microbiolog", "biyokimya", "biochem", "biyofizik", "biophys", "fizyoloj", "physiolog", "biyoloj", "biolog")
_ANSWER_PATTERN = re.compile(r"^(?:cevap(?:im)?|yanit(?:im)?|answer|sik|secenek|option)?\s*[:\-]?\s*\(?([a-e])\)?\s*(?:sikki|secenegi|olsun)?[.!]?$")
_COUNT_PATTERN = re.compile(r"(\d{1,3})\s*(?:tane\s*|adet\s*)?(?:soru|question|mcq|sorudan|sorulu|soruluk)")
_OPTION_PATTERN = re.compile(r"(\d)\s*(?:sik|secenek|option|choice|sikli|secenekli)")
_DIFFICULTY_PATTERNS = (
    re.compile(r"zorluk(?:\s*seviyesi|\s*derecesi)?\s*[:=]?\s*(\d)"),
    re.compile(r"difficulty\s*(?:level)?\s*[:=]?\s*(\d)"),
    re.compile(r"\b(\d)\s*/\s*5\b"),
    re.compile(r"seviye\s*(\d)"),
)
_LECTURE_PATTERN = re.compile(r"(?:lecture|ders|kurs|hafta|week|bolum|chapter|konu)\s*(\d{1,3})|(\d{1,3})\s*\.\s*(?:ders|hafta|bolum)")


@dataclass(slots=True)
class StudyCommand:
    intent: str = MedicalIntent.NONE
    medical: bool = False
    confidence: str = "low"
    subject: str | None = None
    topic_id: str | None = None
    terms: list[TermMatch] = field(default_factory=list)
    structure_ids: list[str] = field(default_factory=list)
    concept_ids: list[str] = field(default_factory=list)
    landmark_ids: list[str] = field(default_factory=list)
    question_count: int | None = None
    option_count: int | None = None
    difficulty: int | None = None
    page_range: tuple[int, int] | None = None
    depth: str | None = None
    answers_at_end: bool = False
    one_at_a_time: bool = False
    wrong_only: bool = False
    professor_style: bool = False
    no_copy: bool = False
    harder: bool = False
    easier: bool = False
    timed: bool = False
    immediate_feedback: bool = False
    current_document: bool = False
    document_hint: str | None = None
    answer_key: str | None = None
    reasons: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def label(self) -> str:
        return INTENT_LABELS_TR.get(self.intent, self.intent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "label": self.label,
            "medical": self.medical,
            "confidence": self.confidence,
            "subject": self.subject,
            "topic_id": self.topic_id,
            "terms": [{"term_id": match.entry.term_id, "canonical": match.entry.canonical, "kind": match.entry.kind} for match in self.terms],
            "structure_ids": list(self.structure_ids),
            "concept_ids": list(self.concept_ids),
            "landmark_ids": list(self.landmark_ids),
            "question_count": self.question_count,
            "option_count": self.option_count,
            "difficulty": self.difficulty,
            "page_range": list(self.page_range) if self.page_range else None,
            "depth": self.depth,
            "flags": {
                "answers_at_end": self.answers_at_end,
                "one_at_a_time": self.one_at_a_time,
                "wrong_only": self.wrong_only,
                "professor_style": self.professor_style,
                "no_copy": self.no_copy,
                "harder": self.harder,
                "easier": self.easier,
                "timed": self.timed,
                "immediate_feedback": self.immediate_feedback,
                "current_document": self.current_document,
            },
            "document_hint": self.document_hint,
            "answer_key": self.answer_key,
            "reasons": list(self.reasons),
        }


def _contains(folded: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {folded} "
    return any(f" {phrase}" in padded for phrase in phrases)


def _wants_open(token_list: list[str]) -> bool:
    """Does the sentence ask for something to be opened in the lab?"""
    return has_stem(token_list, _OPEN) or bool(set(token_list) & _OPEN_WORDS)


def _wants_simple(folded: str, token_list: list[str]) -> bool:
    """Does the sentence ask for a simpler explanation?"""
    return _contains(folded, _SIMPLIFY) or bool(set(token_list) & _SIMPLIFY_WORDS)


class MedicalIntentParser:
    def __init__(
        self,
        curriculum: Curriculum,
        terminology: TerminologyIndex,
        concepts: ConceptGraph | None = None,
    ) -> None:
        self._curriculum = curriculum
        self._terminology = terminology
        self._concepts = concepts

    # ------------------------------------------------------------------

    def is_medical(self, text: str) -> bool:
        return self.parse(text).medical

    def parse(
        self,
        text: str,
        *,
        forced: bool = False,
        contextual: bool = False,
        session: StudySession | None = None,
    ) -> StudyCommand:
        """Parse one request.

        ``forced`` marks a request typed inside the Academy (always
        medical); ``contextual`` marks a request that follows recent
        study activity, so study-shaped follow-ups ("5 şıklı olsun",
        "cevapları en sonda ver") are understood without medical words.
        """
        command = StudyCommand(text=text)
        raw = str(text or "").strip()
        if not raw:
            return command
        folded = fold(raw)
        token_list = tokens(raw)
        word_count = len(token_list)
        active_quiz = bool(session and session.chat_quiz and session.chat_quiz.get("active"))

        # ---- interactive answers come first: "B", "cevap C", "sonraki soru"
        answer = _ANSWER_PATTERN.match(folded)
        if active_quiz and answer:
            command.intent = MedicalIntent.ANSWER
            command.answer_key = answer.group(1).upper()
            command.medical = True
            command.confidence = "high"
            command.reasons.append("active_quiz_answer")
            self._fill_session(command, session)
            return command
        if active_quiz and word_count <= 4 and _contains(folded, _NEXT):
            command.intent = MedicalIntent.NEXT_QUESTION
            command.medical = True
            command.confidence = "high"
            command.reasons.append("active_quiz_next")
            self._fill_session(command, session)
            return command
        if active_quiz and word_count <= 8 and _contains(folded, _WHY_WRONG):
            command.intent = MedicalIntent.WHY_WRONG
            command.medical = True
            command.confidence = "high"
            command.reasons.append("active_quiz_why")
            self._fill_session(command, session)
            return command

        # ---- entities
        matches = self._terminology.find_in_text(raw)
        command.terms = matches
        for match in matches:
            entry = match.entry
            if entry.landmark_of:
                command.landmark_ids.append(entry.term_id)
                if entry.landmark_of not in command.structure_ids:
                    command.structure_ids.append(entry.landmark_of)
            elif entry.structure_id and entry.structure_id not in command.structure_ids:
                command.structure_ids.append(entry.structure_id)
            if entry.concept_id and entry.concept_id not in command.concept_ids:
                command.concept_ids.append(entry.concept_id)
        if self._concepts is not None:
            for concept in self._concepts.find(raw):
                if concept.concept_id not in command.concept_ids:
                    command.concept_ids.append(concept.concept_id)

        # ---- subject and topic
        keyword_subject, subject_basis = self._curriculum.resolve_subject_detail(raw)
        explicit_subject = keyword_subject if subject_basis == "name" else None
        subject = explicit_subject
        term_subject = None
        term_topic = None
        for match in matches:
            if match.entry.structure_id:
                term_subject = "anatomy"
            concept = self._concepts.get(match.entry.concept_id) if self._concepts is not None else None
            if concept is not None:
                term_subject = term_subject or concept.subject
                term_topic = term_topic or concept.topic_id
        if self._concepts is not None and term_topic is None:
            for concept_id in command.concept_ids:
                concept = self._concepts.get(concept_id)
                if concept is not None and concept.topic_id:
                    term_subject = term_subject or concept.subject
                    term_topic = concept.topic_id
                    break
        if subject is None:
            subject = term_subject or keyword_subject
        if subject is None and session is not None and (forced or contextual or active_quiz):
            subject = session.subject
        command.subject = subject
        if term_topic and (subject is None or term_topic.startswith(subject)):
            command.topic_id = term_topic
        else:
            found = self._curriculum.search(raw, subject=subject, limit=1)
            if found and found[0].parent_id is not None:
                command.topic_id = found[0].topic_id
        if command.topic_id is None and session is not None and forced and session.topic_id:
            command.topic_id = session.topic_id

        # ---- medical or not
        medical_stems = has_stem(token_list, _MEDICAL_STEMS) or bool(set(token_list) & _MEDICAL_EXACT)
        subject_named = has_stem(token_list, _SUBJECT_NAMES)
        strong_terms = [match for match in matches if match.entry.kind != "term"]
        # Study-shaped follow-ups ("20 soru hazırla", "cevapları sonda
        # ver", "hocanın tarzında") belong to the Academy even without a
        # medical word; plain system commands never do.
        # A page range only marks a turn as study-shaped when the sentence
        # says "sayfa"/"page": a bare pair is far more often a volume, a
        # brightness, a file count or a clock ("sesi 20-40 arası ayarla"),
        # and taking those would break the rest of JARVIS. Inside an active
        # study context the bare form is still read as a scope below.
        page_range = parse_page_range(raw)
        strong_study_marker = (
            _COUNT_PATTERN.search(folded) is not None
            or _OPTION_PATTERN.search(folded) is not None
            or any(pattern.search(folded) for pattern in _DIFFICULTY_PATTERNS)
            or (page_range is not None and _contains(folded, _PAGE_WORDS))
            or has_stem(token_list, ("sinav", "exam", "quiz", "mcq"))
            or _contains(folded, _PROFESSOR)
            or bool(set(token_list) & _QUIZ_TOKENS)
        )
        study_shaped = strong_study_marker or (
            has_stem(token_list, _STUDY_SHAPED)
            and (
                has_stem(token_list, _GENERATE + _ANALYZE + _HIGHLIGHT)
                or _contains(folded, _QUIZ + _ORAL + _ANSWERS_AT_END + _ONE_AT_A_TIME + _WEAK + _NO_COPY + _HARDER + _EASIER + _HIGH_YIELD + _RAPID + _COMPARE_KNOWLEDGE)
            )
        ) or (
            contextual
            and _contains(folded, _COMPARE_KNOWLEDGE + _ANSWERS_AT_END + _ONE_AT_A_TIME + _NO_COPY + _HARDER + _EASIER + _NEXT)
        )
        intrinsically_medical = bool(
            forced or explicit_subject or strong_terms or command.concept_ids or subject_named or medical_stems
        )
        command.medical = intrinsically_medical or (study_shaped and (contextual or strong_study_marker))
        if forced or explicit_subject or subject_named or (strong_terms and medical_stems):
            command.confidence = "high"
        elif strong_terms or command.concept_ids or medical_stems or (study_shaped and (contextual or strong_study_marker)):
            command.confidence = "medium"
        else:
            command.confidence = "low"
        if not command.medical:
            command.intent = MedicalIntent.NONE
            return command

        # ---- constraints
        count = _COUNT_PATTERN.search(folded)
        if count:
            command.question_count = max(1, min(100, int(count.group(1))))
        options = _OPTION_PATTERN.search(folded)
        if options:
            command.option_count = max(2, min(6, int(options.group(1))))
        for pattern in _DIFFICULTY_PATTERNS:
            found_difficulty = pattern.search(folded)
            if found_difficulty:
                command.difficulty = max(1, min(5, int(found_difficulty.group(1))))
                break
        if command.difficulty is None:
            if _contains(folded, ("cok zor", "very hard", "en zor")):
                command.difficulty = 5
            elif _contains(folded, ("cok kolay", "very easy", "en kolay")):
                command.difficulty = 1
            elif _contains(folded, _HARDER):
                command.harder = True
            elif _contains(folded, _EASIER):
                command.easier = True
            elif has_stem(token_list, ("zor",)) and _contains(folded, _QUESTION_WORDS):
                command.difficulty = 4
            elif has_stem(token_list, ("kolay",)) and _contains(folded, _QUESTION_WORDS):
                command.difficulty = 2
        command.page_range = page_range
        command.answers_at_end = _contains(folded, _ANSWERS_AT_END) and ("cevap" in folded or "answer" in folded or "yanit" in folded)
        command.one_at_a_time = _contains(folded, _ONE_AT_A_TIME)
        command.wrong_only = _contains(folded, _WEAK) and (_contains(folded, _QUESTION_WORDS) or _contains(folded, ("konu", "alan", "tekrar", "topic")))
        command.no_copy = _contains(folded, _NO_COPY)
        command.timed = _contains(folded, _TIMED)
        command.immediate_feedback = _contains(folded, _IMMEDIATE)
        command.professor_style = _contains(folded, _PROFESSOR) and (_contains(folded, _STYLE) or _contains(folded, ("sorular", "sorusu", "sorulari", "questions", "eski")))
        command.current_document = _contains(
            folded,
            ("bu pdf", "bu slayt", "bu belge", "bu dosya", "bu ders", "bu materyal", "bu sunum", "bu notlar",
             "yukledigim", "this pdf", "this document", "this lecture", "attigim", "buradaki", "burada", "bunlarda"),
        )
        lecture = _LECTURE_PATTERN.search(folded)
        if lecture:
            command.document_hint = lecture.group(1) or lecture.group(2)
        if _contains(folded, _DETAILED):
            command.depth = DepthLevel.DETAILED
        elif _contains(folded, _EXAM_FOCUS):
            command.depth = DepthLevel.EXAM
        elif _wants_simple(folded, token_list):
            command.depth = DepthLevel.SIMPLE
        elif _contains(folded, _RAPID):
            command.depth = DepthLevel.RAPID

        command.intent = self._classify(folded, token_list, word_count, command, active_quiz)
        if command.intent == MedicalIntent.MUSCLE_TABLE and not explicit_subject:
            command.subject = "anatomy"
            if command.topic_id and not command.topic_id.startswith("anatomy"):
                command.topic_id = None
        self._fill_session(command, session)
        return command

    # ------------------------------------------------------------------

    def _fill_session(self, command: StudyCommand, session: StudySession | None) -> None:
        if session is None:
            return
        if command.subject is None:
            command.subject = session.subject
        if command.topic_id is None and command.subject == session.subject:
            command.topic_id = session.topic_id

    def _classify(
        self,
        folded: str,
        token_list: list[str],
        word_count: int,
        command: StudyCommand,
        active_quiz: bool,
    ) -> str:
        has_structure = bool(command.structure_ids)
        question_words = _contains(folded, _QUESTION_WORDS) or command.question_count is not None
        generate = has_stem(token_list, _GENERATE)
        pdf_words = _contains(folded, _PDF) or command.current_document or command.page_range is not None

        # Anatomy Lab actions
        lab = _contains(folded, _ANATOMY_LAB)
        if has_structure and (lab or (word_count <= 8 and _wants_open(token_list))):
            if _contains(folded, _QUIZ) or (question_words and lab):
                command.reasons.append("anatomy_quiz")
                return MedicalIntent.ANATOMY_QUIZ
            if has_stem(token_list, _HIGHLIGHT) or command.landmark_ids:
                command.reasons.append("anatomy_highlight")
                return MedicalIntent.ANATOMY_HIGHLIGHT
            if lab or _wants_open(token_list):
                command.reasons.append("anatomy_open")
                return MedicalIntent.ANATOMY_OPEN
        if has_structure and has_stem(token_list, _HIGHLIGHT):
            command.reasons.append("anatomy_highlight")
            return MedicalIntent.ANATOMY_HIGHLIGHT

        # Professor style
        professor = _contains(folded, _PROFESSOR)
        if professor and (question_words or generate) and (command.professor_style or _contains(folded, _STYLE)):
            command.professor_style = True
            command.reasons.append("professor_style_generation")
            return MedicalIntent.PROFESSOR_STYLE_EXAM
        if professor and (_contains(folded, _STYLE) or _contains(folded, ("analiz", "analy", "ogren", "learn", "incele"))):
            command.reasons.append("professor_profile")
            return MedicalIntent.PROFESSOR_PROFILE

        # Documents
        if pdf_words and (_contains(folded, _COMPARE_KNOWLEDGE) or (_contains(folded, _COMPARE) and not has_structure)):
            command.reasons.append("pdf_compare")
            return MedicalIntent.PDF_COMPARE
        if pdf_words and question_words and (generate or command.question_count is not None):
            command.reasons.append("exam_from_document")
            return MedicalIntent.EXAM_GENERATE
        if pdf_words and (has_stem(token_list, _ANALYZE) or command.page_range is not None) and not _contains(folded, _NOTES) and not question_words:
            command.reasons.append("pdf_analyze")
            return MedicalIntent.PDF_ANALYZE

        # Weakness
        if command.wrong_only:
            if question_words or generate:
                command.reasons.append("weak_area_questions")
                return MedicalIntent.EXAM_GENERATE
            command.reasons.append("review_weakness")
            return MedicalIntent.REVIEW_WEAKNESS

        # Questions
        if _contains(folded, _ORAL):
            command.reasons.append("oral_exam")
            return MedicalIntent.ORAL_EXAM
        if (_contains(folded, _QUIZ) or set(token_list) & _QUIZ_TOKENS) and not (
            generate and command.question_count and command.question_count > 5
        ):
            command.reasons.append("quiz")
            return MedicalIntent.QUIZ
        if question_words and (generate or command.question_count is not None or command.option_count is not None):
            if command.one_at_a_time and (command.question_count or 0) <= 10 and not command.answers_at_end:
                command.reasons.append("interactive_questions")
                return MedicalIntent.QUIZ
            command.reasons.append("exam_generate")
            return MedicalIntent.EXAM_GENERATE
        if active_quiz and _contains(folded, _NEXT):
            return MedicalIntent.NEXT_QUESTION

        # Study modes
        if _contains(folded, _NOTES) and (has_stem(token_list, ("cikar", "hazirla", "olustur", "make", "yap", "ver", "al")) or _contains(folded, ("kisa not", "short note", "revision note", "tek sayfa", "one page", "ozet not"))):
            command.reasons.append("short_notes")
            return MedicalIntent.SHORT_NOTES
        if _contains(folded, _HIGH_YIELD):
            command.reasons.append("high_yield")
            return MedicalIntent.HIGH_YIELD
        if _contains(folded, _RAPID):
            command.reasons.append("rapid_review")
            return MedicalIntent.RAPID_REVIEW
        if _contains(folded, _COMPARE) and (len(command.concept_ids) >= 2 or len(command.structure_ids) >= 2 or " ile " in f" {folded} " or " ve " in f" {folded} " or " vs " in f" {folded} "):
            command.reasons.append("compare")
            return MedicalIntent.COMPARE
        if has_stem(token_list, _SUMMARY) and not _contains(folded, _NOTES):
            command.reasons.append("summarize")
            return MedicalIntent.SUMMARIZE
        muscle_fields = sum(1 for field_name in _MUSCLE_FIELDS if field_name in folded)
        if muscle_fields >= 2:
            command.reasons.append("muscle_table")
            return MedicalIntent.MUSCLE_TABLE
        if _contains(folded, _TERMINOLOGY) and command.terms:
            command.reasons.append("terminology")
            return MedicalIntent.TERMINOLOGY
        if command.terms and word_count <= 3 and not has_stem(token_list, _EXPLAIN):
            command.reasons.append("bare_term")
            return MedicalIntent.TERMINOLOGY
        if _wants_simple(folded, token_list):
            command.reasons.append("simplify")
            return MedicalIntent.SIMPLIFY
        if has_stem(token_list, _EXPLAIN) or _contains(folded, ("hakkinda", "about")):
            command.reasons.append("explain")
            return MedicalIntent.EXPLAIN
        command.reasons.append("general_medical")
        return MedicalIntent.GENERAL


def describe_command(command: StudyCommand) -> str:
    """One Turkish line for the ledger and the context panel."""
    parts = [command.label]
    if command.subject:
        parts.append(command.subject)
    if command.question_count:
        parts.append(f"{command.question_count} soru")
    if command.option_count:
        parts.append(f"{command.option_count} şık")
    if command.difficulty:
        parts.append(f"zorluk {command.difficulty}")
    if command.page_range:
        parts.append(f"s. {command.page_range[0]}–{command.page_range[1]}")
    if command.structure_ids:
        parts.append(", ".join(command.structure_ids[:3]))
    return " · ".join(parts)
