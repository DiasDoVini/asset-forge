"""Mesh editing operations: transform, plane cut, primitive/mesh booleans, text, mirror/symmetrize.

All coordinates are in the mesh's own space. ``apply_ops`` runs a list of operations in order;
pipelines call it with the mesh in Y-up (viewer) coordinates so what the user placed with the
gizmo in the browser is exactly what gets applied.

Booleans use manifold3d and need closed solids. Policy when the base isn't one:
- ``add``: falls back to merging the meshes (keeps textures/colors of the base);
- ``subtract`` / ``intersect`` / capped cuts: the base is first turned into a solid (UVs are lost).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Callable, Literal, Union

import manifold3d as m3d
import numpy as np
import trimesh
from pydantic import BaseModel, Field

from .bridge import from_manifold, to_manifold
from .repair import make_printable

Vec3 = tuple[float, float, float]
Matrix = list[list[float]]
BoolMode = Literal["add", "subtract", "intersect"]
IDENTITY: Matrix = np.eye(4).tolist()


class TransformOp(BaseModel):
    op: Literal["transform"] = "transform"
    matrix: Matrix = Field(..., description="4x4 row-major matrix applied to the whole mesh")


class CutOp(BaseModel):
    op: Literal["cut"] = "cut"
    point: Vec3 = Field((0.0, 0.0, 0.0), description="a point on the cutting plane")
    normal: Vec3 = Field((0.0, 1.0, 0.0), description="plane normal")
    keep: Literal["front", "back"] = Field("back", description="front = side the normal points to")
    cap: bool = Field(True, description="close the cut face (needs a solid; open meshes are cut without a cap)")


class PrimitiveOp(BaseModel):
    op: Literal["primitive"] = "primitive"
    shape: Literal["box", "cylinder", "sphere", "cone"] = "box"
    mode: BoolMode = "add"
    size: Vec3 = Field((1.0, 1.0, 1.0), description="bounding size x, y, z; cylinder/cone axis is +Y")
    matrix: Matrix = Field(default_factory=lambda: IDENTITY, description="placement of the primitive (centered at origin)")
    segments: int = Field(48, ge=3, le=256)


class TextOp(BaseModel):
    op: Literal["text"] = "text"
    text: str
    mode: BoolMode = Field("add", description="add = embossed, subtract = engraved")
    height: float = Field(1.0, gt=0, description="letter height (cap height ~ this)")
    depth: float = Field(0.1, gt=0, description="extrusion depth along +Z of the text frame")
    font: str = Field("arialbd", description="font file name in the system fonts folder, or a path")
    matrix: Matrix = Field(default_factory=lambda: IDENTITY, description="placement: text lies in its XY plane, centered, extruded from z=0 to z=depth")


class MirrorOp(BaseModel):
    op: Literal["mirror"] = "mirror"
    axis: Literal["x", "y", "z"] = "x"
    origin: float = Field(0.0, description="plane position along the axis")
    mode: Literal["mirror", "symmetrize_positive", "symmetrize_negative"] = Field(
        "mirror", description="mirror = flip everything; symmetrize_* = keep that half and mirror it onto the other")


class MeshOp(BaseModel):
    op: Literal["mesh"] = "mesh"
    asset: str = Field(..., description="asset ref (slug, slug@v003) or file path of the mesh to combine")
    mode: BoolMode = "add"
    matrix: Matrix = Field(default_factory=lambda: IDENTITY, description="placement of that mesh, in its own Y-up coordinates")


EditOp = Annotated[Union[TransformOp, CutOp, PrimitiveOp, TextOp, MirrorOp, MeshOp], Field(discriminator="op")]

MeshLoader = Callable[[str], trimesh.Trimesh]


def apply_ops(mesh: trimesh.Trimesh, ops: list, load_mesh: MeshLoader | None = None,
              log: Callable[[str], None] = lambda m: None) -> trimesh.Trimesh:
    for i, op in enumerate(ops, 1):
        mesh = _apply(mesh, op, load_mesh, log)
        log(f"op {i} ({op.op}): {len(mesh.faces)} faces")
    return mesh


def _apply(mesh, op, load_mesh, log) -> trimesh.Trimesh:
    if isinstance(op, TransformOp):
        out = mesh.copy()
        out.apply_transform(np.asarray(op.matrix, dtype=np.float64))
        return out
    if isinstance(op, CutOp):
        return cut(mesh, op.point, op.normal, op.keep, op.cap, log)
    if isinstance(op, PrimitiveOp):
        tool = primitive(op.shape, op.size, op.segments).transform(_affine(op.matrix))
        return boolean(mesh, tool, op.mode, log)
    if isinstance(op, TextOp):
        tool = text_solid(op.text, op.height, op.depth, op.font).transform(_affine(op.matrix))
        return boolean(mesh, tool, op.mode, log)
    if isinstance(op, MirrorOp):
        return mirror(mesh, "xyz".index(op.axis), op.origin, op.mode, log)
    if isinstance(op, MeshOp):
        if load_mesh is None:
            raise ValueError("mesh op needs a mesh loader")
        other = load_mesh(op.asset).copy()
        other.apply_transform(np.asarray(op.matrix, dtype=np.float64))
        try:
            tool = to_manifold(other)
        except ValueError:
            if op.mode == "add" and not _is_solid(mesh):
                return trimesh.util.concatenate([mesh, other])
            log(f"{op.asset}: not a closed solid, solidifying it")
            tool = to_manifold(make_printable(other)[0])
        return boolean(mesh, tool, op.mode, log)
    raise ValueError(f"unknown op: {op}")


# ---- building blocks -------------------------------------------------------------------

def _affine(matrix: Matrix) -> np.ndarray:
    m = np.asarray(matrix, dtype=np.float64)
    if m.shape != (4, 4):
        raise ValueError("matrix must be 4x4")
    return m[:3, :]


def _is_solid(mesh: trimesh.Trimesh) -> bool:
    try:
        to_manifold(mesh)
        return True
    except ValueError:
        return False


def _as_solid(mesh: trimesh.Trimesh, log) -> m3d.Manifold:
    try:
        return to_manifold(mesh)
    except ValueError:
        log("base mesh is not a closed solid: converting it (UVs/colors are lost)")
        return to_manifold(make_printable(mesh)[0])


def primitive(shape: str, size: Vec3, segments: int = 48) -> m3d.Manifold:
    sx, sy, sz = (float(v) for v in size)
    if shape == "box":
        return m3d.Manifold.cube([sx, sy, sz], True)
    if shape == "sphere":
        return m3d.Manifold.sphere(0.5, segments).scale([sx, sy, sz])
    # cylinder / cone: built along Z, turned so the axis is +Y, then scaled to the box
    top = 0.0 if shape == "cone" else 0.5
    solid = m3d.Manifold.cylinder(1.0, 0.5, top, segments, True)
    solid = solid.transform(np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0]], dtype=np.float64))  # z -> y
    return solid.scale([sx, sy, sz])


def boolean(mesh: trimesh.Trimesh, tool: m3d.Manifold, mode: str, log) -> trimesh.Trimesh:
    if mode == "add" and not _is_solid(mesh):
        log("base is not a closed solid: merging without boolean union")
        return trimesh.util.concatenate([mesh, from_manifold(tool)])
    base = _as_solid(mesh, log)
    result = {"add": base + tool, "subtract": base - tool, "intersect": base ^ tool}[mode]
    if result.is_empty():
        raise ValueError(f"{mode} produced an empty mesh")
    return from_manifold(result)


def cut(mesh: trimesh.Trimesh, point: Vec3, normal: Vec3, keep: str = "back", cap: bool = True, log=lambda m: None) -> trimesh.Trimesh:
    n = np.asarray(normal, dtype=np.float64)
    n /= np.linalg.norm(n)
    if keep == "back":
        n = -n
    # keep the side n points to
    if cap and _is_solid(mesh):
        offset = float(np.dot(n, point))
        kept, _ = to_manifold(mesh).split_by_plane(n.tolist(), offset)
        if kept.is_empty():
            raise ValueError("cut removed the whole mesh")
        return from_manifold(kept)
    if cap:
        log("mesh is not a closed solid: cut without capping")
    out = trimesh.intersections.slice_mesh_plane(mesh, plane_normal=n, plane_origin=np.asarray(point, dtype=np.float64))
    if len(out.faces) == 0:
        raise ValueError("cut removed the whole mesh")
    return out


def mirror(mesh: trimesh.Trimesh, axis: int, origin: float = 0.0, mode: str = "mirror", log=lambda m: None) -> trimesh.Trimesh:
    reflect = np.eye(4)
    reflect[axis, axis] = -1
    reflect[axis, 3] = 2 * origin
    if mode == "mirror":
        out = mesh.copy()
        out.apply_transform(reflect)  # trimesh flips the winding for negative determinants
        return out
    normal = np.zeros(3)
    normal[axis] = 1.0 if mode == "symmetrize_positive" else -1.0
    point = np.zeros(3)
    point[axis] = origin
    if _is_solid(mesh):
        half, _ = to_manifold(mesh).split_by_plane(normal.tolist(), float(np.dot(normal, point)))
        mirrored = half.transform(reflect[:3, :])
        return from_manifold(half + mirrored)
    half = trimesh.intersections.slice_mesh_plane(mesh, plane_normal=normal, plane_origin=point)
    other = half.copy()
    other.apply_transform(reflect)
    out = trimesh.util.concatenate([half, other])
    out.merge_vertices()
    return out


# ---- text ----------------------------------------------------------------------------

def _font_path(font: str) -> str | None:
    if os.path.isfile(font):
        return font
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in (font, f"{font}.ttf", f"{font}.otf"):
        if (fonts / name).is_file():
            return str(fonts / name)
    return None


def text_outline(text: str, font: str = "arialbd", px: int = 160) -> m3d.CrossSection:
    """Text as 2D polygons, 1 unit = 1 em-height pixel row scaled so letters are ~1 tall, centered."""
    from PIL import Image, ImageDraw, ImageFont
    from skimage import measure

    path = _font_path(font)
    face = ImageFont.truetype(path, px) if path else ImageFont.load_default(px)
    left, top, right, bottom = ImageDraw.Draw(Image.new("L", (1, 1))).multiline_textbbox((0, 0), text, font=face, align="center")
    pad = px // 4
    img = Image.new("L", (right - left + 2 * pad, bottom - top + 2 * pad), 0)
    ImageDraw.Draw(img).multiline_text((pad - left, pad - top), text, fill=255, font=face, align="center")
    arr = np.asarray(img, dtype=np.float64) / 255.0
    contours = measure.find_contours(np.pad(arr, 1), 0.5)
    if not contours:
        raise ValueError("text produced no outline")
    # (row, col) -> (x, y) with y up; EvenOdd fill makes the counters of 'o', 'a', ... holes
    polys = [np.column_stack([c[:, 1], -c[:, 0]]) / px for c in contours if len(c) >= 3]
    xs = m3d.CrossSection(polys, m3d.FillRule.EvenOdd).simplify(0.5 / px)
    lo = np.min([p.min(axis=0) for p in polys], axis=0)
    hi = np.max([p.max(axis=0) for p in polys], axis=0)
    center = (lo + hi) / 2
    return xs.translate([-center[0], -center[1]])


def text_solid(text: str, height: float, depth: float, font: str = "arialbd") -> m3d.Manifold:
    """Extruded text: lies in the XY plane, centered, from z=0 to z=depth, ~``height`` tall."""
    outline = text_outline(text, font)
    pts = np.vstack([np.asarray(p) for p in outline.to_polygons()])
    scale = height / max(float(np.ptp(pts[:, 1])), 1e-9)
    return outline.scale([scale, scale]).extrude(depth)
