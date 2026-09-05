"""Manifest-based plugin runtime.

Plugins contribute tools and nothing else. Every plugin tool is registered
through the ordinary :class:`~app.tools.executor.ToolExecutor`, so it is
evaluated by the permission engine, bound to the approval path, timed
out, and reported exactly like a built-in tool. Plugins are disabled until
the user enables them explicitly.
"""

from app.plugins.discovery import DiscoveredPlugin, discover_plugins
from app.plugins.manifest import (
    ManifestError,
    PluginManifest,
    PluginParameter,
    PluginToolDeclaration,
    load_manifest,
    parse_manifest,
)
from app.plugins.runtime import PluginContext, PluginRecord, PluginRuntime, PluginState

__all__ = [
    "DiscoveredPlugin",
    "ManifestError",
    "PluginContext",
    "PluginManifest",
    "PluginParameter",
    "PluginRecord",
    "PluginRuntime",
    "PluginState",
    "PluginToolDeclaration",
    "discover_plugins",
    "load_manifest",
    "parse_manifest",
]
