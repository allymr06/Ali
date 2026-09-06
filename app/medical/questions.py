"""Question quality, grading, similarity protection and exam analysis.

Everything here is deterministic code: validation of generated items,
letter shuffling, scoring, breakdowns, similarity against imported
professor questions. The model writes questions; this module decides
whether they are fit to show.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Iterable
from typing import Any

from app.core.time import utc_now
from app.medical.models import (
    DIFFICULTY_LABELS_TR,
    Difficulty,
    Exam,
    ExamAttempt,
    Question,
    QuestionAttempt,
    QuestionOption,
    QuestionOrigin,
    QuestionType,
    SourceReference,
    SUBJECT_LABELS_TR,
    new_id,
)
from app.medical.text import fold, jaccard, normalize, similarity, stems

OPTION_KEYS = "ABCDEF"
SIMILARITY_THRESHOLD = 0.45
# Words that stand for the whole option list rather than for an answer. Matched as
# whole tokens: the old substring list missed the ordinary Turkish spellings
# ("tümü", "tamamı", "her ikisi") and threw out the legitimate biochemistry option
# "Vitamin A ve B12 eksikliği" because it contains the letters "a ve b".
CATCH_ALL_OPTION_WORDS = frozenset(
    {
        "hepsi", "hepsini", "hepsidir", "hepsinin", "hepsinde",
        "tumu", "tumunu", "tumudur", "tumunun",
        "tamami", "tamamini", "tamamidir", "tamaminin",
        "hicbiri", "hicbirisi", "hicbiridir", "hicbirinin", "hicbirinde",
        "all", "none", "both",
    }
)
# "Yukarıdakilerin tümü" / "aşağıdakilerin hiçbiri": the plural genitive only ever
# points at the option list. The singular ("yukarıdaki şekil") is left alone.
CATCH_ALL_LIST_PREFIXES = ("yukaridakiler", "asagidakiler")
# "Hiçbir şık doğru değildir" needs both halves: "hiçbir kas buraya tutunmaz" is a
# real answer, "hiçbir şık" is a statement about the paper.
OPTION_NOUN_PREFIXES = ("sik", "secene", "ifade", "ceva")
OPTION_LETTERS = frozenset("abcdef")
LETTER_JOINERS = frozenset({"ve", "veya", "ile", "ya", "hem", "veyahut"})
LETTER_LEADERS = frozenset({"yalniz", "yalnizca", "sadece"})
LETTER_TRAILERS = frozenset(
    {"dogru", "dogrudur", "dogrudurlar", "yanlis", "yanlistir", "de", "da",
     "secenegi", "secenekleri", "sikki", "siklari", "ifadeleri"}
)
MIN_STEM_CHARS = 15
MIN_EXPLANATION_CHARS = 20


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _is_catch_all_option(words: list[str]) -> bool:
    """Does the option stand for the whole list — hepsi, yukarıdakilerin tümü,
    hiçbir şık doğru değildir, her ikisi de doğrudur?"""
    if any(word in CATCH_ALL_OPTION_WORDS for word in words):
        return True
    if any(word.startswith(CATCH_ALL_LIST_PREFIXES) for word in words):
        return True
    if any(word.startswith("hicbir") for word in words) and any(word.startswith(OPTION_NOUN_PREFIXES) for word in words):
        return True
    return "ikisi" in words and bool({"her", "de", "da"} & set(words))


def _references_option_letters(words: list[str]) -> bool:
    """Does the option name other options by letter — B ve C, A ve B doğrudur?

    ``shuffle_options`` re-letters every generated paper, so such an option ends up
    pointing at options nobody meant, and its stated answer becomes false. The whole
    option must be the letter run: "Vitamin A ve B12 eksikliği" keeps a word that is
    not a letter, and "A ve D vitaminleri" keeps its noun, so neither is caught.
    """
    core = [word for word in words if word not in LETTER_LEADERS and word not in LETTER_TRAILERS]
    letters = [word for word in core if word in OPTION_LETTERS]
    joiners = [word for word in core if word in LETTER_JOINERS]
    return len(letters) >= 2 and len(letters) + len(joiners) == len(core)


def validate_question(
    question: Question,
    *,
    expected_options: int | None = None,
    allow_all_of_the_above: bool = False,
    require_explanation: bool = True,
) -> list[str]:
    """Problems that make a question unfit for a student; empty when fine."""
    problems: list[str] = []
    stem = " ".join(question.stem.split())
    if len(stem) < MIN_STEM_CHARS:
        problems.append("stem_too_short")
    options = question.options
    if len(options) < 2:
        problems.append("too_few_options")
    if expected_options is not None and len(options) != expected_options:
        problems.append(f"option_count_{len(options)}_expected_{expected_options}")
    keys = [option.key.upper() for option in options]
    if keys != list(OPTION_KEYS[: len(options)]):
        problems.append("option_keys_not_sequential")
    normalized_texts = [normalize(option.text) for option in options]
    if any(not text for text in normalized_texts):
        problems.append("empty_option")
    if len(set(normalized_texts)) != len(normalized_texts):
        problems.append("duplicate_options")
    option_words = [text.split() for text in normalized_texts]
    if not allow_all_of_the_above and any(_is_catch_all_option(words) for words in option_words):
        problems.append("all_or_none_option")
    # This one has no relaxation: an option that names other options by letter is broken
    # by the shuffle, not merely undesirable. An imported professor question keeps the
    # letters its author wrote and is never re-lettered, so its "A ve B" is authentic.
    if question.origin != QuestionOrigin.IMPORTED_EXAM and any(_references_option_letters(words) for words in option_words):
        problems.append("option_references_another_option")
    correct = question.option(question.correct_key or "")
    if question.correct_key is None:
        problems.append("missing_answer_key")
    elif correct is None:
        problems.append("answer_key_not_an_option")
    else:
        others = [len(option.text) for option in options if option is not correct]
        if others:
            median = statistics.median(others)
            if len(correct.text) > 40 and len(correct.text) > 1.6 * max(median, 1):
                problems.append("correct_option_longest")
        folded_stem = fold(stem)
        folded_correct = fold(correct.text)
        if len(folded_correct) >= 12 and folded_correct in folded_stem:
            problems.append("stem_contains_answer")
    if require_explanation and len(" ".join(question.explanation.split())) < MIN_EXPLANATION_CHARS:
        problems.append("explanation_missing")
    if not 1 <= int(question.difficulty) <= 5:
        problems.append("difficulty_out_of_range")
    return problems


def shuffle_options(question: Question, *, seed: str | None = None) -> Question:
    """Re-letter options in a seeded random order (answers must not cluster on A)."""
    if question.correct_key is None or len(question.options) < 2:
        return question
    rng = random.Random(seed or question.question_id)
    order = list(question.options)
    rng.shuffle(order)
    correct_text = question.option(question.correct_key)
    new_options: list[QuestionOption] = []
    new_key = question.correct_key
    for index, option in enumerate(order):
        key = OPTION_KEYS[index]
        if correct_text is not None and option is correct_text:
            new_key = key
        new_options.append(QuestionOption(key=key, text=option.text, concept=option.concept, explanation=option.explanation))
    question.options = new_options
    question.correct_key = new_key
    return question


def _stated_number(value: Any) -> int:
    """A positive integer the model stated, or 0 when it stated nothing usable."""
    try:
        number = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def build_question(
    raw: dict[str, Any],
    *,
    subject: str,
    topic_id: str | None,
    difficulty: int,
    origin: str = QuestionOrigin.GENERATED,
    professor_id: str | None = None,
    references: Iterable[SourceReference] = (),
    document_title: str = "",
    concept_ids: Iterable[str] = (),
    option_count: int | None = None,
) -> Question:
    """Turn one generated item into a Question (still to be validated)."""
    options: list[QuestionOption] = []
    for item in raw.get("options", []) or []:
        if not isinstance(item, dict):
            continue
        if len(options) == len(OPTION_KEYS):
            break  # A..F is every letter an option can wear; an overflow one is dropped, not left keyless.
        key = str(item.get("key", "")).strip().upper() or OPTION_KEYS[len(options)]
        options.append(
            QuestionOption(
                key=key,
                text=" ".join(str(item.get("text", "")).split()),
                concept=str(item.get("concept") or "").strip(),
                explanation=" ".join(str(item.get("explanation") or "").split()),
            )
        )
    # Normalise letters to A.. in the order given, keeping the correct one attached to its text.
    correct_raw = str(raw.get("correct_key", "")).strip().upper()
    correct_text = next((option.text for option in options if option.key == correct_raw), None)
    for index, option in enumerate(options):
        option.key = OPTION_KEYS[index]
    correct_key = next((option.key for option in options if option.text == correct_text), None) if correct_text else None
    if option_count is not None and len(options) > option_count:
        # Trim from the tail, keeping only a seat for the answer: pulling it forward would put every
        # trimmed question's answer on A, and the bank shows what this function produced.
        answer = next((option for option in options if option.key == correct_key), None)
        spare = option_count - (1 if answer is not None else 0)
        keep: list[QuestionOption] = []
        for option in options:
            if option is answer:
                keep.append(option)
            elif spare > 0:
                keep.append(option)
                spare -= 1
        options = keep
        for index, option in enumerate(options):
            if option is answer:
                correct_key = OPTION_KEYS[index]
            option.key = OPTION_KEYS[index]
    stated_difficulty = raw.get("difficulty")
    try:
        actual_difficulty = int(stated_difficulty) if stated_difficulty else int(difficulty)
    except (TypeError, ValueError):
        actual_difficulty = int(difficulty)
    actual_difficulty = max(1, min(5, actual_difficulty))
    refs = list(references)
    page_number = _stated_number(raw.get("source_page"))
    source_index = _stated_number(raw.get("source_index"))
    if source_index:
        # The model names the excerpt by its [Kaynak N] label, which is the only handle
        # that is unique: two documents routinely share a page number, and resolving by
        # page alone attaches a real title and page to material the question never used.
        chosen = refs[source_index - 1] if source_index <= len(refs) else None
        # The page is the cross-check on the index, and the prompt asks for both:
        # an index with no page rests on one unverified claim, so it cites nothing.
        refs = [chosen] if chosen is not None and page_number == chosen.page_number else []
    elif page_number and refs:
        matching = [ref for ref in refs if ref.page_number == page_number]
        # One document on that page: cite it. Several: nothing here can tell them apart,
        # and a missing citation is honest where a wrong one is not.
        refs = matching[:1] if len({ref.document_id for ref in matching}) == 1 else []
    else:
        refs = []
    if refs:
        refs[0] = SourceReference(
            document_id=refs[0].document_id,
            page_number=refs[0].page_number,
            chunk_id=refs[0].chunk_id,
            quote=" ".join(str(raw.get("source_quote") or refs[0].quote).split())[:300],
            title=refs[0].title or document_title,
        )
    elif origin == QuestionOrigin.LECTURE_DERIVED:
        # The badge follows the reference that survived verification. A page the model
        # invented must not leave the item claiming a lecture origin it cannot show.
        origin = QuestionOrigin.GENERATED
    question_type = str(raw.get("question_type") or QuestionType.SINGLE_BEST_ANSWER)
    if question_type not in {item.value for item in QuestionType}:
        question_type = QuestionType.SINGLE_BEST_ANSWER
    concept = " ".join(str(raw.get("concept") or "").split())
    metadata: dict[str, Any] = {}
    if raw.get("trap"):
        metadata["trap"] = " ".join(str(raw["trap"]).split())[:400]
    if raw.get("topic"):
        metadata["topic_hint"] = " ".join(str(raw["topic"]).split())[:160]
    if concept:
        metadata["concept_name"] = concept
    return Question(
        question_id=new_id("q"),
        subject=subject,
        stem=" ".join(str(raw.get("stem", "")).split()),
        options=options,
        correct_key=correct_key,
        topic_id=topic_id,
        concept_ids=list(concept_ids),
        difficulty=actual_difficulty,
        question_type=question_type,
        explanation=" ".join(str(raw.get("explanation", "")).split()),
        references=refs,
        origin=origin,
        professor_id=professor_id,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# grading and similarity
# ---------------------------------------------------------------------------


def grade(question: Question, answer_key: str | None) -> bool | None:
    """True/False, or None when the question has no answer key."""
    if not question.correct_key:
        return None
    if answer_key is None:
        return False
    return str(answer_key).strip().upper() == question.correct_key.upper()


def question_text(question: Question) -> str:
    return question.stem + " " + " ".join(option.text for option in question.options)


def most_similar(question: Question, others: Iterable[Question]) -> tuple[float, str | None]:
    best = 0.0
    best_id: str | None = None
    text = question_text(question)
    # Stems, not raw word forms: Turkish inflection alone ("humerus"/"humerusun",
    # "bulunur"/"bulunmaktadır") is enough to drop a re-emitted question below the
    # threshold, which is exactly the rewrite this shortcut exists to catch.
    stem_stems = set(stems(question.stem))
    for other in others:
        if other.question_id == question.question_id:
            continue
        score = max(similarity(question.stem, other.stem), similarity(text, question_text(other)))
        # Same answer text plus a largely shared stem is a rewrite, not a new item.
        if question.correct_key and other.correct_key:
            mine = question.option(question.correct_key)
            theirs = other.option(other.correct_key)
            if mine is not None and theirs is not None and normalize(mine.text) == normalize(theirs.text):
                score = max(score, 0.9 * jaccard(stem_stems, set(stems(other.stem))))
        if score > best:
            best, best_id = score, other.question_id
    return best, best_id


def is_too_similar(question: Question, others: Iterable[Question], *, threshold: float = SIMILARITY_THRESHOLD) -> tuple[bool, float, str | None]:
    score, other_id = most_similar(question, others)
    return score >= threshold, score, other_id


# ---------------------------------------------------------------------------
# payloads
# ---------------------------------------------------------------------------


def figure_payload(question: Question) -> dict[str, Any] | None:
    """The lecture page a figure question is about, or None.

    ``image_ref`` holds ``document_id|page_number`` -- the same key the page
    reader uses, so the exam runner can fetch the rendered page. Nothing is
    drawn by JARVIS itself: the figure is always one of the student's own
    pages, and an unreadable reference yields no figure rather than a guess.
    """
    ref = str(question.image_ref or "")
    if "|" not in ref:
        return None
    document_id, _, page = ref.partition("|")
    if not document_id.strip() or not page.strip().isdigit():
        return None
    return {
        "document_id": document_id.strip(),
        "page_number": int(page),
        "title": str(question.metadata.get("figure_title") or ""),
        "caption": str(question.metadata.get("figure_caption") or ""),
    }


def question_payload(
    question: Question,
    *,
    reveal: bool = False,
    include_explanation: bool = False,
    curriculum: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question_id": question.question_id,
        "subject": question.subject,
        "subject_label": SUBJECT_LABELS_TR.get(question.subject, question.subject),
        "topic_id": question.topic_id,
        "topic_label": curriculum.breadcrumb(question.topic_id) if (curriculum is not None and question.topic_id) else "",
        "stem": question.stem,
        "options": [{"key": option.key, "text": option.text} for option in question.options],
        "difficulty": int(question.difficulty),
        "difficulty_label": DIFFICULTY_LABELS_TR.get(int(question.difficulty), str(question.difficulty)),
        "question_type": question.question_type,
        "origin": question.origin,
        "professor_id": question.professor_id,
        "has_answer_key": question.has_answer_key,
        "image_ref": question.image_ref,
        # A figure question shows the student's own lecture page beside the
        # stem; image_ref is "document_id|page_number", the page reader's key.
        "figure": figure_payload(question),
        "concept_ids": list(question.concept_ids),
        "concept_name": str(question.metadata.get("concept_name") or ""),
        "references": [
            {"document_id": ref.document_id, "page_number": ref.page_number, "title": ref.title, "quote": ref.quote, "chunk_id": ref.chunk_id}
            for ref in question.references
        ],
        "created_at": question.created_at.isoformat(),
        "tags": list(question.tags),
    }
    if reveal:
        payload["correct_key"] = question.correct_key
    if include_explanation:
        payload["explanation"] = question.explanation
        payload["option_explanations"] = {option.key: option.explanation for option in question.options if option.explanation}
        payload["trap"] = str(question.metadata.get("trap") or "")
    return payload


def explanation_payload(question: Question, chosen_key: str | None) -> dict[str, Any]:
    correct = question.option(question.correct_key or "")
    chosen = question.option(chosen_key or "") if chosen_key else None
    return {
        "question_id": question.question_id,
        "correct_key": question.correct_key,
        "correct_text": correct.text if correct else None,
        "chosen_key": chosen.key if chosen else None,
        "chosen_text": chosen.text if chosen else None,
        "correct": grade(question, chosen_key),
        "why_correct": question.explanation,
        "why_chosen_wrong": (chosen.explanation if chosen and chosen is not correct else ""),
        "other_options": [
            {"key": option.key, "text": option.text, "why_wrong": option.explanation}
            for option in question.options
            if option is not correct
        ],
        "concept": str(question.metadata.get("concept_name") or ""),
        "trap": str(question.metadata.get("trap") or ""),
        "references": [{"document_id": ref.document_id, "page_number": ref.page_number, "title": ref.title} for ref in question.references],
    }


def format_question_text(question: Question, *, number: int | None = None) -> str:
    """A question as chat text (no answer)."""
    header = f"**Soru {number}**" if number else "**Soru**"
    lines = [f"{header} · {SUBJECT_LABELS_TR.get(question.subject, question.subject)} · zorluk {question.difficulty}/5", "", question.stem, ""]
    lines.extend(f"{option.key}) {option.text}" for option in question.options)
    return "\n".join(lines)


def format_feedback_text(question: Question, chosen_key: str | None, *, detailed: bool = False) -> str:
    correct = question.option(question.correct_key or "")
    result = grade(question, chosen_key)
    if result is None:
        return "Bu sorunun cevap anahtarı yok; değerlendiremiyorum."
    lines: list[str] = []
    if result:
        lines.append(f"✅ Doğru: **{question.correct_key}) {correct.text if correct else ''}**")
    else:
        chosen = question.option(chosen_key or "")
        lines.append(f"❌ Yanlış. Doğru cevap **{question.correct_key}) {correct.text if correct else ''}**.")
        if chosen is not None and chosen.explanation:
            lines.append(f"Senin seçtiğin {chosen.key}) neden yanlış: {chosen.explanation}")
    if question.explanation:
        lines.append(question.explanation)
    if detailed:
        for option in question.options:
            if option is not correct and option.explanation:
                lines.append(f"- {option.key}) {option.explanation}")
    trap = str(question.metadata.get("trap") or "")
    if trap and (detailed or not result):
        lines.append(f"Tuzak: {trap}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# exam analysis
# ---------------------------------------------------------------------------


def analyse_attempt(
    exam: Exam,
    questions: list[Question],
    attempt: ExamAttempt,
    *,
    curriculum: Any | None = None,
    mastery_levels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Score plus useful breakdowns; deterministic and explainable."""
    by_id = {question.question_id: question for question in questions}
    ordered = [by_id[question_id] for question_id in exam.question_ids if question_id in by_id]
    gradable = [question for question in ordered if question.has_answer_key]
    correct_ids: list[str] = []
    wrong_ids: list[str] = []
    unanswered_ids: list[str] = []
    for question in gradable:
        answer = attempt.answers.get(question.question_id)
        if answer is None or answer.answer_key is None:
            unanswered_ids.append(question.question_id)
        elif grade(question, answer.answer_key):
            correct_ids.append(question.question_id)
        else:
            wrong_ids.append(question.question_id)
    total = len(gradable)
    score = round(len(correct_ids) / total, 4) if total else None

    def breakdown(key_of) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for question in gradable:
            key, label = key_of(question)
            bucket = buckets.setdefault(key, {"key": key, "label": label, "total": 0, "correct": 0})
            bucket["total"] += 1
            if question.question_id in correct_ids:
                bucket["correct"] += 1
        rows = list(buckets.values())
        for row in rows:
            row["accuracy"] = round(row["correct"] / row["total"], 3) if row["total"] else None
        rows.sort(key=lambda row: (row["accuracy"] if row["accuracy"] is not None else 2, row["label"]))
        return rows

    def topic_label(question: Question) -> tuple[str, str]:
        if question.topic_id and curriculum is not None:
            return question.topic_id, curriculum.breadcrumb(question.topic_id)
        return question.topic_id or "unknown", question.topic_id or "Konu belirsiz"

    by_subject = breakdown(lambda q: (q.subject, SUBJECT_LABELS_TR.get(q.subject, q.subject)))
    by_topic = breakdown(topic_label)
    by_difficulty = breakdown(lambda q: (str(int(q.difficulty)), DIFFICULTY_LABELS_TR.get(int(q.difficulty), str(q.difficulty))))

    concept_stats: dict[str, dict[str, Any]] = {}
    for question in gradable:
        names = list(question.concept_ids) or [f"topic:{question.topic_id or 'unknown'}"]
        label = str(question.metadata.get("concept_name") or "")
        for concept_id in names:
            stats = concept_stats.setdefault(concept_id, {"concept_id": concept_id, "label": label or concept_id, "total": 0, "correct": 0, "question_ids": []})
            stats["total"] += 1
            stats["question_ids"].append(question.question_id)
            if question.question_id in correct_ids:
                stats["correct"] += 1
    weak: list[dict[str, Any]] = []
    strong: list[dict[str, Any]] = []
    levels = mastery_levels or {}
    for stats in concept_stats.values():
        accuracy = stats["correct"] / stats["total"] if stats["total"] else 0.0
        stats["accuracy"] = round(accuracy, 3)
        stats["mastery"] = levels.get(stats["concept_id"], "unknown")
        if stats["total"] >= 1 and accuracy < 0.5:
            weak.append(stats)
        elif stats["total"] >= 2 and accuracy == 1.0:
            strong.append(stats)
    weak.sort(key=lambda item: (item["accuracy"], -item["total"]))
    strong.sort(key=lambda item: -item["total"])

    flagged = [question_id for question_id, answer in attempt.answers.items() if answer.flagged]
    review_ids = list(dict.fromkeys(wrong_ids + flagged + unanswered_ids))
    weakest_topic = next((row for row in by_topic if row["accuracy"] is not None and row["accuracy"] < 0.7), None)
    suggestion: dict[str, Any] | None = None
    if not wrong_ids and unanswered_ids:
        # Nothing was answered wrongly, so no topic can be called weak and
        # "redo your mistakes" would point at an empty set: what the paper
        # shows is the questions left blank.
        suggestion = {"kind": "answer_blanks", "text": f"{len(unanswered_ids)} soru boş kaldı: aynı sınavı yeniden başlatıp boş bıraktıklarını da cevapla, sonuç ancak o zaman bir şey söyler."}
    elif weakest_topic is not None and weakest_topic["key"] != "unknown":
        suggestion = {
            "kind": "weak_topic",
            "topic_id": weakest_topic["key"],
            "label": weakest_topic["label"],
            "text": f"Sıradaki çalışma: {weakest_topic['label']} konusunu tekrar et, ardından bu konudan zorluk {max(1, int(exam.config.difficulty))} ile yeni bir test çöz.",
        }
    elif score is not None and score >= 0.85:
        suggestion = {
            "kind": "harder",
            "text": "Sonuç güçlü: bir sonraki testte zorluğu bir kademe artırabilirsin.",
        }
    elif wrong_ids:
        suggestion = {"kind": "repeat_wrong", "text": "Yanlış yaptığın soruları 'Yalnız yanlışlarım' seçeneğiyle yeniden çöz."}
    elapsed = None
    if attempt.finished_at and attempt.started_at:
        elapsed = round((attempt.finished_at - attempt.started_at).total_seconds(), 1)
    return {
        "score": score,
        "percent": round(score * 100) if score is not None else None,
        "total": total,
        "correct": len(correct_ids),
        "incorrect": len(wrong_ids),
        "unanswered": len(unanswered_ids),
        "ungradable": len(ordered) - total,
        "by_subject": by_subject,
        "by_topic": by_topic,
        "by_difficulty": by_difficulty,
        "weak_concepts": weak[:12],
        "strong_concepts": strong[:12],
        "review_question_ids": review_ids,
        "wrong_question_ids": wrong_ids,
        "suggestion": suggestion,
        "elapsed_seconds": elapsed,
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
    }


