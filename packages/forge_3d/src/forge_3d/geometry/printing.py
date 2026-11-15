"""3D print preparation: units/scale, repair, orientation, wall-thickness check, bed fit."""

from __future__ import annotations

import numpy as np
import trimesh
from pydantic import BaseModel, Field

from .analyze import mesh_stats
from .repair import make_printable

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


class PrinterProfile(BaseModel):
    name: str = "generic_fdm"
    build_volume_mm: tuple[float, float, float] = (220.0, 220.0, 250.0)
    nozzle_mm: float = 0.4
    min_wall_mm: float = Field(0.8, description="thinnest wall that prints reliably")
    tolerance_mm: float = Field(0.2, description="clearance for fitted parts (pins, joints)")
    max_overhang_deg: float = Field(45.0, description="steepest unsupported overhang, measured from vertical")


def to_millimeters(
    mesh: trimesh.Trimesh,
    source_units: str = "m",
    target_height_mm: float | None = None,
    target_max_dim_mm: float | None = None,
    scale: float | None = None,
) -> trimesh.Trimesh:
    """Convert to mm; optionally rescale to a target height (Z) or longest dimension."""
    out = mesh.copy()
    out.apply_scale(UNIT_TO_MM[source_units])
    if scale:
        out.apply_scale(scale)
    elif target_height_mm:
        out.apply_scale(target_height_mm / out.extents[2])
    elif target_max_dim_mm:
        out.apply_scale(target_max_dim_mm / out.extents.max())
    return out


def place_on_bed(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    out = mesh.copy()
    lo, hi = out.bounds
    out.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return out


def overhang_area(mesh: trimesh.Trimesh, max_overhang_deg: float = 45.0, bed_eps: float | None = None) -> float:
    """Area of downward faces steeper than the allowed overhang, excluding faces resting on the bed."""
    if bed_eps is None:
        bed_eps = 1e-3 * float(mesh.extents.max())
    normals = mesh.face_normals
    down = -normals[:, 2] > np.sin(np.radians(max_overhang_deg))
    on_bed = mesh.triangles[:, :, 2].max(axis=1) <= mesh.bounds[0][2] + bed_eps
    return float(mesh.area_faces[down & ~on_bed].sum())


def orient_for_print(mesh: trimesh.Trimesh, max_overhang_deg: float = 45.0, candidates: int = 32) -> tuple[trimesh.Trimesh, dict]:
    """Pick the rotation that minimizes supported (overhang) area, preferring big flat bases and low height.

    Candidate "down" directions: the six axes plus the largest convex-hull facets.
    """
    hull = mesh.convex_hull
    order = np.argsort(-hull.area_faces)[:candidates]
    dirs = np.vstack([np.eye(3), -np.eye(3), hull.face_normals[order]])
    dirs = np.unique(np.round(dirs, 3), axis=0)

    best = None
    total = float(mesh.area)
    diag = float(np.linalg.norm(mesh.extents))
    sin_limit = np.sin(np.radians(max_overhang_deg))
    bed_eps = 1e-3 * float(mesh.extents.max())
    tris = mesh.triangles
    for d in dirs:
        rot = trimesh.geometry.align_vectors(d, [0.0, 0.0, -1.0])
        r = rot[:3, :3]
        nz = mesh.face_normals @ r[2]
        z = tris @ r[2]  # (F, 3) rotated z of each corner
        zmin = z.min()
        down = -nz > sin_limit
        on_bed = z.max(axis=1) <= zmin + bed_eps
        oh = float(mesh.area_faces[down & ~on_bed].sum())
        height = float(z.max() - zmin)
        # contact: hull facets lying flat on the bed plane
        contact = float(hull.area_faces[(hull.face_normals @ r[2]) < -0.999].sum())
        score = oh / total + 0.1 * height / diag - 0.2 * contact / total
        if best is None or score < best[0]:
            best = (score, rot, oh, contact)

    _, rot, oh, contact = best
    oriented = mesh.copy()
    oriented.apply_transform(rot)
    oriented = place_on_bed(oriented)
    return oriented, {
        "rotation": np.round(rot, 6).tolist(),
        "overhang_area_mm2": round(oh, 2),
        "overhang_ratio": round(oh / total, 4),
        "bed_contact_mm2": round(contact, 2),
    }


def wall_thickness(mesh: trimesh.Trimesh, min_wall_mm: float, samples: int = 1500, seed: int = 0) -> dict:
    """Estimate local thickness by casting rays inward from sampled surface points."""
    points, face_idx = trimesh.sample.sample_surface(mesh, samples, seed=seed)
    normals = mesh.face_normals[face_idx]
    eps = 1e-4 * float(mesh.extents.max())
    origins = points - normals * eps
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, -normals, multiple_hits=False)
    if len(ray_idx) == 0:
        return {"samples": 0}
    dist = np.linalg.norm(locs - origins[ray_idx], axis=1) + eps
    thin = dist < min_wall_mm
    return {
        "samples": int(len(dist)),
        "min_mm": round(float(dist.min()), 3),
        "p5_mm": round(float(np.percentile(dist, 5)), 3),
        "median_mm": round(float(np.median(dist)), 3),
        "thin_ratio": round(float(thin.mean()), 4),
        "thin_points": np.round(points[ray_idx][thin][:200], 3).tolist(),
    }


