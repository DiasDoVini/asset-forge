"""Animation pipelines: 3d.rig (split into animatable parts) and 3d.animate (add/replace clips)."""

from __future__ import annotations

import re
from typing import Literal

import numpy as np
import trimesh
from forge_core import Context, OutputFile, Pipeline, PipelineParams
from pydantic import BaseModel, Field

from . import geometry as g
from .edit_pipelines import _load_y_up, _mesh_file_of
from .geometry import skeleton as sk
from .geometry.anim import (SKIN_KEY, Clip, Clips, Part, Rig, Skin, Track, export_glb, preset_keys, split_by_faces,
                            validate_clip)
from .geometry.bridge import from_manifold, to_manifold
from .geometry.edit import IDENTITY, Matrix, primitive
from .pipelines import derived_slug, input_file, jsonable, mark_base, settings

MENTION = re.compile(r"@([a-z0-9][a-z0-9-]*(?:@v\d+)?)")


# ---- rig files ---------------------------------------------------------------------------

def load_rig(ctx: Context) -> tuple[Rig, Clips, dict[str, trimesh.Trimesh]]:
    store = ctx.project.store
    files = {f.name: f for f in ctx.input_files()}
    if "rig.json" not in files:
        raise ValueError("input is not an animation asset (no rig.json); create one with 3d.rig")
    rig = Rig.model_validate_json(files["rig.json"].read_text(encoding="utf-8"))
    clips = Clips.model_validate_json(files["clips.json"].read_text(encoding="utf-8")) if "clips.json" in files else Clips()
    vdir = store.version_dir(ctx.input.slug, ctx.input.version)
    meshes = {p.name: g.load_mesh(vdir / p.file) for p in rig.rigid}
    if rig.skin is not None:
        meshes[SKIN_KEY] = g.load_mesh(vdir / rig.skin.mesh)
    return rig, clips, meshes


