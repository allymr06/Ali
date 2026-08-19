import pytest
from app.planning.models import (
    Plan,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
)
from app.planning.planner import Planner
from app.planning.executor import PlanExecutor


def test_create_empty_plan():
    planner = Planner()

    plan = planner.create_plan("Test görevi")

    assert plan.goal == "Test görevi"
    assert plan.status is PlanStatus.DRAFT
    assert plan.steps == []
    assert plan.progress == 0.0


def test_create_plan_with_steps():
    planner = Planner()

    steps = [
        PlanStep("Araştır"),
        PlanStep("Uygula"),
    ]

    plan = planner.create_plan(
        "Test görevi",
        steps,
    )

    assert plan.status is PlanStatus.READY
    assert len(plan.steps) == 2
    assert plan.progress == 0.0


def test_plan_rejects_empty_goal():
    planner = Planner()

    import pytest

    with pytest.raises(ValueError):
        planner.create_plan("   ")


def test_plan_rejects_empty_step_name():
    planner = Planner()

    import pytest

    with pytest.raises(ValueError):
        planner.create_plan(
            "Test",
            [PlanStep("   ")],
        )


def test_plan_rejects_duplicate_step_names():
    planner = Planner()

    import pytest

    with pytest.raises(ValueError):
        planner.create_plan(
            "Test",
            [
                PlanStep("Araştır"),
                PlanStep("Araştır"),
            ],
        )


def test_plan_rejects_unknown_dependency():
    planner = Planner()

    import pytest

    with pytest.raises(ValueError):
        planner.create_plan(
            "Test",
            [
                PlanStep(
                    "Uygula",
                    dependencies=["Olmayan"],
                )
            ],
        )


def test_plan_rejects_circular_dependency():
    planner = Planner()

    import pytest

    with pytest.raises(ValueError):
        planner.create_plan(
            "Test",
            [
                PlanStep(
                    "A",
                    dependencies=["B"],
                ),
                PlanStep(
                    "B",
                    dependencies=["A"],
                ),
            ],
        )


def test_ready_steps_respect_dependencies():
    planner = Planner()

    plan = planner.create_plan(
        "Test",
        [
            PlanStep("Araştır"),
            PlanStep(
                "Uygula",
                dependencies=["Araştır"],
            ),
        ],
    )

    ready = planner.get_ready_steps(plan)

    assert [step.name for step in ready] == [
        "Araştır"
    ]

    plan.get_step(
        "Araştır"
    ).status = PlanStepStatus.COMPLETED

    ready = planner.get_ready_steps(plan)

    assert [step.name for step in ready] == [
        "Uygula"
    ]


def test_plan_progress_tracks_completed_steps():
    planner = Planner()

    plan = planner.create_plan(
        "Test",
        [
            PlanStep("A"),
            PlanStep("B"),
            PlanStep("C"),
        ],
    )

    assert plan.progress == 0.0

    plan.get_step("A").status = (
        PlanStepStatus.COMPLETED
    )

    assert plan.progress == 1 / 3

    plan.get_step("B").status = (
        PlanStepStatus.COMPLETED
    )

    assert plan.progress == 2 / 3


def test_plan_get_step_returns_matching_step():
    planner = Planner()

    plan = planner.create_plan(
        "Test",
        [PlanStep("Araştır")],
    )

    assert (
        plan.get_step("Araştır").name
        == "Araştır"
    )


def test_plan_get_step_rejects_unknown_step():
    planner = Planner()

    import pytest

    plan = planner.create_plan(
        "Test",
        [PlanStep("Araştır")],
    )

    with pytest.raises(KeyError):
        plan.get_step("Yok")


def test_plan_executor_does_not_start_completed_plan():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Tamamlanmis plan",
        [PlanStep("step_1")],
    )

    plan.status = PlanStatus.COMPLETED

    with pytest.raises(ValueError):
        executor.start(plan)


def test_plan_executor_does_not_start_cancelled_plan():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Iptal edilmis plan",
        [PlanStep("step_1")],
    )

    plan.status = PlanStatus.CANCELLED

    with pytest.raises(ValueError):
        executor.start(plan)


def test_plan_executor_respects_dependency_order():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Bagimli plan",
        [
            PlanStep("first"),
            PlanStep(
                "second",
                dependencies=["first"],
            ),
            PlanStep(
                "third",
                dependencies=["second"],
            ),
        ],
    )

    executor.start(plan)

    first = executor.next_step(plan)
    assert first is plan.get_step("first")

    executor.complete_step(plan, first)

    second = executor.next_step(plan)
    assert second is plan.get_step("second")

    executor.complete_step(plan, second)

    third = executor.next_step(plan)
    assert third is plan.get_step("third")


def test_plan_executor_does_not_run_step_before_dependency():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Dependency testi",
        [
            PlanStep("first"),
            PlanStep(
                "second",
                dependencies=["first"],
            ),
        ],
    )

    executor.start(plan)

    step = executor.next_step(plan)

    assert step is plan.get_step("first")
    assert plan.get_step("second").status is PlanStepStatus.PENDING


def test_plan_executor_completes_plan_after_all_steps():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Tamamlama testi",
        [
            PlanStep("first"),
            PlanStep("second"),
        ],
    )

    executor.start(plan)

    first = executor.next_step(plan)
    executor.complete_step(plan, first)

    second = executor.next_step(plan)
    executor.complete_step(plan, second)

    assert plan.status is PlanStatus.COMPLETED
    assert executor.next_step(plan) is None


def test_plan_executor_failure_fails_plan():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Failure testi",
        [PlanStep("dangerous_step")],
    )

    executor.start(plan)

    step = executor.next_step(plan)
    executor.fail_step(plan, step)

    assert step.status is PlanStepStatus.FAILED
    assert plan.status is PlanStatus.FAILED


def test_plan_executor_pause_requires_running_plan():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Pause testi",
        [PlanStep("step")],
    )

    with pytest.raises(ValueError):
        executor.pause(plan)


def test_plan_executor_pause_and_resume():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Pause resume testi",
        [PlanStep("step")],
    )

    executor.start(plan)
    executor.pause(plan)

    assert plan.status is PlanStatus.PAUSED

    executor.start(plan)

    assert plan.status is PlanStatus.RUNNING


def test_plan_executor_cancel_prevents_restart():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Cancel testi",
        [PlanStep("step")],
    )

    executor.start(plan)
    executor.cancel(plan)

    assert plan.status is PlanStatus.CANCELLED

    with pytest.raises(ValueError):
        executor.start(plan)


def test_plan_executor_cannot_complete_non_running_step():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Invalid completion",
        [PlanStep("step")],
    )

    executor.start(plan)

    step = plan.get_step("step")

    with pytest.raises(ValueError):
        executor.complete_step(plan, step)


def test_plan_executor_cannot_fail_non_running_step():
    planner = Planner()
    executor = PlanExecutor(planner)

    plan = planner.create_plan(
        "Invalid failure",
        [PlanStep("step")],
    )

    executor.start(plan)

    step = plan.get_step("step")

    with pytest.raises(ValueError):
        executor.fail_step(plan, step)

