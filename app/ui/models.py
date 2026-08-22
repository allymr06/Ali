from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class UIScreen(StrEnum):
    HOME = "home"
    CHAT = "chat"
    TASKS = "tasks"
    MEMORY = "memory"
    VOICE = "voice"
    VISION = "vision"
    RESEARCH = "research"
    TOOLS = "tools"
    INTEGRATIONS = "integrations"
    DIAGNOSTICS = "diagnostics"
    SETTINGS = "settings"


class UITheme(StrEnum):
    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    text: str

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant", "system"}:
            raise ValueError("Chat role is invalid.")
        if not self.text.strip():
            raise ValueError("Chat text cannot be empty.")


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    provider: str
    model: str
    memory_count: int
    task_count: int
    tool_count: int
    enabled_tools: int
    voice_available: bool
    vision_available: bool
    research_available: bool
    windows_available: bool
    diagnostic_event_count: int = 0
    diagnostic_integrity_valid: bool = True
    tasks: tuple[dict[str, object], ...] = ()
    memories: tuple[dict[str, object], ...] = ()
    tools: tuple[dict[str, object], ...] = ()


@dataclass(slots=True)
class UIState:
    screen: UIScreen = UIScreen.HOME
    theme: UITheme = UITheme.DARK
    busy: bool = False
    status: str = "LOCAL CORE READY"
    nav_collapsed: bool = False
    context_collapsed: bool = False
    reduced_motion: bool = False
    messages: list[ChatMessage] = field(default_factory=list)
