from datetime import datetime, timezone

import pytest

from app.core.models import (
    PermissionRequest,
    Request,
    RequestSource,
    Response,
    RiskLevel,
    Task,
    TaskStatus,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)


def test_request_normalizes_text_and_assigns_defaults():
    request = Request("  Merhaba JARVIS  ")

    assert request.text == "Merhaba JARVIS"
    assert request.source is RequestSource.TEXT
    assert request.request_id is not None
    assert request.created_at.tzinfo == timezone.utc
    assert isinstance(request.metadata, dict)


def test_request_rejects_empty_text():
    with pytest.raises(ValueError):
        Request("   ")


def test_response_can_reference_request():
    request = Request("Test isteği")
    response = Response(
        text="Test cevabı",
        request_id=request.request_id,
    )

    assert response.text == "Test cevabı"
    assert response.request_id == request.request_id
    assert response.response_id is not None
    assert response.created_at.tzinfo == timezone.utc


def test_task_starts_queued_with_zero_progress():
    task = Task(goal="Notepad'i aç")

    assert task.status is TaskStatus.QUEUED
    assert task.progress == 0.0
    assert task.current_step is None
    assert task.result is None
    assert task.error is None
    assert task.task_id is not None


def test_task_progress_updates_correctly():
    task = Task(goal="Test görevi")

    task.update_progress(
        0.5,
        current_step="İlk adım",
    )

    assert task.progress == 0.5
    assert task.current_step == "İlk adım"
    assert task.updated_at.tzinfo == timezone.utc


@pytest.mark.parametrize("invalid_progress", [-0.1, 1.1, 2.0])
def test_task_rejects_invalid_progress(invalid_progress):
    task = Task(goal="Test görevi")

    with pytest.raises(ValueError):
        task.update_progress(invalid_progress)


def test_successful_tool_result_is_reported_as_succeeded():
    result = ToolResult(
        status=ToolExecutionStatus.SUCCESS,
        tool_name="test_tool",
        message="İşlem başarılı.",
        verified=True,
    )

    assert result.succeeded is True
    assert result.verified is True
    assert result.execution_id is not None


def test_failed_tool_result_is_not_successful():
    result = ToolResult(
        status=ToolExecutionStatus.FAILED,
        tool_name="test_tool",
        error="Test hatası",
    )

    assert result.succeeded is False
    assert result.error == "Test hatası"


def test_permission_request_contains_operation_and_risk():
    permission = PermissionRequest(
        operation="delete_file",
        risk_level=RiskLevel.HIGH,
        reason="Dosya silme işlemi kullanıcı onayı gerektiriyor.",
        parameters={"path": "C:\\test.txt"},
    )

    assert permission.operation == "delete_file"
    assert permission.risk_level is RiskLevel.HIGH
    assert permission.parameters["path"] == "C:\\test.txt"
    assert permission.operation_id is not None


def test_tool_definition_has_safe_defaults():
    tool = ToolDefinition(
        name="read_file",
        description="Bir dosyanın içeriğini okur.",
    )

    assert tool.name == "read_file"
    assert tool.risk_level is RiskLevel.READ_ONLY
    assert tool.requires_confirmation is False
    assert tool.timeout_seconds == 30.0
    assert isinstance(tool.metadata, dict)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": " ", "description": "Valid"},
        {"name": "valid", "description": " "},
        {"name": "valid", "description": "Valid", "timeout_seconds": 0},
        {"name": "valid", "description": "Valid", "timeout_seconds": -1},
    ],
)
def test_tool_definition_rejects_invalid_contract(kwargs):
    with pytest.raises(ValueError):
        ToolDefinition(**kwargs)


def test_all_timestamps_are_timezone_aware():
    request = Request("Saat testi")
    response = Response("Cevap testi")
    task = Task("Görev testi")

    timestamps = [
        request.created_at,
        response.created_at,
        task.created_at,
        task.updated_at,
    ]

    for timestamp in timestamps:
        assert isinstance(timestamp, datetime)
        assert timestamp.tzinfo is not None
        assert timestamp.utcoffset() is not None
