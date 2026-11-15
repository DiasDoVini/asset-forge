"""Region refinement: regenerate part of a mesh with more detail and stitch it back.

The AI side (crop -> upscale -> image-to-3D) lives in ``refine_pipelines``; this module does the geometry:
pick the region, choose the best camera, place the regenerated part over the base (known camera + ICP),
swap the region's surface and rebuild one closed surface (screened Poisson) with a blended seam.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymeshlab
import trimesh
from scipy.spatial import cKDTree

from .texture import View


def faces_near_points(mesh: trimesh.Trimesh, points, radius: float) -> np.ndarray:
    """Faces whose centers are within ``radius`` of any of ``points`` (brush stamps)."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(pts):
        return np.zeros(len(mesh.faces), bool)
    d, _ = cKDTree(pts).query(mesh.triangles_center, k=1)
    return d <= radius


def faces_in_rect(mesh: trimesh.Trimesh, view: View, rect) -> np.ndarray:
    """Faces whose centers project inside an image rectangle (x0, y0, x1, y1), at any depth."""
    xy, _ = view.project(mesh.triangles_center)
    x0, y0, x1, y1 = rect
    return (xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)


def dilate_faces(mesh: trimesh.Trimesh, mask: np.ndarray, rings: int = 1) -> np.ndarray:
    """Grow a face selection by ``rings`` of neighbouring faces."""
    out = mask.copy()
    adj = mesh.face_adjacency
    for _ in range(rings):
        grow = out.copy()
        grow[adj[out[adj[:, 0]], 1]] = True
        grow[adj[out[adj[:, 1]], 0]] = True
        out = grow
    return out


def visible_area(mesh: trimesh.Trimesh, mask: np.ndarray, view: View) -> float:
    """Area of the selected faces turned toward the camera (how well a view shows the region)."""
    _, _, fwd = view.basis()
    facing = mesh.face_normals[mask] @ -fwd
    return float((mesh.area_faces[mask] * np.clip(facing, 0, None)).sum())


def crop_rect(mesh: trimesh.Trimesh, mask: np.ndarray, view: View, image_size: tuple[int, int],
              margin: float = 0.35, min_px: int = 48, min_context: float = 0.3) -> tuple[int, int, int, int]:
    """Image rectangle around the projected region, with a margin of context, clamped to the image.

    The generator needs to recognise what it sees: a small region (a few brush strokes on the forehead) is
    widened to at least ``min_context`` of the whole model's size on screen (e.g. head and shoulders);
    only the region itself is replaced later."""
    xy, _ = view.project(mesh.vertices[np.unique(mesh.faces[mask])])
    (x0, y0), (x1, y1) = xy.min(axis=0), xy.max(axis=0)
    whole, _ = view.project(mesh.vertices)
    model_size = float(np.ptp(whole, axis=0).max())
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = max((x1 - x0) * (1 + 2 * margin), min_px, min_context * model_size)
    h = max((y1 - y0) * (1 + 2 * margin), min_px, min_context * model_size)
    W, H = image_size
    return (int(max(0, cx - w / 2)), int(max(0, cy - h / 2)), int(min(W, cx + w / 2)), int(min(H, cy + h / 2)))


@dataclass
class Placement:
    matrix: np.ndarray   # part -> world
    icp_cost: float
    scale: float         # ICP's extra scale (≈1 when the camera estimate was good)


