"""AI generation pipelines (ComfyUI): Text -> 3D and Image -> 3D."""

from __future__ import annotations

import io
import random
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh
from forge_core import Context, OutputFile, Pipeline, PipelineParams
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import geometry as g
from .geometry.texture import outline_mask
from .generation import (DEFAULT_MODELS, comfy_for, has_alpha, hunyuan_image_to_mesh, hunyuan_multiview_to_mesh,
                         prepare_subject, prepare_views, trellis_for, trellis_image_to_mesh)
from .pipelines import derived_slug, mark_base, record, settings, write_mesh

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

OBJECT_HINTS = {
    "object": "a single game prop",
    "character": "a single game character, full body, standing in a neutral A-pose",
    "creature": "a single game creature, full body",
    "building": "a single game building, whole structure visible",
    "vehicle": "a single game vehicle",
    "weapon": "a single game weapon",
    "free": "",
}
VIEW_HINT = ("three-quarter front view, whole subject visible and centered, soft even studio lighting, "
             "no cast shadows, isolated on a plain solid white background")


class TrellisOptions(BaseModel):
    """Microsoft TRELLIS.2-4B (own ComfyUI :8190): sharp edges, faces and small parts; optional texture."""
    model_config = ConfigDict(title="TRELLIS.2")
    resolution: Literal[512, 1024, 1536] = Field(1024, description="voxel resolution: 512 fast, 1024 detailed, 1536 finest (slower)")
    texture: bool = Field(False, description="also texture the mesh in the same run (PBR baked to a texture)")
    texture_size: Literal[1024, 2048, 4096] = Field(2048, description="texture resolution when texture is on")
    steps: int = Field(12, ge=4, le=50, description="sampling steps per stage")
    guidance: float = Field(7.5, ge=1.0, le=15.0, description="how strictly it follows the image")


class HunyuanOptions(BaseModel):
    """Tencent Hunyuan3D 2.1 (dedicated ComfyUI :8189). Also the multi-view model when side views are given."""
    model_config = ConfigDict(title="Hunyuan3D 2.1")
    steps: int = Field(30, ge=1, le=100, description="sampling steps")
    cfg: float = Field(5.0, ge=1.0, le=15.0, description="how strictly it follows the image")
    octree_resolution: int = Field(256, ge=128, le=512, description="surface detail: 256 fast, 384/512 finer (more VRAM/time)")
    reconstruct: bool = Field(True, description="rebuild a clean closed surface (Poisson): fixes Hunyuan's noisy surfaces")


# old flat parameter names (still accepted: MCP agents and saved jobs use them) -> (block, field)
_LEGACY = {"steps": ("hunyuan", "steps"), "cfg": ("hunyuan", "cfg"), "octree_resolution": ("hunyuan", "octree_resolution"),
           "reconstruct": ("hunyuan", "reconstruct"), "trellis_resolution": ("trellis", "resolution"),
           "texture": ("trellis", "texture")}


class _MeshGenParams(PipelineParams):
    generator: Literal["trellis2", "hunyuan3d-2.1"] | None = Field(None, description=(
        "generation method. trellis2: Microsoft TRELLIS.2-4B, sharper and more detailed, own ComfyUI :8190 "
        "(comfy/install_trellis.ps1). hunyuan3d-2.1: dedicated ComfyUI :8189. Empty = automatic: project "
        "3d.generator, else trellis2 when its instance is running, else hunyuan3d-2.1. Each has its own options block"))
    trellis: TrellisOptions = Field(default_factory=TrellisOptions, description="options used when the method is trellis2")
    hunyuan: HunyuanOptions = Field(default_factory=HunyuanOptions, description="options used when the method is hunyuan3d-2.1")
    seed: int | None = Field(None, description="seed (empty = random)")
    target_faces: int | None = Field(None, ge=0, description="simplify the result to this (default: project 3d.poly_budget; 0 keeps full resolution)")
    keep_raw: bool = Field(True, description="also keep the dense surface before simplification (highres.glb, role=source): "
                           "source of normal maps and of 3d.from_raw")
    height: float | None = Field(None, gt=0, description="scale so the model is this tall in project units (default: project 3d.default_height)")
    fix_depth_tilt: bool = Field(True, description="thin elongated objects (staffs, swords) that come out leaning toward "
                                 "the camera are stood back into the image plane")

    @model_validator(mode="before")
    @classmethod
    def _legacy_flat_options(cls, data):
        if isinstance(data, dict) and any(k in data for k in _LEGACY):
            data = dict(data)
            for key, (block, field) in _LEGACY.items():
                if key in data:
                    value = data.pop(key)
                    opts = dict(data.get(block) or {})
                    opts.setdefault(field, value)
                    data[block] = opts
        return data


