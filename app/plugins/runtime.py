"""Plugin lifecycle, isolation, and tool registration.

The runtime never bypasses the tool and permission architecture: every
plugin tool becomes an ordinary :class:`ToolDefinition` registered on the
shared :class:`ToolExecutor` with ``source="plugin:<id>"``, so the
permission engine, approval binding, timeouts, and result contracts
apply unchanged. What the runtime adds is trust management (plugins are
disabled until the user enables them and the choice is persisted),
entry-point loading confined to the plugin directory, per-call timeouts,
bounded JSON output, and failure isolation with automatic quarantine.

Honest limit: plugin code runs in-process. Python cannot sandbox it, so a
plugin is trusted code the user placed in the plugins directory and
enabled on purpose. What JARVIS grants through the tool contract is
bounded; what the interpreter allows is not. Process isolation is a
later version.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any

from app.core.models import ToolDefinition, ToolExecutionStatus, ToolResult
from app.diagnostics.models import DiagnosticLevel
from app.plugins.discovery import discover_plugins
from app.plugins.manifest import (
    PluginManifest,
    PluginToolDeclaration,
    is_reparse_point,
)

STATE_FILENAME = "state.json"
STATE_SCHEMA_VERSION = 1
MODULE_NAMESPACE = "jarvis_plugins"
COMPONENT = "plugins"
MAX_OUTPUT_BYTES = 64 * 1024
MAX_LOG_CHARACTERS = 500
MAX_TOOL_TIMEOUT_SECONDS = 120.0
MAX_CONSECUTIVE_FAILURES_LIMIT = 10
WORKER_THREADS = 4


class PluginError(RuntimeError):
    """A plugin broke its contract; the message is safe to display."""


class PluginState(StrEnum):
    REJECTED = "rejected"   # manifest invalid; can never be enabled
    DISABLED = "disabled"   # valid but not enabled (the default)
    RUNNING = "running"     # enabled, entry point loaded, tools registered
    FAILED = "failed"       # enabled but start failed, or quarantined


@dataclass(frozen=True, slots=True)
class PluginContext:
    """What a plugin receives at start: identity, a private directory, a log.

    No settings, credentials, provider clients, or application services
    are ever passed to plugin code.
    """

    plugin_id: str
    version: str
    data_directory: Path
    log: Callable[[str], None]


@dataclass(slots=True)
class PluginRecord:
    plugin_id: str
    directory: Path
    manifest: PluginManifest | None
    state: PluginState
    enabled: bool = False
    error: str | None = None
    consecutive_failures: int = 0
    registered_tools: tuple[str, ...] = ()
    module_name: str | None = None

    @property
    def accepted(self) -> bool:
        return self.manifest is not None

    def snapshot(self) -> dict[str, object]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.manifest.name if self.manifest else None,
            "version": self.manifest.version if self.manifest else None,
            "state": self.state.value,
            "enabled": self.enabled,
            "tools": list(self.registered_tools),
            "consecutive_failures": self.consecutive_failures,
            "error": self.error,
        }


class PluginRuntime:
    """Own every plugin's lifecycle on top of the shared tool executor."""

    def __init__(
        self,
        *,
        directory: Path | str,
        tool_executor: Any,
        diagnostics: Any | None = None,
        tool_timeout_seconds: float = 10.0,
        max_consecutive_failures: int = 3,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        if not 0 < float(tool_timeout_seconds) <= MAX_TOOL_TIMEOUT_SECONDS:
            raise ValueError(
                f"tool_timeout_seconds must be within (0, {MAX_TOOL_TIMEOUT_SECONDS}]."
            )
        if not 1 <= int(max_consecutive_failures) <= MAX_CONSECUTIVE_FAILURES_LIMIT:
            raise ValueError(
                "max_consecutive_failures must be between 1 and "
                f"{MAX_CONSECUTIVE_FAILURES_LIMIT}."
            )
        if int(max_output_bytes) < 1:
            raise ValueError("max_output_bytes must be positive.")
        self._directory = Path(directory)
        self._tool_executor = tool_executor
        self._diagnostics = diagnostics
        self._tool_timeout_seconds = float(tool_timeout_seconds)
        self._max_consecutive_failures = int(max_consecutive_failures)
        self._max_output_bytes = int(max_output_bytes)
        self._records: dict[str, PluginRecord] = {}
        self._lock = RLock()
        self._pool = ThreadPoolExecutor(
            max_workers=WORKER_THREADS, thread_name_prefix="jarvis-plugin"
        )
        self._started = False
        self._closed = False

    # ------------------------------------------------------------------
    # inspection
    # ------------------------------------------------------------------
    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def tool_timeout_seconds(self) -> float:
        return self._tool_timeout_seconds

    def records(self) -> tuple[PluginRecord, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    def get(self, plugin_id: str) -> PluginRecord:
        with self._lock:
            try:
                return self._records[plugin_id]
            except KeyError as exc:
                raise KeyError(f"Plugin '{plugin_id}' is not known.") from exc

    def snapshot(self) -> tuple[dict[str, object], ...]:
        return tuple(record.snapshot() for record in self.records())

    # ------------------------------------------------------------------
    # discovery and trust
    # ------------------------------------------------------------------
    def discover(self) -> tuple[PluginRecord, ...]:
        """Scan the plugins directory; running plugins keep their state."""
        with self._lock:
            enabled_ids = self._load_enabled_ids()
            seen: set[str] = set()
            for found in discover_plugins(self._directory):
                seen.add(found.plugin_id)
                existing = self._records.get(found.plugin_id)
                if existing is not None and existing.state is PluginState.RUNNING:
                    continue
                if existing is not None and existing.registered_tools:
                    # A quarantined plugin still owns registered (disabled)
                    # tools; release them before the record is replaced.
                    self._stop(existing)
                if found.manifest is None:
                    record = PluginRecord(
                        plugin_id=found.plugin_id,
                        directory=found.directory,
                        manifest=None,
                        state=PluginState.REJECTED,
                        enabled=False,
                        error=found.error,
                    )
                    self._emit(
                        "plugin.rejected",
                        f"Plugin '{found.plugin_id}' was rejected: {found.error}",
                        level=DiagnosticLevel.WARNING,
                        attributes={"plugin_id": found.plugin_id},
                    )
                else:
                    record = PluginRecord(
                        plugin_id=found.plugin_id,
                        directory=found.directory,
                        manifest=found.manifest,
                        state=PluginState.DISABLED,
                        enabled=found.plugin_id in enabled_ids,
                    )
                    self._emit(
                        "plugin.discovered",
                        f"Plugin '{found.plugin_id}' {found.manifest.version} discovered.",
                        attributes={
                            "plugin_id": found.plugin_id,
                            "version": found.manifest.version,
                            "tools": len(found.manifest.tools),
                            "enabled": record.enabled,
                        },
                    )
                self._records[found.plugin_id] = record
            for plugin_id in list(self._records):
                if plugin_id not in seen:
                    self._stop(self._records[plugin_id])
                    del self._records[plugin_id]
            return self.records()

    def enable(self, plugin_id: str) -> PluginRecord:
        """Trust a plugin: persist the choice and start it if the runtime runs."""
        with self._lock:
            record = self.get(plugin_id)
            if record.manifest is None:
                raise ValueError(
                    f"Plugin '{plugin_id}' cannot be enabled: {record.error}"
                )
            record.enabled = True
            self._persist_state()
            self._emit(
                "plugin.enabled",
                f"Plugin '{plugin_id}' enabled by the user.",
                attributes={"plugin_id": plugin_id},
            )
            if self._started:
                self._start(record)
            return record

    def disable(self, plugin_id: str, *, reason: str = "user") -> PluginRecord:
        with self._lock:
            record = self.get(plugin_id)
            record.enabled = False
            self._persist_state()
            self._stop(record)
            record.state = (
                PluginState.REJECTED if record.manifest is None else PluginState.DISABLED
            )
            self._emit(
                "plugin.disabled",
                f"Plugin '{plugin_id}' disabled ({reason}).",
                attributes={"plugin_id": plugin_id, "reason": reason},
            )
            return record

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start_enabled(self) -> None:
        with self._lock:
            self._started = True
            for record in self.records():
                if record.enabled and record.manifest is not None:
                    self._start(record)

    def stop_all(self) -> None:
        with self._lock:
            for record in self.records():
                self._stop(record)
            self._started = False
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _start(self, record: PluginRecord) -> None:
        if record.manifest is None or record.state is PluginState.RUNNING:
            return
        manifest = record.manifest
        if record.registered_tools:
            # Quarantined tools stay registered but disabled; re-arm them.
            for name in record.registered_tools:
                self._tool_executor.enable(name)
            record.state = PluginState.RUNNING
            record.error = None
            record.consecutive_failures = 0
            self._emit(
                "plugin.started",
                f"Plugin '{record.plugin_id}' re-armed after quarantine.",
                attributes={"plugin_id": record.plugin_id, "tools": len(record.registered_tools)},
            )
            return
        registered: list[str] = []
        try:
            module = self._import_entry(record)
            factory = getattr(module, manifest.entry_callable, None)
            if not callable(factory):
                raise PluginError(
                    f"entry point '{manifest.entry_module}:{manifest.entry_callable}' "
                    "is not callable."
                )
            data_directory = record.directory / "data"
            data_directory.mkdir(parents=True, exist_ok=True)
            context = PluginContext(
                plugin_id=record.plugin_id,
                version=manifest.version,
                data_directory=data_directory,
                log=self._logger_for(record.plugin_id),
            )
            produced = factory(context)
            if not isinstance(produced, Mapping):
                raise PluginError("entry point must return a mapping of tool callables.")
            declared = {tool.name for tool in manifest.tools}
            extra = sorted(str(key) for key in produced if key not in declared)
            if extra:
                raise PluginError(
                    "entry point returned tools not declared in the manifest: "
                    + ", ".join(extra)
                )
            for declaration in manifest.tools:
                implementation = produced.get(declaration.name)
                if not callable(implementation):
                    raise PluginError(
                        f"declared tool '{declaration.name}' has no callable implementation."
                    )
                definition, handler = self._build_tool(record, declaration, implementation)
                try:
                    self._tool_executor.register(
                        definition, handler, source=f"plugin:{record.plugin_id}"
                    )
                except ValueError as exc:
                    raise PluginError(
                        f"tool '{definition.name}' could not be registered: {exc}"
                    ) from exc
                registered.append(definition.name)
        except Exception as exc:
            for name in registered:
                self._unregister_quietly(name)
            self._unload_module(record)
            record.state = PluginState.FAILED
            record.error = (
                str(exc) if isinstance(exc, PluginError) else type(exc).__name__
            )
            self._emit(
                "plugin.failed",
                f"Plugin '{record.plugin_id}' failed to start: {record.error}",
                level=DiagnosticLevel.ERROR,
                attributes={"plugin_id": record.plugin_id},
            )
            return
        record.registered_tools = tuple(registered)
        record.state = PluginState.RUNNING
        record.error = None
        record.consecutive_failures = 0
        self._emit(
            "plugin.started",
            f"Plugin '{record.plugin_id}' {manifest.version} started.",
            attributes={"plugin_id": record.plugin_id, "tools": len(registered)},
        )

    def _stop(self, record: PluginRecord) -> None:
        was_running = record.state is PluginState.RUNNING or bool(record.registered_tools)
        for name in record.registered_tools:
            self._unregister_quietly(name)
        record.registered_tools = ()
        self._unload_module(record)
        if record.state is PluginState.RUNNING:
            record.state = PluginState.DISABLED
        if was_running:
            self._emit(
                "plugin.stopped",
                f"Plugin '{record.plugin_id}' stopped.",
                attributes={"plugin_id": record.plugin_id},
            )

    def _unregister_quietly(self, name: str) -> None:
        try:
            self._tool_executor.unregister(name)
        except KeyError:
            pass

    # ------------------------------------------------------------------
    # entry point loading
    # ------------------------------------------------------------------
    def _import_entry(self, record: PluginRecord) -> Any:
        manifest = record.manifest
        assert manifest is not None
        module_file = record.directory / f"{manifest.entry_module}.py"
        if is_reparse_point(module_file) or not module_file.is_file():
            raise PluginError(
                f"entry module '{manifest.entry_module}.py' is missing or is a link."
            )
        directory = record.directory.resolve()
        resolved = module_file.resolve()
        if directory not in resolved.parents:
            raise PluginError("entry module escapes the plugin directory.")
        module_name = f"{MODULE_NAMESPACE}.{record.plugin_id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, resolved)
        if spec is None or spec.loader is None:
            raise PluginError("entry module could not be loaded.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        record.module_name = module_name
        return module

    def _unload_module(self, record: PluginRecord) -> None:
        if record.module_name is not None:
            sys.modules.pop(record.module_name, None)
            record.module_name = None

    # ------------------------------------------------------------------
    # tool adapter
    # ------------------------------------------------------------------
    def _build_tool(
        self,
        record: PluginRecord,
        declaration: PluginToolDeclaration,
        implementation: Callable[..., Any],
    ) -> tuple[ToolDefinition, Callable[..., ToolResult]]:
        manifest = record.manifest
        assert manifest is not None
        registered_name = manifest.registered_tool_name(declaration.name)
        parameters = []
        annotations: dict[str, Any] = {}
        for parameter in declaration.parameters:
            annotation: Any = parameter.python_type
            default: Any = inspect.Parameter.empty
            if not parameter.required:
                annotation = parameter.python_type | None
                default = None
            parameters.append(
                inspect.Parameter(
                    parameter.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=annotation,
                    default=default,
                )
            )
            annotations[parameter.name] = annotation
        annotations["return"] = ToolResult

        runtime = self

        def handler(**arguments: Any) -> ToolResult:
            return runtime._invoke(record, registered_name, implementation, arguments)

        handler.__name__ = registered_name
        handler.__qualname__ = registered_name
        handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            parameters, return_annotation=ToolResult
        )
        handler.__annotations__ = annotations
        handler.__doc__ = declaration.description

        definition = ToolDefinition(
            name=registered_name,
            description=f"[plugin {record.plugin_id}] {declaration.description}",
            risk_level=declaration.risk_level,
            requires_confirmation=declaration.requires_confirmation,
            # The adapter enforces its own deadline; give the executor a
            # slightly longer one so the adapter's honest TIMEOUT result wins.
            timeout_seconds=self._tool_timeout_seconds + 1.0,
            version=manifest.version,
            capabilities=frozenset({"plugin", f"plugin:{record.plugin_id}"}),
            tags=frozenset({"plugin"}),
            max_concurrency=2,
            metadata={
                "plugin_id": record.plugin_id,
                "plugin_version": manifest.version,
                "verification_strategy": "none",
            },
        )
        return definition, handler

    def _invoke(
        self,
        record: PluginRecord,
        registered_name: str,
        implementation: Callable[..., Any],
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        if self._closed:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=registered_name,
                message="Plugin runtime is closed.",
                error="closed",
                verified=False,
            )
        try:
            future = self._pool.submit(implementation, **dict(arguments))
        except RuntimeError:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=registered_name,
                message="Plugin runtime is closed.",
                error="closed",
                verified=False,
            )
        try:
            value = future.result(timeout=self._tool_timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            self._note_failure(record, "timeout")
            return ToolResult(
                status=ToolExecutionStatus.TIMEOUT,
                tool_name=registered_name,
                message="Plugin tool timed out.",
                error="timeout",
                verified=False,
                side_effects_may_continue=True,
            )
        except Exception as exc:
            reason = type(exc).__name__
            self._note_failure(record, reason)
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=registered_name,
                message="Plugin tool failed.",
                error=f"Plugin tool raised {reason}.",
                verified=False,
            )
        try:
            encoded = json.dumps(value)
        except (TypeError, ValueError):
            self._note_failure(record, "invalid_output")
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=registered_name,
                message="Plugin tool returned an invalid value.",
                error="Plugin output must be JSON-serializable.",
                verified=False,
            )
        if len(encoded.encode("utf-8")) > self._max_output_bytes:
            self._note_failure(record, "output_too_large")
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=registered_name,
                message="Plugin tool returned too much data.",
                error=f"Plugin output exceeds {self._max_output_bytes} bytes.",
                verified=False,
            )
        self._note_success(record)
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name=registered_name,
            message="Plugin tool completed; the outcome is not verified.",
            data=value,
            verified=False,
        )

    # ------------------------------------------------------------------
    # failure accounting
    # ------------------------------------------------------------------
    def _note_success(self, record: PluginRecord) -> None:
        with self._lock:
            record.consecutive_failures = 0

    def _note_failure(self, record: PluginRecord, reason: str) -> None:
        with self._lock:
            record.consecutive_failures += 1
            failures = record.consecutive_failures
            self._emit(
                "plugin.tool_failed",
                f"Plugin '{record.plugin_id}' tool failed ({reason}).",
                level=DiagnosticLevel.WARNING,
                attributes={
                    "plugin_id": record.plugin_id,
                    "reason": reason,
                    "consecutive_failures": failures,
                },
            )
            if failures >= self._max_consecutive_failures:
                self._quarantine(record, reason)

    def _quarantine(self, record: PluginRecord, reason: str) -> None:
        """Disable a misbehaving plugin without unregistering mid-call.

        Tools stay registered but disabled (a flag flip is safe while one
        of them is still executing); the user's explicit enable re-arms
        them, and stop_all/disable unregister them.
        """
        record.enabled = False
        self._persist_state()
        for name in record.registered_tools:
            try:
                self._tool_executor.disable(name)
            except KeyError:
                pass
        record.state = PluginState.FAILED
        record.error = (
            f"auto-disabled after {record.consecutive_failures} consecutive "
            f"failures ({reason})."
        )
        self._emit(
            "plugin.auto_disabled",
            f"Plugin '{record.plugin_id}' {record.error}",
            level=DiagnosticLevel.ERROR,
            attributes={"plugin_id": record.plugin_id, "reason": reason},
        )

    # ------------------------------------------------------------------
    # persisted trust state
    # ------------------------------------------------------------------
    def _state_path(self) -> Path:
        return self._directory / STATE_FILENAME

    def _load_enabled_ids(self) -> set[str]:
        path = self._state_path()
        if not path.is_file() or is_reparse_point(path):
            return set()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != STATE_SCHEMA_VERSION:
                raise ValueError("unsupported state schema")
            enabled = payload.get("enabled", {})
            if not isinstance(enabled, dict):
                raise ValueError("enabled must be an object")
            return {
                str(plugin_id)
                for plugin_id, flag in enabled.items()
                if flag is True
            }
        except (OSError, ValueError, AttributeError):
            self._emit(
                "plugin.state_invalid",
                "Plugin state file is invalid; every plugin stays disabled.",
                level=DiagnosticLevel.WARNING,
            )
            return set()

    def _persist_state(self) -> None:
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "enabled": {
                record.plugin_id: True
                for record in self.records()
                if record.enabled and record.manifest is not None
            },
        }
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------
    def _logger_for(self, plugin_id: str) -> Callable[[str], None]:
        def log(message: str) -> None:
            text = str(message)[:MAX_LOG_CHARACTERS]
            self._emit(
                "plugin.log",
                f"[{plugin_id}] {text}",
                attributes={"plugin_id": plugin_id},
            )

        return log

    def _emit(
        self,
        name: str,
        message: str,
        *,
        level: DiagnosticLevel = DiagnosticLevel.INFO,
        attributes: dict[str, object] | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        try:
            self._diagnostics.record(
                COMPONENT, name, message, level=level, attributes=attributes
            )
        except Exception:
            # Diagnostics must never take the plugin runtime down.
            pass


__all__ = [
    "COMPONENT",
    "MAX_OUTPUT_BYTES",
    "PluginContext",
    "PluginError",
    "PluginRecord",
    "PluginRuntime",
    "PluginState",
    "STATE_FILENAME",
]
