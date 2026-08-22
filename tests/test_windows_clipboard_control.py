from __future__ import annotations

from dataclasses import dataclass

from app.core.models import RiskLevel, ToolExecutionStatus
from app.platform.windows.clipboard import WindowsClipboardService
from app.tools.executor import ToolExecutor


@dataclass
class FakeClipboard:
    text: str = ""
    fail_read: bool = False
    fail_write: bool = False
    fail_clear: bool = False
    keep_old_value: bool = False

    def read_text(self) -> str:
        if self.fail_read:
            raise RuntimeError("native clipboard busy")
        return self.text

    def write_text(self, text: str) -> None:
        if self.fail_write:
            raise RuntimeError(f"could not write {text}")
        if not self.keep_old_value:
            self.text = text

    def clear(self) -> None:
        if self.fail_clear:
            raise RuntimeError(f"could not clear {self.text}")
        if not self.keep_old_value:
            self.text = ""


def test_clipboard_read_is_bounded_and_verified() -> None:
    service = WindowsClipboardService(FakeClipboard("merhaba"), 20)

    result = service.read()

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == {"text": "merhaba", "character_count": 7}
    assert result.verified is True


def test_clipboard_oversized_read_fails_without_returning_value() -> None:
    secret = "very-secret-token"
    result = WindowsClipboardService(FakeClipboard(secret), 5).read()

    assert result.status is ToolExecutionStatus.FAILED
    assert result.data is None
    assert secret not in repr(result)


def test_clipboard_write_is_exactly_verified_without_echoing_sensitive_input() -> None:
    backend = FakeClipboard()
    secret = "api-key-secret"

    result = WindowsClipboardService(backend).write(secret)

    assert backend.text == secret
    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == {"character_count": len(secret)}
    assert result.verified is True
    assert secret not in repr(result)


def test_clipboard_write_failure_is_fail_closed_and_redacted() -> None:
    secret = "never-log-this-value"

    result = WindowsClipboardService(FakeClipboard(fail_write=True)).write(secret)

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert secret not in repr(result)


def test_clipboard_write_rejects_embedded_null_and_size_overflow() -> None:
    backend = FakeClipboard()
    service = WindowsClipboardService(backend, 3)

    null_result = service.write("a\0b")
    large_result = service.write("four")

    assert null_result.status is ToolExecutionStatus.FAILED
    assert large_result.status is ToolExecutionStatus.FAILED
    assert backend.text == ""


def test_clipboard_mutations_fail_when_readback_does_not_match() -> None:
    backend = FakeClipboard(text="old", keep_old_value=True)
    service = WindowsClipboardService(backend)

    write = service.write("new")
    clear = service.clear()

    assert write.status is ToolExecutionStatus.FAILED
    assert clear.status is ToolExecutionStatus.FAILED
    assert not write.verified and not clear.verified


def test_clipboard_native_resource_errors_are_fail_closed() -> None:
    read = WindowsClipboardService(FakeClipboard(fail_read=True)).read()
    clear = WindowsClipboardService(FakeClipboard(fail_clear=True)).clear()

    assert read.status is ToolExecutionStatus.FAILED
    assert clear.status is ToolExecutionStatus.FAILED
    assert read.data is None and clear.data is None


def test_clipboard_tool_contracts_separate_read_and_mutations() -> None:
    executor = ToolExecutor()
    WindowsClipboardService(FakeClipboard()).register_tools(executor)

    read = executor.get("read_windows_clipboard").definition
    write = executor.get("write_windows_clipboard").definition
    clear = executor.get("clear_windows_clipboard").definition

    assert read.risk_level is RiskLevel.READ_ONLY
    assert read.requires_confirmation is False
    assert read.metadata["sensitive_output"] is True
    for definition in (write, clear):
        assert definition.risk_level is RiskLevel.MEDIUM
        assert definition.requires_confirmation is True
    assert write.metadata["sensitive_parameters"] == ("text",)


def test_clipboard_size_limit_must_be_positive() -> None:
    try:
        WindowsClipboardService(FakeClipboard(), 0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("Expected invalid clipboard limit to be rejected.")
