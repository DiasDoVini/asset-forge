"""Cut a solid into printable parts that fit a build volume, with dowel-pin connectors."""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field

import manifold3d as m3d
import numpy as np
import trimesh

from .bridge import from_manifold, to_manifold
from .printing import orient_for_print, place_on_bed

# 3x4 affine matrices mapping the cut axis onto Z (so Manifold.slice can be used), and back.
_TO_Z = {
    0: np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0]], dtype=np.float64),  # (x,y,z)->(y,z,x)
    1: np.array([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64),  # (x,y,z)->(z,x,y)
    2: np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]], dtype=np.float64),
}
_FROM_Z = {0: _TO_Z[1], 1: _TO_Z[0], 2: _TO_Z[2]}


@dataclass
class Cut:
    axis: int  # 0=x, 1=y, 2=z
    offset: float  # plane position along the axis, in mesh units


@dataclass
class SplitResult:
    parts: list[trimesh.Trimesh]  # laid out for printing
    assembled: list[trimesh.Trimesh]  # same parts at their original position (for previews)
    pin: trimesh.Trimesh | None
    pin_count: int
    cuts: list[Cut]
    # (pin center in assembled coordinates, the cut it crosses) - for previews
    pin_placements: list[tuple[np.ndarray, Cut]] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def auto_cuts(mesh: trimesh.Trimesh, build_volume_mm, margin_mm: float = 5.0) -> list[Cut]:
    """Evenly spaced cuts so every piece fits the build volume (minus a safety margin)."""
    lo = mesh.bounds[0]
    cuts: list[Cut] = []
    for axis in range(3):
        size = float(mesh.extents[axis])
        usable = build_volume_mm[axis] - margin_mm
        n = max(1, math.ceil(size / usable))
        cuts += [Cut(axis, float(lo[axis] + size * k / n)) for k in range(1, n)]
    return cuts


def split_for_print(
    mesh: trimesh.Trimesh,
    build_volume_mm=(220.0, 220.0, 250.0),
    cuts: list[Cut] | None = None,
    margin_mm: float = 5.0,
    connectors: bool = True,
    pin_diameter_mm: float | None = None,
    pin_length_mm: float | None = None,
    tolerance_mm: float = 0.2,
    min_wall_mm: float = 1.2,
    max_pins_per_section: int = 2,
    orient_parts: bool = True,
    circular_segments: int = 32,
) -> SplitResult:
    """Split ``mesh`` (a watertight solid, in mm) along axis-aligned planes.

    Each cut section gets dowel holes on both sides (diameter + clearance) and the
    result includes one pin model to print ``pin_count`` times.
    """
    rotated = False
    if cuts is None:
        cuts = auto_cuts(mesh, build_volume_mm, margin_mm)
        # rotating 90 degrees around Z sometimes avoids cuts entirely
        turned = mesh.copy()
        turned.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
        alt = auto_cuts(turned, build_volume_mm, margin_mm)
        if len(alt) < len(cuts):
            mesh, cuts, rotated = turned, alt, True

    auto_d, auto_len = auto_pin_size(mesh.extents)
    pin_diameter_mm = pin_diameter_mm or auto_d
    pin_length_mm = pin_length_mm or max(auto_len, pin_diameter_mm * 2)
    r_pin = pin_diameter_mm / 2
    r_hole = r_pin + tolerance_mm
    hole_len = pin_length_mm + 2 * tolerance_mm
    inset = r_hole + min_wall_mm

    pieces = [to_manifold(mesh)]
    pin_count = 0
    skipped_sections = 0
    placements: list[tuple[np.ndarray, Cut]] = []
    for cut in cuts:
        normal = np.eye(3)[cut.axis]
        nxt: list[m3d.Manifold] = []
        for piece in pieces:
            bb = np.asarray(piece.bounding_box())
            lo, hi = bb[:3], bb[3:]
            if not (lo[cut.axis] + 1e-6 < cut.offset < hi[cut.axis] - 1e-6):
                nxt.append(piece)
                continue
            if connectors:
                piece, centers = _drill_pin_holes(piece, cut, inset, r_hole, hole_len, max_pins_per_section, circular_segments)
                placements += [(p, cut) for p in centers]
                pin_count += len(centers)
                skipped_sections += not centers
            above, below = piece.split_by_plane(normal.tolist(), cut.offset)
            for half in (below, above):
                for comp in half.decompose():
                    if comp.volume() > 1e-6:
                        nxt.append(comp)
        pieces = nxt

    assembled = [from_manifold(p) for p in pieces]
    parts = []
    for tm in assembled:
        laid = place_on_bed(tm)
        if orient_parts:
            oriented, _ = orient_for_print(tm)
            # never trade a fitting part for a better-oriented one that no longer fits
            if _fits(oriented.extents, build_volume_mm) or not _fits(laid.extents, build_volume_mm):
                laid = oriented
        parts.append(laid)

    pin = None
    if connectors and pin_count:
        chamfer = min(0.4, r_pin * 0.2)
        pin = from_manifold(
            m3d.Manifold.cylinder(pin_length_mm - 2 * chamfer, r_pin, r_pin, circular_segments).translate([0, 0, chamfer])
            + m3d.Manifold.cylinder(chamfer, r_pin - chamfer, r_pin, circular_segments)
            + m3d.Manifold.cylinder(chamfer, r_pin, r_pin - chamfer, circular_segments).translate([0, 0, pin_length_mm - chamfer])
        )
        pin = place_on_bed(pin)

    return SplitResult(
        parts=parts,
        assembled=assembled,
        pin=pin,
        pin_count=pin_count,
        cuts=cuts,
        pin_placements=placements,
        info={
            "parts": len(parts),
            "rotated_90_z": rotated,
            "cuts": [{"axis": "xyz"[c.axis], "offset": round(c.offset, 3)} for c in cuts],
            "pin": {"diameter_mm": pin_diameter_mm, "length_mm": pin_length_mm, "hole_diameter_mm": round(2 * r_hole, 3),
                    "hole_depth_mm": round(hole_len / 2, 3), "count": pin_count},
            "sections_without_pins": skipped_sections,
            "part_extents_mm": [np.round(p.extents, 2).tolist() for p in parts],
            "all_fit": all(_fits(p.extents, build_volume_mm) for p in parts),
        },
    )


