from __future__ import annotations

import sys

import pytest

from scripts.verify import build_steps, verify_python


def test_verification_steps_are_ordered_and_shell_free() -> None:
    steps = build_steps("python-test")

    assert [step.name for step in steps] == [
        "dependency integrity",
        "bytecode compilation",
        "automated tests",
    ]
    assert all(step.command[0] == "python-test" for step in steps)
    assert steps[-1].command[:4] == (
        "python-test",
        "-m",
        "pytest",
        "-q",
    )


def test_verification_requires_exact_supported_python(monkeypatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 11, 9))
    with pytest.raises(RuntimeError, match="3.12"):
        verify_python()
