"""Stylized low-poly conversion: aggressive decimation + flat shading + flat per-face colors."""

from __future__ import annotations

from typing import Literal

import numpy as np
import trimesh
from PIL import Image

from .color import face_colors_from, quantize, rgba_to_hex
from .simplify import simplify

ColorMode = Literal["vertex", "palette_texture", "none"]


def lowpoly(
    mesh: trimesh.Trimesh,
    target_faces: int = 800,
    color_mode: ColorMode = "vertex",
    palette: list[str] | None = None,
    max_colors: int | None = 16,
    remesh: bool = False,
) -> tuple[trimesh.Trimesh, dict]:
    """Return (lowpoly_mesh, info).

    - ``vertex``: flat per-face vertex colors (works everywhere, no texture).
    - ``palette_texture``: tiny palette atlas + UVs pointing at color cells, the usual
      game-ready low-poly setup (one material, trivially recolorable).
    - ``palette`` snaps colors to the project palette; otherwise k-means to ``max_colors``.
    """
    work = mesh
    if remesh:
        work = _isotropic_remesh(mesh, target_faces * 8)
    lp = simplify(work, target_faces=target_faces, preserve_uv=False, preserve_boundary=True, planar=True)

    info: dict = {"faces": int(len(lp.faces)), "color_mode": color_mode}
    if color_mode == "none":
        flat = _unmerge(lp)
        return flat, info

    colors = quantize(face_colors_from(mesh, lp), palette=palette, max_colors=max_colors)
    unique, index = np.unique(colors, axis=0, return_inverse=True)
    index = index.reshape(-1)
    info["palette"] = [rgba_to_hex(c) for c in unique]

    if color_mode == "vertex":
        flat = _unmerge(lp)
        flat.visual.vertex_colors = np.repeat(colors, 3, axis=0)
        return flat, info

    # palette_texture
    cell = 8
    cols = int(np.ceil(np.sqrt(len(unique))))
    rows = int(np.ceil(len(unique) / cols))
    img = np.zeros((rows * cell, cols * cell, 4), dtype=np.uint8)
    for i, c in enumerate(unique):
        r, q = divmod(i, cols)
        img[r * cell:(r + 1) * cell, q * cell:(q + 1) * cell] = c
    r, q = np.divmod(index, cols)
    face_uv = np.stack([(q + 0.5) / cols, 1.0 - (r + 0.5) / rows], axis=1)
    material = trimesh.visual.material.PBRMaterial(
        name="palette",
        baseColorTexture=Image.fromarray(img, "RGBA"),
        metallicFactor=0.0,
        roughnessFactor=1.0,
    )
    flat = _unmerge(lp)
    flat.visual = trimesh.visual.TextureVisuals(uv=np.repeat(face_uv, 3, axis=0), material=material)
    info["palette_texture_size"] = [cols * cell, rows * cell]
    return flat, info


def _unmerge(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """One vertex per face corner, so every renderer shows hard (flat) edges."""
    vertices = mesh.vertices[mesh.faces].reshape(-1, 3)
    faces = np.arange(len(vertices)).reshape(-1, 3)
    return trimesh.Trimesh(vertices, faces, process=False)


def _isotropic_remesh(mesh: trimesh.Trimesh, approx_faces: int) -> trimesh.Trimesh:
    from .bridge import from_meshlab, to_meshlab
    import pymeshlab

    ms, _ = to_meshlab(mesh, keep_uv=False)
    # edge length giving roughly ``approx_faces`` equilateral triangles over the surface
    edge = float(np.sqrt(4 * mesh.area / (np.sqrt(3) * max(approx_faces, 100))))
    ms.meshing_isotropic_explicit_remeshing(iterations=4, targetlen=pymeshlab.PureValue(edge))
    return from_meshlab(ms)
