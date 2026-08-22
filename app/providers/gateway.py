from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, replace

from app.core.models import Context, Request
from app.providers.base import (
    ModelResponse,
    ModelStreamChunk,
    ProviderAuthenticationError,
    ProviderCapability,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.models import RoutingDecision, TaskType
from app.providers.registry import ProviderRegistry
from app.providers.router import ModelRouter
from app.reliability.circuit import CircuitBreaker, CircuitState


@dataclass(slots=True)
class ProviderHealth:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_latency_seconds: float | None = None
    circuit_state: CircuitState = CircuitState.CLOSED


class _GatewayCancelled(Exception):
    pass


class ProviderGateway:
    """The single bounded, observable entry point to model providers."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        router: ModelRouter | None = None,
        timeout_seconds: float = 15.0,
        max_retries: int = 1,
        retry_backoff_seconds: float = 0.25,
        max_retry_delay_seconds: float = 2.0,
        fallback_enabled: bool = True,
        circuit_failure_threshold: int = 5,
        circuit_recovery_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative.")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative.")
        if max_retry_delay_seconds < 0:
            raise ValueError("max_retry_delay_seconds cannot be negative.")
        if circuit_failure_threshold < 1 or circuit_recovery_seconds <= 0:
            raise ValueError("Circuit breaker limits are invalid.")
        self._registry = registry
        self._router = router or ModelRouter(registry)
        self._timeout_seconds = min(timeout_seconds, 15.0)
        self._max_retries = min(max_retries, 1)
        self._retry_backoff_seconds = retry_backoff_seconds
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._fallback_enabled = fallback_enabled
        self._health: dict[str, ProviderHealth] = {}
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_recovery_seconds = circuit_recovery_seconds
        self._breakers: dict[str, CircuitBreaker] = {}

    @property
    def router(self) -> ModelRouter:
        return self._router

    def health(self, provider: str) -> ProviderHealth:
        normalized = provider.strip()
        state = self._health.setdefault(normalized, ProviderHealth())
        circuit = self._breaker(normalized).snapshot()
        return ProviderHealth(
            successes=state.successes,
            failures=state.failures,
            consecutive_failures=state.consecutive_failures,
            last_error=state.last_error,
            last_latency_seconds=state.last_latency_seconds,
            circuit_state=circuit.state,
        )

    def _breaker(self, provider: str) -> CircuitBreaker:
        return self._breakers.setdefault(
            provider,
            CircuitBreaker(
                self._circuit_failure_threshold,
                self._circuit_recovery_seconds,
            ),
        )

    async def _await_operation(
        self,
        awaitable,
        cancel_event,
        *,
        timeout_seconds: float | None = None,
    ):
        timeout = (
            self._timeout_seconds
            if timeout_seconds is None
            else min(self._timeout_seconds, timeout_seconds)
        )
        if timeout <= 0:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise ProviderTimeoutError("Provider request timed out.")
        operation = asyncio.create_task(awaitable)
        cancellation = (
            asyncio.create_task(cancel_event.wait())
            if cancel_event is not None
            else None
        )
        waiters = {operation}
        if cancellation is not None:
            waiters.add(cancellation)
        try:
            done, _ = await asyncio.wait(
                waiters,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation in done:
                return operation.result()
            operation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation
            if cancellation is not None and cancellation in done:
                raise _GatewayCancelled
            raise ProviderTimeoutError("Provider request timed out.")
        finally:
            if not operation.done():
                operation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await operation
            if cancellation is not None:
                cancellation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancellation

    def _remaining_budget(
        self,
        deadline: float,
        provider: str,
    ) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTimeoutError(
                "Provider request exceeded the total timeout budget.",
                provider=provider,
            )
        return min(remaining, self._timeout_seconds)

    @staticmethod
    def _normalize_response(raw, expected_provider: str) -> ModelResponse:
        if isinstance(raw, ModelResponse):
            response = raw
        else:
            try:
                response = ModelResponse(
                    text=raw.text,
                    model=raw.model,
                    provider=raw.provider,
                    finish_reason=getattr(raw, "finish_reason", None),
                    tool_calls=list(getattr(raw, "tool_calls", []) or []),
                    usage=dict(getattr(raw, "usage", {}) or {}),
                    metadata=dict(getattr(raw, "metadata", {}) or {}),
                )
            except (AttributeError, TypeError, ValueError) as exc:
                raise ProviderInvalidResponseError(
                    "Provider returned an invalid response contract.",
                    provider=expected_provider,
                ) from exc
        if response.provider.strip() != expected_provider:
            raise ProviderInvalidResponseError(
                f"Provider identity mismatch: expected '{expected_provider}', "
                f"received '{response.provider}'.",
                provider=expected_provider,
            )
        return response

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        return isinstance(error, ProviderError) and error.retryable

    @staticmethod
    def _normalize_error(error: Exception, provider: str) -> ProviderError:
        if isinstance(error, ProviderError):
            return error
        if isinstance(error, TimeoutError):
            return ProviderTimeoutError(
                "Provider request timed out.",
                provider=provider,
            )
        return ProviderError(
            f"Provider '{provider}' failed unexpectedly.",
            provider=provider,
        )

    async def _backoff(
        self,
        attempt: int,
        cancel_event,
        retry_after_seconds: float | None = None,
        *,
        deadline: float | None = None,
    ) -> None:
        delay = (
            retry_after_seconds
            if retry_after_seconds is not None and retry_after_seconds >= 0
            else self._retry_backoff_seconds * (2 ** max(0, attempt - 1))
        )
        delay = min(delay, self._max_retry_delay_seconds)
        if deadline is not None:
            delay = min(delay, max(0.0, deadline - time.monotonic()))
        if delay <= 0:
            return
        if cancel_event is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=delay)
        except TimeoutError:
            return
        raise _GatewayCancelled

    def _candidates(
        self,
        decision: RoutingDecision,
    ) -> tuple[
        tuple[str, str | None],
        ...,
    ]:
        primary = (
            (
                decision.provider,
                decision.model,
            ),
        )

        if (
            not self._fallback_enabled
            or decision.user_override
        ):
            return primary

        configured_fallbacks = tuple(
            candidate
            for candidate
            in decision.fallback_candidates
            if (
                self._registry.contains(
                    candidate[0]
                )
                and self._registry.get(
                    candidate[0]
                ).is_configured
            )
        )

        candidates = (
            primary
            + configured_fallbacks
        )

        if (
            decision.provider.casefold()
            == "mock"
        ):
            return candidates

        return tuple(
            candidate
            for candidate in candidates
            if (
                candidate[0].casefold()
                != "mock"
            )
        )

    def _reasoning_task_type(
        self,
        request: Request,
        *,
        explicit: TaskType | str | None,
        routed: TaskType,
    ) -> TaskType:
        classifier = getattr(self._router, "classify", None)
        if not callable(classifier):
            return routed
        classified = classifier(request, task_type=explicit)
        return (
            classified
            if isinstance(classified, TaskType)
            else TaskType(classified)
        )

    def _model_metadata(
        self,
        provider: str,
        model: str,
        usage: dict[str, int],
    ) -> dict[str, int | float]:
        if not self._router.catalog.contains(provider, model):
            return {}
        profile = self._router.catalog.get(provider, model)
        metadata: dict[str, int | float] = {}
        if profile.max_context_tokens is not None:
            metadata["model_context_limit"] = profile.max_context_tokens
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        output_tokens = usage.get(
            "output_tokens",
            usage.get("completion_tokens", 0),
        )
        if (
            profile.input_cost_per_million is not None
            and profile.output_cost_per_million is not None
        ):
            metadata["estimated_cost_usd"] = (
                input_tokens * profile.input_cost_per_million
                + output_tokens * profile.output_cost_per_million
            ) / 1_000_000
        return metadata

    async def generate(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        provider: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        task_type: TaskType | str | None = None,
        required_capabilities: Iterable[ProviderCapability] = (),
        cancel_event=None,
    ) -> ModelResponse:
        if response_format is not None and not isinstance(response_format, dict):
            raise TypeError("response_format must be a dictionary.")
        required = self._router.required_capabilities(
            request,
            tools=tools,
            structured_output=response_format is not None,
            extra=required_capabilities,
        )
        decision = self._router.route(
            request,
            tools=tools,
            provider=provider,
            model=model,
            task_type=task_type,
            required=required,
        )
        reasoning_task_type = self._reasoning_task_type(
            request,
            explicit=task_type,
            routed=decision.task_type,
        )
        provider_request = replace(
            request,
            metadata={
                **request.metadata,
                "task_type": decision.task_type.value,
                "reasoning_task_type": reasoning_task_type.value,
                "routing_reason": decision.reason,
            },
        )
        last_error: Exception | None = None
        fallback_count = 0
        candidates = self._candidates(decision)
        deadline = time.monotonic() + self._timeout_seconds
        max_attempts = 1 + int(
            self._max_retries > 0 or self._fallback_enabled
        )
        total_attempts = 0

        for candidate_index, (provider_name, selected_model) in enumerate(
            candidates
        ):
            if total_attempts >= max_attempts:
                break
            provider_instance = self._registry.get(provider_name)
            if not provider_instance.capabilities.supports(required):
                continue
            health = self._health.setdefault(provider_name, ProviderHealth())
            breaker = self._breaker(provider_name)

            for attempt in range(1, self._max_retries + 2):
                if total_attempts >= max_attempts:
                    break
                if not breaker.allow():
                    last_error = ProviderUnavailableError(
                        f"Provider '{provider_name}' circuit is open.",
                        provider=provider_name,
                    )
                    health.circuit_state = CircuitState.OPEN
                    break
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError
                remaining = self._remaining_budget(deadline, provider_name)
                total_attempts += 1
                started = time.monotonic()
                try:
                    provider_request.metadata.pop("_reasoning_level", None)
                    provider_kwargs = {
                        "model": selected_model,
                        "system_prompt": system_prompt,
                        "tools": tools,
                    }
                    if response_format is not None:
                        provider_kwargs["response_format"] = response_format
                    raw = await self._await_operation(
                        provider_instance.generate(
                            provider_request,
                            context,
                            **provider_kwargs,
                        ),
                        cancel_event,
                        timeout_seconds=remaining,
                    )
                    response = self._normalize_response(raw, provider_name)
                except _GatewayCancelled as exc:
                    raise asyncio.CancelledError from exc
                except Exception as exc:
                    error = self._normalize_error(exc, provider_name)
                    health.failures += 1
                    health.consecutive_failures += 1
                    health.last_error = str(error)
                    health.last_latency_seconds = time.monotonic() - started
                    if self._is_retryable(error):
                        breaker.failure()
                    health.circuit_state = breaker.snapshot().state
                    last_error = error
                    if (
                        self._is_retryable(error)
                        and attempt <= self._max_retries
                        and total_attempts < max_attempts
                    ):
                        try:
                            await self._backoff(
                                attempt,
                                cancel_event,
                                error.retry_after_seconds,
                                deadline=deadline,
                            )
                        except _GatewayCancelled as cancel_exc:
                            raise asyncio.CancelledError from cancel_exc
                        continue
                    break

                health.successes += 1
                health.consecutive_failures = 0
                health.last_error = None
                health.last_latency_seconds = time.monotonic() - started
                breaker.success()
                health.circuit_state = CircuitState.CLOSED
                response.metadata.update(
                    {
                        "gateway_attempt": attempt,
                        "gateway_total_attempts": total_attempts,
                        "fallback_count": fallback_count,
                        "routing_reason": decision.reason,
                        "task_type": decision.task_type.value,
                        "reasoning_level": provider_request.metadata.get(
                            "_reasoning_level"
                        ),
                        "provider_latency_seconds": health.last_latency_seconds,
                    }
                )
                response.metadata.update(
                    self._model_metadata(
                        provider_name,
                        response.model,
                        response.usage,
                    )
                )
                return response

            if total_attempts >= max_attempts:
                break
            if candidate_index + 1 < len(candidates):
                fallback_count += 1

        if time.monotonic() >= deadline:
            raise ProviderTimeoutError(
                "Provider request exceeded the total timeout budget.",
            )
        if last_error is not None:
            raise last_error
        raise ProviderUnavailableError(
            "No capable provider candidate is available."
        )

    async def stream(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        provider: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        task_type: TaskType | str | None = None,
        cancel_event=None,
    ) -> AsyncIterator[ModelStreamChunk]:
        required = self._router.required_capabilities(
            request,
            tools=tools,
            streaming=True,
        )
        decision = self._router.route(
            request,
            tools=tools,
            provider=provider,
            model=model,
            task_type=task_type,
            required=required,
            streaming=True,
        )
        reasoning_task_type = self._reasoning_task_type(
            request,
            explicit=task_type,
            routed=decision.task_type,
        )
        provider_request = replace(
            request,
            metadata={
                **request.metadata,
                "task_type": decision.task_type.value,
                "reasoning_task_type": reasoning_task_type.value,
                "routing_reason": decision.reason,
            },
        )
        last_error: Exception | None = None
        fallback_count = 0
        candidates = self._candidates(decision)
        deadline = time.monotonic() + self._timeout_seconds
        max_attempts = 1 + int(
            self._max_retries > 0 or self._fallback_enabled
        )
        total_attempts = 0

        for candidate_index, (provider_name, selected_model) in enumerate(
            candidates
        ):
            if total_attempts >= max_attempts:
                break
            provider_instance = self._registry.get(provider_name)
            if not provider_instance.capabilities.supports(required):
                continue
            health = self._health.setdefault(provider_name, ProviderHealth())
            breaker = self._breaker(provider_name)

            for attempt in range(1, self._max_retries + 2):
                if total_attempts >= max_attempts:
                    break
                if not breaker.allow():
                    last_error = ProviderUnavailableError(
                        f"Provider '{provider_name}' circuit is open.",
                        provider=provider_name,
                    )
                    health.circuit_state = CircuitState.OPEN
                    break
                emitted = False
                remaining = self._remaining_budget(deadline, provider_name)
                total_attempts += 1
                started = time.monotonic()
                try:
                    provider_request.metadata.pop("_reasoning_level", None)
                    async with asyncio.timeout(remaining):
                        async for chunk in provider_instance.stream(
                            provider_request,
                            context,
                            model=selected_model,
                            system_prompt=system_prompt,
                            tools=tools,
                        ):
                            if cancel_event is not None and cancel_event.is_set():
                                raise asyncio.CancelledError
                            if not isinstance(chunk, ModelStreamChunk):
                                raise ProviderInvalidResponseError(
                                    "Provider returned an invalid stream chunk.",
                                    provider=provider_name,
                                )
                            if chunk.provider != provider_name:
                                raise ProviderInvalidResponseError(
                                    "Streaming provider identity mismatch.",
                                    provider=provider_name,
                                )
                            emitted = True
                            chunk.metadata.update(
                                {
                                    "gateway_attempt": attempt,
                                    "gateway_total_attempts": total_attempts,
                                    "fallback_count": fallback_count,
                                    "routing_reason": decision.reason,
                                    "task_type": decision.task_type.value,
                                    "reasoning_level": provider_request.metadata.get(
                                        "_reasoning_level"
                                    ),
                                }
                            )
                            yield chunk
                except TimeoutError as exc:
                    error: Exception = ProviderTimeoutError(
                        "Provider stream timed out.",
                        provider=provider_name,
                    )
                    error.__cause__ = exc
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error = self._normalize_error(exc, provider_name)
                else:
                    health.successes += 1
                    health.consecutive_failures = 0
                    health.last_error = None
                    health.last_latency_seconds = time.monotonic() - started
                    breaker.success()
                    health.circuit_state = CircuitState.CLOSED
                    return

                health.failures += 1
                health.consecutive_failures += 1
                health.last_error = str(error)
                health.last_latency_seconds = time.monotonic() - started
                if self._is_retryable(error):
                    breaker.failure()
                health.circuit_state = breaker.snapshot().state
                last_error = error

                if emitted:
                    raise error
                if (
                    self._is_retryable(error)
                    and attempt <= self._max_retries
                    and total_attempts < max_attempts
                ):
                    try:
                        await self._backoff(
                            attempt,
                            cancel_event,
                            error.retry_after_seconds,
                            deadline=deadline,
                        )
                    except _GatewayCancelled as cancel_exc:
                        raise asyncio.CancelledError from cancel_exc
                    continue
                break

            if total_attempts >= max_attempts:
                break
            if candidate_index + 1 < len(candidates):
                fallback_count += 1

        if time.monotonic() >= deadline:
            raise ProviderTimeoutError(
                "Provider stream exceeded the total timeout budget.",
            )
        if last_error is not None:
            raise last_error
        raise ProviderUnavailableError(
            "No capable streaming provider candidate is available."
        )
