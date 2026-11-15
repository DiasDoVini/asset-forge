"""Humanoid skeletons in the Unreal-mannequin layout and retargeting of library animations onto them.

The Universal Animation Library (Quaternius, CC0) and the UE mannequin share one skeleton: root, pelvis,
spine_01..03, neck_01, Head, clavicle/upperarm/lowerarm/hand, five fingers, thigh/calf/foot/ball. A character
fitted with these names takes those animations directly; Mixamo-named skeletons (3d.skeleton) are mapped.

Retargeting works on world-space rotations, so the two skeletons' bind orientations don't need to match
(ours bind with identity rotations, see ``skeleton``): every source bone's rotation away from its bind pose is
applied to the matching target bone, after aligning the target's rest direction with the source's (a character
in A-pose follows a T-pose library). Hips translation is scaled by the ratio of leg lengths.
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial.transform import Rotation

from .anim import Clip, Key, Track
from .gltf_anim import GltfSkeleton, SampledClip
from .skeleton import Joint, Skeleton, fit_humanoid

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
SIDES = (("l", "Left"), ("r", "Right"))

# Mixamo (3d.skeleton) -> Unreal/UAL names
MIXAMO_TO_UNREAL = {"Hips": "pelvis", "Spine": "spine_01", "Spine1": "spine_02", "Spine2": "spine_03",
                    "Neck": "neck_01", "Head": "Head", "HeadTop_End": "Head_leaf"}
for _s, _S in SIDES:
    MIXAMO_TO_UNREAL.update({f"{_S}Shoulder": f"clavicle_{_s}", f"{_S}Arm": f"upperarm_{_s}",
                             f"{_S}ForeArm": f"lowerarm_{_s}", f"{_S}Hand": f"hand_{_s}", f"{_S}Hand_End": f"hand_leaf_{_s}",
                             f"{_S}UpLeg": f"thigh_{_s}", f"{_S}Leg": f"calf_{_s}", f"{_S}Foot": f"foot_{_s}",
                             f"{_S}ToeBase": f"ball_{_s}", f"{_S}Toe_End": f"ball_leaf_{_s}"})
    for _f in FINGERS:
        for _k in range(1, 5):
            MIXAMO_TO_UNREAL[f"{_S}Hand{_f.capitalize()}{_k}"] = f"{_f}_0{_k}_leaf_{_s}" if _k == 4 else f"{_f}_0{_k}_{_s}"

# markers we add that the library doesn't have, and what they line up with when aligning rest directions
ALIGN_ALIASES = {"hand_leaf_l": "middle_04_leaf_l", "hand_leaf_r": "middle_04_leaf_r"}
PRIMARY_CHILD = {"root": "pelvis", "pelvis": "spine_01", "spine_03": "neck_01", "hand_l": "middle_01_l",
                 "hand_r": "middle_01_r"}
TRANSLATED = {"root", "pelvis"}


def unreal_name(name: str) -> str:
    return MIXAMO_TO_UNREAL.get(name, name)


def is_marker(name: str) -> bool:
    return "leaf" in name or name.endswith("_End")


# ---- fitting ---------------------------------------------------------------------------------

def fit_character(mesh: trimesh.Trimesh, reference: GltfSkeleton | None = None, *, naming: str = "unreal",
                  fingers: bool = False) -> Skeleton:
    """Humanoid skeleton fitted to a standing character (Y-up, facing +Z, T- or A-pose).

    ``naming="unreal"``: UE-mannequin/UAL names with a ``root`` on the floor; ``"mixamo"``: 3d.skeleton's names.
    ``fingers``: five 3-bone fingers per hand, shaped after ``reference``'s hand (the library mannequin) scaled to
    this character's hand; without them the hand is one bone."""
    base = fit_humanoid(mesh)
    pos = {j.name: np.asarray(j.position, dtype=np.float64) for j in base.joints}
    joints: list[Joint] = []
    if naming == "unreal":
        hips = pos["Hips"]
        floor = float(mesh.bounds[0][1])
        joints.append(Joint("root", None, np.array([hips[0], floor, hips[2]]), deform=False))
    for j in base.joints:
        name = unreal_name(j.name) if naming == "unreal" else j.name
        parent = (unreal_name(j.parent) if j.parent else "root") if naming == "unreal" else j.parent
        if fingers and j.name.endswith("Hand_End"):
            continue
        joints.append(Joint(name, parent, pos[j.name], deform=not is_marker(name)))
    if fingers:
        if reference is None:
            raise ValueError("fingers need a reference skeleton (the animation library's mannequin)")
        for s, S in SIDES:
            joints += _fit_fingers(pos, reference, s, S, naming)
    return Skeleton(joints, template=f"humanoid-{naming}" + ("-fingers" if fingers else ""))


