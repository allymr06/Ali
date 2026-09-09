"""Extract source metadata in Blender, without running embedded scripts.

Run with --background --factory-startup --disable-autoexec --python-exit-code 1
--python scripts/inventory_z_anatomy.py -- Startup.blend inventory.json.
Output must be new: existing inventories are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from z_anatomy_source import BLEND_SHA256  # noqa: E402


def inventory(source: Path, destination: Path) -> None:
    with source.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        if digest.hexdigest() != BLEND_SHA256:
            raise ValueError("Source does not match the pinned atlas")
    if destination.exists():
        raise FileExistsError(destination)
    import bpy

    if bpy.context.preferences.filepaths.use_scripts_auto_execute:
        raise ValueError("Automatic script execution must be disabled")
    bpy.ops.wm.open_mainfile(filepath=str(source.resolve()), use_scripts=False)
    records = []
    for obj in bpy.data.objects:
        if obj.type not in {"MESH", "CURVE"}:
            continue
        records.append({
            "name": obj.name,
            "type": obj.type,
            "faces": len(obj.data.polygons) if obj.type == "MESH" else -1,
            "collections": [collection.name for collection in obj.users_collection],
            "properties": {str(key): str(value) for key, value in obj.items()
                           if isinstance(value, (str, int, float))},
        })
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(records, stream, ensure_ascii=False)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:]
    if len(arguments) != 2:
        raise SystemExit("Expected source.blend and a new inventory.json path")
    inventory(Path(arguments[0]), Path(arguments[1]))