def _input_skin(ctx: Context, rig: Rig, mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """The input version's skin weights, carried to ``mesh``'s vertices (they change when the mesh is textured
    or edited); joint indices into ``rig.bones``."""
    vdir = ctx.project.store.version_dir(ctx.input.slug, ctx.input.version)
    data = np.load(vdir / rig.skin.weights)
    names = [str(n) for n in data["joint_names"]]
    order = {b.name: i for i, b in enumerate(rig.bones)}
    remap = np.array([order.get(n, 0) for n in names], dtype=np.uint16)
    joints, weights = data["joints"], data["weights"]
    if len(data["positions"]) != len(mesh.vertices) or not np.allclose(data["positions"], mesh.vertices, atol=1e-6):
        joints, weights = sk.remap_weights(data["positions"], joints, weights, mesh)
    return remap[joints], weights


def write_rig(ctx: Context, rig: Rig, clips: Clips, meshes: dict[str, trimesh.Trimesh],
              skin: tuple[np.ndarray, np.ndarray] | None = None) -> list[OutputFile]:
    """Write an animation asset. Skeleton rigs: ``skin`` = (joints into rig.bones, weights) per vertex of
    ``meshes[SKIN_KEY]``; when omitted they are carried over from the input version."""
    out: list[OutputFile] = []
    parts_dir = ctx.version_dir / "parts"
    parts_dir.mkdir(exist_ok=True)
    for p in rig.rigid:
        p.file = f"parts/{p.name}.glb"
        out.append(OutputFile(g.save_mesh(meshes[p.name], ctx.version_dir / p.file), "part", "model/gltf-binary"))
    if rig.skin is not None:
        mesh = meshes[SKIN_KEY]
        if skin is None:
            skin = _input_skin(ctx, rig, mesh)
        (ctx.version_dir / "skin").mkdir(exist_ok=True)
        out.append(OutputFile(g.save_mesh(mesh, ctx.version_dir / rig.skin.mesh), "part", "model/gltf-binary"))
        np.savez_compressed(ctx.version_dir / rig.skin.weights, joints=skin[0], weights=skin[1],
                            positions=np.asarray(mesh.vertices), joint_names=np.array([b.name for b in rig.bones]))
        out.append(OutputFile(ctx.version_dir / rig.skin.weights, "source", "application/octet-stream"))
    (ctx.version_dir / "rig.json").write_text(rig.model_dump_json(indent=2), encoding="utf-8")
    (ctx.version_dir / "clips.json").write_text(clips.model_dump_json(indent=2), encoding="utf-8")
    out.append(OutputFile(ctx.version_dir / "rig.json", "source", "application/json"))
    out.append(OutputFile(ctx.version_dir / "clips.json", "source", "application/json"))
    glb = export_glb(rig, meshes, clips.clips, ctx.version_dir / "animated.glb", skin=skin)
    out.insert(0, OutputFile(glb, "primary", "model/gltf-binary"))
    ctx.meta["kind"] = "animation"
    ctx.meta.setdefault("extra", {}).update(jsonable({
        "animation": {
            "type": "skeleton" if rig.skin is not None else "parts",
            "template": rig.skin.template if rig.skin is not None else None,
            "parts": [{"name": p.name, "parent": p.parent, "pivot": list(p.pivot), "source": p.source,
                       "bone": p.is_bone, "deform": p.deform,
                       "faces": 0 if p.is_bone else int(len(meshes[p.name].faces))} for p in rig.parts],
            "skin_faces": int(len(meshes[SKIN_KEY].faces)) if rig.skin is not None else None,
            "clips": [{"name": c.name, "duration": c.duration, "loop": c.loop} for c in clips.clips],
            "units": rig.units,
        }}))
    return out


# ---- 3d.rig ------------------------------------------------------------------------------

class Region(BaseModel):
    shape: Literal["box", "cylinder", "sphere", "cone"] = "box"
    size: tuple[float, float, float] = (1.0, 1.0, 1.0)
    matrix: Matrix = Field(default_factory=lambda: IDENTITY, description="placement of the region (Y-up viewer coordinates)")


class PartSpec(BaseModel):
    name: str = Field(..., pattern=r"^[A-Za-z0-9_\-]+$")
    region: Region = Field(..., description="the part is everything of the remaining mesh inside this region")
    pivot: tuple[float, float, float] | None = Field(None, description="rotation pivot (default: center of the region)")
    parent: str | None = Field(None, description="parent part (default: the body / from_part)")
    from_part: str | None = Field(None, description="when re-rigging an animation asset: the existing part to split this one off (default: the root part)")


class PropSpec(BaseModel):
    asset: str = Field(..., description="another mesh asset to include (e.g. a weapon to be picked up)")
    name: str | None = None
    matrix: Matrix = Field(default_factory=lambda: IDENTITY, description="placement (default: its own position)")


class RigParams(PipelineParams):
    parts: list[PartSpec] = Field(default_factory=list, description="parts carved out of the mesh, in order")
    body_name: str = Field("body", description="name of what remains after carving the parts")
    components: bool = Field(False, description="also turn every disconnected piece of the remainder into its own part")
    props: list[PropSpec] = Field(default_factory=list, description="other assets added as free parts (props)")
    mode: Literal["auto", "solid", "faces"] = Field(
        "auto", description="solid: boolean cuts with closed faces (needs a watertight mesh, loses UVs); "
                            "faces: split by faces (keeps textures, leaves openings); auto: solid when possible")


class RigPipeline(Pipeline):
    id = "3d.rig"
    title = "Rig (animation parts)"
    description = ("Split a mesh into animatable parts with pivots and hierarchy (e.g. a rifle's magazine and bolt, "
                   "a chest's lid) and optionally add other assets as props. Creates an animation asset.")
    resources = {"cpu"}
    kind = "animation"
    Params = RigParams
    requires_input = True

    def default_slug(self, params: RigParams) -> str:
        return derived_slug(params, "anim")

    def run(self, ctx: Context, params: RigParams) -> list[OutputFile]:
        if any(f.name == "rig.json" for f in ctx.input_files()):
            return self._extend(ctx, params)
        mesh = _load_y_up(input_file(ctx))
        solid = params.mode == "solid" or (params.mode == "auto" and _is_solid(mesh))
        ctx.log(f"carving {len(params.parts)} part(s) ({'solid booleans' if solid else 'face split'})")
        meshes: dict[str, trimesh.Trimesh] = {}
        parts: list[Part] = []
        rest = mesh
        for spec in params.parts:
            piece, rest = _carve(rest, spec, solid)
            meshes[spec.name] = piece
            parts.append(Part(name=spec.name, file="", pivot=_pivot(spec), parent=spec.parent or params.body_name))
        body_parts = [rest]
        if params.components and len(rest.faces):
            body_parts = sorted(rest.split(only_watertight=False), key=lambda m: -len(m.faces))
        body = body_parts[0] if len(body_parts[0].faces) else None
        if body is None:
            raise ValueError("nothing left for the body: regions cover the whole mesh")
        meshes[params.body_name] = body
        lo, hi = body.bounds
        parts.insert(0, Part(name=params.body_name, file="", pivot=tuple(map(float, [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])), source="body"))
        for i, extra in enumerate(body_parts[1:], 1):
            name = f"piece_{i:02d}"
            meshes[name] = extra
            parts.append(Part(name=name, file="", pivot=tuple(map(float, extra.centroid)), parent=params.body_name, source="components"))
        for prop in params.props:
            parts.append(_add_prop(ctx, prop, meshes, body))
        rig = Rig(units=settings(ctx).units, parts=parts)
        mark_base(ctx)
        return write_rig(ctx, rig, Clips(), meshes)

    def _extend(self, ctx: Context, params: RigParams) -> list[OutputFile]:
        """Input is already an animation asset: carve new parts out of existing ones, keep the clips.
        (Pass the same asset slug as the job's `asset` to save it as a new version of it.)"""
        rig, clips, meshes = load_rig(ctx)
        names = {p.name for p in rig.parts}
        for spec in params.parts:
            if spec.name in names:
                raise ValueError(f"part '{spec.name}' already exists")
            source = spec.from_part or rig.parts[0].name
            if source not in meshes:
                raise ValueError(f"from_part '{source}' is not a part of this rig")
            solid = params.mode == "solid" or (params.mode == "auto" and _is_solid(meshes[source]))
            piece, meshes[source] = _carve(meshes[source], spec, solid)
            meshes[spec.name] = piece
            rig.parts.append(Part(name=spec.name, file="", pivot=_pivot(spec), parent=spec.parent or source))
            names.add(spec.name)
            ctx.log(f"split '{spec.name}' off '{source}'")
        body = meshes[rig.parts[0].name]
        for prop in params.props:
            rig.parts.append(_add_prop(ctx, prop, meshes, body))
        rig = Rig.model_validate(rig.model_dump())
        return write_rig(ctx, rig, clips, meshes)


def _pivot(spec: "PartSpec") -> tuple[float, float, float]:
    pivot = spec.pivot or tuple(np.asarray(spec.region.matrix, dtype=np.float64)[:3, 3])
    return tuple(map(float, pivot))


def _carve(rest: trimesh.Trimesh, spec: "PartSpec", solid: bool) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    if solid:
        tool = primitive(spec.region.shape, spec.region.size).transform(np.asarray(spec.region.matrix, dtype=np.float64)[:3, :])
        base = to_manifold(rest)
        inside, outside = base ^ tool, base - tool
        if inside.is_empty():
            raise ValueError(f"part '{spec.name}': its region does not touch the mesh")
        if outside.is_empty():
            raise ValueError(f"part '{spec.name}': its region swallows everything that was left")
        return from_manifold(inside), from_manifold(outside)
    piece, rest = split_by_faces(rest, _inside_fn(spec.region)(rest.triangles_center))
    if len(piece.faces) == 0:
        raise ValueError(f"part '{spec.name}': no faces inside its region")
    return piece, rest

def _is_solid(mesh: trimesh.Trimesh) -> bool:
    try:
        to_manifold(mesh)
        return True
    except ValueError:
        return False


def _inside_fn(region: Region):
    inv = np.linalg.inv(np.asarray(region.matrix, dtype=np.float64))
    half = np.asarray(region.size, dtype=np.float64) / 2

    def inside(points: np.ndarray) -> np.ndarray:
        p = (inv @ np.column_stack([points, np.ones(len(points))]).T).T[:, :3] / np.where(half == 0, 1, half)
        if region.shape == "box":
            return np.all(np.abs(p) <= 1, axis=1)
        if region.shape == "sphere":
            return (p ** 2).sum(axis=1) <= 1
        radial = p[:, 0] ** 2 + p[:, 2] ** 2  # cylinder / cone axis is Y
        if region.shape == "cone":
            return (np.abs(p[:, 1]) <= 1) & (radial <= ((1 - p[:, 1]) / 2) ** 2)
        return (np.abs(p[:, 1]) <= 1) & (radial <= 1)

    return inside


def _add_prop(ctx: Context, prop: PropSpec, meshes: dict, body: trimesh.Trimesh) -> Part:
    path = _mesh_file_of(ctx, prop.asset)
    mesh = _load_y_up(path)
    name = prop.name or re.sub(r"[^A-Za-z0-9_\-]", "_", prop.asset.split("@")[0])
    if np.allclose(np.asarray(prop.matrix), np.eye(4)):
        # default placement: on the floor, beside the body, scaled to a plausible size relative to it
        k = min(1.0, 0.6 * float(body.extents.max()) / max(float(mesh.extents.max()), 1e-9))
        mesh.apply_scale(k)
        lo, hi = mesh.bounds
        blo, bhi = body.bounds
        mesh.apply_translation([bhi[0] + (hi[0] - lo[0]) * 0.6 - lo[0], blo[1] - lo[1], (blo[2] + bhi[2]) / 2 - (lo[2] + hi[2]) / 2])
    else:
        mesh.apply_transform(np.asarray(prop.matrix, dtype=np.float64))
    meshes[name] = mesh
    return Part(name=name, file="", pivot=tuple(map(float, mesh.bounds.mean(axis=0))), parent=None, source=f"asset:{prop.asset}")


# ---- 3d.animate --------------------------------------------------------------------------

class PresetSpec(BaseModel):
    part: str
    preset: Literal["spin", "hinge", "slide", "bob", "recoil", "shake", "pulse"]
    axis: Literal["x", "y", "z"] = "y"
    amount: float = Field(90.0, description="degrees (spin: turns if <= 4), pulse: percent")
    distance: float = Field(0.1, description="units for slide/bob/recoil/shake")


class AnimateParams(PipelineParams):
    name: str = Field("clip", description="clip name (replaces a clip with the same name)")
    duration: float = Field(2.0, gt=0)
    loop: bool = False
    clip: Clip | None = Field(None, description="explicit clip: tracks of keyframes and attachments (see 3d docs)")
    presets: list[PresetSpec] = Field(default_factory=list, description="procedural motions, one per part")
    prompt: str | None = Field(None, description='describe the motion; mention other assets with @slug. Needs Claude API credentials')
    delete: bool = Field(False, description="remove the clip called `name` instead of creating it")


class AnimatePipeline(Pipeline):
    id = "3d.animate"
    title = "Animate"
    description = ("Add or replace an animation clip on an animation asset: explicit keyframes, procedural presets, "
                   "or a natural-language prompt planned by Claude (mention props with @asset).")
    resources = {"cpu"}
    kind = "animation"
    Params = AnimateParams
    requires_input = True
    same_asset = True

    def run(self, ctx: Context, params: AnimateParams) -> list[OutputFile]:
        rig, clips, meshes = load_rig(ctx)
        if params.delete:
            clips.clips = [c for c in clips.clips if c.name != params.name]
            ctx.log(f"deleted clip {params.name}")
            return write_rig(ctx, rig, clips, meshes)

        if params.clip is not None:
            clip = params.clip
        elif params.presets:
            clip = Clip(name=params.name, duration=params.duration, loop=params.loop, tracks=[
                Track(part=p.part, keys=preset_keys(p.preset, params.duration, p.axis, p.amount, p.distance))
                for p in params.presets])
        elif params.prompt:
            # @mentions that are not parts yet become props
            for ref in MENTION.findall(params.prompt):
                slug = ref.split("@")[0]
                if not any(p.source.startswith(f"asset:{slug}") or p.name == slug for p in rig.parts):
                    ctx.log(f"adding @{ref} as a prop")
                    body = meshes[rig.parts[0].name]
                    rig.parts.append(_add_prop(ctx, PropSpec(asset=ref, name=slug), meshes, body))
            from .planner import plan_clip

            ctx.progress(0.1, "planning the motion with Claude")
            clip, notes = plan_clip(rig, meshes, params.prompt, name=params.name, duration=params.duration, loop=params.loop)
            for n in notes:
                ctx.log(n)
        else:
            raise ValueError("pass `clip`, `presets` or `prompt`")

        errors = validate_clip(rig, clip)
        if errors:
            raise ValueError("invalid clip: " + "; ".join(errors))
        clips.upsert(clip)
        ctx.log(f"clip '{clip.name}': {clip.duration}s, {len(clip.tracks)} track(s), {len(clip.attach)} attachment(s)")
        if params.prompt:
            ctx.meta["prompt"] = params.prompt
        return write_rig(ctx, rig, clips, meshes)



# ---- 3d.skeleton -----------------------------------------------------------------------------

class JointSpec(BaseModel):
    name: str = Field(..., pattern=r"^[A-Za-z0-9_\-:.]+$")
    parent: str | None = None
    position: tuple[float, float, float]
    deform: bool | None = Field(None, description="default: false for *_End markers, true otherwise")


class SkeletonParams(PipelineParams):
    template: Literal["auto", "humanoid", "creature"] = Field(
        "auto", description="humanoid: Mixamo-compatible biped; creature: spine + head + tail + one leg per foot "
                            "found (quadrupeds, spiders...); auto picks by the model's proportions")
    joints: list[JointSpec] | None = Field(None, description=(
        "explicit joints (from the skeleton editor), parents first; overrides the template fit"))
    flip: bool = Field(False, description="creature: the model faces the other way along its long axis")
    keep_clips: bool = Field(True, description="re-skinning a skeleton asset: keep its clips")


class SkeletonPipeline(Pipeline):
    id = "3d.skeleton"
    title = "Skeleton (skinned)"
    description = ("Give a mesh a skeleton that bends it smoothly (skinning): auto-fits a Mixamo-compatible humanoid or "
                   "a creature skeleton (legs, tail), or takes edited joints, computes skin weights and creates an "
                   "animation asset. Animate it like any rig; exports as a skinned glTF for game engines.")
    resources = {"cpu"}
    kind = "animation"
    Params = SkeletonParams
    requires_input = True

    def default_slug(self, params: SkeletonParams) -> str:
        return derived_slug(params, "anim")

    def run(self, ctx: Context, params: SkeletonParams) -> list[OutputFile]:
        clips = Clips()
        props: dict[str, trimesh.Trimesh] = {}
        rigid: list[Part] = []
        if any(f.name == "rig.json" for f in ctx.input_files()):
            old, old_clips, meshes = load_rig(ctx)
            if old.skin is None:
                raise ValueError("this animation asset is made of rigid parts; give the original mesh a skeleton instead")
            mesh = meshes.pop(SKIN_KEY)
            props = meshes
            rigid = old.rigid
            if params.keep_clips:
                clips = old_clips
        else:
            mesh = _load_y_up(input_file(ctx))
        if params.joints:
            skel = sk.Skeleton([sk.Joint(j.name, j.parent, np.asarray(j.position, dtype=np.float64),
                                         (not sk.is_end(j.name)) if j.deform is None else j.deform) for j in params.joints],
                               template="custom")
        else:
            template = params.template
            if template == "auto":
                ext = mesh.extents
                template = "humanoid" if ext[1] > 1.15 * max(ext[0], ext[2]) else "creature"
            skel = sk.fit_humanoid(mesh) if template == "humanoid" else sk.fit_creature(mesh, flip=params.flip)
            ctx.log(f"{template} skeleton fitted: {len(skel.joints)} joints")
        names = [j.name for j in skel.joints]
        if len(set(names)) != len(names):
            raise ValueError("joint names must be unique")
        ctx.progress(0.3, "computing skin weights")
        joints, weights = sk.skin_weights(mesh, skel)
        bones = [Part(name=j.name, pivot=tuple(map(float, j.position)), parent=j.parent, source="bone", deform=j.deform)
                 for j in skel.joints]
        rig = Rig(units=settings(ctx).units, parts=bones + rigid,
                  skin=Skin(template=skel.template if not params.joints else "custom"))
        order = {b.name: i for i, b in enumerate(rig.bones)}
        remap = np.array([order[j.name] for j in skel.joints], dtype=np.uint16)
        kept = [c for c in clips.clips if all(t.part in order or t.part in props for t in c.tracks)]
        if len(kept) < len(clips.clips):
            ctx.log(f"dropped {len(clips.clips) - len(kept)} clip(s) animating joints that no longer exist")
        clips.clips = kept
        if ctx.input is not None and not any(f.name == "rig.json" for f in ctx.input_files()):
            mark_base(ctx)
        return write_rig(ctx, rig, clips, {**props, SKIN_KEY: mesh}, skin=(remap[joints], weights))


ANIM_PIPELINES: list[Pipeline] = [RigPipeline(), SkeletonPipeline(), AnimatePipeline()]
