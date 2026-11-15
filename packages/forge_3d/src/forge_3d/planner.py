"""Natural-language motion planning with Claude: prompt + rig description -> keyframed clip.

Needs Anthropic API credentials (ANTHROPIC_API_KEY, or a profile from `ant auth login`). Without them the
pipelines still work with explicit keyframes or presets, and agents (e.g. Claude Code over MCP/CLI) can
write the clip JSON themselves.
"""

from __future__ import annotations

import json

import numpy as np
import trimesh

from .geometry.anim import Attach, Clip, Key, Rig, Track

MODEL = "claude-opus-5-5"


class PlannerUnavailable(RuntimeError):
    pass


class MissingParts(ValueError):
    """The motion needs parts the rig doesn't have yet (they must be split off first)."""

    def __init__(self, parts: list[dict], notes: str):
        self.parts = parts
        names = ", ".join(f"{p['name']} ({p['description']})" for p in parts)
        super().__init__(f"this animation needs parts that are not separated yet: {names}. "
                         f"Split them off with 3d.rig (editor: Animar > Peças) and try again. {notes}".strip())


_VEC = {"type": "array", "items": {"type": "number"}}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["missing_parts", "notes", "duration", "loop", "tracks", "attach"],
    "properties": {
        "missing_parts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "description"],
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}}}},
        "notes": {"type": "string"},
        "duration": {"type": "number"},
        "loop": {"type": "boolean"},
        "tracks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["part", "keys"],
            "properties": {
                "part": {"type": "string"},
                "keys": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["t", "position", "rotation_deg", "scale"],
                    "properties": {"t": {"type": "number"}, "position": _VEC, "rotation_deg": _VEC, "scale": _VEC}}}}}},
        "attach": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["part", "to", "start", "end"],
            "properties": {"part": {"type": "string"}, "to": {"type": "string"}, "start": {"type": "number"},
                           "end": {"anyOf": [{"type": "number"}, {"type": "null"}]}}}},
    },
}

SYSTEM = """You plan keyframe animations for rigid-part 3D game assets.

The asset is a hierarchy of rigid parts. Coordinates are Y-up; generated models usually face +Z.
Each part rotates and scales around its pivot. A keyframe is an OFFSET from the part's rest pose,
expressed in its parent's frame:
- position: [dx, dy, dz] added to the rest position (asset units)
- rotation_deg: [rx, ry, rz] intrinsic XYZ Euler rotation around the pivot, in degrees
- scale: [sx, sy, sz] multipliers (1 = unchanged)
Children follow their parents. Only animate parts that exist. Times are seconds from 0 to duration;
start each track with a key at t=0 that matches the rest pose unless the motion starts elsewhere, and keep
rotations between consecutive keys under 90 degrees (add intermediate keys for bigger turns).
"attach" makes a ROOT part (a prop) follow another part from `start` to `end` (null = until the end),
keeping the relative pose it had when grabbed - use it for picking up, holding and putting down objects.

If the requested motion needs a part that is not separated in this rig (e.g. a magazine, a trigger, a lid,
a door, an arm), list it in missing_parts with a short description of where it is and leave tracks empty.
Otherwise leave missing_parts empty. Use notes for one or two sentences about what the animation does.
Keep motions physically plausible and readable for a game: clear anticipation, motion and settle.

SKELETON RIGS (parts of kind "bone"): the model is one skinned mesh bent by a joint hierarchy. Each bone's
"pivot" is its joint; rotating a bone bends the mesh there and moves every child joint. Bones are bound
with no rotation, so rotation_deg is the joint rotation from the bind pose, in the parent's frame (same
axes as the model: X right->left of the screen is the model's left = +X, Y up, Z toward the viewer).
Animate mostly with rotations; translate only the root (Hips) for bobbing, crouching or moving.
Humanoid skeletons use Mixamo names (Hips, Spine, Spine1, Spine2, Neck, Head, Left/RightShoulder, Arm,
ForeArm, Hand, Left/RightUpLeg, Leg, Foot, ToeBase). For a limb hanging down, a NEGATIVE X rotation swings
it forward (toward +Z) and positive X swings it back; a knee (Leg) bends with positive X; an elbow
(ForeArm) bends with negative X when the arm hangs down. Z rotation raises an arm sideways: +Z for the
left arm, -Z for the right arm. Y rotation twists (turn head, torso). Keep joint rotations anatomical
(knee 0..130, elbow 0..140, neck +-60). Never list missing_parts for skeleton rigs: limbs already bend;
only props held in hands would be missing assets (mention them in notes)."""


