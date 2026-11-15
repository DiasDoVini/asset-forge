"""Texturing pipelines: 3d.unwrap, 3d.texture_ai (project the source image + AI views), 3d.apply_texture (paint).

All three work on plain mesh assets (one ``model.glb``) and on animation assets (one texture per part).
A future Hunyuan3D-Paint backend plugs into 3d.texture_ai as another ``method`` - same inputs/outputs
(an unwrapped mesh + a texture per mesh).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh
from forge_core import Context, OutputFile, Pipeline, PipelineParams
from PIL import Image
from pydantic import Field

from . import geometry as g
from .geometry import pbr
from .geometry import texture as tx
from .pipelines import input_file, jsonable, settings, up_axis, write_mesh

Views = Literal["back", "left", "right"]


# ---- loading / saving either kind of asset ---------------------------------------------------------

def _is_animation(ctx: Context) -> bool:
    return any(f.name == "rig.json" for f in ctx.input_files())


def _load(ctx: Context) -> tuple[dict[str, trimesh.Trimesh], tuple | None]:
    """{name: mesh} in Y-up + a writer state. Plain meshes come back as {"model": mesh}."""
    if _is_animation(ctx):
        from .anim_pipelines import load_rig

        rig, clips, meshes = load_rig(ctx)
        return meshes, (rig, clips)
    src = input_file(ctx)
    mesh = g.load_mesh(src)
    if up_axis(src) == 2:
        from .pipelines import Y_UP_TO_Z_UP

        mesh.apply_transform(np.linalg.inv(Y_UP_TO_Z_UP))
    return {"model": mesh}, None


def finish_pbr(ctx: Context, meshes: dict[str, trimesh.Trimesh], normal_map: bool = True,
               high: trimesh.Trimesh | None = None) -> dict[str, trimesh.Trimesh]:
    """Complete each textured mesh's PBR set: metallic/roughness (uniform when the texturing gave none) and a
    tangent-space normal map baked from the dense surface the model was generated from (when one exists)."""
    from .edit_pipelines import find_highres

    if normal_map and high is None:
        path = find_highres(ctx)
        if path is None:
            ctx.log("normal map: no high-resolution surface in this asset's history (only generated models have one)")
        else:
            whole = trimesh.util.concatenate(list(meshes.values()))
            dense, _ = g.repair.orient_outward(g.load_mesh(path))  # older assets kept an inside-out copy
            high, cost = pbr.align_highres(dense, whole)
            ctx.log(f"normal map: baking from {path.parent.name}/{path.name}" if high is not None
                    else f"normal map: {path.name} does not line up with this mesh any more (fit {cost:.1e}); skipped")
    out = {}
    for name, mesh in meshes.items():
        maps = pbr.maps_of(mesh)
        if maps is None:
            out[name] = mesh
            continue
        if maps.metallic is None:
            maps.metallic = pbr.uniform(maps.base.size, pbr.DEFAULT_METALLIC)
        if maps.roughness is None:
            maps.roughness = pbr.uniform(maps.base.size, pbr.DEFAULT_ROUGHNESS)
        if high is not None:
            maps.normal = pbr.bake_normal_map(mesh, high, maps.base.size)
        out[name] = pbr.with_maps(mesh, maps)
    return out


def write_pbr_files(ctx: Context, meshes: dict[str, trimesh.Trimesh]) -> list[OutputFile]:
    """The texture set as separate images (engines import them as layers): base color, metallic, roughness,
    normal. Animation parts get a name prefix."""
    files = []
    for name, mesh in meshes.items():
        maps = pbr.maps_of(mesh)
        if maps is None:
            continue
        prefix = "" if len(meshes) == 1 else f"{name}_"
        for key, img in (("basecolor", maps.base), ("metallic", maps.metallic), ("roughness", maps.roughness),
                         ("normal", maps.normal)):
            if img is not None:
                path = ctx.version_dir / f"{prefix}{key}.png"
                img.save(path)
                files.append(OutputFile(path, "texture", "image/png"))
    return files


def _save(ctx: Context, meshes: dict[str, trimesh.Trimesh], state, extra_files: list[OutputFile],
          pbr_maps: bool = False, normal_map: bool = True) -> list[OutputFile]:
    if pbr_maps:
        meshes = finish_pbr(ctx, meshes, normal_map=normal_map)
    if state is not None:
        from .anim_pipelines import write_rig

        rig, clips = state
        return write_rig(ctx, rig, clips, meshes) + (write_pbr_files(ctx, meshes) if pbr_maps else []) + extra_files
    out = [write_mesh(ctx, meshes["model"], "model", "glb", "primary", src_up=1)]
    if pbr_maps:
        out += write_pbr_files(ctx, meshes)
    else:
        img = tx.texture_image(meshes["model"])
        if img is not None:
            path = ctx.version_dir / "basecolor.png"
            img.save(path)
            out.append(OutputFile(path, "texture", "image/png"))
    ctx.meta["kind"] = ctx.input.kind if ctx.input is not None else "mesh"
    ctx.meta.setdefault("extra", {})["stats"] = jsonable({"after": g.mesh_stats(meshes["model"])})
    return out + extra_files


def _ensure_uv(mesh: trimesh.Trimesh, resolution: int, force: bool = False) -> tuple[trimesh.Trimesh, tx.Texels, np.ndarray]:
    """(mesh with UVs, texels, current texture as RGBA array) - unwrapping and baking colors if needed."""
    image = tx.texture_image(mesh)
    if image is not None and not force:
        arr = np.asarray(image.convert("RGBA")).copy()
        return mesh, tx.rasterize_uv(mesh, (arr.shape[1], arr.shape[0])), arr
    un = tx.unwrap(mesh, resolution)
    texels = tx.rasterize_uv(un.mesh, un.size)
    return un.mesh, texels, tx.bake_source_colors(mesh, un, texels)


# ---- 3d.unwrap -----------------------------------------------------------------------------------

class UnwrapParams(PipelineParams):
    resolution: Literal[512, 1024, 2048, 4096] = Field(2048, description="texture size (animation parts: per part)")
    force: bool = Field(False, description="re-unwrap even if the mesh already has a UV texture")


class Unwrap(Pipeline):
    id = "3d.unwrap"
    title = "Prepare for painting (UV)"
    description = "Automatic UV unwrap (xatlas) with a texture holding the current colors, ready to paint or texture with AI."
    resources = {"cpu"}
    kind = "mesh"
    Params = UnwrapParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: UnwrapParams) -> list[OutputFile]:
        meshes, state = _load(ctx)
        res = params.resolution if state is None else min(params.resolution, 2048)
        out = {}
        for i, (name, mesh) in enumerate(meshes.items()):
            ctx.progress(i / len(meshes), f"unwrapping {name}")
            m, _, img = _ensure_uv(mesh, res, force=params.force)
            out[name] = tx.with_texture(m, img)
            ctx.log(f"{name}: {img.shape[1]}x{img.shape[0]} texture")
        return _save(ctx, out, state, [])


# ---- 3d.texture_ai -------------------------------------------------------------------------------

VIEW_PROMPTS = {
    "back": "Show this exact same object from directly behind (back view)",
    "left": "Show this exact same object seen from its left side (side view)",
    "right": "Show this exact same object seen from its right side (side view)",
}
# camera yaw relative to the front: the object faces +Z, so its left side (+X) is seen from yaw +90
VIEW_YAW = {"back": 180.0, "left": 90.0, "right": -90.0}


class TextureAIParams(PipelineParams):
    views: list[Views] = Field(default_factory=lambda: ["back", "left", "right"],
                               description="extra views generated with Qwen-Image-Edit (empty = project only the source image)")
    resolution: Literal[512, 1024, 2048, 4096] = Field(2048, description="texture size for meshes without UVs")
    source_image: str | None = Field(None, description="image asset or file to project (default: the asset's own source image)")
    image_steps: int = Field(4, ge=1, le=50)
    seed: int | None = None
    method: Literal["project", "hunyuan3d-paint"] = Field(
        "project", description="project: source image projected + Qwen-generated views (fast, any ComfyUI). "
                               "hunyuan3d-paint: Hunyuan3D 2.1 multiview PBR paint (best quality; needs the dedicated "
                               "instance with the native paint nodes + converted weights)")
    paint_steps: int = Field(20, ge=4, le=60, description="hunyuan3d-paint: sampling steps")
    paint_views: int = Field(6, ge=1, le=6, description="hunyuan3d-paint: number of generated views")
    side_views: dict[Views, str] = Field(default_factory=dict, description=(
        'real images of the other sides, {"left"|"back"|"right": asset or file} (same convention as '
        "3d.image_to_3d: left = the subject's own left side). project method: used instead of generating those "
        "sides with Qwen"))
    pbr: bool = Field(True, description=(
        "full PBR set: metallic + roughness (from the paint model, else uniform) and a normal map baked from the "
        "dense surface the model was generated from; the 4 maps are also saved as images"))
    source_view: tuple[float, float] | None = Field(None, description=(
        "project method: camera [yaw, pitch] in degrees of the source image, when the automatic fit picks a wrong one"))
    local_align: bool = Field(True, description=(
        "project method: after the global camera fit, shift the image band by band (head, torso, legs) onto the "
        "mesh silhouette - fixes local offsets from perspective/pose differences"))
    use_asset_views: bool = Field(True, description=(
        "also use the side views the mesh was generated from (multi-view 3d.image_to_3d keeps them as view_*.png)"))


class TextureAI(Pipeline):
    id = "3d.texture_ai"
    title = "AI texture"
    description = ("Color a mesh from its source image. project: the image is projected (camera found by silhouette "
                   "matching) and the back/sides are generated with Qwen-Image-Edit. hunyuan3d-paint: Hunyuan3D 2.1 "
                   "generates consistent multiview PBR images that are baked onto the UVs.")
    resources = {"gpu"}
    kind = "mesh"
    Params = TextureAIParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: TextureAIParams) -> list[OutputFile]:
        from .edit_pipelines import _mesh_or_image, find_source_image

        src_path = _mesh_or_image(ctx, params.source_image) if params.source_image else find_source_image(ctx)
        if src_path is None:
            raise ValueError("no source image to project (only generated meshes have one); pass source_image=<image asset or file>")
        source = Image.open(src_path)
        source.load()
        meshes, state = _load(ctx)
        names = list(meshes)
        prepared = {}
        for name in names:
            m, texels, base = _ensure_uv(meshes[name], params.resolution if state is None else 1024)
            prepared[name] = (m, texels, base)
        if params.method == "hunyuan3d-paint":
            if params.side_views:
                ctx.log("hunyuan3d-paint takes a single reference image: side_views ignored (use method=project)")
            return self._hunyuan(ctx, params, src_path, source, names, prepared, state)
        whole = trimesh.util.concatenate([prepared[n][0] for n in names])

        ctx.progress(0.05, "finding the source camera")
        if params.source_view:
            front, iou = tx.fit_view(whole, float(params.source_view[0]), float(params.source_view[1]), tx.subject_mask(source))
        else:
            front, iou = tx.align_view(whole, tx.subject_mask(source), yaws=range(0, 360, 15))
        ctx.log(f"source view: yaw {front.yaw:.0f}°, pitch {front.pitch:.0f}°, silhouette match {iou:.0%}")
        if iou < 0.45:
            ctx.log("warning: the source image does not match the mesh silhouette well; colors may land off")
        front_img = source.convert("RGB")
        if params.local_align:
            front_img, shift = tx.local_align(whole, front, front_img)
            if shift:
                ctx.log(f"source image: local alignment moved parts by up to {shift:.0f}px")
        views = [(front_img, front, 2.0)]
        extra_files: list[OutputFile] = []

        real = self._real_views(ctx, params)
        for side, path in real.items():
            img = Image.open(path).convert("RGB")
            v, iou = self._align_side(whole, front, side, img)
            ctx.log(f"{side} view ({path.name}): yaw {v.yaw:.0f}°, match {iou:.0%}" + ("" if iou >= 0.45 else " (skipped: poor match)"))
            if iou >= 0.45:
                if params.local_align:
                    img, _ = tx.local_align(whole, v, img)
                views.append((img, v, 1.5))  # real views outrank generated ones
        missing = [v for v in params.views if v not in real]
        if missing:
            views += self._generate_views(ctx, src_path, whole, front, params.model_copy(update={"views": missing}),
                                          extra_files)

        ctx.progress(0.85, "projecting")
        textures = tx.project_images([prepared[n][0] for n in names], [prepared[n][1] for n in names], views,
                                     [prepared[n][2] for n in names], occluder=whole)
        out = {n: tx.with_texture(prepared[n][0], t) for n, t in zip(names, textures)}
        ctx.meta.setdefault("extra", {})["texture_ai"] = jsonable({
            "source_image": src_path.name, "source_view": {"yaw": front.yaw, "pitch": front.pitch, "match": round(iou, 3)},
            "views": [{"yaw": v.yaw, "pitch": v.pitch} for _, v, _ in views[1:]],
            "real_views": {k: p.name for k, p in real.items()}})
        return _save(ctx, out, state, extra_files, pbr_maps=params.pbr)

    @staticmethod
    def _real_views(ctx: Context, params: TextureAIParams) -> dict[str, Path]:
        from .edit_pipelines import find_side_views
        from .gen_pipelines import _resolve_image_path

        found = find_side_views(ctx) if params.use_asset_views and not params.source_image else {}
        found.update({side: _resolve_image_path(ctx, ref) for side, ref in params.side_views.items() if ref})
        if found:
            ctx.log("real side views: " + ", ".join(f"{k}={p.name}" for k, p in found.items()))
        return found

    @staticmethod
    def _align_side(whole, front: tx.View, side: str, img: Image.Image):
        expected = (front.yaw + VIEW_YAW[side]) % 360
        yaws = [(expected + d) % 360 for d in range(-30, 31, 10)]
        return tx.align_view(whole, tx.subject_mask(img), yaws=yaws, pitches=(front.pitch,))

    @staticmethod
    def _hunyuan(ctx: Context, params: TextureAIParams, src_path: Path, source: Image.Image, names: list[str],
                 prepared: dict, state):
        """Paint the whole asset at once (parts merged so colors stay consistent across them), then copy the
        result into each mesh's own atlas."""
        import tempfile

        # a "sticker" outline in the reference reads as the subject's color to the paint model
        source, outline = tx.strip_outline(source)
        if outline:
            ctx.log(f"reference image: removed a {outline}px outline around the subject")

        from .gen_pipelines import _fetch, _models, _seed
        from .generation import comfy_for, hunyuan_paint, prepare_subject

        client = comfy_for(ctx, settings(ctx).comfy_url)
        models = _models(ctx)
        missing = [m for m in ("paint_unet", "paint_vae") if not _comfy_lists(client, models[m])]
        if missing or not _comfy_has_node(client, "Hunyuan3DPaintConditioning"):
            raise RuntimeError(
                f"Hunyuan3D paint is not available in ComfyUI at {client.url}: "
                + (f"missing models {[models[m] for m in missing]} (run comfy/convert_hunyuan_paint.py); " if missing else "")
                + ("" if _comfy_has_node(client, "Hunyuan3DPaintConditioning") else
                   "the native paint nodes are missing (dedicated instance: powershell -File comfy\\install.ps1)"))

        merged = trimesh.util.concatenate([prepared[n][0] for n in names])
        offsets = np.cumsum([0] + [len(prepared[n][0].faces) for n in names])[:-1]
        size = params.resolution
        ctx.progress(0.05, "unwrapping for the paint model")
        un = tx.unwrap(merged, size)
        seed = _seed(params.seed)
        with tempfile.TemporaryDirectory() as tmp:
            mesh_path = Path(tmp) / "paint_mesh.glb"
            g.save_mesh(trimesh.Trimesh(un.mesh.vertices, un.mesh.faces, visual=un.mesh.visual, process=False), mesh_path)
            ref_path = Path(tmp) / "paint_ref.png"
            prepare_subject(source, Image.fromarray(tx.subject_mask(source).astype(np.uint8) * 255)).save(ref_path)
            mesh_name = client.upload_image(mesh_path)
            ref_name = client.upload_image(ref_path)
        graph, nodes = hunyuan_paint(mesh_name, ref_name, seed, models=models, steps=params.paint_steps,
                                     num_views=params.paint_views, texture_size=size)
        ctx.progress(0.1, "painting (Hunyuan3D 2.1)")
        outs = client.run_workflow(graph.to_dict(), output_nodes=list(nodes.values()),
                                   on_progress=lambda v, m: ctx.progress(0.1 + 0.8 * v, "painting (Hunyuan3D 2.1)"))
        by_node = {}
        for o in outs:
            by_node.setdefault(o.node_id, o)
        albedo = Image.open(io.BytesIO(_fetch(client, by_node[nodes["albedo"]]))).convert("RGBA")
        files: list[OutputFile] = []
        mr = None
        if nodes["views"] in by_node:
            path = ctx.version_dir / "paint_views.png"
            Image.open(io.BytesIO(_fetch(client, by_node[nodes["views"]]))).save(path)
            files.append(OutputFile(path, "source", "image/png"))
        if nodes["mr"] in by_node:  # glTF layout already: G = roughness, B = metallic
            mr = Image.open(io.BytesIO(_fetch(client, by_node[nodes["mr"]]))).convert("RGB")

        ctx.progress(0.92, "applying textures")
        out = {}
        for name, offset in zip(names, offsets):
            m, texels, base = prepared[name]
            if len(names) == 1:
                # single mesh: use the paint atlas directly
                target, color, mr_img = un.mesh, albedo, mr
            else:
                target = m
                color = Image.fromarray(tx.transfer_texture(m, texels, base, un.mesh, albedo, int(offset)), "RGBA")
                mr_img = None if mr is None else Image.fromarray(
                    tx.transfer_texture(m, texels, base, un.mesh, mr, int(offset)), "RGBA").convert("RGB")
            if params.pbr and mr_img is not None:
                metallic, roughness = pbr.split_mr(mr_img)
                out[name] = pbr.with_maps(target, pbr.PBRMaps(color, metallic, roughness))
            else:
                out[name] = tx.with_texture(target, color)
        ctx.meta["seed"] = seed
        ctx.meta.setdefault("extra", {})["texture_ai"] = {"method": "hunyuan3d-paint", "source_image": src_path.name,
                                                          "steps": params.paint_steps, "views": params.paint_views}
        return _save(ctx, out, state, files, pbr_maps=params.pbr)

    @staticmethod
    def _generate_views(ctx, src_path: Path, whole, front: tx.View, params: TextureAIParams, files: list[OutputFile]):
        from forge_2d.graphs import DEFAULT_MODELS as MODELS_2D
        from forge_2d.graphs import qwen_edit

        from .gen_pipelines import _fetch, _seed
        from .generation import comfy_for

        client = comfy_for(ctx, settings(ctx).comfy_url)
        models = {**MODELS_2D, **(ctx.style("2d").get("models") or {})}
        name = client.upload_image(src_path)
        seed = _seed(params.seed)
        out = []
        for i, view in enumerate(params.views):
            ctx.check_cancelled()
            ctx.progress(0.1 + 0.7 * i / len(params.views), f"generating the {view} view")
            prompt = (f"{VIEW_PROMPTS[view]}. Keep exactly the same design, colors, materials, proportions and art "
                      "style. Plain white background, whole object visible, camera at the same height.")
            graph, img_id, _ = qwen_edit(prompt, [name], seed, params.image_steps, models=models)
            outs = client.run_workflow(graph.to_dict(), output_nodes=[img_id])
            img = Image.open(io.BytesIO(_fetch(client, outs[0]))).convert("RGB")
            path = ctx.version_dir / f"view_{view}.png"
            img.save(path)
            files.append(OutputFile(path, "source", "image/png"))
            v, iou = TextureAI._align_side(whole, front, view, img)
            ctx.log(f"{view} view: yaw {v.yaw:.0f}°, match {iou:.0%}" + ("" if iou >= 0.45 else " (skipped: poor match)"))
            if iou >= 0.45:
                out.append((img, v, 1.0))
        return out


