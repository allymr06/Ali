from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.conversation.models import (
    Conversation,
    ConversationTurn,
    MessageRole,
)
from app.conversation.sqlite import (
    SQLiteConversationStore,
)
from app.core.models import Response
from app.providers.gemini import GeminiProvider
from app.ui.controller import DesktopController


def test_sqlite_conversation_round_trip_survives_reopen(
    tmp_path,
) -> None:
    path = (
        tmp_path
        / "conversations.sqlite3"
    )

    conversation_id = uuid4()
    request_id = uuid4()
    response_id = uuid4()

    store = SQLiteConversationStore(path)

    conversation = Conversation(
        conversation_id=conversation_id
    )

    conversation.add_turn(
        ConversationTurn(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content="Merhaba JARVIS",
            request_id=request_id,
            metadata={
                "source": "text",
            },
        )
    )

    conversation.add_turn(
        ConversationTurn(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content="Merhaba.",
            request_id=request_id,
            response_id=response_id,
            metadata={
                "provider": "gemini",
            },
        )
    )

    store.save(conversation)
    store.close()

    reopened = SQLiteConversationStore(
        path
    )

    restored = reopened.get(
        conversation_id
    )

    assert (
        restored.conversation_id
        == conversation_id
    )

    assert [
        turn.role
        for turn
        in restored.turns
    ] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]

    assert [
        turn.content
        for turn
        in restored.turns
    ] == [
        "Merhaba JARVIS",
        "Merhaba.",
    ]

    assert (
        restored.turns[0].request_id
        == request_id
    )

    assert (
        restored.turns[1].response_id
        == response_id
    )

    assert (
        restored.turns[1]
        .metadata["provider"]
        == "gemini"
    )

    reopened.close()


def test_sqlite_conversation_list_is_latest_first(
    tmp_path,
) -> None:
    store = SQLiteConversationStore(
        tmp_path
        / "conversations.sqlite3"
    )

    base_time = datetime(
        2026,
        1,
        1,
        12,
        0,
        0,
        tzinfo=timezone.utc,
    )

    first = Conversation(
        created_at=base_time,
        updated_at=base_time,
    )

    store.save(first)

    second_time = (
        base_time
        + timedelta(seconds=1)
    )

    second = Conversation(
        created_at=second_time,
        updated_at=second_time,
    )

    second.add_turn(
        ConversationTurn(
            conversation_id=(
                second.conversation_id
            ),
            role=MessageRole.USER,
            content="newer",
        )
    )

    # add_turn() correctly refreshes updated_at using
    # the live clock. Set an explicit timestamp here so
    # this ordering regression test never depends on
    # machine clock resolution.
    second.updated_at = second_time

    store.save(second)

    assert [
        item.conversation_id
        for item
        in store.list()
    ] == [
        second.conversation_id,
        first.conversation_id,
    ]

    store.close()


def test_sqlite_conversation_list_breaks_timestamp_ties_by_latest_insert(
    tmp_path,
) -> None:
    store = SQLiteConversationStore(
        tmp_path
        / "conversations.sqlite3"
    )

    timestamp = datetime(
        2026,
        1,
        1,
        12,
        0,
        0,
        tzinfo=timezone.utc,
    )

    first = Conversation(
        created_at=timestamp,
        updated_at=timestamp,
    )

    second = Conversation(
        created_at=timestamp,
        updated_at=timestamp,
    )

    store.save(first)
    store.save(second)

    conversations = store.list()

    assert [
        conversation.conversation_id
        for conversation
        in conversations
    ] == [
        second.conversation_id,
        first.conversation_id,
    ]

    store.close()



def test_sqlite_conversation_delete_is_durable(
    tmp_path,
) -> None:
    path = (
        tmp_path
        / "conversations.sqlite3"
    )

    store = SQLiteConversationStore(path)

    conversation = store.save(
        Conversation()
    )

    deleted = store.delete(
        conversation.conversation_id
    )

    assert (
        deleted.conversation_id
        == conversation.conversation_id
    )

    with pytest.raises(KeyError):
        store.get(
            conversation.conversation_id
        )

    store.close()

    reopened = SQLiteConversationStore(
        path
    )

    assert reopened.list() == ()

    reopened.close()


def test_sqlite_store_close_is_idempotent(
    tmp_path,
) -> None:
    store = SQLiteConversationStore(
        tmp_path
        / "conversations.sqlite3"
    )

    store.close()
    store.close()


def test_closed_sqlite_store_rejects_reads(
    tmp_path,
) -> None:
    store = SQLiteConversationStore(
        tmp_path
        / "conversations.sqlite3"
    )

    store.close()

    with pytest.raises(
        RuntimeError,
        match="closed",
    ):
        store.list()


def test_application_restores_conversation_after_restart(
    tmp_path,
) -> None:
    database = str(
        tmp_path
        / "conversations.sqlite3"
    )

    settings = Settings(
        conversation_database_path=database,
        memory_database_path=None,
        task_database_path=None,
        task_runtime_directory=None,
        windows_integrations_enabled=False,
    )

    first_app = create_application(
        settings
    )

    first_controller = DesktopController(
        first_app
    )

    first_id = (
        first_controller
        .context
        .conversation_id
    )

    asyncio.run(
        first_controller.submit_command(
            "Persist this conversation"
        )
    )

    first_controller.close()

    second_app = create_application(
        settings
    )

    second_controller = DesktopController(
        second_app
    )

    assert (
        second_controller
        .context
        .conversation_id
        == first_id
    )

    assert [
        (
            item.role,
            item.text,
        )
        for item
        in second_controller
        .state
        .messages
    ] == [
        (
            "user",
            "Persist this conversation",
        ),
        (
            "assistant",
            (
                "Mock yan\u0131t\u0131: "
                "Persist this conversation"
            ),
        ),
    ]

    second_controller.close()


