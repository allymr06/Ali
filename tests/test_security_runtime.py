from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent.approval import ApprovalStatus, ApprovalStore
from app.agent.approval_gate import ApprovalGate, ApprovalGateResult
from app.agent.loop import AgentLoop
from app.agent.models import AgentMode, AgentStatus
from app.core.models import (
    Request,
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.core.time import utc_now
from app.main import JARVISApplication, create_application
from app.planning.models import PlanStep
from app.security.approval import (
    ApprovalExecutionContext,
    ApprovalGrant,
    approval_binding_digest,
    validate_approval_grant,
)
from app.security.permissions import (
    ParameterPermissionRule,
    PermissionDecision,
    PermissionEngine,
    PermissionPolicy,
    PermissionScope,
)
from app.tools.executor import ToolExecutor
from tests.security_helpers import bound_approval


def test_permission_policy_requires_complete_non_overlapping_classification() -> None:
    with pytest.raises(ValueError):
        PermissionPolicy(
            auto_allow=frozenset({RiskLevel.READ_ONLY}),
            require_confirmation=frozenset({RiskLevel.MEDIUM}),
            deny=frozenset({RiskLevel.CRITICAL}),
        )

    with pytest.raises(ValueError):
        PermissionPolicy(
            auto_allow=frozenset({RiskLevel.READ_ONLY, RiskLevel.LOW}),
            require_confirmation=frozenset(
                {RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH}
            ),
            deny=frozenset({RiskLevel.CRITICAL}),
        )


def test_permission_scope_enforces_tool_allowlist_denylist_and_risk_ceiling() -> None:
    engine = PermissionEngine()
    low = ToolDefinition(name="low", description="Low", risk_level=RiskLevel.LOW)
    high = ToolDefinition(
        name="high",
        description="High",
        risk_level=RiskLevel.HIGH,
    )

    outside = engine.evaluate(
        low,
        scope=PermissionScope(allowed_tools=frozenset({"other"})),
    )
    denied = engine.evaluate(
        low,
        scope=PermissionScope(denied_tools=frozenset({"low"})),
    )
    excessive = engine.evaluate(
        high,
        scope=PermissionScope(max_risk_level=RiskLevel.MEDIUM),
    )

    assert outside.denied and "outside" in outside.reason
    assert denied.denied and "denied" in denied.reason
    assert excessive.denied and "exceeds" in excessive.reason


def test_parameter_rule_can_force_denial_but_never_force_allow() -> None:
    with pytest.raises(ValueError):
        ParameterPermissionRule(
            name="unsafe-allow",
            risk_level=RiskLevel.READ_ONLY,
            matches=lambda _: True,
            decision=PermissionDecision.ALLOW,
        )

    engine = PermissionEngine()
    engine.register_parameter_rule(
        "file",
        ParameterPermissionRule(
            name="protected-path",
            risk_level=RiskLevel.CRITICAL,
            matches=lambda values: values.get("path") == "system",
            decision=PermissionDecision.DENY,
            reason="Protected path.",
        ),
    )
    result = engine.evaluate(
        ToolDefinition(name="file", description="File"),
        parameters={"path": "system"},
    )

    assert result.denied
    assert result.reason == "Protected path."
    assert result.matched_rules == ("protected-path",)


def test_permission_rule_lifecycle_revision_and_bounded_audit() -> None:
    engine = PermissionEngine(audit_capacity=2)
    rule = ParameterPermissionRule(
        name="confirm-large",
        risk_level=RiskLevel.HIGH,
        matches=lambda values: bool(values.get("large")),
    )
    engine.register_parameter_rule("copy", rule)
    assert engine.revision == 1
    assert engine.list_parameter_rules("copy") == (rule,)
    with pytest.raises(ValueError):
        engine.register_parameter_rule("copy", rule)

    tool = ToolDefinition(name="copy", description="Copy")
    engine.evaluate(tool, parameters={"large": False})
    engine.evaluate(tool, parameters={"large": True})
    final = engine.evaluate(tool, parameters={"large": False})

    audit = engine.audit_log()
    assert len(audit) == 2
    assert audit[-1].evaluation_id == final.evaluation_id
    assert audit[-1].evaluated_at.tzinfo is not None
    assert audit[-1].policy_revision == 1

    assert engine.unregister_parameter_rule("copy", "confirm-large") is rule
    assert engine.revision == 2


def test_approval_grant_validation_binds_every_execution_identity() -> None:
    task_id = uuid4()
    plan_id = uuid4()
    step_id = uuid4()
    request_id = uuid4()
    conversation_id = uuid4()
    operation_id = uuid4()
    parameters = {"path": "a.txt"}
    context = ApprovalExecutionContext(
        task_id,
        plan_id,
        step_id,
        conversation_id=conversation_id,
        request_id=request_id,
        approval_operation_id=operation_id,
    )
    grant = ApprovalGrant(
        operation_id=operation_id,
        binding_digest=approval_binding_digest(
            operation="delete",
            tool_name="file",
            parameters=parameters,
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_id,
            tool_version="1.0.0",
            request_id=request_id,
            conversation_id=conversation_id,
        ),
        expires_at=utc_now() + timedelta(minutes=1),
        task_id=task_id,
    )

    assert validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters=parameters,
        context=context,
        tool_version="1.0.0",
    ).valid
    assert not validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters={"path": "b.txt"},
        context=context,
        tool_version="1.0.0",
    ).valid
    assert not validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters=parameters,
        context=ApprovalExecutionContext(task_id, plan_id, uuid4()),
        tool_version="1.0.0",
    ).valid
    assert not validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters=parameters,
        context=context,
        tool_version="2.0.0",
    ).valid
    assert not validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters=parameters,
        context=ApprovalExecutionContext(
            task_id,
            plan_id,
            step_id,
            conversation_id=uuid4(),
            request_id=request_id,
            approval_operation_id=operation_id,
        ),
        tool_version="1.0.0",
    ).valid
    assert not validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters=parameters,
        context=ApprovalExecutionContext(
            task_id,
            plan_id,
            step_id,
            conversation_id=conversation_id,
            request_id=uuid4(),
            approval_operation_id=operation_id,
        ),
        tool_version="1.0.0",
    ).valid


