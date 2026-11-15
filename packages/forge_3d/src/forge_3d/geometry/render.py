"""Server-side preview renders (numpy rasterizer, no GL): shaded views, wireframe, contact sheets, UV sheet.

Orthographic cameras around the model (View convention: yaw 0 = front, camera on +Z; yaw +90 = the model's
left side). All views of a sheet share one scale, so proportions compare between them.
"""

from __future__ import annotations

import numpy as np
import trimesh
from PIL import Image, ImageDraw

from .io import shade_smooth
from .texture import View, _rasterize, has_uv_texture, texture_image

BACKGROUND = (232, 233, 236)
BASE_COLOR = np.array([196, 198, 204], dtype=np.float64)

# (label, yaw, pitch)
STANDARD_VIEWS = [
    ("frente", 0.0, 5.0),
    ("3/4 frente-esquerda", 40.0, 12.0),
    ("perfil esquerdo", 90.0, 0.0),
    ("costas", 180.0, 5.0),
    ("perfil direito", -90.0, 0.0),
    ("topo", 0.0, 80.0),
]


def _preview_mesh(mesh: trimesh.Trimesh, max_faces: int) -> trimesh.Trimesh:
    if len(mesh.faces) <= max_faces:
        return mesh
    from .simplify import simplify

    return simplify(mesh, target_faces=max_faces)


def _camera(mesh: trimesh.Trimesh, yaw: float, pitch: float, size: int, fill: float = 0.86) -> View:
    center = mesh.bounds.mean(axis=0)
    radius = float(np.linalg.norm(mesh.vertices - center, axis=1).max()) or 1.0
    return View(yaw, pitch, size * fill / (2 * radius), size / 2, size / 2, center)


def render_view(mesh: trimesh.Trimesh, yaw: float, pitch: float = 0.0, size: int = 512, wireframe: bool = False,
                max_faces: int = 80_000, background=BACKGROUND) -> Image.Image:
    """Shaded orthographic render; colors from the texture (UVs) or vertex colors when present."""
    mesh = _preview_mesh(mesh, max_faces)
    smooth = shade_smooth(mesh)
    view = _camera(smooth, yaw, pitch, size)
    xy, d = view.project(smooth.vertices)
    tex, _ = _rasterize(xy[smooth.faces], (size, size), depth=d[smooth.faces])
    hit = tex.tri >= 0
    faces = smooth.faces[tex.tri[hit]]
    bary = tex.bary[hit][..., None]
    n = (smooth.vertex_normals[faces] * bary).sum(1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)

    albedo = np.tile(BASE_COLOR, (len(faces), 1))
    if has_uv_texture(smooth):
        img = np.asarray(texture_image(smooth).convert("RGB"), dtype=np.float64)
        uv = (np.asarray(smooth.visual.uv)[faces] * bary).sum(1)
        h, w = img.shape[:2]
        px = np.clip((uv[:, 0] % 1.0) * (w - 1), 0, w - 1).astype(int)
        py = np.clip((1 - uv[:, 1] % 1.0) * (h - 1), 0, h - 1).astype(int)
        albedo = img[py, px]
    elif smooth.visual.kind == "vertex":
        cols = np.asarray(smooth.visual.vertex_colors, dtype=np.float64)[:, :3]
        if np.ptp(cols) > 0:  # all-white "default" colors carry no information
            albedo = (cols[faces] * bary).sum(1)

    right, up, fwd = view.basis()
    key = 0.45 * right + 0.65 * up - 0.6 * fwd
    fill = -0.6 * right + 0.1 * up - 0.8 * fwd
    key, fill = key / np.linalg.norm(key), fill / np.linalg.norm(fill)
    light = 0.28 + 0.62 * np.clip(n @ key, 0, 1) + 0.22 * np.clip(n @ fill, 0, 1)
    rim = 0.12 * np.clip(1 - np.abs(n @ fwd), 0, 1) ** 3
    rgb = np.empty((size, size, 3), dtype=np.float64)
    rgb[:] = background
    rgb[hit] = np.clip(albedo * light[:, None] + 255 * rim[:, None], 0, 255)
    out = Image.fromarray(rgb.astype(np.uint8))
    if wireframe:
        draw = ImageDraw.Draw(out)
        visible = np.unique(tex.tri[hit])
        edges = np.unique(np.sort(smooth.faces[visible][:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0)
        for a, b in xy[edges]:
            draw.line([tuple(a), tuple(b)], fill=(40, 40, 48), width=1)
    return out


def contact_sheet(tiles: list[tuple[str, Image.Image]], columns: int = 3, label_height: int = 22) -> Image.Image:
    """Grid of labeled tiles (same size)."""
    w, h = tiles[0][1].size
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (w * columns, (h + label_height) * rows), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate(tiles):
        x, y = (i % columns) * w, (i // columns) * (h + label_height)
        sheet.paste(img, (x, y + label_height))
        draw.text((x + 6, y + 5), label, fill=(30, 30, 36))
    return sheet


def views_sheet(mesh: trimesh.Trimesh, views=STANDARD_VIEWS, size: int = 384, columns: int = 3,
                wireframe: bool = False) -> Image.Image:
    return contact_sheet([(label, render_view(mesh, yaw, pitch, size, wireframe=wireframe)) for label, yaw, pitch in views],
                         columns=columns)


def uv_sheet(mesh: trimesh.Trimesh, size: int = 768) -> Image.Image | None:
    """The texture with the UV layout drawn over it (None when the mesh has no texture)."""
    if not has_uv_texture(mesh):
        return None
    img = texture_image(mesh).convert("RGB").resize((size, size))
    draw = ImageDraw.Draw(img)
    uv = np.asarray(mesh.visual.uv)
    pts = np.column_stack([uv[:, 0] * (size - 1), (1 - uv[:, 1]) * (size - 1)])
    faces = mesh.faces if len(mesh.faces) <= 60_000 else mesh.faces[:: len(mesh.faces) // 60_000 + 1]
    for tri in pts[faces]:
        draw.polygon([tuple(p) for p in tri], outline=(255, 255, 255))
    return img