def new_attempt(exam: Exam) -> ExamAttempt:
    return ExamAttempt(attempt_id=new_id("att"), exam_id=exam.exam_id, started_at=utc_now())


def record_answer(attempt: ExamAttempt, question: Question, answer_key: str | None, *, elapsed_seconds: float | None = None, flagged: bool | None = None) -> QuestionAttempt:
    key = str(answer_key).strip().upper() if answer_key else None
    previous = attempt.answers.get(question.question_id)
    entry = QuestionAttempt(
        question_id=question.question_id,
        answer_key=key,
        correct=grade(question, key) if key else None,
        elapsed_seconds=elapsed_seconds if elapsed_seconds is not None else (previous.elapsed_seconds if previous else None),
        flagged=bool(flagged) if flagged is not None else (previous.flagged if previous else False),
    )
    attempt.answers[question.question_id] = entry
    return entry


def difficulty_label(value: int) -> str:
    return DIFFICULTY_LABELS_TR.get(int(value), str(value))


__all__ = [
    "Difficulty",
    "OPTION_KEYS",
    "SIMILARITY_THRESHOLD",
    "analyse_attempt",
    "build_question",
    "difficulty_label",
    "explanation_payload",
    "format_feedback_text",
    "format_question_text",
    "grade",
    "is_too_similar",
    "most_similar",
    "new_attempt",
    "question_payload",
    "record_answer",
    "shuffle_options",
    "validate_question",
]