def describe_rig(rig: Rig, meshes: dict[str, trimesh.Trimesh]) -> str:
    rows = []
    for p in rig.parts:
        if p.is_bone:
            if p.deform:
                rows.append({"name": p.name, "parent": p.parent, "pivot": np.round(p.pivot, 4).tolist(),
                             "kind": "bone (skeleton joint)"})
            continue
        lo, hi = meshes[p.name].bounds
        rows.append({
            "name": p.name, "parent": p.parent, "pivot": np.round(p.pivot, 4).tolist(),
            "bbox_min": np.round(lo, 4).tolist(), "bbox_max": np.round(hi, 4).tolist(),
            "kind": "prop (another asset)" if p.source.startswith("asset:") else "part of the model",
        })
    return json.dumps({"units": rig.units, "parts": rows}, indent=1)


def plan_clip(rig: Rig, meshes: dict[str, trimesh.Trimesh], prompt: str, name: str = "clip",
              duration: float | None = None, loop: bool = False, client=None) -> tuple[Clip, list[str]]:
    """Returns (clip, notes). Raises MissingParts when the rig must be split further first."""
    if client is None:
        try:
            import anthropic

            client = anthropic.Anthropic()
        except Exception as exc:  # SDK missing or no credential source
            raise PlannerUnavailable(f"Claude API not available ({exc}). Use presets/keyframes, or set "
                                     "ANTHROPIC_API_KEY / run `ant auth login`.") from exc
    user = (f"Rig:\n{describe_rig(rig, meshes)}\n\nAnimation request: {prompt}\n"
            + (f"Target duration: about {duration} s. " if duration else "")
            + ("It should loop seamlessly." if loop else ""))
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        name_ = type(exc).__name__
        if "Authentication" in name_ or "auth" in str(exc).lower():
            raise PlannerUnavailable(f"Claude API credentials missing or invalid ({name_}). Set ANTHROPIC_API_KEY "
                                     "or run `ant auth login`; presets and keyframes work without it.") from exc
        raise
    if response.stop_reason == "refusal":
        raise RuntimeError("the planner declined this request")
    text = next(b.text for b in response.content if b.type == "text")
    return clip_from_plan(json.loads(text), name=name, loop=loop)


def clip_from_plan(plan: dict, name: str, loop: bool = False) -> tuple[Clip, list[str]]:
    if plan.get("missing_parts"):
        raise MissingParts(plan["missing_parts"], plan.get("notes", ""))

    def vec(v, default):
        v = list(v or [])
        return tuple((v + list(default))[:3]) if len(v) < 3 else tuple(v[:3])

    tracks = [Track(part=tr["part"], keys=[Key(t=max(0.0, k["t"]), position=vec(k.get("position"), (0, 0, 0)),
                                                rotation_deg=vec(k.get("rotation_deg"), (0, 0, 0)),
                                                scale=vec(k.get("scale"), (1, 1, 1))) for k in tr["keys"]])
              for tr in plan.get("tracks", []) if tr.get("keys")]
    attach = [Attach(part=a["part"], to=a["to"], start=max(0.0, a["start"]), end=a.get("end")) for a in plan.get("attach", [])]
    last_key = max([k.t for tr in tracks for k in tr.keys] + [a.end or 0 for a in attach] + [0.1])
    duration = max(float(plan.get("duration") or 0), last_key)
    clip = Clip(name=name, duration=duration, loop=bool(plan.get("loop", loop)), tracks=tracks, attach=attach)
    return clip, [plan["notes"]] if plan.get("notes") else []
