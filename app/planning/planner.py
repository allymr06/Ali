from __future__ import annotations

from collections import defaultdict

from app.planning.models import (
    Plan,
    PlanStep,
    PlanStepStatus,
    PlanStatus,
)


class Planner:
    """Create and validate executable plans for JARVIS."""

    def create_plan(
        self,
        goal: str,
        steps: list[PlanStep] | None = None,
    ) -> Plan:
        goal = goal.strip()

        if not goal:
            raise ValueError("Plan goal cannot be empty.")

        plan = Plan(
            goal=goal,
            steps=list(steps or []),
        )

        self.validate(plan)

        if plan.steps:
            plan.status = PlanStatus.READY

        return plan

    def validate(self, plan: Plan) -> None:
        if not plan.goal.strip():
            raise ValueError("Plan goal cannot be empty.")

        names: set[str] = set()

        for step in plan.steps:
            if not step.name.strip():
                raise ValueError(
                    "Plan step name cannot be empty."
                )

            if step.name in names:
                raise ValueError(
                    f"Duplicate plan step: {step.name}"
                )

            names.add(step.name)

        for step in plan.steps:
            for dependency in step.dependencies:
                if dependency not in names:
                    raise ValueError(
                        f"Unknown dependency: {dependency}"
                    )

        self._validate_cycles(plan)

    def get_ready_steps(self, plan: Plan) -> list[PlanStep]:
        self.validate(plan)

        ready: list[PlanStep] = []

        for step in plan.steps:
            if step.status is not PlanStepStatus.PENDING:
                continue

            if all(
                plan.get_step(dependency).status
                is PlanStepStatus.COMPLETED
                for dependency in step.dependencies
            ):
                ready.append(step)

        return ready

    @staticmethod
    def _validate_cycles(plan: Plan) -> None:
        graph = defaultdict(list)

        for step in plan.steps:
            graph[step.name].extend(step.dependencies)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise ValueError(
                    "Circular dependency detected."
                )

            if name in visited:
                return

            visiting.add(name)

            for dependency in graph[name]:
                visit(dependency)

            visiting.remove(name)
            visited.add(name)

        for step in plan.steps:
            visit(step.name)
