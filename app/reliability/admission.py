from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import RLock


class AdmissionRejectedError(RuntimeError):
    """Raised when bounded request capacity is exhausted."""


@dataclass(frozen=True, slots=True)
class AdmissionSnapshot:
    active: int
    waiting: int
    max_concurrent: int
    max_queue: int
    accepted: int
    rejected: int


class AdmissionLease:
    def __init__(self, controller: AdmissionController) -> None:
        self._controller = controller
        self._released = False

    async def __aenter__(self) -> AdmissionLease:
        return self

    async def __aexit__(self, *_args) -> None:
        self.release()

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._controller._release()


class AdmissionController:
    """Bound concurrent Core work and queued callers without unbounded growth."""

    def __init__(
        self,
        max_concurrent: int = 8,
        max_queue: int = 32,
        wait_timeout_seconds: float = 2.0,
    ) -> None:
        if max_concurrent < 1 or max_queue < 0 or wait_timeout_seconds <= 0:
            raise ValueError("Admission limits are invalid.")
        self._max_concurrent = max_concurrent
        self._max_queue = max_queue
        self._timeout = wait_timeout_seconds
        self._semaphore = asyncio.BoundedSemaphore(max_concurrent)
        self._lock = RLock()
        self._active = 0
        self._waiting = 0
        self._accepted = 0
        self._rejected = 0

    async def acquire(self) -> AdmissionLease:
        with self._lock:
            if self._active + self._waiting >= (
                self._max_concurrent + self._max_queue
            ):
                self._rejected += 1
                raise AdmissionRejectedError("Core request queue is full.")
            self._waiting += 1
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self._timeout
            )
        except asyncio.CancelledError:
            with self._lock:
                self._waiting -= 1
            raise
        except TimeoutError as exc:
            with self._lock:
                self._waiting -= 1
                self._rejected += 1
            raise AdmissionRejectedError(
                "Core request admission timed out."
            ) from exc
        with self._lock:
            self._waiting -= 1
            self._active += 1
            self._accepted += 1
        return AdmissionLease(self)

    def _release(self) -> None:
        with self._lock:
            if self._active < 1:
                raise RuntimeError("Admission lease release is unbalanced.")
            self._active -= 1
        self._semaphore.release()

    def snapshot(self) -> AdmissionSnapshot:
        with self._lock:
            return AdmissionSnapshot(
                active=self._active,
                waiting=self._waiting,
                max_concurrent=self._max_concurrent,
                max_queue=self._max_queue,
                accepted=self._accepted,
                rejected=self._rejected,
            )
