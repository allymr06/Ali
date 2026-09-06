"""Interpretable mastery, spaced review and actionable insights.

No opaque model: a concept's level follows from its recent accuracy and
streak, the next review date from the level, and every review item can
say why it was chosen. Confusions (which wrong option was picked for
which concept) are counted so insights can name the mix-up.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from app.core.time import utc_now
from app.medical.catalog import Curriculum
from app.medical.concepts import ConceptGraph
from app.medical.models import (
    MASTERY_LABELS_TR,
    ConceptMastery,
    MasteryLevel,
    Question,
    SUBJECT_LABELS_TR,
)
from app.medical.store import MedicalStore

RECENT_WINDOW = 8
REVIEW_INTERVALS: dict[str, timedelta] = {
    MasteryLevel.UNKNOWN: timedelta(days=1),
    MasteryLevel.WEAK: timedelta(days=1),
    MasteryLevel.MODERATE: timedelta(days=3),
    MasteryLevel.STRONG: timedelta(days=7),
}
MAX_STRONG_INTERVAL = timedelta(days=30)


def level_for(mastery: ConceptMastery) -> str:
    if mastery.attempts < 2:
        return MasteryLevel.UNKNOWN
    recent = mastery.recent[-RECENT_WINDOW:] or []
    recent_accuracy = sum(1 for item in recent if item) / len(recent) if recent else mastery.accuracy
    if recent_accuracy < 0.5:
        return MasteryLevel.WEAK
    if recent_accuracy < 0.8 or mastery.attempts < 3:
        return MasteryLevel.MODERATE
    return MasteryLevel.STRONG


def next_review_for(mastery: ConceptMastery, now: datetime) -> datetime:
    level = mastery.level
    if level == MasteryLevel.STRONG:
        interval = min(REVIEW_INTERVALS[MasteryLevel.STRONG] * max(1, mastery.streak // 2 + 1), MAX_STRONG_INTERVAL)
    else:
        interval = REVIEW_INTERVALS.get(level, timedelta(days=1))
    return now + interval


def reason_for(mastery: ConceptMastery) -> str:
    recent = mastery.recent[-RECENT_WINDOW:]
    recent_text = f"son {len(recent)} denemede {sum(1 for r in recent if r)} doğru" if recent else "henüz deneme yok"
    label = MASTERY_LABELS_TR.get(mastery.level, mastery.level)
    if mastery.level == MasteryLevel.WEAK:
        return f"{label}: {mastery.attempts} denemede {mastery.correct} doğru ({recent_text})."
    if mastery.level == MasteryLevel.MODERATE:
        return f"{label}: {recent_text}; bir tekrar daha güçlendirir."
    if mastery.level == MasteryLevel.STRONG:
        return f"{label}: {recent_text}, {mastery.streak} doğru serisi; aralıklı tekrar."
    return f"{label}: yalnızca {mastery.attempts} deneme var."


class LearningEngine:
    def __init__(
        self,
        store: MedicalStore,
        curriculum: Curriculum,
        concepts: ConceptGraph | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._curriculum = curriculum
        self._concepts = concepts
        self._clock = clock or utc_now

    # ------------------------------------------------------------------
    # naming
    # ------------------------------------------------------------------

    def concept_name(self, concept_id: str, *, fallback: str = "") -> str:
        if self._concepts is not None:
            concept = self._concepts.get(concept_id)
            if concept is not None:
                return concept.name
        if concept_id.startswith("topic:"):
            topic_id = concept_id[len("topic:") :]
            crumb = self._curriculum.breadcrumb(topic_id)
            return crumb or topic_id
        return fallback or concept_id

    @staticmethod
    def concept_ids_for(question: Question) -> list[str]:
        if question.concept_ids:
            return list(question.concept_ids)
        return [f"topic:{question.topic_id or question.subject}"]

    # ------------------------------------------------------------------
    # recording
    # ------------------------------------------------------------------

    def record(self, question: Question, correct: bool, *, chosen_key: str | None = None) -> list[ConceptMastery]:
        now = self._clock()
        updated: list[ConceptMastery] = []
        for concept_id in self.concept_ids_for(question):
            mastery = self._store.get_mastery(concept_id) or ConceptMastery(concept_id=concept_id, subject=question.subject)
            if not mastery.subject:
                mastery.subject = question.subject
            mastery.attempts += 1
            if correct:
                mastery.correct += 1
                mastery.streak += 1
            else:
                mastery.streak = 0
                chosen = question.option(chosen_key or "") if chosen_key else None
                if chosen is not None:
                    key = (chosen.concept or chosen.text)[:80]
                    mastery.confusions[key] = mastery.confusions.get(key, 0) + 1
            mastery.recent = (mastery.recent + [bool(correct)])[-RECENT_WINDOW:]
            mastery.last_attempt_at = now
            mastery.level = level_for(mastery)
            mastery.next_review_at = next_review_for(mastery, now)
            mastery.reason = reason_for(mastery)
            self._store.save_mastery(mastery)
            updated.append(mastery)
        return updated

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def all(self) -> list[ConceptMastery]:
        return self._store.list_mastery()

    def levels(self) -> dict[str, str]:
        return {item.concept_id: item.level for item in self.all()}

    def weak(self, *, limit: int = 10, subject: str | None = None) -> list[ConceptMastery]:
        items = [
            item
            for item in self.all()
            if item.level in {MasteryLevel.WEAK, MasteryLevel.MODERATE} and (subject is None or item.subject == subject)
        ]
        items.sort(key=lambda item: (item.level != MasteryLevel.WEAK, item.accuracy, -item.attempts))
        return items[: max(1, limit)]

    def strong(self, *, limit: int = 10, subject: str | None = None) -> list[ConceptMastery]:
        items = [item for item in self.all() if item.level == MasteryLevel.STRONG and (subject is None or item.subject == subject)]
        items.sort(key=lambda item: (-item.streak, -item.attempts))
        return items[: max(1, limit)]

    def review_queue(self, *, limit: int = 12, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = now or self._clock()
        due = [item for item in self.all() if item.next_review_at is not None and item.next_review_at <= moment]
        due.sort(key=lambda item: (item.level != MasteryLevel.WEAK, item.level != MasteryLevel.MODERATE, item.next_review_at or moment))
        queue = []
        for item in due[: max(1, limit)]:
            overdue_days = (moment - item.next_review_at).days if item.next_review_at else 0
            queue.append(
                {
                    "concept_id": item.concept_id,
                    "name": self.concept_name(item.concept_id),
                    "subject": item.subject,
                    "subject_label": SUBJECT_LABELS_TR.get(item.subject, item.subject),
                    "level": item.level,
                    "level_label": MASTERY_LABELS_TR.get(item.level, item.level),
                    "reason": item.reason + (f" {overdue_days} gün gecikmiş." if overdue_days > 0 else ""),
                    "attempts": item.attempts,
                    "correct": item.correct,
                    "next_review_at": item.next_review_at.isoformat() if item.next_review_at else None,
                }
            )
        return queue

    def weak_concept_names(self, *, limit: int = 8, subject: str | None = None) -> list[str]:
        return [self.concept_name(item.concept_id) for item in self.weak(limit=limit, subject=subject)]

    def hints_for(self, concept_ids: Iterable[str], *, limit: int = 4) -> list[str]:
        """Short learning-history lines for the tutor prompt."""
        hints: list[str] = []
        for concept_id in concept_ids:
            mastery = self._store.get_mastery(concept_id)
            if mastery is None or mastery.level == MasteryLevel.UNKNOWN:
                continue
            name = self.concept_name(concept_id)
            if mastery.level == MasteryLevel.WEAK:
                confusion = max(mastery.confusions.items(), key=lambda item: item[1])[0] if mastery.confusions else None
                hints.append(f"{name}: weak ({mastery.correct}/{mastery.attempts})" + (f", often confused with '{confusion}'" if confusion else ""))
            elif mastery.level == MasteryLevel.STRONG:
                hints.append(f"{name}: strong, can be brief")
            if len(hints) >= limit:
                break
        return hints

    # ------------------------------------------------------------------
    # adaptive difficulty (transparent rule)
    # ------------------------------------------------------------------

    @staticmethod
    def suggest_difficulty(current: int, recent_results: Iterable[bool]) -> tuple[int, str]:
        results = list(recent_results)[-5:]
        if len(results) < 5:
            return current, "Yeterli deneme yok; zorluk aynı kalıyor."
        correct = sum(1 for item in results if item)
        if correct >= 4 and current < 5:
            return current + 1, f"Son 5 sorunun {correct}'i doğru: zorluk bir kademe arttı."
        if correct <= 1 and current > 1:
            return current - 1, f"Son 5 sorunun yalnız {correct}'i doğru: zorluk bir kademe düştü, eksik kavram önce anlatılacak."
        return current, f"Son 5 soruda {correct} doğru: zorluk aynı kalıyor."

    # ------------------------------------------------------------------
    # insights and summary
    # ------------------------------------------------------------------

    def insights(self, *, limit: int = 5) -> list[str]:
        lines: list[str] = []
        items = self.all()
        for item in sorted(items, key=lambda entry: (entry.level != MasteryLevel.WEAK, -entry.attempts)):
            name = self.concept_name(item.concept_id)
            if item.confusions:
                confusion, count = max(item.confusions.items(), key=lambda pair: pair[1])
                if count >= 2:
                    lines.append(f"{name} sorularında {count} kez '{confusion}' seçeneğine kaydın: bu ikisini ayıran kriteri tekrar et.")
                    continue
            if item.level == MasteryLevel.WEAK and item.attempts >= 3:
                lines.append(f"{name}: {item.attempts} denemede {item.correct} doğru; en zayıf alanlarından biri.")
            elif item.level == MasteryLevel.STRONG and item.streak >= 4:
                lines.append(f"{name}: {item.streak} soruluk doğru serisi; tekrar aralığı uzatıldı.")
            if len(lines) >= limit:
                break
        # Subject-level pattern
        by_subject: dict[str, list[ConceptMastery]] = {}
        for item in items:
            by_subject.setdefault(item.subject or "", []).append(item)
        for subject, entries in by_subject.items():
            attempts = sum(entry.attempts for entry in entries)
            correct = sum(entry.correct for entry in entries)
            if attempts >= 10 and correct / attempts < 0.6 and len(lines) < limit:
                lines.append(f"{SUBJECT_LABELS_TR.get(subject, subject)} genelinde doğruluk %{round(100 * correct / attempts)}; bu dersi öncelikle çalış.")
        return lines[:limit]

    def summary(self) -> dict[str, Any]:
        items = self.all()
        counts = {level.value: 0 for level in MasteryLevel}
        for item in items:
            counts[item.level] = counts.get(item.level, 0) + 1
        attempts = sum(item.attempts for item in items)
        correct = sum(item.correct for item in items)
        return {
            "concepts": len(items),
            "attempts": attempts,
            "correct": correct,
            "accuracy": round(correct / attempts, 3) if attempts else None,
            "levels": counts,
            "due_reviews": len(self.review_queue(limit=100)),
        }

    def mastery_payload(self, mastery: ConceptMastery) -> dict[str, Any]:
        return {
            "concept_id": mastery.concept_id,
            "name": self.concept_name(mastery.concept_id),
            "subject": mastery.subject,
            "subject_label": SUBJECT_LABELS_TR.get(mastery.subject, mastery.subject),
            "attempts": mastery.attempts,
            "correct": mastery.correct,
            "accuracy": round(mastery.accuracy, 3),
            "recent": list(mastery.recent),
            "streak": mastery.streak,
            "level": mastery.level,
            "level_label": MASTERY_LABELS_TR.get(mastery.level, mastery.level),
            "reason": mastery.reason,
            "last_attempt_at": mastery.last_attempt_at.isoformat() if mastery.last_attempt_at else None,
            "next_review_at": mastery.next_review_at.isoformat() if mastery.next_review_at else None,
            "confusions": dict(sorted(mastery.confusions.items(), key=lambda item: -item[1])[:5]),
        }
