from app.main import create_application


def test_application_wires_task_manager_to_engine() -> None:
    app = create_application()

    assert app.task_manager is app.engine.task_manager
