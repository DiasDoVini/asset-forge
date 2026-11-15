"""PBR texture sets: base color, metallic, roughness and a baked tangent-space normal map (glTF conventions).

glTF packs metallic and roughness in one texture (G = roughness, B = metallic); normal maps are OpenGL style
(+Y / green = toward +V). Separate grayscale files are also produced for engines that want them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh
from PIL import Image
from scipy.spatial import cKDTree

from .texture import dilate, rasterize_uv, texture_image

DEFAULT_ROUGHNESS = 0.85
DEFAULT_METALLIC = 0.0


@dataclass
class PBRMaps:
    base: Image.Image                     # RGBA
    metallic: Image.Image | None = None   # L, 0..255 = 0..1
    roughness: Image.Image | None = None  # L
    normal: Image.Image | None = None     # RGB, tangent space

    def packed_mr(self) -> Image.Image | None:
        """glTF metallicRoughness texture (R unused/white, G roughness, B metallic)."""
        if self.metallic is None and self.roughness is None:
            return None
        size = (self.metallic or self.roughness).size
        r = Image.new("L", size, 255)
        g = self.roughness or Image.new("L", size, int(DEFAULT_ROUGHNESS * 255))
        b = self.metallic or Image.new("L", size, int(DEFAULT_METALLIC * 255))
        return Image.merge("RGB", (r, g.resize(size), b.resize(size)))


def split_mr(mr: Image.Image) -> tuple[Image.Image, Image.Image]:
    """(metallic, roughness) from a glTF-packed metallicRoughness image."""
    _, g, b = mr.convert("RGB").split()
    return b, g


def maps_of(mesh: trimesh.Trimesh) -> PBRMaps | None:
    """The PBR maps a textured mesh carries (None without a base color texture)."""
    base = texture_image(mesh)
    if base is None:
        return None
    mat = mesh.visual.material
    mr = getattr(mat, "metallicRoughnessTexture", None)
    normal = getattr(mat, "normalTexture", None)
    metallic = roughness = None
    if isinstance(mr, Image.Image):
        metallic, roughness = split_mr(mr)
    return PBRMaps(base.convert("RGBA"), metallic, roughness, normal.convert("RGB") if isinstance(normal, Image.Image) else None)


def with_maps(mesh: trimesh.Trimesh, maps: PBRMaps, name: str = "pbr") -> trimesh.Trimesh:
    """Copy of ``mesh`` (which has UVs) with a glTF PBR material made of ``maps``."""
    mr = maps.packed_mr()
    material = trimesh.visual.material.PBRMaterial(
        name=name, baseColorTexture=maps.base,
        metallicRoughnessTexture=mr, normalTexture=maps.normal,
        metallicFactor=1.0 if mr is not None else DEFAULT_METALLIC,
        roughnessFactor=1.0 if mr is not None else DEFAULT_ROUGHNESS)
    out = mesh.copy()
    out.visual = trimesh.visual.TextureVisuals(uv=mesh.visual.uv, material=material)
    return out


def uniform(size: tuple[int, int], value: float) -> Image.Image:
    return Image.new("L", size, int(round(np.clip(value, 0, 1) * 255)))


# ---- normal map bake ----------------------------------------------------------------------------

def align_highres(high: trimesh.Trimesh, low: trimesh.Trimesh, max_cost: float = 2e-3) -> tuple[trimesh.Trimesh | None, float]:
    """Bring a high-resolution version of ``low`` (raw generator output, any scale/placement) onto it:
    bounding boxes first, then ICP with scale. Returns (aligned copy or None if it does not fit, ICP cost
    relative to the model size)."""
    from .repair import drop_fragments

    core, _ = drop_fragments(high, min_ratio=0.05)          # floaters would skew the bounding box
    s = float(np.linalg.norm(low.extents) / max(np.linalg.norm(core.extents), 1e-12))
    init = trimesh.transformations.translation_matrix(low.bounds.mean(axis=0)) @ np.diag([s, s, s, 1.0]) @ \
        trimesh.transformations.translation_matrix(-core.bounds.mean(axis=0))
    pts = core.sample(8000) if len(core.faces) else core.vertices
    pts = trimesh.transform_points(pts, init)
    matrix, _, cost = trimesh.registration.icp(pts, low, max_iterations=40, scale=True)
    rel = float(cost) / max(float(np.linalg.norm(low.extents)) ** 2, 1e-12)
    if rel > max_cost:
        return None, rel
    out = high.copy()
    out.apply_transform(matrix @ init)
    return out, rel


def tangent_frames(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """Per-face tangent (+U) and bitangent (+V) directions from positions and UVs."""
    p = mesh.vertices[mesh.faces]
    uv = np.asarray(mesh.visual.uv)[mesh.faces]
    e1, e2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    d1, d2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(det) > 1e-12, 1.0 / np.where(det == 0, 1, det), 0.0)[:, None]
    t = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
    b = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r
    return t, b


def bake_normal_map(low: trimesh.Trimesh, high: trimesh.Trimesh, size: tuple[int, int], k: int = 4) -> Image.Image:
    """Tangent-space normal map of ``high``'s surface detail on ``low``'s UV layout (both in the same space).

    Each texel takes the smooth normal of the nearest high-resolution surface points (inverse-distance
    weighted) and expresses it in the low mesh's tangent frame there."""
    texels = rasterize_uv(low, size)
    rows, cols = np.nonzero(texels.mask)
    f = texels.tri[rows, cols]
    bary = texels.bary[rows, cols].astype(np.float64)[..., None]
    corners = low.faces[f]
    pts = (low.vertices[corners] * bary).sum(1)
    n_low = (low.vertex_normals[corners] * bary).sum(1)
    n_low /= np.maximum(np.linalg.norm(n_low, axis=1, keepdims=True), 1e-12)

    hv = np.asarray(high.vertices)
    hn = np.asarray(high.vertex_normals)
    dist, idx = cKDTree(hv).query(pts, k=k, workers=-1)
    w = 1.0 / np.maximum(dist, 1e-9)
    n_high = (hn[idx] * w[..., None]).sum(1)
    n_high /= np.maximum(np.linalg.norm(n_high, axis=1, keepdims=True), 1e-12)
    # keep the low normal where the nearest high surface faces away (thin parts, the other side of a cape)
    flip = (n_high * n_low).sum(1) < 0.2
    n_high[flip] = n_low[flip]

    t_face, b_face = tangent_frames(low)
    t = t_face[f] - n_low * (t_face[f] * n_low).sum(1, keepdims=True)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    handed = np.sign((np.cross(n_low, t) * b_face[f]).sum(1))
    handed[handed == 0] = 1.0
    b = np.cross(n_low, t) * handed[:, None]
    ts = np.stack([(n_high * t).sum(1), (n_high * b).sum(1), (n_high * n_low).sum(1)], axis=1)
    ts /= np.maximum(np.linalg.norm(ts, axis=1, keepdims=True), 1e-12)

    w_, h_ = size
    img = np.zeros((h_, w_, 3), dtype=np.uint8)
    img[...] = (128, 128, 255)
    img[rows, cols] = np.clip((ts * 0.5 + 0.5) * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(dilate(img, texels.mask), "RGB")
