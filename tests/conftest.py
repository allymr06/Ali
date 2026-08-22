from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_runtime_memory_database(monkeypatch, tmp_path) -> None:
    """Prevent application bootstrap tests from sharing durable user state."""
    monkeypatch.setenv(
        "JARVIS_OLLAMA_WARM_ENABLED",
        "false",
    )

    monkeypatch.setenv(
        "JARVIS_OLLAMA_HYBRID_ENABLED",
        "false",
    )

    monkeypatch.setenv(
        "JARVIS_MEMORY_DATABASE_PATH",
        str(tmp_path / "runtime-memory.sqlite3"),
    )
    monkeypatch.setenv(
        "JARVIS_TASK_DATABASE_PATH",
        str(tmp_path / "runtime-tasks.sqlite3"),
    )
    monkeypatch.setenv(
        "JARVIS_TASK_RUNTIME_DIRECTORY",
        str(tmp_path / "task-runtime"),
    )
