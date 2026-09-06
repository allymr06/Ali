"""Request augmentation: a domain layer's say over one core turn.

A domain module (the Medical Academy is the first) registers one
asynchronous augmenter with the core engine. For each general request
the augmenter may add to the system prompt, narrow the exposed tools
(never widen them), answer directly without a model call, or ask the
engine not to write memories for the turn. Anything else about the turn
— approvals, permissions, verification, budgets — is untouched.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.models import Context, Request


@dataclass(frozen=True, slots=True)
class RequestAugmentation:
    system_prompt: str | None = None
    allowed_tools: frozenset[str] | None = None
    direct_response: str | None = None
    kind: str = "augmented"
    suppress_memory: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.system_prompt is not None and not isinstance(self.system_prompt, str):
            raise TypeError("system_prompt must be a string or None.")
        if self.direct_response is not None and not isinstance(self.direct_response, str):
            raise TypeError("direct_response must be a string or None.")
        if self.allowed_tools is not None:
            object.__setattr__(
                self,
                "allowed_tools",
                frozenset(str(name).strip() for name in self.allowed_tools if str(name).strip()),
            )
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("kind cannot be empty.")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def empty(self) -> bool:
        return (
            self.system_prompt is None
            and self.allowed_tools is None
            and self.direct_response is None
            and not self.suppress_memory
        )


RequestAugmenter = Callable[[Request, Context], Awaitable["RequestAugmentation | None"]]
