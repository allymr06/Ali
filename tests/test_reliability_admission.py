from __future__ import annotations

import asyncio

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import Context, Request
from app.providers.base import ModelResponse
from app.reliability.admission import (
    AdmissionController,
    AdmissionRejectedError,
)


@pytest.mark.asyncio
async def test_admission_bounds_active_and_queued_requests() -> None:
    controller = AdmissionController(
        max_concurrent=2, max_queue=1, wait_timeout_seconds=1
    )
    first = await controller.acquire()
    second = await controller.acquire()
    queued = asyncio.create_task(controller.acquire())
    await asyncio.sleep(0)

    with pytest.raises(AdmissionRejectedError, match="queue"):
        await controller.acquire()
    snapshot = controller.snapshot()
    assert (snapshot.active, snapshot.waiting, snapshot.rejected) == (2, 1, 1)

    first.release()
    third = await queued
    assert controller.snapshot().active == 2
    second.release()
    third.release()
    assert controller.snapshot().active == 0


@pytest.mark.asyncio
async def test_admission_timeout_and_cancellation_release_queue_accounting() -> None:
    controller = AdmissionController(1, 2, wait_timeout_seconds=0.01)
    active = await controller.acquire()
    with pytest.raises(AdmissionRejectedError, match="timed out"):
        await controller.acquire()
    pending = asyncio.create_task(controller.acquire())
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert controller.snapshot().waiting == 0
    active.release()


def test_admission_validates_limits_and_unbalanced_release() -> None:
    with pytest.raises(ValueError, match="limits"):
        AdmissionController(0, 0, 1)


@pytest.mark.asyncio
async def test_core_rejects_overload_and_records_metric(monkeypatch) -> None:
    application = create_application(
        Settings(
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            core_max_concurrent_requests=1,
            core_max_queued_requests=0,
        )
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(request, _context, **_kwargs):
        entered.set()
        await release.wait()
        return ModelResponse(
            text="done", model="mock-model", provider="mock"
        )

    monkeypatch.setattr(application.provider_gateway, "generate", slow)
    first = asyncio.create_task(
        application.engine.handle(Request("first"), Context())
    )
    await entered.wait()
    with pytest.raises(AdmissionRejectedError):
        await application.engine.handle(Request("second"), Context())
    release.set()
    assert (await first).text == "done"
    metrics = application.diagnostics.metrics.snapshot()
    assert metrics["counters"]["core.requests.rejected"] == 1
    assert application.engine.admission.snapshot().active == 0


@pytest.mark.asyncio
async def test_mock_core_handles_bounded_parallel_load() -> None:
    application = create_application(
        Settings(
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            core_max_concurrent_requests=10,
            core_max_queued_requests=100,
        )
    )
    responses = await asyncio.wait_for(
        asyncio.gather(
            *(
                application.engine.handle(
                    Request(f"load-{index}"), Context()
                )
                for index in range(100)
            )
        ),
        timeout=5,
    )
    assert len(responses) == 100
    assert application.engine.admission.snapshot().accepted == 100
    assert application.engine.admission.snapshot().active == 0
