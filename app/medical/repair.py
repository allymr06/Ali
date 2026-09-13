"""Correct learning summaries that earlier code derived from evidence it should not have used.

Two faults left wrong rows behind: an answer to a question the scoring
policy does not count (no source, unreviewed, conflicting) was written into
concept mastery and the understanding evidence as if it measured something;
and a histology answer was filed under the nearest concept name rather than
the exact one, so "tek katlı kübik epitel" moved "tek katlı yassı epitel".

This module puts the summaries back in line with the source records — the
attempts, the sessions, the events — without deleting any of them. Every
correction is written to the ``learning_repair`` ledger under a key, so
running it again applies nothing twice; the database is backed up first,
and the report says exactly what changed and what was already done.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.time import utc_now
from app.medical.models import new_id
from app.medical.questions import analyse_attempt
from app.medical.review import SCORING_POLICY

REPAIR_KIND = "learning_repair"
EVENT_KIND = "understanding_event"


def backup_database(store: Any, *, directory: Path | None = None) -> Path | None:
    """Copy the SQLite file with the backup API; None for an in-memory store."""
    path = getattr(store, "path", None)
    if not path:
        return None
    source = Path(path)
    if not source.is_file():
        return None
    target_dir = directory or (source.parent / "backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"{source.stem}-before-repair-{stamp}{source.suffix}"
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


def repair_learning(academy: Any, *, backup: bool = True, backup_directory: Path | None = None) -> dict[str, Any]:
    """Apply every correction the current policy calls for; safe to run again."""
    store = academy.store
    learning = academy.learning
    understanding = academy.study.understanding
    histology = academy.study.histology
    now = utc_now().isoformat()
    report: dict[str, Any] = {
        "backup": None,
        "policy": SCORING_POLICY,
        "unscored_questions": [],
        "mastery_corrected": 0,
        "events_excluded": 0,
        "findings_touched": 0,
        "histology_relinked": [],
        "analyses_recomputed": 0,
        "already_applied": 0,
        "at": now,
    }
    if backup:
        target = backup_database(store, directory=backup_directory)
        report["backup"] = str(target) if target else None
    else:
        report["backup_skipped"] = True
    applied = {str(item.get("key")) for item in store.list_records(REPAIR_KIND, limit=5000)}

    def ledger(key: str, body: dict[str, Any]) -> None:
        store.save_record(REPAIR_KIND, new_id("rep"), {"key": key, "at": now, "policy": SCORING_POLICY, **body}, subject_key=body.get("kind"))
        applied.add(key)

    # 1. Answers to questions the policy does not score: out of mastery and out of the findings' evidence.
    attempts = store.list_attempts(limit=5000)
    answered_ids: list[str] = []
    for attempt in attempts:
        for question_id, entry in attempt.answers.items():
            if entry.answer_key and question_id not in answered_ids:
                answered_ids.append(question_id)
    for event in understanding.events(limit=5000):
        question_id = str(event.get("question_id") or "")
        if event.get("source") in ("exam", "check", "diagnosis", "review") and question_id and question_id not in answered_ids and not question_id.startswith("histology-"):
            answered_ids.append(question_id)
    for question_id in answered_ids:
        question = store.get_question(question_id)
        if question is None:
            continue
        decision = academy.scoring_of(question)
        if decision["scored"]:
            continue
        key = f"unscored:{question_id}"
        if key in applied:
            report["already_applied"] += 1
            continue
        reason = f"Puansız soru ({decision['label']}): ölçme kanıtı sayılmaz."
        corrected = learning.exclude_question(question, reason=reason)
        withdrawn = understanding.exclude_question(question_id, reason)
        report["mastery_corrected"] += len(corrected)
        report["events_excluded"] += int(withdrawn.get("events", 0))
        report["findings_touched"] += int(withdrawn.get("findings", 0))
        report["unscored_questions"].append({"question_id": question_id, "status": decision["status"], "label": decision["label"], "mastery": [item.concept_id for item in corrected], "events": withdrawn.get("events", 0)})
        ledger(key, {"kind": "unscored_question", "question_id": question_id, "status": decision["status"], "mastery": [item.concept_id for item in corrected], "events": withdrawn.get("events", 0), "findings": withdrawn.get("findings", 0)})

    # 2. Histology answers filed under a concept the exact name does not give.
    for event in understanding.events(limit=5000):
        if event.get("source") != "histology":
            continue
        event_id = str(event.get("event_id"))
        specimen_id = str(event.get("question_id") or "").removeprefix("histology-")
        specimen = histology.specimen(specimen_id)
        if specimen is None:
            continue
        wanted = histology._concept_for(str(specimen.get("label") or ""), str(specimen.get("latin") or ""))
        recorded = str((event.get("concept_ids") or [""])[0])
        key = f"histology:{event_id}"
        if key in applied:
            report["already_applied"] += 1
            continue
        if not recorded or wanted == recorded:
            continue
        correct = bool(event.get("correct"))
        learning.retract(recorded, correct, reason=f"Histoloji cevabı '{specimen.get('label', '')}' bu kavrama ait değildi; kayıt taşındı.")
        learning.credit(wanted, correct, subject="histology")
        event["concept_ids"] = [wanted]
        event["relinked_from"] = recorded
        store.save_record(EVENT_KIND, event_id, event, subject_key=wanted)
        understanding.relink_evidence(event_id, recorded, wanted)
        specimen["concept_id"] = wanted
        histology._save(specimen, "Öğrenme kaydı tam ad eşleşmesine göre düzeltildi.")
        report["histology_relinked"].append({"event_id": event_id, "specimen_id": specimen_id, "from": recorded, "to": wanted, "correct": correct})
        ledger(key, {"kind": "histology_relink", "event_id": event_id, "specimen_id": specimen_id, "from": recorded, "to": wanted})

    # 3. Stored results of finished papers, recomputed under the policy; the answers are untouched.
    for attempt in attempts:
        if attempt.finished_at is None:
            continue
        exam = store.get_exam(attempt.exam_id)
        if exam is None:
            continue
        questions = store.get_questions(exam.question_ids)
        scoring = {question.question_id: academy.scoring_of(question) for question in questions}
        analysis = analyse_attempt(exam, questions, attempt, curriculum=academy.curriculum, mastery_levels=learning.levels(), scoring=scoring)
        previous = attempt.analysis or {}
        for keep in ("events", "adaptive"):
            if keep in previous:
                analysis[keep] = previous[keep]
        analysis["scoring"] = scoring
        analysis["policy"] = SCORING_POLICY
        analysis["repaired_at"] = now
        if previous.get("score") != analysis["score"] or previous.get("policy") != SCORING_POLICY or previous.get("total") != analysis["total"]:
            attempt.score = analysis["score"]
            attempt.analysis = analysis
            store.save_attempt(attempt)
            report["analyses_recomputed"] += 1
    return report


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"Yedek: {report.get('backup') or ('alınmadı' if report.get('backup_skipped') else 'yok (bellek içi depo)')}",
        f"Puansız soru düzeltmesi: {len(report['unscored_questions'])} soru, {report['mastery_corrected']} ustalık satırı, {report['events_excluded']} olay, {report['findings_touched']} bulgu",
        f"Histoloji yeniden bağlama: {len(report['histology_relinked'])}",
        f"Yeniden hesaplanan sınav sonucu: {report['analyses_recomputed']}",
        f"Daha önce uygulanmış (atlandı): {report['already_applied']}",
    ]
    for item in report["unscored_questions"]:
        lines.append(f"  - {item['question_id']}: {item['label']} → {', '.join(item['mastery']) or 'ustalık satırı yok'}")
    for item in report["histology_relinked"]:
        lines.append(f"  - {item['event_id']}: {item['from']} → {item['to']}")
    return "\n".join(lines)


__all__ = ["REPAIR_KIND", "backup_database", "format_report", "repair_learning"]
