"""Polygon reduction (quadric edge collapse)."""

from __future__ import annotations

import trimesh

from .bridge import from_meshlab, to_meshlab


def simplify(
    mesh: trimesh.Trimesh,
    target_faces: int | None = None,
    ratio: float | None = None,
    preserve_boundary: bool = True,
    preserve_uv: bool = True,
    preserve_topology: bool = False,
    planar: bool = True,
    quality: float = 0.3,
) -> trimesh.Trimesh:
    """Reduce face count to ``target_faces`` or to ``ratio`` of the current count.

    UVs (and the material) are preserved when present and ``preserve_uv`` is set.
    """
    if target_faces is None and ratio is None:
        raise ValueError("pass target_faces or ratio")
    if target_faces is None:
        if not 0 < ratio <= 1:
            raise ValueError("ratio must be in (0, 1]")
        target_faces = int(len(mesh.faces) * ratio)
    target_faces = max(4, int(target_faces))
    if target_faces >= len(mesh.faces):
        return mesh.copy()

    ms, uv = to_meshlab(mesh, keep_uv=preserve_uv)
    common = dict(
        targetfacenum=target_faces,
        qualitythr=quality,
        preserveboundary=preserve_boundary,
        boundaryweight=2.0,
        optimalplacement=True,
        preservenormal=True,
        planarquadric=planar,
    )
    if uv:
        ms.meshing_decimation_quadric_edge_collapse_with_texture(extratcoordw=1.0, **common)
    else:
        ms.meshing_decimation_quadric_edge_collapse(preservetopology=preserve_topology, autoclean=True, **common)
    return from_meshlab(ms, source=mesh, uv=uv)
