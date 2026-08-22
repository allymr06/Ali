from __future__ import annotations

import json
import time
from threading import Event

from app.providers.ollama_warm import (
    OllamaWarmKeeper,
)


def test_warm_once_loads_model_without_generation(
) -> None:
    calls = []

    def transport(
        url,
        payload,
        timeout,
    ):
        calls.append(
            (
                url,
                payload,
                timeout,
            )
        )

    keeper = OllamaWarmKeeper(
        base_url=(
            "http://localhost:11434/v1/"
        ),
        model="llama3.2:latest",
        keep_alive_seconds=1800,
        refresh_seconds=120,
        retry_seconds=15,
        timeout_seconds=20,
        transport=transport,
    )

    assert keeper.warm_once() is True

    assert len(calls) == 1

    url, payload, timeout = calls[0]

    assert (
        url
        == "http://localhost:11434/api/generate"
    )

    assert timeout == 20.0

    body = json.loads(
        payload.decode("utf-8")
    )

    assert body == {
        "model": "llama3.2:latest",
        "prompt": "",
        "stream": False,
        "keep_alive": "1800s",
    }

    snapshot = keeper.snapshot()

    assert snapshot.attempts == 1
    assert snapshot.successes == 1
    assert snapshot.last_error is None


def test_warm_failure_is_fail_open(
) -> None:
    def transport(
        url,
        payload,
        timeout,
    ):
        raise OSError(
            "Ollama offline"
        )

    keeper = OllamaWarmKeeper(
        base_url=(
            "http://localhost:11434/v1/"
        ),
        model="llama3.2:latest",
        keep_alive_seconds=30,
        refresh_seconds=10,
        retry_seconds=1,
        timeout_seconds=1,
        transport=transport,
    )

    assert keeper.warm_once() is False

    snapshot = keeper.snapshot()

    assert snapshot.attempts == 1
    assert snapshot.successes == 0
    assert snapshot.last_error is not None

    assert (
        "OSError"
        in snapshot.last_error
    )


def test_start_does_not_block_on_slow_model_load(
) -> None:
    entered = Event()
    release = Event()

    def transport(
        url,
        payload,
        timeout,
    ):
        entered.set()

        release.wait(
            timeout=1.0
        )

    keeper = OllamaWarmKeeper(
        base_url=(
            "http://localhost:11434/v1/"
        ),
        model="llama3.2:latest",
        keep_alive_seconds=30,
        refresh_seconds=10,
        retry_seconds=1,
        timeout_seconds=1,
        transport=transport,
    )

    started = time.perf_counter()

    assert keeper.start() is True

    startup_elapsed = (
        time.perf_counter()
        - started
    )

    # start() must return while the transport is
    # still blocked in the background thread.
    assert startup_elapsed < 0.25

    assert entered.wait(
        timeout=0.5
    )

    assert keeper.start() is False

    release.set()

    deadline = (
        time.monotonic()
        + 1.0
    )

    while (
        keeper.snapshot().successes
        < 1
        and time.monotonic()
        < deadline
    ):
        time.sleep(0.01)

    keeper.close()

    assert (
        keeper.snapshot().running
        is False
    )


def test_background_worker_refreshes_keep_alive(
) -> None:
    second_call = Event()
    calls = []

    def transport(
        url,
        payload,
        timeout,
    ):
        calls.append(
            time.monotonic()
        )

        if len(calls) >= 2:
            second_call.set()

    keeper = OllamaWarmKeeper(
        base_url=(
            "http://localhost:11434/v1/"
        ),
        model="llama3.2:latest",
        keep_alive_seconds=1.0,
        refresh_seconds=0.03,
        retry_seconds=0.01,
        timeout_seconds=0.1,
        transport=transport,
    )

    try:
        assert keeper.start() is True

        assert second_call.wait(
            timeout=0.75
        )

        assert len(calls) >= 2

        snapshot = keeper.snapshot()

        assert snapshot.successes >= 2

    finally:
        keeper.close()

    assert (
        keeper.snapshot().running
        is False
    )
