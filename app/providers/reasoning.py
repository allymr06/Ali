from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from app.providers.models import TaskType


class ReasoningLevel(str, Enum):
    """Provider-facing reasoning effort without exposing reasoning content."""

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReasoningPolicy:
    """Stateless policy for selecting a bounded reasoning effort.

    The policy returns only an effort level. It never requests, stores, or
    exposes a model's private reasoning trace.
    """

    AUTO = "auto"
    DEEP_REASONING_METADATA_KEY = "deep_reasoning"

    _AUTO_LEVELS = {
        "social": ReasoningLevel.MINIMAL,
        TaskType.SIMPLE.value: ReasoningLevel.MINIMAL,
        TaskType.STANDARD.value: ReasoningLevel.LOW,
        TaskType.VISION.value: ReasoningLevel.LOW,
        TaskType.COMPLEX.value: ReasoningLevel.MEDIUM,
        TaskType.AGENTIC.value: ReasoningLevel.MEDIUM,
        TaskType.LONG_RUNNING.value: ReasoningLevel.MEDIUM,
    }
    _GEMINI_MINIMAL_UNSUPPORTED_PREFIXES = ("gemini-3.7",)
    _DEEP_REASONING_MARKERS = (
        "derin düşün",
        "iyice düşün",
        "adım adım düşün",
        "think deeply",
        "deep thinking",
    )

    @classmethod
    def explicitly_requests_deep_reasoning(cls, text: str) -> bool:
        normalized = " ".join(text.casefold().split())
        return any(marker in normalized for marker in cls._DEEP_REASONING_MARKERS)

    @classmethod
    def select(
        cls,
        *,
        task_type: TaskType | str,
        model: str,
        config_override: ReasoningLevel | str = AUTO,
        request_metadata: Mapping[str, Any] | None = None,
    ) -> ReasoningLevel:
        """Resolve the effective effort for one request.

        ``request_metadata["deep_reasoning"] is True`` represents an
        explicit user's "deep/derin dusun" request and is the only way auto
        selection can rise to ``high``.
        """

        override = cls._coerce_override(config_override)
        metadata = request_metadata or {}

        if metadata.get(cls.DEEP_REASONING_METADATA_KEY) is True:
            selected = ReasoningLevel.HIGH
        elif override != cls.AUTO:
            selected = ReasoningLevel(override)
        else:
            selected = cls._auto_level(task_type)

        return cls.normalize_for_model(selected, model=model)

    @classmethod
    def normalize_for_model(
        cls,
        level: ReasoningLevel | str,
        *,
        model: str,
    ) -> ReasoningLevel:
        """Normalize an effort to the selected model's supported levels."""

        selected = cls._coerce_level(level)
        normalized_model = model.strip().casefold()
        if not normalized_model:
            raise ValueError("model cannot be empty.")
        if (
            selected is ReasoningLevel.MINIMAL
            and normalized_model.startswith(
                cls._GEMINI_MINIMAL_UNSUPPORTED_PREFIXES
            )
        ):
            return ReasoningLevel.LOW
        return selected

    @classmethod
    def _auto_level(cls, task_type: TaskType | str) -> ReasoningLevel:
        task_value = (
            task_type.value
            if isinstance(task_type, TaskType)
            else str(task_type).strip().casefold()
        )
        try:
            return cls._AUTO_LEVELS[task_value]
        except KeyError as exc:
            raise ValueError(f"Unsupported task type: {task_type!r}.") from exc

    @staticmethod
    def _coerce_override(value: ReasoningLevel | str) -> str:
        normalized = value.value if isinstance(value, ReasoningLevel) else str(value)
        normalized = normalized.strip().casefold()
        allowed = {ReasoningPolicy.AUTO, *(item.value for item in ReasoningLevel)}
        if normalized not in allowed:
            raise ValueError(
                "config_override must be auto, minimal, low, medium, or high."
            )
        return normalized

    @staticmethod
    def _coerce_level(value: ReasoningLevel | str) -> ReasoningLevel:
        try:
            return value if isinstance(value, ReasoningLevel) else ReasoningLevel(value)
        except ValueError as exc:
            raise ValueError(
                "reasoning level must be minimal, low, medium, or high."
            ) from exc