def test_expired_or_naive_approval_grants_fail_closed() -> None:
    context = ApprovalExecutionContext(None, None, uuid4())
    digest = approval_binding_digest(
        operation="write",
        tool_name="file",
        parameters={},
        task_id=None,
        plan_id=None,
        step_id=context.step_id,
    )
    expired = ApprovalGrant(
        uuid4(),
        digest,
        utc_now() - timedelta(seconds=1),
        None,
    )
    naive = ApprovalGrant(
        uuid4(),
        digest,
        (utc_now() + timedelta(seconds=1)).replace(tzinfo=None),
        None,
    )

    for grant in (expired, naive):
        validation = validate_approval_grant(
            grant,
            operation="write",
            tool_name="file",
            parameters={},
            context=context,
        )
        assert validation.valid is False


def test_unbound_confirmation_boolean_cannot_authorize_execution() -> None:
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="dangerous",
            description="Dangerous",
            risk_level=RiskLevel.HIGH,
        ),
        lambda: "executed",
    )

    result = executor.execute("dangerous", confirmation_granted=True)

    assert result.status is ToolExecutionStatus.BLOCKED
    assert "unbound" in (result.error or "").lower()


def test_executor_accepts_exact_bound_grant_and_rejects_changed_parameters() -> None:
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="write",
            description="Write",
            risk_level=RiskLevel.MEDIUM,
        ),
        lambda path: path,
    )
    authorization = bound_approval("write", parameters={"path": "a.txt"})

    accepted = executor.execute(
        "write",
        parameters={"path": "a.txt"},
        **authorization,
    )
    rejected = executor.execute(
        "write",
        parameters={"path": "b.txt"},
        **authorization,
    )

    assert accepted.status is ToolExecutionStatus.SUCCESS
    assert rejected.status is ToolExecutionStatus.BLOCKED


def test_agent_gate_uses_real_tool_contract_and_cannot_be_downgraded() -> None:
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(
            name="send",
            description="Send",
            risk_level=RiskLevel.HIGH,
        ),
        lambda: "sent",
    )
    gate = ApprovalGate(tool_executor=tools)
    step = PlanStep(
        "send",
        metadata={
            "tool_name": "send",
            "risk_level": "read_only",
            "requires_approval": False,
        },
    )

    decision = gate.evaluate(step=step, plan_id=uuid4())

    assert decision.result is ApprovalGateResult.PENDING
    assert decision.request is not None
    assert decision.request.risk_level == RiskLevel.HIGH.value


