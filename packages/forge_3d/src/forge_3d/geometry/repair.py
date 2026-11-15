"""Mesh cleanup and solidification (watertight, manifold) for printing and booleans."""

from __future__ import annotations

import numpy as np
import pymeshlab
import trimesh

from .bridge import from_manifold, from_meshlab, to_manifold, to_meshlab


def orient_outward(mesh: trimesh.Trimesh, samples: int = 64) -> tuple[trimesh.Trimesh, int]:
    """Make every connected piece face outward; returns (mesh, pieces flipped).

    Per piece (on welded positions), rays leave a few faces along their normal: a normal that points inward
    sees the piece's own surface an odd number of times. The majority decides whether the piece is flipped.
    Unlike trimesh's ``fix_normals`` (signed volume), this works on open and non-manifold meshes, which is
    what AI generators produce - a wrong volume sign there turned whole models inside out."""
    faces = np.asarray(mesh.faces)
    if not len(faces):
        return mesh, 0
    welded = trimesh.Trimesh(np.asarray(mesh.vertices), faces, process=False)
    welded.merge_vertices(merge_tex=True, merge_norm=True)
    labels = trimesh.graph.connected_component_labels(welded.face_adjacency, node_count=len(faces))
    normals = welded.face_normals
    centers = welded.triangles_center
    eps = 1e-4 * float(np.linalg.norm(welded.extents) or 1.0)
    rng = np.random.default_rng(0)
    flip = np.zeros(len(faces), dtype=bool)
    flipped = 0
    for label in np.unique(labels):
        idx = np.nonzero(labels == label)[0]
        if len(idx) < 4:
            continue
        pick = rng.choice(idx, size=min(samples, len(idx)), replace=False)
        piece = welded.submesh([idx], append=True)
        origins = centers[pick] + normals[pick] * eps
        try:
            hit_rays = piece.ray.intersects_location(origins, normals[pick], multiple_hits=True)[1]
        except Exception:
            continue
        counts = np.bincount(hit_rays, minlength=len(pick))
        inward = (counts % 2 == 1).mean()
        if inward > 0.6:
            flip[idx] = True
            flipped += 1
    if not flipped:
        return mesh, 0
    out = mesh.copy()
    f = np.asarray(out.faces).copy()
    f[flip] = f[flip][:, ::-1]
    out.faces = f
    return out, flipped


def clean(mesh: trimesh.Trimesh, min_component_ratio: float = 0.02, max_hole_edges: int = 2000) -> tuple[trimesh.Trimesh, list[str]]:
    """Remove garbage and fix topology without changing the overall shape."""
    actions: list[str] = []
    was_consistent = bool(mesh.is_winding_consistent)
    ms, _ = to_meshlab(mesh, keep_uv=False)

    def step(name: str, fn, **kw):
        before = (ms.current_mesh().vertex_number(), ms.current_mesh().face_number())
        try:
            fn(**kw)
        except pymeshlab.PyMeshLabException as exc:  # some filters refuse non-manifold input
            actions.append(f"{name}: skipped ({exc})")
            return
        after = (ms.current_mesh().vertex_number(), ms.current_mesh().face_number())
        if after != before:
            actions.append(f"{name}: verts {before[0]}->{after[0]}, faces {before[1]}->{after[1]}")

    step("remove_duplicate_vertices", ms.meshing_remove_duplicate_vertices)
    step("remove_duplicate_faces", ms.meshing_remove_duplicate_faces)
    step("remove_null_faces", ms.meshing_remove_null_faces)
    step("remove_unreferenced_vertices", ms.meshing_remove_unreferenced_vertices)
    step("repair_non_manifold_edges", ms.meshing_repair_non_manifold_edges, method=0)
    step("repair_non_manifold_vertices", ms.meshing_repair_non_manifold_vertices)
    if min_component_ratio > 0:
        step(
            "remove_small_components",
            ms.meshing_remove_connected_component_by_diameter,
            mincomponentdiag=pymeshlab.PercentageValue(min_component_ratio * 100),
        )
    # Hole filling on a still non-manifold mesh can produce faces that later make
    # pymeshlab's quadric decimation crash the whole process (access violation).
    remaining = _non_manifold_edge_count(ms)
    if remaining:
        actions.append(f"close_holes: skipped ({remaining} non-manifold edges left; use make_solid or reconstruct_surface)")
    else:
        step("close_holes", ms.meshing_close_holes, maxholesize=max_hole_edges, newfaceselected=False)
    # re-orienting a mesh that is already consistent flips patches of it (seen on TRELLIS.2 output): only
    # when the winding is actually broken
    if not was_consistent:
        step("re_orient_faces", ms.meshing_re_orient_faces_coherently)
    out = from_meshlab(ms)
    out, flipped = orient_outward(out)
    if flipped:
        actions.append(f"orientation: {flipped} piece(s) turned outward")
    return out, actions


def _non_manifold_edge_count(ms: pymeshlab.MeshSet) -> int:
    faces = ms.current_mesh().face_matrix()
    if len(faces) == 0:
        return 0
    edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts > 2).sum())


