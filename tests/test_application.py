from app.main import create_application


def test_application_wires_task_manager_to_engine() -> None:
    app = create_application()

    assert app.task_manager is app.engine.task_manager


def test_application_exposes_agent_loop():
    from app.agent.loop import AgentLoop
    from app.main import create_application

    application = create_application()

    assert isinstance(
        application.agent_loop,
        AgentLoop,
    )

    assert (
        application.agent_loop.engine
        is application.engine
    )
