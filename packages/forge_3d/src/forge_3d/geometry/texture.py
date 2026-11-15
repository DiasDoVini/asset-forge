"""Texturing: UV unwrap (xatlas) + bake existing colors, texel rasterization, image projection.

Everything works in the mesh's own (Y-up) space. Textures are RGBA uint8 arrays, row 0 = top (PIL),
and UV v=0 is the bottom of the image (glTF/trimesh convention), so ``row = (1 - v) * H``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh
from PIL import Image
from scipy import ndimage

from .bridge import weld

DEFAULT_GRAY = (190, 190, 190, 255)


# ---- UV unwrap -----------------------------------------------------------------------------

@dataclass
class Unwrapped:
    mesh: trimesh.Trimesh       # new vertices (split at UV seams) with uv
    size: tuple[int, int]       # texture (width, height)
    source_faces: np.ndarray    # (F, 3) corner vertex indices into the *source* mesh, per new face


def unwrap(mesh: trimesh.Trimesh, resolution: int = 2048, padding: int = 4) -> Unwrapped:
    import xatlas

    pos, faces, _ = weld(mesh)  # weld so old UV seams don't fragment the charts
    atlas = xatlas.Atlas()
    atlas.add_mesh(np.asarray(pos, dtype=np.float32), np.asarray(faces, dtype=np.uint32))
    pack = xatlas.PackOptions()
    pack.resolution = int(resolution)
    pack.padding = int(padding)
    pack.bilinear = True
    atlas.generate(pack_options=pack)
    vmap, idx, uvs = atlas.get_mesh(0)
    k = resolution / max(atlas.width, atlas.height)
    size = (max(8, int(round(atlas.width * k))), max(8, int(round(atlas.height * k))))
    new = trimesh.Trimesh(pos[vmap], idx.astype(np.int64), process=False)
    new.visual = trimesh.visual.TextureVisuals(uv=uvs.astype(np.float64))
    return Unwrapped(new, size, np.asarray(mesh.faces))  # xatlas keeps the face order


# ---- texel rasterization ----------------------------------------------------------------------

@dataclass
class Texels:
    """Which triangle covers each texel and where (barycentric)."""
    tri: np.ndarray    # (H, W) int32, -1 = empty
    bary: np.ndarray   # (H, W, 3) float32

    @property
    def mask(self) -> np.ndarray:
        return self.tri >= 0


def rasterize_uv(mesh: trimesh.Trimesh, size: tuple[int, int]) -> Texels:
    w, h = size
    uv = mesh.visual.uv[mesh.faces]  # (F, 3, 2)
    px = np.stack([uv[..., 0] * w - 0.5, (1.0 - uv[..., 1]) * h - 0.5], axis=-1)
    return _rasterize(px, (w, h))


def _rasterize(px: np.ndarray, size: tuple[int, int], depth: np.ndarray | None = None):
    """Rasterize (F, 3, 2) pixel-space triangles. Returns Texels, or (Texels, zbuffer) when depth is given
    (per-corner depths, smaller = closer)."""
    w, h = size
    tri = np.full((h, w), -1, dtype=np.int32)
    bary = np.zeros((h, w, 3), dtype=np.float32)
    zbuf = np.full((h, w), np.inf, dtype=np.float32) if depth is not None else None
    lo = np.floor(px.min(axis=1)).astype(int)
    hi = np.ceil(px.max(axis=1)).astype(int)
    lo = np.clip(lo, 0, [w - 1, h - 1])
    hi = np.clip(hi, 0, [w - 1, h - 1])
    a, b, c = px[:, 0], px[:, 1], px[:, 2]
    den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    for f in np.nonzero(np.abs(den) > 1e-12)[0]:
        x0, y0 = lo[f]
        x1, y1 = hi[f]
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        l0 = ((b[f, 1] - c[f, 1]) * (xs - c[f, 0]) + (c[f, 0] - b[f, 0]) * (ys - c[f, 1])) / den[f]
        l1 = ((c[f, 1] - a[f, 1]) * (xs - c[f, 0]) + (a[f, 0] - c[f, 0]) * (ys - c[f, 1])) / den[f]
        l2 = 1.0 - l0 - l1
        inside = (l0 >= -1e-4) & (l1 >= -1e-4) & (l2 >= -1e-4)
        if not inside.any():
            continue
        yy, xx = ys[inside], xs[inside]
        bc = np.stack([l0[inside], l1[inside], l2[inside]], axis=-1)
        if zbuf is not None:
            z = bc @ depth[f]
            closer = z < zbuf[yy, xx]
            yy, xx, bc, z = yy[closer], xx[closer], bc[closer], z[closer]
            zbuf[yy, xx] = z
        tri[yy, xx] = f
        bary[yy, xx] = bc
    texels = Texels(tri, bary)
    return (texels, zbuf) if zbuf is not None else texels


def texel_points(mesh: trimesh.Trimesh, texels: Texels) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(rows, cols, xyz) of covered texels, plus their face normals."""
    rows, cols = np.nonzero(texels.mask)
    f = texels.tri[rows, cols]
    corners = mesh.vertices[mesh.faces[f]]  # (N, 3, 3)
    p = np.einsum("nk,nkd->nd", texels.bary[rows, cols].astype(np.float64), corners)
    return rows, cols, p, mesh.face_normals[f]


