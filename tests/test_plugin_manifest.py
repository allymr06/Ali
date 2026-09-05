"""Plugin manifest validation: strict schema, risk floor, safe entry points."""

from __future__ import annotations

import json

import pytest

from app.core.models import RiskLevel
from app.plugins.manifest import (
    MANIFEST_FILENAME,
    MAX_MANIFEST_BYTES,
    MAX_PARAMETERS_PER_TOOL,
    MAX_TOOLS_PER_PLUGIN,
    ManifestError,
    load_manifest,
    parse_manifest,
)
from tests.plugin_helpers import ECHO_FIXTURE, echo_manifest, fixture_manifest


def test_sample_manifest_parses_with_namespaced_tools() -> None:
    manifest = load_manifest(ECHO_FIXTURE)

    assert manifest.plugin_id == "echo"
    assert manifest.version == "1.0.0"
    assert manifest.entry_module == "plugin"
    assert manifest.entry_callable == "create_plugin"
    assert manifest.capabilities == frozenset({"tools"})
    tool = manifest.tools[0]
    assert tool.name == "echo"
    assert tool.risk_level is RiskLevel.LOW  # read_only is raised to the floor
    assert tool.requires_confirmation is False
    assert [p.name for p in tool.parameters] == ["text", "repeat"]
    assert tool.parameters[0].required is True
    assert tool.parameters[1].required is False
    assert tool.parameters[1].python_type is int
    assert manifest.registered_tool_name("echo") == "plugin_echo_echo"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda m: m.update(schema_version=2), "schema_version"),
        (lambda m: m.update(plugin_id="Echo!"), "invalid format"),
        (lambda m: m.update(plugin_id="other"), "directory name"),
        (lambda m: m.update(version="1.0"), "invalid format"),
        (lambda m: m.update(entry_point="../evil:run"), "entry_point"),
        (lambda m: m.update(entry_point="plugin"), "entry_point"),
        (lambda m: m.update(entry_point="plugin:"), "entry_point"),
        (lambda m: m.update(surprise=True), "unknown fields"),
        (lambda m: m.update(capabilities=["tools", "network"]), "unsupported"),
        (lambda m: m.update(capabilities=[]), "must include 'tools'"),
        (lambda m: m.update(tools=[]), "at least one tool"),
        (lambda m: m.update(tools="echo"), "must be a list"),
        (lambda m: m.pop("name"), "missing fields"),
        (lambda m: m["tools"][0].update(risk_level="critical"), "cannot be critical"),
        (lambda m: m["tools"][0].update(risk_level="bogus"), "not a known risk level"),
        (lambda m: m["tools"][0].update(name="Echo"), "invalid format"),
        (lambda m: m["tools"][0].update(extra=1), "unknown fields"),
        (lambda m: m["tools"][0].update(requires_confirmation="yes"), "true or false"),
        (lambda m: m["tools"].append(dict(m["tools"][0])), "twice"),
        (
            lambda m: m["tools"][0]["parameters"].append({"name": "text", "type": "string"}),
            "twice",
        ),
        (
            lambda m: m["tools"][0]["parameters"].append({"name": "blob", "type": "object"}),
            "type must be one of",
        ),
        (
            lambda m: m["tools"][0]["parameters"].append({"name": "Bad", "type": "string"}),
            "invalid format",
        ),
        (
            lambda m: m["tools"].extend(
                {"name": f"t{i}", "description": "x"} for i in range(MAX_TOOLS_PER_PLUGIN)
            ),
            "more than",
        ),
        (
            lambda m: m["tools"][0]["parameters"].extend(
                {"name": f"p{i}", "type": "string"} for i in range(MAX_PARAMETERS_PER_TOOL)
            ),
            "more than",
        ),
    ],
)
def test_invalid_manifests_are_rejected(mutate, message) -> None:
    payload = fixture_manifest()
    mutate(payload)

    with pytest.raises(ManifestError, match=message):
        parse_manifest(payload, expected_id="echo")


def test_manifest_must_be_an_object() -> None:
    with pytest.raises(ManifestError, match="must be an object"):
        parse_manifest(["not", "an", "object"])


@pytest.mark.parametrize(
    ("declared", "confirmation", "expected_risk", "expected_confirmation"),
    [
        ("read_only", False, RiskLevel.LOW, False),
        ("low", False, RiskLevel.LOW, False),
        ("low", True, RiskLevel.LOW, True),
        ("medium", False, RiskLevel.MEDIUM, True),
        ("high", False, RiskLevel.HIGH, True),
    ],
)
def test_risk_floor_and_forced_confirmation(
    declared, confirmation, expected_risk, expected_confirmation
) -> None:
    manifest = parse_manifest(
        echo_manifest(risk_level=declared, requires_confirmation=confirmation),
        expected_id="echo",
    )

    assert manifest.tools[0].risk_level is expected_risk
    assert manifest.tools[0].requires_confirmation is expected_confirmation


def test_risk_level_defaults_to_the_floor() -> None:
    payload = echo_manifest()
    payload["tools"][0].pop("risk_level")

    assert parse_manifest(payload, expected_id="echo").tools[0].risk_level is RiskLevel.LOW


def test_manifest_file_limits(tmp_path) -> None:
    directory = tmp_path / "echo"
    directory.mkdir()
    with pytest.raises(ManifestError, match="missing"):
        load_manifest(directory)

    (directory / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="not valid"):
        load_manifest(directory)

    payload = fixture_manifest()
    payload["description"] = "x" * (MAX_MANIFEST_BYTES)
    (directory / MANIFEST_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestError, match="larger than"):
        load_manifest(directory)

    (directory / MANIFEST_FILENAME).write_text(json.dumps(fixture_manifest()), encoding="utf-8")
    assert load_manifest(directory).plugin_id == "echo"


def test_manifest_link_is_rejected(tmp_path, monkeypatch) -> None:
    directory = tmp_path / "echo"
    directory.mkdir()
    (directory / MANIFEST_FILENAME).write_text(json.dumps(fixture_manifest()), encoding="utf-8")
    monkeypatch.setattr("app.plugins.manifest.is_reparse_point", lambda path: True)

    with pytest.raises(ManifestError, match="regular file"):
        load_manifest(directory)
