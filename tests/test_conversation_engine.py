from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.conversation.engine import ConversationEngine
from app.conversation.models import (
    Conversation,
    ConversationStatus,
    ConversationTurn,
    MessageRole,
)
from app.conversation.store import InMemoryConversationStore
from app.core.engine import CoreEngine
from app.core.models import Context, Request, Response, ToolDefinition
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor


def test_turn_contract_rejects_invalid_role_payloads():
    conversation_id = uuid4()

    with pytest.raises(ValueError):
        ConversationTurn(conversation_id, MessageRole.USER, "")
    with pytest.raises(ValueError):
        ConversationTurn(conversation_id, MessageRole.ASSISTANT, None)
    with pytest.raises(ValueError):
        ConversationTurn(conversation_id, MessageRole.TOOL, "result")


def test_turn_serializes_provider_message_contract():
    turn = ConversationTurn(
        conversation_id=uuid4(),
        role=MessageRole.ASSISTANT,
        content=None,
        tool_calls=[{"id": "call", "function": {"name": "tool"}}],
    )

    assert turn.to_message() == {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call", "function": {"name": "tool"}}],
    }


def test_conversation_rejects_foreign_and_duplicate_turns():
    conversation = Conversation()
    foreign = ConversationTurn(uuid4(), MessageRole.USER, "hello")

    with pytest.raises(ValueError, match="another conversation"):
        conversation.add_turn(foreign)

    turn = ConversationTurn(
        conversation.conversation_id,
        MessageRole.USER,
        "hello",
    )
    conversation.add_turn(turn)
    with pytest.raises(ValueError, match="Duplicate"):
        conversation.add_turn(turn)


def test_store_returns_isolated_copies():
    store = InMemoryConversationStore()
    conversation = Conversation()
    store.save(conversation)

    loaded = store.get(conversation.conversation_id)
    loaded.metadata["changed"] = True

    assert store.get(conversation.conversation_id).metadata == {}


def test_engine_create_ensure_archive_and_delete_lifecycle():
    engine = ConversationEngine()
    conversation_id = uuid4()

    created = engine.create(conversation_id)
    assert engine.ensure(conversation_id).conversation_id == created.conversation_id
    with pytest.raises(ValueError, match="already exists"):
        engine.create(conversation_id)

    archived = engine.archive(conversation_id)
    assert archived.status is ConversationStatus.ARCHIVED

    with pytest.raises(ValueError, match="archived"):
        engine.prepare_request(Request("hello"), Context(conversation_id=conversation_id))

    assert engine.activate(conversation_id).status is ConversationStatus.ACTIVE
    assert len(engine.list()) == 1

    removed = engine.delete(conversation_id)
    assert removed.conversation_id == conversation_id
    with pytest.raises(KeyError):
        engine.get(conversation_id)


def test_prepare_request_is_idempotent_by_request_id():
    engine = ConversationEngine()
    context = Context()
    request = Request("hello")

    engine.prepare_request(request, context)
    engine.prepare_request(request, context)

    conversation = engine.get(context.conversation_id)
    assert len(conversation.turns) == 1
    assert context.values["messages"] == [{"role": "user", "content": "hello"}]


def test_complete_response_is_idempotent_by_response_id():
    engine = ConversationEngine()
    context = Context()
    request = Request("hello")
    response = Response("hi", request_id=request.request_id)
    engine.prepare_request(request, context)

    first = engine.complete_response(request, response, context)
    second = engine.complete_response(request, response, context)

    assert first.turn_id == second.turn_id
    assert len(engine.get(context.conversation_id).turns) == 2


def test_complete_response_persists_assurance_and_reasoning_metadata():
    engine = ConversationEngine()
    context = Context()
    request = Request("kanıtlı yanıt")
    response = Response(
        "yanıt",
        request_id=request.request_id,
        metadata={
            "provider": "gemini",
            "model": "gemini-test",
            "outcome": "completed",
            "reasoning_level": "medium",
            "assurance_level": "research_supported",
            "uncertainty_summary": "Yayın tarihi bilinmiyor.",
        },
    )
    engine.prepare_request(request, context)

    turn = engine.complete_response(request, response, context)

    assert turn is not None
    assert turn.metadata == {
        "outcome": "completed",
        "provider": "gemini",
        "model": "gemini-test",
        "reasoning_level": "medium",
        "assurance_level": "research_supported",
        "uncertainty_summary": "Yayın tarihi bilinmiyor.",
    }


def test_engine_preserves_atomic_tool_chain():
    engine = ConversationEngine(max_context_messages=2)
    context = Context()
    request = Request("weather")
    engine.prepare_request(request, context)
    engine.add_assistant_tool_calls(
        context,
        request_id=request.request_id,
        content=None,
        tool_calls=[{"id": "call", "function": {"name": "weather"}}],
    )
    engine.add_tool_result(
        context,
        request_id=request.request_id,
        tool_call_id="call",
        content="sunny",
    )

    assert [message["role"] for message in context.values["messages"]] == [
        "user",
        "assistant",
        "tool",
    ]


