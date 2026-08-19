import pytest

from app.planning.executor import PlanExecutor
from app.planning.models import (
    PlanStatus,
    PlanStep,
    PlanStepStatus,
)
from app.planning.planner import Planner


def make_plan():
    planner = Planner()

    return planner.create_plan(
        "Test planı",
        [
            PlanStep("Araştır"),
            PlanStep(
                "Uygula",
                dependencies=["Araştır"],
            ),
            PlanStep(
                "Doğrula",
                dependencies=["Uygula"],
            ),
        ],
    )


def test_executor_starts_plan():
    plan = make_plan()
    executor = PlanExecutor()

    result = executor.start(plan)

    assert result is plan
    assert plan.status is PlanStatus.RUNNING


def test_executor_starts_paused_plan():
    plan = make_plan()
    plan.status = PlanStatus.PAUSED

    executor = PlanExecutor()

    executor.start(plan)

    assert plan.status is PlanStatus.RUNNING


def test_executor_rejects_start_of_completed_plan():
    plan = make_plan()
    plan.status = PlanStatus.COMPLETED

    with pytest.raises(ValueError):
        PlanExecutor().start(plan)


def test_executor_returns_first_ready_step():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)

    step = executor.next_step(plan)

    assert step is not None
    assert step.name == "Araştır"
    assert step.status is PlanStepStatus.RUNNING


def test_executor_respects_step_dependencies():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)

    first = executor.next_step(plan)

    assert first is not None
    assert first.name == "Araştır"

    executor.complete_step(plan, first)

    second = executor.next_step(plan)

    assert second is not None
    assert second.name == "Uygula"
    assert second.status is PlanStepStatus.RUNNING


def test_executor_completes_plan_after_all_steps():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)

    first = executor.next_step(plan)
    assert first is not None
    executor.complete_step(plan, first)

    second = executor.next_step(plan)
    assert second is not None
    executor.complete_step(plan, second)

    third = executor.next_step(plan)
    assert third is not None
    executor.complete_step(plan, third)

    assert plan.status is PlanStatus.COMPLETED
    assert plan.progress == 1.0


def test_executor_fails_plan_when_step_fails():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)

    step = executor.next_step(plan)
    assert step is not None

    executor.fail_step(plan, step)

    assert step.status is PlanStepStatus.FAILED
    assert plan.status is PlanStatus.FAILED


def test_executor_pauses_running_plan():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)
    executor.pause(plan)

    assert plan.status is PlanStatus.PAUSED


def test_executor_can_resume_paused_plan():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)
    executor.pause(plan)
    executor.start(plan)

    assert plan.status is PlanStatus.RUNNING


def test_executor_cancels_plan():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)
    executor.cancel(plan)

    assert plan.status is PlanStatus.CANCELLED


def test_executor_rejects_next_step_when_not_running():
    plan = make_plan()

    with pytest.raises(ValueError):
        PlanExecutor().next_step(plan)


def test_executor_rejects_completing_non_running_step():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)

    step = plan.get_step("Araştır")

    with pytest.raises(ValueError):
        executor.complete_step(plan, step)


def test_executor_rejects_failing_non_running_step():
    plan = make_plan()
    executor = PlanExecutor()

    executor.start(plan)

    step = plan.get_step("Araştır")

    with pytest.raises(ValueError):
        executor.fail_step(plan, step)


def test_executor_cancel_rejects_completed_plan():
    plan = make_plan()
    plan.status = PlanStatus.COMPLETED

    with pytest.raises(ValueError):
        PlanExecutor().cancel(plan)