def fits_build_volume(mesh: trimesh.Trimesh, build_volume_mm) -> bool:
    """True if the mesh fits as-is or rotated 90° around Z."""
    ex = mesh.extents
    bx, by, bz = build_volume_mm
    return bool(ex[2] <= bz and ((ex[0] <= bx and ex[1] <= by) or (ex[1] <= bx and ex[0] <= by)))


def fit_scale(extents, build_volume_mm, margin_mm: float = 5.0) -> float:
    """Largest uniform scale (<= 1) that fits the build volume, allowing a 90° turn around Z."""
    ex, ey, ez = (float(v) for v in extents)
    bx, by, bz = (float(v) - margin_mm for v in build_volume_mm)
    planar = max(min(bx / ex, by / ey), min(bx / ey, by / ex))
    return min(1.0, planar, bz / ez)


def print_optimize(
    mesh: trimesh.Trimesh,
    profile: PrinterProfile | None = None,
    source_units: str = "m",
    target_height_mm: float | None = None,
    target_max_dim_mm: float | None = None,
    scale: float | None = None,
    repair: bool = True,
    orient: bool = True,
    voxel_resolution: int = 256,
    check_walls: bool = True,
    fit_to_volume: bool = False,
    fit_margin_mm: float = 5.0,
) -> tuple[trimesh.Trimesh, dict]:
    """``fit_to_volume`` shrinks the result uniformly when it doesn't fit the printer
    (for meshes without a meaningful real-world size, e.g. AI generated ones)."""
    profile = profile or PrinterProfile()
    report: dict = {"profile": profile.model_dump(), "input": mesh_stats(mesh)}
    out = to_millimeters(mesh, source_units, target_height_mm, target_max_dim_mm, scale)
    if repair:
        out, actions = make_printable(out, voxel_resolution=voxel_resolution)
        report["repair"] = actions
    if orient:
        out, report["orientation"] = orient_for_print(out, profile.max_overhang_deg)
    else:
        out = place_on_bed(out)
    if fit_to_volume and not fits_build_volume(out, profile.build_volume_mm):
        factor = fit_scale(out.extents, profile.build_volume_mm, margin_mm=fit_margin_mm)
        out.apply_scale(factor)
        out = place_on_bed(out)
        report["scaled_to_fit"] = round(factor, 4)
    report["output"] = mesh_stats(out)
    report["fits_build_volume"] = fits_build_volume(out, profile.build_volume_mm)
    if check_walls and out.is_watertight:
        report["walls"] = wall_thickness(out, profile.min_wall_mm)
    warnings = []
    if not report["output"]["watertight"]:
        warnings.append("result is not watertight; slicer may fail")
    if not report["fits_build_volume"]:
        warnings.append("does not fit build volume; run 3d.split")
    if report.get("walls", {}).get("thin_ratio", 0) > 0.01:
        warnings.append(f"walls thinner than {profile.min_wall_mm} mm detected")
    report["warnings"] = warnings
    return out, report
