"""Editing pipelines: manual operations (3d.edit) and AI edits (3d.ai_edit)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import trimesh
from forge_core import Context, OutputFile, Pipeline, PipelineParams
from pydantic import Field

from . import geometry as g
from .geometry.edit import EditOp, apply_ops
from .pipelines import Y_UP_TO_Z_UP, input_file, settings, up_axis, write_mesh
from .settings import MeshFormat


def _load_y_up(path: Path) -> trimesh.Trimesh:
    mesh = g.load_mesh(path)
    if up_axis(path) == 2:
        mesh.apply_transform(np.linalg.inv(Y_UP_TO_Z_UP))
    return mesh


def _mesh_file_of(ctx: Context, ref: str) -> Path:
    from forge_core.runner import resolve_input

    av, path = resolve_input(ctx.project, ref)
    if av is not None:
        files = [f for f in ctx.project.store.files_of(av, "primary") + ctx.project.store.files_of(av)
                 if f.suffix.lower() in g.MESH_EXTENSIONS]
        if not files:
            raise ValueError(f"{ref} has no mesh file")
        path = files[0]
    return Path(path)


# ---- 3d.edit ------------------------------------------------------------------------

class EditParams(PipelineParams):
    ops: list[EditOp] = Field(..., min_length=1, description=(
        "operations applied in order, in Y-up viewer coordinates: transform, cut, primitive (add/subtract/intersect "
        "box/cylinder/sphere/cone), text (emboss/engrave), mirror (mirror/symmetrize), mesh (combine another asset)"))
    format: MeshFormat | None = Field(None, description="output format (default: same as the input)")


class Edit(Pipeline):
    id = "3d.edit"
    title = "Edit"
    description = ("Manual edits as a list of operations: move/rotate/scale, plane cut, boolean with primitives or "
                   "another mesh, embossed/engraved text, mirror/symmetrize. Output is a new version of the asset.")
    resources = {"cpu"}
    kind = "mesh"
    Params = EditParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: EditParams) -> list[OutputFile]:
        src = input_file(ctx)
        mesh = _load_y_up(src)
        ctx.log(f"{len(params.ops)} operation(s) on {src.name}")
        out = apply_ops(mesh, params.ops, load_mesh=lambda ref: _load_y_up(_mesh_file_of(ctx, ref)), log=ctx.log)
        ctx.check_cancelled()
        fmt = params.format or (src.suffix.lower().lstrip(".") if src.suffix.lower() in {".glb", ".obj", ".stl", ".ply", ".3mf"}
                                else settings(ctx).export_format)
        ctx.meta["kind"] = ctx.input.kind if ctx.input is not None else "mesh"
        ctx.meta["extra"] = {"stats": {"after": g.mesh_stats(out)}, "edit": [op.model_dump(mode="json") for op in params.ops]}
        return [write_mesh(ctx, out, "model", fmt, "primary", src_up=1)]


# ---- 3d.ai_edit ---------------------------------------------------------------------

DERIVED_IMAGE_PREFIXES = ("view_", "refine_", "paint_", "texture", "basecolor", "metallic", "roughness", "normal")


def find_highres(ctx: Context, max_depth: int = 20) -> Path | None:
    """The dense surface a generated mesh was simplified from (highres.glb, or the older raw.glb), searching
    this version and its ancestors. It may sit in another scale/placement: align it before use."""
    store = ctx.project.store
    av = ctx.input
    for _ in range(max_depth):
        if av is None:
            return None
        sources = {f.name: f for f in store.files_of(av, "source")}
        for name in ("highres.glb", "raw.glb"):
            if name in sources:
                return sources[name]
        if not av.parent:
            return None
        av = store.get(av.parent)
    return None


def find_source_image(ctx: Context, max_depth: int = 20) -> Path | None:
    """The image a generated asset came from: this version's source images, else its ancestors'."""
    store = ctx.project.store
    av = ctx.input
    for _ in range(max_depth):
        if av is None:
            return None
        images = [f for f in store.files_of(av, "source") if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
        # prefer the prepared subject (centered on white), then the concept image
        for name in ("edited.png", "input.png", "concept.png"):
            for f in images:
                if f.name == name:
                    return f
        # other images derived along the way (generated/real side views, refine crops, paint views) are not
        # the subject's source: keep walking up
        own = [f for f in images if not f.name.startswith(DERIVED_IMAGE_PREFIXES)]
        if own:
            return own[0]
        if not av.parent:
            return None
        av = store.get(av.parent)
    return None


def find_side_views(ctx: Context, max_depth: int = 20) -> dict[str, Path]:
    """Real side views next to the source image: what a multi-view 3d.image_to_3d was generated from
    (view_left/back/right.png in the version holding input.png). Views generated by 3d.texture_ai don't count."""
    store = ctx.project.store
    av = ctx.input
    for _ in range(max_depth):
        if av is None:
            return {}
        sources = {f.name: f for f in store.files_of(av, "source")}
        if "input.png" in sources:
            if av.pipeline != "3d.image_to_3d":
                return {}
            return {side: sources[f"view_{side}.png"] for side in ("left", "back", "right") if f"view_{side}.png" in sources}
        if not av.parent:
            return {}
        av = store.get(av.parent)
    return {}