def _swing(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation (3x3) taking direction a onto direction b."""
    a = a / max(np.linalg.norm(a), 1e-12)
    b = b / max(np.linalg.norm(b), 1e-12)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        if c > 0:
            return np.eye(3)
        axis = np.cross(a, [1.0, 0, 0] if abs(a[0]) < 0.9 else [0, 1.0, 0])
        return Rotation.from_rotvec(np.pi * axis / np.linalg.norm(axis)).as_matrix()
    angle = np.arctan2(np.linalg.norm(v), c)
    return Rotation.from_rotvec(v / np.linalg.norm(v) * angle).as_matrix()


def _fit_fingers(pos: dict[str, np.ndarray], ref: GltfSkeleton, s: str, S: str, naming: str) -> list[Joint]:
    idx = ref.index()
    need = [f"hand_{s}", f"lowerarm_{s}", f"middle_04_leaf_{s}"]
    if any(n not in idx for n in need):
        raise ValueError("the reference skeleton has no fingers")
    P = ref.bind_positions
    hand_u, fore_u, tip_u = (P[idx[n]] for n in need)
    hand_c, fore_c, tip_c = pos[f"{S}Hand"], pos[f"{S}ForeArm"], pos[f"{S}Hand_End"]
    rot = _swing(hand_u - fore_u, hand_c - fore_c)
    k = np.linalg.norm(tip_c - hand_c) / max(np.linalg.norm(tip_u - hand_u), 1e-9)
    out = []
    hand_name = f"hand_{s}" if naming == "unreal" else f"{S}Hand"
    for f in FINGERS:
        parent = hand_name
        for n in range(1, 5):
            src = f"{f}_0{n}_leaf_{s}" if n == 4 else f"{f}_0{n}_{s}"
            if src not in idx:
                break
            p = hand_c + k * rot @ (P[idx[src]] - hand_u)
            name = src if naming == "unreal" else f"{S}Hand{f.capitalize()}{n}"
            out.append(Joint(name, parent, p, deform=n < 4))
            parent = name
    return out


# ---- retargeting -----------------------------------------------------------------------------

def _rot(m: np.ndarray) -> np.ndarray:
    """Rotation part of (…,4,4) or (…,3,3) matrices with the scale removed."""
    r = m[..., :3, :3]
    return r / np.linalg.norm(r, axis=-2, keepdims=True)


def retarget(source: GltfSkeleton, clip: SampledClip, target: Skeleton, *, name: str | None = None,
             loop: bool | None = None, rot_tol_deg: float = 0.4, pos_tol: float | None = None) -> Clip:
    """A clip for ``target`` (identity-bind skeleton, joint names Unreal or Mixamo) that reproduces ``clip``."""
    src = source.index()
    src_bind_rot = _rot(source.bind_world)
    src_bind_pos = source.bind_positions
    world_rot = _rot(clip.world)                        # (T, J, 3, 3)
    world_pos = clip.world[..., :3, 3]
    delta = np.einsum("tjab,jcb->tjac", world_rot, src_bind_rot)   # world rotation away from bind: W·B^T

    tj = {j.name: j for j in target.joints}
    mapped = {j.name: unreal_name(j.name) for j in target.joints if unreal_name(j.name) in src}
    if "pelvis" not in mapped.values():
        raise ValueError("the target skeleton has no hips the library can drive (needs a humanoid skeleton)")

    # rest alignment: rotate each target bone's rest direction onto the source bone's bind direction
    align: dict[str, np.ndarray] = {}
    for j in target.joints:                              # parents come first
        inherited = align.get(j.parent, np.eye(3)) if j.parent else np.eye(3)
        b = mapped.get(j.name)
        kids = [c for c in target.joints if c.parent == j.name]
        want = PRIMARY_CHILD.get(unreal_name(j.name))
        kid = next((c for c in kids if unreal_name(c.name) == want), None) if want else None
        if kid is None and len(kids) == 1:
            kid = kids[0]
        kb = None
        if kid is not None:
            kb = mapped.get(kid.name) or ALIGN_ALIASES.get(unreal_name(kid.name))
            kb = kb if kb in src else None
        if b is None or kb is None:
            align[j.name] = inherited
            continue
        d_t = np.asarray(kid.position) - np.asarray(j.position)
        d_s = src_bind_pos[src[kb]] - src_bind_pos[src[b]]
        align[j.name] = _swing(d_t, d_s) if np.linalg.norm(d_t) > 1e-9 else inherited

    # hips height ratio (leg length) for translations
    def leg(names_pos: dict[str, np.ndarray]) -> float:
        hips = names_pos["pelvis"][1]
        low = min(p[1] for n, p in names_pos.items() if n.startswith(("foot", "ball")))
        return max(hips - low, 1e-6)

    t_pos = {mapped[n]: np.asarray(tj[n].position) for n in mapped}
    s_pos = {n: src_bind_pos[i] for n, i in src.items()}
    scale = leg(t_pos) / leg(s_pos) if any(n.startswith("foot") for n in t_pos) else 1.0
    pos_tol = pos_tol if pos_tol is not None else 0.002 * leg(t_pos)

    # world rotation of every target joint over time: C = delta(source) · A
    T = len(clip.times)
    C: dict[str, np.ndarray] = {}
    for j in target.joints:
        b = mapped.get(j.name)
        if b is not None:
            C[j.name] = delta[:, src[b]] @ align[j.name]
        else:   # markers: follow the parent
            C[j.name] = C[j.parent] if j.parent else np.repeat(np.eye(3)[None], T, axis=0)

    tracks: list[Track] = []
    for j in target.joints:
        b = mapped.get(j.name)
        if b is None:
            continue
        parent_c = C[j.parent] if j.parent else np.repeat(np.eye(3)[None], T, axis=0)
        local = np.einsum("tba,tbc->tac", parent_c, C[j.name])          # P^T · C
        quats = Rotation.from_matrix(local).as_quat()
        for k in range(1, T):
            if np.dot(quats[k], quats[k - 1]) < 0:
                quats[k] = -quats[k]
        offsets = None
        if b in TRANSLATED:
            pb = source.parents[src[b]]
            if j.parent is None or pb < 0:
                offsets = scale * (world_pos[:, src[b]] - src_bind_pos[src[b]])
            else:
                rest = np.asarray(j.position) - np.asarray(tj[j.parent].position)
                dp = delta[:, pb]
                u = np.einsum("tba,tb->ta", dp, world_pos[:, src[b]] - world_pos[:, pb])
                v = src_bind_pos[src[b]] - src_bind_pos[pb]
                a_p = align.get(j.parent, np.eye(3))
                offsets = (rest + scale * (u - v)) @ a_p - rest       # A_p^T·(…) - rest, row vectors
        keep = reduce_keys(clip.times, quats, offsets, rot_tol_deg, pos_tol)
        if offsets is None and len(keep) == 2 and _same_rot(quats[keep[0]], quats[keep[1]], rot_tol_deg) \
                and _same_rot(quats[keep[0]], np.array([0, 0, 0, 1.0]), rot_tol_deg):
            continue                                     # never leaves the rest pose: no track
        euler = Rotation.from_quat(quats[keep]).as_euler("XYZ", degrees=True)
        keys = [Key(t=round(float(clip.times[k]), 4), rotation_deg=tuple(round(float(x), 3) for x in euler[i]),
                    position=tuple(round(float(x), 5) for x in offsets[k]) if offsets is not None else (0.0, 0.0, 0.0))
                for i, k in enumerate(keep)]
        tracks.append(Track(part=j.name, keys=keys))
    name = name or clip.name
    return Clip(name=name, duration=max(clip.duration, 1e-3), loop=name.lower().endswith("loop") if loop is None else loop,
                tracks=tracks)


def _same_rot(a: np.ndarray, b: np.ndarray, tol_deg: float) -> bool:
    return 2 * np.degrees(np.arccos(min(1.0, abs(float(np.dot(a, b)))))) <= tol_deg


def reduce_keys(times: np.ndarray, quats: np.ndarray, offsets: np.ndarray | None, rot_tol_deg: float,
                pos_tol: float) -> list[int]:
    """Indices of the keyframes to keep so that linear/slerp interpolation stays within the tolerances."""
    n = len(times)
    if n <= 2:
        return list(range(n))
    keep = {0, n - 1}
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        mid = np.arange(a + 1, b)
        w = (times[mid] - times[a]) / max(times[b] - times[a], 1e-9)
        qa, qb = quats[a], quats[b]
        dot = float(np.dot(qa, qb))
        if dot < 0:
            qb, dot = -qb, -dot
        if dot > 0.9995:
            q = qa[None] + w[:, None] * (qb - qa)[None]
        else:
            th = np.arccos(dot)
            q = (np.sin((1 - w) * th)[:, None] * qa + np.sin(w * th)[:, None] * qb) / np.sin(th)
        q /= np.linalg.norm(q, axis=1, keepdims=True)
        err = 2 * np.degrees(np.arccos(np.clip(np.abs(np.sum(q * quats[mid], axis=1)), 0, 1))) / rot_tol_deg
        if offsets is not None:
            p = offsets[a][None] + w[:, None] * (offsets[b] - offsets[a])[None]
            err = np.maximum(err, np.linalg.norm(p - offsets[mid], axis=1) / max(pos_tol, 1e-9))
        worst = int(np.argmax(err))
        if err[worst] > 1.0:
            k = int(mid[worst])
            keep.add(k)
            stack += [(a, k), (k, b)]
    return sorted(keep)
