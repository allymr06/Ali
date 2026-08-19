from __future__ import annotations

from threading import RLock

from app.tools.base import RegisteredTool


class ToolRegistry:
    """Central registry for executable JARVIS tools."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._lock = RLock()

    def register(self, tool: RegisteredTool) -> None:
        name = tool.name.strip()

        if not name:
            raise ValueError("Tool name cannot be empty.")

        with self._lock:
            if name in self._tools:
                raise ValueError(
                    f"Tool '{name}' is already registered."
                )

            self._tools[name] = tool

    def unregister(self, name: str) -> RegisteredTool:
        normalized_name = name.strip()

        with self._lock:
            try:
                return self._tools.pop(normalized_name)
            except KeyError as exc:
                raise KeyError(
                    f"Tool '{normalized_name}' is not registered."
                ) from exc

    def get(self, name: str) -> RegisteredTool:
        normalized_name = name.strip()

        with self._lock:
            try:
                return self._tools[normalized_name]
            except KeyError as exc:
                raise KeyError(
                    f"Tool '{normalized_name}' is not registered."
                ) from exc

    def contains(self, name: str) -> bool:
        with self._lock:
            return name.strip() in self._tools

    def list_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._tools.keys())

    def list_tools(self) -> tuple[RegisteredTool, ...]:
        with self._lock:
            return tuple(self._tools.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)