def _drill_pin_holes(piece, cut: Cut, inset: float, r_hole: float, hole_len: float, max_pins: int, segments: int):
    """Subtract dowel holes straddling the cut plane. Returns (piece, pin centers in world coordinates)."""
    section = piece.transform(_TO_Z[cut.axis]).slice(cut.offset)
    if section.is_empty():
        return piece, []
    hole_proto = m3d.Manifold.cylinder(hole_len, r_hole, r_hole, segments, True)
    # Prefer pins well inside the section (away from thin outer walls and surface detail, and
    # visible on the cut face); fall back to the minimum safe inset on narrow sections.
    pts = np.vstack([np.asarray(p) for p in section.to_polygons()])
    short_side = float((pts.max(axis=0) - pts.min(axis=0)).min())
    insets = sorted({max(inset, 0.25 * short_side), max(inset, 0.12 * short_side), inset}, reverse=True)
    for depth in insets:
        usable = section.offset(-depth, m3d.JoinType.Round)
        if usable.is_empty():
            continue
        drilled, centers = piece, []
        for region in usable.decompose():
            if region.area() < 1e-6:
                continue
            for u, v in _pin_positions(region, max_pins, min_spacing=3 * r_hole):
                hole = hole_proto.translate([u, v, cut.offset]).transform(_FROM_Z[cut.axis])
                # only drill if the hole stays fully inside the solid (pin would otherwise poke out)
                if (hole - drilled).volume() > 1e-3 * hole.volume():
                    continue
                drilled = drilled - hole
                centers.append(_FROM_Z[cut.axis] @ np.array([u, v, cut.offset, 1.0]))
        if centers:
            return drilled, centers
    return piece, []