def test_agent_gate_denies_critical_tool_without_creating_approval() -> None:
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(
            name="critical",
            description="Critical",
            risk_level=RiskLevel.CRITICAL,
        ),
        lambda: "never",
    )
    gate = ApprovalGate(tool_executor=tools)

    decision = gate.evaluate(
        step=PlanStep("critical", metadata={"tool_name": "critical"})
    )

    assert decision.result is ApprovalGateResult.DENIED
    assert decision.request is None
    assert gate.store.list() == []


@pytest.mark.asyncio
async def test_agent_bound_approval_reaches_final_tool_boundary() -> None:
    application = create_application()
    application.tool_executor.register(
        ToolDefinition(
            name="send",
            description="Send",
            risk_level=RiskLevel.HIGH,
        ),
        lambda: ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name="send",
            data="sent",
            verified=True,
        ),
    )
    plan = application.engine.create_plan(
        "send",
        [PlanStep("send", metadata={"tool_name": "send", "parameters": {}})],
    )
    loop = AgentLoop(
        engine=application.engine,
        plan_builder=lambda request, context: plan,
    )

    waiting = await loop.run(Request("send"), mode=AgentMode.TASK)
    operation_id = plan.steps[0].metadata["approval_operation_id"]
    loop.approve(operation_id)
    completed = await loop.run(Request("send"), mode=AgentMode.TASK)

    assert waiting.status is AgentStatus.WAITING_FOR_APPROVAL
    assert completed.status is AgentStatus.COMPLETED


def test_approval_requests_are_immutable_and_store_transition_is_atomic() -> None:
    store = ApprovalStore()
    request = store.create(
        operation="send",
        reason="Send message",
        risk_level="high",
    )
    with pytest.raises(FrozenInstanceError):
        request.status = ApprovalStatus.APPROVED  # type: ignore[misc]

    def approve() -> str:
        try:
            return store.approve(request.operation_id).status.value
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: approve(), range(8)))

    assert outcomes.count(ApprovalStatus.APPROVED.value) == 1
    assert outcomes.count("rejected") == 7


def test_approval_store_expires_stale_requests_with_injected_clock() -> None:
    now = [utc_now()]
    store = ApprovalStore(clock=lambda: now[0])
    request = store.create(
        operation="write",
        reason="Write file",
        risk_level="medium",
        expires_in_seconds=1,
    )
    now[0] += timedelta(seconds=2)

    expired = store.expire_stale()

    assert expired[0].operation_id == request.operation_id
    assert expired[0].status is ApprovalStatus.EXPIRED
    assert store.get(request.operation_id).status is ApprovalStatus.EXPIRED


def test_a_rejected_call_gives_the_one_time_approval_back() -> None:
    """A capability is spent by the action, not by the attempt.

    Argument binding and the concurrency gate both run after the
    permission check. When they rejected a call the grant had already
    been consumed, so the user's single approval was gone even though
    nothing ran, and the retry reported "Approval replay blocked."
    instead of the real reason.
    """
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="write",
            description="Write",
            risk_level=RiskLevel.MEDIUM,
        ),
        lambda path: path,
    )
    authorization = bound_approval("write", parameters={"gecersiz": 1})

    first = executor.execute("write", parameters={"gecersiz": 1}, **authorization)
    second = executor.execute("write", parameters={"gecersiz": 1}, **authorization)

    assert first.status is ToolExecutionStatus.FAILED
    assert "path" in (first.error or "")
    assert second.status is ToolExecutionStatus.FAILED, (
        "the handler never ran, so the approval was never spent"
    )
    assert "path" in (second.error or ""), (
        "the caller keeps seeing the real fault, not a replay block masking it"
    )


