"""Run ONLY in factory-startup Blender with autoexec disabled; see docs.

Extracts pinned, licensed source geometry without adding subdivision/displacement.
Curve bevels are the author's own nerve/vessel geometry, not inferred trajectories.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from z_anatomy_source import ATTRIBUTION, FULL_ATTRIBUTION, BLEND_SHA256, LANDMARKS, LICENSE, OBJECTS, REVISION, SCENES, SOURCE  # noqa: E402


def dependencies(obj: object) -> list[object]:
    """The objects this one needs to evaluate: parent, modifier and constraint
    targets, and a curve's bevel/taper profiles. A nerve is a bevelled curve;
    dropping its profile would silently produce an empty mesh."""
    found = []
    parent = getattr(obj, "parent", None)
    if parent is not None:
        found.append(parent)
    for modifier in getattr(obj, "modifiers", []):
        for attribute in ("object", "target", "mirror_object", "offset_object", "start_cap", "end_cap", "curve", "origin", "auxiliary_target"):
            value = getattr(modifier, attribute, None)
            if value is not None and hasattr(value, "name"):
                found.append(value)
    for constraint in getattr(obj, "constraints", []):
        for attribute in ("target", "pole_target", "camera", "depth_object"):
            value = getattr(constraint, attribute, None)
            if value is not None and hasattr(value, "name"):
                found.append(value)
    data = getattr(obj, "data", None)
    for attribute in ("bevel_object", "taper_object"):
        value = getattr(data, attribute, None)
        if value is not None and hasattr(value, "name"):
            found.append(value)
    return found


def export_scene(bpy: object, wanted: set[str]) -> object:
    """A scene holding only the allowlisted objects, for the depsgraph to evaluate.

    The atlas is a whole body. Evaluating the file's own scene evaluates
    thousands of unrelated modifiers — one boolean among them crashes Blender
    3.6 — so a fresh empty scene is made and only the wanted objects are linked
    into it, with the parents, hook targets and curve profiles they need. The
    file's own scene is left untouched, nothing is deleted, nothing is saved,
    and each object still brings its own dependencies into the graph.
    """
    keep = {name for name in wanted if name in bpy.data.objects}
    queue = list(keep)
    while queue:
        current = bpy.data.objects[queue.pop()]
        for other in dependencies(current):
            name = getattr(other, "name", None)
            if name and name in bpy.data.objects and name not in keep:
                keep.add(name)
                queue.append(name)
    scene = bpy.data.scenes.new("jarvis-export")
    for name in sorted(keep):
        scene.collection.objects.link(bpy.data.objects[name])
    print(f"NARROWED the export scene to {len(keep)} objects", flush=True)
    return scene


def export(source: Path, destination: Path, *, full: bool = False) -> None:
    import bpy
    from mathutils.bvhtree import BVHTree

    objects = dict(OBJECTS)
    attribution = FULL_ATTRIBUTION if full else ATTRIBUTION
    scenes = list(SCENES)
    cards = {}
    if full:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.medical.atlas import catalog, scenes as atlas_scenes
        cards = {c["structure_id"]: c for c in catalog()}
        objects.update({sid: [card["object"]] for sid, card in cards.items()})
        scenes.extend(atlas_scenes())

    if bpy.context.preferences.filepaths.use_scripts_auto_execute:
        raise RuntimeError("Embedded scripts must be disabled.")
    # Blender ships its own interpreter (3.10 in the 3.6 LTS series), so the
    # digest is read in chunks rather than with hashlib.file_digest (3.11+).
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != BLEND_SHA256:
        raise ValueError("Source blend hash differs from the reviewed atlas.")
    # Fail rather than overwrite an earlier export/snapshot.
    destination.mkdir(parents=True, exist_ok=False)
    bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False)
    missing = [name for names in objects.values() for name in names if name not in bpy.data.objects]
    missing += [name for marks in LANDMARKS.values() for name in marks.values() if name not in bpy.data.objects]
    if missing:
        raise ValueError(f"Pinned atlas objects missing: {missing}")
    wanted = {name for names in objects.values() for name in names}
    wanted |= {name for marks in LANDMARKS.values() for name in marks.values()}
    scene = export_scene(bpy, wanted)
    with bpy.context.temp_override(scene=scene):
        graph = bpy.context.evaluated_depsgraph_get()
    assets = []
    for sid, names in objects.items():
        vertices, normals, triangles = [], [], []
        for name in names:
            obj = bpy.data.objects[name]
            evaluated = obj.evaluated_get(graph)
            mesh = evaluated.to_mesh()
            try:
                mesh.calc_loop_triangles()
                offset = len(vertices)
                matrix = evaluated.matrix_world
                normal_matrix = matrix.to_3x3().inverted().transposed()
                vertices.extend(matrix @ vertex.co for vertex in mesh.vertices)
                normals.extend((normal_matrix @ vertex.normal).normalized() for vertex in mesh.vertices)
                triangles.extend(tuple(offset + i for i in face.vertices) for face in mesh.loop_triangles)
            finally:
                evaluated.to_mesh_clear()
        if not triangles:
            raise ValueError(f"No real geometry for {sid}")
        bounds = {"min": [min(v[i] for v in vertices) for i in range(3)], "max": [max(v[i] for v in vertices) for i in range(3)]}
        extent = max(bounds["max"][i] - bounds["min"][i] for i in range(3))
        anchors = {}
        if sid in LANDMARKS:
            tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
            for lid, name in LANDMARKS[sid].items():
                marker = bpy.data.objects[name]
                if sid != "femur" and not any(m.type == "HOOK" and m.object and m.object.name in names for m in marker.modifiers):
                    raise ValueError(f"Unmatched bone hook: {name}")
                evaluated = marker.evaluated_get(graph)
                mesh = evaluated.to_mesh()
                try:
                    hits = [tree.find_nearest(evaluated.matrix_world @ v.co) for v in mesh.vertices]
                    point, _normal, _face, distance = min((h for h in hits if h[0] is not None), key=lambda h: h[3])
                    if distance > extent * 0.035:
                        raise ValueError(f"Annotation too far from bone: {name}: {distance}")
                    anchors[lid] = {"anchor": list(point), "confidence": "approximate", "method": f"Z-Anatomy annotation {name}; nearest endpoint projected to source surface; distance {distance:.6g} m; not independently anatomically reviewed"}
                finally:
                    evaluated.to_mesh_clear()
        path = destination / f"{sid}.obj"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(f"# {attribution}\n# {SOURCE}\n")
            for v in vertices:
                stream.write("v " + " ".join(f"{c:.9g}" for c in v) + "\n")
            for n in normals:
                stream.write("vn " + " ".join(f"{c:.9g}" for c in n) + "\n")
            for triangle in triangles:
                stream.write("f " + " ".join(f"{i + 1}//{i + 1}" for i in triangle) + "\n")
        assets.append({"structure_id": sid, "file": path.name, "license": LICENSE, "source": SOURCE,
                       "attribution": attribution, "side": cards.get(sid, {}).get("side", "right"), "up_axis": "z", "landmarks": anchors,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "provenance": {"dataset": "Z-Anatomy", "revision": REVISION, "objects": names,
                                      "vertices": len(vertices), "triangles": len(triangles),
                                      "geometry": "source evaluated mesh; no added subdivision or invented anatomical detail"}})
        print(f"EXPORTED {sid}: {len(triangles)} triangles, {len(anchors)} source pins", flush=True)
    (destination / "manifest.json").write_text(json.dumps({"assets": assets, "scenes": scenes, "full_atlas": full}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    source_path, output_path = args[:2]
    export(Path(source_path), Path(output_path), full="--full" in args[2:])
