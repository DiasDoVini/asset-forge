"""Rigid-part animation: rigs (parts + pivots + hierarchy), clips (keyframes + attachments), glTF export.

Conventions (all Y-up, the viewer's space):
- A part's mesh is stored in world coordinates; its ``pivot`` is the point it rotates/scales around.
- In the glTF, each part is a node whose rest translation is ``pivot - parent.pivot`` and whose mesh is
  shifted by ``-pivot``. Animating the node therefore rotates the part around its pivot, and children
  follow their parent (hierarchy).
- Keyframes are *offsets from the rest pose*, in the parent's frame: ``position`` is added to the rest
  translation, ``rotation_deg`` is an XYZ Euler rotation, ``scale`` multiplies.
- Attachments (``attach``) parent a root part (e.g. a prop) to another part for a time range: the prop
  keeps its relative pose to that part, as if held. They are baked into sampled keyframes on export.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh
from pydantic import BaseModel, Field, model_validator
from scipy.spatial.transform import Rotation, Slerp

from .io import _with_default_material

Vec3 = tuple[float, float, float]


# ---- data model -------------------------------------------------------------------------

class Part(BaseModel):
    """A rigid part (own mesh) or, in skeleton rigs, a bone joint (no mesh: it deforms the skinned mesh)."""
    name: str
    file: str = Field("", description="mesh file of this part, relative to the asset version folder ('' for bones)")
    pivot: Vec3 = (0.0, 0.0, 0.0)
    parent: str | None = None
    source: str = Field("segment", description="segment | components | body | asset:<ref> | bone")
    deform: bool = Field(True, description="bones: whether it deforms the mesh (end markers do not)")

    @property
    def is_bone(self) -> bool:
        return self.source == "bone"


class Skin(BaseModel):
    mesh: str = Field("skin/mesh.glb", description="the deforming mesh, in bind (rest) pose")
    weights: str = Field("skin/weights.npz", description="per-vertex joints/weights + the vertex positions they belong to")
    template: str = "custom"


class Rig(BaseModel):
    units: str = "m"
    parts: list[Part]
    skin: Skin | None = Field(None, description="skeleton rigs: the skinned mesh driven by the bone parts")

    @property
    def bones(self) -> list[Part]:
        return [p for p in self.ordered() if p.is_bone]

    @property
    def rigid(self) -> list[Part]:
        return [p for p in self.ordered() if not p.is_bone]

    @model_validator(mode="after")
    def _check(self):
        names = [p.name for p in self.parts]
        if len(set(names)) != len(names):
            raise ValueError("part names must be unique")
        for p in self.parts:
            if p.parent is not None and p.parent not in names:
                raise ValueError(f"part {p.name}: unknown parent {p.parent}")
        # no cycles
        by = {p.name: p for p in self.parts}
        for p in self.parts:
            seen, cur = set(), p
            while cur.parent is not None:
                if cur.name in seen:
                    raise ValueError(f"parent cycle at {p.name}")
                seen.add(cur.name)
                cur = by[cur.parent]
        return self

    def part(self, name: str) -> Part:
        for p in self.parts:
            if p.name == name:
                return p
        raise KeyError(f"unknown part: {name}. Parts: {[p.name for p in self.parts]}")

    def ordered(self) -> list[Part]:
        """Parents before children."""
        out, placed = [], set()
        while len(out) < len(self.parts):
            for p in self.parts:
                if p.name not in placed and (p.parent is None or p.parent in placed):
                    out.append(p)
                    placed.add(p.name)
        return out


class Key(BaseModel):
    t: float = Field(..., ge=0, description="time in seconds")
    position: Vec3 = Field((0.0, 0.0, 0.0), description="offset from the rest position, in the parent's frame")
    rotation_deg: Vec3 = Field((0.0, 0.0, 0.0), description="intrinsic XYZ Euler rotation around the part's pivot, degrees (three.js order)")
    scale: Vec3 = (1.0, 1.0, 1.0)


class Track(BaseModel):
    part: str
    keys: list[Key] = Field(..., min_length=1)


class Attach(BaseModel):
    part: str = Field(..., description="a root part (no parent), e.g. a prop")
    to: str = Field(..., description="the part that holds it")
    start: float = Field(..., ge=0)
    end: float | None = Field(None, description="release time (default: end of the clip)")


class Clip(BaseModel):
    name: str
    duration: float = Field(..., gt=0)
    loop: bool = False
    tracks: list[Track] = Field(default_factory=list)
    attach: list[Attach] = Field(default_factory=list)


class Clips(BaseModel):
    clips: list[Clip] = Field(default_factory=list)

    def upsert(self, clip: Clip) -> None:
        self.clips = [c for c in self.clips if c.name != clip.name] + [clip]


def validate_clip(rig: Rig, clip: Clip) -> list[str]:
    """Errors that make a clip unusable with this rig (empty list = ok)."""
    errors = []
    names = {p.name for p in rig.parts}
    for tr in clip.tracks:
        if tr.part not in names:
            errors.append(f"track for unknown part '{tr.part}'")
    for a in clip.attach:
        if a.part not in names or a.to not in names:
            errors.append(f"attachment {a.part}->{a.to}: unknown part")
        elif rig.part(a.part).parent is not None:
            errors.append(f"attachment: '{a.part}' must be a root part (no parent)")
    return errors


# ---- evaluation --------------------------------------------------------------------------

def _trs(t: np.ndarray, q: np.ndarray, s: np.ndarray) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = Rotation.from_quat(q).as_matrix() * s
    m[:3, 3] = t
    return m


def _sample_track(keys: list[Key], t: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(position offset, quaternion xyzw, scale) at time t: linear / slerp, clamped at the ends."""
    ks = sorted(keys, key=lambda k: k.t)
    times = np.array([k.t for k in ks])
    pos = np.array([k.position for k in ks], dtype=np.float64)
    scl = np.array([k.scale for k in ks], dtype=np.float64)
    rots = Rotation.from_euler("XYZ", [k.rotation_deg for k in ks], degrees=True)
    if len(ks) == 1 or t <= times[0]:
        return pos[0], rots[0].as_quat(), scl[0]
    if t >= times[-1]:
        return pos[-1], rots[-1].as_quat(), scl[-1]
    i = int(np.searchsorted(times, t) - 1)
    w = (t - times[i]) / max(times[i + 1] - times[i], 1e-9)
    q = Slerp(times[i:i + 2], rots[i:i + 2])([t]).as_quat()[0]
    return pos[i] + (pos[i + 1] - pos[i]) * w, q, scl[i] + (scl[i + 1] - scl[i]) * w