def test_a_busy_concurrency_slot_does_not_spend_the_approval() -> None:
    """The slot frees, the same grant still works, a true replay still fails."""
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="convert",
            description="Convert",
            risk_level=RiskLevel.MEDIUM,
            max_concurrency=1,
        ),
        lambda path: path,
    )
    definition = executor.get("convert").definition
    authorization = bound_approval("convert", parameters={"path": "a.pdf"})

    assert executor._try_acquire_execution_slot(definition) is True
    blocked = executor.execute("convert", parameters={"path": "a.pdf"}, **authorization)
    executor._release_execution_slot(definition)
    retried = executor.execute("convert", parameters={"path": "a.pdf"}, **authorization)
    replayed = executor.execute("convert", parameters={"path": "a.pdf"}, **authorization)

    assert blocked.status is ToolExecutionStatus.BLOCKED
    assert "concurrent" in (blocked.error or "").lower()
    assert retried.status is ToolExecutionStatus.SUCCESS, (
        "a call the executor turned away must not cost the user their approval"
    )
    assert retried.data == "a.pdf"
    assert replayed.status is ToolExecutionStatus.BLOCKED, (
        "once the tool has actually run, the capability is spent for good"
    )
    assert replayed.error == "Approval replay blocked."


@pytest.mark.asyncio
async def test_the_async_path_returns_the_approval_the_same_way() -> None:
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="convert_async",
            description="Convert",
            risk_level=RiskLevel.MEDIUM,
            max_concurrency=1,
        ),
        lambda path: path,
    )
    definition = executor.get("convert_async").definition
    authorization = bound_approval("convert_async", parameters={"path": "b.pdf"})

    assert executor._try_acquire_execution_slot(definition) is True
    blocked = await executor.execute(
        "convert_async", parameters={"path": "b.pdf"}, **authorization
    )
    executor._release_execution_slot(definition)
    retried = await executor.execute(
        "convert_async", parameters={"path": "b.pdf"}, **authorization
    )

    assert blocked.status is ToolExecutionStatus.BLOCKED
    assert retried.status is ToolExecutionStatus.SUCCESS
    assert retried.data == "b.pdf"


def _overlap_counting_handler(
    ledger: dict[str, int],
    hold: threading.Event,
):
    """A handler that reports how many copies of itself ran at once."""
    lock = threading.Lock()

    def handler() -> str:
        with lock:
            ledger["live"] += 1
            ledger["peak"] = max(ledger["peak"], ledger["live"])
        try:
            hold.wait(10.0)
        finally:
            with lock:
                ledger["live"] -= 1
        return "done"

    return handler


