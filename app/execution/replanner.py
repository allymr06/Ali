from __future__ import annotations

from collections.abc import Callable

from app.planning.models import Plan, PlanStep, PlanStepStatus


ReplanCallback = Callable[
    [Plan, PlanStep, str],
    Plan | None,
]


class Replanner:
    """Recover failed plans by producing a replacement executable plan."""

    def __init__(
        self,
        callback: ReplanCallback | None = None,
        *,
        max_replans: int = 1,
    ) -> None:
        if max_replans < 0:
            raise ValueError("max_replans cannot be negative.")

        self._callback = callback
        self.max_replans = max_replans

    def can_replan(self, attempts: int) -> bool:
        return (
            self._callback is not None
            and attempts < self.max_replans
        )

    def replan(
        self,
        plan: Plan,
        failed_step: PlanStep,
        error: str,
    ) -> Plan | None:
        if self._callback is None:
            return None

        replacement = self._callback(
            plan,
            failed_step,
            error,
        )

        if replacement is None:
            return None

        if not replacement.goal.strip():
            raise ValueError(
                "Replanner returned a plan with an empty goal."
            )

        if not replacement.steps:
            raise ValueError(
                "Replanner returned an empty plan."
            )

        return replacement


def replace_failed_step(
    plan: Plan,
    failed_step: PlanStep,
    replacement: PlanStep,
) -> Plan:
    """Replace one failed step while preserving the remaining plan."""

    index = plan.steps.index(failed_step)

    replacement.dependencies = list(
        failed_step.dependencies
    )

    replacement.status = PlanStepStatus.PENDING

    plan.steps[index] = replacement
    plan.status = plan.status.__class__.READY

    return plan