class AIEditParams(PipelineParams):
    instruction: str = Field(..., description='what to change, e.g. "open the lid", "add two curved horns", "make it taller"')
    keep_style: bool = Field(True, description="ask the image editor to keep the art style, framing and everything else")
    seed: int | None = Field(None, description="3D seed (default: the asset's own seed, so unchanged parts stay similar)")
    image_steps: int = Field(4, ge=1, le=50, description="Qwen-Image-Edit steps (4 with the Lightning LoRA)")
    steps: int = Field(30, ge=1, le=100)
    octree_resolution: int = Field(256, ge=128, le=512)
    target_faces: int | None = Field(None, ge=0)
    source_image: str | None = Field(None, description="override: image asset or file to edit instead of the asset's own source image")


class AIEdit(Pipeline):
    id = "3d.ai_edit"
    title = "AI Edit"
    description = ("Edit a generated mesh with words: the source image is edited with Qwen-Image-Edit and the mesh "
                   "is regenerated (Hunyuan3D 2.1, same seed). Best for shape changes; the old version is kept.")
    resources = {"gpu"}
    kind = "mesh"
    Params = AIEditParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: AIEditParams) -> list[OutputFile]:
        from forge_2d.graphs import DEFAULT_MODELS as MODELS_2D
        from forge_2d.graphs import qwen_edit
        from forge_2d.prompts import edit_instruction
        from PIL import Image

        from .gen_pipelines import _fetch, _image_to_mesh, _MeshGenParams, _seed
        from .generation import comfy_for

        if params.source_image:
            src = _mesh_or_image(ctx, params.source_image)
        else:
            src = find_source_image(ctx)
        if src is None:
            raise ValueError("this asset has no source image to edit (only meshes made by 3d.text_to_3d / "
                             "3d.image_to_3d have one); pass source_image=<image asset or file>")
        ctx.log(f"editing source image {src.name}")
        client = comfy_for(ctx, settings(ctx).comfy_url)
        models_2d = {**MODELS_2D, **(ctx.style("2d").get("models") or {})}
        missing = [m for m in (models_2d["qwen_edit_unet"], models_2d["qwen_clip"]) if not _comfy_has(client, m)]
        if missing:
            raise RuntimeError(f"Qwen-Image-Edit models missing in ComfyUI: {missing}. "
                               "Run: uv run python comfy/download_models.py qwen-image-edit-2511 qwen-2.5-vl-7b")

        image = Image.open(src).convert("RGB")
        prompt = edit_instruction(params.instruction, ctx.style("2d"), keep_style=params.keep_style)
        prompt += " Keep a single object isolated on a plain white background, whole object visible."
        seed = params.seed if params.seed is not None else (ctx.input.seed if ctx.input and ctx.input.seed is not None else _seed(None))
        name = client.upload_image(src)
        graph, img_id, mask_id = qwen_edit(prompt, [name], seed, params.image_steps, models=models_2d, remove_bg=True)
        ctx.progress(0.02, "editing image (Qwen-Image-Edit)")
        outs = client.run_workflow(graph.to_dict(), output_nodes=[img_id, mask_id],
                                   on_progress=lambda v, m: ctx.progress(0.02 + 0.25 * v, "editing image"))
        first = {}
        for o in outs:
            first.setdefault(o.node_id, o)
        edited = Image.open(io.BytesIO(_fetch(client, first[img_id])))
        mask = Image.open(io.BytesIO(_fetch(client, first[mask_id])))
        edited_path = ctx.version_dir / "edited.png"
        edited.save(edited_path)

        gen = _MeshGenParams(seed=seed, hunyuan={"steps": params.steps, "octree_resolution": params.octree_resolution},
                             target_faces=params.target_faces)
        files = _image_to_mesh(ctx, client, edited, mask, gen, seed, span=(0.3, 0.95))
        files.append(OutputFile(edited_path, "source", "image/png"))
        ctx.meta["prompt"] = params.instruction
        ctx.meta.setdefault("extra", {}).update({"ai_edit": {"instruction": params.instruction, "edit_prompt": prompt,
                                                             "source_image": src.name}})
        return files


def _mesh_or_image(ctx: Context, ref: str) -> Path:
    from forge_core.runner import resolve_input

    av, path = resolve_input(ctx.project, ref)
    if av is not None:
        files = ctx.project.store.files_of(av, "primary") + ctx.project.store.files_of(av)
        images = [f for f in files if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
        if not images:
            raise ValueError(f"{ref} has no image")
        return images[0]
    return Path(path)


def _comfy_has(client, filename: str) -> bool:
    for folder in ("diffusion_models", "unet", "text_encoders", "clip"):
        if filename in client.models(folder):
            return True
    return False


EDIT_PIPELINES: list[Pipeline] = [Edit(), AIEdit()]