def _seed(seed: int | None) -> int:
    return seed if seed is not None and seed >= 0 else random.randint(0, 2**31 - 1)


def _models(ctx: Context) -> dict[str, str]:
    return {**DEFAULT_MODELS, **settings(ctx).models}


def _open(src) -> Image.Image:
    img = Image.open(src)
    img.load()
    return img


def _resolve_image_path(ctx: Context, ref: str) -> Path:
    """An asset ref (its primary image) or an image file."""
    from forge_core.runner import resolve_input

    av, path = resolve_input(ctx.project, ref)
    if av is not None:
        files = ctx.project.store.files_of(av, "primary") or ctx.project.store.files_of(av)
        path = next((f for f in files if f.suffix.lower() in IMAGE_EXTENSIONS), None)
        if path is None:
            raise ValueError(f"{ref} has no image file")
    return path


def _fetch(client, out) -> bytes:
    return client.download(out.filename, out.subfolder, out.type)


def _default_generator(ctx: Context, cfg) -> str:
    """TRELLIS.2 when its instance is up and set up (sharper, more detailed), else Hunyuan3D 2.1."""
    try:
        trellis_for(ctx, cfg.trellis_url)
        return "trellis2"
    except Exception:
        return "hunyuan3d-2.1"


def _image_to_mesh(ctx: Context, client, image: Image.Image, mask: Image.Image | None, params: _MeshGenParams,
                   seed: int, span: tuple[float, float],
                   side_views: dict[str, tuple[Image.Image, Image.Image | None]] | None = None) -> list[OutputFile]:
    """Shared tail of image/text -> 3D: prep, Hunyuan3D, cleanup, scale, write files.

    ``side_views`` (left/back/right images of the same subject) switch to Hunyuan3D 2.0 multi-view."""
    cfg = settings(ctx)
    models = _models(ctx)
    subject_path = ctx.version_dir / "input.png"
    extra_files: list[OutputFile] = []
    generator = params.generator or cfg.generator or _default_generator(ctx, cfg)
    textured = None
    if generator == "trellis2" and side_views:
        ctx.log("side views need the Hunyuan3D multi-view model: using it instead of TRELLIS.2")
        generator = "hunyuan3d-2.1"
    if generator == "trellis2":
        prepare_subject(image, mask).save(subject_path)
        # TRELLIS.2 wants the cut-out subject with real transparency, not composited on white
        rgba_path = ctx.version_dir / "input_rgba.png"
        prepare_subject(image, mask, keep_alpha=True).save(rgba_path)
        client = trellis_for(ctx, cfg.trellis_url)
        ctx.log(f"TRELLIS.2: {client.url}")
        # a textured mesh must not be decimated afterwards (UV seams split it open): TRELLIS reduces it to the
        # budget before texturing; untextured output stays dense and goes through our own simplification
        budget = cfg.poly_budget if params.target_faces is None else params.target_faces
        opts = params.trellis
        max_faces = budget if opts.texture and budget else 500_000
        graph, saves = trellis_image_to_mesh(client.upload_image(rgba_path), seed, resolution=opts.resolution,
                                             texture=opts.texture, texture_size=opts.texture_size, max_faces=max_faces,
                                             steps=opts.steps, guidance=opts.guidance)
        save_id = saves["mesh"]
        model_name, workflow = "TRELLIS.2-4B", f"trellis2-{opts.resolution}"
        extra_files.append(OutputFile(rgba_path, "source", "image/png"))
    elif side_views:
        if models["hunyuan3d_mv"] not in client.models("checkpoints"):
            raise RuntimeError(f"multi-view model missing in ComfyUI ({models['hunyuan3d_mv']}); install it with: "
                               "uv run python comfy/download_models.py hunyuan3d-2mv")
        prepared = prepare_views({"front": (image, mask), **side_views})
        names = {}
        for side, img in prepared.items():
            path = subject_path if side == "front" else ctx.version_dir / f"view_{side}.png"
            img.save(path)
            names[side] = client.upload_image(path)
            if side != "front":
                extra_files.append(OutputFile(path, "source", "image/png"))
        h = params.hunyuan
        graph, save_id = hunyuan_multiview_to_mesh(names, seed, models=models, steps=h.steps, cfg=h.cfg,
                                                   octree_resolution=h.octree_resolution)
        model_name, workflow = models["hunyuan3d_mv"], "hunyuan3d-2mv"
        ctx.log(f"multi-view: {', '.join(names)}")
    else:
        prepare_subject(image, mask).save(subject_path)
        graph, save_id = hunyuan_image_to_mesh(client.upload_image(subject_path), seed, models=models,
                                               steps=params.hunyuan.steps, cfg=params.hunyuan.cfg,
                                               octree_resolution=params.hunyuan.octree_resolution)
        model_name, workflow = models["hunyuan3d"], "hunyuan3d-2.1"
    a, b = span
    ctx.check_cancelled()
    ctx.progress(a, f"generating mesh ({workflow})")
    out_nodes = list(saves.values()) if generator == "trellis2" else [save_id]
    outs = client.run_workflow(graph.to_dict(), output_nodes=out_nodes,
                               on_progress=lambda v, m: ctx.progress(a + (b - a) * v, "generating mesh"))
    by_node = {o.node_id: o for o in outs}
    if save_id not in by_node:
        raise RuntimeError("ComfyUI returned no mesh")
    raw = trimesh.load(io.BytesIO(_fetch(client, by_node[save_id])), file_type="glb", force="mesh")
    ctx.log(f"raw mesh: {len(raw.faces)} faces")
    dense = None
    if generator == "trellis2" and params.trellis.texture and saves.get("textured") in by_node:
        textured = trimesh.load(io.BytesIO(_fetch(client, by_node[saves["textured"]])), file_type="glb", force="mesh")
        if saves.get("dense") in by_node:
            dense = trimesh.load(io.BytesIO(_fetch(client, by_node[saves["dense"]])), file_type="glb", force="mesh")

    ctx.progress(b, "cleaning up")
    reconstruct = generator != "trellis2" and params.hunyuan.reconstruct
    if textured is not None:
        mesh, actions = textured, ["textured by TRELLIS.2: topology kept (UVs)"]
    elif reconstruct:
        mesh, actions = g.reconstruct_surface(raw)
    else:
        mesh, actions = g.clean(raw, min_component_ratio=0.02, max_hole_edges=0)
    for a in actions:
        ctx.log(a)
    if params.fix_depth_tilt:
        mesh, tilt = g.remove_depth_tilt(mesh)
        if tilt:
            ctx.log(f"elongated object leaning {tilt:.0f}° in depth: rotated back into the image plane")
    if dense is None:
        dense = mesh.copy()  # what simplification starts from: the detail source for normal maps
    if len(dense.faces) > 500_000:  # plenty for normal maps, half the file size of Hunyuan's ~1M-face surface
        dense = g.simplify(dense, target_faces=500_000, quality=0.6, planar=False)
    target = cfg.poly_budget if params.target_faces is None else params.target_faces
    if textured is not None:
        target = 0  # already at the budget (see above)
    if target and len(mesh.faces) > target:
        # higher quality threshold + no planar quadric: rounder small forms (buttons, folds, fingers), crisper
        # creases than the generic defaults at the same face count
        mesh = g.simplify(mesh, target_faces=target, quality=0.6, planar=False)
        # decimation leaves a few 1-2 triangle slivers floating around: drop pieces under 1% of the model's size
        mesh, dropped = g.drop_fragments(mesh)
        if dropped:
            ctx.log(f"removed {dropped} tiny fragments left by simplification")
    height = params.height or cfg.default_height
    if height:
        mesh.apply_scale(height / mesh.extents[1])
    lo, hi = mesh.bounds  # glTF is Y-up: stand on the ground, centered
    mesh.apply_translation([-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2])

    # the dense surface in the model's own space (scale, placement): normal maps and 3d.from_raw use it as is
    high, _ = (g.pbr.align_highres(dense, mesh) if len(dense.faces) > len(mesh.faces) else (None, 0.0))
    if textured is not None:
        from .tex_pipelines import finish_pbr, write_pbr_files

        maps = finish_pbr(ctx, {"model": mesh}, normal_map=high is not None, high=high)
        mesh = maps["model"]
        extra_files += write_pbr_files(ctx, maps)
    files = [write_mesh(ctx, mesh, "model", "glb", "primary"), OutputFile(subject_path, "source", "image/png"),
             *extra_files]
    if params.keep_raw:
        files.append(write_mesh(ctx, high if high is not None else raw, "highres", "glb", "source"))
    record(ctx, None, mesh, generation={"model": model_name, "raw_faces": int(max(len(raw.faces), len(dense.faces))),
                                        "options": (params.trellis if generator == "trellis2" else params.hunyuan).model_dump(),
                                        "comfy": client.url})
    ctx.meta["seed"] = seed
    ctx.meta["workflow"] = workflow
    return files