def place_part(part: trimesh.Trimesh, view: View, subject_rect, base_near: trimesh.Trimesh,
               icp_iterations: int = 60) -> tuple[trimesh.Trimesh, Placement]:
    """Put a mesh generated from an image crop where that crop sits on the base mesh.

    ``subject_rect``: the subject's bounding box in the *source image* pixels (x0, y0, x1, y1) — the generated
    mesh fills it as seen from ``view``. ``base_near``: the base surface around the region (ICP target).
    """
    x0, y0, x1, y1 = subject_rect
    right, up, fwd = view.basis()
    out = part.copy()
    lo, hi = out.bounds
    out.apply_translation(-(lo + hi) / 2)
    ext = np.maximum(hi - lo, 1e-9)
    s = float(np.mean([(x1 - x0) / view.scale / ext[0], (y1 - y0) / view.scale / ext[1]]))
    out.apply_scale(s)
    rot = np.eye(4)
    rot[:3, :3] = np.column_stack([right, up, -fwd])  # generated meshes face +Z: x right, y up, z to the camera
    out.apply_transform(rot)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    target = view.center + right * (cx - view.cx) / view.scale - up * (cy - view.cy) / view.scale
    c = out.bounds.mean(axis=0)
    shift = target - c
    depth = base_near.bounds.mean(axis=0) @ fwd  # depth is unknown from the image: match the base's
    shift += fwd * (depth - (c + shift) @ fwd)
    out.apply_translation(shift)
    initial = trimesh.transformations.translation_matrix(shift) @ rot @ np.diag([s, s, s, 1.0]) @ \
        trimesh.transformations.translation_matrix(-(lo + hi) / 2)
    pts = out.sample(20000) if len(out.faces) else out.vertices
    matrix, _, cost = trimesh.registration.icp(pts, base_near, max_iterations=icp_iterations, scale=True)
    out.apply_transform(matrix)
    return out, Placement(matrix @ initial, float(cost), float(np.cbrt(abs(np.linalg.det(matrix[:3, :3])))))


def merge_region(base: trimesh.Trimesh, region: np.ndarray, part: trimesh.Trimesh, depth: int = 11,
                 band: float = 0.07, max_offset: float = 0.25, base_samples: int = 300_000,
                 detail: float = 6.0, max_part_samples: int = 400_000) -> trimesh.Trimesh:
    """Replace the base surface inside ``region`` (face mask) by the part's surface; one closed mesh out.

    One surface everywhere (two overlapping ones make Poisson build steps and shards): the base outside the
    region, the part inside it. Near the border the part is pulled onto the base, fully at the edge and fading
    out over ``band`` × the region size, so the seam is continuous. Part faces far from the region
    (> ``max_offset`` × region size: pieces the generator invented outside the crop) are dropped.
    """
    centers = base.triangles_center
    region_size = float(np.linalg.norm(np.ptp(centers[region], axis=0))) if region.any() else 1.0
    base_keep = base.submesh([np.nonzero(~region)[0]], append=True)
    dist, nearest = cKDTree(centers).query(part.triangles_center, k=1)
    keep = region[nearest] & (dist <= max_offset * region_size)
    part_in = part.submesh([np.nonzero(keep)[0]], append=True)

    # sample density: the region gets `detail` times the base's (capped), so Poisson resolves it finer
    # without exploding the face count when the region is tiny
    density = base_samples / max(base_keep.area, 1e-12)
    n_base = base_samples
    n_part = int(min(max_part_samples, max(2000, part_in.area * density * detail)))
    p1, f1 = trimesh.sample.sample_surface(base_keep, n_base, seed=1)
    p2, f2 = trimesh.sample.sample_surface(part_in, n_part, seed=2)
    n2 = part_in.face_normals[f2]

    # blend weight of each part point: 0 at the region border, 1 deeper than `band`
    outside = centers[~region]
    if len(outside):
        d_out, _ = cKDTree(outside).query(p2, k=1)
        w = np.clip(d_out / max(band * region_size, 1e-9), 0, 1)
        w = w * w * (3 - 2 * w)  # smoothstep
        near = w < 1
        if near.any():
            snapped, _, tri = trimesh.proximity.closest_point(base, p2[near])
            p2[near] = snapped + (p2[near] - snapped) * w[near, None]
            n_mix = base.face_normals[tri] * (1 - w[near, None]) + n2[near] * w[near, None]
            n2[near] = n_mix / np.maximum(np.linalg.norm(n_mix, axis=1, keepdims=True), 1e-12)

    pts = np.vstack([p1, p2])
    nrm = np.vstack([base_keep.face_normals[f1], n2])
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=pts, v_normals_matrix=nrm))
    ms.generate_surface_reconstruction_screened_poisson(depth=depth, preclean=True, samplespernode=1.5, pointweight=4)
    faces = ms.current_mesh().face_number()
    ms.meshing_remove_connected_component_by_face_number(mincomponentsize=int(faces * 0.01))
    ms.meshing_remove_unreferenced_vertices()
    m = ms.current_mesh()
    out = trimesh.Trimesh(m.vertex_matrix(), m.face_matrix(), process=True)
    from .repair import orient_outward

    out, _ = orient_outward(out)
    return out
