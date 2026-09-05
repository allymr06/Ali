"""Discover plugins in the trusted plugins directory.

Discovery is deliberately narrow: only immediate subdirectories of the
configured root whose names are valid plugin ids are considered, links
and junctions are never followed, and a broken manifest rejects that one
plugin without affecting the others. Discovery imports no plugin code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.plugins.manifest import (
    PLUGIN_ID_PATTERN,
    ManifestError,
    PluginManifest,
    is_reparse_point,
    load_manifest,
)


@dataclass(frozen=True, slots=True)
class DiscoveredPlugin:
    plugin_id: str
    directory: Path
    manifest: PluginManifest | None
    error: str | None

    @property
    def accepted(self) -> bool:
        return self.manifest is not None and self.error is None


def discover_plugins(root: Path) -> tuple[DiscoveredPlugin, ...]:
    """Return every candidate plugin below ``root``, valid or rejected."""
    root = Path(root)
    if not root.is_dir():
        return ()
    found: list[DiscoveredPlugin] = []
    try:
        entries = sorted(os.scandir(root), key=lambda entry: entry.name)
    except OSError:
        return ()
    for entry in entries:
        if not PLUGIN_ID_PATTERN.fullmatch(entry.name):
            continue
        directory = root / entry.name
        if is_reparse_point(directory):
            found.append(
                DiscoveredPlugin(
                    entry.name, directory, None, "plugin directory is a link or junction."
                )
            )
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        try:
            manifest = load_manifest(directory)
        except ManifestError as exc:
            found.append(DiscoveredPlugin(entry.name, directory, None, str(exc)))
            continue
        found.append(DiscoveredPlugin(entry.name, directory, manifest, None))
    return tuple(found)


__all__ = ["DiscoveredPlugin", "discover_plugins"]
