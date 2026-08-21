from __future__ import annotations

from collections.abc import Iterable

from app.core.models import Request
from app.providers.base import (
    ProviderCapability,
    ProviderCapabilityError,
)
from app.providers.catalog import ModelCatalog
from app.providers.models import RoutingDecision, TaskType
from app.providers.registry import ProviderRegistry


class ModelRouter:
    """Select a capable provider/model without coupling Core to a vendor."""

    _COMPLEX_MARKERS = (
        "analyze",
        "architecture",
        "compare",
        "design",
        "incele",
        "karşılaştır",
        "mimari",
        "planla",
        "reason",
        "tasarla",
    )
    _LONG_RUNNING_MARKERS = (
        "background",
        "long-running",
        "monitor",
        "uzun süren",
        "izle",
    )

    def __init__(
        self,
        registry: ProviderRegistry,
        catalog: ModelCatalog | None = None,
    ) -> None:
        self._registry = registry
        self._catalog = catalog or ModelCatalog()

    @property
    def catalog(self) -> ModelCatalog:
        return self._catalog

    def classify(
        self,
        request: Request,
        *,
        tools: list[dict] | None = None,
        task_type: TaskType | str | None = None,
    ) -> TaskType:
        explicit = task_type or request.metadata.get("task_type")
        if explicit is not None:
            try:
                return explicit if isinstance(explicit, TaskType) else TaskType(explicit)
            except ValueError as exc:
                raise ValueError(f"Unknown task type: {explicit}") from exc

        if request.metadata.get("vision") or request.metadata.get("images"):
            return TaskType.VISION
        if tools:
            return TaskType.AGENTIC

        normalized = request.text.casefold()
        if any(marker in normalized for marker in self._LONG_RUNNING_MARKERS):
            return TaskType.LONG_RUNNING
        if (
            len(request.text.split()) >= 40
            or any(marker in normalized for marker in self._COMPLEX_MARKERS)
        ):
            return TaskType.COMPLEX
        if len(request.text.split()) <= 12:
            return TaskType.SIMPLE
        return TaskType.STANDARD

    @staticmethod
    def required_capabilities(
        request: Request,
        *,
        tools: list[dict] | None = None,
        streaming: bool = False,
        structured_output: bool = False,
        extra: Iterable[ProviderCapability] = (),
    ) -> frozenset[ProviderCapability]:
        extra_capabilities = tuple(extra)
        if not all(
            isinstance(item, ProviderCapability)
            for item in extra_capabilities
        ):
            raise TypeError(
                "Required capabilities must be ProviderCapability values."
            )
        required = {ProviderCapability.TEXT, *extra_capabilities}
        if tools:
            required.add(ProviderCapability.TOOL_CALLING)
        if streaming:
            required.add(ProviderCapability.STREAMING)
        if structured_output or request.metadata.get("structured_output"):
            required.add(ProviderCapability.STRUCTURED_OUTPUT)
        if request.metadata.get("vision") or request.metadata.get("images"):
            required.add(ProviderCapability.VISION)
        return frozenset(required)

    def _ensure_capable(
        self,
        provider_name: str,
        required: frozenset[ProviderCapability],
    ) -> None:
        provider = self._registry.get(provider_name)
        missing = provider.capabilities.missing(required)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ProviderCapabilityError(
                f"Provider '{provider_name}' lacks capabilities: {names}.",
                provider=provider_name,
            )

    def route(
        self,
        request: Request,
        *,
        tools: list[dict] | None = None,
        provider: str | None = None,
        model: str | None = None,
        task_type: TaskType | str | None = None,
        required: frozenset[ProviderCapability] | None = None,
        streaming: bool = False,
    ) -> RoutingDecision:
        selected_task_type = self.classify(
            request,
            tools=tools,
            task_type=task_type,
        )
        capabilities = required or self.required_capabilities(
            request,
            tools=tools,
            streaming=streaming,
        )
        requested_provider = provider or request.metadata.get("provider")
        requested_model = model or request.metadata.get("model")
        user_override = requested_provider is not None or requested_model is not None

        if requested_provider is not None:
            requested_provider = str(requested_provider).strip()
            if not requested_provider:
                raise ValueError("Provider override cannot be empty.")
            self._ensure_capable(requested_provider, capabilities)
            if requested_model is not None:
                requested_model = str(requested_model).strip()
                if not requested_model:
                    raise ValueError("Model override cannot be empty.")
                if self._catalog.contains(requested_provider, requested_model):
                    profile = self._catalog.get(requested_provider, requested_model)
                    if not profile.supports(selected_task_type, capabilities):
                        raise ProviderCapabilityError(
                            f"Model '{requested_provider}/{requested_model}' "
                            "does not satisfy the requested task.",
                            provider=requested_provider,
                        )
            return RoutingDecision(
                provider=requested_provider,
                model=requested_model,
                task_type=selected_task_type,
                required_capabilities=capabilities,
                reason="user_provider_override",
                user_override=True,
            )

        default_provider = self._registry.get_default().name.strip()
        default_candidates = self._catalog.candidates(
            task_type=selected_task_type,
            required=capabilities,
            provider=default_provider,
        )
        all_candidates = self._catalog.candidates(
            task_type=selected_task_type,
            required=capabilities,
        )

        default_is_capable = self._registry.get(
            default_provider
        ).capabilities.supports(capabilities)

        if requested_model is not None:
            self._ensure_capable(default_provider, capabilities)
            selected_model = str(requested_model).strip()
            if not selected_model:
                raise ValueError("Model override cannot be empty.")
            if self._catalog.contains(default_provider, selected_model):
                profile = self._catalog.get(default_provider, selected_model)
                if not profile.supports(selected_task_type, capabilities):
                    raise ProviderCapabilityError(
                        f"Model '{default_provider}/{selected_model}' does not "
                        "satisfy the requested task.",
                        provider=default_provider,
                    )
            return RoutingDecision(
                provider=default_provider,
                model=selected_model,
                task_type=selected_task_type,
                required_capabilities=capabilities,
                reason="user_model_override",
                user_override=True,
            )

        primary = default_candidates[0] if default_candidates else None
        default_profiles = self._catalog.list(default_provider)
        if not default_is_capable or (
            default_profiles and primary is None and all_candidates
        ):
            primary = next(
                (
                    candidate
                    for candidate in all_candidates
                    if self._registry.contains(candidate.provider)
                    and self._registry.get(candidate.provider).capabilities.supports(
                        capabilities
                    )
                ),
                None,
            )
            capable_providers = self._registry.list_capable(capabilities)
            if primary is None and not capable_providers:
                self._ensure_capable(default_provider, capabilities)
            selected_provider = (
                primary.provider
                if primary is not None
                else capable_providers[0].name.strip()
            )
            reason = (
                "capability_routing"
                if not default_is_capable
                else "task_type_routing"
            )
        else:
            selected_provider = default_provider
            reason = (
                "default_provider_catalog_match"
                if primary is not None
                else "default_provider_dynamic_model"
            )
        selected_model = primary.model if primary is not None else None
        fallbacks = tuple(
            (candidate.provider, candidate.model)
            for candidate in all_candidates
            if (candidate.provider, candidate.model)
            != (selected_provider, selected_model)
            and self._registry.contains(candidate.provider)
            and self._registry.get(candidate.provider).capabilities.supports(
                capabilities
            )
        )
        catalog_pairs = {
            (candidate.provider, candidate.model)
            for candidate in all_candidates
        }
        dynamic_fallbacks = tuple(
            (candidate.name.strip(), None)
            for candidate in self._registry.list_capable(capabilities)
            if candidate.name.strip() != selected_provider
            and (candidate.name.strip(), None) not in catalog_pairs
            and not any(
                provider_name == candidate.name.strip()
                for provider_name, _ in fallbacks
            )
        )
        return RoutingDecision(
            provider=selected_provider,
            model=selected_model,
            task_type=selected_task_type,
            required_capabilities=capabilities,
            reason=reason,
            fallback_candidates=fallbacks + dynamic_fallbacks,
            user_override=user_override,
        )
