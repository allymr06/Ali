from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.engine import CoreEngine
from app.core.interaction_policy import (
    InteractionPolicy,
)
from app.core.models import (
    Request,
    ToolDefinition,
)
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.base import ModelResponse
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor


def build_engine(
    provider,
    *,
    tool_executor=None,
):
    registry = ProviderRegistry()

    registry.register(
        provider,
        make_default=True,
    )

    return CoreEngine(
        registry,
        MemoryManager(
            InMemoryStore()
        ),
        tool_executor=(
            tool_executor
            if tool_executor is not None
            else ToolExecutor()
        ),
    )


def register_launch_tool(
    executor,
    called,
):
    def launch_windows_application(
        application: str,
    ):
        called.append(
            application
        )

        return {
            "application": application,
            "launched": True,
        }

    executor.register(
        ToolDefinition(
            name=(
                "launch_windows_application"
            ),
            description=(
                "Launch a Windows application."
            ),
        ),
        launch_windows_application,
    )


def test_identity_policy_is_provider_free_and_fail_closed(
) -> None:
    policy = InteractionPolicy()

    identity = policy.evaluate(
        Request(
            "sen kimsin?"
        )
    )

    assert identity.kind == "identity"
    assert identity.expose_tools is False
    assert identity.system_prompt is None
    assert identity.direct_response is not None
    assert "JARVIS" in identity.direct_response

    compound = policy.evaluate(
        Request(
            "sen kimsin ve not defterini ac"
        )
    )

    assert compound.kind == "general"
    assert compound.expose_tools is True
    assert compound.direct_response is None


def test_broader_identity_phrase_is_direct(
) -> None:
    policy = InteractionPolicy()

    decision = policy.evaluate(
        Request(
            "genel olarak sen kimsin"
        )
    )

    assert decision.kind == "identity"
    assert decision.expose_tools is False
    assert decision.direct_response is not None
    assert "JARVIS" in decision.direct_response


def test_simple_social_request_suppresses_tools(
) -> None:
    policy = InteractionPolicy()

    decision = policy.evaluate(
        Request(
            "merhaba"
        )
    )

    assert decision.kind == "social"
    assert decision.expose_tools is False
    assert decision.direct_response is None
    assert decision.system_prompt is not None


def test_identity_request_never_calls_provider_or_tool(
) -> None:
    provider_calls = []

    class NeverIdentityProvider(
        MockProvider
    ):
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
            provider_calls.append(
                kwargs
            )

            raise AssertionError(
                "Identity request reached provider."
            )

    executor = ToolExecutor()
    called = []

    register_launch_tool(
        executor,
        called,
    )

    engine = build_engine(
        NeverIdentityProvider(),
        tool_executor=executor,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "sen kimsin?"
            )
        )
    )

    assert provider_calls == []
    assert called == []

    assert "JARVIS" in response.text

    assert (
        response.metadata["provider"]
        == "core"
    )

    assert (
        response.metadata["model"]
        == "jarvis-identity-composer"
    )

    assert (
        response.metadata["model_iterations"]
        == 0
    )

    assert (
        response.metadata["model_tokens"]
        == 0
    )

    assert (
        response.metadata["tool_calls"]
        == 0
    )

    assert (
        response.metadata["interaction_kind"]
        == "identity"
    )

    assert (
        response.metadata["tools_suppressed"]
        is True
    )

    assert (
        response.metadata[
            "provider_metadata"
        ][
            "generation_skipped"
        ]
        is True
    )


def test_identity_variants_keep_fixed_identity(
) -> None:
    policy = InteractionPolicy()

    first = policy.evaluate(
        Request(
            "sen kimsin?",
            request_id=UUID(int=1),
        )
    )

    second = policy.evaluate(
        Request(
            "sen kimsin?",
            request_id=UUID(int=2),
        )
    )

    assert (
        first.direct_response
        != second.direct_response
    )

    assert (
        "JARVIS"
        in first.direct_response
    )

    assert (
        "JARVIS"
        in second.direct_response
    )


def test_compound_request_keeps_tools_available(
) -> None:
    captured = []

    class CaptureProvider(
        MockProvider
    ):
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
            captured.append(
                kwargs
            )

            return ModelResponse(
                text="Tamam.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
            )

    executor = ToolExecutor()
    called = []

    register_launch_tool(
        executor,
        called,
    )

    engine = build_engine(
        CaptureProvider(),
        tool_executor=executor,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "sen kimsin ve not defterini ac"
            )
        )
    )

    assert len(captured) == 1

    assert (
        captured[0]["tools"]
        is not None
    )

    tool_names = {
        item["function"]["name"]
        for item in captured[0]["tools"]
    }

    assert (
        "launch_windows_application"
        in tool_names
    )

    assert (
        response.metadata["tools_suppressed"]
        is False
    )


def test_plaintext_tool_json_is_blocked_and_never_executed(
) -> None:
    raw_payload = (
        '{"name":"launch_windows_application",'
        '"parameters":"{'
        '\\"application\\":\\"Notepad\\"'
        '}"}'
    )

    class BrokenToolProvider(
        MockProvider
    ):
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
            return ModelResponse(
                text=raw_payload,
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
            )

    executor = ToolExecutor()
    called = []

    register_launch_tool(
        executor,
        called,
    )

    engine = build_engine(
        BrokenToolProvider(),
        tool_executor=executor,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Bana k\u0131sa bir cevap ver."
            )
        )
    )

    assert called == []

    assert (
        raw_payload
        not in response.text
    )

    assert (
        "launch_windows_application"
        not in response.text
    )

    assert (
        response.metadata[
            "blocked_plaintext_tool_call"
        ]
        == "launch_windows_application"
    )

    assert (
        response.metadata[
            "provider_metadata"
        ][
            "plaintext_tool_call_blocked"
        ]
        is True
    )

    assert (
        response.metadata["tool_calls"]
        == 0
    )


def test_unknown_json_is_not_mistaken_for_registered_tool(
) -> None:
    policy = InteractionPolicy()

    result = policy.plaintext_tool_name(
        (
            '{"name":"not_a_real_tool",'
            '"parameters":{}}'
        ),
        (
            "launch_windows_application",
        ),
    )

    assert result is None


def test_valid_explanatory_json_is_not_blocked(
) -> None:
    policy = InteractionPolicy()

    result = policy.plaintext_tool_name(
        (
            '{"name":"Ali",'
            '"parameters":42}'
        ),
        (
            "launch_windows_application",
        ),
    )

    assert result is None
