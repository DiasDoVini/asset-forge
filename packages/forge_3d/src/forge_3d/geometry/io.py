"""Loading and saving meshes."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import trimesh

MESH_EXTENSIONS = {".glb", ".gltf", ".obj", ".stl", ".ply", ".off", ".3mf"}

# glTF's default material is fully metallic, which renders almost black without an environment map.
# Primitives exported without a material (vertex/face colors, plain meshes) get this matte one instead.
MATTE = {"name": "matte", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0, "roughnessFactor": 0.8}}

# edges sharper than this stay hard in the exported normals; smoother ones are shaded smooth
CREASE_ANGLE = 60.0


def load_mesh(path: str | Path) -> trimesh.Trimesh:
    """Load any supported file as a single Trimesh (scenes are flattened with transforms applied)."""
    path = Path(path)
    if path.suffix.lower() not in MESH_EXTENSIONS:
        raise ValueError(f"unsupported mesh format: {path.suffix}")
    if path.suffix.lower() in (".glb", ".gltf"):
        scene = trimesh.load(path, force="scene")
        for name, geom in list(scene.geometry.items()):
            scene.geometry[name] = _colors_from_attributes(geom)
        loaded = scene.to_mesh() if len(scene.geometry) else None
    else:
        loaded = trimesh.load(path, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ValueError(f"no triangle geometry found in {path}")
    # exported normals split vertices along hard edges (see shade_smooth): weld them back so the
    # topology (watertightness, booleans, printing) is the real one; UV seams stay split
    loaded.merge_vertices(merge_tex=False, merge_norm=True)
    return loaded


def _colors_from_attributes(geom):
    """Our GLBs give vertex-colored primitives a matte material (see MATTE); trimesh then loads them as an
    untextured TextureVisuals with the colors tucked into vertex_attributes, and drops them when meshes are
    merged. Turn those back into vertex colors."""
    vis = getattr(geom, "visual", None)
    if (isinstance(geom, trimesh.Trimesh) and vis is not None and vis.kind != "vertex"
            and getattr(vis, "uv", None) is None and "color" in getattr(vis, "vertex_attributes", {})):
        colors = np.asarray(vis.vertex_attributes["color"])
        if len(colors) == len(geom.vertices):
            geom = geom.copy()
            geom.visual = trimesh.visual.ColorVisuals(geom, vertex_colors=colors)
    return geom


def save_mesh(mesh: trimesh.Trimesh | trimesh.Scene, path: str | Path) -> Path:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in MESH_EXTENSIONS:
        raise ValueError(f"unsupported mesh format: {suffix}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".glb":
        # without normals, glTF viewers/engines (three.js, Blender, Unity...) shade every triangle flat
        if isinstance(mesh, trimesh.Scene):
            mesh = mesh.copy()
            for name, geom in list(mesh.geometry.items()):
                if isinstance(geom, trimesh.Trimesh) and len(geom.faces):
                    mesh.geometry[name] = shade_smooth(geom)
        else:
            mesh = shade_smooth(mesh)
        path.write_bytes(_with_default_material(mesh.export(file_type="glb", include_normals=True)))
    else:
        mesh.export(path)
    return path


def _with_default_material(glb: bytes) -> bytes:
    magic, version, _ = struct.unpack("<4sII", glb[:12])
    json_len, json_type = struct.unpack("<II", glb[12:20])
    if magic != b"glTF" or json_type != 0x4E4F534A:
        return glb
    doc = json.loads(glb[20:20 + json_len])
    prims = [p for m in doc.get("meshes", []) for p in m.get("primitives", []) if "material" not in p]
    if not prims:
        return glb
    doc.setdefault("materials", []).append(MATTE)
    for p in prims:
        p["material"] = len(doc["materials"]) - 1
    chunk = json.dumps(doc, separators=(",", ":")).encode()
    chunk += b" " * (-len(chunk) % 4)
    rest = glb[20 + json_len:]
    header = struct.pack("<4sII", magic, version, 12 + 8 + len(chunk) + len(rest))
    return header + struct.pack("<II", len(chunk), json_type) + chunk + rest


def shade_smooth(mesh: trimesh.Trimesh, crease_angle: float = CREASE_ANGLE) -> trimesh.Trimesh:
    """Copy with smooth vertex normals, split along edges sharper than ``crease_angle`` degrees (hard edges stay
    hard). Meshes whose faces share no vertices (stylized low poly) keep flat normals. Colors/UVs are kept."""
    return shade_smooth_indexed(mesh, crease_angle)[0]


def shade_smooth_indexed(mesh: trimesh.Trimesh, crease_angle: float = CREASE_ANGLE) -> tuple[trimesh.Trimesh, np.ndarray]:
    """``shade_smooth`` plus, for every output vertex, the input vertex it came from (to carry per-vertex data
    such as skin weights)."""
    faces = np.asarray(mesh.faces)
    adj = mesh.face_adjacency
    smooth_edges = adj[mesh.face_adjacency_angles < np.radians(crease_angle)]
    groups = trimesh.graph.connected_component_labels(smooth_edges, node_count=len(faces))
    # one new vertex per (original vertex, smooth patch)
    corners = np.stack([faces.ravel(), np.repeat(groups, 3)], axis=1)
    keys, inverse = np.unique(corners, axis=0, return_inverse=True)
    orig = keys[:, 0]
    out = trimesh.Trimesh(np.asarray(mesh.vertices)[orig], inverse.reshape(-1, 3), process=False)
    vis = mesh.visual
    if vis.kind == "texture" and getattr(vis, "uv", None) is not None and len(vis.uv) == len(mesh.vertices):
        out.visual = trimesh.visual.TextureVisuals(uv=np.asarray(vis.uv)[orig], material=vis.material)
    elif vis.kind == "vertex":
        out.visual = trimesh.visual.ColorVisuals(out, vertex_colors=np.asarray(vis.vertex_colors)[orig])
    elif vis.kind == "face":
        out.visual = trimesh.visual.ColorVisuals(out, face_colors=np.asarray(vis.face_colors))
    elif vis.kind == "texture":
        out.visual = vis.copy()
    out.metadata.update(mesh.metadata)
    _ = out.vertex_normals  # computed now (area-weighted per patch) so the export includes them
    return out, orig
