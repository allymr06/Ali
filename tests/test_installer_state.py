from __future__ import annotations

import os

from installer.entrypoint import configure_state


def test_installer_configures_all_durable_state_paths(monkeypatch, tmp_path) -> None:
    for name in (
        "JARVIS_CONVERSATION_DATABASE_PATH",
        "JARVIS_MEMORY_DATABASE_PATH",
        "JARVIS_TASK_DATABASE_PATH",
        "JARVIS_TASK_RUNTIME_DIRECTORY",
        "JARVIS_RESEARCH_CACHE_DATABASE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    configure_state(tmp_path)

    assert os.environ["JARVIS_CONVERSATION_DATABASE_PATH"] == str(
        tmp_path / "jarvis_conversations.sqlite3"
    )
    assert os.environ["JARVIS_MEMORY_DATABASE_PATH"] == str(
        tmp_path / "jarvis_memory.sqlite3"
    )
    assert os.environ["JARVIS_TASK_DATABASE_PATH"] == str(
        tmp_path / "jarvis_tasks.sqlite3"
    )
    assert os.environ["JARVIS_TASK_RUNTIME_DIRECTORY"] == str(tmp_path / "tasks")
    assert os.environ["JARVIS_RESEARCH_CACHE_DATABASE_PATH"] == str(
        tmp_path / "jarvis_research.sqlite3"
    )
