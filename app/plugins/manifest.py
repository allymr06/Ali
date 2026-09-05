"""Versioned plugin manifests with strict, fail-closed validation.

A plugin lives in its own directory below the plugins root and describes
everything it contributes in ``plugin.json``. The manifest is validated
before any plugin code is imported: unknown fields, oversized files,
unsafe entry points, risk levels below the plugin floor, and ``critical``
tools are rejected with a :class:`ManifestError`.

Only the ``tools`` capability exists in this version. A plugin cannot
lower the risk of what it registers: the effective risk of every tool is
at least :data:`PLUGIN_RISK_FLOOR`, and medium-or-higher tools always
require confirmation through the existing approval path.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.models import RiskLevel

MANIFEST_FILENAME = "plugin.json"
MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 64 * 1024
MAX_TOOLS_PER_PLUGIN = 32
MAX_PARAMETERS_PER_TOOL = 16
PLUGIN_RISK_FLOOR = RiskLevel.LOW
SUPPORTED_CAPABILITIES = frozenset({"tools"})

PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
PARAMETER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
MODULE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
CALLABLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,60}$")
VERSION_PATTERN = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")

PARAMETER_TYPES: Mapping[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}

_RISK_ORDER = {
    RiskLevel.READ_ONLY: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}

_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "plugin_id",
        "name",
        "version",
        "description",
        "entry_point",
        "capabilities",
        "tools",
    }
)
_TOOL_KEYS = frozenset(
    {"name", "description", "risk_level", "requires_confirmation", "parameters"}
)
_PARAMETER_KEYS = frozenset({"name", "type", "required", "description"})


class ManifestError(ValueError):
    """The manifest is missing, malformed, or declares something unsafe."""


@dataclass(frozen=True, slots=True)
class PluginParameter:
    name: str
    type_name: str
    required: bool = True
    description: str = ""

    @property
    def python_type(self) -> type:
        return PARAMETER_TYPES[self.type_name]


@dataclass(frozen=True, slots=True)
class PluginToolDeclaration:
    name: str
    description: str
    risk_level: RiskLevel
    requires_confirmation: bool
    parameters: tuple[PluginParameter, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginManifest:
    schema_version: int
    plugin_id: str
    name: str
    version: str
    description: str
    entry_module: str
    entry_callable: str
    capabilities: frozenset[str]
    tools: tuple[PluginToolDeclaration, ...]

    @property
    def registered_prefix(self) -> str:
        """Every plugin tool is namespaced so it cannot shadow a built-in."""
        return "plugin_" + self.plugin_id.replace("-", "_") + "_"

    def registered_tool_name(self, tool_name: str) -> str:
        return self.registered_prefix + tool_name


def risk_at_least(level: RiskLevel, floor: RiskLevel) -> RiskLevel:
    return level if _RISK_ORDER[level] >= _RISK_ORDER[floor] else floor


def is_reparse_point(path: Path) -> bool:
    """True for symlinks and Windows junctions (never followed)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"{where} must be an object.")
    return value


def _allowed_keys(
    mapping: Mapping[str, Any],
    allowed: frozenset[str],
    required: frozenset[str],
    where: str,
) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise ManifestError(f"{where} has unknown fields: {', '.join(unknown)}.")
    missing = sorted(key for key in required if key not in mapping)
    if missing:
        raise ManifestError(f"{where} is missing fields: {', '.join(missing)}.")


