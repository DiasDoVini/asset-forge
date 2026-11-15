"""Mesh diagnostics."""

from __future__ import annotations

import numpy as np
import trimesh

from .bridge import has_uv, weld


def mesh_stats(mesh: trimesh.Trimesh) -> dict:
    """Topology and size report. Edge checks run on the position-welded mesh so UV seams don't count as holes."""
    vertices, faces, _ = weld(mesh)
    welded = trimesh.Trimesh(vertices, faces, process=False)
    _, counts = np.unique(welded.edges_sorted, axis=0, return_counts=True)
    labels = trimesh.graph.connected_component_labels(welded.face_adjacency, node_count=len(faces))
    watertight = bool(welded.is_watertight)
    material = getattr(mesh.visual, "material", None)
    return {
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "watertight": watertight,
        "winding_consistent": bool(welded.is_winding_consistent),
        "is_volume": bool(welded.is_volume),
        "boundary_edges": int((counts == 1).sum()),
        "non_manifold_edges": int((counts > 2).sum()),
        "components": int(labels.max() + 1) if len(labels) else 0,
        "bounds": np.round(mesh.bounds, 4).tolist(),
        "extents": np.round(mesh.extents, 4).tolist(),
        "surface_area": float(mesh.area),
        "volume": float(welded.volume) if watertight else None,
        "has_uv": has_uv(mesh),
        "has_texture": bool(material is not None and _material_image(material) is not None),
        "has_vertex_colors": mesh.visual.kind == "vertex",
    }


def _material_image(material):
    return getattr(material, "image", None) or getattr(material, "baseColorTexture", None)


def remove_depth_tilt(mesh: trimesh.Trimesh, min_elongation: float = 4.0,
                      min_degrees: float = 8.0) -> tuple[trimesh.Trimesh, float]:
    """Thin, elongated objects (staffs, swords, batons) generated from a front image sometimes come out leaning
    toward/away from the camera although the image shows them upright. Rotate the long axis back into the
    image plane (XY), keeping its direction as seen from the front. Returns (mesh, degrees corrected; 0 = left
    alone: not elongated enough or already in plane)."""
    pts, _ = trimesh.sample.sample_surface(mesh, 4000, seed=0) if len(mesh.faces) else (mesh.vertices, None)
    pts = np.asarray(pts)
    if len(pts) < 10:
        return mesh, 0.0
    centered = pts - pts.mean(axis=0)
    evals, evecs = np.linalg.eigh(centered.T @ centered / len(pts))
    axis = evecs[:, 2]
    if evals[2] < (min_elongation ** 2) * max(evals[1], 1e-12):  # variance ratio = elongation squared
        return mesh, 0.0
    in_plane = np.array([axis[0], axis[1], 0.0])
    if np.linalg.norm(in_plane) < 1e-6:  # pointing straight at the camera: no in-plane direction to keep
        return mesh, 0.0
    in_plane /= np.linalg.norm(in_plane)
    tilt = float(np.degrees(np.arccos(np.clip(abs(axis @ in_plane), -1, 1))))
    if tilt < min_degrees:
        return mesh, 0.0
    if axis @ in_plane < 0:
        axis = -axis
    rot_axis = np.cross(axis, in_plane)
    out = mesh.copy()
    out.apply_transform(trimesh.transformations.rotation_matrix(np.radians(tilt), rot_axis, point=pts.mean(axis=0)))
    return out, tilt