def test_a_timed_out_worker_keeps_the_slot_until_it_actually_stops() -> None:
    """The slot follows the work, not the wait.

    Abandoning a handler on timeout does not stop the thread running it.
    A tool that declares one writer - every confirming filesystem tool
    does - must not get a second one while the first is still inside the
    handler, and must not stay wedged once it leaves.
    """
    executor = ToolExecutor()
    ledger = {"live": 0, "peak": 0}
    hold = threading.Event()
    executor.register(
        ToolDefinition(
            name="one_writer",
            description="One writer",
            timeout_seconds=0.05,
            max_concurrency=1,
        ),
        _overlap_counting_handler(ledger, hold),
    )

    abandoned = executor.execute("one_writer")
    intruder = executor.execute("one_writer")
    hold.set()
    deadline = time.monotonic() + 5.0
    while (
        executor._active_execution_counts.get("one_writer")
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    hold.clear()
    after_worker_finished = executor.execute("one_writer")
    hold.set()

    assert abandoned.status is ToolExecutionStatus.TIMEOUT
    assert intruder.status is ToolExecutionStatus.BLOCKED, (
        "the abandoned handler is still running, so its slot is still taken"
    )
    assert ledger["peak"] == 1, "two writers were inside the tool at once"
    assert after_worker_finished.status is ToolExecutionStatus.TIMEOUT, (
        "the slot must come back when the work ends, or the tool is wedged"
    )


@pytest.mark.asyncio
async def test_the_async_path_holds_the_slot_for_the_thread_too() -> None:
    """Cancelling the await does not reach into the thread."""
    executor = ToolExecutor()
    ledger = {"live": 0, "peak": 0}
    hold = threading.Event()
    executor.register(
        ToolDefinition(
            name="one_writer_async",
            description="One writer",
            timeout_seconds=0.05,
            max_concurrency=1,
        ),
        _overlap_counting_handler(ledger, hold),
    )

    abandoned = await executor.execute("one_writer_async")
    intruder = await executor.execute("one_writer_async")
    hold.set()
    deadline = time.monotonic() + 5.0
    while (
        executor._active_execution_counts.get("one_writer_async")
        and time.monotonic() < deadline
    ):
        await asyncio.sleep(0.01)
    hold.clear()
    after_worker_finished = await executor.execute("one_writer_async")
    hold.set()

    assert abandoned.status is ToolExecutionStatus.TIMEOUT
    assert intruder.status is ToolExecutionStatus.BLOCKED, (
        "the thread runs on after the timeout, so its slot is still taken"
    )
    assert ledger["peak"] == 1, "two writers were inside the tool at once"
    assert after_worker_finished.status is ToolExecutionStatus.TIMEOUT, (
        "the slot must come back when the work ends, or the tool is wedged"
    )


@pytest.mark.asyncio
async def test_a_slot_outlives_a_work_item_cancelled_before_it_ever_ran() -> None:
    """The slot follows the work item, and the work item may never start.

    A busy worker pool leaves a submitted handler queued. The wait times
    out, the queued item is cancelled, and the handler never runs - so
    nothing inside the handler could ever hand the slot back. Binding the
    slot to the work item instead covers both hazards with one rule: it
    comes back when the item ends, and never while the handler is still
    inside the tool.
    """
    executor = ToolExecutor()
    ledger = {"live": 0, "peak": 0}
    hold = threading.Event()
    executor.register(
        ToolDefinition(
            name="queued_writer",
            description="One writer",
            timeout_seconds=0.05,
            max_concurrency=1,
        ),
        _overlap_counting_handler(ledger, hold),
    )
    # Starve every pool a thread-bound handler could land in - the loop's
    # default executor and the one the executor owns - so the submitted
    # work item can only sit in a queue and never start.
    occupied = threading.Event()
    running = threading.Semaphore(0)

    def squat() -> None:
        running.release()
        occupied.wait(10.0)

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    executor._worker_pool = ThreadPoolExecutor(max_workers=1)
    # run_in_executor submits there and then, so both workers are busy
    # before this coroutine ever yields.
    squatters = [
        loop.run_in_executor(None, squat),
        asyncio.wrap_future(executor._worker_pool.submit(squat)),
    ]

    for _ in squatters:
        assert running.acquire(timeout=5.0), "the pools never got busy"

    try:
        never_ran = await executor.execute("queued_writer")

        assert never_ran.status is ToolExecutionStatus.TIMEOUT
        assert ledger["peak"] == 0, "the queued handler never started"
        assert not executor._active_execution_counts, (
            "a slot that never reached the handler must still come back, "
            "or one timeout removes the tool for the life of the process"
        )
        assert never_ran.side_effects_may_continue is False, (
            "a work item cancelled before it ran left nothing behind"
        )

        occupied.set()
        await asyncio.gather(*squatters)

        abandoned = await executor.execute("queued_writer")
        intruder = await executor.execute("queued_writer")

        assert abandoned.status is ToolExecutionStatus.TIMEOUT
        assert intruder.status is ToolExecutionStatus.BLOCKED, (
            "this handler really is inside the tool, so it keeps its slot"
        )
        assert ledger["peak"] == 1, "two writers were inside the tool at once"
    finally:
        occupied.set()
        hold.set()
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_the_executor_owns_one_worker_pool_and_gives_its_threads_back(
) -> None:
    """Thread-bound handlers run on our pool, and it closes cleanly."""
    executor = ToolExecutor()
    workers: list[threading.Thread] = []

    def note() -> str:
        workers.append(threading.current_thread())
        return "ok"

    executor.register(
        ToolDefinition(name="note", description="Note"),
        note,
    )

    assert executor._worker_pool is None, "no thread exists until one is needed"

    first = await executor.execute("note")
    pool = executor._worker_pool
    second = await executor.execute("note")

    assert first.status is ToolExecutionStatus.SUCCESS
    assert second.status is ToolExecutionStatus.SUCCESS
    assert executor._worker_pool is pool, (
        "a fresh pool per call would spend a thread on every tool call"
    )
    assert workers and threading.current_thread() not in workers, (
        "a thread-bound handler must not run on the event loop's thread"
    )

    executor.shutdown(wait=True)

    assert executor._worker_pool is None
    assert not any(worker.is_alive() for worker in workers), (
        "shutdown joins the workers rather than orphaning them"
    )


def _starve_the_worker_pool(
    executor: ToolExecutor,
) -> tuple[threading.Event, "asyncio.Future[None]"]:
    """Fill the executor's own pool so the next submit can only queue.

    The squatter deliberately runs on an unnamed pool, so a test that
    counts surviving ``jarvis-tool`` threads never mistakes it for one.
    """
    occupied = threading.Event()
    running = threading.Semaphore(0)

    def squat() -> None:
        running.release()
        occupied.wait(10.0)

    executor._worker_pool = ThreadPoolExecutor(max_workers=1)
    squatter = asyncio.wrap_future(executor._worker_pool.submit(squat))

    assert running.acquire(timeout=5.0), "the pool never got busy"

    return occupied, squatter


async def _threads_have_ended(
    workers: list[threading.Thread],
    *,
    timeout: float = 5.0,
) -> bool:
    """Wait briefly for pool threads to notice their pool is gone."""
    deadline = time.monotonic() + timeout

    while (
        any(worker.is_alive() for worker in workers)
        and time.monotonic() < deadline
    ):
        await asyncio.sleep(0.01)

    return not any(worker.is_alive() for worker in workers)


@pytest.mark.asyncio
async def test_a_shutdown_refuses_the_turn_instead_of_faking_a_cancellation(
) -> None:
    """Every caller is owed a ToolResult, teardown included.

    ``pool.shutdown(cancel_futures=True)`` cancels a queued work item,
    the cancellation rides ``asyncio.wrap_future`` back into the wait,
    and reading the result there raises CancelledError - a
    BaseException that no ``except Exception`` between the executor and
    the turn would catch. The user would be told they cancelled an
    action they never touched, and replace_application closes the old
    runtime while its loop is still running, so this is reachable.
    """
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="queued_note",
            description="Note",
            timeout_seconds=5.0,
        ),
        lambda: "done",
    )
    occupied, squatter = _starve_the_worker_pool(executor)

    try:
        turn = asyncio.ensure_future(executor.execute("queued_note"))
        # Enough turns of the loop for the work item to be submitted and
        # the wait to be entered before the pool is pulled out from
        # under it.
        for _ in range(5):
            await asyncio.sleep(0)

        executor.shutdown()

        refusal = await turn

        assert refusal.status is ToolExecutionStatus.BLOCKED
        assert refusal.error == "Tool executor is shutting down."
        assert refusal.side_effects_may_continue is False, (
            "a work item cancelled before it ran left nothing behind"
        )
        assert not executor._active_execution_counts, (
            "the cancelled work item hands its slot back"
        )
    finally:
        occupied.set()
        await squatter
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_an_approval_survives_a_timeout_that_never_ran_the_tool() -> None:
    """A capability buys an action, and this action provably never ran.

    A confirming tool whose work item sat in a full pool until the
    timeout is the one interrupted case the executor can prove nothing
    happened in. Keeping the one-time grant would charge the user for
    an action nobody took, and the retry would then be refused as a
    replay of it.
    """
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="queued_writer",
            description="Writer",
            risk_level=RiskLevel.MEDIUM,
            timeout_seconds=0.05,
        ),
        lambda: "written",
    )
    occupied, squatter = _starve_the_worker_pool(executor)
    approval = bound_approval("queued_writer")
    grant = approval["approval_grant"]

    try:
        never_ran = await executor.execute("queued_writer", **approval)

        assert never_ran.status is ToolExecutionStatus.TIMEOUT
        assert never_ran.side_effects_may_continue is False
        assert executor.approval_grant_is_spent(grant) is False, (
            "an approval that bought nothing is still the user's to spend"
        )

        occupied.set()
        await squatter

        retried = await executor.execute("queued_writer", **approval)

        assert retried.status is ToolExecutionStatus.SUCCESS, (
            "the same grant must still buy the action it was given for"
        )
    finally:
        occupied.set()
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_shutdown_without_waiting_still_ends_the_worker_threads(
) -> None:
    """Both production call sites take the default, so pin the default.

    ``wait=False`` returns before the threads are gone, so the promise
    it can keep is narrower: the pool is really closed - not merely
    dropped, which would leave a handler still inside it free to queue
    more work - its idle workers are on their way out, and the next
    execution builds a fresh one rather than inheriting a dead pool.
    """
    executor = ToolExecutor()
    workers: list[threading.Thread] = []

    def note() -> str:
        workers.append(threading.current_thread())
        return "ok"

    executor.register(
        ToolDefinition(name="note", description="Note"),
        note,
    )
    first = await executor.execute("note")
    pool = executor._worker_pool

    assert first.status is ToolExecutionStatus.SUCCESS
    assert workers and all(
        worker.name.startswith("jarvis-tool") for worker in workers
    ), "tool work must be identifiable in a thread dump"

    executor.shutdown()

    assert executor._worker_pool is None

    with pytest.raises(RuntimeError):
        pool.submit(note)

    assert await _threads_have_ended(workers), (
        "an idle worker must end with its pool, not outlive the runtime"
    )

    revived = await executor.execute("note")

    assert revived.status is ToolExecutionStatus.SUCCESS, (
        "a later execution builds a fresh pool"
    )

    executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_closing_the_application_gives_the_tool_threads_back() -> None:
    """The runtime that owns the executor is the one that must end it.

    ``JARVISApplication.close`` is the only production caller, and
    replace_application runs it on every settings change. Drop the
    wiring and each swap leaves another pool behind.
    """
    executor = ToolExecutor()
    workers: list[threading.Thread] = []

    def note() -> str:
        workers.append(threading.current_thread())
        return "ok"

    executor.register(
        ToolDefinition(name="note", description="Note"),
        note,
    )

    assert (await executor.execute("note")).status is (
        ToolExecutionStatus.SUCCESS
    )
    assert executor._worker_pool is not None

    closed: list[str] = []
    application = JARVISApplication.__new__(JARVISApplication)
    application.plugins = None
    application.medical = None
    application.conversation_engine = SimpleNamespace(store=object())
    application.task_manager = SimpleNamespace(
        close=lambda: closed.append("tasks")
    )
    application.memory_manager = SimpleNamespace(
        close=lambda: closed.append("memory")
    )
    application.tool_executor = executor

    application.close()

    assert closed == ["tasks", "memory"], "the durable stores still close"
    assert executor._worker_pool is None, (
        "nothing else in the runtime calls shutdown()"
    )
    assert await _threads_have_ended(workers), (
        "a swapped-out runtime must not leave its worker threads running"
    )


