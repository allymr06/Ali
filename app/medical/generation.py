"""Question generation and exam assembly.

The model drafts items against a strict schema; this module grounds the
request (lecture evidence, curated facts, concept hints, the professor's
style directive), validates every item, rejects near-copies of existing
questions, shuffles the letters, tags concepts and persists what
survived. What did not survive is reported, never shown.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from typing import Any

from app.medical.anatomy import AnatomyLab
from app.medical.catalog import Curriculum
from app.medical.concepts import ConceptGraph
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient, MedicalModelError
from app.medical.models import (
    SUBJECT_LABELS_TR,
    Exam,
    ExamConfig,
    KnowledgePriority,
    Question,
    QuestionOrigin,
    new_id,
)
from app.medical.professor import StyleProfiler
from app.medical.prompts import PIPELINE_SYSTEM, question_generation_prompt
from app.medical.questions import (
    SIMILARITY_THRESHOLD,
    build_question,
    is_too_similar,
    shuffle_options,
    validate_question,
)
from app.medical.retrieval import RetrievalScope, Retriever
from app.medical.schemas import QUESTIONS_SCHEMA
from app.medical.store import MedicalStore

MAX_ROUNDS = 3
MAX_BATCH = 20
EVIDENCE_CHARS_FOR_GENERATION = 9000


class GenerationError(RuntimeError):
    pass


class QuestionGenerator:
    def __init__(
        self,
        store: MedicalStore,
        model: MedicalModelClient,
        retriever: Retriever,
        curriculum: Curriculum,
        concepts: ConceptGraph,
        anatomy: AnatomyLab,
        learning: LearningEngine,
    ) -> None:
        self._store = store
        self._model = model
        self._retriever = retriever
        self._curriculum = curriculum
        self._concepts = concepts
        self._anatomy = anatomy
        self._learning = learning

    # ------------------------------------------------------------------
    # grounding
    # ------------------------------------------------------------------

    def _topic_query(self, config: ExamConfig) -> str:
        parts: list[str] = []
        for topic_id in config.topic_ids:
            topic = self._curriculum.get(topic_id)
            if topic is not None:
                parts.append(topic.title_tr)
                parts.extend(topic.keywords[:6])
        if not parts:
            for subject in config.subjects:
                parts.append(SUBJECT_LABELS_TR.get(subject, subject))
        return " ".join(parts)

    def _evidence(self, config: ExamConfig) -> tuple[str, list[Any]]:
        if config.knowledge_priority == KnowledgePriority.STANDARD_FIRST and not config.document_ids:
            return "", []
        blocks: list[Any] = []
        if config.document_ids:
            for document_id in config.document_ids[:6]:
                document = self._store.get_document(document_id)
                if document is None:
                    continue
                blocks.extend(
                    self._retriever.page_evidence(
                        document,
                        config.page_from or 1,
                        config.page_to or document.page_count or 10**6,
                        max_chars=EVIDENCE_CHARS_FOR_GENERATION // max(1, min(6, len(config.document_ids))),
                    )
                )
            query = self._topic_query(config)
            if query and len("".join(block.text for block in blocks)) < 1500:
                blocks.extend(self._retriever.retrieve(query, RetrievalScope(document_ids=list(config.document_ids), page_from=config.page_from, page_to=config.page_to), limit=6))
        elif config.topic_ids and self._store.summary()["chunks"]:
            query = self._topic_query(config)
            if query:
                blocks.extend(self._retriever.retrieve(query, RetrievalScope(), limit=6))
        seen: set[str] = set()
        unique = []
        for block in blocks:
            key = block.reference.chunk_id or f"{block.reference.document_id}:{block.reference.page_number}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(block)
        return self._retriever.format_evidence(unique), unique

    def _curated_facts(self, config: ExamConfig) -> str:
        if "anatomy" not in config.subjects and not any(topic.startswith("anatomy") for topic in config.topic_ids):
            return ""
        structure_ids: list[str] = []
        for structure in self._anatomy.all():
            if structure.topic_id and any(structure.topic_id.startswith(topic_id) for topic_id in config.topic_ids):
                structure_ids.append(structure.structure_id)
        random.Random(",".join(config.topic_ids)).shuffle(structure_ids)
        return self._anatomy.facts_for_prompt(structure_ids, limit=3, max_chars=3500)

    def _concept_hints(self, config: ExamConfig) -> list[str]:
        hints: list[str] = []
        for topic_id in config.topic_ids:
            for concept in self._concepts.by_topic(topic_id)[:25]:
                hints.append(concept.name)
        return hints

    def _avoid(self, config: ExamConfig) -> list[Question]:
        avoid: list[Question] = []
        if config.professor_id:
            avoid.extend(self._store.query_questions(professor_id=config.professor_id, origin=QuestionOrigin.IMPORTED_EXAM, limit=200))
        for topic_id in config.topic_ids or [None]:
            avoid.extend(self._store.query_questions(subject=config.subjects[0] if config.subjects else None, topic_id=topic_id, limit=60))
        unique: dict[str, Question] = {}
        for question in avoid:
            unique.setdefault(question.question_id, question)
        return list(unique.values())

    # ------------------------------------------------------------------
    # generation
    # ------------------------------------------------------------------

    async def generate(self, config: ExamConfig, *, professor_directive: str | None = None) -> tuple[list[Question], list[str]]:
        if not self._model.available:
            raise GenerationError("Soru üretimi için model sağlayıcısı gerekli; API anahtarını ayarlardan kontrol et.")
        subject = config.subjects[0] if config.subjects else (self._curriculum.subject_of(config.topic_ids[0]) if config.topic_ids else None)
        if subject is None:
            raise GenerationError("Ders seçilmeden soru üretilemez.")
        topic_id = config.topic_ids[0] if config.topic_ids else None
        topic_path = " / ".join(self._curriculum.breadcrumb(item) for item in config.topic_ids[:3]) if config.topic_ids else ""
        evidence_text, blocks = self._evidence(config)
        if config.knowledge_priority == KnowledgePriority.STRICT_LECTURE and not evidence_text:
            raise GenerationError("Katı ders materyali modunda soru üretmek için seçili belgelerden metin bulunamadı.")
        curated = self._curated_facts(config)
        hints = self._concept_hints(config)
        weak = self._learning.weak_concept_names(limit=8, subject=subject) if config.weak_emphasis else []
        directive = professor_directive
        if directive is None and config.professor_id:
            profile = self._store.get_professor(config.professor_id)
            if profile is not None:
                directive = StyleProfiler.directive(profile)
        avoid = self._avoid(config)
        references = [block.reference for block in blocks]
        accepted: list[Question] = []
        notes: list[str] = []
        needed = max(1, int(config.question_count))
        rounds = 0
        rejected_reasons: dict[str, int] = {}
        unverified_sources = 0
        while len(accepted) < needed and rounds < MAX_ROUNDS:
            rounds += 1
            batch = min(MAX_BATCH, needed - len(accepted) + (1 if rounds > 1 else 0))
            prompt = question_generation_prompt(
                count=batch,
                option_count=config.option_count,
                difficulty=config.difficulty,
                subject=subject,
                topic_path=topic_path,
                question_type=config.question_type,
                evidence_text=evidence_text,
                knowledge_priority=config.knowledge_priority,
                style_directive=directive,
                avoid_stems=[question.stem for question in avoid] + [question.stem for question in accepted],
                concept_hints=hints,
                curated_facts=curated,
                weak_concepts=weak,
            )
            try:
                data = await self._model.structured("question_generation", prompt, QUESTIONS_SCHEMA, system_prompt=PIPELINE_SYSTEM)
            except MedicalModelError as exc:
                notes.append(f"Model turu {rounds} başarısız: {exc}")
                if not accepted:
                    raise GenerationError(str(exc)) from exc
                break
            for raw in data.get("questions", []):
                if len(accepted) >= needed:
                    break
                # What the model claims about its source; build_question keeps the claim
                # only when the excerpt it names is one of the excerpts we sent.
                claims_lecture = bool(evidence_text and (raw.get("source_index") or raw.get("source_page")))
                question = build_question(
                    raw,
                    subject=subject,
                    topic_id=topic_id,
                    difficulty=config.difficulty,
                    origin=QuestionOrigin.LECTURE_DERIVED if claims_lecture else QuestionOrigin.GENERATED,
                    professor_id=config.professor_id,
                    references=references,
                    option_count=config.option_count,
                )
                if claims_lecture and question.origin != QuestionOrigin.LECTURE_DERIVED:
                    unverified_sources += 1
                problems = validate_question(question, expected_options=config.option_count)
                if problems:
                    for problem in problems:
                        rejected_reasons[problem] = rejected_reasons.get(problem, 0) + 1
                    continue
                similar, score, other_id = is_too_similar(question, avoid + accepted)
                if similar:
                    rejected_reasons["too_similar"] = rejected_reasons.get("too_similar", 0) + 1
                    continue
                question.concept_ids = self._concept_ids_for(question, topic_id)
                shuffle_options(question, seed=question.question_id)
                accepted.append(question)
        if rejected_reasons:
            notes.append("Elenen taslaklar: " + ", ".join(f"{reason}×{count}" for reason, count in sorted(rejected_reasons.items())))
        if unverified_sources:
            notes.append(
                f"{unverified_sources} soruda belirtilen ders kaynağı verilen alıntılarla eşleşmedi; "
                "bu sorular kaynaksız ve 'Üretilmiş' olarak işaretlendi."
            )
        if not accepted:
            raise GenerationError("Model geçerli soru üretemedi; " + (notes[-1] if notes else "tekrar dene."))
        if len(accepted) < needed:
            notes.append(f"{needed} sorudan {len(accepted)} tanesi kalite süzgecinden geçti.")
        self._store.save_questions(accepted)
        return accepted, notes

    def _concept_ids_for(self, question: Question, topic_id: str | None) -> list[str]:
        name = str(question.metadata.get("concept_name") or "")
        ids: list[str] = []
        if name:
            for concept in self._concepts.find(name, limit=2):
                if question.subject == concept.subject or concept.subject == "anatomy":
                    ids.append(concept.concept_id)
                    break
        if not ids:
            for concept in self._concepts.find(question.stem, limit=1):
                if concept.subject == question.subject:
                    ids.append(concept.concept_id)
        if not ids:
            ids.append(f"topic:{topic_id or question.subject}")
        return ids

    # ------------------------------------------------------------------
    # bank-based selection (no model)
    # ------------------------------------------------------------------

    def from_bank(
        self,
        config: ExamConfig,
        *,
        wrong_question_ids: Iterable[str] = (),
        exclude: Iterable[str] = (),
        only_wrong: bool = False,
    ) -> list[Question]:
        """Pick existing questions; ``only_wrong`` never pads from the bank.

        A "wrong answers only" paper padded with unseen questions would be
        labelled as the student's mistakes while most of it never was one.
        """
        excluded = set(exclude)
        wanted = [question_id for question_id in wrong_question_ids if question_id not in excluded]
        chosen: list[Question] = []
        if wanted:
            chosen.extend(self._store.get_questions(wanted))
        if only_wrong:
            return chosen[: config.question_count]
        if len(chosen) < config.question_count:
            candidates: list[Question] = []
            subjects = config.subjects or [None]
            topics = config.topic_ids or [None]
            for subject in subjects:
                for topic_id in topics:
                    candidates.extend(
                        self._store.query_questions(subject=subject, topic_id=topic_id, with_answer_key=True, limit=300)
                    )
            seen = {question.question_id for question in chosen} | excluded
            pool = [question for question in candidates if question.question_id not in seen]
            if config.difficulty:
                pool.sort(key=lambda question: abs(int(question.difficulty) - int(config.difficulty)))
            rng = random.Random(config.title or new_id("seed"))
            head = pool[: max(config.question_count * 2, 10)]
            rng.shuffle(head)
            for question in head:
                if len(chosen) >= config.question_count:
                    break
                if question.question_id not in seen:
                    seen.add(question.question_id)
                    chosen.append(question)
        return chosen[: config.question_count]


class ExamBuilder:
    def __init__(self, store: MedicalStore, curriculum: Curriculum) -> None:
        self._store = store
        self._curriculum = curriculum

    def title_for(self, config: ExamConfig) -> str:
        if config.title.strip():
            return config.title.strip()[:120]
        parts: list[str] = []
        if config.topic_ids:
            topic = self._curriculum.get(config.topic_ids[0])
            if topic is not None:
                parts.append(topic.title_tr)
        elif config.subjects:
            parts.append(" + ".join(SUBJECT_LABELS_TR.get(item, item) for item in config.subjects[:3]))
        if config.wrong_only:
            parts.append("yanlışlar")
        if config.professor_id:
            profile = self._store.get_professor(config.professor_id)
            if profile is not None:
                parts.append(f"{profile.name} tarzı")
        parts.append(f"{config.question_count} soru")
        return " · ".join(parts)

    def build(self, config: ExamConfig, questions: list[Question], *, notes: Iterable[str] = ()) -> Exam:
        if config.randomize:
            order = list(questions)
            random.Random(new_id("exam")).shuffle(order)
            questions = order
        exam = Exam(
            exam_id=new_id("exam"),
            title=self.title_for(config),
            config=config,
            question_ids=[question.question_id for question in questions],
            status="ready",
            mode="study" if config.immediate_feedback else "simulation",
            generation_notes=list(notes),
        )
        self._store.save_exam(exam)
        return exam
