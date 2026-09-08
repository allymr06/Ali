"""Exam-date planning with curriculum coverage.

A plan names an exam and its date (a date, in the student's own time zone,
never a moment), the scope the exam covers (subjects, topics, documents and
page ranges — confirmed by the student, never inferred silently), the
minutes available per day and the days that are not, and optional weights.
From the library, the mastery rows, the understanding findings and the
review queue it computes what each topic in scope is:

    unstudied           in scope, no material opened, no question answered
    studied_unassessed  material opened, no answers
    assessed_limited    answered, but too little or too mixed to say more
    demonstrated        strong mastery with reasoning that held up, and no
                        open finding
    due_review          a concept under it is due in the review queue
    misconception       an open, supported finding needs repair

Opening a PDF is "studied", never "demonstrated". From those states it lays
out one day at a time within the day's budget — never over it — with the
reason and an estimated duration (labelled as an estimate, refined from
the minutes activities actually took) for every activity. Planned, started,
skipped, completed and missed are five different things; a timer running
out completes nothing. When the scope cannot fit the remaining budget the
plan says so with the numbers and shows what stays uncovered. Replanning
after a missed day, a changed budget or a changed scope keeps completed
work and manual adjustments.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from app.core.time import utc_now
from app.medical.learning import is_due
from app.medical.models import SUBJECT_LABELS_TR, MasteryLevel, QuestionOrigin, new_id

PLAN_KIND = "study_plan"
ACTIVITY_KIND = "plan_activity"
STUDY_LOG_KIND = "study_log"

HORIZON_DAYS = 14
MIN_ACTIVITY_MINUTES = 5
DEFAULT_DAILY_MINUTES = 45

COVERAGE_LABELS_TR: dict[str, str] = {
    "misconception": "Onarım bekleyen yanlış anlama",
    "due_review": "Tekrar zamanı geldi",
    "unstudied": "Kapsamda, çalışılmadı",
    "studied_unassessed": "Çalışıldı, ölçülmedi",
    "assessed_limited": "Ölçüldü, kanıt sınırlı",
    "demonstrated": "Anlama gösterildi",
}
ACTIVITY_LABELS_TR: dict[str, str] = {
    "repair": "Yanlış anlamayı onar",
    "review": "Tekrar sorusu",
    "read": "Materyali oku",
    "assess": "Kısa test (5 soru)",
    "practice": "Alıştırma (5 soru)",
    "prerequisite": "Ön koşulu kontrol et",
    "recap": "Kısa hatırlatma",
}
ACTIVITY_STATUS_LABELS_TR: dict[str, str] = {"planned": "Planlandı", "started": "Başlandı", "skipped": "Atlandı", "completed": "Tamamlandı", "missed": "Kaçırıldı"}

# Configurable priorities: how much each state of a topic pulls, before the
# topic's weight and the nearness of the exam.
DEFAULT_PRIORITIES: dict[str, float] = {"misconception": 3.0, "due_review": 2.5, "unstudied": 2.0, "studied_unassessed": 1.8, "prerequisite": 1.5, "assessed_limited": 1.2, "demonstrated": 0.3}
DEFAULT_ESTIMATES: dict[str, int] = {"repair": 15, "review": 10, "read": 20, "assess": 12, "practice": 12, "prerequisite": 10, "recap": 5}
STATE_ACTIVITY: dict[str, str] = {"misconception": "repair", "due_review": "review", "unstudied": "read", "studied_unassessed": "assess", "assessed_limited": "practice", "demonstrated": "recap"}


def parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalı.") from exc


def _clean_minutes(value: Any, default: int = DEFAULT_DAILY_MINUTES) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(minutes, 12 * 60))


class StudyPlanner:
    def __init__(
        self,
        store: Any,
        curriculum: Any,
        concepts: Any,
        learning: Any,
        understanding: Any | None = None,
        prerequisites: Any | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        remind: Callable[[str, str], str | None] | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        self._curriculum = curriculum
        self._concepts = concepts
        self._learning = learning
        self._understanding = understanding
        self._prerequisites = prerequisites
        # The clock returns an aware moment; "today" is its local date.
        self._clock = clock or (lambda: utc_now().astimezone())
        self._remind = remind
        self._emit = emit or (lambda event: None)

    # ------------------------------------------------------------------
    # time
    # ------------------------------------------------------------------

    def now(self) -> datetime:
        moment = self._clock()
        return moment if moment.tzinfo is not None else moment.astimezone()

    def today(self) -> date:
        """The date where the clock is: the student's own, never a UTC date."""
        return self.now().date()

    # ------------------------------------------------------------------
    # plans
    # ------------------------------------------------------------------

    def plans(self, *, include_done: bool = False) -> list[dict[str, Any]]:
        items = self._store.list_records(PLAN_KIND, limit=200)
        if not include_done:
            items = [item for item in items if item.get("status") == "active"]
        items.sort(key=lambda item: (item.get("exam_date", ""), item.get("name", "")))
        return items

    def plan(self, plan_id: str) -> dict[str, Any] | None:
        return self._store.get_record(PLAN_KIND, plan_id)

    def create(
        self,
        name: str,
        exam_date: Any,
        *,
        subjects: list[str] | None = None,
        topic_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        page_ranges: dict[str, list[list[int]]] | None = None,
        daily_minutes: int | dict[str, int] = DEFAULT_DAILY_MINUTES,
        unavailable: list[str] | None = None,
        weights: dict[str, float] | None = None,
        weighting: str = "manual",
        notify: dict[str, Any] | None = None,
        priorities: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        title = " ".join(str(name or "").split())[:80]
        if not title:
            raise ValueError("Sınavın adı boş olamaz.")
        when = parse_date(exam_date)
        if when < self.today():
            raise ValueError("Sınav tarihi geçmişte olamaz.")
        weekdays = {str(day): _clean_minutes(daily_minutes) for day in range(7)} if not isinstance(daily_minutes, dict) else {str(day): _clean_minutes(daily_minutes.get(str(day), daily_minutes.get(day, DEFAULT_DAILY_MINUTES))) for day in range(7)}
        scope = self._scope(subjects, topic_ids, document_ids, page_ranges)
        inferred = bool(document_ids) and not (topic_ids or subjects)
        now = self.now().isoformat()
        record = {
            "plan_id": new_id("plan"),
            "name": title,
            "exam_date": when.isoformat(),
            "status": "active",
            "scope": scope if not inferred else {"subjects": [], "topic_ids": [], "document_ids": list(document_ids or []), "page_ranges": dict(page_ranges or {})},
            "proposed_scope": scope if inferred else None,
            "scope_confirmed": not inferred,
            "budget": {"weekdays": weekdays, "overrides": {}, "unavailable": sorted({parse_date(item).isoformat() for item in (unavailable or [])})},
            "weights": {str(key): float(value) for key, value in (weights or {}).items()},
            "weighting": weighting if weighting in ("manual", "evidence") else "manual",
            "priorities": {**DEFAULT_PRIORITIES, **{key: float(value) for key, value in (priorities or {}).items() if key in DEFAULT_PRIORITIES}},
            "estimates": {},
            "notify": {"enabled": bool((notify or {}).get("enabled", False)), "time": str((notify or {}).get("time") or "09:00")},
            "reminders": {},
            "created_at": now,
            "history": [{"at": now, "note": "Plan oluşturuldu." + (" Kapsam belgelerden önerildi; onay bekliyor." if inferred else "")}],
        }
        self._store.save_record(PLAN_KIND, record["plan_id"], record, subject_key=record["status"])
        return record

    def _scope(self, subjects, topic_ids, document_ids, page_ranges, *, expand_documents: bool = True) -> dict[str, Any]:
        """The scope as the student stated it; documents add their topics only when asked to."""
        topics = [item for item in (topic_ids or []) if self._curriculum.exists(item)]
        subject_list = [item for item in (subjects or []) if item in self._curriculum.subject_ids()]
        documents = []
        for document_id in document_ids or []:
            document = self._store.get_document(document_id)
            if document is None:
                continue
            documents.append(document_id)
            if not expand_documents:
                continue
            for topic_id in document.topic_ids:
                if self._curriculum.exists(topic_id) and topic_id not in topics:
                    topics.append(topic_id)
        return {"subjects": subject_list, "topic_ids": topics, "document_ids": documents, "page_ranges": {key: [[int(a), int(b)] for a, b in value] for key, value in (page_ranges or {}).items() if key in documents}}

    def confirm_scope(self, plan_id: str, *, subjects: list[str] | None = None, topic_ids: list[str] | None = None, document_ids: list[str] | None = None, page_ranges: dict[str, list[list[int]]] | None = None) -> dict[str, Any]:
        record = self._require(plan_id)
        proposed = record.get("proposed_scope") or record["scope"]
        scope = self._scope(subjects if subjects is not None else proposed.get("subjects"), topic_ids if topic_ids is not None else proposed.get("topic_ids"), document_ids if document_ids is not None else proposed.get("document_ids"), page_ranges if page_ranges is not None else proposed.get("page_ranges"), expand_documents=topic_ids is None and subjects is None)
        record["scope"] = scope
        record["proposed_scope"] = None
        record["scope_confirmed"] = True
        record["history"].append({"at": self.now().isoformat(), "note": f"Kapsam onaylandı: {len(self.topics_in_scope(record))} konu."})
        self._save(record)
        return self.replan(plan_id, reason="Kapsam onaylandı.")

    def update(self, plan_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Change the date, the budget, the scope, the weights or the notifications; history keeps the old values."""
        record = self._require(plan_id)
        changes: list[str] = []
        if "exam_date" in fields:
            when = parse_date(fields["exam_date"])
            if when.isoformat() != record["exam_date"]:
                changes.append(f"sınav tarihi {record['exam_date']} → {when.isoformat()}")
                record["exam_date"] = when.isoformat()
        if "name" in fields and str(fields["name"]).strip():
            record["name"] = " ".join(str(fields["name"]).split())[:80]
        if "daily_minutes" in fields:
            value = fields["daily_minutes"]
            weekdays = {str(day): _clean_minutes(value) for day in range(7)} if not isinstance(value, dict) else {**record["budget"]["weekdays"], **{str(day): _clean_minutes(minutes) for day, minutes in value.items()}}
            if weekdays != record["budget"]["weekdays"]:
                changes.append("günlük süre değişti")
                record["budget"]["weekdays"] = weekdays
        if "overrides" in fields and isinstance(fields["overrides"], dict):
            record["budget"]["overrides"] = {parse_date(key).isoformat(): _clean_minutes(value) for key, value in fields["overrides"].items()}
            changes.append("gün bazlı süre değişti")
        if "unavailable" in fields:
            record["budget"]["unavailable"] = sorted({parse_date(item).isoformat() for item in fields["unavailable"] or []})
            changes.append("boş günler değişti")
        if any(key in fields for key in ("subjects", "topic_ids", "document_ids", "page_ranges")):
            record["scope"] = self._scope(fields.get("subjects", record["scope"]["subjects"]), fields.get("topic_ids", record["scope"]["topic_ids"]), fields.get("document_ids", record["scope"]["document_ids"]), fields.get("page_ranges", record["scope"]["page_ranges"]), expand_documents="topic_ids" not in fields and "subjects" not in fields)
            record["scope_confirmed"] = True
            record["proposed_scope"] = None
            changes.append("kapsam değişti")
        if "weights" in fields and isinstance(fields["weights"], dict):
            record["weights"] = {str(key): float(value) for key, value in fields["weights"].items()}
            changes.append("ağırlıklar değişti")
        if "weighting" in fields and fields["weighting"] in ("manual", "evidence"):
            record["weighting"] = fields["weighting"]
        if "priorities" in fields and isinstance(fields["priorities"], dict):
            record["priorities"] = {**record.get("priorities", DEFAULT_PRIORITIES), **{key: float(value) for key, value in fields["priorities"].items() if key in DEFAULT_PRIORITIES}}
            changes.append("öncelikler değişti")
        if "notify" in fields and isinstance(fields["notify"], dict):
            record["notify"] = {"enabled": bool(fields["notify"].get("enabled", record["notify"]["enabled"])), "time": str(fields["notify"].get("time") or record["notify"]["time"])}
        if "status" in fields and fields["status"] in ("active", "done", "archived"):
            record["status"] = fields["status"]
        if changes:
            record["history"].append({"at": self.now().isoformat(), "note": "Değişti: " + ", ".join(changes) + "."})
            self._save(record)
            return self.replan(plan_id, reason=", ".join(changes))
        self._save(record)
        return record

    def delete(self, plan_id: str) -> bool:
        for activity in self._store.list_records(ACTIVITY_KIND, subject_key=plan_id, limit=5000):
            self._store.delete_record(ACTIVITY_KIND, activity["activity_id"])
        return self._store.delete_record(PLAN_KIND, plan_id)

    def _require(self, plan_id: str) -> dict[str, Any]:
        record = self.plan(plan_id)
        if record is None:
            raise ValueError("Plan bulunamadı.")
        return record

    def _save(self, record: dict[str, Any]) -> dict[str, Any]:
        record["updated_at"] = self.now().isoformat()
        return self._store.save_record(PLAN_KIND, record["plan_id"], record, subject_key=record.get("status", "active"))

    # ------------------------------------------------------------------
    # scope and coverage
    # ------------------------------------------------------------------

    def topics_in_scope(self, record: dict[str, Any]) -> list[Any]:
        """Leaf topics the plan covers, in curriculum order, each once."""
        scope = record.get("scope") or {}
        chosen: dict[str, Any] = {}
        for subject in scope.get("subjects", []):
            for topic in self._curriculum.descendants(subject):
                if not self._curriculum.children(topic.topic_id):
                    chosen.setdefault(topic.topic_id, topic)
        for topic_id in scope.get("topic_ids", []):
            topic = self._curriculum.get(topic_id)
            if topic is None:
                continue
            leaves = [item for item in self._curriculum.descendants(topic_id) if not self._curriculum.children(item.topic_id)]
            for leaf in leaves or [topic]:
                chosen.setdefault(leaf.topic_id, leaf)
        return list(chosen.values())

    def _study_logs(self) -> list[dict[str, Any]]:
        return self._store.list_records(STUDY_LOG_KIND, limit=5000)

    def log_study(self, *, topic_id: str | None = None, document_id: str | None = None, activity: str = "read", minutes: int | None = None, page_number: int | None = None) -> dict[str, Any]:
        """Record that material was opened or worked on. It marks a topic studied, never mastered."""
        topics = [topic_id] if topic_id else []
        if document_id:
            document = self._store.get_document(document_id)
            if document is not None:
                topics.extend(item for item in document.topic_ids if item not in topics)
        record = {"log_id": new_id("log"), "topic_ids": [item for item in topics if item], "document_id": document_id, "page_number": page_number, "activity": activity, "minutes": minutes, "at": self.now().isoformat()}
        self._store.save_record(STUDY_LOG_KIND, record["log_id"], record, subject_key=(topics[0] if topics else document_id) or "-")
        return record

    def coverage(self, plan_id: str) -> dict[str, Any]:
        record = self._require(plan_id)
        return self._coverage(record)

    def _coverage(self, record: dict[str, Any]) -> dict[str, Any]:
        topics = self.topics_in_scope(record)
        logs = self._study_logs()
        studied_topics: set[str] = set()
        for log in logs:
            studied_topics.update(log.get("topic_ids", []))
        attempts = self._store.list_attempts(limit=2000)
        answered_by_topic: dict[str, list[bool]] = {}
        question_cache: dict[str, Any] = {}
        for attempt in attempts:
            for question_id, entry in attempt.answers.items():
                if not entry.answer_key or entry.correct is None:
                    continue
                if question_id not in question_cache:
                    question_cache[question_id] = self._store.get_question(question_id)
                question = question_cache[question_id]
                if question is None or question.metadata.get("invalidated"):
                    continue
                answered_by_topic.setdefault(question.topic_id or "", []).append(bool(entry.correct))
        mastery = {item.concept_id: item for item in self._learning.all()}
        moment = self.now()
        events = self._understanding.events(limit=2000) if self._understanding is not None else []
        rows: list[dict[str, Any]] = []
        counts: dict[str, int] = {key: 0 for key in COVERAGE_LABELS_TR}
        for topic in topics:
            topic_id = topic.topic_id
            concept_ids = [concept.concept_id for concept in self._concepts.by_topic(topic_id)] if self._concepts is not None else []
            concept_mastery = [mastery[concept_id] for concept_id in concept_ids if concept_id in mastery]
            topic_answers = answered_by_topic.get(topic_id, [])
            # Exam answers feed the mastery rows too, so the larger of the two
            # counts is the topic's evidence, not their sum.
            attempts_count = max(len(topic_answers), sum(item.attempts for item in concept_mastery))
            studied = topic_id in studied_topics or any(self._curriculum.is_within(item, topic_id) for item in studied_topics)
            findings = self._understanding.open_findings_for(concept_ids) if (self._understanding is not None and concept_ids) else []
            active = [item for item in findings if item.get("status") in ("supported", "reopened")]
            due = any(is_due(item, moment) for item in concept_mastery)
            strong = [item for item in concept_mastery if item.level == MasteryLevel.STRONG]
            supported_reasoning = any(event.get("classification") == "correct_supported" and not event.get("invalidated") and set(event.get("concept_ids", [])) & set(concept_ids) for event in events)
            flags = {
                "studied": studied,
                "assessed": attempts_count > 0,
                "due_review": due,
                "misconception": bool(active),
                "demonstrated": bool(strong) and not findings and supported_reasoning,
            }
            if active:
                state = "misconception"
            elif due:
                state = "due_review"
            elif not studied and attempts_count == 0:
                state = "unstudied"
            elif attempts_count == 0:
                state = "studied_unassessed"
            elif flags["demonstrated"]:
                state = "demonstrated"
            else:
                state = "assessed_limited"
            counts[state] += 1
            rows.append(
                {
                    "topic_id": topic_id,
                    "title": topic.title_tr,
                    "subject": topic.subject,
                    "subject_label": SUBJECT_LABELS_TR.get(topic.subject, topic.subject),
                    "path": self._curriculum.breadcrumb(topic_id),
                    "state": state,
                    "state_label": COVERAGE_LABELS_TR[state],
                    "flags": flags,
                    "attempts": attempts_count,
                    "correct": max(sum(1 for item in topic_answers if item), sum(item.correct for item in concept_mastery)),
                    "concepts": len(concept_ids),
                    "findings": len(active),
                    "finding_concepts": [item["concept_id"] for item in active],
                    "weight": self._weight(record, topic_id),
                }
            )
        return {"plan_id": record["plan_id"], "topics": rows, "counts": counts, "total": len(rows), "labels": COVERAGE_LABELS_TR}

    def _weight(self, record: dict[str, Any], topic_id: str) -> float:
        manual = record.get("weights") or {}
        if topic_id in manual:
            return max(0.1, float(manual[topic_id]))
        if record.get("weighting") == "evidence":
            imported = self._store.query_questions(topic_id=topic_id, origin=QuestionOrigin.IMPORTED_EXAM, limit=50)
            return 1.0 + min(1.0, len(imported) / 10)
        return 1.0

    # ------------------------------------------------------------------
    # activities and days
    # ------------------------------------------------------------------

    def activities(self, plan_id: str, *, day: date | str | None = None) -> list[dict[str, Any]]:
        items = self._store.list_records(ACTIVITY_KIND, subject_key=plan_id, limit=5000, newest_first=False)
        if day is not None:
            wanted = parse_date(day).isoformat()
            items = [item for item in items if item.get("date") == wanted]
        items.sort(key=lambda item: (item.get("date", ""), int(item.get("order", 0))))
        return items

    def budget_for(self, record: dict[str, Any], day: date) -> int:
        budget = record.get("budget") or {}
        key = day.isoformat()
        if key in budget.get("unavailable", []):
            return 0
        if key in (budget.get("overrides") or {}):
            return _clean_minutes(budget["overrides"][key])
        return _clean_minutes((budget.get("weekdays") or {}).get(str(day.weekday()), DEFAULT_DAILY_MINUTES))

    def _estimate(self, record: dict[str, Any], kind: str) -> tuple[int, str]:
        """The default, pulled towards what this kind of work actually took.

        One long session does not become the new estimate on its own: the
        default counts as one observation, so the estimate moves with the
        evidence rather than jumping to it.
        """
        default = DEFAULT_ESTIMATES.get(kind, 10)
        observed = (record.get("estimates") or {}).get(kind)
        if isinstance(observed, dict) and observed.get("minutes") and int(observed.get("count", 0)) > 0:
            count = int(observed["count"])
            blended = (default + float(observed["minutes"]) * count) / (count + 1)
            return max(MIN_ACTIVITY_MINUTES, int(round(blended))), f"tahmini (gözlemden: {count} kez)"
        return default, "tahmini"

    def _candidates(self, record: dict[str, Any], coverage: dict[str, Any], *, exclude_topics: set[str]) -> list[dict[str, Any]]:
        priorities = {**DEFAULT_PRIORITIES, **(record.get("priorities") or {})}
        days_left = max(1, (parse_date(record["exam_date"]) - self.today()).days)
        urgency = 1.0 + 1.0 / days_left
        items: list[dict[str, Any]] = []
        for index, row in enumerate(coverage["topics"]):
            if row["topic_id"] in exclude_topics:
                continue
            state = row["state"]
            kind = STATE_ACTIVITY[state]
            score = priorities.get(state, 1.0) * row["weight"] * urgency
            reason = {
                "misconception": f"{row['findings']} desteklenen yanlış anlama bulgusu onarım bekliyor.",
                "due_review": "Konunun bir kavramı tekrar kuyruğunda.",
                "unstudied": "Sınav kapsamında ama hiç açılmadı.",
                "studied_unassessed": "Okundu ama hiç soru çözülmedi; bilgi ölçülmedi.",
                "assessed_limited": f"{row['attempts']} cevap var; kanıt hâlâ sınırlı.",
                "demonstrated": "Anlama gösterildi; kısa bir hatırlatma yeter.",
            }[state]
            minutes, label = self._estimate(record, kind)
            items.append({"topic_id": row["topic_id"], "title": row["title"], "kind": kind, "kind_label": ACTIVITY_LABELS_TR[kind], "state": state, "reason": reason, "estimate_minutes": minutes, "estimate_label": label, "score": round(score, 3), "order": index})
            if state == "misconception" and self._prerequisites is not None:
                # Only the concepts the findings are about: a neighbour's
                # prerequisite is not this gap's foundation.
                for concept_id in row.get("finding_concepts", [])[:3]:
                    for prerequisite in self._prerequisites.prerequisites_of(concept_id, depth=1):
                        level = self._learning.levels().get(prerequisite["concept_id"], MasteryLevel.UNKNOWN)
                        if level in (MasteryLevel.WEAK, MasteryLevel.UNKNOWN):
                            minutes, label = self._estimate(record, "prerequisite")
                            items.append({"topic_id": row["topic_id"], "concept_id": prerequisite["concept_id"], "title": prerequisite["name"], "kind": "prerequisite", "kind_label": ACTIVITY_LABELS_TR["prerequisite"], "state": "prerequisite", "reason": f"{row['title']} konusundaki bulgu {prerequisite['name']} ön koşuluna dayanabilir ({prerequisite['provenance_label'] if 'provenance_label' in prerequisite else prerequisite['provenance']}).", "estimate_minutes": minutes, "estimate_label": label, "score": round(priorities.get("prerequisite", 1.5) * row["weight"] * urgency, 3), "order": index})
                            break
        items.sort(key=lambda item: (-item["score"], item["order"], item["kind"]))
        return items

    def _existing_minutes(self, activities: list[dict[str, Any]]) -> int:
        return sum(int(item.get("estimate_minutes", 0)) for item in activities if item.get("status") in ("planned", "started", "completed"))

    def replan(self, plan_id: str, *, reason: str = "") -> dict[str, Any]:
        """Lay the horizon out again from today, keeping what was done or set by hand.

        Yesterday's planned work that was never started is marked missed and
        goes back into the pool; nothing is carried over above a day's
        budget, so a missed day never becomes an impossible backlog.
        """
        record = self._require(plan_id)
        today = self.today()
        exam = parse_date(record["exam_date"])
        for activity in self.activities(plan_id):
            when = parse_date(activity["date"])
            if when < today and activity.get("status") == "planned":
                activity["status"] = "missed"
                self._store.save_record(ACTIVITY_KIND, activity["activity_id"], activity, subject_key=plan_id)
            elif when >= today and activity.get("status") == "planned" and not activity.get("manual"):
                self._store.delete_record(ACTIVITY_KIND, activity["activity_id"])
        if not record.get("scope_confirmed"):
            record["history"].append({"at": self.now().isoformat(), "note": "Plan bekliyor: kapsam onaylanmadı."})
            self._save(record)
            return self.summary(plan_id)
        coverage = self._coverage(record)
        kept = self.activities(plan_id)
        done_recently = {item["topic_id"] for item in kept if item.get("status") in ("completed", "started") and item.get("kind") in ("read", "recap") and parse_date(item["date"]) >= today - timedelta(days=3)}
        candidates = self._candidates(record, coverage, exclude_topics=done_recently)
        horizon_end = min(exam, today + timedelta(days=HORIZON_DAYS - 1))
        day = today
        order_base = 100
        scheduled: set[tuple[str, str]] = {(item["topic_id"], item["kind"]) for item in kept if item.get("status") in ("planned", "started", "completed") and parse_date(item["date"]) >= today}
        remaining = [item for item in candidates if (item["topic_id"], item["kind"]) not in scheduled]
        while day <= horizon_end and remaining:
            budget = self.budget_for(record, day)
            used = self._existing_minutes([item for item in kept if item.get("date") == day.isoformat()])
            order = order_base
            leftovers: list[dict[str, Any]] = []
            for candidate in remaining:
                minutes = candidate["estimate_minutes"]
                split = False
                if minutes > budget and budget > 0 and used == 0:
                    # Longer than any one day allows: today's session is the
                    # day's budget and the activity says it continues.
                    minutes, split = budget, True
                if minutes and used + minutes <= budget:
                    activity = {**candidate, "estimate_minutes": minutes, "split": split, "activity_id": new_id("act"), "plan_id": plan_id, "date": day.isoformat(), "status": "planned", "order": order, "manual": False, "started_at": None, "completed_at": None, "actual_minutes": None}
                    if split:
                        activity["reason"] = candidate["reason"] + f" Tahmini {candidate['estimate_minutes']} dk: bir güne sığmaz, birkaç oturuma yayılır."
                    self._store.save_record(ACTIVITY_KIND, activity["activity_id"], activity, subject_key=plan_id)
                    used += minutes
                    order += 1
                else:
                    leftovers.append(candidate)
            remaining = leftovers
            day += timedelta(days=1)
        record["uncovered"] = [{"topic_id": item["topic_id"], "title": item["title"], "kind": item["kind"], "estimate_minutes": item["estimate_minutes"], "reason": item["reason"]} for item in remaining]
        record["last_planned_at"] = self.now().isoformat()
        if reason:
            record["history"].append({"at": self.now().isoformat(), "note": f"Yeniden planlandı: {reason}"})
        self._save(record)
        return self.summary(plan_id)

    def summary(self, plan_id: str) -> dict[str, Any]:
        record = self._require(plan_id)
        today = self.today()
        exam = parse_date(record["exam_date"])
        days_left = (exam - today).days
        coverage = self._coverage(record) if record.get("scope_confirmed") else {"topics": [], "counts": {key: 0 for key in COVERAGE_LABELS_TR}, "total": 0, "labels": COVERAGE_LABELS_TR}
        activities = self.activities(plan_id)
        future = [item for item in activities if parse_date(item["date"]) >= today and item.get("status") in ("planned", "started")]
        available = sum(self.budget_for(record, today + timedelta(days=offset)) for offset in range(max(0, days_left) + 1))
        needed = sum(int(item.get("estimate_minutes", 0)) for item in future) + sum(int(item.get("estimate_minutes", 0)) for item in record.get("uncovered", []))
        fit = needed <= available
        return {
            **{key: record[key] for key in ("plan_id", "name", "exam_date", "status", "scope", "proposed_scope", "scope_confirmed", "budget", "weights", "weighting", "priorities", "notify", "history", "estimates")},
            "days_left": days_left,
            "today": today.isoformat(),
            "coverage": coverage,
            "planned": len(future),
            "planned_minutes": sum(int(item.get("estimate_minutes", 0)) for item in future),
            "available_minutes": available,
            "needed_minutes": needed,
            "fit": fit,
            "overload": None if fit else {"needed_minutes": needed, "available_minutes": available, "short_by": needed - available, "message": f"Kapsam kalan süreye sığmıyor: tahmini {needed} dk iş var, sınava kadar {available} dk boş. Plan en öncelikli işleri sığdırdı; {len(record.get('uncovered', []))} etkinlik açıkta kaldı."},
            "uncovered": list(record.get("uncovered", [])),
            "days": self._days(record, activities, today, min(exam, today + timedelta(days=6))),
        }

    def _days(self, record: dict[str, Any], activities: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
        days: list[dict[str, Any]] = []
        day = start
        while day <= end:
            items = [item for item in activities if item.get("date") == day.isoformat()]
            days.append({"date": day.isoformat(), "weekday": day.weekday(), "budget": self.budget_for(record, day), "planned_minutes": sum(int(item.get("estimate_minutes", 0)) for item in items if item.get("status") in ("planned", "started", "completed")), "activities": [self.activity_payload(item) for item in items]})
            day += timedelta(days=1)
        return days

    def activity_payload(self, activity: dict[str, Any]) -> dict[str, Any]:
        return {**activity, "status_label": ACTIVITY_STATUS_LABELS_TR.get(activity.get("status", "planned"), activity.get("status", ""))}

    # ------------------------------------------------------------------
    # today
    # ------------------------------------------------------------------

    def today_view(self, plan_id: str | None = None) -> dict[str, Any]:
        """The practical view: today's activities, the next action, the numbers that matter."""
        records = [self._require(plan_id)] if plan_id else self.plans()
        if not records:
            return {"plan": None, "date": self.today().isoformat(), "activities": [], "next": None, "message": "Sınav planı yok. Bir sınav ekleyip kapsamını ve günlük sürenizi girin."}
        record = records[0]
        if not record.get("scope_confirmed"):
            return {"plan": {"plan_id": record["plan_id"], "name": record["name"], "exam_date": record["exam_date"]}, "date": self.today().isoformat(), "activities": [], "next": None, "message": "Kapsam belgelerden önerildi; planlamadan önce onayla ya da düzelt.", "proposed_scope": record.get("proposed_scope")}
        today = self.today()
        if record.get("last_planned_at") is None or parse_date(record["last_planned_at"][:10]) < today:
            self.replan(record["plan_id"], reason="yeni gün")
            record = self._require(record["plan_id"])
        items = [self.activity_payload(item) for item in self.activities(record["plan_id"], day=today)]
        pending = [item for item in items if item["status"] in ("planned", "started")]
        started = [item for item in pending if item["status"] == "started"]
        next_item = (started or pending or [None])[0]
        summary = self.summary(record["plan_id"])
        budget = self.budget_for(record, today)
        done_minutes = sum(int(item.get("actual_minutes") or item.get("estimate_minutes", 0)) for item in items if item["status"] == "completed")
        self._maybe_remind(record, today, items)
        return {
            "plan": {"plan_id": record["plan_id"], "name": record["name"], "exam_date": record["exam_date"], "days_left": summary["days_left"]},
            "date": today.isoformat(),
            "budget": budget,
            "planned_minutes": sum(int(item.get("estimate_minutes", 0)) for item in items if item["status"] in ("planned", "started", "completed")),
            "done_minutes": done_minutes,
            "activities": items,
            "next": next_item,
            "message": ("Bugün için plan yok: gün boş bırakılmış." if budget == 0 else ("Bugünün işi bitti." if items and not pending else ("Bugün için sığan etkinlik yok." if not items else ""))),
            "fit": summary["fit"],
            "overload": summary["overload"],
            "uncovered_count": len(summary["uncovered"]),
            "coverage_counts": summary["coverage"]["counts"],
        }

    def _maybe_remind(self, record: dict[str, Any], today: date, items: list[dict[str, Any]]) -> None:
        notify = record.get("notify") or {}
        if not notify.get("enabled") or self._remind is None or not items:
            return
        key = today.isoformat()
        if key in (record.get("reminders") or {}):
            return
        text = f"{record['name']}: bugün {len(items)} çalışma etkinliği planlı ({sum(int(item.get('estimate_minutes', 0)) for item in items)} dk tahmini)."
        try:
            reminder_id = self._remind(text, str(notify.get("time") or "09:00"))
        except Exception:
            reminder_id = None
        record.setdefault("reminders", {})[key] = reminder_id
        self._save(record)

    # ------------------------------------------------------------------
    # doing the work
    # ------------------------------------------------------------------

    def _activity(self, activity_id: str) -> dict[str, Any]:
        record = self._store.get_record(ACTIVITY_KIND, activity_id)
        if record is None:
            raise ValueError("Etkinlik bulunamadı.")
        return record

    def start(self, activity_id: str) -> dict[str, Any]:
        activity = self._activity(activity_id)
        if activity.get("status") in ("planned", "missed", "skipped"):
            activity["status"] = "started"
            activity["started_at"] = self.now().isoformat()
            self._store.save_record(ACTIVITY_KIND, activity_id, activity, subject_key=activity["plan_id"])
        return self.activity_payload(activity)

    def complete(self, activity_id: str, *, minutes: int | None = None) -> dict[str, Any]:
        """Completion is the student's word, never a timer's; the minutes it took refine the estimate."""
        activity = self._activity(activity_id)
        if activity.get("status") == "completed":
            return self.activity_payload(activity)
        now = self.now()
        actual = minutes
        if actual is None and activity.get("started_at"):
            actual = max(1, int(round((now - datetime.fromisoformat(activity["started_at"])).total_seconds() / 60)))
        activity["status"] = "completed"
        activity["completed_at"] = now.isoformat()
        activity["actual_minutes"] = actual
        self._store.save_record(ACTIVITY_KIND, activity_id, activity, subject_key=activity["plan_id"])
        record = self.plan(activity["plan_id"])
        if record is not None:
            if actual:
                estimates = record.setdefault("estimates", {})
                previous = estimates.get(activity["kind"]) or {}
                count = int(previous.get("count", 0))
                average = float(previous.get("minutes", DEFAULT_ESTIMATES.get(activity["kind"], 10)))
                estimates[activity["kind"]] = {"minutes": round((average * count + actual) / (count + 1), 1) if count else float(actual), "count": count + 1}
            self._save(record)
            if activity.get("kind") in ("read", "recap"):
                self.log_study(topic_id=activity.get("topic_id"), activity=activity["kind"], minutes=actual)
        return self.activity_payload(activity)

    def skip(self, activity_id: str, note: str = "") -> dict[str, Any]:
        activity = self._activity(activity_id)
        if activity.get("status") in ("planned", "started"):
            activity["status"] = "skipped"
            activity["skip_note"] = " ".join(str(note or "").split())[:200]
            self._store.save_record(ACTIVITY_KIND, activity_id, activity, subject_key=activity["plan_id"])
        return self.activity_payload(activity)

    def add_manual(self, plan_id: str, *, day: Any, title: str, minutes: int, topic_id: str | None = None, kind: str = "read", reason: str = "Elle eklendi") -> dict[str, Any]:
        """A manual adjustment survives every replan; it still has to fit the day."""
        record = self._require(plan_id)
        when = parse_date(day)
        budget = self.budget_for(record, when)
        used = self._existing_minutes(self.activities(plan_id, day=when))
        estimate = max(MIN_ACTIVITY_MINUTES, int(minutes))
        if used + estimate > budget:
            raise ValueError(f"Bu gün için {budget} dk ayrılmış, {used} dk planlı: {estimate} dk daha sığmıyor.")
        activity = {"activity_id": new_id("act"), "plan_id": plan_id, "date": when.isoformat(), "kind": kind if kind in ACTIVITY_LABELS_TR else "read", "kind_label": ACTIVITY_LABELS_TR.get(kind, ACTIVITY_LABELS_TR["read"]), "topic_id": topic_id, "title": " ".join(str(title).split())[:80] or "Çalışma", "state": "manual", "reason": reason, "estimate_minutes": estimate, "estimate_label": "öğrencinin tahmini", "score": 0, "order": 50, "status": "planned", "manual": True, "started_at": None, "completed_at": None, "actual_minutes": None}
        self._store.save_record(ACTIVITY_KIND, activity["activity_id"], activity, subject_key=plan_id)
        record["history"].append({"at": self.now().isoformat(), "note": f"Elle eklendi: {activity['title']} ({when.isoformat()})."})
        self._save(record)
        return self.activity_payload(activity)
