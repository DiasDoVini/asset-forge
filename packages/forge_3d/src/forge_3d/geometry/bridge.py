"""Conversions between trimesh, pymeshlab and manifold3d representations."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pymeshlab
import trimesh
import manifold3d as m3d
from PIL import Image


def has_uv(mesh: trimesh.Trimesh) -> bool:
    uv = getattr(mesh.visual, "uv", None)
    return uv is not None and len(uv) == len(mesh.vertices)


def has_vertex_colors(mesh: trimesh.Trimesh) -> bool:
    return mesh.visual.kind == "vertex"


def weld(mesh: trimesh.Trimesh, digits: int = 6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Merge vertices by position only (ignoring UV seams).

    Returns (vertices, faces, inverse) where ``inverse`` maps original vertex
    indices to welded ones. Needed because trimesh splits vertices at UV seams,
    which looks like open boundaries to mesh-processing algorithms.
    """
    rounded = np.round(mesh.vertices, digits)
    _, first, inverse = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    return mesh.vertices[first], inverse[mesh.faces], inverse


def to_meshlab(mesh: trimesh.Trimesh, keep_uv: bool = True) -> tuple[pymeshlab.MeshSet, bool]:
    """Build a MeshSet from a trimesh. Returns (meshset, uv_kept).

    UVs are carried as wedge coordinates on a position-welded mesh so seams do
    not become holes.
    """
    vertices, faces, inverse = weld(mesh)
    if keep_uv and has_uv(mesh):
        return _meshlab_with_wedge_uv(vertices, faces, mesh.visual.uv[mesh.faces]), True
    kwargs: dict = {
        "vertex_matrix": np.asarray(vertices, dtype=np.float64),
        "face_matrix": np.asarray(faces, dtype=np.int32),
    }
    uv_kept = False
    if has_vertex_colors(mesh):
        colors = np.zeros((len(vertices), 4))
        colors[inverse] = mesh.visual.vertex_colors[:, :4] / 255.0
        kwargs["v_color_matrix"] = colors
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(**kwargs))
    return ms, uv_kept


def _meshlab_with_wedge_uv(vertices: np.ndarray, faces: np.ndarray, wedge_uv: np.ndarray) -> pymeshlab.MeshSet:
    """Load via a temporary OBJ: pymeshlab.Mesh() cannot set per-face texture ids, which the
    texture-aware filters require. The texture image itself is a 1px placeholder; the real
    material is restored from the source mesh in ``from_meshlab``."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        Image.new("RGB", (1, 1)).save(tmp / "t.png")
        (tmp / "m.mtl").write_text("newmtl m\nmap_Kd t.png\n")
        uv = wedge_uv.reshape(-1, 2)
        idx = np.arange(len(uv)).reshape(-1, 3) + 1
        f = np.stack([faces + 1, idx], axis=-1).reshape(len(faces), 6)
        with open(tmp / "m.obj", "w") as fh:
            fh.write("mtllib m.mtl\nusemtl m\n")
            np.savetxt(fh, vertices, fmt="v %.8f %.8f %.8f")
            np.savetxt(fh, uv, fmt="vt %.8f %.8f")
            np.savetxt(fh, f, fmt="f %d/%d %d/%d %d/%d")
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(str(tmp / "m.obj"))
    return ms


def from_meshlab(ms: pymeshlab.MeshSet, source: trimesh.Trimesh | None = None, uv: bool = False) -> trimesh.Trimesh:
    """Convert the current MeshSet mesh back to trimesh, restoring material/UVs."""
    m = ms.current_mesh()
    vertices = m.vertex_matrix()
    faces = m.face_matrix()
    if uv and source is not None and m.has_wedge_tex_coord():
        wedge = m.wedge_tex_coord_matrix().reshape(-1, 3, 2)
        # unweld per corner, then merge corners sharing position AND uv
        corner_v = vertices[faces].reshape(-1, 3)
        corner_uv = wedge.reshape(-1, 2)
        key = np.hstack([np.round(corner_v, 6), np.round(corner_uv, 6)])
        _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
        visual = trimesh.visual.TextureVisuals(uv=corner_uv[first], material=source.visual.material)
        return trimesh.Trimesh(corner_v[first], inverse.reshape(-1, 3), visual=visual, process=False)
    out = trimesh.Trimesh(vertices, faces, process=False)
    if m.has_vertex_color():
        out.visual.vertex_colors = (np.clip(m.vertex_color_matrix(), 0, 1) * 255).astype(np.uint8)
    return out


def to_manifold(mesh: trimesh.Trimesh) -> m3d.Manifold:
    vertices, faces, _ = weld(mesh)
    man = m3d.Manifold(m3d.Mesh(
        vert_properties=np.asarray(vertices, dtype=np.float32),
        tri_verts=np.asarray(faces, dtype=np.uint32),
    ))
    if man.status() != m3d.Error.NoError:
        raise ValueError(f"mesh is not a valid manifold solid: {man.status()}")
    return man


def from_manifold(man: m3d.Manifold) -> trimesh.Trimesh:
    mesh = man.to_mesh()
    return trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts), process=True)
