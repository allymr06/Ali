from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class VerificationStep:
    name: str
    command: tuple[str, ...]


def build_steps(python: str = sys.executable) -> tuple[VerificationStep, ...]:
    return (
        VerificationStep(
            "dependency integrity", (python, "-m", "pip", "check")
        ),
        VerificationStep(
            "bytecode compilation",
            (python, "-m", "compileall", "-q", "app", "tests"),
        ),
        VerificationStep(
            "automated tests",
            (python, "-m", "pytest", "-q", "-o", "cache_dir=.test-cache"),
        ),
    )


def verify_python() -> None:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("JARVIS verification requires Python 3.12.")


def run(root: Path | None = None) -> int:
    verify_python()
    project = (root or Path(__file__).resolve().parents[1]).resolve()
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = "17"
    for step in build_steps():
        print(f"[verify] {step.name}", flush=True)
        completed = subprocess.run(
            step.command,
            cwd=project,
            env=environment,
            check=False,
        )
        if completed.returncode:
            print(
                f"[verify] failed: {step.name} (exit {completed.returncode})",
                file=sys.stderr,
            )
            return completed.returncode
    print("[verify] all acceptance gates passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
