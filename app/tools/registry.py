from __future__ import annotations

from threading import RLock

from app.tools.base import RegisteredTool


class ToolRegistry:
    """Central registry for executable JARVIS tools."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._lock = RLock()
        self._revision = 0

    @property
    def revision(self) -> int:
        """Monotonic revision for cache invalidation and discovery."""
        with self._lock:
            return self._revision

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
            self._revision += 1

    def unregister(self, name: str) -> RegisteredTool:
        normalized_name = name.strip()

        with self._lock:
            try:
                tool = self._tools.pop(normalized_name)
                self._revision += 1
                return tool
            except KeyError as exc:
                raise KeyError(
                    f"Tool '{normalized_name}' is not registered."
                ) from exc

    def get(
        self,
        name: str,
        *,
        include_disabled: bool = False,
    ) -> RegisteredTool:
        normalized_name = name.strip()

        with self._lock:
            try:
                tool = self._tools[normalized_name]
            except KeyError as exc:
                raise KeyError(
                    f"Tool '{normalized_name}' is not registered."
                ) from exc

            if not include_disabled and not tool.enabled:
                raise KeyError(
                    f"Tool '{normalized_name}' is disabled."
                )

            return tool

    def contains(self, name: str, *, include_disabled: bool = False) -> bool:
        with self._lock:
            tool = self._tools.get(name.strip())
            return bool(
                tool is not None
                and (include_disabled or tool.enabled)
            )

    def list_names(self, *, include_disabled: bool = False) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                name
                for name, tool in self._tools.items()
                if include_disabled or tool.enabled
            )

    def list_tools(
        self,
        *,
        names: set[str] | frozenset[str] | None = None,
        capabilities: set[str] | frozenset[str] | None = None,
        tags: set[str] | frozenset[str] | None = None,
        include_disabled: bool = False,
    ) -> tuple[RegisteredTool, ...]:
        allowed_names = (
            None
            if names is None
            else {value.strip() for value in names if value.strip()}
        )
        required_capabilities = {
            value.strip().lower() for value in (capabilities or set())
        }
        required_tags = {
            value.strip().lower() for value in (tags or set())
        }

        with self._lock:
            return tuple(
                tool
                for tool in self._tools.values()
                if (include_disabled or tool.enabled)
                and (allowed_names is None or tool.name in allowed_names)
                and required_capabilities.issubset(
                    tool.definition.capabilities
                )
                and required_tags.issubset(tool.definition.tags)
            )

    def set_enabled(self, name: str, enabled: bool) -> RegisteredTool:
        """Enable or disable a tool without losing its registration."""
        with self._lock:
            tool = self.get(name, include_disabled=True)

            if tool.enabled != enabled:
                tool.enabled = enabled
                self._revision += 1

            return tool

    def enable(self, name: str) -> RegisteredTool:
        return self.set_enabled(name, True)

    def disable(self, name: str) -> RegisteredTool:
        return self.set_enabled(name, False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)