# ---- 3d.image_to_3d -------------------------------------------------------------

Side = Literal["left", "back", "right"]


class ImageTo3DParams(_MeshGenParams):
    remove_background: bool | None = Field(None, description="cut out the subject first (default: only if the image has no transparency)")
    side_views: dict[Side, str] = Field(default_factory=dict, description=(
        "extra images of the same subject by side, the input image being the front: "
        '{"left": "slug@v002" | file, "back": ..., "right": ...}. "left" = its own left side (it faces '
        'the left of the image), "right" = its right side (faces the right of the image). Any subset; uses '
        "Hunyuan3D 2.0 multi-view (better hidden sides and proportions, less detail than 2.1 from one image)"))
    strip_outline: bool = Field(True, description="remove a sticker-style outline/halo around the subject "
                                "(it would become geometry); only acts when one is detected")


class ImageTo3D(Pipeline):
    id = "3d.image_to_3d"
    title = "Image → 3D"
    description = ("Build a 3D mesh from an image with the chosen method (TRELLIS.2 or Hunyuan3D 2.1), optionally with "
                   "left/back/right views of the same subject (Hunyuan3D 2.0 multi-view). Background removed and subject "
                   "centered automatically.")
    resources = {"gpu"}
    kind = "mesh"
    Params = ImageTo3DParams
    requires_input = True

    def default_slug(self, params: ImageTo3DParams) -> str:
        return derived_slug(params, "3d")

    def run(self, ctx: Context, params: ImageTo3DParams) -> list[OutputFile]:
        from forge_2d.graphs import remove_background

        files = [f for f in ctx.input_files("primary") + ctx.input_files() if f.suffix.lower() in IMAGE_EXTENSIONS]
        if not files:
            raise ValueError("input must be an image (png/jpg/webp)")
        client = comfy_for(ctx, settings(ctx).comfy_url)
        ctx.log(f"ComfyUI: {client.url}")

        def cutout(path: Path) -> tuple[Image.Image, Image.Image | None]:
            image = _open(path)
            mask = None
            remove = params.remove_background if params.remove_background is not None else not has_alpha(image)
            if remove:
                graph, node = remove_background(client.upload_image(path), models=_models(ctx))
                outs = client.run_workflow(graph.to_dict(), output_nodes=[node])
                mask = _open(io.BytesIO(_fetch(client, outs[0])))
            if params.strip_outline:
                rgba = image.convert("RGBA")
                if mask is not None:
                    rgba.putalpha(mask.convert("L").resize(rgba.size))
                keep, width = outline_mask(rgba)
                if width:
                    ctx.log(f"{path.name}: removed a {width}px outline around the subject")
                    mask = Image.fromarray(keep.astype(np.uint8) * 255)
            return image, mask

        ctx.progress(0.02, "preparing images")
        image, mask = cutout(files[0])
        sides = {side: cutout(_resolve_image_path(ctx, ref)) for side, ref in params.side_views.items() if ref}
        files = _image_to_mesh(ctx, client, image, mask, params, _seed(params.seed), span=(0.1, 0.95),
                               side_views=sides)
        mark_base(ctx)
        return files


