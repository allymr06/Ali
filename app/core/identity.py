from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Identity:
    """
    Runtime identity of the assistant.

    The system name is permanent and remains JARVIS.
    The current user-facing name is intentionally unset until
    the project reaches its final identity configuration.
    """

    system_name: str = "JARVIS"
    current_name: str | None = None

    def __post_init__(self) -> None:
        self.system_name = self.system_name.strip()

        if not self.system_name:
            raise ValueError("System name cannot be empty.")

        if self.current_name is not None:
            self.current_name = self.current_name.strip()

            if not self.current_name:
                self.current_name = None

    @property
    def display_name(self) -> str:
        """
        Return the current user-facing name.

        Until a final name is assigned, the system name is used.
        """
        return self.current_name or self.system_name

    def rename(self, new_name: str) -> None:
        """
        Set the current user-facing assistant name.
        """
        normalized_name = new_name.strip()

        if not normalized_name:
            raise ValueError("Current name cannot be empty.")

        self.current_name = normalized_name

    def clear_current_name(self) -> None:
        """
        Remove the current user-facing name and fall back to JARVIS.
        """
        self.current_name = None

    def describe(self) -> str:
        """
        Return a human-readable description of the assistant identity.
        """
        if self.current_name is None:
            return f"Ben {self.system_name}."

        return (
            f"Ben {self.current_name}. "
            f"Sistem kimliğim {self.system_name}."
        )