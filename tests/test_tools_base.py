from app.core.models import ToolDefinition
from app.tools.base import RegisteredTool


def test_registered_tool_exposes_definition_name() -> None:
    definition = ToolDefinition(
        name="test_tool",
        description="A test tool.",
    )

    tool = RegisteredTool(
        definition=definition,
        handler=lambda: "ok",
    )

    assert tool.name == "test_tool"
    assert tool.definition is definition
