"""3D pipelines: thin wrappers binding forge_3d.geometry to the forge_core pipeline contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh
from forge_core import Context, OutputFile, Pipeline, PipelineParams
from forge_core.assets import parse_ref
from pydantic import Field

from . import geometry as g
from .settings import MeshFormat, Settings3D

FORMAT_MIME = {
    "glb": "model/gltf-binary",
    "obj": "model/obj",
    "stl": "model/stl",
    "ply": "application/ply",
    "3mf": "model/3mf",
}


# ---- helpers ----------------------------------------------------------------

def settings(ctx: Context) -> Settings3D:
    return Settings3D.from_style(ctx.style("3d"))


# glTF (and OBJ as exported by Blender & co.) are Y-up; STL/3MF/PLY are Z-up (CAD/printing).
Y_UP_EXTENSIONS = {".glb", ".gltf", ".obj"}
Y_UP_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])


def input_file(ctx: Context) -> Path:
    files = [f for f in ctx.input_files("primary") if f.suffix.lower() in g.MESH_EXTENSIONS]
    if not files:
        files = [f for f in ctx.input_files() if f.suffix.lower() in g.MESH_EXTENSIONS]
    if not files:
        raise ValueError("input asset has no mesh file (glb/gltf/obj/stl/ply/off/3mf)")
    return files[0]


def up_axis(path: Path) -> int:
    return 1 if path.suffix.lower() in Y_UP_EXTENSIONS else 2


def load_input(ctx: Context) -> trimesh.Trimesh:
    path = input_file(ctx)
    ctx.log(f"loading {path.name}")
    return g.load_mesh(path)


def load_input_z_up(ctx: Context) -> trimesh.Trimesh:
    """Input mesh in Z-up (what printing/splitting assume)."""
    path = input_file(ctx)
    mesh = load_input(ctx)
    if up_axis(path) == 1:
        mesh.apply_transform(Y_UP_TO_Z_UP)
    return mesh


def write_mesh(ctx: Context, mesh, name: str, fmt: str, role: str = "primary", src_up: int | None = None) -> OutputFile:
    """Save ``mesh``; when ``src_up`` (axis the mesh currently uses as up) differs from the
    convention of ``fmt``, rotate so the file opens upright in other tools."""
    path = ctx.version_dir / f"{name}.{fmt}"
    dst_up = up_axis(path)
    if src_up is not None and src_up != dst_up:
        mesh = mesh.copy()
        mesh.apply_transform(Y_UP_TO_Z_UP if src_up == 1 else np.linalg.inv(Y_UP_TO_Z_UP))
    g.save_mesh(mesh, path)
    return OutputFile(path, role, FORMAT_MIME.get(fmt))


def write_report(ctx: Context, report: dict, name: str = "report.json") -> OutputFile:
    path = ctx.version_dir / name
    path.write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
    return OutputFile(path, "report", "application/json")


def jsonable(obj):
    """Plain-JSON copy (numpy scalars/arrays -> Python), safe for asset.json."""
    return json.loads(json.dumps(obj, default=_json_default))


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def derived_slug(params: PipelineParams, suffix: str) -> str:
    ref = params.input_asset or "mesh"
    try:
        base, _ = parse_ref(ref)
    except ValueError:
        base = Path(ref).stem
    if Path(ref).suffix:
        base = Path(ref).stem
    return f"{base}-{suffix}"


def mark_base(ctx: Context) -> None:
    """Derived assets (lowpoly, print, parts...) point at what they came from (core index column `base`)."""
    if ctx.input is not None:
        ctx.meta.setdefault("extra", {})["base"] = ctx.input.ref


def record(ctx: Context, before: trimesh.Trimesh | None, after: trimesh.Trimesh, **extra) -> None:
    stats = {"after": g.mesh_stats(after)}
    if before is not None:
        stats["before"] = {k: v for k, v in g.mesh_stats(before).items() if k in ("vertices", "faces", "watertight", "components")}
    ctx.meta["kind"] = "mesh"
    ctx.meta.setdefault("extra", {}).update(jsonable({"stats": stats, **extra}))


# ---- 3d.simplify --------------------------------------------------------------

class SimplifyParams(PipelineParams):
    target_faces: int | None = Field(None, ge=4, description="target triangle count (default: project 3d.poly_budget)")
    ratio: float | None = Field(None, gt=0, le=1, description="keep this fraction of faces (overrides target_faces)")
    preserve_uv: bool = Field(True, description="keep UVs/texture (texture-aware quadric)")
    preserve_boundary: bool = True
    format: MeshFormat | None = Field(None, description="output format (default: project 3d.export_format)")


class Simplify(Pipeline):
    id = "3d.simplify"
    title = "Simplify"
    description = "Reduce polygon count with quadric edge collapse, keeping UVs and silhouette."
    resources = {"cpu"}
    kind = "mesh"
    Params = SimplifyParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: SimplifyParams) -> list[OutputFile]:
        cfg = settings(ctx)
        mesh = load_input(ctx)
        target = None if params.ratio else (params.target_faces or cfg.poly_budget)
        ctx.progress(0.2, f"decimating {len(mesh.faces)} faces")
        out = g.simplify(mesh, target_faces=target, ratio=params.ratio,
                         preserve_uv=params.preserve_uv, preserve_boundary=params.preserve_boundary)
        ctx.check_cancelled()
        record(ctx, mesh, out)
        ctx.log(f"{len(mesh.faces)} -> {len(out.faces)} faces")
        return [write_mesh(ctx, out, "model", params.format or cfg.export_format, src_up=up_axis(input_file(ctx)))]


# ---- 3d.lowpoly ---------------------------------------------------------------

class LowPolyParams(PipelineParams):
    target_faces: int | None = Field(None, ge=12, description="triangle count (default: project 3d.lowpoly_faces)")
    color_mode: Literal["vertex", "palette_texture", "none"] = Field(
        "palette_texture", description="palette_texture: tiny atlas + UVs (game-ready); vertex: flat vertex colors")
    use_project_palette: bool = Field(True, description="snap colors to style.palette when the project has one")
    max_colors: int = Field(16, ge=1, le=256, description="k-means colors when not using the project palette")
    remesh: bool = Field(False, description="isotropic remesh first (more even triangles, slower)")
    format: MeshFormat | None = None


class LowPoly(Pipeline):
    id = "3d.lowpoly"
    title = "Low Poly"
    description = "Stylized low-poly version: aggressive decimation, flat shading, flat colors sampled from the source."
    resources = {"cpu"}
    kind = "mesh"
    Params = LowPolyParams
    requires_input = True

    def default_slug(self, params: LowPolyParams) -> str:
        return derived_slug(params, "lowpoly")

    def run(self, ctx: Context, params: LowPolyParams) -> list[OutputFile]:
        cfg = settings(ctx)
        mesh = load_input(ctx)
        palette = cfg.palette if params.use_project_palette and cfg.palette else None
        ctx.progress(0.2, "building low poly")
        out, info = g.lowpoly(mesh, target_faces=params.target_faces or cfg.lowpoly_faces,
                              color_mode=params.color_mode, palette=palette,
                              max_colors=params.max_colors, remesh=params.remesh)
        ctx.check_cancelled()
        record(ctx, mesh, out, lowpoly=info)
        mark_base(ctx)
        fmt = params.format or cfg.export_format
        if fmt == "stl" and params.color_mode != "none":
            ctx.log("warning: STL cannot store colors")
        return [write_mesh(ctx, out, "model", fmt, src_up=up_axis(input_file(ctx)))]


# ---- 3d.repair ----------------------------------------------------------------

class RepairParams(PipelineParams):
    orientation_only: bool = Field(False, description="only turn inside-out pieces outward (keeps UVs/textures): fixes "
                                   "the 'peeled' look of models whose faces were flipped")
    make_solid: bool = Field(False, description="also force a single watertight solid (loses UVs; for printing/booleans)")
    reconstruct_surface: bool = Field(False, description="rebuild the surface (Poisson) - for messy AI/scan meshes the normal repair can't fix; loses UVs")
    min_component_ratio: float = Field(0.02, ge=0, le=1, description="drop floating bits smaller than this fraction of the model size")
    max_hole_edges: int = Field(2000, ge=0, description="fill holes bounded by up to this many edges")
    voxel_resolution: int = Field(256, ge=32, le=1024)
    format: MeshFormat | None = None


class Repair(Pipeline):
    id = "3d.repair"
    title = "Repair"
    description = "Clean up a mesh: duplicates, degenerate/non-manifold faces, floating bits, holes, normals."
    resources = {"cpu"}
    kind = "mesh"
    Params = RepairParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: RepairParams) -> list[OutputFile]:
        cfg = settings(ctx)
        mesh = load_input(ctx)
        ctx.progress(0.2, "cleaning")
        if params.orientation_only:
            out, flipped = g.repair.orient_outward(mesh)
            actions = [f"orientation: {flipped} piece(s) turned outward" if flipped else "orientation: already outward"]
        elif params.reconstruct_surface:
            out, actions = g.reconstruct_surface(mesh)
        elif params.make_solid:
            out, actions = g.make_printable(mesh, voxel_resolution=params.voxel_resolution,
                                            min_component_ratio=params.min_component_ratio)
        else:
            out, actions = g.clean(mesh, params.min_component_ratio, params.max_hole_edges)
        for a in actions:
            ctx.log(a)
        record(ctx, mesh, out, repair=actions)
        return [write_mesh(ctx, out, "model", params.format or cfg.export_format, src_up=up_axis(input_file(ctx)))]


# ---- 3d.transform (basic edit) --------------------------------------------------

class TransformParams(PipelineParams):
    scale: float = Field(1.0, gt=0, description="uniform scale factor")
    target_height: float | None = Field(None, gt=0, description="rescale so the height (up axis) equals this (project units)")
    rotate_deg: tuple[float, float, float] = Field((0.0, 0.0, 0.0), description="rotation around X, Y, Z in degrees (applied in that order)")
    translate: tuple[float, float, float] = (0.0, 0.0, 0.0)
    pivot: Literal["keep", "center", "bottom_center"] = Field("keep", description="move the origin after transforming")
    format: MeshFormat | None = None


class Transform(Pipeline):
    id = "3d.transform"
    title = "Transform"
    description = "Scale, rotate, translate and re-center the pivot (bottom center = standing on the ground)."
    resources = {"cpu"}
    kind = "mesh"
    Params = TransformParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: TransformParams) -> list[OutputFile]:
        cfg = settings(ctx)
        up = up_axis(input_file(ctx))
        out = load_input(ctx)
        for axis, deg in zip(np.eye(3), params.rotate_deg):
            if deg:
                out.apply_transform(trimesh.transformations.rotation_matrix(np.radians(deg), axis))
        if params.target_height:
            out.apply_scale(params.target_height / out.extents[up])
        elif params.scale != 1.0:
            out.apply_scale(params.scale)
        lo, hi = out.bounds
        if params.pivot == "center":
            out.apply_translation(-(lo + hi) / 2)
        elif params.pivot == "bottom_center":
            shift = -(lo + hi) / 2
            shift[up] = -lo[up]
            out.apply_translation(shift)
        out.apply_translation(params.translate)
        record(ctx, None, out)
        return [write_mesh(ctx, out, "model", params.format or cfg.export_format, src_up=up)]


# ---- 3d.convert -----------------------------------------------------------------

class ConvertParams(PipelineParams):
    format: MeshFormat = Field(..., description="target format")


class Convert(Pipeline):
    id = "3d.convert"
    title = "Convert"
    description = "Export the mesh in another file format (glb, obj, stl, ply, 3mf)."
    resources = {"cpu"}
    kind = "mesh"
    Params = ConvertParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: ConvertParams) -> list[OutputFile]:
        mesh = load_input(ctx)
        record(ctx, None, mesh)
        return [write_mesh(ctx, mesh, "model", params.format, src_up=up_axis(input_file(ctx)))]


# ---- 3d.print -------------------------------------------------------------------

class PrintParams(PipelineParams):
    source_units: Literal["mm", "cm", "m", "in"] | None = Field(None, description="units of the input mesh (default: project 3d.units)")
    target_height_mm: float | None = Field(None, gt=0, description="scale so the printed height is this")
    target_max_dim_mm: float | None = Field(None, gt=0, description="scale so the longest side is this")
    scale: float | None = Field(None, gt=0, description="extra uniform scale after unit conversion")
    repair: bool = True
    orient: bool = Field(True, description="auto-orient to minimize overhangs")
    voxel_resolution: int = Field(256, ge=32, le=1024, description="resolution for the watertight fallback remesh")
    check_walls: bool = True
    min_wall_mm: float | None = Field(None, gt=0, description="override printer.min_wall_mm")
    fit_to_printer: bool = Field(True, description="when no size is given and the model is too big, shrink it to fit the printer (off: keep size and use 3d.split)")


class PrintOptimize(Pipeline):
    id = "3d.print"
    title = "Print Optimizer"
    description = ("Make a mesh printable: real-world scale in mm, watertight manifold solid, auto-orientation, "
                   "wall-thickness and build-volume checks. Outputs STL + 3MF + report.")
    resources = {"cpu"}
    kind = "mesh"
    Params = PrintParams
    requires_input = True

    def default_slug(self, params: PrintParams) -> str:
        return derived_slug(params, "print")

    def run(self, ctx: Context, params: PrintParams) -> list[OutputFile]:
        cfg = settings(ctx)
        profile = cfg.printer
        if params.min_wall_mm:
            profile = profile.model_copy(update={"min_wall_mm": params.min_wall_mm})
        mesh = load_input_z_up(ctx)
        ctx.progress(0.1, "optimizing for print")
        out, report = g.print_optimize(
            mesh, profile, source_units=params.source_units or cfg.units,
            target_height_mm=params.target_height_mm, target_max_dim_mm=params.target_max_dim_mm,
            scale=params.scale, repair=params.repair, orient=params.orient,
            voxel_resolution=params.voxel_resolution, check_walls=params.check_walls,
            fit_to_volume=params.fit_to_printer and not (params.target_height_mm or params.target_max_dim_mm or params.scale),
        )
        if "scaled_to_fit" in report:
            ctx.log(f"no size given: scaled by {report['scaled_to_fit']} to fit {profile.name}")
        ctx.check_cancelled()
        for w in report["warnings"]:
            ctx.log(f"warning: {w}")
        ctx.meta["kind"] = "print"
        ctx.meta["extra"] = jsonable({"stats": {"after": report["output"]}, "print": {
            "fits_build_volume": report["fits_build_volume"], "warnings": report["warnings"],
            "scaled_to_fit": report.get("scaled_to_fit"),
            "printer": profile.name}})
        mark_base(ctx)
        ctx.progress(0.9, "writing files")
        return [
            write_mesh(ctx, out, "model", "stl", "primary"),
            write_mesh(ctx, out, "model", "3mf", "print"),
            write_report(ctx, report),
        ]


# ---- 3d.split -------------------------------------------------------------------

class SplitParams(PipelineParams):
    build_volume_mm: tuple[float, float, float] | None = Field(None, description="override printer.build_volume_mm")
    cuts: list[tuple[Literal["x", "y", "z"], float]] | None = Field(
        None, description='manual cuts as [axis, position_mm], e.g. [["z", 120]]; default: automatic to fit the bed')
    margin_mm: float = Field(5.0, ge=0, description="safety margin inside the build volume")
    connectors: bool = Field(True, description="add dowel-pin holes on each cut and output a pin model")
    pin_diameter_mm: float | None = Field(None, gt=0, description="dowel diameter (default: scaled to the model, 4-10 mm)")
    pin_length_mm: float | None = Field(None, gt=0, description="dowel length (default: 2.5x the diameter)")
    tolerance_mm: float | None = Field(None, ge=0, description="hole clearance (default: printer.tolerance_mm)")
    max_pins_per_section: int = Field(2, ge=1, le=8)
    orient_parts: bool = True
    source_units: Literal["mm", "cm", "m", "in"] | None = Field(
        None, description="units of the input (default: mm if the input came from 3d.print, else project 3d.units)")
    repair: bool = Field(True, description="solidify the input first if it is not a watertight solid")


class Split(Pipeline):
    id = "3d.split"
    title = "Print Part Splitter"
    description = "Cut a model into parts that fit the printer, with dowel-pin connectors. One STL per part + pin + exploded preview."
    resources = {"cpu"}
    kind = "print"
    Params = SplitParams
    requires_input = True

    def default_slug(self, params: SplitParams) -> str:
        return derived_slug(params, "parts")

    def run(self, ctx: Context, params: SplitParams) -> list[OutputFile]:
        cfg = settings(ctx)
        profile = cfg.printer
        mesh = load_input_z_up(ctx)
        units = params.source_units or ("mm" if ctx.input is not None and ctx.input.kind == "print" else cfg.units)
        mesh = g.to_millimeters(mesh, units)
        if params.repair and not g.is_printable_solid(mesh):
            ctx.progress(0.1, "solidifying input")
            mesh, actions = g.make_printable(mesh)
            for a in actions:
                ctx.log(a)
        cuts = None
        if params.cuts:
            cuts = [g.Cut("xyz".index(a), float(pos)) for a, pos in params.cuts]
        ctx.progress(0.3, "cutting")
        res = g.split_for_print(
            mesh,
            build_volume_mm=params.build_volume_mm or profile.build_volume_mm,
            cuts=cuts,
            margin_mm=params.margin_mm,
            connectors=params.connectors,
            pin_diameter_mm=params.pin_diameter_mm,
            pin_length_mm=params.pin_length_mm,
            tolerance_mm=profile.tolerance_mm if params.tolerance_mm is None else params.tolerance_mm,
            min_wall_mm=max(profile.min_wall_mm, 1.2),
            max_pins_per_section=params.max_pins_per_section,
            orient_parts=params.orient_parts,
        )
        ctx.check_cancelled()
        ctx.progress(0.8, f"writing {len(res.parts)} parts")
        outputs = [write_mesh(ctx, g.exploded_scene(res.assembled, res.cuts, res.pin, res.pin_placements), "exploded", "glb", "primary", src_up=2)]
        for i, part in enumerate(res.parts, 1):
            outputs.append(write_mesh(ctx, part, f"part_{i:02d}", "stl", "part"))
        if res.pin is not None:
            outputs.append(write_mesh(ctx, res.pin, f"pin_x{res.pin_count}", "stl", "part"))
        if not res.info["all_fit"]:
            ctx.log("warning: some parts still exceed the build volume")
        if res.info["sections_without_pins"]:
            ctx.log(f"warning: {res.info['sections_without_pins']} cut section(s) too thin for pins")
        ctx.meta["kind"] = "print"
        ctx.meta["extra"] = jsonable({"split": res.info})
        mark_base(ctx)
        outputs.append(write_report(ctx, res.info))
        return outputs


PIPELINES: list[Pipeline] = [Simplify(), LowPoly(), Repair(), Transform(), Convert(), PrintOptimize(), Split()]
