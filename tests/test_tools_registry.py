import pytest

from app.core.models import ToolDefinition
from app.tools.base import RegisteredTool
from app.tools.registry import ToolRegistry


def create_tool(name: str = "test_tool") -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            name=name,
            description="Test tool.",
        ),
        handler=lambda: "ok",
    )


def test_registry_starts_empty() -> None:
    registry = ToolRegistry()

    assert len(registry) == 0
    assert registry.list_names() == ()


def test_registry_can_register_tool() -> None:
    registry = ToolRegistry()
    tool = create_tool()

    registry.register(tool)

    assert len(registry) == 1
    assert registry.contains("test_tool")
    assert registry.get("test_tool") is tool


def test_registry_rejects_duplicate_tool() -> None:
    registry = ToolRegistry()

    registry.register(create_tool())

    with pytest.raises(ValueError):
        registry.register(create_tool())


def test_tool_definition_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        create_tool("   ")


def test_registry_rejects_unknown_tool() -> None:
    registry = ToolRegistry()

    with pytest.raises(KeyError):
        registry.get("unknown")


def test_registry_can_unregister_tool() -> None:
    registry = ToolRegistry()
    tool = create_tool()

    registry.register(tool)

    removed = registry.unregister("test_tool")

    assert removed is tool
    assert len(registry) == 0
    assert registry.contains("test_tool") is False


def test_registry_lists_tools() -> None:
    registry = ToolRegistry()

    registry.register(create_tool("alpha"))
    registry.register(create_tool("beta"))

    assert registry.list_names() == ("alpha", "beta")
    assert len(registry.list_tools()) == 2