# ---- 3d.text_to_3d --------------------------------------------------------------

class TextTo3DParams(_MeshGenParams):
    prompt: str = Field(..., description="what to build (content only; the project style is appended)")
    asset_type: Literal["object", "character", "creature", "building", "vehicle", "weapon", "free"] = "object"
    use_style: bool = Field(True, description="append the project style prompt and LoRAs to the concept image")
    image_steps: int = Field(8, ge=1, le=50, description="Z-Image Turbo steps for the concept image")
    raw_prompt: bool = Field(False, description="use the prompt verbatim for the concept image")


def concept_prompt(params: TextTo3DParams, style: dict) -> str:
    if params.raw_prompt:
        return params.prompt
    parts = [OBJECT_HINTS[params.asset_type], params.prompt.strip().rstrip("."), VIEW_HINT]
    if params.use_style and style.get("prompt"):
        parts.append(style["prompt"].strip().rstrip("."))
    return ". ".join(p for p in parts if p) + "."


class TextTo3D(Pipeline):
    id = "3d.text_to_3d"
    title = "Text → 3D"
    description = ("Prompt -> concept image in the project style (Z-Image Turbo) -> 3D mesh (TRELLIS.2 or Hunyuan3D 2.1). "
                   "The concept image is kept as a source file.")
    resources = {"gpu"}
    kind = "mesh"
    Params = TextTo3DParams

    def run(self, ctx: Context, params: TextTo3DParams) -> list[OutputFile]:
        from forge_2d.graphs import DEFAULT_MODELS as MODELS_2D
        from forge_2d.graphs import text2img_zimage

        style = ctx.style("3d")
        full = concept_prompt(params, style)
        seed = _seed(params.seed)
        client = comfy_for(ctx, settings(ctx).comfy_url)
        ctx.log(f"ComfyUI: {client.url}")
        ctx.log(f"concept prompt: {full}")
        models_2d = {**MODELS_2D, **(ctx.style("2d").get("models") or {})}
        loras = (style.get("loras") or []) if params.use_style else []
        graph, img_id, mask_id = text2img_zimage(full, 1024, 1024, seed, params.image_steps, models=models_2d,
                                                 remove_bg=True, loras=loras)
        ctx.progress(0.02, "concept image")
        outs = client.run_workflow(graph.to_dict(), output_nodes=[img_id, mask_id],
                                   on_progress=lambda v, m: ctx.progress(0.02 + 0.2 * v, "concept image"))
        first = {}
        for o in outs:
            first.setdefault(o.node_id, o)
        image = _open(io.BytesIO(_fetch(client, first[img_id])))
        mask = _open(io.BytesIO(_fetch(client, first[mask_id])))
        concept = ctx.version_dir / "concept.png"
        image.save(concept)
        files = _image_to_mesh(ctx, client, image, mask, params, seed, span=(0.25, 0.95))
        files.append(OutputFile(concept, "source", "image/png"))
        ctx.meta["prompt"] = params.prompt
        ctx.meta.setdefault("extra", {})["concept_prompt"] = full
        return files