def rest_translation(rig: Rig, part: Part) -> np.ndarray:
    pivot = np.asarray(part.pivot, dtype=np.float64)
    if part.parent is None:
        return pivot
    return pivot - np.asarray(rig.part(part.parent).pivot, dtype=np.float64)


def local_matrices(rig: Rig, clip: Clip | None, t: float) -> dict[str, np.ndarray]:
    tracks = {tr.part: tr for tr in (clip.tracks if clip else [])}
    out = {}
    for p in rig.parts:
        rest = rest_translation(rig, p)
        if p.name in tracks:
            dp, q, s = _sample_track(tracks[p.name].keys, t)
            out[p.name] = _trs(rest + dp, q, s)
        else:
            out[p.name] = _trs(rest, np.array([0, 0, 0, 1.0]), np.ones(3))
    return out


def world_matrices(rig: Rig, clip: Clip | None, t: float, local: dict[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    local = local or local_matrices(rig, clip, t)
    world: dict[str, np.ndarray] = {}
    for p in rig.ordered():
        world[p.name] = local[p.name] if p.parent is None else world[p.parent] @ local[p.name]
    return world


def bake_attachments(rig: Rig, clip: Clip, fps: float = 30.0) -> dict[str, list[tuple[float, np.ndarray]]]:
    """For every attached root part: sampled (t, node matrix) over the whole clip.

    While a span is active the part follows its holder with the relative pose it had at the grab;
    after release it stays where it was dropped (until a later span grabs it again).
    """
    if not clip.attach:
        return {}
    times = np.unique(np.concatenate([np.arange(0.0, clip.duration, 1.0 / fps), [clip.duration]]))
    return {part: [(float(t), attached_pose(rig, clip, part, float(t))) for t in times]
            for part in {a.part for a in clip.attach}}


def attached_pose(rig: Rig, clip: Clip, part: str, t: float) -> np.ndarray:
    spans = sorted((a for a in clip.attach if a.part == part), key=lambda a: a.start)
    offsets: list[np.ndarray] = []

    def end_of(a: Attach) -> float:
        return clip.duration if a.end is None else a.end

    def pose_before(i: int, time: float) -> np.ndarray:
        """Pose at ``time`` considering only spans before index i."""
        for j in range(i - 1, -1, -1):
            if time >= spans[j].start:
                hold_t = min(time, end_of(spans[j]))
                return world_matrices(rig, clip, hold_t)[spans[j].to] @ offsets[j]
        return world_matrices(rig, clip, time)[part]

    for i, a in enumerate(spans):
        holder_at_grab = world_matrices(rig, clip, a.start)[a.to]
        offsets.append(np.linalg.inv(holder_at_grab) @ pose_before(i, a.start))
    return pose_before(len(spans), t)

# ---- presets ------------------------------------------------------------------------------

AXES = {"x": 0, "y": 1, "z": 2}
PresetName = Literal["spin", "hinge", "slide", "bob", "recoil", "shake", "pulse"]


def preset_keys(name: str, duration: float, axis: str = "y", amount: float = 90.0, distance: float = 0.1) -> list[Key]:
    """Procedural keyframes for one part. ``amount`` = degrees (or turns for spin), ``distance`` = units."""
    i = AXES[axis]

    def rot(deg):
        r = [0.0, 0.0, 0.0]
        r[i] = deg
        return tuple(r)

    def off(d):
        p = [0.0, 0.0, 0.0]
        p[i] = d
        return tuple(p)

    d = duration
    if name == "spin":
        turns = amount / 360.0 if amount > 4 else amount
        steps = max(4, int(abs(turns) * 4))  # <=90 degrees per segment so slerp keeps the direction
        return [Key(t=d * k / steps, rotation_deg=rot(360.0 * turns * k / steps)) for k in range(steps + 1)]
    if name == "hinge":
        return [Key(t=0, rotation_deg=rot(0)), Key(t=d * 0.4, rotation_deg=rot(amount)), Key(t=d * 0.6, rotation_deg=rot(amount)), Key(t=d, rotation_deg=rot(0))]
    if name == "slide":
        return [Key(t=0), Key(t=d * 0.4, position=off(distance)), Key(t=d * 0.6, position=off(distance)), Key(t=d)]
    if name == "bob":
        return [Key(t=0), Key(t=d * 0.5, position=off(distance)), Key(t=d)]
    if name == "recoil":
        return [Key(t=0), Key(t=d * 0.08, position=off(-distance), rotation_deg=rot(amount * 0.1)), Key(t=d * 0.5), Key(t=d)]
    if name == "shake":
        n = 8
        return [Key(t=d * k / n, position=off(distance * (1 if k % 2 else -1) if 0 < k < n else 0)) for k in range(n + 1)]
    if name == "pulse":
        s = 1 + amount / 100.0
        return [Key(t=0), Key(t=d * 0.5, scale=(s, s, s)), Key(t=d)]
    raise ValueError(f"unknown preset: {name}")


# ---- rig construction ------------------------------------------------------------------------

def region_mask_faces(mesh: trimesh.Trimesh, inside) -> np.ndarray:
    return np.asarray(inside(mesh.triangles_center), dtype=bool)


def split_by_faces(mesh: trimesh.Trimesh, mask: np.ndarray) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """(selected, rest) by face mask; keeps UVs/colors, no capping (for open or textured meshes)."""
    a = mesh.submesh([np.nonzero(mask)[0]], append=True) if mask.any() else trimesh.Trimesh()
    b = mesh.submesh([np.nonzero(~mask)[0]], append=True) if (~mask).any() else trimesh.Trimesh()
    return a, b


# ---- glTF export ------------------------------------------------------------------------------

SKIN_KEY = "skin"  # meshes[SKIN_KEY] is the skinned mesh of a skeleton rig


def build_scene(rig: Rig, meshes: dict[str, trimesh.Trimesh]) -> trimesh.Scene:
    scene = trimesh.Scene()
    for p in rig.ordered():
        if p.is_bone:
            continue
        m = meshes[p.name].copy()
        m.apply_translation(-np.asarray(p.pivot, dtype=np.float64))
        local = np.eye(4)
        local[:3, 3] = rest_translation(rig, p)
        kwargs = {"node_name": p.name, "geom_name": p.name, "transform": local}
        if p.parent is not None:
            kwargs["parent_node_name"] = p.parent
        scene.add_geometry(m, **kwargs)
    return scene


def _decompose(m: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    t = m[:3, 3].copy()
    s = np.linalg.norm(m[:3, :3], axis=0)
    r = m[:3, :3] / np.where(s == 0, 1, s)
    if np.linalg.det(r) < 0:
        s[0] = -s[0]
        r[:, 0] = -r[:, 0]
    return t, Rotation.from_matrix(r).as_quat(), s


def clip_channels(rig: Rig, clip: Clip, fps: float = 30.0) -> list[tuple[str, str, np.ndarray, np.ndarray]]:
    """(node name, path, times, values) for a clip; attached parts are baked at ``fps``."""
    channels = []
    baked = bake_attachments(rig, clip, fps)
    for tr in clip.tracks:
        if tr.part in baked:
            continue
        p = rig.part(tr.part)
        rest = rest_translation(rig, p)
        ks = sorted(tr.keys, key=lambda k: k.t)
        times = np.array([k.t for k in ks], dtype=np.float32)
        channels.append((p.name, "translation", times, np.array([rest + np.asarray(k.position) for k in ks], dtype=np.float32)))
        quats = Rotation.from_euler("XYZ", [k.rotation_deg for k in ks], degrees=True).as_quat()
        for j in range(1, len(quats)):  # keep consecutive quaternions in the same hemisphere
            if np.dot(quats[j], quats[j - 1]) < 0:
                quats[j] = -quats[j]
        channels.append((p.name, "rotation", times, quats.astype(np.float32)))
        channels.append((p.name, "scale", times, np.array([k.scale for k in ks], dtype=np.float32)))
    for part, samples in baked.items():
        times = np.array([t for t, _ in samples], dtype=np.float32)
        trs = [_decompose(m) for _, m in samples]
        quats = np.array([q for _, q, _ in trs])
        for j in range(1, len(quats)):
            if np.dot(quats[j], quats[j - 1]) < 0:
                quats[j] = -quats[j]
        channels.append((part, "translation", times, np.array([t for t, _, _ in trs], dtype=np.float32)))
        channels.append((part, "rotation", times, quats.astype(np.float32)))
        channels.append((part, "scale", times, np.array([s for _, _, s in trs], dtype=np.float32)))
    return channels


def export_glb(rig: Rig, meshes: dict[str, trimesh.Trimesh], clips: list[Clip], path: str | Path, fps: float = 30.0,
               skin: tuple[np.ndarray, np.ndarray] | None = None) -> Path:
    """GLB with the part hierarchy and one glTF animation per clip (game-engine ready).

    Skeleton rigs: ``meshes[SKIN_KEY]`` is skinned to the bone parts with ``skin`` = (joints, weights) per
    vertex (joint indices into ``rig.bones``); rigid parts (props) are exported as usual."""
    if rig.skin is None:
        glb = build_scene(rig, meshes).export(file_type="glb")
        glb = _with_default_material(glb)
    else:
        from .io import shade_smooth_indexed
        from .skeleton import inject_skin

        scene = build_scene(rig, meshes)
        smooth, orig = shade_smooth_indexed(meshes[SKIN_KEY])
        scene.add_geometry(smooth, node_name="skin_mesh", geom_name="skin_mesh")
        glb = _with_default_material(scene.export(file_type="glb", include_normals=True))
        joints, weights = skin
        bones = [(b.name, b.parent, np.asarray(b.pivot, dtype=np.float64)) for b in rig.bones]
        glb = inject_skin(glb, "skin_mesh", bones, joints[orig], weights[orig])
    if clips:
        glb = _add_animations(glb, [(c.name, clip_channels(rig, c, fps)) for c in clips])
    path = Path(path)
    path.write_bytes(glb)
    return path


def _add_animations(glb: bytes, animations: list[tuple[str, list[tuple[str, str, np.ndarray, np.ndarray]]]]) -> bytes:
    magic, version, _ = struct.unpack("<4sII", glb[:12])
    json_len, _ = struct.unpack("<II", glb[12:20])
    doc = json.loads(glb[20:20 + json_len])
    rest = glb[20 + json_len:]
    bin_len, bin_type = struct.unpack("<II", rest[:8])
    binary = bytearray(rest[8:8 + bin_len])

    node_index = {n.get("name"): i for i, n in enumerate(doc.get("nodes", []))}
    views = doc.setdefault("bufferViews", [])
    accessors = doc.setdefault("accessors", [])
    kinds = {1: "SCALAR", 3: "VEC3", 4: "VEC4"}

    def accessor(arr: np.ndarray, minmax: bool = False) -> int:
        arr = np.ascontiguousarray(arr, dtype=np.float32)
        while len(binary) % 4:
            binary.append(0)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": arr.nbytes})
        binary.extend(arr.tobytes())
        comps = 1 if arr.ndim == 1 else arr.shape[1]
        acc = {"bufferView": len(views) - 1, "componentType": 5126, "count": int(arr.shape[0]), "type": kinds[comps]}
        if minmax:
            acc["min"] = [float(arr.min())]
            acc["max"] = [float(arr.max())]
        accessors.append(acc)
        return len(accessors) - 1

    out = []
    for name, channels in animations:
        samplers, chans = [], []
        for node, path, times, values in channels:
            if node not in node_index:
                continue
            samplers.append({"input": accessor(times, minmax=True), "output": accessor(values), "interpolation": "LINEAR"})
            chans.append({"sampler": len(samplers) - 1, "target": {"node": node_index[node], "path": path}})
        if chans:
            out.append({"name": name, "samplers": samplers, "channels": chans})
    if out:
        doc.setdefault("animations", []).extend(out)
    while len(binary) % 4:
        binary.append(0)
    doc["buffers"][0]["byteLength"] = len(binary)
    chunk = json.dumps(doc, separators=(",", ":")).encode()
    chunk += b" " * (-len(chunk) % 4)
    body = struct.pack("<II", len(chunk), 0x4E4F534A) + chunk + struct.pack("<II", len(binary), bin_type) + bytes(binary)
    return struct.pack("<4sII", magic, version, 12 + len(body)) + body
