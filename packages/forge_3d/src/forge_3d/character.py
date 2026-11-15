"""Characters ready to animate.

- 3d.character: concept (image or generated mesh) -> the same character redrawn in a T-pose on the animation
  library's mannequin, without weapons/props/capes (Qwen-Image-Edit) -> each removed item generated on its own
  as a prop asset -> body regenerated in 3D -> 3d.character_rig.
- 3d.character_rig: a standing character mesh -> cleaned mesh, humanoid skeleton (Unreal-mannequin/UAL or
  Mixamo names), skin weights, library animations retargeted onto it, deformation check and pose previews.
- 3d.retarget: more library animations on an existing humanoid skeleton asset.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh
from forge_core import Context, OutputFile, Pipeline, PipelineParams
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from . import animlib
from . import geometry as g
from .anim_pipelines import load_rig, write_rig
from .edit_pipelines import _load_y_up, find_source_image
from .geometry import skeleton as sk
from .geometry.anim import SKIN_KEY, Clip, Clips, Part, Rig, Skin
from .geometry.deform import deformation_report, posed_mesh
from .geometry.render import contact_sheet, render_view
from .geometry.retarget import fit_character, retarget
from .pipelines import derived_slug, input_file, jsonable, mark_base, settings, write_report

AnimSet = Literal["basic", "combat", "social", "all"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DEFAULT_HEIGHT = 1.8
FACING = {"+z": 0.0, "-z": 180.0, "+x": -90.0, "-x": 90.0}   # yaw that turns the model to face +Z


class AnimationChoice(BaseModel):
    model_config = ConfigDict(title="Animations")
    sets: list[AnimSet] = Field(default_factory=lambda: ["basic"], description=(
        "clip sets from the animation library: basic (idle, walk, jog, sprint, jump, crouch, roll, hit, death), "
        "combat (sword, punches, spell, pistol, shield), social (talk, dance, sit, yes/no), all"))
    names: list[str] = Field(default_factory=list, description='extra clips by name ("Walk_Loop" or "UAL2_Standard/Yes")')
    root_motion: bool = Field(False, description="clips move the character (root motion) instead of playing in place")


class RigOptions(BaseModel):
    model_config = ConfigDict(title="Rig")
    naming: Literal["unreal", "mixamo"] = Field("unreal", description=(
        "bone names: unreal = UE mannequin / Universal Animation Library (root, pelvis, spine_01, upperarm_l...), "
        "mixamo = Hips, Spine, LeftArm... (Mixamo, Unity humanoid)"))
    fingers: bool = Field(False, description="finger bones (3 per finger, shaped after the library's hand); leave off "
                          "for mitten-like hands, where separate finger bones would smear the mesh")
    mesh_fix: Literal["none", "clean", "rebuild"] = Field("clean", description=(
        "clean: remove loose fragments, duplicate and broken faces (keeps the texture); rebuild: one closed "
        "surface remeshed from a voxel volume (fixes self-intersections and gaps that tear when posed; colors "
        "become vertex colors); none: use the mesh as is"))
    binding: Literal["surface", "volume"] = Field("surface", description=(
        "how skin weights find their bone: surface = nearest bone in straight line, then blended over the surface "
        "(best for bodies); volume = distance through the model's volume (long hair, skirts or sleeves hanging "
        "next to other limbs)"))
    target_faces: int | None = Field(None, ge=0, description="triangle budget of the deforming mesh (default: project "
                                     "3d.poly_budget; textured meshes are not decimated)")
    height: float | None = Field(None, gt=0, description="scale the character to this height in project units "
                                 "(default: project 3d.default_height, else 1.8)")
    facing: Literal["auto", "+z", "-z", "+x", "-x"] = Field("auto", description=(
        "direction the model faces now (it is turned to face +Z). auto: squares up a character that is turned a "
        "little (generated from a three-quarter view) by lining its shoulders up with X"))
    check: bool = Field(True, description="play every clip and report where the mesh stretches or tears; pose previews")


# ---- the rig stage ---------------------------------------------------------------------------

def square_up(mesh: trimesh.Trimesh) -> float:
    """Yaw (degrees) that lines the shoulders/arms up with X, turning the model the least (generators face the
    camera, +Z, so a model is off by less than 90°). 0 when the upper body has no clear left-right axis."""
    v = np.asarray(mesh.vertices)
    lo, hi = mesh.bounds
    H = float(hi[1] - lo[1])
    band = v[(v[:, 1] > lo[1] + 0.55 * H) & (v[:, 1] < lo[1] + 0.85 * H)][:, [0, 2]]
    if len(band) < 50:
        return 0.0
    vals, vecs = np.linalg.eigh(np.cov((band - band.mean(axis=0)).T))
    if vals[1] < 1.3 * vals[0]:
        return 0.0
    ax = vecs[:, 1]
    a = float(np.degrees(np.arctan2(ax[1], ax[0])))
    a = (a + 90.0) % 180.0 - 90.0
    return a if abs(a) > 3.0 else 0.0


def _normalize(ctx: Context, mesh: trimesh.Trimesh, opts: RigOptions, height: float) -> trimesh.Trimesh:
    yaw = square_up(mesh) if opts.facing == "auto" else FACING[opts.facing]
    if opts.facing == "auto" and yaw:
        ctx.log(f"model was turned {yaw:.0f}°: squared up to face the front")
    if yaw:
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.radians(yaw), [0, 1, 0]))
    mesh.apply_scale(height / max(float(mesh.extents[1]), 1e-9))
    lo, hi = mesh.bounds
    mesh.apply_translation([-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2])
    return mesh


def _fix_mesh(ctx: Context, mesh: trimesh.Trimesh, opts: RigOptions) -> trimesh.Trimesh:
    from .gen_pipelines import _colors_onto

    from .geometry.texture import texture_image

    textured = texture_image(mesh) is not None
    budget = settings(ctx).poly_budget if opts.target_faces is None else opts.target_faces
    if opts.mesh_fix == "rebuild":
        source = mesh
        solid, how = g.solidify(mesh, voxel_resolution=256)
        ctx.log(f"mesh rebuilt as one closed surface ({how})")
        if budget and len(solid.faces) > budget:
            solid = g.simplify(solid, target_faces=budget, quality=0.6, planar=False)
        if _colors_onto(source, solid):
            ctx.log("colors carried over as vertex colors")
        return solid
    if opts.mesh_fix == "clean":
        if textured:
            mesh, dropped = g.drop_fragments(mesh, min_ratio=0.02)
            if dropped:
                ctx.log(f"removed {dropped} loose fragment(s)")
        else:
            mesh, actions = g.clean(mesh, min_component_ratio=0.02, max_hole_edges=200)
            for a in actions:
                ctx.log(a)
    if budget and len(mesh.faces) > budget:
        if textured:
            ctx.log(f"{len(mesh.faces)} faces kept: a textured mesh is not decimated (UV seams would open); "
                    "use mesh_fix=rebuild or 3d.simplify before texturing")
        else:
            mesh = g.simplify(mesh, target_faces=budget, quality=0.6, planar=False)
            mesh, _ = g.drop_fragments(mesh)
            ctx.log(f"simplified to {len(mesh.faces)} faces")
    welded = trimesh.Trimesh(np.asarray(mesh.vertices), np.asarray(mesh.faces), process=False)
    welded.merge_vertices(merge_tex=True, merge_norm=True)
    pieces = int(trimesh.graph.connected_components(welded.face_adjacency, nodes=np.arange(len(welded.faces)),
                                                    min_len=1).__len__())
    if pieces > 1:
        ctx.log(f"note: the mesh has {pieces} separate pieces; loose ones follow the nearest bone "
                "(mesh_fix=rebuild joins them into one surface)")
    return mesh


def _clips_for(choice: AnimationChoice, skeleton: sk.Skeleton, ctx: Context, span: tuple[float, float]) -> list[Clip]:
    names = animlib.expand(choice.sets, choice.names)
    if not names:
        return []
    refs = animlib.resolve(names, root_motion=choice.root_motion)
    clips: list[Clip] = []
    a, b = span
    for i, (lib, name) in enumerate(refs):
        ctx.check_cancelled()
        ctx.progress(a + (b - a) * i / len(refs), f"animation {name}")
        f = animlib.load(lib)
        clips.append(retarget(f.skeleton, f.clip(name), skeleton))
    ctx.log(f"{len(clips)} animation(s) from {', '.join(sorted({lib.id for lib, _ in refs}))}")
    return clips


def _pose_sheet(mesh: trimesh.Trimesh, rig: Rig, clips: list[Clip], joints, weights) -> Image.Image:
    tiles = [("rest", render_view(mesh, 25.0, 8.0, 320))]
    for clip in clips[:8]:
        t = clip.duration * (0.0 if len(clips) == 1 else 0.4)
        tiles.append((clip.name, render_view(posed_mesh(mesh, rig, clip, t, joints, weights), 25.0, 8.0, 320)))
    return contact_sheet(tiles, columns=3)


def rig_character(ctx: Context, mesh: trimesh.Trimesh, opts: RigOptions, anims: AnimationChoice,
                  span: tuple[float, float] = (0.0, 1.0), extra: dict | None = None) -> list[OutputFile]:
    a, b = span

    def at(f: float) -> float:
        return a + (b - a) * f

    cfg = settings(ctx)
    height = opts.height or cfg.default_height or DEFAULT_HEIGHT
    ctx.progress(at(0.02), "preparing the mesh")
    mesh = _fix_mesh(ctx, _normalize(ctx, mesh, opts, height), opts)
    wants_clips = bool(animlib.expand(anims.sets, anims.names)) if (anims.sets or anims.names) else False
    reference = None
    if opts.fingers or wants_clips:
        reference = animlib.reference()
    ctx.progress(at(0.1), "fitting the skeleton")
    skel = fit_character(mesh, reference.skeleton if reference else None, naming=opts.naming, fingers=opts.fingers)
    ctx.log(f"skeleton: {len(skel.joints)} joints ({skel.template})")
    ctx.progress(at(0.15), "computing skin weights")
    joints, weights = sk.skin_weights(mesh, skel, method="heat" if opts.binding == "surface" else "geodesic")
    bones = [Part(name=j.name, pivot=tuple(map(float, j.position)), parent=j.parent, source="bone", deform=j.deform)
             for j in skel.joints]
    rig = Rig(units=cfg.units, parts=bones, skin=Skin(template=skel.template))
    order = {p.name: i for i, p in enumerate(rig.bones)}
    remap = np.array([order[j.name] for j in skel.joints], dtype=np.uint16)
    skin = (remap[joints], weights)
    clips = _clips_for(anims, skel, ctx, (at(0.45), at(0.7))) if wants_clips else []
    files: list[OutputFile] = []
    info: dict = {"skeleton": skel.template, "joints": len(skel.joints), "height": round(height, 3),
                  "naming": opts.naming, "library": reference.path.name if reference else None, **(extra or {})}
    if opts.check and clips:
        ctx.progress(at(0.72), "checking deformation")
        report = deformation_report(mesh, rig, clips, skin[0], skin[1])
        info["deformation"] = {"grade": report["grade"], "worst_ratio": report["worst_ratio"],
                               "problems": [c for c in report["clips"] if c["grade"] != "ok"]}
        for c in report["clips"]:
            if c["grade"] != "ok":
                ctx.log(f"{c['name']}: {c['torn_pct']}% of edges torn (stretched > x2.5, max x{c['max_stretch']}) "
                        f"around {', '.join(c['bones']) or '?'}")
        ctx.log(f"deformation check: {report['grade']}")
        files.append(write_report(ctx, report, "deformation.json"))
        ctx.progress(at(0.85), "pose previews")
        sheet = _pose_sheet(mesh, rig, clips, skin[0], skin[1])
        path = ctx.version_dir / "poses.png"
        sheet.save(path)
        files.append(OutputFile(path, "preview", "image/png"))
    ctx.progress(at(0.92), "writing the animated model")
    files = write_rig(ctx, rig, Clips(clips=clips), {SKIN_KEY: mesh}, skin=skin) + files
    ctx.meta.setdefault("extra", {})["character"] = jsonable(info)
    return files


# ---- 3d.character_rig ------------------------------------------------------------------------

class CharacterRigParams(PipelineParams):
    animations: AnimationChoice = Field(default_factory=AnimationChoice)
    rig: RigOptions = Field(default_factory=RigOptions)


class CharacterRig(Pipeline):
    id = "3d.character_rig"
    title = "Character rig"
    description = ("Make a standing character mesh (T- or A-pose, facing front) animatable: cleans the mesh, fits a "
                   "humanoid skeleton (Unreal mannequin / UAL or Mixamo names), computes skin weights, applies "
                   "animations from the library (Universal Animation Library: idle, walk, run, jump, attacks...), "
                   "checks where it tears and renders pose previews. Creates an animation asset.")
    resources = {"cpu"}
    kind = "animation"
    Params = CharacterRigParams
    requires_input = True

    def default_slug(self, params: CharacterRigParams) -> str:
        return derived_slug(params, "rig")

    def run(self, ctx: Context, params: CharacterRigParams) -> list[OutputFile]:
        if any(f.name == "rig.json" for f in ctx.input_files()):
            rig, _, meshes = load_rig(ctx)
            if rig.skin is None:
                raise ValueError("this animation asset is made of rigid parts: rig the original mesh instead")
            mesh = meshes[SKIN_KEY]
            ctx.log("re-rigging the skinned mesh of the input animation asset")
        else:
            mesh = _load_y_up(input_file(ctx))
        mark_base(ctx)
        return rig_character(ctx, mesh, params.rig, params.animations)


# ---- 3d.retarget -----------------------------------------------------------------------------

class RetargetParams(PipelineParams):
    animations: AnimationChoice = Field(default_factory=lambda: AnimationChoice(sets=[]))
    replace: bool = Field(False, description="drop the asset's current clips first")


class RetargetPipeline(Pipeline):
    id = "3d.retarget"
    title = "Add library animations"
    description = ("Apply animations from the animation library (Universal Animation Library...) to a humanoid "
                   "skeleton asset (from 3d.character_rig or 3d.skeleton): clips are retargeted to its proportions.")
    resources = {"cpu"}
    kind = "animation"
    Params = RetargetParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: RetargetParams) -> list[OutputFile]:
        rig, clips, meshes = load_rig(ctx)
        if rig.skin is None:
            raise ValueError("input has no skeleton: create one with 3d.character_rig or 3d.skeleton")
        skel = sk.Skeleton([sk.Joint(b.name, b.parent, np.asarray(b.pivot, dtype=np.float64), b.deform) for b in rig.bones],
                           template=rig.skin.template)
        new = _clips_for(params.animations, skel, ctx, (0.05, 0.85))
        if not new:
            raise ValueError("choose animations: sets and/or names")
        if params.replace:
            clips = Clips()
        for c in new:
            clips.upsert(c)
        return write_rig(ctx, rig, clips, meshes)


# ---- 3d.character ----------------------------------------------------------------------------

DEFAULT_REMOVE = "weapons, shields, staffs and anything held in the hands, capes and cloaks, and objects floating around the character"
# naming garments ("wide sleeves") makes the image editor add them: the wording only talks about the legs
LOOSE = {
    "keep": "",
    "fit": (" Each leg is clearly separate from the waist down: the outfit opens at the front below the belt, showing "
            "the trousers and boots, while keeping its colors, trims and decorations."),
    "remove": " Instead of any long robe, coat or skirt, the character wears the fitted clothes that would be under it.",
}


class CharacterParams(PipelineParams):
    remove: str = Field(DEFAULT_REMOVE, description="what to take off the character before rigging (things that "
                        "would get in the way of animation); everything else is kept")
    loose_clothing: Literal["keep", "fit", "remove"] = Field("fit", description=(
        "long robes, coats and skirts: fit = opened below the belt so each leg is free (animates cleanly), "
        "keep = as designed (walking stretches them between the legs), remove = show what is worn under them"))
    extract: list[str] = Field(default_factory=list, description=(
        'items to generate on their own as separate 3D prop assets, e.g. ["sword", "round shield"]'))
    notes: str | None = Field(None, description="extra instructions for the T-pose redraw (e.g. 'keep the helmet')")
    mannequin: bool = Field(True, description="show the library mannequin to the image editor as the pose to copy "
                            "(proportions the skeleton expects)")
    generator: Literal["trellis2", "hunyuan3d-2.1"] | None = Field(None, description="3D method (default: automatic)")
    texture: bool = Field(True, description="TRELLIS.2: texture the body (and props) in the same run")
    seed: int | None = None
    image_steps: int = Field(4, ge=1, le=50, description="Qwen-Image-Edit steps (4 with the Lightning LoRA)")
    animations: AnimationChoice = Field(default_factory=AnimationChoice)
    rig: RigOptions = Field(default_factory=RigOptions)


STRIP_PROMPT = (
    "Remove {remove} from the picture. Keep the character, its pose, face, hair, headwear, clothing, colors and art "
    "style exactly the same, and fill in the parts of the body and clothes they were hiding. Plain white background.")
TPOSE_PROMPT = (
    "Redraw the character from image 1 standing in a strict T-pose{mannequin}: arms straight out to the sides at "
    "shoulder height, elbows straight, hands open with fingers together and palms facing down, legs straight and "
    "slightly apart, feet flat on the ground, facing the camera. Full body from the top of the head to the feet, "
    "front view, centered. It is the same character: keep the face, hair, headwear, body shape, clothing, armor, "
    "colors, patterns and art style of image 1 exactly; only the pose changes. The hands are empty and nothing "
    "touches or covers the arms.{loose}{notes} Plain white background, soft even lighting, no shadows.")
PROP_PROMPT = (
    "Show only the {item} from image 1, by itself: whole, undamaged and not held by anyone, the character removed. "
    "Seen from the front, centered, filling most of the picture. Keep its exact design, colors, materials and art "
    "style. Plain white background, soft even lighting, no shadows.")


def _free_vram(url: str) -> None:
    import httpx

    try:
        httpx.post(url.rstrip("/") + "/free", json={"unload_models": True, "free_memory": True}, timeout=10)
    except Exception:
        pass


def _source_image(ctx: Context) -> tuple[Image.Image, str]:
    files = ctx.input_files("primary") + ctx.input_files()
    img = next((f for f in files if f.suffix.lower() in IMAGE_EXTENSIONS), None)
    if img is not None and not any(f.suffix.lower() in g.MESH_EXTENSIONS for f in files):
        return Image.open(img).convert("RGB"), img.name
    if any(f.name == "rig.json" for f in files):
        raise ValueError("the input is already an animation asset: use 3d.character_rig to re-rig it")
    src = find_source_image(ctx)
    if src is not None:
        return Image.open(src).convert("RGB"), src.name
    mesh = _load_y_up(input_file(ctx))
    return render_view(mesh, 0.0, 0.0, 1024, background=(255, 255, 255)), "front render of the mesh"


def _mannequin_image() -> Image.Image | None:
    try:
        ref = animlib.reference()
        mesh = trimesh.load(ref.path, force="mesh")
        return render_view(mesh, 0.0, 0.0, 1024, background=(255, 255, 255))
    except Exception:
        return None


class CharacterPipeline(Pipeline):
    id = "3d.character"
    title = "Character → animatable"
    description = ("Turn a character concept (image or 3D model) into an animatable asset: redraws it in a T-pose on the "
                   "animation library's mannequin without weapons, capes and props (Qwen-Image-Edit), generates the "
                   "removed items as separate prop assets, rebuilds the body in 3D (TRELLIS.2 / Hunyuan3D), then rigs it "
                   "(skeleton, skin weights, library animations, deformation check). Keeps the T-pose mesh as <slug>-base.")
    resources = {"gpu"}
    kind = "animation"
    Params = CharacterParams
    requires_input = True

    def default_slug(self, params: CharacterParams) -> str:
        return derived_slug(params, "char")

    def run(self, ctx: Context, params: CharacterParams) -> list[OutputFile]:
        from forge_2d.graphs import DEFAULT_MODELS as MODELS_2D
        from forge_2d.graphs import qwen_edit
        from forge_core.comfy_select import use_instance
        from forge_core.runner import run_pipeline

        from .gen_pipelines import _seed

        # fail early, before minutes of GPU work, when there is no library to animate with
        if params.animations.sets or params.animations.names or params.rig.fingers or params.mannequin:
            animlib.reference()
        seed = _seed(params.seed)
        image, origin = _source_image(ctx)
        ctx.log(f"concept: {origin}")
        src_path = ctx.version_dir / "concept.png"
        image.save(src_path)
        files: list[OutputFile] = [OutputFile(src_path, "source", "image/png")]

        models_2d = {**MODELS_2D, **(ctx.style("2d").get("models") or {})}
        use_instance(ctx, files={"diffusion_models": models_2d["qwen_edit_unet"]}, what="Qwen-Image-Edit")
        client = ctx.comfy
        concept_upload = client.upload_image(src_path)
        uploads = [concept_upload]
        mannequin = _mannequin_image() if params.mannequin else None
        if mannequin is not None:
            mpath = ctx.version_dir / "mannequin.png"
            mannequin.save(mpath)
            uploads.append(client.upload_image(mpath))
        notes = f" {params.notes.strip().rstrip('.')}." if params.notes else ""
        prompt = TPOSE_PROMPT.format(mannequin=" exactly like the mannequin in image 2 (copy only its pose)" if mannequin is not None else "",
                                     loose=LOOSE[params.loose_clothing], notes=notes)
        remove = params.remove.strip().rstrip(".")

        def edit(prompt_text: str, names: list[str], label: str, span: tuple[float, float]) -> Image.Image:
            ctx.check_cancelled()
            graph, img_id, _ = qwen_edit(prompt_text, names, seed, params.image_steps, models=models_2d)
            a, b = span
            outs = client.run_workflow(graph.to_dict(), output_nodes=[img_id],
                                       on_progress=lambda v, m: ctx.progress(a + (b - a) * v, label))
            out = next(o for o in outs if o.node_id == img_id)
            return Image.open(io.BytesIO(client.download(out.filename, out.subfolder, out.type))).convert("RGB")

        # two edits: taking things off works best as an edit of its own (asked together with the new pose, big
        # items such as capes survive), then the pose
        if remove:
            ctx.progress(0.02, "removing items (Qwen-Image-Edit)")
            stripped = edit(STRIP_PROMPT.format(remove=remove), uploads[:1], "removing items", (0.02, 0.07))
            stripped_path = ctx.version_dir / "stripped.png"
            stripped.save(stripped_path)
            files.append(OutputFile(stripped_path, "source", "image/png"))
            uploads = [client.upload_image(stripped_path)] + uploads[1:]
        ctx.progress(0.07, "T-pose (Qwen-Image-Edit)")
        tpose = edit(prompt, uploads, "T-pose", (0.07, 0.12))
        tpose_path = ctx.version_dir / "tpose.png"
        tpose.save(tpose_path)
        files.append(OutputFile(tpose_path, "source", "image/png"))
        prop_images: list[tuple[str, Path]] = []
        for i, item in enumerate(params.extract):
            ctx.progress(0.12 + 0.06 * i / max(len(params.extract), 1), f"isolating {item}")
            img = edit(PROP_PROMPT.format(item=item.strip()), [concept_upload], f"isolating {item}", (0.12, 0.18))
            slug = _slugify(item)
            path = ctx.version_dir / f"prop_{slug}.png"
            img.save(path)
            files.append(OutputFile(path, "source", "image/png"))
            prop_images.append((item, path))
        _free_vram(client.url)   # the 3D generators need the VRAM

        store = ctx.project.store
        folder = category = None
        if ctx.input is not None:
            meta = store.get_meta(ctx.input.slug)
            folder, category = meta.folder, meta.category
        gen = {"seed": seed, "trellis": {"texture": params.texture}}
        if params.generator:
            gen["generator"] = params.generator

        def nested(pipeline: str, p: dict, asset: str, label: str, span: tuple[float, float]):
            a, b = span
            return run_pipeline(ctx.project, pipeline, p, asset=asset, folder=folder, category=category,
                                on_log=lambda m: ctx.log(f"[{label}] {m}"),
                                on_progress=lambda v, m: ctx.progress(a + (b - a) * v, f"{label}: {m or ''}".strip()),
                                cancel=ctx._cancel)

        base = nested("3d.image_to_3d", {"input_asset": str(tpose_path), **gen}, f"{ctx.slug}-base", "body", (0.2, 0.6))
        ctx.log(f"T-pose body: {base.ref}")
        props = []
        for i, (item, path) in enumerate(prop_images):
            span = (0.6 + 0.12 * i / len(prop_images), 0.6 + 0.12 * (i + 1) / len(prop_images))
            av = nested("3d.image_to_3d", {"input_asset": str(path), **gen}, f"{ctx.slug}-{_slugify(item)}", item, span)
            props.append({"item": item, "asset": av.ref})
            ctx.log(f"prop {item}: {av.ref}")

        model = next(f for f in store.files_of(base, "primary") if f.suffix.lower() in g.MESH_EXTENSIONS)
        mesh = _load_y_up(model)
        ctx.meta["seed"] = seed
        mark_base(ctx)
        files = rig_character(ctx, mesh, params.rig, params.animations, span=(0.74, 0.99),
                              extra={"base": base.ref, "props": props, "removed": remove,
                                     "tpose_prompt": prompt}) + files
        return files


def _slugify(text: str) -> str:
    from forge_core.project import slugify

    return slugify(text)[:40] or "item"


CHARACTER_PIPELINES: list[Pipeline] = [CharacterPipeline(), CharacterRig(), RetargetPipeline()]
