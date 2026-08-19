
def test_tool_executor_generates_openai_schema() -> None:
    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def get_weather(
        city: str,
        days: int = 1,
    ) -> str:
        """Get weather information for a city."""
        return f"{city}: sunny"

    executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information for a city.",
        ),
        get_weather,
    )

    schema = executor.get_openai_tools()

    assert schema == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                        },
                        "days": {
                            "type": "integer",
                            "default": 1,
                        },
                    },
                    "required": ["city"],
                },
            },
        }
    ]

def test_tool_executor_generates_openai_schema() -> None:
    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def get_weather(
        city: str,
        days: int = 1,
    ) -> str:
        """Get weather information for a city."""
        return f"{city}: sunny"

    executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information for a city.",
        ),
        get_weather,
    )

    schema = executor.get_openai_tools()

    assert schema == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                        },
                        "days": {
                            "type": "integer",
                            "default": 1,
                        },
                    },
                    "required": ["city"],
                },
            },
        }
    ]
