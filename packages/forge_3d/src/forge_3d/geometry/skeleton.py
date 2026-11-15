"""Skeletons for generated meshes: templates, automatic fitting, skin weights (bone heat) and skinned glTF.

Conventions (same as ``anim``: Y-up, models face +Z, the model's left side is +X):
- A joint is a point; a bone goes from a joint to its children. Bones bind with identity rotation, so a
  joint's rest translation is ``joint - parent joint`` and clips (offsets from rest, XYZ Euler) apply as-is.
- Humanoids use Mixamo names and hierarchy (Hips, Spine, ..., LeftArm, LeftForeArm, LeftHand, LeftUpLeg...), so
  Mixamo animations and engine humanoid retargeting (Unity Humanoid, Unreal IK Retargeter) map directly.
- ``*_End`` joints only mark where a bone ends (head top, toe tip): they carry no weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh
from scipy import ndimage, sparse
from scipy.sparse.linalg import factorized

Vec3 = tuple[float, float, float]


@dataclass
class Joint:
    name: str
    parent: str | None
    position: np.ndarray
    deform: bool = True


@dataclass
class Skeleton:
    joints: list[Joint] = field(default_factory=list)
    template: str = "custom"

    def by_name(self) -> dict[str, Joint]:
        return {j.name: j for j in self.joints}

    def children(self, name: str) -> list[Joint]:
        return [j for j in self.joints if j.parent == name]


# ---- templates (joint name, parent, deforms) --------------------------------------------------

HUMANOID = [
    ("Hips", None), ("Spine", "Hips"), ("Spine1", "Spine"), ("Spine2", "Spine1"), ("Neck", "Spine2"),
    ("Head", "Neck"), ("HeadTop_End", "Head"),
    ("LeftShoulder", "Spine2"), ("LeftArm", "LeftShoulder"), ("LeftForeArm", "LeftArm"), ("LeftHand", "LeftForeArm"),
    ("LeftHand_End", "LeftHand"),
    ("RightShoulder", "Spine2"), ("RightArm", "RightShoulder"), ("RightForeArm", "RightArm"), ("RightHand", "RightForeArm"),
    ("RightHand_End", "RightHand"),
    ("LeftUpLeg", "Hips"), ("LeftLeg", "LeftUpLeg"), ("LeftFoot", "LeftLeg"), ("LeftToeBase", "LeftFoot"),
    ("LeftToe_End", "LeftToeBase"),
    ("RightUpLeg", "Hips"), ("RightLeg", "RightUpLeg"), ("RightFoot", "RightLeg"), ("RightToeBase", "RightFoot"),
    ("RightToe_End", "RightToeBase"),
]


def is_end(name: str) -> bool:
    return name.endswith("_End")


# ---- fitting helpers --------------------------------------------------------------------------

def _interior(mesh: trimesh.Trimesh, point: np.ndarray, axis: int = 2) -> np.ndarray:
    """Move ``point`` to the middle of the mesh along ``axis`` (a ray through it, first/last hits nearest to it)."""
    direction = np.zeros(3)
    direction[axis] = -1.0
    origin = point.copy()
    origin[axis] = mesh.bounds[1][axis] + 1.0
    try:
        hits, _, _ = mesh.ray.intersects_location([origin], [direction], multiple_hits=True)
    except Exception:
        hits = np.empty((0, 3))
    if len(hits) < 2:
        return point
    depth = np.sort(hits[:, axis])
    # the pair of hits enclosing the point's current depth, else the outermost pair
    out = point.copy()
    inside = [(a, b) for a, b in zip(depth[:-1], depth[1:]) if a <= point[axis] <= b]
    a, b = inside[0] if inside else (depth[0], depth[-1])
    out[axis] = (a + b) / 2
    return out


def _slice_components(pts2d: np.ndarray, cell: float, min_cells: int = 3) -> list[np.ndarray]:
    """Group 2D points into blobs on an occupancy grid (feet on the floor, legs at knee height)."""
    if not len(pts2d):
        return []
    lo = pts2d.min(axis=0)
    idx = np.floor((pts2d - lo) / cell).astype(int)
    grid = np.zeros(idx.max(axis=0) + 1, dtype=bool)
    grid[idx[:, 0], idx[:, 1]] = True
    grid = ndimage.binary_closing(grid, iterations=1)
    labels, n = ndimage.label(grid)
    lab = labels[idx[:, 0], idx[:, 1]]
    groups = [pts2d[lab == i] for i in range(1, n + 1)]
    return [gp for gp in groups if len(np.unique(np.floor((gp - lo) / cell).astype(int), axis=0)) >= min_cells]


def _shoulders_and_neck(v: np.ndarray, cx: float, y0: float, H: float) -> tuple[float, float]:
    """(shoulder joint height, neck height). The shoulder line is the top of the silhouette a little to each
    side of the centre (where the deltoids / shoulder pads are): unlike widths, it isn't fooled by capes,
    high collars or hats, which sit on the centre line or hang below the shoulders."""
    tops = []
    for sign in (1.0, -1.0):
        for off in (0.10, 0.12, 0.14):
            col = v[(np.abs(v[:, 0] - (cx + sign * off * H)) < 0.012 * H) & (v[:, 1] < y0 + 0.93 * H)
                    & (v[:, 1] > y0 + 0.55 * H)]
            if len(col):
                tops.append(float(col[:, 1].max()))
    if not tops:
        return y0 + 0.80 * H, y0 + 0.84 * H
    top = float(np.median(tops))
    return top - 0.035 * H, top + 0.03 * H


def fit_humanoid(mesh: trimesh.Trimesh) -> Skeleton:
    """Mixamo-style humanoid fitted to a standing character (Y-up, facing +Z; A-, T- or I-pose)."""
    v = np.asarray(mesh.vertices)
    lo, hi = mesh.bounds
    H = float(hi[1] - lo[1])
    y0 = float(lo[1])
    cx = float(np.median(v[(v[:, 1] > y0 + 0.45 * H) & (v[:, 1] < y0 + 0.75 * H), 0])) if len(v) else 0.0

    def at(h):
        return y0 + h * H

    pos: dict[str, np.ndarray] = {}
    # feet: blobs on the floor, split left (+X) / right (-X)
    floor = v[v[:, 1] < at(0.05)]
    feet = {}
    for side, sign in (("Left", 1.0), ("Right", -1.0)):
        pts = floor[(floor[:, 0] - cx) * sign > 0.01 * H]
        if len(pts) < 10:
            pts = np.array([[cx + sign * 0.09 * H, y0, float(np.median(v[:, 2]))]])
        fx, fz = float(np.median(pts[:, 0])), float(np.median(pts[:, 2]))
        toe_z, heel_z = float(pts[:, 2].max()), float(pts[:, 2].min())
        feet[side] = (fx, fz, toe_z, heel_z)
    # the neck: where the torso narrows above the shoulders. Proportions come from it, not from the total
    # height, so hats, horns or tall hair don't push everything up
    shoulder_y, neck_y = _shoulders_and_neck(v, cx, y0, H)
    hips_y = y0 + 0.62 * (neck_y - y0)
    pos["Hips"] = np.array([cx, hips_y, 0.0])
    span = neck_y - hips_y
    for name, f in (("Spine", 0.22), ("Spine1", 0.45), ("Spine2", 0.68)):
        pos[name] = np.array([cx, hips_y + f * span, 0.0])
    pos["Neck"] = np.array([cx, neck_y, 0.0])
    pos["Head"] = np.array([cx, neck_y + 0.045 * H, 0.0])
    head_top = v[(np.abs(v[:, 0] - cx) < 0.08 * H) & (v[:, 1] > at(0.85))]
    pos["HeadTop_End"] = np.array([cx, float(head_top[:, 1].max()) if len(head_top) else at(1.0), 0.0])
    # arms: the farthest points to each side between the hips and the head are the hands
    arm_band = v[(v[:, 1] > at(0.35)) & (v[:, 1] < at(0.9))]
    for side, sign in (("Left", 1.0), ("Right", -1.0)):
        lateral = (arm_band[:, 0] - cx) * sign
        out = arm_band[lateral > 0.1 * H]
        if len(out) >= 20:
            # the hand is the end of the arm's *mass*: a percentile, so thin props held in it (a wand, a
            # sword) don't pull the hand to their tip
            lat = (out[:, 0] - cx) * sign
            reach = np.percentile(lat, 97)
            tip = np.median(out[(lat >= reach - 0.02 * H) & (lat <= reach + 0.02 * H)], axis=0)
        else:
            tip = np.array([cx + sign * 0.4 * H, at(0.7), 0.0])
        shoulder = np.array([cx + sign * min(0.115 * H, 0.8 * abs(tip[0] - cx)), shoulder_y, 0.0])
        arm_dir = (tip - shoulder) / max(np.linalg.norm(tip - shoulder), 1e-9)
        wrist = tip - arm_dir * 0.07 * H
        pos[f"{side}Shoulder"] = np.array([cx + sign * 0.035 * H, shoulder_y + 0.01 * H, 0.0])
        pos[f"{side}Arm"] = shoulder
        pos[f"{side}ForeArm"] = (shoulder + wrist) / 2
        pos[f"{side}Hand"] = wrist
        pos[f"{side}Hand_End"] = tip
        fx, fz, toe_z, heel_z = feet[side]
        foot_len = max(toe_z - heel_z, 0.04 * H)
        pos[f"{side}UpLeg"] = np.array([cx + 0.8 * (fx - cx), hips_y - 0.035 * H, fz])
        pos[f"{side}Leg"] = np.array([fx, at(0.28), fz])
        pos[f"{side}Foot"] = np.array([fx, at(0.065), heel_z + 0.25 * foot_len])
        pos[f"{side}ToeBase"] = np.array([fx, at(0.025), heel_z + 0.7 * foot_len])
        pos[f"{side}Toe_End"] = np.array([fx, at(0.02), toe_z])
    # depth: the spine sits a fixed depth behind the chest/belly (capes and backpacks make the middle of the
    # whole slice useless); limbs sit in the middle of their own cross-section
    spine_names = {"Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "HeadTop_End",
                   "LeftShoulder", "RightShoulder"}
    for name, p in pos.items():
        if name in spine_names:
            sl = v[(np.abs(v[:, 0] - cx) < 0.06 * H) & (np.abs(v[:, 1] - p[1]) < 0.025 * H)]
            if len(sl):
                q = p.copy()
                q[2] = float(sl[:, 2].max()) - (0.06 if name in ("Neck", "Head", "HeadTop_End") else 0.08) * H
                pos[name] = _interior(mesh, q) if not is_end(name) else q
        elif not is_end(name):
            pos[name] = _interior(mesh, p)
    for side in ("Left", "Right"):  # clavicles sit at the shoulder's depth
        pos[f"{side}Shoulder"][2] = (pos[f"{side}Shoulder"][2] + pos[f"{side}Arm"][2]) / 2
    # legs stand over the feet (robes and capes around them have nothing to say about depth); the hips sit
    # between the legs and the lower spine blends from the hips to the chest
    for side in ("Left", "Right"):
        ankle_z = pos[f"{side}Foot"][2]
        for name in (f"{side}UpLeg", f"{side}Leg"):
            pos[name][2] = ankle_z
    pos["Hips"][2] = (pos["LeftUpLeg"][2] + pos["RightUpLeg"][2]) / 2
    chest_z = pos["Spine2"][2]
    for name, f in (("Spine", 0.33), ("Spine1", 0.66)):
        pos[name][2] = pos["Hips"][2] + f * (chest_z - pos["Hips"][2])
    return Skeleton([Joint(n, par, pos[n], not is_end(n)) for n, par in HUMANOID], template="humanoid")


def fit_creature(mesh: trimesh.Trimesh, flip: bool = False) -> Skeleton:
    """Spine along the body's long horizontal axis, a head chain at the front, a tail chain if the body goes
    on behind the hips, and one 3-bone leg per foot found on the floor (any number of legs)."""
    v = np.asarray(mesh.vertices)
    lo, hi = mesh.bounds
    ext = hi - lo
    H = float(ext[1])
    ax = 2 if ext[2] >= ext[0] else 0           # body axis: the longer horizontal one
    side_ax = 0 if ax == 2 else 2
    front = -1.0 if flip else 1.0
    a_lo, a_hi = (lo[ax], hi[ax]) if front > 0 else (hi[ax], lo[ax])
    L = float(ext[ax])

    def axis_point(t: float, y: float) -> np.ndarray:  # t: 0 = rear, 1 = front
        p = np.zeros(3)
        p[ax] = a_lo + (a_hi - a_lo) * t
        p[side_ax] = float(np.median(v[:, side_ax]))
        p[1] = y
        return p

    # body height: median height of the middle third (legs excluded by taking points above 35% height)
    mid = v[(np.abs(v[:, ax] - (lo[ax] + hi[ax]) / 2) < L / 6) & (v[:, 1] > lo[1] + 0.35 * H)]
    body_y = float(np.median(mid[:, 1])) if len(mid) else lo[1] + 0.6 * H
    joints: list[Joint] = []
    spine_t = [0.3, 0.45, 0.6, 0.72]
    names = ["Hips", "Spine", "Spine1", "Spine2"]
    for i, (n, t) in enumerate(zip(names, spine_t)):
        joints.append(Joint(n, None if i == 0 else names[i - 1], axis_point(t, body_y)))
    joints.append(Joint("Neck", "Spine2", axis_point(0.84, body_y)))
    joints.append(Joint("Head", "Neck", axis_point(0.93, body_y)))
    joints.append(Joint("HeadTop_End", "Head", axis_point(1.0, body_y), deform=False))
    # tail: what lies behind the hips
    behind = v[(v[:, ax] - (a_lo + (a_hi - a_lo) * 0.3)) * front < 0]
    if len(behind) > 0.03 * len(v):
        prev = "Hips"
        for i, t in enumerate((0.2, 0.12, 0.05), start=1):
            pt = axis_point(t, body_y)
            near = behind[np.abs(behind[:, ax] - pt[ax]) < 0.05 * L]
            if len(near):
                pt[1] = float(np.median(near[:, 1]))
            joints.append(Joint(f"Tail{i}", prev, pt))
            prev = f"Tail{i}"
        joints.append(Joint("Tail_End", prev, axis_point(0.0, body_y), deform=False))
    # legs: one per foot blob
    floor = v[v[:, 1] < lo[1] + 0.06 * H]
    blobs = _slice_components(floor[:, [0, 2]], cell=max(0.02 * max(ext[0], ext[2]), 1e-4))
    feet = [np.array([float(np.median(b[:, 0])), lo[1], float(np.median(b[:, 1]))]) for b in blobs]
    feet.sort(key=lambda p: -(p[ax] * front))                  # front to back
    spine = [j for j in joints if j.name in names]
    counters = {"Left": 0, "Right": 0}
    side_sign = 1.0 if ax == 2 else -1.0                       # model's left is +X when facing +Z
    for foot in feet:
        lateral = (foot[side_ax] - float(np.median(v[:, side_ax]))) * side_sign * front
        side = "Left" if lateral >= 0 else "Right"
        counters[side] += 1
        base = f"{side}Leg{counters[side]}"
        root = min(spine, key=lambda j: abs(j.position[ax] - foot[ax]))
        hip = root.position.copy()
        hip[side_ax] = root.position[side_ax] + 0.4 * (foot[side_ax] - root.position[side_ax])
        knee = _knee(v, hip, foot, H)
        ankle = foot.copy()
        ankle[1] = lo[1] + 0.06 * H
        joints += [Joint(f"{base}_Upper", root.name, hip), Joint(f"{base}_Lower", f"{base}_Upper", knee),
                   Joint(f"{base}_Foot", f"{base}_Lower", ankle),
                   Joint(f"{base}_End", f"{base}_Foot", foot.copy(), deform=False)]
    for j in joints:
        if j.deform:
            # legs: centre vertically (they run sideways/down); centring across them would pull the joint
            # toward the body's middle or the other side
            j.position = _interior(mesh, j.position, axis=1 if j.name.startswith(("Left", "Right")) else side_ax)
    return Skeleton(joints, template="creature")


def _knee(v: np.ndarray, hip: np.ndarray, foot: np.ndarray, H: float) -> np.ndarray:
    """The knee of a leg going from ``hip`` to ``foot``: the top of the leg's material between them, seen
    from above (spider/insect legs arch up before coming down); straight legs get the midpoint."""
    a, b = hip[[0, 2]], foot[[0, 2]]
    ab = b - a
    length = float(np.linalg.norm(ab))
    mid = (hip + foot) / 2
    if length < 0.1 * H:
        mid[1] = foot[1] + 0.5 * (hip[1] - foot[1])
        return mid
    t = ((v[:, [0, 2]] - a) @ ab) / (length ** 2)
    off = np.linalg.norm(v[:, [0, 2]] - (a + np.clip(t, 0, 1)[:, None] * ab), axis=1)
    corridor = v[(t > 0.25) & (t < 0.85) & (off < 0.06 * H)]
    if len(corridor) < 5:
        mid[1] = foot[1] + 0.5 * (hip[1] - foot[1])
        return mid
    top = corridor[np.argmax(corridor[:, 1])]
    knee = top.copy()
    knee[1] -= 0.03 * H
    return knee


# ---- skin weights (bone heat) -------------------------------------------------------------------

def _segment_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def _bone_segments(skeleton: Skeleton, ji: int) -> list[tuple[np.ndarray, np.ndarray]]:
    joints = skeleton.joints
    j = joints[ji]
    pos = {k.name: k.position for k in joints}
    kids = skeleton.children(j.name)
    if kids:
        return [(np.asarray(j.position, float), np.asarray(c.position, float)) for c in kids]
    tail = j.position + (j.position - pos[j.parent]) * 0.5 if j.parent else j.position
    return [(np.asarray(j.position, float), np.asarray(tail, float))]


def geodesic_bone_distances(mesh: trimesh.Trimesh, skeleton: Skeleton, deform: list[int],
                            resolution: int = 96) -> np.ndarray | None:
    """(V, len(deform)) distances from each vertex to each bone measured *through the model's volume*
    (geodesic voxel binding): a cape hanging next to an arm is far from it, since the path has to go up
    through the shoulder. None if the model can't be voxelized."""
    from scipy.sparse.csgraph import dijkstra

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    lo = verts.min(axis=0)
    pitch = float(np.max(np.ptp(verts, axis=0))) / resolution
    if pitch <= 0:
        return None
    pts, _ = trimesh.sample.sample_surface(mesh, 400_000, seed=0)
    shape = np.ceil(np.ptp(verts, axis=0) / pitch).astype(int) + 3
    occ = np.zeros(shape, dtype=bool)
    ijk = np.floor((np.vstack([pts, verts]) - lo) / pitch).astype(int) + 1
    occ[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = True
    occ = ndimage.binary_dilation(occ)
    occ = ndimage.binary_fill_holes(occ)
    ids = -np.ones(shape, dtype=np.int64)
    cells = np.argwhere(occ)
    ids[tuple(cells.T)] = np.arange(len(cells))
    rows, cols, wts = [], [], []
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dy, dz) > (0, 0, 0)]
    for off in offsets:
        nb = cells + off
        ok = np.all((nb >= 0) & (nb < shape), axis=1)
        a = ids[tuple(cells[ok].T)]
        b = ids[tuple(nb[ok].T)]
        keep = b >= 0
        rows.append(a[keep])
        cols.append(b[keep])
        wts.append(np.full(int(keep.sum()), pitch * np.sqrt(sum(o * o for o in off))))
    graph = sparse.coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))),
                              shape=(len(cells), len(cells))).tocsr()
    centers = (cells - 1 + 0.5) * pitch + lo
    from scipy.spatial import cKDTree

    tree = cKDTree(centers)
    vert_cell = np.floor((verts - lo) / pitch).astype(int) + 1
    vid = ids[tuple(vert_cell.T)]
    missing = vid < 0
    if missing.any():
        vid[missing] = tree.query(verts[missing], k=1)[1]
    offset = np.linalg.norm(verts - centers[vid], axis=1)
    out = np.empty((len(verts), len(deform)))
    for k, ji in enumerate(deform):
        src = []
        for a, b in _bone_segments(skeleton, ji):
            n = max(2, int(np.linalg.norm(b - a) / pitch) + 1)
            line = a + np.linspace(0, 1, n)[:, None] * (b - a)
            src.extend(tree.query(line, k=1)[1].tolist())   # bones outside the volume snap to its nearest cell
        d = dijkstra(graph, directed=False, indices=np.unique(src), min_only=True)
        out[:, k] = d[vid] + offset
    if not np.isfinite(out).all():
        far = ~np.isfinite(out)
        out[far] = np.nan
        # pieces not connected to any bone through the volume (floating props): fall back to straight distance
        return np.where(np.isnan(out), np.inf, out)
    return out