def _string(
    value: Any,
    where: str,
    *,
    minimum: int = 1,
    maximum: int = 300,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{where} must be a string.")
    text = value.strip()
    if len(text) < minimum:
        raise ManifestError(f"{where} cannot be empty.")
    if len(text) > maximum:
        raise ManifestError(f"{where} is longer than {maximum} characters.")
    if pattern is not None and not pattern.fullmatch(text):
        raise ManifestError(f"{where} has an invalid format.")
    return text


def _boolean(value: Any, where: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ManifestError(f"{where} must be true or false.")
    return value


def _parameter(payload: Any, where: str) -> PluginParameter:
    mapping = _object(payload, where)
    _allowed_keys(mapping, _PARAMETER_KEYS, frozenset({"name", "type"}), where)
    name = _string(mapping["name"], f"{where}.name", maximum=41, pattern=PARAMETER_NAME_PATTERN)
    type_name = _string(mapping["type"], f"{where}.type", maximum=16)
    if type_name not in PARAMETER_TYPES:
        raise ManifestError(
            f"{where}.type must be one of: {', '.join(sorted(PARAMETER_TYPES))}."
        )
    return PluginParameter(
        name=name,
        type_name=type_name,
        required=_boolean(mapping.get("required"), f"{where}.required", True),
        description=(
            _string(mapping["description"], f"{where}.description", minimum=0, maximum=200)
            if "description" in mapping
            else ""
        ),
    )


def _tool(payload: Any, where: str) -> PluginToolDeclaration:
    mapping = _object(payload, where)
    _allowed_keys(mapping, _TOOL_KEYS, frozenset({"name", "description"}), where)
    name = _string(mapping["name"], f"{where}.name", maximum=41, pattern=TOOL_NAME_PATTERN)
    description = _string(mapping["description"], f"{where}.description", maximum=300)

    declared = mapping.get("risk_level", PLUGIN_RISK_FLOOR.value)
    if not isinstance(declared, str):
        raise ManifestError(f"{where}.risk_level must be a string.")
    try:
        declared_level = RiskLevel(declared.strip().lower())
    except ValueError as exc:
        raise ManifestError(f"{where}.risk_level is not a known risk level.") from exc
    if declared_level is RiskLevel.CRITICAL:
        raise ManifestError(
            f"{where}.risk_level cannot be critical: critical operations are "
            "denied by policy, so the tool could never run."
        )
    effective = risk_at_least(declared_level, PLUGIN_RISK_FLOOR)
    requires_confirmation = _boolean(
        mapping.get("requires_confirmation"), f"{where}.requires_confirmation", False
    )
    if _RISK_ORDER[effective] >= _RISK_ORDER[RiskLevel.MEDIUM]:
        # Plugins cannot opt out of the approval path for risky work.
        requires_confirmation = True

    raw_parameters = mapping.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise ManifestError(f"{where}.parameters must be a list.")
    if len(raw_parameters) > MAX_PARAMETERS_PER_TOOL:
        raise ManifestError(
            f"{where} declares more than {MAX_PARAMETERS_PER_TOOL} parameters."
        )
    parameters = tuple(
        _parameter(item, f"{where}.parameters[{index}]")
        for index, item in enumerate(raw_parameters)
    )
    seen: set[str] = set()
    for parameter in parameters:
        if parameter.name in seen:
            raise ManifestError(f"{where} declares parameter '{parameter.name}' twice.")
        seen.add(parameter.name)

    return PluginToolDeclaration(
        name=name,
        description=description,
        risk_level=effective,
        requires_confirmation=requires_confirmation,
        parameters=parameters,
    )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def parse_manifest(
    payload: Any, *, expected_id: str | None = None
) -> PluginManifest:
    """Validate a decoded manifest object and return the typed manifest."""
    mapping = _object(payload, "manifest")
    _allowed_keys(
        mapping,
        _MANIFEST_KEYS,
        frozenset({"schema_version", "plugin_id", "name", "version", "entry_point", "tools"}),
        "manifest",
    )

    schema_version = mapping["schema_version"]
    if isinstance(schema_version, bool) or schema_version != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"manifest.schema_version must be {MANIFEST_SCHEMA_VERSION}."
        )

    plugin_id = _string(
        mapping["plugin_id"], "manifest.plugin_id", maximum=41, pattern=PLUGIN_ID_PATTERN
    )
    if expected_id is not None and plugin_id != expected_id:
        raise ManifestError(
            "manifest.plugin_id must match the plugin directory name."
        )
    name = _string(mapping["name"], "manifest.name", maximum=80)
    version = _string(
        mapping["version"], "manifest.version", maximum=14, pattern=VERSION_PATTERN
    )
    description = (
        _string(mapping["description"], "manifest.description", minimum=0, maximum=300)
        if "description" in mapping
        else ""
    )

    entry_point = _string(mapping["entry_point"], "manifest.entry_point", maximum=120)
    module_name, separator, callable_name = entry_point.partition(":")
    if (
        not separator
        or not MODULE_NAME_PATTERN.fullmatch(module_name)
        or not CALLABLE_NAME_PATTERN.fullmatch(callable_name)
    ):
        raise ManifestError(
            "manifest.entry_point must look like 'module:callable' and name a "
            "module file inside the plugin directory."
        )

    raw_capabilities = mapping.get("capabilities", ["tools"])
    if not isinstance(raw_capabilities, list) or not all(
        isinstance(item, str) for item in raw_capabilities
    ):
        raise ManifestError("manifest.capabilities must be a list of strings.")
    capabilities = frozenset(item.strip().lower() for item in raw_capabilities)
    unsupported = sorted(capabilities - SUPPORTED_CAPABILITIES)
    if unsupported:
        raise ManifestError(
            f"manifest.capabilities contains unsupported entries: {', '.join(unsupported)}."
        )

    raw_tools = mapping["tools"]
    if not isinstance(raw_tools, list):
        raise ManifestError("manifest.tools must be a list.")
    if not raw_tools:
        raise ManifestError("manifest.tools must declare at least one tool.")
    if len(raw_tools) > MAX_TOOLS_PER_PLUGIN:
        raise ManifestError(
            f"manifest.tools declares more than {MAX_TOOLS_PER_PLUGIN} tools."
        )
    if "tools" not in capabilities:
        raise ManifestError("manifest.capabilities must include 'tools'.")
    tools = tuple(_tool(item, f"manifest.tools[{index}]") for index, item in enumerate(raw_tools))
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            raise ManifestError(f"manifest.tools declares '{tool.name}' twice.")
        seen.add(tool.name)

    return PluginManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        plugin_id=plugin_id,
        name=name,
        version=version,
        description=description,
        entry_module=module_name,
        entry_callable=callable_name,
        capabilities=capabilities,
        tools=tools,
    )


def load_manifest(directory: Path) -> PluginManifest:
    """Read and validate ``plugin.json`` from a plugin directory.

    The manifest must be a regular file (no symlink or junction) of at
    most :data:`MAX_MANIFEST_BYTES`, and its ``plugin_id`` must equal the
    directory name.
    """
    path = directory / MANIFEST_FILENAME
    if is_reparse_point(path):
        raise ManifestError("plugin.json must be a regular file, not a link.")
    if not path.is_file():
        raise ManifestError("plugin.json is missing.")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ManifestError("plugin.json could not be read.") from exc
    if size > MAX_MANIFEST_BYTES:
        raise ManifestError(f"plugin.json is larger than {MAX_MANIFEST_BYTES} bytes.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("plugin.json is not valid UTF-8 JSON.") from exc
    return parse_manifest(payload, expected_id=directory.name)


__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_MANIFEST_BYTES",
    "MAX_PARAMETERS_PER_TOOL",
    "MAX_TOOLS_PER_PLUGIN",
    "PLUGIN_ID_PATTERN",
    "PLUGIN_RISK_FLOOR",
    "PARAMETER_TYPES",
    "ManifestError",
    "PluginManifest",
    "PluginParameter",
    "PluginToolDeclaration",
    "is_reparse_point",
    "load_manifest",
    "parse_manifest",
    "risk_at_least",
]
