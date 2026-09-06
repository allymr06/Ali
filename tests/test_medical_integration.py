"""The Medical Academy's contract with the core: augment, never override."""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core import engine as engine_module
from app.core.augmentation import RequestAugmentation
from app.core.engine import (
    REQUEST_AUGMENTATION_BUDGET_FRACTION,
    REQUEST_AUGMENTATION_TIMEOUT_SECONDS,
    CoreEngine,
)
from app.core.models import Request, ToolDefinition
from app.diagnostics.models import DiagnosticLevel
from app.diagnostics.service import DiagnosticsService
from app.execution.models import ExecutionLimits
from app.medical.tutor import MEDICAL_TOOLS
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.base import ModelResponse
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor

STUDY_TURN = "Kalbin odacıklarını anlat"
AUGMENTATION_EVENTS = (
    "request.augmentation_failed",
    "request.augmentation_invalid",
    "request.augmentation_timeout",
)


class RecordingProvider(MockProvider):
    """A provider that answers instantly and keeps what Core handed it."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.tools: list[list[str] | None] = []

    async def generate(
        self, request, context, *, model=None, system_prompt=None, tools=None, **kwargs
    ) -> ModelResponse:
        self.prompts.append(system_prompt or "")
        self.tools.append(
            None if tools is None else sorted(t["function"]["name"] for t in tools)
        )
        return ModelResponse(
            text="Yanıt hazır.", model=model or "mock", provider=self.name,
            finish_reason="stop",
        )


def build_engine(*, augmenter=None, tools: tuple[str, ...] = ()) -> SimpleNamespace:
    registry, provider = ProviderRegistry(), RecordingProvider()
    registry.register(provider, make_default=True)
    executor = ToolExecutor()
    for name in tools:
        definition = ToolDefinition(name=name, description=f"{name} test tool.")
        executor.register(definition, lambda value="": f"{value}: ok")
    diagnostics, memory = DiagnosticsService(), MemoryManager(InMemoryStore())
    engine = CoreEngine(
        registry, memory, tool_executor=executor,
        request_augmenter=augmenter, diagnostics=diagnostics,
    )
    return SimpleNamespace(
        engine=engine, provider=provider, memory=memory, diagnostics=diagnostics
    )


def core_events(bundle: SimpleNamespace, name: str) -> list:
    events = bundle.diagnostics.ledger.list(component="core", limit=200)
    return [event for event in events if event.name == name]


def settings_for(**overrides) -> Settings:
    options = dict(
        default_provider="mock", default_model="mock-model",
        windows_integrations_enabled=False, memory_database_path=None,
        task_database_path=None, task_runtime_directory=None, medical_directory=None,
    )
    options.update(overrides)
    return Settings(**options)


# ---------------------------------------------------------------------------
# the augmentation contract
# ---------------------------------------------------------------------------


def test_augmentation_validates_its_inputs_and_normalises_the_tool_names() -> None:
    source = {"intent": "explain"}
    augmentation = RequestAugmentation(
        system_prompt="Ders modu.",
        allowed_tools=["medical_lookup_term", "  medical_lookup_term  ", "", "   "],
        kind="medical",
        metadata=source,
    )
    assert isinstance(augmentation.allowed_tools, frozenset)
    assert augmentation.allowed_tools == frozenset({"medical_lookup_term"})
    # The metadata is copied, so a later edit inside the domain layer
    # cannot rewrite what the finished turn reports.
    source["intent"] = "quiz"
    assert augmentation.metadata == {"intent": "explain"}

    for empty_kind in ("", "   ", None):
        with pytest.raises(ValueError):
            RequestAugmentation(kind=empty_kind)
    with pytest.raises(TypeError):
        RequestAugmentation(system_prompt=17)
    with pytest.raises(TypeError):
        RequestAugmentation(direct_response=b"cevap")

    # A kind or some metadata alone says nothing about the turn, so Core
    # reads those as no claim at all.
    assert RequestAugmentation().empty is True
    assert RequestAugmentation(kind="medical", metadata={"intent": "x"}).empty is True
    assert RequestAugmentation(system_prompt="Ders modu.").empty is False
    assert RequestAugmentation(direct_response="Cevap.").empty is False
    assert RequestAugmentation(suppress_memory=True).empty is False
    # Narrowing to no tools at all is still a claim, not the absence of one.
    assert RequestAugmentation(allowed_tools=[]).empty is False


# ---------------------------------------------------------------------------
# what an augmenter may change about a turn
# ---------------------------------------------------------------------------


def test_the_augmenter_owns_the_system_prompt_and_the_turn_kind() -> None:
    async def study(request, context) -> RequestAugmentation:
        return RequestAugmentation(
            system_prompt="Anatomi dersi: yalnızca kaynaktaki bilgiyi kullan.",
            kind="medical",
            metadata={"intent": "explain", "evidence_count": 2},
        )

    bundle = build_engine(augmenter=study, tools=("alpha",))
    response = asyncio.run(bundle.engine.handle(Request(STUDY_TURN)))
    assert bundle.provider.prompts[0].startswith("Anatomi dersi: yalnızca kaynaktaki")
    assert response.metadata["interaction_kind"] == "medical"
    assert response.metadata["augmentation"] == {"intent": "explain", "evidence_count": 2}
    assert response.metadata["augmented_tools"] is None
    # An untouched tool list still reaches the model in full.
    assert bundle.provider.tools[0] == ["alpha"]


def test_a_domain_layer_can_only_narrow_the_tools_core_exposed() -> None:
    # registered tools, what Core exposed, what the layer asked for, and
    # what the model was actually offered. A tool Core withheld ("gamma"
    # in the second row) stays withheld, and a tool nobody registered is
    # never conjured up.
    scenarios = (
        (("alpha", "beta"), None, {"beta", "gamma"}, ["beta"]),
        (("alpha", "beta", "gamma"), ["alpha", "beta"], {"beta", "gamma"}, ["beta"]),
        (("alpha", "beta"), None, {"medical_ghost"}, None),
    )
    for registered, exposed, asked, offered in scenarios:

        async def narrow(request, context, asked=asked) -> RequestAugmentation:
            return RequestAugmentation(allowed_tools=asked, kind="medical")

        bundle = build_engine(augmenter=narrow, tools=registered)
        request = Request("Kalp anatomisini anlat")
        if exposed is not None:
            request.metadata["allowed_tools"] = exposed
        response = asyncio.run(bundle.engine.handle(request))
        assert bundle.provider.tools[0] == offered
        assert response.metadata["augmented_tools"] == sorted(asked)


def test_a_claim_that_only_narrows_tools_or_hushes_memory_still_names_the_turn() -> None:
    # Neither claim touches the prompt or the answer, yet both mean a
    # domain layer owned the turn, so both must say so in the ledger.
    async def narrow(request, context) -> RequestAugmentation:
        return RequestAugmentation(
            allowed_tools=["alpha"], kind="medical", metadata={"intent": "quiz"}
        )

    async def hush(request, context) -> RequestAugmentation:
        return RequestAugmentation(
            suppress_memory=True, kind="medical", metadata={"intent": "review"}
        )

    for augmenter, intent, offered, narrowed in (
        (narrow, "quiz", ["alpha"], ["alpha"]),
        (hush, "review", ["alpha", "beta"], None),
    ):
        bundle = build_engine(augmenter=augmenter, tools=("alpha", "beta"))
        response = asyncio.run(bundle.engine.handle(Request(STUDY_TURN)))
        assert response.metadata["interaction_kind"] == "medical"
        assert response.metadata["augmentation"] == {"intent": intent}
        assert response.metadata["augmented_tools"] == narrowed
        # Naming the turn is all it does: the model still answers it, with
        # Core's own prompt and the tools the claim left standing.
        assert response.text == "Yanıt hazır."
        assert response.metadata["outcome"] == "completed"
        assert response.metadata["tools_suppressed"] is False
        assert bundle.provider.tools[0] == offered
        assert bundle.provider.prompts[0] != ""


def test_a_direct_response_answers_the_turn_without_the_model() -> None:
    async def answer(request, context) -> RequestAugmentation:
        return RequestAugmentation(
            direct_response="Zayıf kavram listen henüz boş.",
            kind="medical",
            suppress_memory=True,
            metadata={"intent": "review"},
        )

    bundle = build_engine(augmenter=answer, tools=("alpha",))
    response = asyncio.run(bundle.engine.handle(Request("Zayıf konularımı tekrar et")))
    assert bundle.provider.prompts == []
    assert response.text == "Zayıf kavram listen henüz boş."
    assert response.metadata["outcome"] == "completed"
    assert response.metadata["provider"] == "core"
    assert response.metadata["interaction_kind"] == "medical"
    assert response.metadata["tools_suppressed"] is True
    assert response.metadata["augmentation"] == {"intent": "review"}


def test_suppressed_memory_is_not_written_and_the_turn_says_why() -> None:
    async def claim(request, context) -> RequestAugmentation:
        return RequestAugmentation(
            system_prompt="Tıp çalışma modu.", kind="medical", suppress_memory=True
        )

    worth_remembering = "HATIRLA: Anatomi dersine çalışıyorum"
    bundle = build_engine(augmenter=claim)
    response = asyncio.run(bundle.engine.handle(Request(worth_remembering)))
    assert response.metadata["memory_decision"] is True
    assert response.metadata["memory_saved"] is False
    assert "not written to personal memory" in response.metadata["memory_write_reason"]
    assert bundle.memory.count() == 0

    # Without the claim the very same turn does write the memory.
    plain = build_engine()
    control = asyncio.run(plain.engine.handle(Request(worth_remembering)))
    assert control.metadata["memory_saved"] is True
    assert control.metadata["memory_write_reason"] is None
    assert plain.memory.count() == 1


# ---------------------------------------------------------------------------
# a broken domain layer is an inconvenience, never an outage
# ---------------------------------------------------------------------------


def test_a_failing_or_nonsense_augmenter_leaves_the_turn_intact() -> None:
    async def explode(request, context):
        raise RuntimeError("study store is gone")

    async def wrong_type(request, context):
        return {"system_prompt": "Ders modu."}

    async def claims_nothing(request, context) -> RequestAugmentation:
        return RequestAugmentation(kind="medical", metadata={"intent": "none"})

    # An empty augmentation is no failure, so it is dropped in silence; the
    # other two are reported. None of them may touch the turn itself.
    for augmenter, reported, attributes in (
        (explode, ["request.augmentation_failed"], {"error_type": "RuntimeError"}),
        (wrong_type, ["request.augmentation_invalid"], {}),
        (claims_nothing, [], {}),
    ):
        bundle = build_engine(augmenter=augmenter, tools=("alpha",))
        response = asyncio.run(bundle.engine.handle(Request(STUDY_TURN)))
        assert response.text == "Yanıt hazır."
        assert response.metadata["outcome"] == "completed"
        assert response.metadata["interaction_kind"] == "general"
        assert response.metadata["augmentation"] is None
        assert response.metadata["augmented_tools"] is None
        assert bundle.provider.tools[0] == ["alpha"]
        assert [n for n in AUGMENTATION_EVENTS if core_events(bundle, n)] == reported
        for name in reported:
            event = core_events(bundle, name)[0]
            assert event.level is DiagnosticLevel.WARNING
            assert {key: event.attributes.get(key) for key in attributes} == attributes


def test_an_augmenter_that_hangs_costs_a_moment_not_the_whole_turn(monkeypatch) -> None:
    async def hang(request, context):
        await asyncio.Event().wait()
        return RequestAugmentation(system_prompt="Ders modu.", kind="medical")

    # The bound is shrunk so the hang is not paid for in real seconds; what
    # the test measures is the wait Core computed, not this value.
    monkeypatch.setattr(engine_module, "REQUEST_AUGMENTATION_TIMEOUT_SECONDS", 0.05)
    budget = 8.0
    bundle = build_engine(augmenter=hang)
    request = Request(STUDY_TURN)
    response = asyncio.run(
        bundle.engine.handle(request, limits=ExecutionLimits(timeout_seconds=budget))
    )

    # Half one: the waiting augmenter is abandoned at its own small bound,
    # far below the budget of the turn it was asked about.
    timed_out = core_events(bundle, "request.augmentation_timeout")
    assert len(timed_out) == 1
    assert timed_out[0].level is DiagnosticLevel.WARNING
    waited = timed_out[0].attributes["timeout_seconds"]
    assert waited == pytest.approx(0.05)
    assert waited <= budget * REQUEST_AUGMENTATION_BUDGET_FRACTION
    assert response.metadata["elapsed_seconds"] < budget * REQUEST_AUGMENTATION_BUDGET_FRACTION

    # Half two: the turn is still answered by the model, and none of the
    # abandoned claim reached it.
    assert response.request_id == request.request_id
    assert response.text == "Yanıt hazır."
    assert response.metadata["outcome"] == "completed"
    assert response.metadata["interaction_kind"] == "general"
    assert response.metadata["augmentation"] is None
    assert response.metadata["augmented_tools"] is None
    assert not any("Ders modu." in prompt for prompt in bundle.provider.prompts)


def test_the_augmentation_wait_is_a_small_slice_of_the_request_budget(monkeypatch) -> None:
    waits: list[float] = []
    original = CoreEngine._await_provider

    async def record(awaitable, *, cancel_event, timeout):
        waits.append(timeout)
        return await original(awaitable, cancel_event=cancel_event, timeout=timeout)

    monkeypatch.setattr(CoreEngine, "_await_provider", staticmethod(record))

    async def study(request, context) -> RequestAugmentation:
        return RequestAugmentation(system_prompt="Ders modu.", kind="medical")

    # A generous budget is capped by the constant; a tight one by the
    # fraction. Either way the domain layer never gets the whole turn.
    budgets = (
        (600.0, REQUEST_AUGMENTATION_TIMEOUT_SECONDS),
        (2.0, 2.0 * REQUEST_AUGMENTATION_BUDGET_FRACTION),
    )
    for budget, expected in budgets:
        waits.clear()
        bundle = build_engine(augmenter=study)
        response = asyncio.run(
            bundle.engine.handle(
                Request(STUDY_TURN), limits=ExecutionLimits(timeout_seconds=budget)
            )
        )
        # The first wait of the turn is the domain layer's; the model call
        # that follows still holds what is left of the budget.
        augmentation_wait, model_wait = waits[0], waits[1]
        assert augmentation_wait == pytest.approx(expected, rel=0.05)
        assert augmentation_wait <= budget * REQUEST_AUGMENTATION_BUDGET_FRACTION
        assert model_wait > augmentation_wait
        assert response.metadata["interaction_kind"] == "medical"


def test_identity_clock_and_social_turns_never_reach_the_domain_layer() -> None:
    seen: list[str] = []

    async def greedy(request, context) -> RequestAugmentation:
        seen.append(request.text)
        return RequestAugmentation(direct_response="Tıp cevabı.", kind="medical")

    bundle = build_engine(augmenter=greedy)
    kinds = []
    for text in ("Sen kimsin?", "Saat kaç?", "Merhaba"):
        response = asyncio.run(bundle.engine.handle(Request(text)))
        assert response.text != "Tıp cevabı."
        kinds.append(response.metadata["interaction_kind"])
    assert kinds == ["identity", "clock", "social"]
    assert seen == []

    # Everything else is offered to the domain layer.
    response = asyncio.run(bundle.engine.handle(Request(STUDY_TURN)))
    assert seen == [STUDY_TURN]
    assert response.text == "Tıp cevabı."


# ---------------------------------------------------------------------------
# settings and bootstrap wiring
# ---------------------------------------------------------------------------


def test_medical_settings_read_their_environment_variables(monkeypatch, tmp_path):
    environment = (
        ("JARVIS_MEDICAL_ENABLED", "false"),
        ("JARVIS_MEDICAL_DIRECTORY", str(tmp_path / "med")),
        ("JARVIS_MEDICAL_MODEL", "  gemini-study  "),
        ("JARVIS_MEDICAL_MAX_DOCUMENT_PAGES", "12"),
        ("JARVIS_MEDICAL_MAX_DOCUMENT_BYTES", str(4 * 1024 * 1024)),
        ("JARVIS_MEDICAL_VISION_PAGES_PER_DOCUMENT", "0"),
    )
    for variable, value in environment:
        monkeypatch.setenv(variable, value)
    settings = Settings.from_environment()
    assert settings.medical_enabled is False
    assert settings.medical_directory == str(tmp_path / "med")
    assert settings.medical_model == "gemini-study"
    assert settings.medical_max_document_pages == 12
    assert settings.medical_max_document_bytes == 4 * 1024 * 1024
    assert settings.medical_vision_pages_per_document == 0

    for variable, _ in environment:
        monkeypatch.delenv(variable)
    defaults = Settings.from_environment()
    assert defaults.medical_enabled is True
    assert defaults.medical_directory.endswith("medical")
    assert defaults.medical_model == ""
    assert defaults.medical_max_document_pages == 400
    assert defaults.medical_max_document_bytes == 60 * 1024 * 1024
    assert defaults.medical_vision_pages_per_document == 12


def test_every_documented_medical_bound_is_enforced() -> None:
    legal = (
        ("medical_max_document_pages", (1, 5_000)),
        ("medical_max_document_bytes", (1024 * 1024, 512 * 1024 * 1024)),
        ("medical_vision_pages_per_document", (0, 200)),
    )
    illegal = (
        ("medical_directory", ("", "   ")),
        ("medical_max_document_pages", (0, 5_001)),
        ("medical_max_document_bytes", (1024 * 1024 - 1, 512 * 1024 * 1024 + 1)),
        ("medical_vision_pages_per_document", (-1, 201)),
    )
    for field, values in legal:
        for value in values:
            settings_for(**{field: value})
    for field, values in illegal:
        for value in values:
            with pytest.raises(ValueError):
                settings_for(**{field: value})


def test_bootstrap_gives_the_academy_its_tools_and_the_engine_its_augmenter() -> None:
    application = create_application(settings_for())
    try:
        assert application.medical is not None
        assert MEDICAL_TOOLS <= set(application.tool_executor.list_names())
        # The academy's own bound method is the engine's augmenter.
        augmenter = application.engine.request_augmenter
        assert getattr(augmenter, "__self__", None) is application.medical
        # No directory was configured, so the study store stays in memory.
        assert application.medical.available()["persistent"] is False
    finally:
        application.close()
    # Closing the application closed the study store with it.
    with pytest.raises(sqlite3.ProgrammingError):
        application.medical.store.summary()


def test_the_academy_can_be_switched_off_completely() -> None:
    application = create_application(settings_for(medical_enabled=False))
    try:
        assert application.medical is None
        assert application.engine.request_augmenter is None
        names = application.tool_executor.list_names()
        assert not [name for name in names if name.startswith("medical_")]
        with pytest.raises(TypeError):
            application.engine.request_augmenter = "app.medical"
    finally:
        application.close()
