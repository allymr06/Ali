from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.models import ToolResult
from app.execution.models import VerificationResult

Verifier = Callable[[Any], bool]


class VerificationEngine:
    """Verify tool execution results."""

    def verify(
        self,
        result: ToolResult,
        verifier: Verifier | None = None,
    ) -> VerificationResult:
        if not result.succeeded:
            return VerificationResult(
                passed=False,
                reason=result.error or result.message or "Tool execution failed.",
            )

        if verifier is None:
            if result.verified is not True:
                return VerificationResult(
                    passed=False,
                    reason="Tool result has no explicit postcondition verification.",
                )
            return VerificationResult(
                passed=True,
                reason="Tool reported an explicitly verified postcondition.",
            )

        try:
            passed = bool(verifier(result.data))
        except Exception as exc:
            return VerificationResult(
                passed=False,
                reason=f"Verification failed with exception: {exc}",
            )

        if not passed:
            return VerificationResult(
                passed=False,
                reason="Custom verification predicate returned false.",
            )

        return VerificationResult(passed=True)
