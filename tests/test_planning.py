from app.planning.models import (
    Plan,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
)
from app.planning.planner import Planner


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
