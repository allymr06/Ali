from __future__ import annotations

import pytest

from app.providers.models import TaskType
from app.providers.reasoning import ReasoningLevel, ReasoningPolicy


@pytest.mark.parametrize(
    ("task_type", "expected"),
    [
        ("social", ReasoningLevel.MINIMAL),
        (TaskType.SIMPLE, ReasoningLevel.MINIMAL),
        (TaskType.STANDARD, ReasoningLevel.LOW),
        (TaskType.VISION, ReasoningLevel.LOW),
        (TaskType.COMPLEX, ReasoningLevel.MEDIUM),
        (TaskType.AGENTIC, ReasoningLevel.MEDIUM),
        (TaskType.LONG_RUNNING, ReasoningLevel.MEDIUM),
    ],
)
def test_auto_maps_task_type_without_selecting_high(task_type, expected):
    assert (
        ReasoningPolicy.select(
            task_type=task_type,
            model="gemini-3.5-flash-lite",
        )
        is expected
    )
    assert expected is not ReasoningLevel.HIGH


@pytest.mark.parametrize(
    "override",
    [
        ReasoningLevel.MINIMAL,
        "low",
        ReasoningLevel.MEDIUM,
        "high",
    ],
)
def test_explicit_config_override_is_honored(override):
    assert ReasoningPolicy.select(
        task_type=TaskType.SIMPLE,
        model="gemini-3.5-flash-lite",
        config_override=override,
    ).value == (override.value if isinstance(override, ReasoningLevel) else override)


def test_explicit_deep_request_is_the_only_auto_path_to_high():
    assert ReasoningPolicy.select(
        task_type=TaskType.SIMPLE,
        model="gemini-3.5-flash-lite",
        request_metadata={"deep_reasoning": True},
    ) is ReasoningLevel.HIGH

    assert ReasoningPolicy.select(
        task_type=TaskType.COMPLEX,
        model="gemini-3.5-flash-lite",
        request_metadata={"deep_reasoning": False},
    ) is ReasoningLevel.MEDIUM


def test_deep_request_overrides_a_lower_global_setting():
    assert ReasoningPolicy.select(
        task_type=TaskType.STANDARD,
        model="gemini-3.5-flash-lite",
        config_override="minimal",
        request_metadata={"deep_reasoning": True},
    ) is ReasoningLevel.HIGH


def test_gemini_37_normalizes_unsupported_minimal_to_low():
    assert ReasoningPolicy.select(
        task_type=TaskType.SIMPLE,
        model="gemini-3.7-flash",
        config_override="minimal",
    ) is ReasoningLevel.LOW


def test_minimal_remains_available_on_supported_gemini_model():
    assert ReasoningPolicy.select(
        task_type=TaskType.SIMPLE,
        model="gemini-3.5-flash-lite",
        config_override="minimal",
    ) is ReasoningLevel.MINIMAL


@pytest.mark.parametrize("override", ["", "auto-high", "maximum"])
def test_invalid_config_override_is_rejected(override):
    with pytest.raises(ValueError, match="config_override"):
        ReasoningPolicy.select(
            task_type=TaskType.STANDARD,
            model="gemini-3.5-flash-lite",
            config_override=override,
        )


def test_unknown_task_type_and_empty_model_are_rejected():
    with pytest.raises(ValueError, match="Unsupported task type"):
        ReasoningPolicy.select(
            task_type="unknown",
            model="gemini-3.5-flash-lite",
        )

    with pytest.raises(ValueError, match="model cannot be empty"):
        ReasoningPolicy.select(
            task_type=TaskType.STANDARD,
            model=" ",
        )