def _comfy_lists(client, filename: str) -> bool:
    return any(filename in client.models(folder) for folder in ("diffusion_models", "unet", "vae"))


def _comfy_has_node(client, node: str) -> bool:
    try:
        return node in client.object_info(node)
    except Exception:
        return False


# ---- 3d.bake_pbr -------------------------------------------------------------------------------------

class BakePBRParams(PipelineParams):
    normal_map: bool = Field(True, description="bake the normal map from the dense surface the model was generated from")
    roughness: float | None = Field(None, ge=0, le=1, description="replace the roughness map with this uniform value")
    metallic: float | None = Field(None, ge=0, le=1, description="replace the metallic map with this uniform value")


class BakePBR(Pipeline):
    id = "3d.bake_pbr"
    title = "PBR maps"
    description = ("Complete a textured mesh's PBR set (base color, metallic, roughness, normal): normal map baked "
                   "from the high-res surface, uniform metallic/roughness where none exist. The 4 maps are saved as images.")
    resources = {"cpu"}
    kind = "mesh"
    Params = BakePBRParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: BakePBRParams) -> list[OutputFile]:
        meshes, state = _load(ctx)
        if not any(pbr.maps_of(m) is not None for m in meshes.values()):
            raise ValueError("the mesh has no texture yet: texture it first (3d.texture_ai or the Paint tab)")
        if params.roughness is not None or params.metallic is not None:
            for name, mesh in meshes.items():
                maps = pbr.maps_of(mesh)
                if maps is None:
                    continue
                if params.roughness is not None:
                    maps.roughness = pbr.uniform(maps.base.size, params.roughness)
                if params.metallic is not None:
                    maps.metallic = pbr.uniform(maps.base.size, params.metallic)
                meshes[name] = pbr.with_maps(mesh, maps)
        return _save(ctx, meshes, state, [], pbr_maps=True, normal_map=params.normal_map)