def reconstruct_surface(mesh: trimesh.Trimesh, depth: int = 10, min_component_ratio: float = 0.01) -> tuple[trimesh.Trimesh, list[str]]:
    """Rebuild a clean, closed surface with screened Poisson reconstruction.

    Meant for AI-generated meshes (e.g. Hunyuan3D surface nets), whose topology is full of
    non-manifold edges that repair filters can't untangle. Keeps the shape and size; small
    detached blobs (interior bubbles, noise) below ``min_component_ratio`` of the face count go away.
    """
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=np.asarray(mesh.vertices, dtype=np.float64),
                               face_matrix=np.asarray(mesh.faces, dtype=np.int32)))
    ms.compute_normal_per_vertex()
    ms.generate_surface_reconstruction_screened_poisson(depth=depth, preclean=True, samplespernode=1.5, pointweight=4)
    faces_before = ms.current_mesh().face_number()
    if min_component_ratio > 0:
        ms.meshing_remove_connected_component_by_face_number(mincomponentsize=int(faces_before * min_component_ratio))
    ms.meshing_remove_unreferenced_vertices()
    out = from_meshlab(ms)
    out, _ = orient_outward(out)
    return out, [f"poisson reconstruction (depth {depth}): {len(mesh.faces)} -> {faces_before} faces, "
                 f"{faces_before - len(out.faces)} faces of small blobs removed"]


def solidify(mesh: trimesh.Trimesh, voxel_resolution: int = 256) -> tuple[trimesh.Trimesh, str]:
    """Return a single watertight manifold solid.

    1. Already a volume -> unchanged.
    2. Every shell is a closed volume -> boolean union (merges overlapping parts, common in AI meshes).
    3. Otherwise -> voxel remesh at ``voxel_resolution`` along the longest axis, smoothed.
    """
    shells = mesh.split(only_watertight=False)
    if len(shells) == 1 and mesh.is_volume:
        try:
            return from_manifold(to_manifold(mesh)), "already solid"
        except ValueError:
            pass
    # several closed shells may overlap (valid topology, invalid solid): always union them
    if shells and all(s.is_volume for s in shells):
        try:
            union = to_manifold(shells[0])
            for s in shells[1:]:
                union = union + to_manifold(s)
            return from_manifold(union), f"boolean union of {len(shells)} shells"
        except ValueError:
            pass
    return _voxel_remesh(mesh, voxel_resolution), f"voxel remesh ({voxel_resolution})"


def _voxel_remesh(mesh: trimesh.Trimesh, resolution: int) -> trimesh.Trimesh:
    pitch = float(mesh.extents.max()) / resolution
    grid = mesh.voxelized(pitch).fill()
    out = grid.marching_cubes
    # marching cubes runs in voxel index space; map back to world coordinates
    out.apply_transform(grid.transform)
    trimesh.smoothing.filter_taubin(out, iterations=10)
    out, _ = orient_outward(out)
    try:
        return from_manifold(to_manifold(out))
    except ValueError:
        return out


def make_printable(mesh: trimesh.Trimesh, voxel_resolution: int = 256, min_component_ratio: float = 0.02) -> tuple[trimesh.Trimesh, list[str]]:
    shells = mesh.split(only_watertight=False)
    if shells and all(s.is_volume for s in shells):
        # closed shells: go straight to the boolean union. Welding first would fuse vertices
        # of shells that merely touch and create non-manifold edges.
        solid, how = solidify(mesh, voxel_resolution=voxel_resolution)
        return solid, [f"solidify: {how}"]
    cleaned, actions = clean(mesh, min_component_ratio=min_component_ratio)
    solid, how = solidify(cleaned, voxel_resolution=voxel_resolution)
    actions.append(f"solidify: {how}")
    return solid, actions


def is_printable_solid(mesh: trimesh.Trimesh) -> bool:
    try:
        return to_manifold(mesh).volume() > 0
    except ValueError:
        return False



def drop_fragments(mesh: trimesh.Trimesh, min_ratio: float = 0.01) -> tuple[trimesh.Trimesh, int]:
    """Remove loose pieces smaller than ``min_ratio`` of the model's diagonal (slivers left by decimation).

    Pieces are found on positions only (UV seams split vertices but don't separate pieces) and removed by
    face mask, so textures/colors are kept and nothing is copied per piece."""
    faces = np.asarray(mesh.faces)
    if not len(faces):
        return mesh, 0
    welded = trimesh.Trimesh(np.asarray(mesh.vertices), faces, process=False)
    welded.merge_vertices(merge_tex=True, merge_norm=True)
    labels = trimesh.graph.connected_component_labels(welded.face_adjacency, node_count=len(faces))
    count = int(labels.max()) + 1
    if count <= 1:
        return mesh, 0
    tri = np.asarray(mesh.vertices)[faces]                       # (F, 3, 3)
    lo = np.full((count, 3), np.inf)
    hi = np.full((count, 3), -np.inf)
    np.minimum.at(lo, labels, tri.min(axis=1))
    np.maximum.at(hi, labels, tri.max(axis=1))
    size = np.linalg.norm(hi - lo, axis=1)
    small = size < min_ratio * float(np.linalg.norm(mesh.extents))
    if not small.any() or small.all():
        return mesh, 0
    out = mesh.copy()
    out.update_faces(~small[labels])
    out.remove_unreferenced_vertices()
    return out, int(small.sum())