def skin_weights(mesh: trimesh.Trimesh, skeleton: Skeleton, max_influences: int = 4,
                 method: str = "geodesic", smooth: float = 0.015) -> tuple[np.ndarray, np.ndarray]:
    """(joint indices (V, 4) uint16, weights (V, 4) float32) per vertex, indices into ``skeleton.joints``.

    Bone heat (as Blender's automatic weights, without the visibility test): every vertex is "heated" by its
    nearest bone, and the heat diffuses over the surface (edge-length Laplacian), so weights blend smoothly
    across joints and follow the surface instead of jumping across gaps. ``geodesic``: the nearest bone is
    measured through the model's volume (a cape hanging next to an arm is far from it).

    Vertices split at UV/normal seams are welded first: solved apart, the two sides of a seam would get
    slightly different weights and the surface would open along it when posed."""
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    scale0 = float(np.linalg.norm(mesh.extents)) or 1.0
    _, first, inverse = np.unique(np.round(verts / (1e-6 * scale0)).astype(np.int64), axis=0,
                                  return_index=True, return_inverse=True)
    inverse = inverse.ravel()
    if len(first) < len(verts):
        welded = trimesh.Trimesh(vertices=verts[first], faces=inverse[np.asarray(mesh.faces)], process=False)
        j, w = skin_weights(welded, skeleton, max_influences, method, smooth)
        return j[inverse], w[inverse]
    n = len(verts)
    joints = skeleton.joints
    deform = [i for i, j in enumerate(joints) if j.deform]
    if not deform:
        raise ValueError("the skeleton has no deforming joints")
    pos = {j.name: j.position for j in joints}
    dist = np.full((n, len(deform)), np.inf)
    for k, ji in enumerate(deform):
        for a, b in _bone_segments(skeleton, ji):
            dist[:, k] = np.minimum(dist[:, k], _segment_distance(verts, a, b))
    if method == "geodesic":
        geo = geodesic_bone_distances(mesh, skeleton, deform)
        if geo is not None:
            # vertices the volume doesn't connect to any bone keep the straight-line distance
            unreachable = ~np.isfinite(geo).any(axis=1)
            geo[unreachable] = dist[unreachable]
            geo = np.where(np.isfinite(geo), geo, dist * 10)
            dist = geo
    dmin = dist.min(axis=1)
    scale = float(np.linalg.norm(mesh.extents)) or 1.0
    edges = np.asarray(mesh.edges_unique)
    lengths = np.maximum(np.asarray(mesh.edges_unique_length), 1e-6 * scale)
    w = 1.0 / lengths ** 2
    W = sparse.coo_matrix((np.concatenate([w, w]), (np.concatenate([edges[:, 0], edges[:, 1]]),
                                                   np.concatenate([edges[:, 1], edges[:, 0]]))), shape=(n, n)).tocsr()
    L = sparse.diags(np.asarray(W.sum(axis=1)).ravel()) - W
    d = np.maximum(dmin, 1e-3 * scale)
    heat = 1.0 / d ** 2
    solve = factorized((L + sparse.diags(heat)).tocsc())
    weights = np.zeros((n, len(deform)))
    near = dist <= dmin[:, None] * 1.0001
    for k in range(len(deform)):
        rhs = heat * near[:, k]
        if rhs.any():
            weights[:, k] = solve(rhs)
    weights = np.clip(weights, 0.0, None)
    weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
    if smooth > 0:
        # implicit Laplacian smoothing over ~smooth x the model's size: spreads the switch from one bone to the
        # next over a few centimetres (armpits and crotch of A-posed models otherwise crease sharply)
        r = smooth * scale
        smooth_solve = factorized((sparse.identity(n, format="csc") + (r * r) * L).tocsc())
        weights = np.column_stack([smooth_solve(weights[:, k]) for k in range(weights.shape[1])])
        weights = np.clip(weights, 0.0, None)
        weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
    order = np.argsort(-weights, axis=1)
    top = order[:, :max_influences]
    tw = np.take_along_axis(weights, top, axis=1)
    # keeping only the strongest influences cuts a weight off where it drops out of the top 4: subtracting the
    # first left-out weight makes that continuous (both are zero where they swap), so neighbouring vertices
    # never jump apart when posed
    if weights.shape[1] > max_influences:
        tw = np.clip(tw - np.take_along_axis(weights, order[:, max_influences:max_influences + 1], axis=1), 0.0, None)
    tw = np.clip(tw - 0.005, 0.0, None)
    total = tw.sum(axis=1, keepdims=True)
    fallback = total[:, 0] <= 0
    tw = np.where(fallback[:, None], 0.0, tw / np.maximum(total, 1e-12))
    tw[fallback, 0] = 1.0
    top[fallback, 0] = np.argmin(dist[fallback], axis=1)
    idx = np.asarray(deform)[top]
    return idx.astype(np.uint16), tw.astype(np.float32)


