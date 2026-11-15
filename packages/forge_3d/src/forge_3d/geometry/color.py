"""Sampling surface color from textured / colored meshes, and palette helpers."""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.cluster.vq import kmeans2

from .analyze import _material_image
from .bridge import has_uv

DEFAULT_GRAY = np.array([180, 180, 180, 255], dtype=np.uint8)


def surface_colors(mesh: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """RGBA uint8 color of ``mesh`` at the surface point closest to each of ``points``."""
    points = np.asarray(points, dtype=np.float64)
    kind = mesh.visual.kind
    image = _material_image(mesh.visual.material) if kind == "texture" else None
    if kind not in ("vertex", "face") and not (image is not None and has_uv(mesh)):
        factor = _base_color_factor(mesh)
        return np.tile(factor if factor is not None else DEFAULT_GRAY, (len(points), 1))

    closest, _, tri = trimesh.proximity.closest_point(mesh, points)
    if kind == "face":
        return mesh.visual.face_colors[tri].astype(np.uint8)
    bary = trimesh.triangles.points_to_barycentric(mesh.triangles[tri], closest)
    if kind == "vertex":
        corner = mesh.visual.vertex_colors[mesh.faces[tri]].astype(np.float64)
        return np.clip((corner * bary[:, :, None]).sum(1), 0, 255).astype(np.uint8)
    uv = (mesh.visual.uv[mesh.faces[tri]] * bary[:, :, None]).sum(1)
    colors = trimesh.visual.color.uv_to_color(uv, image).astype(np.float64)
    factor = _base_color_factor(mesh)
    if factor is not None:
        colors = colors * (factor / 255.0)
    return np.clip(colors, 0, 255).astype(np.uint8)


def _base_color_factor(mesh: trimesh.Trimesh) -> np.ndarray | None:
    factor = getattr(getattr(mesh.visual, "material", None), "baseColorFactor", None)
    if factor is None:
        return None
    factor = np.asarray(factor, dtype=np.float64)
    if factor.max() <= 1.0:
        factor = factor * 255
    return factor.astype(np.uint8)


def face_colors_from(source: trimesh.Trimesh, target: trimesh.Trimesh) -> np.ndarray:
    """Average source surface color over a few sample points inside each target face."""
    weights = np.array([[1 / 3, 1 / 3, 1 / 3], [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6]])
    tris = target.triangles  # (F, 3, 3)
    pts = np.einsum("sk,fkd->fsd", weights, tris).reshape(-1, 3)
    cols = surface_colors(source, pts).reshape(len(tris), len(weights), 4).astype(np.float64)
    return cols.mean(axis=1).astype(np.uint8)


def hex_to_rgba(value: str) -> np.ndarray:
    value = value.lstrip("#")
    if len(value) == 6:
        value += "ff"
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4, 6)], dtype=np.uint8)


def rgba_to_hex(color) -> str:
    return "#" + "".join(f"{int(c):02x}" for c in color[:3])


def quantize(colors: np.ndarray, palette: list[str] | None = None, max_colors: int | None = 16, seed: int = 0) -> np.ndarray:
    """Snap colors to a fixed palette (hex strings) or to ``max_colors`` k-means clusters."""
    colors = np.asarray(colors, dtype=np.uint8)
    if palette:
        pal = np.stack([hex_to_rgba(p) for p in palette]).astype(np.float64)
        d = ((colors[:, None, :3].astype(np.float64) - pal[None, :, :3]) ** 2).sum(-1)
        return pal[d.argmin(1)].astype(np.uint8)
    if not max_colors:
        return colors
    unique = np.unique(colors, axis=0)
    if len(unique) <= max_colors:
        return colors
    data = colors[:, :3].astype(np.float64)
    centroids, labels = kmeans2(data, max_colors, minit="++", seed=seed)
    out = colors.copy()
    out[:, :3] = np.clip(np.round(centroids[labels]), 0, 255).astype(np.uint8)
    return out