# ---- 3d.from_raw -------------------------------------------------------------------

def _colors_onto(source: trimesh.Trimesh, target: trimesh.Trimesh, samples: int = 1_000_000) -> bool:
    """Give ``target`` vertex colors read off ``source``'s surface (texture or vertex colors) at the nearest point.
    Returns False when the source has no colors."""
    from scipy.spatial import cKDTree

    tex = g.texture.texture_image(source)
    vc = source.visual.vertex_colors if source.visual.kind == "vertex" else None
    if tex is None and (vc is None or np.ptp(np.asarray(vc)[:, :3]) == 0):
        return False
    pts, fidx = trimesh.sample.sample_surface(source, samples, seed=0)
    bary = trimesh.triangles.points_to_barycentric(source.triangles[fidx], pts)
    if tex is not None:
        uv = np.einsum("nk,nkd->nd", bary, np.asarray(source.visual.uv)[source.faces[fidx]])
        colors = trimesh.visual.color.uv_to_color(uv, tex.convert("RGBA"))
    else:
        colors = np.einsum("nk,nkd->nd", bary, np.asarray(vc, dtype=np.float64)[source.faces[fidx]]).astype(np.uint8)
    _, nearest = cKDTree(pts).query(target.vertices, k=1, workers=-1)
    target.visual = trimesh.visual.ColorVisuals(target, vertex_colors=colors[nearest])
    return True


class FromRawParams(PipelineParams):
    source: Literal["auto", "highres", "raw"] = Field("auto", description=(
        "dense surface to start from: highres.glb (cleaned, before simplification; newer assets) or raw.glb (the "
        "generator's direct output, older assets). auto = highres when present"))
    cleanup: Literal["none", "clean", "reconstruct"] = Field("clean", description=(
        "none: keep the raw topology as is; clean: remove duplicates, degenerate faces and loose bits; "
        "reconstruct: rebuild one closed surface (Poisson; smooths small detail away)"))
    min_component_ratio: float = Field(0.02, ge=0, le=0.5, description="drop loose pieces smaller than this fraction of the model's size")
    smooth: int = Field(0, ge=0, le=20, description="Taubin smoothing passes (removes voxel stair-steps without shrinking)")
    target_faces: int | None = Field(None, ge=0, description="simplify to this (default: project 3d.poly_budget; 0 keeps every triangle)")
    height: float | None = Field(None, gt=0, description="scale to this height (default: same size and place as the current model)")
    keep_colors: bool = Field(True, description="carry the current model's colors (texture or vertex colors) over to the "
                                                "rebuilt surface as vertex colors")


