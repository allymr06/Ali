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
from z_anatomy_source import ATTRIBUTION, BLEND_SHA256, LANDMARKS, LICENSE, OBJECTS, REVISION, SCENES, SOURCE  # noqa: E402


def export(source: Path, destination: Path) -> None:
    import bpy
    from mathutils.bvhtree import BVHTree

    if bpy.context.preferences.filepaths.use_scripts_auto_execute:
        raise RuntimeError("Embedded scripts must be disabled.")
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != BLEND_SHA256:
            raise ValueError("Source blend hash differs from the reviewed atlas.")
    # Fail rather than overwrite an earlier export/snapshot.
    destination.mkdir(parents=True, exist_ok=False)
    bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False)
    missing = [name for names in OBJECTS.values() for name in names if name not in bpy.data.objects]
    missing += [name for marks in LANDMARKS.values() for name in marks.values() if name not in bpy.data.objects]
    if missing:
        raise ValueError(f"Pinned atlas objects missing: {missing}")
    graph = bpy.context.evaluated_depsgraph_get()
    assets = []
    for sid, names in OBJECTS.items():
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
            stream.write(f"# {ATTRIBUTION}\n# {SOURCE}\n")
            for v in vertices:
                stream.write("v " + " ".join(f"{c:.9g}" for c in v) + "\n")
            for n in normals:
                stream.write("vn " + " ".join(f"{c:.9g}" for c in n) + "\n")
            for triangle in triangles:
                stream.write("f " + " ".join(f"{i + 1}//{i + 1}" for i in triangle) + "\n")
        assets.append({"structure_id": sid, "file": path.name, "license": LICENSE, "source": SOURCE,
                       "attribution": ATTRIBUTION, "side": "right", "up_axis": "z", "landmarks": anchors,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "provenance": {"dataset": "Z-Anatomy", "revision": REVISION, "objects": names,
                                      "vertices": len(vertices), "triangles": len(triangles),
                                      "geometry": "source evaluated mesh; no added subdivision or invented anatomical detail"}})
        print(f"EXPORTED {sid}: {len(triangles)} triangles, {len(anchors)} source pins", flush=True)
    (destination / "manifest.json").write_text(json.dumps({"assets": assets, "scenes": SCENES}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    source_path, output_path = sys.argv[sys.argv.index("--") + 1:]
    export(Path(source_path), Path(output_path))
