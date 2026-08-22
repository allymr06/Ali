from __future__ import annotations

import gc

import pytest


@pytest.fixture(autouse=True)
def finalize_tk_objects_on_main_thread():
    """Collect garbage on the main thread after every test.

    Tcl aborts the process with a native breakpoint exception when a
    Tk object's finalizer runs on a foreign thread, which happens when
    a later test's worker thread triggers the garbage collector while
    UI-test leftovers are still uncollected.
    """
    yield
    gc.collect()


@pytest.fixture(autouse=True)
def isolate_runtime_memory_database(monkeypatch, tmp_path) -> None:
    """Prevent application bootstrap tests from sharing durable user state."""
    monkeypatch.setenv(
        "JARVIS_STATE_DIRECTORY",
        str(tmp_path / "runtime-state"),
    )
    monkeypatch.setenv(
        "JARVIS_CONVERSATION_DATABASE_PATH",
        str(tmp_path / "runtime-conversations.sqlite3"),
    )
    monkeypatch.setenv(
        "JARVIS_MEMORY_DATABASE_PATH",
        str(tmp_path / "runtime-memory.sqlite3"),
    )
    monkeypatch.setenv(
        "JARVIS_RESEARCH_CACHE_DATABASE_PATH",
        str(tmp_path / "runtime-research.sqlite3"),
    )
    monkeypatch.setenv(
        "JARVIS_TASK_DATABASE_PATH",
        str(tmp_path / "runtime-tasks.sqlite3"),
    )
    monkeypatch.setenv(
        "JARVIS_TASK_RUNTIME_DIRECTORY",
        str(tmp_path / "task-runtime"),
    )
