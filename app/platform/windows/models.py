from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class WindowsLaunchMethod(str, Enum):
    EXECUTABLE = "executable"


@dataclass(frozen=True, slots=True)
class WindowsApplication:
    """Trusted launch definition for a Windows application."""

    application_id: str
    display_name: str
    executable: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    arguments: tuple[str, ...] = ()
    process_names: frozenset[str] = field(default_factory=frozenset)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    launch_method: WindowsLaunchMethod = WindowsLaunchMethod.EXECUTABLE
    source: str = "builtin"

    def __post_init__(self) -> None:
        for name in ("application_id", "display_name", "executable", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Windows application {name} cannot be empty.")
            object.__setattr__(self, name, value.strip())

        application_id = self.application_id.lower()
        if not all(character.isalnum() or character in "-_." for character in application_id):
            raise ValueError("Windows application_id contains invalid characters.")
        object.__setattr__(self, "application_id", application_id)

        for field_name in ("aliases", "process_names", "capabilities"):
            values = getattr(self, field_name)
            if isinstance(values, str) or not all(
                isinstance(value, str) for value in values
            ):
                raise TypeError(f"Windows application {field_name} must contain strings.")
            object.__setattr__(
                self,
                field_name,
                frozenset(
                    value.strip().lower()
                    for value in values
                    if value.strip()
                ),
            )

        if not isinstance(self.arguments, tuple) or not all(
            isinstance(value, str) for value in self.arguments
        ):
            raise TypeError("Windows application arguments must be a tuple of strings.")
        if not isinstance(self.launch_method, WindowsLaunchMethod):
            raise TypeError("launch_method must be a WindowsLaunchMethod.")

    @property
    def all_names(self) -> frozenset[str]:
        return frozenset({self.application_id, *self.aliases})

    @property
    def expected_process_names(self) -> frozenset[str]:
        if self.process_names:
            return self.process_names
        return frozenset({Path(self.executable).name.lower()})


@dataclass(frozen=True, slots=True)
class WindowsProcess:
    """Observed Windows process information."""

    pid: int
    name: str
    executable_path: str | None = None
    session_name: str | None = None
    session_number: int | None = None
    memory_kb: int | None = None

    def __post_init__(self) -> None:
        if self.pid < 1:
            raise ValueError("Process pid must be positive.")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Process name cannot be empty.")
        object.__setattr__(self, "name", self.name.strip())

    def to_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "name": self.name,
            "executable_path": self.executable_path,
            "session_name": self.session_name,
            "session_number": self.session_number,
            "memory_kb": self.memory_kb,
        }


@dataclass(frozen=True, slots=True)
class WindowsLaunchOutcome:
    application_id: str
    pid: int | None
    verified: bool
    message: str
    process: WindowsProcess | None = None
    error: str | None = None

