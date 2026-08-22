from __future__ import annotations

import asyncio

from app.core.engine import CoreEngine
from app.core.models import (
    Context,
    Request,
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
)
from app.providers.gateway import ProviderGateway
from app.providers.ollama_hybrid import OllamaHybridPolicy
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor
from app.tools.fast_actions import (
    ApprovedApplicationFastRouter,
)


def make_router():
    return ApprovedApplicationFastRouter(
        {
            "notepad": (
                "notepad",
                "Notepad",
            ),
            "editor": (
                "notepad",
                "Notepad",
            ),
            "calculator": (
                "calculator",
                "Calculator",
            ),
        }
    )


def test_exact_launch_routes():
    route = make_router().route(
        Request(
            "Notepad a\u00e7."
        ),
        available_tool_names={
            "launch_windows_application",
        },
    )

    assert route is not None

    assert route.parameters == {
        "application": "notepad",
    }


def test_prefix_launch_routes():
    route = make_router().route(
        Request(
            "L\u00fctfen a\u00e7 Notepad."
        ),
        available_tool_names={
            "launch_windows_application",
        },
    )

    assert route is not None

    assert route.parameters == {
        "application": "notepad",
    }


def test_apostrophe_object_suffix_routes():
    route = make_router().route(
        Request(
            "Notepad'i a\u00e7."
        ),
        available_tool_names={
            "launch_windows_application",
        },
    )

    assert route is not None

    assert route.parameters == {
        "application": "notepad",
    }


def test_ambiguous_commands_fail_closed():
    router = make_router()

    for text in (
        "Notepad nas\u0131l a\u00e7\u0131l\u0131r?",
        "Notepad a\u00e7may\u0131 anlat.",
        "Notepad kapat.",
        "Bilinmeyen uygulama a\u00e7.",
        "Notepad a\u00e7 ve sonra dosya sil.",
    ):
        assert (
            router.route(
                Request(text),
                available_tool_names={
                    (
                        "launch_windows_"
                        "application"
                    ),
                },
            )
            is None
        )


class CapturingProvider(AIProvider):
    def __init__(self):
        self.calls = []

    @property
    def name(self):
        return "ollama"

    @property
    def capabilities(self):
        return ModelCapabilities(
            text=True,
            streaming=True,
            structured_output=True,
            tool_calling=True,
            vision=False,
        )

    @property
    def is_configured(self):
        return True

    async def generate(
        self,
        request,
        context,
        *,
        model=None,
        system_prompt=None,
        tools=None,
        response_format=None,
    ):
        self.calls.append(
            {
                "model": model,
                "tools": tools,
            }
        )

        return ModelResponse(
            text="provider-called",
            model=(
                model
                or "llama3.2:latest"
            ),
            provider="ollama",
        )


def test_exact_launch_skips_model():
    provider = (
        CapturingProvider()
    )

    registry = ProviderRegistry(
        default_provider="ollama",
    )

    registry.register(
        provider
    )

    executor = ToolExecutor()
    launched = []

    def launch(
        application: str,
    ):
        launched.append(
            application
        )

        return ToolResult(
            status=(
                ToolExecutionStatus.SUCCESS
            ),
            tool_name=(
                "launch_windows_application"
            ),
            message="launched",
            data={
                "application_id": (
                    application
                )
            },
            verified=True,
        )

    executor.register(
        ToolDefinition(
            name=(
                "launch_windows_application"
            ),
            description=(
                "Launch approved app."
            ),
            risk_level=RiskLevel.LOW,
        ),
        launch,
    )

    gateway = ProviderGateway(
        registry,
        max_retries=0,
        fallback_enabled=False,
    )

    engine = CoreEngine(
        provider_registry=registry,
        memory_manager=MemoryManager(
            InMemoryStore()
        ),
        tool_executor=executor,
        provider_gateway=gateway,
        ollama_hybrid_policy=(
            OllamaHybridPolicy(
                enabled=True,
                chat_model="gemma3:4b",
                tool_model=(
                    "llama3.2:latest"
                ),
            )
        ),
        fast_action_router=(
            make_router()
        ),
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Notepad a\u00e7."
            ),
            Context(),
        )
    )

    assert provider.calls == []

    assert launched == [
        "notepad",
    ]

    assert (
        response.text
        == "Notepad a\u00e7\u0131ld\u0131."
    )

    assert (
        response.metadata[
            "fast_action_route"
        ]
        == "launch_windows_application"
    )

    assert (
        response.metadata[
            "model_iterations"
        ]
        == 0
    )

    assert (
        response.metadata[
            "completion_verified"
        ]
        is True
    )