def remap_weights(src_positions: np.ndarray, joints: np.ndarray, weights: np.ndarray,
                  target: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """Carry per-vertex skin data to another vertex set of the same surface (e.g. after UV unwrapping)."""
    from scipy.spatial import cKDTree

    _, nearest = cKDTree(src_positions).query(np.asarray(target.vertices), k=1, workers=-1)
    return joints[nearest], weights[nearest]


# ---- skinned glTF -------------------------------------------------------------------------------

def inject_skin(glb: bytes, mesh_node: str, skeleton_nodes: list[tuple[str, str | None, np.ndarray]],
                joints: np.ndarray, weights: np.ndarray) -> bytes:
    """Add bone nodes, a skin and JOINTS_0/WEIGHTS_0 to the (single-primitive) mesh node ``mesh_node`` of a
    GLB written by trimesh. ``skeleton_nodes``: (name, parent, world position) in hierarchy order; their
    order is the skin's joint order (``joints`` indexes it). The mesh must be in bind (world) space."""
    import json
    import struct

    _, version, _ = struct.unpack("<4sII", glb[:12])
    jlen = struct.unpack("<I", glb[12:16])[0]
    doc = json.loads(glb[20:20 + jlen])
    blen = struct.unpack("<I", glb[20 + jlen:24 + jlen])[0]
    blob = bytearray(glb[28 + jlen:28 + jlen + blen])

    def add(arr: np.ndarray, component: int, kind: str, target: int | None = None) -> int:
        while len(blob) % 4:
            blob.append(0)
        view = {"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes}
        if target:
            view["target"] = target
        blob.extend(arr.tobytes())
        doc.setdefault("bufferViews", []).append(view)
        count = arr.shape[0]
        doc.setdefault("accessors", []).append({"bufferView": len(doc["bufferViews"]) - 1, "componentType": component,
                                                "count": int(count), "type": kind})
        return len(doc["accessors"]) - 1

    nodes = doc.setdefault("nodes", [])
    mesh_idx = next(i for i, nd in enumerate(nodes) if nd.get("name") == mesh_node and "mesh" in nd)
    index: dict[str, int] = {}
    world = {name: np.asarray(p, dtype=np.float64) for name, _, p in skeleton_nodes}
    roots = []
    for name, parent, p in skeleton_nodes:
        rest = world[name] - (world[parent] if parent else 0.0)
        nodes.append({"name": name, "translation": [float(x) for x in rest]})
        index[name] = len(nodes) - 1
        if parent:
            nodes[index[parent]].setdefault("children", []).append(index[name])
        else:
            roots.append(index[name])
    scene = doc["scenes"][doc.get("scene", 0)]
    scene.setdefault("nodes", []).extend(roots)

    ibm = np.zeros((len(skeleton_nodes), 16), dtype=np.float32)
    for k, (name, _, _) in enumerate(skeleton_nodes):
        m = np.eye(4)
        m[:3, 3] = -world[name]
        ibm[k] = m.T.reshape(-1)  # column-major
    ibm_acc = add(ibm, 5126, "MAT4")
    doc.setdefault("skins", []).append({"joints": [index[name] for name, _, _ in skeleton_nodes],
                                        "inverseBindMatrices": ibm_acc, "skeleton": roots[0], "name": "Armature"})
    nodes[mesh_idx]["skin"] = len(doc["skins"]) - 1
    prim = doc["meshes"][nodes[mesh_idx]["mesh"]]["primitives"][0]
    prim["attributes"]["JOINTS_0"] = add(np.ascontiguousarray(joints, dtype=np.uint16), 5123, "VEC4", 34962)
    prim["attributes"]["WEIGHTS_0"] = add(np.ascontiguousarray(weights, dtype=np.float32), 5126, "VEC4", 34962)

    doc["buffers"][0]["byteLength"] = len(blob)
    chunk = json.dumps(doc, separators=(",", ":")).encode()
    chunk += b" " * (-len(chunk) % 4)
    while len(blob) % 4:
        blob.append(0)
    total = 12 + 8 + len(chunk) + 8 + len(blob)
    return (struct.pack("<4sII", b"glTF", version, total) + struct.pack("<II", len(chunk), 0x4E4F534A) + chunk
            + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))
