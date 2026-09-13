"""A ledger for the academy's long jobs: a paper or a note the model writes.

A job the student starts has an identity and a visible state from the
moment it is accepted: ``running`` until the coroutine returns, then
``done``, ``failed`` or ``timeout``; ``interrupted`` when the process that
ran it ended first (the ledger is in the store, so a job survives a page
reload and a restart is honest about what it lost). The request itself is
kept with the job, so a retry re-sends what the student asked for rather
than what a form happens to hold. Two requests with the same fingerprint
while one is still running are one job: a second click reports the first.

The ledger never invents an outcome: a job is ``done`` only when its result
was saved by the pipeline that produced it, and a failure carries the error
the pipeline raised, in the words it raised it with.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from app.core.time import utc_now
from app.medical.models import new_id

JOB_KIND = "academy_job"
JOB_LABELS_TR: dict[str, str] = {
    "create_exam": "Sınav hazırlama",
    "create_note": "Not hazırlama",
}
STATUS_LABELS_TR: dict[str, str] = {
    "running": "Hazırlanıyor",
    "done": "Tamamlandı",
    "failed": "Başarısız",
    "timeout": "Zaman aşımı",
    "interrupted": "Yarım kaldı",
}
TIMEOUTS_SECONDS: dict[str, float] = {"create_exam": 300.0, "create_note": 240.0}
RECENT_LIMIT = 30


def fingerprint(kind: str, request: dict[str, Any]) -> str:
    body = json.dumps({"kind": kind, "request": request}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


class JobLedger:
    def __init__(self, store: Any, *, emit: Callable[[dict[str, Any]], None] | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._emit = emit
        self._clock = clock or utc_now
        self._lock = threading.RLock()
        self._running: dict[str, str] = {}  # fingerprint → job_id
        self._mark_interrupted()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def _mark_interrupted(self) -> None:
        """Jobs left running by a process that is gone did not finish."""
        for job in self._store.list_records(JOB_KIND, limit=200):
            if job.get("status") == "running":
                job["status"] = "interrupted"
                job["status_label"] = STATUS_LABELS_TR["interrupted"]
                job["error"] = "Uygulama iş bitmeden kapandı; sonuç kaydedilmedi."
                job["finished_at"] = self._clock().isoformat()
                self._store.save_record(JOB_KIND, job["job_id"], job, subject_key=job.get("kind"))

    def _save(self, job: dict[str, Any]) -> dict[str, Any]:
        job["status_label"] = STATUS_LABELS_TR.get(job["status"], job["status"])
        job["kind_label"] = JOB_LABELS_TR.get(job["kind"], job["kind"])
        self._store.save_record(JOB_KIND, job["job_id"], job, subject_key=job["kind"])
        if self._emit is not None:
            try:
                self._emit({"kind": "job_state", "job": dict(job)})
            except Exception:
                pass
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._store.get_record(JOB_KIND, job_id)

    def recent(self, *, limit: int = RECENT_LIMIT) -> list[dict[str, Any]]:
        return self._store.list_records(JOB_KIND, limit=limit)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self, kind: str, request: dict[str, Any], *, title: str = "") -> tuple[dict[str, Any], bool]:
        """Register a job; ``(job, created)`` — created is False for a duplicate of a running one."""
        key = fingerprint(kind, request)
        with self._lock:
            existing_id = self._running.get(key)
            if existing_id:
                existing = self.get(existing_id)
                if existing is not None and existing.get("status") == "running":
                    return existing, False
                self._running.pop(key, None)
            job = {
                "job_id": new_id("job"),
                "kind": kind,
                "title": " ".join(str(title or "").split())[:160],
                "request": request,
                "fingerprint": key,
                "status": "running",
                "started_at": self._clock().isoformat(),
                "finished_at": None,
                "error": None,
                "result": None,
                "attempt": 1 + sum(1 for item in self.recent(limit=200) if item.get("fingerprint") == key),
            }
            self._running[key] = job["job_id"]
        return self._save(job), True

    def finish(self, job_id: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return self._close(job_id, "done", result=result)

    def fail(self, job_id: str, error: str, *, status: str = "failed") -> dict[str, Any] | None:
        return self._close(job_id, status, error=error)

    def _close(self, job_id: str, status: str, *, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                return None
            if job.get("status") != "running":
                return job
            job["status"] = status
            job["finished_at"] = self._clock().isoformat()
            job["result"] = result
            job["error"] = " ".join(str(error or "").split())[:400] or None
            self._running.pop(str(job.get("fingerprint")), None)
        return self._save(job)

    async def run(self, kind: str, request: dict[str, Any], operation: Callable[[], Awaitable[dict[str, Any]]], *, title: str = "", summarize: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
        """Run ``operation`` under the ledger: one entry, a bounded wait, the truthful end state.

        Returns the operation's result. A duplicate request raises
        :class:`DuplicateJob` carrying the running job, so the caller can
        tell the student the work is already under way instead of starting
        it twice.
        """
        job, created = self.start(kind, request, title=title)
        if not created:
            raise DuplicateJob(job)
        timeout = TIMEOUTS_SECONDS.get(kind, 300.0)
        try:
            outcome = await asyncio.wait_for(operation(), timeout=timeout)
        except asyncio.TimeoutError:
            self.fail(job["job_id"], f"{int(timeout)} saniye içinde tamamlanmadı; sağlayıcı yanıt vermedi.", status="timeout")
            raise JobTimeout(job["job_id"], timeout)
        except asyncio.CancelledError:
            self.fail(job["job_id"], "İş iptal edildi.", status="interrupted")
            raise
        except Exception as exc:
            self.fail(job["job_id"], str(exc) or type(exc).__name__)
            raise
        summary = summarize(outcome) if summarize is not None else None
        self.finish(job["job_id"], summary)
        return {**outcome, "job_id": job["job_id"]} if isinstance(outcome, dict) else outcome


class DuplicateJob(Exception):
    def __init__(self, job: dict[str, Any]) -> None:
        super().__init__("Bu iş zaten sürüyor.")
        self.job = job


class JobTimeout(Exception):
    def __init__(self, job_id: str, timeout: float) -> None:
        super().__init__(f"İş {int(timeout)} saniye içinde tamamlanmadı; sağlayıcı yanıt vermedi.")
        self.job_id = job_id
        self.timeout = timeout


__all__ = ["DuplicateJob", "JOB_KIND", "JOB_LABELS_TR", "JobLedger", "JobTimeout", "STATUS_LABELS_TR", "TIMEOUTS_SECONDS", "fingerprint"]