# ---- 3d.apply_texture ----------------------------------------------------------------------------

class ApplyTextureParams(PipelineParams):
    textures: dict[str, str] = Field(..., description='image path per mesh: {"model": ...} or {part name: ...} for animation assets')


class ApplyTexture(Pipeline):
    id = "3d.apply_texture"
    title = "Apply painted textures"
    description = "Replace the textures of a mesh (or of animation parts) with painted images, keeping the UVs."
    resources = {"cpu"}
    kind = "mesh"
    Params = ApplyTextureParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: ApplyTextureParams) -> list[OutputFile]:
        meshes, state = _load(ctx)
        unknown = set(params.textures) - set(meshes)
        if unknown:
            raise ValueError(f"unknown meshes: {sorted(unknown)}; available: {sorted(meshes)}")
        for name, path in params.textures.items():
            mesh = meshes[name]
            if not tx.has_uv_texture(mesh):
                raise ValueError(f"{name} has no UV texture yet; run 3d.unwrap first")
            img = Image.open(path).convert("RGBA")
            meshes[name] = tx.with_texture(mesh, img)
            ctx.log(f"{name}: texture {img.width}x{img.height}")
        return _save(ctx, meshes, state, [])


TEX_PIPELINES: list[Pipeline] = [Unwrap(), TextureAI(), ApplyTexture(), BakePBR()]
