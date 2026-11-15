"""3D exporters: download a mesh asset in another format / unit / axis convention, or a print kit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh
from forge_core import Exporter, ExportContext, ExportParams
from pydantic import Field

from . import geometry as g
from .pipelines import Y_UP_TO_Z_UP, up_axis
from .settings import Settings3D

Units = Literal["mm", "cm", "m", "in"]
TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


def _mesh_file(ctx: ExportContext) -> Path:
    files = [f for f in ctx.files("primary") + ctx.files() if f.suffix.lower() in g.MESH_EXTENSIONS]
    if not files:
        raise ValueError("asset has no mesh file")
    return files[0]


def _source_units(ctx: ExportContext, path: Path) -> str:
    if ctx.asset.kind == "print" or up_axis(path) == 2:
        return "mm"
    return Settings3D.from_style(ctx.style("3d")).units


class MeshExportParams(ExportParams):
    format: Literal["glb", "obj", "stl", "ply", "3mf"] = Field("glb", description="file format (OBJ comes zipped with its .mtl and textures)")
    units: Units | None = Field(None, description="convert to these units (default: keep the asset's units)")
    scale: float = Field(1.0, gt=0, description="extra uniform scale")
    up_axis: Literal["auto", "y", "z"] = Field("auto", description="auto = the format's convention (glTF/OBJ Y-up, STL/3MF/PLY Z-up)")
    pivot: Literal["keep", "bottom_center", "center"] = "keep"
    max_faces: int | None = Field(None, ge=4, description="simplify on export if the mesh has more faces")


class MeshExporter(Exporter):
    id = "3d.mesh"
    title = "Malha 3D"
    description = "Export the mesh as GLB, OBJ, STL, PLY or 3MF, converting units, scale, up axis and pivot."
    kinds = {"mesh", "print"}
    Params = MeshExportParams

    def run(self, ctx: ExportContext, p: MeshExportParams) -> list[Path]:
        src = _mesh_file(ctx)
        mesh = g.load_mesh(src)
        if up_axis(src) == 2:  # work in Y-up
            mesh.apply_transform(np.linalg.inv(Y_UP_TO_Z_UP))
        if p.max_faces and len(mesh.faces) > p.max_faces:
            mesh = g.simplify(mesh, target_faces=p.max_faces)
        factor = p.scale
        if p.units:
            factor *= TO_MM[_source_units(ctx, src)] / TO_MM[p.units]
        if factor != 1.0:
            mesh.apply_scale(factor)
        lo, hi = mesh.bounds
        if p.pivot == "center":
            mesh.apply_translation(-(lo + hi) / 2)
        elif p.pivot == "bottom_center":
            mesh.apply_translation([-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2])
        target_up = {"auto": up_axis(Path(f"x.{p.format}")), "y": 1, "z": 2}[p.up_axis]
        if target_up == 2:
            mesh.apply_transform(Y_UP_TO_Z_UP)

        name = ctx.asset.slug
        if p.format == "obj":
            text, extras = trimesh.exchange.obj.export_obj(mesh, include_texture=True, return_texture=True,
                                                            mtl_name=f"{name}.mtl")
            out = [ctx.out_dir / f"{name}.obj"]
            out[0].write_text(text, encoding="utf-8")
            for fname, data in (extras or {}).items():
                (ctx.out_dir / fname).write_bytes(data)
                out.append(ctx.out_dir / fname)
            return out
        return [g.save_mesh(mesh, ctx.out_dir / f"{name}.{p.format}")]


class PrintKitParams(ExportParams):
    format: Literal["stl", "3mf"] = Field("stl", description="format for every printable file")


class PrintKitExporter(Exporter):
    id = "3d.print_kit"
    title = "Kit de impressão"
    description = "All printable files (model or split parts + dowel pin) and a README with counts and tolerances."
    kinds = {"print"}
    Params = PrintKitParams

    def run(self, ctx: ExportContext, p: PrintKitParams) -> list[Path]:
        parts = [f for f in ctx.files("part") if f.suffix.lower() in g.MESH_EXTENSIONS]
        sources = parts or [f for f in ctx.files("primary") if f.suffix.lower() in g.MESH_EXTENSIONS and f.suffix.lower() != ".glb"]
        if not sources:
            sources = [_mesh_file(ctx)]
        out: list[Path] = []
        for f in sources:
            mesh = g.load_mesh(f)
            if up_axis(f) == 1:  # printing is Z-up
                mesh.apply_transform(Y_UP_TO_Z_UP)
            out.append(g.save_mesh(mesh, ctx.out_dir / f"{f.stem}.{p.format}"))
        out.append(self._readme(ctx, out))
        return out

    @staticmethod
    def _readme(ctx: ExportContext, files: list[Path]) -> Path:
        extra = ctx.asset.extra or {}
        split = extra.get("split") or {}
        printing = extra.get("print") or {}
        lines = [f"{ctx.asset.name or ctx.asset.slug} ({ctx.asset.ref})", ""]
        pin = split.get("pin") or {}
        for f in files:
            count = ""
            if f.stem.startswith("pin_x"):
                count = f"  <- imprimir {pin.get('count', f.stem.removeprefix('pin_x'))}x"
            lines.append(f"- {f.name}{count}")
        if pin.get("count"):
            lines += ["", f"Pinos: {pin['count']}x de diametro {pin['diameter_mm']} mm e comprimento {pin['length_mm']} mm.",
                      f"Furos: diametro {pin['hole_diameter_mm']} mm, profundidade {pin.get('hole_depth_mm', '?')} mm em cada lado de cada corte.",
                      "As pecas saem deitadas sobre a face de corte. Cole os pinos em um dos lados e depois encaixe as pecas."]
        if printing.get("warnings"):
            lines += ["", "Avisos:"] + [f"- {w}" for w in printing["warnings"]]
        report = [f for f in ctx.files("report")]
        if report:
            try:
                data = json.loads(report[0].read_text(encoding="utf-8"))
                if "part_extents_mm" in data:
                    lines += ["", "Tamanho das pecas (mm): " + "; ".join("x".join(str(round(v)) for v in e) for e in data["part_extents_mm"])]
            except (OSError, ValueError):
                pass
        path = ctx.out_dir / "LEIA-ME.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


class AnimationGlbParams(ExportParams):
    clips: list[str] | None = Field(None, description="only these clips (default: all)")


class AnimationGlbExporter(Exporter):
    id = "3d.animation_glb"
    title = "GLB animado"
    description = "glTF binary with the part hierarchy and animation clips (Unity, Godot, Unreal, Blender, three.js)."
    kinds = {"animation"}
    Params = AnimationGlbParams

    def run(self, ctx: ExportContext, p: AnimationGlbParams) -> list[Path]:
        from .geometry.anim import Clips, Rig, export_glb

        vdir = ctx.version_dir
        rig = Rig.model_validate_json((vdir / "rig.json").read_text(encoding="utf-8"))
        clips = Clips.model_validate_json((vdir / "clips.json").read_text(encoding="utf-8")).clips
        if p.clips:
            missing = set(p.clips) - {c.name for c in clips}
            if missing:
                raise ValueError(f"unknown clips: {sorted(missing)}")
            clips = [c for c in clips if c.name in p.clips]
        meshes = {part.name: g.load_mesh(vdir / part.file) for part in rig.rigid}
        skin = None
        if rig.skin is not None:  # skeleton rig: the skinned mesh + its weights
            import numpy as np

            from .geometry.anim import SKIN_KEY

            meshes[SKIN_KEY] = g.load_mesh(vdir / rig.skin.mesh)
            data = np.load(vdir / rig.skin.weights)
            order = {b.name: i for i, b in enumerate(rig.bones)}
            remap = np.array([order[str(n)] for n in data["joint_names"]], dtype=np.uint16)
            joints, weights = data["joints"], data["weights"]
            if len(data["positions"]) != len(meshes[SKIN_KEY].vertices):
                from .geometry.skeleton import remap_weights

                joints, weights = remap_weights(data["positions"], joints, weights, meshes[SKIN_KEY])
            skin = (remap[joints], weights)
        return [export_glb(rig, meshes, clips, ctx.out_dir / f"{ctx.asset.slug}.glb", skin=skin)]


class MediaParams(ExportParams):
    sheet: str = Field(..., description="uploaded PNG sprite sheet with the rendered frames (the web UI renders it)")
    frames: int = Field(..., ge=1, le=2000)
    columns: int = Field(..., ge=1)
    fps: float = Field(24.0, gt=0, le=120)
    format: Literal["gif", "mp4", "webm"] = "gif"
    loop: bool = True
    name: str = Field("animation", pattern=r"^[A-Za-z0-9_\-]+$")


class AnimationMediaExporter(Exporter):
    id = "3d.animation_media"
    title = "GIF / vídeo"
    description = "Turns frames rendered by the web viewer (a sprite sheet) into a GIF, MP4 or WebM."
    kinds = {"animation"}
    Params = MediaParams
    show_in_dialog = False  # needs frames rendered by the Animar tab

    def run(self, ctx: ExportContext, p: MediaParams) -> list[Path]:
        from .media import frames_from_sheet, write_media

        frames = frames_from_sheet(Path(p.sheet), p.frames, p.columns)
        return [write_media(frames, ctx.out_dir / f"{ctx.asset.slug}-{p.name}.{p.format}", p.fps, p.loop)]


EXPORTERS: list[Exporter] = [MeshExporter(), PrintKitExporter(), AnimationGlbExporter(), AnimationMediaExporter()]