def _pin_positions(region: m3d.CrossSection, max_pins: int, min_spacing: float) -> list[tuple[float, float]]:
    polys = [np.asarray(p) for p in region.to_polygons()]
    outer = max(polys, key=lambda p: abs(_signed_area(p)))
    if max_pins >= 2 and len(outer) >= 2:
        pts = outer if len(outer) <= 256 else outer[np.linspace(0, len(outer) - 1, 256).astype(int)]
        d = np.linalg.norm(pts[:, None] - pts[None], axis=-1)
        i, j = np.unravel_index(d.argmax(), d.shape)
        if d[i, j] >= min_spacing:
            return [tuple(pts[i]), tuple(pts[j])]
    c = _centroid(outer)
    probe = m3d.CrossSection.square([1e-3, 1e-3], True).translate(c.tolist())
    if abs((probe ^ region).area() - probe.area()) < 1e-9:
        return [tuple(c)]
    return [tuple(outer[0])]  # inset boundary points are always valid positions


def _signed_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _centroid(p: np.ndarray) -> np.ndarray:
    x, y = p[:, 0], p[:, 1]
    cross = x * np.roll(y, -1) - np.roll(x, -1) * y
    a = cross.sum() / 2
    if abs(a) < 1e-12:
        return p.mean(axis=0)
    return np.array([((x + np.roll(x, -1)) * cross).sum(), ((y + np.roll(y, -1)) * cross).sum()]) / (6 * a)


def _fits(extents, build) -> bool:
    ex = sorted(extents[:2])
    b = sorted(build[:2])
    return bool(extents[2] <= build[2] + 1e-6 and ex[0] <= b[0] + 1e-6 and ex[1] <= b[1] + 1e-6)


def auto_pin_size(extents) -> tuple[float, float]:
    """Dowel (diameter, length) in mm scaled to the model: 4 mm on small prints up to 10 mm on big ones."""
    d = float(np.clip(round(0.02 * float(max(extents)) * 2) / 2, 4.0, 10.0))
    return d, round(d * 2.5)


PIN_COLOR = np.array([245, 158, 11, 255], dtype=np.uint8)


def exploded_scene(
    parts: list[trimesh.Trimesh],
    cuts: list[Cut] | None = None,
    pin: trimesh.Trimesh | None = None,
    pin_placements: list[tuple[np.ndarray, Cut]] | None = None,
    gap: float | None = None,
) -> trimesh.Scene:
    """Assembly preview: parts pulled apart along the cut axes (so the cut faces and their pin
    holes face the viewer at an angle) with the dowel pins shown where they go, in orange.

    Without ``cuts`` the parts are pushed radially from the common center.
    """
    scene = trimesh.Scene()
    if not parts:
        return scene
    lo = np.min([p.bounds[0] for p in parts], axis=0)
    hi = np.max([p.bounds[1] for p in parts], axis=0)
    gap = gap if gap is not None else 0.18 * float((hi - lo).max())
    center = (lo + hi) / 2

    def offset(point: np.ndarray, skip: Cut | None = None) -> np.ndarray:
        if not cuts:
            d = point - center
            n = np.linalg.norm(d)
            return d / n * gap if n > 1e-9 else np.zeros(3)
        out = np.zeros(3)
        for c in cuts:
            if c is not skip:
                out[c.axis] += np.sign(point[c.axis] - c.offset) * gap / 2
        return out

    for i, p in enumerate(parts):
        q = p.copy()
        q.apply_translation(offset(q.bounds.mean(axis=0)))
        rgb = np.array(colorsys.hsv_to_rgb((i * 0.618034) % 1.0, 0.45, 0.9)) * 255
        q.visual.face_colors = np.append(rgb, 255).astype(np.uint8)
        scene.add_geometry(q, node_name=f"part_{i + 1:02d}", geom_name=f"part_{i + 1:02d}")

    if pin is not None:
        proto = pin.copy()
        proto.apply_translation(-proto.bounds.mean(axis=0))  # centered, axis along Z
        for k, (point, cut) in enumerate(pin_placements or []):
            q = proto.copy()
            if cut.axis != 2:  # Z-aligned pin -> cut axis (same permutation used to drill the hole)
                m = np.eye(4)
                m[:3, :] = _FROM_Z[cut.axis]
                q.apply_transform(m)
            q.apply_translation(np.asarray(point)[:3] + offset(np.asarray(point)[:3], skip=cut))
            q.visual.face_colors = PIN_COLOR
            scene.add_geometry(q, node_name=f"pin_{k + 1:02d}", geom_name=f"pin_{k + 1:02d}")
    return scene