def dilate(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill uncovered texels with the nearest covered color (avoids dark seams with mipmapping)."""
    if mask.all() or not mask.any():
        return img
    _, (iy, ix) = ndimage.distance_transform_edt(~mask, return_indices=True)
    return img[iy, ix]


# ---- bake existing colors into a new atlas -----------------------------------------------------------

def bake_source_colors(source: trimesh.Trimesh, un: Unwrapped, texels: Texels) -> np.ndarray:
    """Texture (H, W, 4) holding the source mesh's current look (texture, vertex or face colors)."""
    w, h = un.size
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[...] = DEFAULT_GRAY
    rows, cols = np.nonzero(texels.mask)
    f = texels.tri[rows, cols]
    bc = texels.bary[rows, cols].astype(np.float64)
    corners = un.source_faces[f]  # source vertex indices per corner
    kind = source.visual.kind
    if kind == "vertex":
        vc = source.visual.vertex_colors.astype(np.float64)
        img[rows, cols] = np.clip(np.einsum("nk,nkc->nc", bc, vc[corners]), 0, 255).astype(np.uint8)
    elif kind == "face":
        img[rows, cols] = source.visual.face_colors[f]
    elif kind == "texture" and getattr(source.visual, "uv", None) is not None:
        from .analyze import _material_image

        tex = _material_image(source.visual.material)
        if tex is not None:
            uv = np.einsum("nk,nkd->nd", bc, source.visual.uv[corners])
            img[rows, cols] = trimesh.visual.color.uv_to_color(uv, tex)
        else:
            factor = getattr(source.visual.material, "baseColorFactor", None)
            if factor is not None:
                img[rows, cols] = np.asarray(factor)[:4]
    return dilate(img, texels.mask)


def with_texture(un_mesh: trimesh.Trimesh, image: np.ndarray | Image.Image) -> trimesh.Trimesh:
    """New base color on ``un_mesh``'s UVs. Its metallic/roughness and normal maps (same UV layout) are kept."""
    img = image if isinstance(image, Image.Image) else Image.fromarray(np.asarray(image, dtype=np.uint8), "RGBA")
    old = getattr(un_mesh.visual, "material", None)
    mr = getattr(old, "metallicRoughnessTexture", None)
    normal = getattr(old, "normalTexture", None)
    material = trimesh.visual.material.PBRMaterial(
        name="painted", baseColorTexture=img,
        metallicRoughnessTexture=mr if isinstance(mr, Image.Image) else None,
        normalTexture=normal if isinstance(normal, Image.Image) else None,
        metallicFactor=1.0 if isinstance(mr, Image.Image) else 0.0,
        roughnessFactor=1.0 if isinstance(mr, Image.Image) else 0.85)
    out = un_mesh.copy()
    out.visual = trimesh.visual.TextureVisuals(uv=un_mesh.visual.uv, material=material)
    return out


def texture_image(mesh: trimesh.Trimesh) -> Image.Image | None:
    from .analyze import _material_image

    if mesh.visual.kind != "texture" or getattr(mesh.visual, "uv", None) is None:
        return None
    return _material_image(mesh.visual.material)


def has_uv_texture(mesh: trimesh.Trimesh) -> bool:
    return texture_image(mesh) is not None


def transfer_texture(target: trimesh.Trimesh, target_texels: Texels, base: np.ndarray, source_uv_mesh: trimesh.Trimesh,
                     source_image: Image.Image, face_offset: int) -> np.ndarray:
    """Copy colors from a texture laid out on ``source_uv_mesh`` (whose faces ``face_offset..`` are the
    target's faces, in order - e.g. one part of a merged mesh) into the target's own UV atlas."""
    out = base.copy()
    rows, cols = np.nonzero(target_texels.mask)
    f = target_texels.tri[rows, cols] + face_offset
    bc = target_texels.bary[rows, cols].astype(np.float64)
    uv = np.einsum("nk,nkd->nd", bc, source_uv_mesh.visual.uv[source_uv_mesh.faces[f]])
    out[rows, cols] = trimesh.visual.color.uv_to_color(uv, source_image.convert("RGBA"))
    out[rows, cols, 3] = 255
    return dilate(out, target_texels.mask)


# ---- orthographic views, silhouettes, projection -----------------------------------------------------

@dataclass
class View:
    yaw: float      # degrees around +Y; 0 = camera on +Z looking at -Z (the model's front)
    pitch: float    # degrees, camera above the horizon when > 0
    scale: float    # pixels per unit
    cx: float       # image position of the view center
    cy: float
    center: np.ndarray

    def basis(self):
        y, p = np.radians(self.yaw), np.radians(self.pitch)
        fwd = -np.array([np.sin(y) * np.cos(p), np.sin(p), np.cos(y) * np.cos(p)])  # camera -> scene
        right = np.cross(fwd, [0.0, 1.0, 0.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        return right, up, fwd

    def project(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(pixel xy, depth along the view) for world points."""
        right, up, fwd = self.basis()
        q = pts - self.center
        xy = np.stack([self.cx + (q @ right) * self.scale, self.cy - (q @ up) * self.scale], axis=-1)
        return xy, q @ fwd


def depth_map(mesh: trimesh.Trimesh, view: View, size: tuple[int, int]) -> np.ndarray:
    xy, d = view.project(mesh.vertices)
    _, z = _rasterize(xy[mesh.faces], size, depth=d[mesh.faces])
    return z


def silhouette(mesh: trimesh.Trimesh, view: View, size: tuple[int, int]) -> np.ndarray:
    """Binary silhouette (no depth needed): every projected triangle filled with PIL - fast."""
    from PIL import ImageDraw

    xy, _ = view.project(mesh.vertices)
    img = Image.new("1", size, 0)
    draw = ImageDraw.Draw(img)
    for tri in xy[mesh.faces]:
        draw.polygon([tuple(p) for p in tri], fill=1)
    return np.asarray(img, dtype=bool)


def fit_view(mesh: trimesh.Trimesh, yaw: float, pitch: float, target_mask: np.ndarray) -> tuple[View, float]:
    """Orthographic view at (yaw, pitch) whose silhouette best overlaps ``target_mask``; returns (view, IoU)."""
    h, w = target_mask.shape
    ys, xs = np.nonzero(target_mask)
    if not len(xs):
        raise ValueError("image has no subject (empty mask)")
    center = mesh.bounds.mean(axis=0)
    probe = View(yaw, pitch, 1.0, 0.0, 0.0, center)
    xy, _ = probe.project(mesh.vertices)
    (mx0, my0), (mx1, my1) = xy.min(axis=0), xy.max(axis=0)
    # match the silhouette's bounding box to the subject's (height first: images keep proportions)
    tw, th = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    s = min(th / max(my1 - my0, 1e-9), tw / max(mx1 - mx0, 1e-9))
    view = View(yaw, pitch, s, 0.0, 0.0, center)
    xy, _ = view.project(mesh.vertices)
    view.cx = (xs.min() + xs.max()) / 2 - (xy[:, 0].min() + xy[:, 0].max()) / 2
    view.cy = (ys.min() + ys.max()) / 2 - (xy[:, 1].min() + xy[:, 1].max()) / 2
    sil = silhouette(mesh, view, (w, h))
    inter = (sil & target_mask).sum()
    union = (sil | target_mask).sum()
    return view, float(inter / max(union, 1))


def align_view(mesh: trimesh.Trimesh, mask: np.ndarray, yaws, pitches=(0, 10, 20, 30), work: int = 192) -> tuple[View, float]:
    """Search yaw/pitch for the camera that produced ``mask`` (subject silhouette of an image)."""
    h, w = mask.shape
    k = work / max(h, w)
    small = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((max(8, int(w * k)), max(8, int(h * k))), Image.NEAREST)) > 127
    best = None
    for yaw in yaws:
        for pitch in pitches:
            view, iou = fit_view(mesh, float(yaw), float(pitch), small)
            if best is None or iou > best[1]:
                best = (view, iou)
    view, iou = best
    # refine around the best yaw, then scale the view back to the full image
    for yaw in np.arange(view.yaw - 8, view.yaw + 9, 2):
        v, i = fit_view(mesh, float(yaw), view.pitch, small)
        if i > iou:
            view, iou = v, i
    view.scale /= k
    view.cx = (view.cx + 0.5) / k - 0.5
    view.cy = (view.cy + 0.5) / k - 0.5
    return view, iou


def subject_mask(img: Image.Image, white_threshold: int = 244) -> np.ndarray:
    """Subject pixels of an image: alpha if present, else everything that isn't near-white background."""
    if img.mode in ("RGBA", "LA") and np.asarray(img.getchannel("A")).min() < 250:
        return np.asarray(img.getchannel("A")) > 127
    rgb = np.asarray(img.convert("RGB"))
    mask = ~np.all(rgb >= white_threshold, axis=-1)
    return ndimage.binary_opening(mask, iterations=2)


def strip_outline(img: Image.Image, **kw) -> tuple[Image.Image, int]:
    """Remove a uniform outline/halo around the subject (sticker-style art) and composite it on white.
    Returns (clean RGB image, outline width removed in px; 0 = no outline found). See ``outline_mask``."""
    keep, width = outline_mask(img, **kw)
    if not width:
        return img.convert("RGB"), 0
    rgb = np.asarray(img.convert("RGB"))
    out = np.full_like(rgb, 255)
    out[keep] = rgb[keep]
    return Image.fromarray(out), width


def outline_mask(img: Image.Image, max_px: int | None = None, tolerance: float = 30.0, share: float = 0.6,
                 min_rings: int = 3) -> tuple[np.ndarray, int]:
    """The subject without its sticker-style outline/halo: (mask, outline width in px; 0 = no outline found,
    mask = the plain subject mask).

    Peels the silhouette ring by ring while each ring is mostly one color, the same color as the outline so
    far; the outline ends at the first ring that is varied or a different color. Real subjects have varied
    colors at their edge and stop right away; a solid-colored subject never ends within ``max_px`` and is
    left alone. The outermost antialiased ring is ignored, and one transition ring is removed after the outline.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
    mask = subject_mask(img)
    if not mask.any():
        return mask, 0
    max_px = max_px or max(4, int(0.06 * max(mask.shape)))

    def ring_color(ring: np.ndarray) -> np.ndarray | None:
        med = np.median(rgb[ring], axis=0)
        return med if (np.linalg.norm(rgb[ring] - med, axis=1) < tolerance).mean() >= share else None

    current, width, colors, ended = mask, 0, [], False
    for k in range(max_px):
        inner = ndimage.binary_erosion(current)
        ring = current & ~inner
        if not ring.any() or not inner.any():
            break
        if k > 0:  # ring 0 is antialiasing against the background: peeled, never used as the outline color
            color = ring_color(ring)
            same = color is not None and (not colors or np.linalg.norm(color - np.median(colors, axis=0)) < tolerance)
            if same:
                colors.append(color)
            elif colors:  # varied or a new color after the outline: the subject starts here
                ended = True
                break
            else:  # varied right below the edge: no outline
                break
        current, width = inner, width + 1
    if not ended or len(colors) < min_rings:
        return mask, 0
    current = ndimage.binary_erosion(current)  # transition between outline and subject
    width += 1
    # the halo fills concave gaps (between legs, inside curls) wider than the band: remove outline-colored
    # pixels connected to the stripped band too
    outline_color = np.median(colors, axis=0)
    halo = current & (np.linalg.norm(rgb - outline_color, axis=2) < tolerance)
    labels, _ = ndimage.label(halo | (mask & ~current))
    touching = np.unique(labels[mask & ~current])
    current &= ~(np.isin(labels, touching[touching > 0]) & halo)
    return ndimage.binary_opening(current, iterations=1), width


def sampling_mask(img: Image.Image, edge_margin: float = 0.025) -> np.ndarray:
    """Subject pixels safe to sample colors from: away from the edge (background bleed, outlines, small
    misalignments), but never wiping out thin parts — the margin shrinks to 40% of the local thickness
    (a 15 px staff keeps its middle)."""
    mask = subject_mask(img)
    margin = max(2.0, edge_margin * max(mask.shape))
    dist = ndimage.distance_transform_edt(mask)
    local = ndimage.maximum_filter(dist, size=int(2 * margin) + 1)  # half the local thickness
    return mask & (dist >= np.clip(0.4 * local, 1.0, margin))


def local_align(mesh: trimesh.Trimesh, view: View, img: Image.Image, bands: int = 8, search: float = 0.04,
                work: int = 320) -> tuple[Image.Image, float]:
    """Warp ``img`` so its subject lines up with the mesh silhouette band by band (head, torso, legs...).

    The global camera fit is orthographic; concept art is drawn/rendered with perspective and slight
    pose differences, so e.g. the head can sit a few pixels off while the body matches. Each horizontal band
    gets its own shift (searched within ``search`` × subject height), smoothed across bands.
    Returns (warped image, largest shift in pixels)."""
    W, H = img.size
    k = work / max(W, H)
    w, h = max(8, int(W * k)), max(8, int(H * k))
    small_view = View(view.yaw, view.pitch, view.scale * k, (view.cx + 0.5) * k - 0.5, (view.cy + 0.5) * k - 0.5, view.center)
    target = silhouette(mesh, small_view, (w, h))
    src = np.asarray(Image.fromarray(subject_mask(img).astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 127
    ys = np.nonzero(target.any(axis=1))[0]
    if len(ys) < bands * 2:
        return img, 0.0
    y0, y1 = ys.min(), ys.max() + 1
    r = max(1, int(search * (y1 - y0)))
    edges = np.linspace(y0, y1, bands + 1).astype(int)
    centers, shifts = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        pad = (b - a) // 2
        lo, hi = max(0, a - pad), min(h, b + pad)
        tgt = target[lo:hi]
        if tgt.sum() < 10:
            continue
        best, best_iou = (0, 0), -1.0
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                s = np.roll(np.roll(src, -dy, axis=0), -dx, axis=1)[lo:hi]
                inter = np.logical_and(s, tgt).sum()
                iou = inter / max(np.logical_or(s, tgt).sum(), 1)
                # prefer small shifts on ties
                score = iou - 1e-4 * (abs(dx) + abs(dy))
                if score > best_iou:
                    best, best_iou = (dx, dy), score
        centers.append((a + b) / 2)
        shifts.append(best)
    if not shifts:
        return img, 0.0
    shifts = np.asarray(shifts, dtype=np.float64)
    if len(shifts) >= 3:  # one odd band should not tear the image
        shifts = ndimage.median_filter(shifts, size=(3, 1), mode="nearest")
    rows = np.arange(H) * k
    dx = np.interp(rows, centers, shifts[:, 0]) / k
    dy = np.interp(rows, centers, shifts[:, 1]) / k
    if np.abs(np.concatenate([dx, dy])).max() < 0.5:
        return img, 0.0
    arr = np.asarray(img.convert("RGB"), dtype=np.float64)
    gy, gx = np.mgrid[0:H, 0:W].astype(np.float64)
    sx, sy = gx + dx[:, None], gy + dy[:, None]
    out = np.stack([ndimage.map_coordinates(arr[..., c], [sy, sx], order=1, mode="nearest") for c in range(3)], axis=-1)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), float(np.abs(np.concatenate([dx, dy])).max())


def project_images(meshes: list[trimesh.Trimesh], texel_sets: list[Texels], views: list[tuple[Image.Image, View, float]],
                   bases: list[np.ndarray], occluder: trimesh.Trimesh | None = None,
                   fill_unseen: bool = True, edge_margin: float = 0.025) -> list[np.ndarray]:
    """Project view images onto the texels of each mesh (visibility-tested), blending by facing angle.

    ``views``: (image, view, weight). ``bases``: textures to start from (kept where nothing projects).
    ``occluder``: geometry used for visibility (default: all meshes merged).
    """
    occluder = occluder or trimesh.util.concatenate(meshes)
    prepared = []
    for img, view, weight in views:
        rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
        # sample colors only well inside the subject: edges carry background bleed, outlines/halos
        # (sticker-style art) and small alignment errors
        mask = sampling_mask(img, edge_margin)
        z = depth_map(occluder, view, (rgb.shape[1], rgb.shape[0]))
        prepared.append((rgb, mask, z, view, weight))
    out = []
    for mesh, texels, base in zip(meshes, texel_sets, bases):
        rows, cols, pts, normals = texel_points(mesh, texels)
        acc = np.zeros((len(pts), 3))
        wsum = np.zeros(len(pts))
        extent = float(np.linalg.norm(occluder.extents))
        for rgb, mask, z, view, weight in prepared:
            _, _, fwd = view.basis()
            facing = np.clip(-(normals @ fwd), 0, 1)
            xy, d = view.project(pts)
            px = np.round(xy).astype(int)
            h, w = z.shape
            ok = (facing > 0.05) & (px[:, 0] >= 0) & (px[:, 0] < w) & (px[:, 1] >= 0) & (px[:, 1] < h)
            idx = np.nonzero(ok)[0]
            visible = np.abs(d[idx] - z[px[idx, 1], px[idx, 0]]) < extent * 0.01
            visible &= mask[px[idx, 1], px[idx, 0]]
            idx = idx[visible]
            wgt = weight * facing[idx] ** 2
            acc[idx] += rgb[px[idx, 1], px[idx, 0]] * wgt[:, None]
            wsum[idx] += wgt
        img = base.copy()
        seen = wsum > 1e-6
        img[rows[seen], cols[seen], :3] = np.clip(acc[seen] / wsum[seen, None], 0, 255).astype(np.uint8)
        img[rows[seen], cols[seen], 3] = 255
        if fill_unseen and seen.any() and (~seen).any():
            # unseen surface takes the color of the nearest *seen surface point in 3D* (texture-space
            # neighbors can belong to unrelated UV islands)
            from scipy.spatial import cKDTree

            _, nearest = cKDTree(pts[seen]).query(pts[~seen], k=1, workers=-1)
            src_r, src_c = rows[seen][nearest], cols[seen][nearest]
            img[rows[~seen], cols[~seen]] = img[src_r, src_c]
        out.append(dilate(img, texels.mask))
    return out
