from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from threading import (
    Event,
    Lock,
    Thread,
    current_thread,
)
from typing import Callable
from urllib.parse import (
    urlsplit,
    urlunsplit,
)
from urllib.request import (
    Request as URLRequest,
    urlopen,
)

from app.core.time import utc_now


OllamaWarmTransport = Callable[
    [
        str,
        bytes,
        float,
    ],
    None,
]


@dataclass(
    frozen=True,
    slots=True,
)
class OllamaWarmSnapshot:
    """Observable state of the background Ollama warmer."""

    running: bool
    attempts: int
    successes: int
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_duration_seconds: float | None
    last_error: str | None


class OllamaWarmKeeper:
    """
    Load an Ollama model without blocking JARVIS startup and
    periodically refresh its keep-alive lease.

    Network failures never propagate into application startup.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        keep_alive_seconds: float = 1800.0,
        refresh_seconds: float = 120.0,
        retry_seconds: float = 15.0,
        timeout_seconds: float = 30.0,
        transport: OllamaWarmTransport | None = None,
    ) -> None:
        normalized_model = model.strip()

        if not normalized_model:
            raise ValueError(
                "Ollama warm model cannot be empty."
            )

        self._keep_alive_seconds = (
            self._positive_number(
                keep_alive_seconds,
                "keep_alive_seconds",
            )
        )

        self._refresh_seconds = (
            self._positive_number(
                refresh_seconds,
                "refresh_seconds",
            )
        )

        self._retry_seconds = (
            self._positive_number(
                retry_seconds,
                "retry_seconds",
            )
        )

        self._timeout_seconds = (
            self._positive_number(
                timeout_seconds,
                "timeout_seconds",
            )
        )

        if (
            self._refresh_seconds
            >= self._keep_alive_seconds
        ):
            raise ValueError(
                "refresh_seconds must be less than "
                "keep_alive_seconds."
            )

        self._model = normalized_model

        self._generate_url = (
            self._native_generate_url(
                base_url
            )
        )

        self._transport = (
            transport
            or self._default_transport
        )

        self._stop_event = Event()
        self._state_lock = Lock()
        self._thread: Thread | None = None

        self._attempts = 0
        self._successes = 0
        self._last_attempt_at: (
            datetime | None
        ) = None
        self._last_success_at: (
            datetime | None
        ) = None
        self._last_duration_seconds: (
            float | None
        ) = None
        self._last_error: str | None = None

    @staticmethod
    def _positive_number(
        value: float,
        name: str,
    ) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(
                value,
                (int, float),
            )
        ):
            raise TypeError(
                f"{name} must be numeric."
            )

        parsed = float(value)

        if (
            not math.isfinite(parsed)
            or parsed <= 0
        ):
            raise ValueError(
                f"{name} must be positive."
            )

        return parsed

    @staticmethod
    def _native_generate_url(
        base_url: str,
    ) -> str:
        normalized = base_url.strip()

        parsed = urlsplit(
            normalized
        )

        if (
            parsed.scheme
            not in {"http", "https"}
            or not parsed.netloc
        ):
            raise ValueError(
                "Ollama base URL must be "
                "an HTTP(S) URL."
            )

        path = parsed.path.rstrip("/")

        if path.endswith("/v1"):
            path = path[:-3]

        path = path.rstrip("/")

        native_path = (
            f"{path}/api/generate"
            if path
            else "/api/generate"
        )

        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                native_path,
                "",
                "",
            )
        )

    def _payload(self) -> bytes:
        return json.dumps(
            {
                "model": self._model,

                # Ollama can load the model without needing
                # user-visible response generation.
                "prompt": "",

                "stream": False,
                "keep_alive": (
                    f"{self._keep_alive_seconds:g}s"
                ),
            },
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _default_transport(
        url: str,
        payload: bytes,
        timeout_seconds: float,
    ) -> None:
        request = URLRequest(
            url,
            data=payload,
            headers={
                "Content-Type": (
                    "application/json"
                ),
            },
            method="POST",
        )

        with urlopen(
            request,
            timeout=timeout_seconds,
        ) as response:
            response.read()

    def warm_once(self) -> bool:
        """
        Refresh one Ollama model keep-alive lease.

        Failure is recorded and returned as False rather than
        escaping into the JARVIS lifecycle.
        """
        attempted_at = utc_now()
        started = time.perf_counter()

        error: str | None = None
        success = False

        try:
            self._transport(
                self._generate_url,
                self._payload(),
                self._timeout_seconds,
            )
        except Exception as exc:
            error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )[:500]
        else:
            success = True

        duration = (
            time.perf_counter()
            - started
        )

        with self._state_lock:
            self._attempts += 1
            self._last_attempt_at = (
                attempted_at
            )
            self._last_duration_seconds = (
                duration
            )

            if success:
                self._successes += 1
                self._last_success_at = (
                    utc_now()
                )
                self._last_error = None
            else:
                self._last_error = error

        return success

    def _run(self) -> None:
        while not self._stop_event.is_set():
            success = self.warm_once()

            delay = (
                self._refresh_seconds
                if success
                else self._retry_seconds
            )

            if self._stop_event.wait(
                delay
            ):
                break

    def start(self) -> bool:
        """
        Start warming in a daemon thread.

        This method itself never performs model loading and is
        therefore safe on the synchronous bootstrap path.
        """
        with self._state_lock:
            if (
                self._thread is not None
                and self._thread.is_alive()
            ):
                return False

            self._stop_event.clear()

            thread = Thread(
                target=self._run,
                name="jarvis-ollama-warm",
                daemon=True,
            )

            self._thread = thread

            thread.start()

        return True

    def close(self) -> None:
        """Stop future keep-alive refreshes."""
        self._stop_event.set()

        with self._state_lock:
            thread = self._thread

        if (
            thread is not None
            and thread is not current_thread()
        ):
            thread.join(
                timeout=1.0
            )

        if (
            thread is not None
            and not thread.is_alive()
        ):
            with self._state_lock:
                if self._thread is thread:
                    self._thread = None

    def snapshot(
        self,
    ) -> OllamaWarmSnapshot:
        with self._state_lock:
            thread = self._thread

            return OllamaWarmSnapshot(
                running=(
                    thread is not None
                    and thread.is_alive()
                ),
                attempts=self._attempts,
                successes=self._successes,
                last_attempt_at=(
                    self._last_attempt_at
                ),
                last_success_at=(
                    self._last_success_at
                ),
                last_duration_seconds=(
                    self._last_duration_seconds
                ),
                last_error=self._last_error,
            )
