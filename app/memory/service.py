from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.memory.manager import MemoryManager
from app.memory.models import MemoryEntry
from app.tools.executor import ToolExecutor


@dataclass(slots=True)
class MemoryService:
    """User-visible memory controls exposed through strict tool boundaries."""

    manager: MemoryManager

    @staticmethod
    def _serialize(memory: MemoryEntry) -> dict[str, object]:
        return {
            "memory_id": str(memory.memory_id),
            "content": memory.content,
            "memory_type": memory.memory_type.value,
            "importance": memory.importance,
            "confidence": memory.confidence,
            "source": memory.source.value,
            "source_reference": memory.source_reference,
            "freshness": memory.freshness().value,
            "active": memory.active,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
            "expires_at": (
                memory.expires_at.isoformat() if memory.expires_at else None
            ),
        }

    def list(self, active_only: bool = True, limit: int = 100) -> list[dict[str, object]]:
        if limit < 1 or limit > 500:
            raise ValueError("Memory list limit must be between 1 and 500.")
        memories = self.manager.active() if active_only else self.manager.all()
        ordered = sorted(memories, key=lambda item: item.updated_at, reverse=True)
        return [self._serialize(memory) for memory in ordered[:limit]]

    def search(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        if limit < 1 or limit > 100:
            raise ValueError("Memory search limit must be between 1 and 100.")
        return [self._serialize(memory) for memory in self.manager.recall(query, limit=limit)]

    def forget(self, memory_id: str) -> ToolResult:
        memory = self.manager.forget(UUID(memory_id))
        verified = not self.manager.get(memory.memory_id).active
        return ToolResult(
            status=(ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.FAILED),
            tool_name="forget_memory",
            message="Memory deactivated." if verified else "Memory deactivation failed.",
            data={"memory_id": memory_id, "active": not verified},
            verified=verified,
        )

    def delete(self, memory_id: str) -> ToolResult:
        identifier = UUID(memory_id)
        self.manager.delete(identifier)
        try:
            self.manager.get(identifier)
        except KeyError:
            verified = True
        else:
            verified = False
        return ToolResult(
            status=(ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.FAILED),
            tool_name="delete_memory",
            message="Memory permanently deleted." if verified else "Memory deletion failed.",
            data={"memory_id": memory_id},
            verified=verified,
        )

    def register_tools(self, executor: ToolExecutor) -> None:
        def list_memories(active_only: bool = True, limit: int = 100) -> ToolResult:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="list_memories",
                message="Memory records observed.",
                data=self.list(active_only=active_only, limit=limit),
                verified=True,
            )

        def search_memories(query: str, limit: int = 10) -> ToolResult:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="search_memories",
                message="Relevant memory records observed.",
                data=self.search(query=query, limit=limit),
                verified=True,
            )

        executor.register(
            ToolDefinition(
                name="list_memories",
                description="List JARVIS memory records with source and freshness.",
                capabilities=frozenset({"memory", "observe"}),
                tags=frozenset({"memory", "read-only"}),
                metadata={"verification_strategy": "storage_readback"},
            ),
            list_memories,
            source="core:memory",
        )
        executor.register(
            ToolDefinition(
                name="search_memories",
                description="Search relevant JARVIS memory records.",
                capabilities=frozenset({"memory", "search"}),
                tags=frozenset({"memory", "read-only"}),
                metadata={"verification_strategy": "storage_readback"},
            ),
            search_memories,
            source="core:memory",
        )
        executor.register(
            ToolDefinition(
                name="forget_memory",
                description="Deactivate a memory while retaining its history.",
                risk_level=RiskLevel.MEDIUM,
                requires_confirmation=True,
                capabilities=frozenset({"memory", "forget"}),
                tags=frozenset({"memory", "action"}),
                metadata={"verification_strategy": "storage_readback"},
            ),
            self.forget,
            source="core:memory",
        )
        executor.register(
            ToolDefinition(
                name="delete_memory",
                description="Permanently delete one JARVIS memory record.",
                risk_level=RiskLevel.HIGH,
                requires_confirmation=True,
                capabilities=frozenset({"memory", "delete"}),
                tags=frozenset({"memory", "destructive"}),
                metadata={"verification_strategy": "storage_absence"},
            ),
            self.delete,
            source="core:memory",
        )