@pytest.mark.asyncio
async def test_controller_recovers_and_accepts_next_message_after_error(
) -> None:
    app = create_application(
        Settings(
            conversation_database_path=None,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            windows_integrations_enabled=False,
        )
    )

    controller = DesktopController(
        app
    )

    calls = 0

    async def flaky_handle(
        request,
        context,
    ):
        nonlocal calls

        calls += 1

        if calls == 1:
            raise RuntimeError(
                "temporary provider failure"
            )

        return Response(
            "Recovered response",
            request_id=(
                request.request_id
            ),
        )

    app.engine.handle = flaky_handle

    failed = await controller.submit_command(
        "first"
    )

    recovered = (
        await controller.submit_command(
            "second"
        )
    )

    assert failed.role == "system"

    assert (
        "tekrar deneyebilirsin"
        in failed.text
    )

    assert (
        recovered.role
        == "assistant"
    )

    assert (
        recovered.text
        == "Recovered response"
    )

    assert (
        controller.state.busy
        is False
    )

    assert (
        controller.state.status
        == "LOCAL CORE READY"
    )

    assert calls == 2

    controller.close()


class FakeCompletions:
    def __init__(self) -> None:
        self.calls = []

    async def create(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="fast",
                        tool_calls=[],
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )


class FakeGeminiClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(
            completions=FakeCompletions()
        )


@pytest.mark.asyncio
async def test_gemini_chat_defaults_to_low_reasoning_effort(
) -> None:
    client = FakeGeminiClient()

    provider = GeminiProvider(
        Settings(
            gemini_model=(
                "gemini-3.7-flash"
            ),
            gemini_reasoning_effort=(
                "low"
            ),
        ),
        client=client,
    )

    from app.core.models import (
        Context,
        Request,
    )

    response = await provider.generate(
        Request(
            "quick response"
        ),
        Context(),
    )

    assert response.text == "fast"

    assert (
        client.chat
        .completions
        .calls[0][
            "reasoning_effort"
        ]
        == "low"
    )


@pytest.mark.asyncio
async def test_gemini_flash_lite_uses_minimal_reasoning_effort(
) -> None:
    client = FakeGeminiClient()
    provider = GeminiProvider(
        Settings(
            gemini_model="gemini-3.5-flash-lite",
            gemini_reasoning_effort="minimal",
        ),
        client=client,
    )

    from app.core.models import Context, Request

    await provider.generate(Request("quick response"), Context())

    assert (
        client.chat.completions.calls[0]["reasoning_effort"]
        == "minimal"
    )


@pytest.mark.asyncio
async def test_gemini_37_normalizes_unsupported_minimal_to_low(
) -> None:
    client = FakeGeminiClient()
    provider = GeminiProvider(
        Settings(
            gemini_model="gemini-3.7-flash",
            gemini_reasoning_effort="minimal",
        ),
        client=client,
    )

    from app.core.models import Context, Request

    await provider.generate(Request("quick response"), Context())

    assert client.chat.completions.calls[0]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_gemini_stream_also_uses_low_reasoning_effort(
) -> None:
    captured = {}

    class FakeStreamCompletions:
        async def create(
            self,
            **kwargs,
        ):
            captured.update(
                kwargs
            )

            async def iterator():
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content="x",
                                tool_calls=[],
                            ),
                            finish_reason=None,
                        )
                    ],
                    usage=None,
                    model="gemini-3.7-flash",
                )

            return iterator()

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=(
                FakeStreamCompletions()
            )
        )
    )

    provider = GeminiProvider(
        Settings(
            gemini_model=(
                "gemini-3.7-flash"
            ),
            gemini_reasoning_effort=(
                "low"
            ),
        ),
        client=client,
    )

    from app.core.models import (
        Context,
        Request,
    )

    chunks = [
        chunk
        async for chunk
        in provider.stream(
            Request("hello"),
            Context(),
        )
    ]

    assert chunks[0].text == "x"

    assert (
        captured[
            "reasoning_effort"
        ]
        == "low"
    )


def test_gemini_reasoning_effort_rejects_invalid_value(
) -> None:
    with pytest.raises(
        ValueError,
        match="gemini_reasoning_effort",
    ):
        Settings(
            gemini_reasoning_effort=(
                "maximum"
            )
        )


def test_environment_defaults_to_durable_conversation_store(
    monkeypatch,
) -> None:
    monkeypatch.delenv(
        "JARVIS_CONVERSATION_DATABASE_PATH",
        raising=False,
    )

    settings = (
        Settings.from_environment()
    )

    assert (
        settings.conversation_database_path
        == "data\\jarvis_conversations.sqlite3"
    )


def test_environment_defaults_gemini_to_minimal_reasoning(
    monkeypatch,
) -> None:
    monkeypatch.delenv(
        "JARVIS_GEMINI_REASONING_EFFORT",
        raising=False,
    )

    settings = (
        Settings.from_environment()
    )

    assert (
        settings.gemini_reasoning_effort
        == "minimal"
    )
