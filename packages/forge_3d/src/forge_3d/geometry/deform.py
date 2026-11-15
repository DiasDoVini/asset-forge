"""Posing a skinned mesh (linear blend skinning, as game engines do) and checking how well it deforms.

The check plays every clip and measures how much each edge of the surface stretches or collapses compared to the
rest pose: tears and "candy-wrapper" collapses at joints show up as extreme ratios, and the bone that owns the
worst vertices says where to look (weights, a joint placed outside the limb, limbs fused in the source mesh).
"""

from __future__ import annotations

import numpy as np
import trimesh

from .anim import Clip, Rig, world_matrices


def skinning_matrices(rig: Rig, clip: Clip | None, t: float) -> np.ndarray:
    """(B, 4, 4) per bone of ``rig.bones``: posed world matrix times the inverse bind (bones bind unrotated at
    their pivot)."""
    world = world_matrices(rig, clip, t)
    out = np.empty((len(rig.bones), 4, 4))
    for k, b in enumerate(rig.bones):
        inv_bind = np.eye(4)
        inv_bind[:3, 3] = -np.asarray(b.pivot, dtype=np.float64)
        out[k] = world[b.name] @ inv_bind
    return out


def pose_vertices(vertices: np.ndarray, joints: np.ndarray, weights: np.ndarray, mats: np.ndarray) -> np.ndarray:
    v = np.column_stack([vertices, np.ones(len(vertices))])
    out = np.zeros((len(vertices), 3))
    for k in range(joints.shape[1]):
        w = weights[:, k]
        live = w > 0
        if not live.any():
            continue
        m = mats[joints[live, k]]                      # (n, 4, 4)
        out[live] += w[live, None] * np.einsum("nab,nb->na", m, v[live])[:, :3]
    return out


def posed_mesh(mesh: trimesh.Trimesh, rig: Rig, clip: Clip | None, t: float, joints: np.ndarray,
               weights: np.ndarray) -> trimesh.Trimesh:
    out = mesh.copy()
    out.vertices = pose_vertices(np.asarray(mesh.vertices), joints, weights, skinning_matrices(rig, clip, t))
    return out


def deformation_report(mesh: trimesh.Trimesh, rig: Rig, clips: list[Clip], joints: np.ndarray, weights: np.ndarray,
                       samples: int = 16, tear: float = 2.5, stretch: float = 1.8, collapse: float = 0.4) -> dict:
    """Per clip: worst edge stretch/collapse ratios, the share of torn edges (stretched past ``tear``: the surface
    visibly rips or spikes) and of strained ones (past ``stretch`` or under ``collapse``: creases, normal at
    bent knees and hips with linear blend skinning), and the bones around them. Graded on tears."""
    welded = trimesh.Trimesh(np.asarray(mesh.vertices), np.asarray(mesh.faces), process=False)
    welded.merge_vertices(merge_tex=True, merge_norm=True)
    # the welded mesh keeps the first copy of each vertex: map its vertices back to the originals' weights
    from scipy.spatial import cKDTree

    _, src = cKDTree(np.asarray(mesh.vertices)).query(welded.vertices, k=1)
    J, W = joints[src], weights[src]
    edges = np.asarray(welded.edges_unique)
    rest = np.linalg.norm(welded.vertices[edges[:, 0]] - welded.vertices[edges[:, 1]], axis=1)
    ok = rest > 1e-6 * float(np.linalg.norm(welded.extents) or 1.0)
    edges, rest = edges[ok], rest[ok]
    owner = J[np.arange(len(J)), np.argmax(W, axis=1)]
    bones = [b.name for b in rig.bones]
    report = {"clips": [], "edges": int(len(edges))}
    worst_overall = 1.0
    for clip in clips:
        hi, lo = 1.0, 1.0
        bad = np.zeros(len(edges), dtype=bool)
        torn = np.zeros(len(edges), dtype=bool)
        for t in np.linspace(0.0, clip.duration, max(2, samples)):
            v = pose_vertices(welded.vertices, J, W, skinning_matrices(rig, clip, float(t)))
            ratio = np.linalg.norm(v[edges[:, 0]] - v[edges[:, 1]], axis=1) / rest
            hi, lo = max(hi, float(ratio.max())), min(lo, float(ratio.min()))
            bad |= (ratio > stretch) | (ratio < collapse)
            torn |= ratio > tear
        share = float(bad.mean()) if len(bad) else 0.0
        torn_share = float(torn.mean()) if len(torn) else 0.0
        culprits: dict[str, int] = {}
        focus = torn if torn.any() else bad
        if focus.any():
            ids, counts = np.unique(owner[edges[focus, 0]], return_counts=True)
            for i, c in sorted(zip(ids, counts), key=lambda x: -x[1])[:3]:
                culprits[bones[int(i)]] = int(c)
        worst_overall = max(worst_overall, hi, 1.0 / max(lo, 1e-6))
        report["clips"].append({"name": clip.name, "max_stretch": round(hi, 2), "min_ratio": round(lo, 2),
                                "torn_pct": round(100 * torn_share, 3), "strained_pct": round(100 * share, 2),
                                "bones": culprits,
                                "grade": "ok" if torn_share < 0.001 else ("warn" if torn_share < 0.005 else "bad")})
    grades = [c["grade"] for c in report["clips"]]
    report["grade"] = "bad" if "bad" in grades else ("warn" if "warn" in grades else "ok")
    report["worst_ratio"] = round(worst_overall, 2)
    return report