class FromRaw(Pipeline):
    id = "3d.from_raw"
    title = "Rework from raw"
    description = ("Rebuild a generated model from its dense surface (the raw generator output / high-res mesh kept with "
                   "it), with your own cleanup, smoothing and triangle budget - when the raw looks better than model.glb.")
    resources = {"cpu"}
    kind = "mesh"
    Params = FromRawParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: FromRawParams) -> list[OutputFile]:
        import pymeshlab

        from .pipelines import load_input

        path = self._source(ctx, params.source)
        if path is None:
            raise ValueError("no raw/high-res surface in this asset's history (only meshes generated by "
                             "3d.image_to_3d / 3d.text_to_3d keep one)")
        current = load_input(ctx)
        dense = g.load_mesh(path)
        ctx.log(f"source: {path.parent.name}/{path.name} ({len(dense.faces):,} faces)")

        ctx.progress(0.1, "cleaning up")
        if params.cleanup == "reconstruct":
            mesh, actions = g.reconstruct_surface(dense, min_component_ratio=0.01)
        elif params.cleanup == "clean":
            mesh, actions = g.clean(dense, min_component_ratio=params.min_component_ratio, max_hole_edges=0)
        else:
            mesh, flipped = g.repair.orient_outward(dense)
            actions = [f"orientation: {flipped} piece(s) turned outward"] if flipped else []
        for a in actions:
            ctx.log(a)
        if params.smooth:
            ms = pymeshlab.MeshSet()
            ms.add_mesh(pymeshlab.Mesh(mesh.vertices, mesh.faces))
            ms.apply_coord_taubin_smoothing(stepsmoothnum=params.smooth)
            mesh = trimesh.Trimesh(ms.current_mesh().vertex_matrix(), ms.current_mesh().face_matrix(), process=False)
            ctx.log(f"Taubin smoothing: {params.smooth} passes")

        ctx.progress(0.5, "placing")
        placed, cost = g.pbr.align_highres(mesh, current)
        if placed is not None:
            mesh = placed
        else:  # it no longer lines up (the model was edited): stand it like a fresh generation
            ctx.log(f"the raw surface does not line up with the current model (fit {cost:.1e}): placed by size")
            mesh.apply_scale(current.extents[1] / mesh.extents[1])
            lo, hi = mesh.bounds
            mesh.apply_translation([-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2])
        if params.height:  # rescale in place: same footprint center, same floor
            lo, hi = mesh.bounds
            anchor = np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
            mesh.apply_translation(-anchor)
            mesh.apply_scale(params.height / mesh.extents[1])
            mesh.apply_translation(anchor)

        cfg = settings(ctx)
        target = cfg.poly_budget if params.target_faces is None else params.target_faces
        if target and len(mesh.faces) > target:
            ctx.progress(0.7, "simplifying")
            mesh = g.simplify(mesh, target_faces=target, quality=0.6, planar=False)
        mesh, dropped = g.drop_fragments(mesh, min_ratio=max(params.min_component_ratio / 2, 0.005))
        if dropped:
            ctx.log(f"removed {dropped} tiny fragments")
        colored = params.keep_colors and _colors_onto(current, mesh)
        if colored:
            ctx.log("colors carried over from the current model (vertex colors; texture it again for a UV texture)")
        elif g.pbr.maps_of(current) is not None:
            ctx.log("note: the new surface has no texture yet - run 3d.texture_ai (or the Paint tab) again")
        record(ctx, current, mesh, from_raw={"source": f"{path.parent.name}/{path.name}", "cleanup": params.cleanup,
                                             "smooth": params.smooth, "target_faces": target})
        return [write_mesh(ctx, mesh, "model", "glb", "primary")]

    @staticmethod
    def _source(ctx: Context, which: str) -> Path | None:
        store = ctx.project.store
        av = ctx.input
        names = {"auto": ("highres.glb", "raw.glb"), "highres": ("highres.glb",), "raw": ("raw.glb",)}[which]
        for _ in range(20):
            if av is None:
                return None
            sources = {f.name: f for f in store.files_of(av, "source")}
            for name in names:
                if name in sources:
                    return sources[name]
            if not av.parent:
                return None
            av = store.get(av.parent)
        return None


GEN_PIPELINES: list[Pipeline] = [TextTo3D(), ImageTo3D(), FromRaw()]
