"""Prerequisites: what has to be understood first, and where that claim comes from.

The concept graph relates concepts in many ways — a bone articulates with
another, two tissues contrast, a pathway feeds the next. A *prerequisite*
is a different, stronger claim: without concept B, concept A cannot be
understood. This module keeps prerequisite edges apart from the rest, each
with its provenance:

* ``curriculum`` — curated with the order the curriculum teaches things in,
  reviewed as a set (``data/prerequisites.json``);
* ``imported`` — stated by imported material (a lecture says "önce X'i
  hatırlayın"), kept pending until the student confirms it;
* ``model_suggested`` — proposed by the model, clearly labelled, pending.

No other relation is ever turned into a prerequisite. A dependency cycle is
refused at confirmation and tolerated in traversal; an unknown concept has
no prerequisites and says so.

When the student keeps struggling with a concept, :class:`PrerequisiteDiagnosis`
picks a few plausible prerequisites, asks a bounded number of short
questions, locates the deepest one that failed, lays out a short path from
that foundation back to the original concept, and returns to the original
objective. The student can skip, shorten or redirect at any point.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.time import utc_now
from app.medical.models import SUBJECT_LABELS_TR, MasteryLevel, new_id
from app.medical.questions import grade, question_payload

DATA_DIRECTORY = Path(__file__).with_name("data")
PREREQUISITES_FILE = DATA_DIRECTORY / "prerequisites.json"

EDGE_KIND = "prerequisite"
DIAGNOSIS_KIND = "prerequisite_diagnosis"

PROVENANCE_LABELS_TR: dict[str, str] = {
    "curriculum": "Müfredat sırası (gözden geçirilmiş)",
    "imported": "Ders materyalinde belirtilmiş",
    "model_suggested": "Model önerisi",
    "student": "Öğrenci önerdi",
}
STATUS_LABELS_TR: dict[str, str] = {"reviewed": "Onaylı", "pending": "Onay bekliyor", "rejected": "Reddedildi"}

MAX_DEPTH = 2
MAX_CANDIDATES = 3
MAX_DIAGNOSTIC_QUESTIONS = 3
STRUGGLE_ATTEMPTS = 3
STEP_ESTIMATE_MINUTES = 10


@dataclass(slots=True)
class PrerequisiteEdge:
    edge_id: str
    concept_id: str
    requires: str
    provenance: str = "curriculum"
    status: str = "reviewed"
    note: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "concept_id": self.concept_id,
            "requires": self.requires,
            "provenance": self.provenance,
            "provenance_label": PROVENANCE_LABELS_TR.get(self.provenance, self.provenance),
            "status": self.status,
            "status_label": STATUS_LABELS_TR.get(self.status, self.status),
            "note": self.note,
            "source": dict(self.source),
            "created_at": self.created_at,
        }


def load_seed_edges(path: Path | None = None) -> list[PrerequisiteEdge]:
    data = json.loads((path or PREREQUISITES_FILE).read_text(encoding="utf-8"))
    edges: list[PrerequisiteEdge] = []
    for item in data.get("edges", []):
        concept_id = str(item.get("concept_id", "")).strip()
        requires = str(item.get("requires", "")).strip()
        if not concept_id or not requires or concept_id == requires:
            continue
        edges.append(PrerequisiteEdge(edge_id=f"seed:{concept_id}<-{requires}", concept_id=concept_id, requires=requires, provenance="curriculum", status="reviewed", note=str(item.get("note", "")), source={"file": "prerequisites.json", "version": data.get("version")}))
    return edges


class PrerequisiteGraph:
    def __init__(self, concepts: Any, store: Any | None = None, *, seeds: Path | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self._concepts = concepts
        self._store = store
        self._clock = clock or utc_now
        self._seeds = seeds
        self._edges: dict[str, PrerequisiteEdge] = {}
        self.reload()

    def reload(self) -> None:
        self._edges = {}
        for edge in load_seed_edges(self._seeds):
            self._edges[edge.edge_id] = edge
        if self._store is not None:
            for record in self._store.list_records(EDGE_KIND, limit=5000, newest_first=False):
                edge = PrerequisiteEdge(
                    edge_id=str(record.get("edge_id") or record.get("record_id")),
                    concept_id=str(record.get("concept_id", "")),
                    requires=str(record.get("requires", "")),
                    provenance=str(record.get("provenance", "model_suggested")),
                    status=str(record.get("status", "pending")),
                    note=str(record.get("note", "")),
                    source=dict(record.get("source") or {}),
                    created_at=str(record.get("created_at", "")),
                )
                if edge.concept_id and edge.requires:
                    self._edges[edge.edge_id] = edge

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._edges)

    def edges(self, *, reviewed_only: bool = False) -> list[PrerequisiteEdge]:
        items = list(self._edges.values())
        if reviewed_only:
            items = [edge for edge in items if edge.status == "reviewed"]
        return items

    def edge(self, edge_id: str) -> PrerequisiteEdge | None:
        return self._edges.get(edge_id)

    def known(self, concept_id: str) -> bool:
        return self._concepts is not None and concept_id in self._concepts

    def name(self, concept_id: str) -> str:
        concept = self._concepts.get(concept_id) if self._concepts is not None else None
        return concept.name if concept is not None else concept_id

    def subject(self, concept_id: str) -> str:
        concept = self._concepts.get(concept_id) if self._concepts is not None else None
        return concept.subject if concept is not None else ""

    def edges_for(self, concept_id: str, *, reviewed_only: bool = True) -> list[PrerequisiteEdge]:
        return [edge for edge in self._edges.values() if edge.concept_id == concept_id and (edge.status == "reviewed" or not reviewed_only)]

    def dependents_of(self, concept_id: str, *, reviewed_only: bool = True) -> list[PrerequisiteEdge]:
        return [edge for edge in self._edges.values() if edge.requires == concept_id and (edge.status == "reviewed" or not reviewed_only)]

    def prerequisites_of(self, concept_id: str, *, depth: int = MAX_DEPTH, reviewed_only: bool = True) -> list[dict[str, Any]]:
        """Prerequisites up to ``depth`` edges away, nearest first, each once.

        A cycle in the data cannot loop this: a concept already seen is not
        followed again, whatever edge leads back to it.
        """
        found: list[dict[str, Any]] = []
        seen = {concept_id}
        queue: deque[tuple[str, int, list[str]]] = deque([(concept_id, 0, [concept_id])])
        while queue:
            current, level, path = queue.popleft()
            if level >= max(0, depth):
                continue
            for edge in self.edges_for(current, reviewed_only=reviewed_only):
                if edge.requires in seen:
                    continue
                seen.add(edge.requires)
                chain = path + [edge.requires]
                found.append({"concept_id": edge.requires, "name": self.name(edge.requires), "subject": self.subject(edge.requires), "depth": level + 1, "via": current, "provenance": edge.provenance, "status": edge.status, "note": edge.note, "path": chain, "edge_id": edge.edge_id})
                queue.append((edge.requires, level + 1, chain))
        return found

    def path_to(self, foundation_id: str, target_id: str, *, reviewed_only: bool = True) -> list[str]:
        """The concepts from ``foundation_id`` up to ``target_id`` along prerequisite edges, or []."""
        if foundation_id == target_id:
            return [target_id]
        seen = {target_id}
        queue: deque[list[str]] = deque([[target_id]])
        while queue:
            path = queue.popleft()
            for edge in self.edges_for(path[-1], reviewed_only=reviewed_only):
                if edge.requires in seen:
                    continue
                chain = path + [edge.requires]
                if edge.requires == foundation_id:
                    return list(reversed(chain))
                seen.add(edge.requires)
                queue.append(chain)
        return []

    def would_cycle(self, concept_id: str, requires: str) -> bool:
        """True when making ``requires`` a prerequisite of ``concept_id`` closes a loop."""
        if concept_id == requires:
            return True
        return concept_id in {item["concept_id"] for item in self.prerequisites_of(requires, depth=50)}

    # ------------------------------------------------------------------
    # edges learned later: pending until the student says so
    # ------------------------------------------------------------------

    def suggest(self, concept_id: str, requires: str, *, provenance: str = "model_suggested", note: str = "", source: dict[str, Any] | None = None) -> PrerequisiteEdge:
        if self._store is None:
            raise ValueError("Ön koşul önerisi için depo gerekli.")
        if provenance not in ("imported", "model_suggested", "student"):
            raise ValueError("Öneri kaynağı 'imported', 'model_suggested' ya da 'student' olmalı.")
        if not self.known(concept_id) or not self.known(requires):
            raise ValueError("Bilinmeyen kavram: ön koşul kaydedilmedi.")
        if concept_id == requires:
            raise ValueError("Bir kavram kendi ön koşulu olamaz.")
        edge_id = f"{provenance}:{concept_id}<-{requires}"
        existing = self._edges.get(edge_id)
        if existing is not None and existing.status != "rejected":
            return existing
        edge = PrerequisiteEdge(edge_id=edge_id, concept_id=concept_id, requires=requires, provenance=provenance, status="pending", note=str(note)[:300], source=dict(source or {}), created_at=self._clock().isoformat())
        self._store.save_record(EDGE_KIND, edge_id, edge.to_dict(), subject_key=concept_id)
        self._edges[edge_id] = edge
        return edge

    def confirm(self, edge_id: str) -> PrerequisiteEdge:
        edge = self._edges.get(edge_id)
        if edge is None or self._store is None:
            raise ValueError("Ön koşul önerisi bulunamadı.")
        if edge.status == "reviewed":
            return edge
        if self.would_cycle(edge.concept_id, edge.requires):
            raise ValueError(f"Bu ön koşul bir döngü oluşturur: {self.name(edge.requires)} zaten {self.name(edge.concept_id)} kavramına dayanıyor.")
        edge.status = "reviewed"
        self._store.save_record(EDGE_KIND, edge_id, edge.to_dict(), subject_key=edge.concept_id)
        return edge

    def reject(self, edge_id: str) -> PrerequisiteEdge:
        edge = self._edges.get(edge_id)
        if edge is None or self._store is None:
            raise ValueError("Ön koşul önerisi bulunamadı.")
        if edge.provenance == "curriculum":
            raise ValueError("Müfredat ön koşulu buradan silinmez.")
        edge.status = "rejected"
        self._store.save_record(EDGE_KIND, edge_id, edge.to_dict(), subject_key=edge.concept_id)
        return edge

    def payload(self, concept_id: str) -> dict[str, Any]:
        return {
            "concept_id": concept_id,
            "name": self.name(concept_id),
            "known": self.known(concept_id),
            "prerequisites": self.prerequisites_of(concept_id),
            "pending": [{**edge.to_dict(), "requires_name": self.name(edge.requires)} for edge in self.edges_for(concept_id, reviewed_only=False) if edge.status == "pending"],
            "dependents": [{"concept_id": edge.concept_id, "name": self.name(edge.concept_id), "provenance": edge.provenance} for edge in self.dependents_of(concept_id)],
        }


class PrerequisiteDiagnosis:
    """Find the foundation a struggle rests on, repair it briefly, come back."""

    def __init__(self, graph: PrerequisiteGraph, store: Any, learning: Any, understanding: Any | None, curriculum: Any, *, clock: Callable[[], datetime] | None = None) -> None:
        self._graph = graph
        self._store = store
        self._learning = learning
        self._understanding = understanding
        self._curriculum = curriculum
        self._clock = clock or utc_now

    # ------------------------------------------------------------------
    # when to look deeper
    # ------------------------------------------------------------------

    def struggling(self, *, limit: int = 8) -> list[dict[str, Any]]:
        """Concepts worth a look under the surface: repeatedly wrong, or an active finding."""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        if self._understanding is not None:
            for finding in self._understanding.findings(limit=200):
                if finding.get("status") in ("supported", "reopened") and finding["concept_id"] not in seen:
                    seen.add(finding["concept_id"])
                    items.append({"concept_id": finding["concept_id"], "name": finding.get("concept_name") or self._graph.name(finding["concept_id"]), "reason": "Desteklenen yanlış anlama bulgusu var.", "prerequisites": len(self._graph.prerequisites_of(finding["concept_id"]))})
        for mastery in self._learning.all():
            if mastery.concept_id in seen:
                continue
            if mastery.level == MasteryLevel.WEAK and mastery.attempts >= STRUGGLE_ATTEMPTS:
                seen.add(mastery.concept_id)
                items.append({"concept_id": mastery.concept_id, "name": self._graph.name(mastery.concept_id), "reason": f"{mastery.attempts} denemede {mastery.correct} doğru.", "prerequisites": len(self._graph.prerequisites_of(mastery.concept_id))})
        return items[:limit]

    # ------------------------------------------------------------------
    # the diagnosis
    # ------------------------------------------------------------------

    def get(self, diagnosis_id: str) -> dict[str, Any] | None:
        return self._store.get_record(DIAGNOSIS_KIND, diagnosis_id)

    def open_for(self, concept_id: str) -> dict[str, Any] | None:
        for record in self._store.list_records(DIAGNOSIS_KIND, subject_key=concept_id, limit=20):
            if record.get("status") in ("open", "located"):
                return record
        return None

    def _question_for(self, concept_id: str, exclude: set[str]) -> dict[str, Any] | None:
        candidates = [question for question in self._store.query_questions(concept_id=concept_id, with_answer_key=True, limit=30) if question.question_id not in exclude and not question.metadata.get("invalidated")]
        if not candidates:
            return None
        recent = {event.get("question_id") for event in (self._understanding.events(limit=100) if self._understanding is not None else [])}
        candidates.sort(key=lambda item: (item.question_id in recent, item.created_at))
        return question_payload(candidates[0], reveal=False, include_explanation=False, curriculum=self._curriculum)

    def start(self, concept_id: str, *, objective: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
        """Open a diagnosis: up to three prerequisites, each with one short question when the bank has one.

        With no recorded prerequisite the record says so honestly and offers
        the fallbacks; nothing is invented.
        """
        existing = self.open_for(concept_id)
        if existing is not None:
            return existing
        name = self._graph.name(concept_id)
        now = self._clock().isoformat()
        goal = dict(objective or {})
        goal.setdefault("concept_id", concept_id)
        goal.setdefault("name", name)
        record: dict[str, Any] = {
            "diagnosis_id": new_id("dx"),
            "concept_id": concept_id,
            "concept_name": name,
            "subject": self._graph.subject(concept_id),
            "objective": goal,
            "reason": reason,
            "status": "open",
            "opened_at": now,
            "intro": f"{name} konusuna dönmeden önce şu temel kavramı kontrol edelim.",
            "candidates": [],
            "asked": 0,
            "located": None,
            "path": [],
            "steps": [],
            "skipped": [],
            "limitations": [],
            "history": [{"at": now, "note": "Teşhis açıldı." + (f" Neden: {reason}" if reason else "")}],
        }
        if not self._graph.known(concept_id):
            record["status"] = "no_prerequisites"
            record["limitations"].append("Bu kavram kavram grafiğinde kayıtlı değil; ön koşulu bilinmiyor.")
        prerequisites = self._graph.prerequisites_of(concept_id) if self._graph.known(concept_id) else []
        # Nearest first, and among those the weakest-known first.
        levels = self._learning.levels()
        order = {MasteryLevel.WEAK: 0, MasteryLevel.UNKNOWN: 1, MasteryLevel.MODERATE: 2, MasteryLevel.STRONG: 3}
        prerequisites.sort(key=lambda item: (item["depth"], order.get(levels.get(item["concept_id"], MasteryLevel.UNKNOWN), 1)))
        used: set[str] = set()
        for item in prerequisites[:MAX_CANDIDATES]:
            question = self._question_for(item["concept_id"], used)
            if question is not None:
                used.add(question["question_id"])
            record["candidates"].append({**item, "subject_label": SUBJECT_LABELS_TR.get(item.get("subject", ""), item.get("subject", "")), "mastery": levels.get(item["concept_id"], MasteryLevel.UNKNOWN), "question": question, "answer": None, "skipped": False})
        if not prerequisites and record["status"] == "open":
            record["status"] = "no_prerequisites"
            record["limitations"].append(f"{name} için kayıtlı ön koşul yok: müfredat sırasında bundan önce gelen bir kavram işaretlenmemiş.")
        if record["status"] == "no_prerequisites":
            record["fallback"] = [
                "Kavramın kendi sayfasını Kütüphane'de aç ve bir anlama kontrolü yap.",
                "JARVIS'e 'bu kavramı temelden anlat' de.",
                "Bir ön koşul biliyorsan Konular ekranından öner; onayınla kaydedilir.",
            ]
        elif not any(item["question"] for item in record["candidates"]):
            record["limitations"].append("Ön koşullar biliniyor ama soru bankasında onlara ait anahtarlı soru yok; teşhis soruları üretilene kadar yol doğrudan en yakın ön koşuldan başlar.")
            record["located"] = record["candidates"][0]["concept_id"]
            record["path"] = self._graph.path_to(record["located"], concept_id) or [record["located"], concept_id]
            record["steps"] = self._steps(record["path"], goal)
            record["status"] = "located"
        self._store.save_record(DIAGNOSIS_KIND, record["diagnosis_id"], record, subject_key=concept_id)
        return record

    def _steps(self, path: list[str], objective: dict[str, Any]) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for concept_id in path:
            steps.append({"concept_id": concept_id, "name": self._graph.name(concept_id), "subject_label": SUBJECT_LABELS_TR.get(self._graph.subject(concept_id), self._graph.subject(concept_id)), "activity": "Kavramı oku ve bir anlama kontrolü yap", "estimate_minutes": STEP_ESTIMATE_MINUTES, "estimate_label": "tahmini", "status": "planned"})
        steps.append({"concept_id": objective.get("concept_id"), "name": objective.get("name", ""), "activity": "Orijinal hedefe dön" + (f": {objective['question_stem'][:80]}" if objective.get("question_stem") else ""), "estimate_minutes": 5, "estimate_label": "tahmini", "status": "planned", "objective": True})
        return steps

    def answer(self, diagnosis_id: str, concept_id: str, answer_key: str | None, *, confidence: str | None = None, submission_id: str | None = None) -> dict[str, Any]:
        record = self.get(diagnosis_id)
        if record is None:
            raise ValueError("Teşhis bulunamadı.")
        candidate = next((item for item in record["candidates"] if item["concept_id"] == concept_id), None)
        if candidate is None or not candidate.get("question"):
            raise ValueError("Bu kavram için teşhis sorusu yok.")
        if candidate.get("answer") is not None:
            return record
        if record["asked"] >= MAX_DIAGNOSTIC_QUESTIONS:
            raise ValueError("Teşhis soru sınırına ulaşıldı.")
        question = self._store.get_question(candidate["question"]["question_id"])
        if question is None:
            raise ValueError("Soru bulunamadı.")
        correct = grade(question, answer_key)
        self._learning.record(question, bool(correct), chosen_key=answer_key)
        event_id = None
        if self._understanding is not None:
            event = self._understanding.record_event(question, correct=correct, answer_key=answer_key, confidence=confidence, source="diagnosis", submission_id=submission_id)
            event_id = event.get("event_id")
        candidate["answer"] = {"answer_key": answer_key, "correct": correct, "event_id": event_id, "at": self._clock().isoformat()}
        candidate["question"] = question_payload(question, reveal=True, include_explanation=True, curriculum=self._curriculum)
        record["asked"] += 1
        record["history"].append({"at": self._clock().isoformat(), "note": f"{candidate['name']}: {'doğru' if correct else 'yanlış'}."})
        if all(item.get("answer") is not None or item.get("skipped") or not item.get("question") for item in record["candidates"]) or record["asked"] >= MAX_DIAGNOSTIC_QUESTIONS:
            self._locate(record)
        self._store.save_record(DIAGNOSIS_KIND, diagnosis_id, record, subject_key=record["concept_id"])
        return record

    def _locate(self, record: dict[str, Any]) -> None:
        failed = [item for item in record["candidates"] if item.get("answer") and item["answer"].get("correct") is False]
        concept_id = record["concept_id"]
        if failed:
            deepest = max(failed, key=lambda item: item["depth"])
            record["located"] = deepest["concept_id"]
            record["path"] = self._graph.path_to(deepest["concept_id"], concept_id) or [deepest["concept_id"], concept_id]
            record["verdict"] = f"Başlangıç noktası: {deepest['name']}. Buradan {record['concept_name']} konusuna dönen kısa bir yol hazırlandı."
        else:
            unanswered = [item for item in record["candidates"] if item.get("answer") is None and item.get("question") and not item.get("skipped")]
            record["located"] = None
            record["path"] = [concept_id]
            if unanswered:
                record["verdict"] = "Sorulan ön koşullar doğru; kalanlar atlandı. Sorun temelde görünmüyor: doğrudan kavramın kendisine dön."
            else:
                record["verdict"] = "Ön koşullar sağlam görünüyor: sorun bu kavramın kendisinde. Doğrudan kavrama dön."
        record["steps"] = self._steps(record["path"], record["objective"])
        record["status"] = "located"
        record["history"].append({"at": self._clock().isoformat(), "note": record["verdict"]})

    def skip(self, diagnosis_id: str, concept_id: str | None = None) -> dict[str, Any]:
        """Skip one candidate's question, or (while located) one step of the path."""
        record = self.get(diagnosis_id)
        if record is None:
            raise ValueError("Teşhis bulunamadı.")
        if record["status"] == "open":
            for item in record["candidates"]:
                if concept_id is None or item["concept_id"] == concept_id:
                    if item.get("answer") is None:
                        item["skipped"] = True
                        record["skipped"].append(item["concept_id"])
                    if concept_id is not None:
                        break
            if all(item.get("answer") is not None or item.get("skipped") or not item.get("question") for item in record["candidates"]):
                self._locate(record)
        else:
            for step in record.get("steps", []):
                if (concept_id is None or step.get("concept_id") == concept_id) and step.get("status") == "planned" and not step.get("objective"):
                    step["status"] = "skipped"
                    record["skipped"].append(step.get("concept_id"))
                    break
        record["history"].append({"at": self._clock().isoformat(), "note": f"Atlandı: {self._graph.name(concept_id) if concept_id else 'bir adım'}."})
        self._store.save_record(DIAGNOSIS_KIND, diagnosis_id, record, subject_key=record["concept_id"])
        return record

    def shorten(self, diagnosis_id: str) -> dict[str, Any]:
        """Keep only the foundation and the return to the objective."""
        record = self.get(diagnosis_id)
        if record is None or record.get("status") != "located":
            raise ValueError("Kısaltılacak bir yol yok.")
        steps = record.get("steps", [])
        if len(steps) > 2:
            for step in steps[1:-1]:
                if step.get("status") == "planned":
                    step["status"] = "skipped"
        record["history"].append({"at": self._clock().isoformat(), "note": "Yol kısaltıldı: yalnız temel kavram ve dönüş."})
        self._store.save_record(DIAGNOSIS_KIND, diagnosis_id, record, subject_key=record["concept_id"])
        return record

    def redirect(self, diagnosis_id: str, concept_id: str) -> dict[str, Any]:
        """Start the path from a concept the student chooses instead."""
        record = self.get(diagnosis_id)
        if record is None:
            raise ValueError("Teşhis bulunamadı.")
        if not self._graph.known(concept_id):
            raise ValueError("Bilinmeyen kavram.")
        record["located"] = concept_id
        record["path"] = self._graph.path_to(concept_id, record["concept_id"]) or [concept_id, record["concept_id"]]
        record["steps"] = self._steps(record["path"], record["objective"])
        record["status"] = "located"
        record["verdict"] = f"Yol {self._graph.name(concept_id)} kavramından başlatıldı (öğrenci seçimi)."
        record["history"].append({"at": self._clock().isoformat(), "note": record["verdict"]})
        self._store.save_record(DIAGNOSIS_KIND, diagnosis_id, record, subject_key=record["concept_id"])
        return record

    def complete_step(self, diagnosis_id: str, concept_id: str) -> dict[str, Any]:
        record = self.get(diagnosis_id)
        if record is None:
            raise ValueError("Teşhis bulunamadı.")
        for step in record.get("steps", []):
            if step.get("concept_id") == concept_id and step.get("status") == "planned":
                step["status"] = "completed"
                step["completed_at"] = self._clock().isoformat()
                break
        if all(step.get("status") in ("completed", "skipped") for step in record.get("steps", [])):
            record["status"] = "closed"
            record["closed_at"] = self._clock().isoformat()
            record["history"].append({"at": self._clock().isoformat(), "note": "Yol tamamlandı; orijinal hedefe dönüldü."})
        self._store.save_record(DIAGNOSIS_KIND, diagnosis_id, record, subject_key=record["concept_id"])
        return record

    def finish(self, diagnosis_id: str) -> dict[str, Any]:
        """Close the diagnosis now and hand back the original objective."""
        record = self.get(diagnosis_id)
        if record is None:
            raise ValueError("Teşhis bulunamadı.")
        record["status"] = "closed"
        record["closed_at"] = self._clock().isoformat()
        record["history"].append({"at": self._clock().isoformat(), "note": "Teşhis kapatıldı; orijinal hedefe dönüldü."})
        self._store.save_record(DIAGNOSIS_KIND, diagnosis_id, record, subject_key=record["concept_id"])
        return record

    def recent(self, *, limit: int = 10) -> list[dict[str, Any]]:
        return self._store.list_records(DIAGNOSIS_KIND, limit=limit)
