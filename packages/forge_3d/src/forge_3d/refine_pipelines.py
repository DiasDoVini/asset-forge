"""3d.refine_region: regenerate a region of a generated mesh (face, hands, a weapon...) with more detail.

The region is cropped out of the source image (the view that shows it best), upscaled, turned into a mesh by
Hunyuan3D 2.1 — so the whole model capacity goes to that piece — then placed over the base and stitched in.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
import trimesh
from forge_core import Context, OutputFile, Pipeline, PipelineParams
from PIL import Image
from pydantic import BaseModel, Field

from . import geometry as g
from .anim_pipelines import Region, _inside_fn
from .geometry import refine as rf
from .geometry import texture as tx
from .pipelines import input_file, load_input, record, settings, write_mesh

Vec3 = tuple[float, float, float]


class RefineArea(BaseModel):
    """What to refine. Any combination; the region is the union."""
    points: list[Vec3] = Field(default_factory=list, description="brush stamps on the surface (Y-up model coordinates)")
    radius: float = Field(0.05, gt=0, description="brush radius (model units)")
    shapes: list[Region] = Field(default_factory=list, description="volumes (box/sphere/cylinder): faces inside them")
    prompt: str | None = Field(None, description='describe the part, e.g. "the face", "the right hand" (located by '
                                                 "Claude on the source image; needs ANTHROPIC_API_KEY)")
    image_box: tuple[float, float, float, float] | None = Field(
        None, description="[x0, y0, x1, y1] fractions (0..1) of the source image: everything of the model seen inside it")


class RefineRegionParams(PipelineParams):
    region: RefineArea
    source_image: str | None = Field(None, description="image to crop from (default: the asset's own source image)")
    octree_resolution: int = Field(384, ge=128, le=512, description="detail of the regenerated piece")
    steps: int = Field(30, ge=1, le=100)
    seed: int | None = None
    upscale: bool = Field(True, description="4x-upscale the crop before generating (sharper input, more detail)")
    margin: float = Field(0.35, ge=0.0, le=1.5, description="context around the region in the crop (fraction of its size)")
    blend: float = Field(0.12, ge=0.02, le=0.4, description="seam width: the new surface fades into the old one over "
                                                            "this fraction of the region size (wider = smoother seam, less new detail at the border)")
    target_faces: int | None = Field(None, ge=0, description="triangles of the result (default: same as the input; 0 = keep all)")
    keep_part: bool = Field(True, description="also save the regenerated piece (role=source)")


class RefineRegion(Pipeline):
    id = "3d.refine_region"
    title = "Refine region"
    description = ("Regenerate a region of a generated mesh (face, hands, an object) with much more detail: the region "
                   "is cropped from the source image, upscaled, regenerated with Hunyuan3D 2.1 and stitched back. Mark it "
                   "with brush points, volumes or a text description.")
    resources = {"gpu"}
    kind = "mesh"
    Params = RefineRegionParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: RefineRegionParams) -> list[OutputFile]:
        from .edit_pipelines import _mesh_or_image, find_side_views, find_source_image
        from .gen_pipelines import _fetch, _models, _seed
        from .generation import comfy_for, hunyuan_image_to_mesh, prepare_subject
        from .tex_pipelines import TextureAI

        if ctx.input is not None and ctx.input.kind == "animation":
            raise ValueError("refine the mesh before rigging it (animation assets are split into parts)")
        src_path = _mesh_or_image(ctx, params.source_image) if params.source_image else find_source_image(ctx)
        if src_path is None:
            raise ValueError("no source image to refine from (only generated meshes have one); pass source_image=")
        base = load_input(ctx)
        colors = base.visual.vertex_colors if base.visual.kind in ("vertex", "face") else None
        had_colors = tx.has_uv_texture(base) or (colors is not None and np.ptp(np.asarray(colors)[:, :3]) > 0)
        source = Image.open(src_path).convert("RGB")

        ctx.progress(0.02, "finding the camera")
        front, iou = tx.align_view(base, tx.subject_mask(source), yaws=range(-30, 31, 5), pitches=(0, 5, 10, 15))
        if iou < 0.6:
            front, iou = tx.align_view(base, tx.subject_mask(source), yaws=range(0, 360, 15))
        ctx.log(f"source view: yaw {front.yaw:.0f}°, pitch {front.pitch:.0f}°, match {iou:.0%}")

        mask = self._region_mask(ctx, params.region, base, front, source)
        if not mask.any():
            raise ValueError("the region selects no part of the model")
        ctx.log(f"region: {int(mask.sum())} of {len(base.faces)} faces")

        # the view that shows the region best: the source image, or a real side view when the front barely shows it.
        # Regions marked on the source image (image_box / prompt) always use it.
        views = [("front", source, front)]
        image_defined = params.region.image_box is not None or bool(params.region.prompt)
        if not params.source_image and not image_defined:
            for side, path in find_side_views(ctx).items():
                img = Image.open(path).convert("RGB")
                v, viou = TextureAI._align_side(base, front, side, img)
                if viou >= 0.45:
                    views.append((side, img, v))
        seen = [rf.visible_area(base, mask, t[2]) for t in views]
        best = int(np.argmax(seen))
        name, image, view = views[best] if seen[best] > 2 * seen[0] else views[0]
        if name != "front":
            ctx.log(f"using the {name} view (shows the region best)")
        if rf.visible_area(base, mask, view) < 0.15 * base.area_faces[mask].sum():
            ctx.log("warning: the region is mostly hidden in the available images; the new detail will be invented")

        rect = rf.crop_rect(base, mask, view, image.size, margin=params.margin)
        crop = image.crop(rect)
        crop_path = ctx.version_dir / "refine_crop.png"
        crop.save(crop_path)
        client = comfy_for(ctx, settings(ctx).comfy_url)
        big = crop
        if params.upscale:
            ctx.progress(0.06, "upscaling the crop")
            big = self._upscale(client, crop_path, crop) or crop
        k = big.width / crop.width
        cmask = tx.subject_mask(big)
        if not cmask.any():
            raise ValueError("the crop has no subject (is the source image on a plain background?)")
        ys, xs = np.nonzero(cmask)
        subject_rect = (rect[0] + xs.min() / k, rect[1] + ys.min() / k, rect[0] + (xs.max() + 1) / k, rect[1] + (ys.max() + 1) / k)
        part_input = ctx.version_dir / "refine_input.png"
        prepare_subject(big, Image.fromarray(cmask.astype(np.uint8) * 255)).save(part_input)

        seed = _seed(params.seed)
        graph, save_id = hunyuan_image_to_mesh(client.upload_image(part_input), seed, models=_models(ctx),
                                               steps=params.steps, octree_resolution=params.octree_resolution)
        ctx.check_cancelled()
        ctx.progress(0.1, "regenerating the region (Hunyuan3D 2.1)")
        outs = client.run_workflow(graph.to_dict(), output_nodes=[save_id],
                                   on_progress=lambda v, m: ctx.progress(0.1 + 0.55 * v, "regenerating the region"))
        if not outs:
            raise RuntimeError("ComfyUI returned no mesh")
        raw = trimesh.load(io.BytesIO(_fetch(client, outs[0])), file_type="glb", force="mesh")
        ctx.progress(0.68, "cleaning up the new piece")
        part, _ = g.reconstruct_surface(raw, depth=10)
        ctx.log(f"regenerated piece: {len(raw.faces)} raw faces")

        ctx.progress(0.75, "placing it on the model")
        near = rf.faces_in_rect(base, view, rect) | rf.dilate_faces(base, mask, 4)
        base_near = base.submesh([np.nonzero(near)[0]], append=True)
        part, placement = rf.place_part(part, view, subject_rect, base_near)
        ctx.log(f"fit: ICP error {placement.icp_cost:.2e}, scale correction {placement.scale:.3f}")
        if abs(placement.scale - 1) > 0.25:
            ctx.log("warning: the piece needed a large scale correction; check the result")

        ctx.progress(0.82, "stitching")
        merged = rf.merge_region(base, mask, part, band=params.blend)
        target = len(base.faces) if params.target_faces is None else params.target_faces
        out = merged
        if target and len(merged.faces) > target:
            ctx.progress(0.92, "simplifying")
            out = g.simplify(merged, target_faces=target, quality=0.6, planar=False)
            ctx.log(f"stitched surface: {len(merged.faces)} faces -> {len(out.faces)}")
        out, dropped = g.drop_fragments(out)
        if dropped:
            ctx.log(f"removed {dropped} tiny fragments")
        if had_colors:
            ctx.log("note: colors/textures are not carried over to the new surface; run 3d.texture_ai again")

        files = [write_mesh(ctx, out, "model", "glb"), OutputFile(crop_path, "source", "image/png"),
                 OutputFile(part_input, "source", "image/png")]
        if params.keep_part:
            piece = g.simplify(part, target_faces=150_000) if len(part.faces) > 150_000 else part
            files.append(write_mesh(ctx, piece, "refine_part", "glb", "source"))
        record(ctx, base, out, refine={"view": name, "crop": list(rect), "faces_region": int(mask.sum()),
                                       "icp_cost": placement.icp_cost, "scale": placement.scale,
                                       "octree_resolution": params.octree_resolution, "seed": seed})
        ctx.meta["seed"] = seed
        return files

    @staticmethod
    def _region_mask(ctx: Context, area: RefineArea, mesh: trimesh.Trimesh, view: tx.View, image: Image.Image) -> np.ndarray:
        mask = np.zeros(len(mesh.faces), bool)
        if area.points:
            mask |= rf.faces_near_points(mesh, area.points, area.radius)
        for shape in area.shapes:
            mask |= np.asarray(_inside_fn(shape)(mesh.triangles_center), bool)
        W, H = image.size
        if area.image_box:
            x0, y0, x1, y1 = area.image_box
            mask |= rf.faces_in_rect(mesh, view, (x0 * W, y0 * H, x1 * W, y1 * H))
        if area.prompt:
            box = locate_in_image(image, area.prompt)
            rect = (box[0] * W, box[1] * H, box[2] * W, box[3] * H)
            ctx.log(f'"{area.prompt}" located at {[round(v) for v in rect]} in the source image')
            mask |= rf.faces_in_rect(mesh, view, rect)
        return mask

    @staticmethod
    def _upscale(client, path: Path, crop: Image.Image) -> Image.Image | None:
        from forge_2d.graphs import Graph

        from .gen_pipelines import _fetch

        models = client.models("upscale_models")
        if not models:
            return None
        name = "4x-UltraSharp.safetensors" if "4x-UltraSharp.safetensors" in models else models[0]
        graph = Graph()
        up = graph("ImageUpscaleWithModel", upscale_model=graph("UpscaleModelLoader", model_name=name),
                   image=graph("LoadImage", image=client.upload_image(path)))
        save = graph("SaveImage", images=up, filename_prefix="forge/3d/refine")
        outs = client.run_workflow(graph.to_dict(), output_nodes=[save.id])
        return Image.open(io.BytesIO(_fetch(client, outs[0]))).convert("RGB") if outs else None


LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "box": {"type": "array", "items": {"type": "number"},
                "description": "x0, y0, x1, y1 as fractions of the image width/height (0..1), tight around the part"},
        "note": {"type": "string"},
    },
    "required": ["found", "box", "note"],
    "additionalProperties": False,
}


def locate_in_image(image: Image.Image, prompt: str, client=None) -> tuple[float, float, float, float]:
    """Bounding box (fractions of the image) of the part ``prompt`` describes, found by Claude."""
    from .planner import MODEL, PlannerUnavailable

    if client is None:
        try:
            import anthropic

            client = anthropic.Anthropic()
        except Exception as exc:
            raise PlannerUnavailable(f"Claude API not available ({exc}); mark the region with the brush or a volume, "
                                     "or set ANTHROPIC_API_KEY") from exc
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={"format": {"type": "json_schema", "schema": LOCATE_SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": f"This is a game asset concept. Give the bounding box of: {prompt}. "
                                         "Include the whole part with a little room around it."},
            ]}],
        )
    except Exception as exc:
        if "Authentication" in type(exc).__name__ or "auth" in str(exc).lower():
            raise PlannerUnavailable("Claude API credentials missing or invalid; use the brush or a volume, or set "
                                     "ANTHROPIC_API_KEY") from exc
        raise
    data = json.loads(next(b.text for b in response.content if b.type == "text"))
    box = [float(v) for v in data.get("box", [])][:4]
    if not data.get("found") or len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError(f'could not find "{prompt}" in the source image ({data.get("note", "")})')
    return tuple(min(1.0, max(0.0, v)) for v in box)  # type: ignore[return-value]


PIPELINES = [RefineRegion()]