def test_sensitive_tool_content_is_ephemeral_for_provider_only():
    store = InMemoryConversationStore()
    engine = ConversationEngine(store)
    context = Context()
    request = Request("panoyu oku")
    engine.prepare_request(request, context)
    engine.add_assistant_tool_calls(
        context,
        request_id=request.request_id,
        content=None,
        tool_calls=[{"id": "call", "function": {"name": "clipboard"}}],
    )
    engine.add_tool_result(
        context,
        request_id=request.request_id,
        tool_call_id="call",
        content="Sensitive output was not retained.",
        provider_content="çok gizli pano metni",
    )

    assert context.values["messages"][-1]["content"] == "çok gizli pano metni"
    persisted = store.get(context.conversation_id)
    assert persisted.turns[-1].content == "Sensitive output was not retained."
    assert "çok gizli" not in repr(persisted)


def test_context_budget_summarizes_omitted_turn_groups_without_deleting_history():
    engine = ConversationEngine(
        max_context_messages=2,
        max_context_characters=200,
        summary_max_characters=150,
    )
    context = Context()

    for index in range(3):
        request = Request(f"question {index}")
        engine.prepare_request(request, context)
        engine.complete_response(
            request,
            Response(f"answer {index}", request_id=request.request_id),
            context,
        )

    conversation = engine.get(context.conversation_id)
    messages = context.values["messages"]

    assert len(conversation.turns) == 6
    assert conversation.summary_turn_count == 4
    assert "question 0" in conversation.summary
    assert messages[0]["role"] == "system"
    assert messages[-2:] == [
        {"role": "user", "content": "question 2"},
        {"role": "assistant", "content": "answer 2"},
    ]
    assert "summary_updated_at" in conversation.metadata


def test_system_prompt_is_injected_without_becoming_a_persisted_turn():
    engine = ConversationEngine(system_prompt="You are JARVIS.")
    context = Context()
    engine.prepare_request(Request("hello"), context)

    assert context.values["messages"][0] == {
        "role": "system",
        "content": "You are JARVIS.",
    }
    assert len(engine.get(context.conversation_id).turns) == 1


@pytest.mark.asyncio
async def test_core_preserves_multi_turn_conversation_and_current_user_message():
    captured = []

    class CapturingProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            captured.append(list(context.values["messages"]))
            return SimpleNamespace(
                text=f"answer:{request.text}",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    registry = ProviderRegistry()
    registry.register(CapturingProvider(), make_default=True)
    engine = CoreEngine(registry, MemoryManager(InMemoryStore()))
    context = Context()

    first = await engine.handle(Request("first"), context)
    second = await engine.handle(Request("second"), context)

    assert [message["content"] for message in captured[0]] == ["first"]
    assert [message["content"] for message in captured[1]] == [
        "first",
        "answer:first",
        "second",
    ]
    assert first.metadata["conversation_id"] == str(context.conversation_id)
    assert second.metadata["conversation_id"] == str(context.conversation_id)
    assert second.metadata["conversation_turn_count"] == 4
    assert second.metadata["conversation_turn_id"] is not None
    assert len(engine.conversation_engine.get(context.conversation_id).turns) == 4


@pytest.mark.asyncio
async def test_core_persists_verified_tool_chain_and_final_response():
    class ToolProvider(MockProvider):
        def __init__(self):
            self.calls = 0

        async def generate(self, request, context, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    text="",
                    model="mock-model",
                    provider="mock",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call_echo",
                            "function": {
                                "name": "echo",
                                "arguments": '{"value":"ok"}',
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )
            return SimpleNamespace(
                text="finished",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    registry = ProviderRegistry()
    registry.register(ToolProvider(), make_default=True)
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(name="echo", description="Echo"),
        lambda value: value,
    )
    engine = CoreEngine(
        registry,
        MemoryManager(InMemoryStore()),
        tool_executor=tools,
    )
    context = Context()

    await engine.handle(Request("run echo"), context)

    turns = engine.conversation_engine.get(context.conversation_id).turns
    assert [turn.role for turn in turns] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert turns[1].tool_calls[0]["id"] == "call_echo"
    assert turns[2].tool_call_id == "call_echo"
    assert turns[2].metadata["verified"] is True


@pytest.mark.asyncio
async def test_core_rejects_tool_call_without_identity_and_preserves_chain():
    executed = False

    class MissingIdentityProvider(MockProvider):
        def __init__(self):
            self.calls = 0

        async def generate(self, request, context, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    text="",
                    model="mock-model",
                    provider="mock",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "function": {
                                "name": "unsafe",
                                "arguments": "{}",
                            }
                        }
                    ],
                    usage={},
                    metadata={},
                )
            messages = context.values["messages"]
            assistant = messages[-2]
            tool = messages[-1]
            assert assistant["tool_calls"][0]["id"].startswith("invalid-")
            assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"]
            assert "id is missing" in tool["content"]
            return SimpleNamespace(
                text="rejected",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    def unsafe():
        nonlocal executed
        executed = True

    registry = ProviderRegistry()
    registry.register(MissingIdentityProvider(), make_default=True)
    tools = ToolExecutor()
    tools.register(ToolDefinition(name="unsafe", description="Unsafe"), unsafe)
    engine = CoreEngine(
        registry,
        MemoryManager(InMemoryStore()),
        tool_executor=tools,
    )

    response = await engine.handle(Request("run"), Context())

    assert executed is False
    assert response.metadata["invalid_tool_calls"] == 1
    assert response.metadata["tool_calls"] == 0
