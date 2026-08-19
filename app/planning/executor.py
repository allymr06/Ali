from __future__ import annotations

from app.planning.models import (
    Plan,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
)
from app.planning.planner import Planner


class PlanExecutor:
    """Execute a validated plan step-by-step."""

    def __init__(self, planner: Planner | None = None) -> None:
        self._planner = planner or Planner()

    def start(self, plan: Plan) -> Plan:
        self._planner.validate(plan)

        if plan.status not in (
            PlanStatus.DRAFT,
            PlanStatus.READY,
            PlanStatus.PAUSED,
        ):
            raise ValueError(
                f"Cannot start plan from state {plan.status.value}."
            )

        plan.status = PlanStatus.RUNNING
        return plan

    def next_step(self, plan: Plan) -> PlanStep | None:
        self._planner.validate(plan)

        if plan.status is PlanStatus.COMPLETED:
            return None

        if plan.status is not PlanStatus.RUNNING:
            raise ValueError(
                f"Cannot get next step from state {plan.status.value}."
            )

        ready_steps = self._planner.get_ready_steps(plan)

        if not ready_steps:
            if plan.steps and all(
                step.status
                in (
                    PlanStepStatus.COMPLETED,
                    PlanStepStatus.SKIPPED,
                )
                for step in plan.steps
            ):
                plan.status = PlanStatus.COMPLETED
                return None

            return None

        step = ready_steps[0]
        step.status = PlanStepStatus.RUNNING
        return step

    def complete_step(
        self,
        plan: Plan,
        step: PlanStep,
    ) -> PlanStep:
        if plan.status is not PlanStatus.RUNNING:
            raise ValueError(
                f"Cannot complete step while plan is "
                f"in state {plan.status.value}."
            )

        if step.status is not PlanStepStatus.RUNNING:
            raise ValueError(
                f"Cannot complete step from state "
                f"{step.status.value}."
            )

        step.status = PlanStepStatus.COMPLETED

        if plan.steps and all(
            item.status
            in (
                PlanStepStatus.COMPLETED,
                PlanStepStatus.SKIPPED,
            )
            for item in plan.steps
        ):
            plan.status = PlanStatus.COMPLETED

        return step

    def fail_step(
        self,
        plan: Plan,
        step: PlanStep,
    ) -> PlanStep:
        if plan.status is not PlanStatus.RUNNING:
            raise ValueError(
                f"Cannot fail step while plan is "
                f"in state {plan.status.value}."
            )

        if step.status is not PlanStepStatus.RUNNING:
            raise ValueError(
                f"Cannot fail step from state "
                f"{step.status.value}."
            )

        step.status = PlanStepStatus.FAILED
        plan.status = PlanStatus.FAILED

        return step

    def pause(self, plan: Plan) -> Plan:
        if plan.status is not PlanStatus.RUNNING:
            raise ValueError(
                f"Cannot pause plan from state {plan.status.value}."
            )

        plan.status = PlanStatus.PAUSED
        return plan

    def cancel(self, plan: Plan) -> Plan:
        if plan.status in (
            PlanStatus.COMPLETED,
            PlanStatus.CANCELLED,
        ):
            raise ValueError(
                f"Cannot cancel plan from state {plan.status.value}."
            )

        plan.status = PlanStatus.CANCELLED
        return plan
