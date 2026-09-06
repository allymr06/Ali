"""The tutor: turns one parsed study command into what the core should do.

Deterministic where possible (quiz answers, navigation, terminology
cards, session changes), model-backed where semantics are needed
(explanations, comparisons, notes), and always explicit about which
material grounds the answer. The result is a ``RequestAugmentation`` the
core engine applies to the turn.

``plan`` runs inside the engine's augmentation slice — about two seconds
of the turn's budget, after which the engine cancels it — so nothing here
awaits the provider. Work that needs the model either goes into the
prompt the engine then sends, or onto the academy's background runner,
which reports through events.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any, Protocol

from app.core.augmentation import RequestAugmentation
from app.core.time import utc_now
from app.medical.anatomy import AnatomyLab
from app.medical.catalog import Curriculum
from app.medical.concepts import ConceptGraph
from app.medical.context import SessionManager, StudyContext
from app.medical.generation import ExamBuilder, GenerationError, QuestionGenerator
from app.medical.intents import MedicalIntent, MedicalIntentParser, StudyCommand, describe_command
from app.medical.learning import LearningEngine
from app.medical.models import (
    KNOWLEDGE_SOURCE_LABELS_TR,
    SUBJECT_LABELS_TR,
    Exam,
    ExamConfig,
    KnowledgePriority,
    KnowledgeSource,
    Question,
    StudySession,
)
from app.medical.professor import StyleProfiler
from app.medical.prompts import tutor_system_prompt
from app.medical.questions import (
    analyse_attempt,
    format_feedback_text,
    format_question_text,
    grade,
    new_attempt,
    record_answer,
)
from app.medical.retrieval import RetrievalScope, Retriever
from app.medical.store import MedicalStore
from app.medical.terminology import TerminologyIndex
from app.medical.text import tokens

MEDICAL_TOOLS: frozenset[str] = frozenset(
    {"medical_search_library", "medical_lookup_term", "medical_open_anatomy", "medical_study_state"}
)
CONTEXT_WINDOW = timedelta(minutes=20)
CHAT_QUIZ_MAX = 10
STOP_WORDS: frozenset[str] = frozenset({"bitir", "dur", "durdur", "stop", "yeter", "kapat", "iptal", "quit", "end"})
STOP_WORD_LIMIT = 6

EventCallback = Callable[[dict[str, Any]], None]


class BackgroundRunner(Protocol):
    """Schedules a pipeline off the turn; False when there is no loop for it."""

    def __call__(self, coroutine: Coroutine[Any, Any, Any], *, label: str = "") -> bool: ...


class MedicalTutor:
    def __init__(
        self,
        *,
        store: MedicalStore,
        curriculum: Curriculum,
        terminology: TerminologyIndex,
        concepts: ConceptGraph,
        anatomy: AnatomyLab,
        parser: MedicalIntentParser,
        sessions: SessionManager,
        retriever: Retriever,
        learning: LearningEngine,
        generator: QuestionGenerator,
        exams: ExamBuilder,
        emit: EventCallback,
        run_background: BackgroundRunner,
        model_available: Callable[[], bool],
        document_jobs: Any | None = None,
    ) -> None:
        self._store = store
        self._curriculum = curriculum
        self._terminology = terminology
        self._concepts = concepts
        self._anatomy = anatomy
        self._parser = parser
        self._sessions = sessions
        self._retriever = retriever
        self._learning = learning
        self._generator = generator
        self._exams = exams
        self._emit = emit
        self._run_background = run_background
        self._model_available = model_available
        self._document_jobs = document_jobs
        self.last_command: StudyCommand | None = None
        # Papers the model is writing right now, so a second "beni sına"
        # neither starts a duplicate job nor promises a second quiz.
        self._preparing: set[str] = set()

    # ------------------------------------------------------------------
    # entry
    # ------------------------------------------------------------------

    def parse(self, text: str, *, forced: bool = False) -> StudyCommand:
        session = self._sessions.get()
        return self._parser.parse(text, forced=forced, contextual=self._contextual(session), session=session)

    @staticmethod
    def _contextual(session: StudySession) -> bool:
        if session.last_activity_at is None:
            return False
        return utc_now() - session.last_activity_at <= CONTEXT_WINDOW

    async def plan(self, text: str, *, forced: bool = False, spoken: bool = False) -> RequestAugmentation | None:
        session = self._sessions.get()
        command = self._parser.parse(text, forced=forced, contextual=self._contextual(session), session=session)
        # A bare stop word carries no medical marker, so the parser hands it
        # back as MedicalIntent.NONE; the quiz intro still promised it closes
        # the quiz, so it is decided here rather than inside an intent branch.
        stopping = self._wants_stop(text, session)
        if not command.medical and not stopping:
            return None
        self.last_command = command
        session = self._sessions.apply_command(command)
        session.last_activity_at = utc_now()
        self._sessions.save(session)
        context = self._sessions.resolve(command, spoken=spoken)
        base_metadata = {"intent": command.intent, "label": command.label, "summary": describe_command(command), "confidence": command.confidence}
        intent = command.intent
        try:
            if stopping:
                return self._stop_quiz(session, base_metadata)
            if intent == MedicalIntent.ANSWER:
                return self._answer(command, session, base_metadata)
            if intent == MedicalIntent.NEXT_QUESTION:
                return self._next_question(session, base_metadata)
            if intent == MedicalIntent.QUIZ:
                return self._start_quiz(command, context, session, base_metadata)
            if intent == MedicalIntent.ORAL_EXAM:
                return self._start_oral(command, context, session, base_metadata)
            if intent in {MedicalIntent.EXAM_GENERATE, MedicalIntent.PROFESSOR_STYLE_EXAM}:
                return self._generate_exam(command, context, session, base_metadata)
            if intent in {MedicalIntent.ANATOMY_OPEN, MedicalIntent.ANATOMY_HIGHLIGHT, MedicalIntent.ANATOMY_QUIZ}:
                return self._anatomy_action(command, base_metadata)
            if intent in {MedicalIntent.PDF_ANALYZE, MedicalIntent.PDF_COMPARE}:
                return self._document_action(command, context, session, base_metadata)
            if intent == MedicalIntent.PROFESSOR_PROFILE:
                return self._professor_profile(command, base_metadata)
            if intent == MedicalIntent.REVIEW_WEAKNESS:
                return self._review_weakness(command, context, base_metadata)
            if intent == MedicalIntent.WHY_WRONG:
                return self._why_wrong(command, context, session, base_metadata)
        except GenerationError as exc:
            return RequestAugmentation(direct_response=str(exc), kind="medical", suppress_memory=True, metadata={**base_metadata, "error": "generation"})
        return self._teach(command, context, session, base_metadata)

    # ------------------------------------------------------------------
    # teaching turns (model)
    # ------------------------------------------------------------------

    def _evidence_for(self, command: StudyCommand, context: StudyContext) -> tuple[str, list[Any]]:
        if context.knowledge_source == KnowledgeSource.STANDARD:
            return "", []
        scope = RetrievalScope(document_ids=list(context.document_ids), page_from=context.page_from, page_to=context.page_to, subject=context.subject)
        if context.knowledge_source == KnowledgeSource.SELECTED_DOCUMENTS and not scope.document_ids:
            return "", []
        if not self._store.summary()["chunks"]:
            return "", []
        query = command.text
        if command.terms:
            query += " " + " ".join(match.entry.canonical for match in command.terms[:4])
        blocks = self._retriever.retrieve(query, scope, limit=5)
        return self._retriever.format_evidence(blocks), blocks

    def _quiz_note(self, session: StudySession) -> str | None:
        quiz = session.chat_quiz or {}
        if not quiz.get("active"):
            return None
        if quiz.get("mode") == "oral":
            topic = self._curriculum.breadcrumb(session.topic_id) if session.topic_id else SUBJECT_LABELS_TR.get(session.subject or "", "tıp")
            return (
                f"ORAL EXAM in progress on: {topic}. Protocol: if the student's message answers your previous question, "
                "evaluate it briefly (doğru / eksik / yanlış) with the model answer in 2–3 sentences and one exam point, then "
                "ask exactly ONE new question and stop. If it is not an answer, respond and then remind them the exam continues. "
                "Never ask more than one question at a time; do not reveal an answer before the student tries."
            )
        return None

    def _teach(self, command: StudyCommand, context: StudyContext, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        evidence_text, blocks = self._evidence_for(command, context)
        curated = self._anatomy.facts_for_prompt(command.structure_ids, limit=2) if command.structure_ids else ""
        hints = self._learning.hints_for(command.concept_ids) if command.concept_ids else []
        professor_directive = None
        if command.professor_style and context.professor_id:
            profile = self._store.get_professor(context.professor_id)
            if profile is not None:
                professor_directive = StyleProfiler.directive(profile)
        prompt = tutor_system_prompt(
            subject=context.subject,
            topic_path=self._curriculum.breadcrumb(context.topic_id) if context.topic_id else "",
            intent=command.intent,
            depth=context.depth,
            knowledge_source=context.knowledge_source,
            knowledge_priority=context.knowledge_priority,
            evidence_text=evidence_text,
            curated_facts=curated,
            professor_directive=professor_directive,
            mastery_hints=hints,
            spoken=context.spoken,
            quiz_note=self._quiz_note(session),
        )
        references = self._retriever.references(blocks)
        metadata = {
            **metadata,
            "evidence_count": len(blocks),
            "references": references,
            "knowledge_source": context.knowledge_source,
            "knowledge_source_label": KNOWLEDGE_SOURCE_LABELS_TR.get(context.knowledge_source, context.knowledge_source),
            "subject": context.subject,
            "topic_id": context.topic_id,
            "structure_ids": list(command.structure_ids),
            "curated_facts": bool(curated),
        }
        if references:
            self._emit({"kind": "references", "references": references, "summary": describe_command(command)})
        return RequestAugmentation(system_prompt=prompt, allowed_tools=MEDICAL_TOOLS, kind="medical", suppress_memory=True, metadata=metadata)

    def _review_weakness(self, command: StudyCommand, context: StudyContext, metadata: dict[str, Any]) -> RequestAugmentation:
        weak = self._learning.weak(limit=6, subject=context.subject)
        if not weak:
            return RequestAugmentation(
                direct_response="Henüz zayıf olarak işaretlenmiş bir kavram yok: birkaç quiz ya da sınav çözdükçe yanlışların burada birikir ve tekrar listesi oluşur.",
                kind="medical",
                suppress_memory=True,
                metadata=metadata,
            )
        lines = []
        for item in weak:
            name = self._learning.concept_name(item.concept_id)
            confusion = max(item.confusions.items(), key=lambda pair: pair[1])[0] if item.confusions else ""
            lines.append(f"- {name}: {item.correct}/{item.attempts} correct" + (f"; confused with '{confusion}'" if confusion else ""))
        prompt = tutor_system_prompt(
            subject=context.subject,
            topic_path=self._curriculum.breadcrumb(context.topic_id) if context.topic_id else "",
            intent=MedicalIntent.REVIEW_WEAKNESS,
            depth=context.depth,
            knowledge_source=KnowledgeSource.STANDARD,
            knowledge_priority=context.knowledge_priority,
            evidence_text="",
            curated_facts=self._anatomy.facts_for_prompt([cid.split(".")[1] for cid in (item.concept_id for item in weak) if cid.startswith("anatomy.") and len(cid.split(".")) >= 2], limit=2),
            professor_directive=None,
            mastery_hints=[],
            spoken=context.spoken,
            quiz_note="Weak concepts to review (from the student's own quiz history):\n" + "\n".join(lines),
        )
        return RequestAugmentation(system_prompt=prompt, allowed_tools=MEDICAL_TOOLS, kind="medical", suppress_memory=True, metadata={**metadata, "weak_concepts": [item.concept_id for item in weak]})

    # ------------------------------------------------------------------
    # quiz in chat (deterministic)
    # ------------------------------------------------------------------

    @staticmethod
    def _wants_stop(text: str, session: StudySession) -> bool:
        quiz = session.chat_quiz or {}
        if not quiz.get("active"):
            return False
        spoken = tokens(text)
        # Whole words, folded: "dur" and "end" sit inside "dura mater" and
        # "tendon", so a substring test would close the quiz on a question.
        return len(spoken) <= STOP_WORD_LIMIT and any(word in STOP_WORDS for word in spoken)

    def _stop_quiz(self, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        quiz = session.chat_quiz or {}
        summary = self._finish_quiz(session, quiz, abandoned=True)
        return RequestAugmentation(direct_response=summary, kind="medical", suppress_memory=True, metadata={**metadata, "quiz": "stopped"})

    def _config_from(self, command: StudyCommand, context: StudyContext, *, count: int, interactive: bool) -> ExamConfig:
        subject = context.subject or (self._curriculum.subject_of(context.topic_id) if context.topic_id else None)
        professor_id = context.professor_id if (command.professor_style or command.intent == MedicalIntent.PROFESSOR_STYLE_EXAM) else None
        if (command.professor_style or command.intent == MedicalIntent.PROFESSOR_STYLE_EXAM) and professor_id is None:
            profiles = self._store.list_professors()
            if len(profiles) == 1:
                professor_id = profiles[0].profile_id
            elif subject:
                matching = [profile for profile in profiles if profile.subject == subject]
                professor_id = matching[0].profile_id if len(matching) == 1 else None
        document_ids = list(context.document_ids)
        if command.current_document and not document_ids:
            latest = [document for document in self._store.list_documents() if document.status == "ready"]
            if latest:
                document_ids = [latest[0].document_id]
        return ExamConfig(
            subjects=[subject] if subject else [],
            topic_ids=[context.topic_id] if context.topic_id else [],
            document_ids=document_ids,
            page_from=context.page_from,
            page_to=context.page_to,
            question_count=count,
            option_count=context.option_count,
            difficulty=context.difficulty,
            professor_id=professor_id,
            knowledge_priority=context.knowledge_priority,
            timed_seconds=0,
            immediate_feedback=interactive or command.immediate_feedback,
            answers_at_end=command.answers_at_end or not interactive,
            randomize=True,
            weak_emphasis=command.wrong_only,
            wrong_only=command.wrong_only,
            one_at_a_time=True,
        )

    def _wrong_question_ids(self, *, limit: int = 40) -> list[str]:
        ids: list[str] = []
        for attempt in self._store.list_attempts(limit=30):
            for question_id, answer in attempt.answers.items():
                if answer.correct is False and question_id not in ids:
                    ids.append(question_id)
        return ids[:limit]

    def _bank_questions(self, config: ExamConfig) -> tuple[list[Question], list[str]]:
        """What the stored bank alone can offer for this paper, and its note.

        Asked before the model on every chat quiz and exam: the augmenter
        runs inside the engine's short augmentation slice, so anything the
        bank can answer is answered without waiting for a provider.
        """
        if config.wrong_only:
            wrong = self._generator.from_bank(config, wrong_question_ids=self._wrong_question_ids(), only_wrong=True)
            # A "wrong answers only" paper is never padded from the rest of
            # the bank, so an empty result means the model has to write it.
            return (wrong[: config.question_count], [f"Yanlış yaptığın {len(wrong)} sorudan seçildi."]) if wrong else ([], [])
        if config.document_ids or config.knowledge_priority == KnowledgePriority.STRICT_LECTURE:
            # The bank is indexed by subject and topic, never by document, so
            # a paper the student scoped to a lecture note cannot come from it.
            return [], []
        bank = self._generator.from_bank(config)
        return (bank, ["Soru bankasından seçildi."]) if bank else ([], [])

    def _bank_is_enough(self, questions: list[Question], config: ExamConfig) -> bool:
        """The bank answers the turn when it fills the request — or when there
        is no provider that could write anything better."""
        if not questions:
            return False
        if config.wrong_only:
            # A "wrong answers only" paper is exactly the student's own
            # mistakes: a short one is honest, a topped-up one would not be
            # theirs, so however many there are, that is the paper.
            return True
        return len(questions) >= config.question_count or not self._model_available()

    async def _model_questions(self, config: ExamConfig) -> tuple[list[Question], list[str]]:
        """Generate with the model. Only ever awaited off the turn."""
        notes: list[str] = []
        if config.wrong_only:
            # Reached only when the bank held no recorded mistakes at all.
            notes.append("Kayıtlı yanlışın yoktu; yeni sorular üretildi.")
        try:
            generated, generation_notes = await self._generator.generate(config)
            return generated, notes + generation_notes
        except GenerationError as exc:
            notes.append(str(exc))
        bank = self._generator.from_bank(config)
        if bank:
            notes.append("Soru bankasından seçildi.")
            return bank, notes
        raise GenerationError(notes[-1])

    def _no_questions_error(self) -> GenerationError:
        return GenerationError("Uygun soru bulunamadı; önce bir ders ve konu seç ya da model bağlantısını ayarla.")

    def _start_quiz(self, command: StudyCommand, context: StudyContext, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        if context.subject is None and context.topic_id is None:
            return RequestAugmentation(
                direct_response="Hangi dersten sınayayım? Örneğin: “beni anatomiden sına” ya da Tıp Akademisi'nde bir konu seç.",
                kind="medical",
                suppress_memory=True,
                metadata=metadata,
            )
        count = min(CHAT_QUIZ_MAX, command.question_count or min(session.question_count, 5))
        config = self._config_from(command, context, count=count, interactive=True)
        questions, notes = self._bank_questions(config)
        if self._bank_is_enough(questions, config):
            exam, text = self._begin_quiz(config, questions, notes)
            return RequestAugmentation(direct_response=text, kind="medical", suppress_memory=True, metadata={**metadata, "exam_id": exam.exam_id, "quiz": "started"})
        if not self._model_available():
            raise self._no_questions_error()
        return self._prepare_later(
            "quiz",
            lambda: self._prepare_quiz(config),
            label="Quiz hazırlığı",
            text="Soruları hazırlıyorum; birkaç saniye sürebilir. Hazır olunca ilk soruyu bildirim merkezine bırakırım, sohbette harfle cevaplayabilirsin.",
            metadata=metadata,
        )

    def _begin_quiz(self, config: ExamConfig, questions: list[Question], notes: list[str]) -> tuple[Exam, str]:
        """Store the paper, put the chat quiz into its started state and return
        the exam together with the text that opens it."""
        exam = self._exams.build(config, questions, notes=notes)
        attempt = new_attempt(exam)
        self._store.save_attempt(attempt)
        self._sessions.start_chat_quiz(exam.question_ids, mode="quiz", exam_id=exam.exam_id)
        self._sessions.update_chat_quiz(attempt_id=attempt.attempt_id)
        first = self._store.get_question(exam.question_ids[0]) if exam.question_ids else None
        intro = f"Quiz başladı: {exam.title}. Cevabını harfle ver (A–{'ABCDEF'[len(first.options) - 1] if first else 'E'}); “sonraki soru” ile atlayabilir, “bitir” ile kapatabilirsin."
        self._emit({"kind": "quiz_started", "exam_id": exam.exam_id, "title": exam.title, "count": len(exam.question_ids)})
        return exam, intro + "\n\n" + (format_question_text(first, number=1) if first else "")

    # ------------------------------------------------------------------
    # papers the model has to write (never inside the turn)
    # ------------------------------------------------------------------

    def _prepare_later(
        self,
        key: str,
        job: Callable[[], Coroutine[Any, Any, None]],
        *,
        label: str,
        text: str,
        metadata: dict[str, Any],
    ) -> RequestAugmentation:
        """Answer the turn now and let the model write the paper in the background.

        The engine gives the augmenter about two seconds of the turn's
        budget and cancels it when that expires; one generation round trip
        takes several. Awaiting the model here would drop the turn and tell
        the student nothing, so the request is acknowledged immediately and
        the work reports itself through events when it lands.
        """
        if key in self._preparing:
            return RequestAugmentation(
                direct_response="Bir önceki isteğin için soruları hâlâ hazırlıyorum; hazır olunca bildiririm.",
                kind="medical",
                suppress_memory=True,
                metadata={**metadata, key: "preparing"},
            )
        self._preparing.add(key)
        if not self._run_background(job(), label=label):
            # Nothing will run, so the promise below must not be made.
            self._preparing.discard(key)
            return RequestAugmentation(
                direct_response="Soru hazırlığı şu an başlatılamadı; birazdan tekrar dener misin?",
                kind="medical",
                suppress_memory=True,
                metadata={**metadata, "error": "background"},
            )
        return RequestAugmentation(direct_response=text, kind="medical", suppress_memory=True, metadata={**metadata, key: "preparing"})

    async def _prepare_quiz(self, config: ExamConfig) -> None:
        try:
            questions, notes = await self._model_questions(config)
        finally:
            self._preparing.discard("quiz")
        if self._sessions.chat_quiz_state().get("active"):
            # The student started another quiz while the model was writing this
            # one; the paper is kept, but what they are answering is not replaced.
            exam = self._exams.build(config, questions, notes=notes)
            self._emit({"kind": "exam_ready", "exam_id": exam.exam_id, "title": exam.title, "count": len(exam.question_ids)})
            return
        exam, text = self._begin_quiz(config, questions, notes)
        self._emit({"kind": "quiz_ready", "exam_id": exam.exam_id, "title": exam.title, "count": len(exam.question_ids), "question": text})

    async def _prepare_exam(self, config: ExamConfig) -> None:
        try:
            questions, notes = await self._model_questions(config)
        finally:
            self._preparing.discard("exam")
        exam = self._exams.build(config, questions, notes=notes)
        session = self._sessions.get()
        session.active_exam_id = exam.exam_id
        self._sessions.save(session)
        self._emit({"kind": "exam_ready", "exam_id": exam.exam_id, "title": exam.title, "count": len(exam.question_ids)})

    def _start_oral(self, command: StudyCommand, context: StudyContext, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        self._sessions.start_chat_quiz([], mode="oral")
        self._sessions.update_chat_quiz(active=True)
        session = self._sessions.get()
        prompt = tutor_system_prompt(
            subject=context.subject,
            topic_path=self._curriculum.breadcrumb(context.topic_id) if context.topic_id else "",
            intent=MedicalIntent.ORAL_EXAM,
            depth=context.depth,
            knowledge_source=context.knowledge_source,
            knowledge_priority=context.knowledge_priority,
            evidence_text=self._evidence_for(command, context)[0],
            curated_facts=self._anatomy.facts_for_prompt(command.structure_ids, limit=2) if command.structure_ids else "",
            professor_directive=None,
            mastery_hints=[],
            spoken=context.spoken,
            quiz_note=(self._quiz_note(session) or "") + "\nStart now: greet in one line and ask the first question only.",
        )
        return RequestAugmentation(system_prompt=prompt, allowed_tools=MEDICAL_TOOLS, kind="medical", suppress_memory=True, metadata={**metadata, "quiz": "oral"})

    def _current_question(self, quiz: dict[str, Any]) -> Question | None:
        ids = quiz.get("question_ids") or []
        index = int(quiz.get("index") or 0)
        if index >= len(ids):
            return None
        return self._store.get_question(str(ids[index]))

    def _answer(self, command: StudyCommand, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        quiz = dict(session.chat_quiz or {})
        if quiz.get("mode") == "oral" or not quiz.get("question_ids"):
            # Free-form oral exam: the model evaluates; a bare letter is not an answer there.
            return self._teach(command, self._sessions.resolve(command), session, metadata)
        question = self._current_question(quiz)
        if question is None:
            return RequestAugmentation(direct_response=self._finish_quiz(session, quiz), kind="medical", suppress_memory=True, metadata=metadata)
        key = command.answer_key
        result = grade(question, key)
        attempt = self._store.get_attempt(str(quiz.get("attempt_id") or "")) if quiz.get("attempt_id") else None
        if attempt is not None:
            record_answer(attempt, question, key)
            self._store.save_attempt(attempt)
        if result is not None:
            self._learning.record(question, result, chosen_key=key)
        feedback = format_feedback_text(question, key)
        answered = dict(quiz.get("answered") or {})
        answered[question.question_id] = key
        index = int(quiz.get("index") or 0) + 1
        self._sessions.update_chat_quiz(index=index, answered=answered, last_question_id=question.question_id, last_answer=key)
        quiz = self._sessions.chat_quiz_state()
        nxt = self._current_question(quiz)
        if nxt is None:
            text = feedback + "\n\n" + self._finish_quiz(self._sessions.get(), quiz)
        else:
            text = feedback + "\n\n" + format_question_text(nxt, number=index + 1)
        self._emit({"kind": "quiz_progress", "exam_id": quiz.get("exam_id"), "index": index, "total": len(quiz.get("question_ids") or [])})
        return RequestAugmentation(direct_response=text, kind="medical", suppress_memory=True, metadata={**metadata, "correct": result, "question_id": question.question_id})

    def _next_question(self, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        quiz = dict(session.chat_quiz or {})
        if quiz.get("mode") == "oral":
            return self._teach(self._parser.parse("sonraki soru", forced=True, session=session), self._sessions.resolve(None), session, metadata)
        question = self._current_question(quiz)
        if question is None:
            return RequestAugmentation(direct_response=self._finish_quiz(session, quiz), kind="medical", suppress_memory=True, metadata=metadata)
        index = int(quiz.get("index") or 0) + 1
        self._sessions.update_chat_quiz(index=index, last_question_id=question.question_id, last_answer=None)
        quiz = self._sessions.chat_quiz_state()
        nxt = self._current_question(quiz)
        skipped = f"Soru atlandı (doğru cevap: {question.correct_key})." if question.correct_key else "Soru atlandı."
        if nxt is None:
            return RequestAugmentation(direct_response=skipped + "\n\n" + self._finish_quiz(self._sessions.get(), quiz), kind="medical", suppress_memory=True, metadata=metadata)
        return RequestAugmentation(direct_response=skipped + "\n\n" + format_question_text(nxt, number=index + 1), kind="medical", suppress_memory=True, metadata=metadata)

    def _finish_quiz(self, session: StudySession, quiz: dict[str, Any], *, abandoned: bool = False) -> str:
        exam_id = quiz.get("exam_id")
        attempt_id = quiz.get("attempt_id")
        self._sessions.stop_chat_quiz()
        if quiz.get("mode") == "oral":
            return "Sözlü sınav kapatıldı. İstediğinde “sözlü sınav yap” diyerek devam edebilirsin."
        exam = self._store.get_exam(str(exam_id)) if exam_id else None
        attempt = self._store.get_attempt(str(attempt_id)) if attempt_id else None
        if exam is None or attempt is None:
            return "Quiz bitti."
        attempt.finished_at = utc_now()
        questions = self._store.get_questions(exam.question_ids)
        analysis = analyse_attempt(exam, questions, attempt, curriculum=self._curriculum, mastery_levels=self._learning.levels())
        attempt.score = analysis["score"]
        attempt.analysis = analysis
        self._store.save_attempt(attempt)
        exam.status = "completed"
        exam.finished_at = attempt.finished_at
        self._store.save_exam(exam)
        self._emit({"kind": "quiz_finished", "exam_id": exam.exam_id, "attempt_id": attempt.attempt_id, "percent": analysis["percent"]})
        header = "Quiz yarıda kapatıldı." if abandoned else "Quiz bitti."
        lines = [f"{header} Sonuç: {analysis['correct']}/{analysis['total']} doğru" + (f" (%{analysis['percent']})" if analysis["percent"] is not None else "") + "."]
        if analysis["weak_concepts"]:
            lines.append("Zayıf kavramlar: " + ", ".join(self._learning.concept_name(item["concept_id"], fallback=item["label"]) for item in analysis["weak_concepts"][:4]) + ".")
        if analysis["suggestion"]:
            lines.append(analysis["suggestion"]["text"])
        lines.append("Ayrıntılı analiz Tıp Akademisi › Sınav ekranında.")
        return "\n".join(lines)

    def _why_wrong(self, command: StudyCommand, context: StudyContext, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        quiz = session.chat_quiz or {}
        question_id = quiz.get("last_question_id")
        question = self._store.get_question(str(question_id)) if question_id else None
        if question is None:
            return self._teach(command, context, session, metadata)
        chosen = quiz.get("last_answer")
        correct = question.option(question.correct_key or "")
        detail = [
            f"Question: {question.stem}",
            "Options: " + " | ".join(f"{option.key}) {option.text}" for option in question.options),
            f"Correct: {question.correct_key}) {correct.text if correct else ''}",
            f"Student chose: {chosen or 'nothing'}",
            f"Stored explanation: {question.explanation}",
        ]
        for option in question.options:
            if option.explanation:
                detail.append(f"Why {option.key} is wrong/right: {option.explanation}")
        prompt = tutor_system_prompt(
            subject=context.subject or question.subject,
            topic_path=self._curriculum.breadcrumb(question.topic_id) if question.topic_id else "",
            intent=MedicalIntent.WHY_WRONG,
            depth=context.depth,
            knowledge_source=KnowledgeSource.STANDARD,
            knowledge_priority=context.knowledge_priority,
            evidence_text="",
            curated_facts=self._anatomy.facts_for_prompt(command.structure_ids, limit=1) if command.structure_ids else "",
            professor_directive=None,
            mastery_hints=self._learning.hints_for(question.concept_ids),
            spoken=context.spoken,
            quiz_note="Quiz item under discussion:\n" + "\n".join(detail),
        )
        return RequestAugmentation(system_prompt=prompt, allowed_tools=MEDICAL_TOOLS, kind="medical", suppress_memory=True, metadata={**metadata, "question_id": question.question_id})

    # ------------------------------------------------------------------
    # exams from chat
    # ------------------------------------------------------------------

    def _generate_exam(self, command: StudyCommand, context: StudyContext, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        if context.subject is None and context.topic_id is None and not command.current_document and not context.document_ids:
            return RequestAugmentation(
                direct_response="Hangi ders ya da konudan sınav hazırlayayım? Örneğin: “omuz eklemi konusundan 20 soru hazırla” ya da Tıp Akademisi'nde konu seç.",
                kind="medical",
                suppress_memory=True,
                metadata=metadata,
            )
        count = command.question_count or session.question_count
        config = self._config_from(command, context, count=count, interactive=not command.answers_at_end and command.one_at_a_time)
        if command.intent == MedicalIntent.PROFESSOR_STYLE_EXAM and config.professor_id is None:
            profiles = self._store.list_professors()
            if not profiles:
                return RequestAugmentation(
                    direct_response="Henüz bir hoca profili yok. Tıp Akademisi › Hoca Tarzı ekranından eski sınav sorularını yükle; profil oluşunca aynı tarzda yeni sorular üretebilirim.",
                    kind="medical",
                    suppress_memory=True,
                    metadata=metadata,
                )
            return RequestAugmentation(
                direct_response="Birden fazla hoca profili var; Tıp Akademisi'nde çalışma oturumu için birini seç, sonra tekrar iste.",
                kind="medical",
                suppress_memory=True,
                metadata=metadata,
            )
        questions, notes = self._bank_questions(config)
        if not self._bank_is_enough(questions, config):
            if not self._model_available():
                raise self._no_questions_error()
            return self._prepare_later(
                "exam",
                lambda: self._prepare_exam(config),
                label="Sınav hazırlığı",
                text="Sınavı hazırlıyorum; birkaç saniye sürebilir. Hazır olunca bildirim merkezine düşer, Tıp Akademisi › Sınav ekranından başlatabilirsin.",
                metadata=metadata,
            )
        exam = self._exams.build(config, questions, notes=notes)
        session = self._sessions.get()
        session.active_exam_id = exam.exam_id
        self._sessions.save(session)
        self._emit({"kind": "exam_ready", "exam_id": exam.exam_id, "title": exam.title, "count": len(exam.question_ids)})
        lines = [f"Sınav hazır: **{exam.title}** — {len(exam.question_ids)} soru, {config.option_count} şık, zorluk {config.difficulty}/5."]
        if config.professor_id:
            profile = self._store.get_professor(config.professor_id)
            if profile is not None:
                lines.append(f"Hoca tarzı: {profile.name} ({profile.basis})")
        if config.answers_at_end:
            lines.append("Cevaplar sınav bitince gösterilecek.")
        for note in notes[:3]:
            lines.append(f"Not: {note}")
        lines.append("Tıp Akademisi › Sınav ekranında başlatabilirsin; “beni sına” dersen sohbette tek tek sorarım.")
        return RequestAugmentation(direct_response="\n".join(lines), kind="medical", suppress_memory=True, metadata={**metadata, "exam_id": exam.exam_id})

    # ------------------------------------------------------------------
    # anatomy, documents, professors (deterministic)
    # ------------------------------------------------------------------

    def _anatomy_action(self, command: StudyCommand, metadata: dict[str, Any]) -> RequestAugmentation:
        structure = self._anatomy.get(command.structure_ids[0]) if command.structure_ids else None
        if structure is None:
            return RequestAugmentation(direct_response="Anatomi Lab'de açacak bir yapı bulamadım; kemik, eklem veya kas adını Latince ya da Türkçe söyle.", kind="medical", suppress_memory=True, metadata=metadata)
        highlights = [landmark_id.split(".")[-1] for landmark_id in command.landmark_ids]
        event = {"kind": "anatomy_open", "structure_id": structure.structure_id, "highlight": highlights, "quiz": command.intent == MedicalIntent.ANATOMY_QUIZ}
        self._emit(event)
        description = self._anatomy.describe(structure.structure_id) or {}
        model = description.get("model", {})
        if command.intent == MedicalIntent.ANATOMY_QUIZ:
            text = f"Anatomi Lab'de {structure.canonical} için quiz açıldı: işaretli yapıları tanı."
        elif highlights:
            names = ", ".join(landmark.latin for landmark in structure.landmarks if landmark.landmark_id in highlights)
            text = f"Anatomi Lab'de {structure.canonical} açıldı; işaretlenen: {names or ', '.join(highlights)}."
        else:
            text = f"Anatomi Lab'de {structure.canonical} ({structure.turkish}) açıldı: {len(structure.landmarks)} işaret noktası, ilişkili yapılar ve kart."
        if not model.get("available"):
            text += " Lisanslı 3B model kayıtlı olmadığı için şematik işaret haritası gösteriliyor."
        return RequestAugmentation(direct_response=text, kind="medical", suppress_memory=True, metadata={**metadata, "structure_id": structure.structure_id})

    def _document_action(self, command: StudyCommand, context: StudyContext, session: StudySession, metadata: dict[str, Any]) -> RequestAugmentation:
        documents = [document for document in self._store.list_documents() if document.status == "ready"]
        chosen = None
        if command.document_hint:
            for document in documents:
                if command.document_hint in document.title or command.document_hint in document.file_name:
                    chosen = document
                    break
        if chosen is None and context.document_ids:
            chosen = self._store.get_document(context.document_ids[0])
        if chosen is None and documents:
            chosen = documents[0]
        if chosen is None:
            return RequestAugmentation(
                direct_response="Henüz işlenmiş bir belge yok. Tıp Akademisi › Kütüphane'den PDF ya da ders notu yükle; sonra “bu PDF'yi analiz et” diyebilirsin.",
                kind="medical",
                suppress_memory=True,
                metadata=metadata,
            )
        page_from, page_to = (command.page_range or (context.page_from, context.page_to))
        if self._document_jobs is None or not self._model_available():
            return RequestAugmentation(
                direct_response=f"“{chosen.title}” için analiz modeli gerekiyor; API bağlantısını ayarlardan kontrol et.",
                kind="medical",
                suppress_memory=True,
                metadata=metadata,
            )
        if command.intent == MedicalIntent.PDF_COMPARE:
            started = self._run_background(self._document_jobs.compare(chosen.document_id, page_from=page_from, page_to=page_to), label=f"“{chosen.title}” karşılaştırması")
            text = f"“{chosen.title}” belgesini standart tıp bilgisiyle karşılaştırmaya başladım" + (f" (s. {page_from}–{page_to})" if page_from and page_to else "") + ". Bitince bildirim merkezine ve Kütüphane'ye düşer: tutarlı / basitleştirilmiş / eksik / yanıltıcı / hatalı / terminoloji farkı olarak etiketlenir."
        else:
            started = self._run_background(self._document_jobs.analyze(chosen.document_id, page_from=page_from, page_to=page_to), label=f"“{chosen.title}” analizi")
            text = f"“{chosen.title}” belgesini analiz etmeye başladım" + (f" (s. {page_from}–{page_to})" if page_from and page_to else "") + ": konular, terimler ve yüksek verimli noktalar çıkarılacak; şekilli sayfalar görüntüden incelenecek. Bitince bildiririm."
        if not started:
            # Nothing was scheduled, so "Bitince bildiririm" would be a promise
            # nobody keeps.
            return RequestAugmentation(
                direct_response=f"“{chosen.title}” için işi şu an başlatamadım; birazdan tekrar dener misin?",
                kind="medical",
                suppress_memory=True,
                metadata={**metadata, "error": "background"},
            )
        session.document_ids = [chosen.document_id] if chosen.document_id not in session.document_ids else session.document_ids
        if command.page_range:
            session.page_from, session.page_to = command.page_range
        self._sessions.save(session)
        return RequestAugmentation(direct_response=text, kind="medical", suppress_memory=True, metadata={**metadata, "document_id": chosen.document_id})

    def _professor_profile(self, command: StudyCommand, metadata: dict[str, Any]) -> RequestAugmentation:
        profiles = self._store.list_professors()
        if not profiles:
            return RequestAugmentation(
                direct_response="Henüz hoca profili yok. Tıp Akademisi › Hoca Tarzı ekranından eski sınav sorularını (PDF, metin ya da ekran görüntüsü) yükle; ben soru yapısını, çeldirici tarzını ve terminoloji yoğunluğunu kanıta dayalı olarak çıkarırım.",
                kind="medical",
                suppress_memory=True,
                metadata=metadata,
            )
        lines = []
        for profile in profiles[:3]:
            strong = [feature for feature in profile.features if feature.level in {"high", "very_high"} and feature.observed >= 2]
            summary = ", ".join(f"{feature.label_tr.lower()} ({feature.observed}/{feature.total})" for feature in strong[:4]) or "belirgin bir kalıp yok"
            lines.append(f"**{profile.name}** — {profile.basis} Öne çıkan: {summary}. Ortalama şık: {profile.average_options}.")
        lines.append("Ayrıntılar Tıp Akademisi › Hoca Tarzı ekranında; “hocanın tarzında 20 soru hazırla” diyebilirsin.")
        return RequestAugmentation(direct_response="\n".join(lines), kind="medical", suppress_memory=True, metadata=metadata)