@pytest.mark.asyncio
async def test_a_cancelled_coroutine_gives_its_slot_back_at_once() -> None:
    """A coroutine really is stopped, so nothing has to outlive the wait."""
    executor = ToolExecutor()

    async def slow() -> str:
        await asyncio.sleep(10)
        return "done"

    executor.register(
        ToolDefinition(
            name="slow_coroutine",
            description="Slow",
            timeout_seconds=0.05,
            max_concurrency=1,
        ),
        slow,
    )

    first = await executor.execute("slow_coroutine")
    second = await executor.execute("slow_coroutine")

    assert first.status is ToolExecutionStatus.TIMEOUT
    assert second.status is ToolExecutionStatus.TIMEOUT


@pytest.mark.asyncio
async def test_a_retried_step_reports_the_real_fault_not_a_replay_warning(
) -> None:
    """One approval buys one attempt, and the user still reads why it failed.

    A retryable tool re-offers the grant its first attempt already spent.
    The executor is right to refuse it, but that refusal used to land on
    top of the error the user needed: a mail server that could not be
    reached was reported as a blocked approval replay.
    """
    application = create_application()
    attempts: list[int] = []

    def send() -> str:
        attempts.append(1)
        raise RuntimeError("SMTP sunucusuna ulasilamadi")

    application.tool_executor.register(
        ToolDefinition(
            name="send_retryable",
            description="Send",
            risk_level=RiskLevel.HIGH,
            retry_max_attempts=2,
            idempotent=True,
        ),
        send,
    )
    plan = application.engine.create_plan(
        "send",
        [
            PlanStep(
                "send_retryable",
                metadata={"tool_name": "send_retryable", "parameters": {}},
            )
        ],
    )
    loop = AgentLoop(
        engine=application.engine,
        plan_builder=lambda request, context: plan,
    )

    waiting = await loop.run(Request("send"), mode=AgentMode.TASK)
    loop.approve(plan.steps[0].metadata["approval_operation_id"])
    failed = await loop.run(Request("send"), mode=AgentMode.TASK)

    assert waiting.status is AgentStatus.WAITING_FOR_APPROVAL
    assert failed.status is AgentStatus.FAILED
    assert plan.steps[0].metadata["tool_error"] == (
        "SMTP sunucusuna ulasilamadi"
    ), "the retry must not overwrite the fault the user has to act on"
    assert "replay" not in failed.response_text.lower()
    assert len(attempts) == 1, (
        "a spent approval never buys a second run of the handler"
    )
