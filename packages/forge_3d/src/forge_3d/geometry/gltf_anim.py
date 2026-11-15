"""Read skeletal animations out of glTF/GLB files (animation libraries such as Quaternius' Universal Animation
Library): the skin's skeleton in bind pose and every clip sampled on a common time grid as world matrices.

Only what retargeting needs: no meshes, morph targets or cameras. Non-joint ancestors of the skeleton (an
"Armature" node, a -90° X root correction exported by Blender) are taken as static.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

_DTYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NORM = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path: str | Path) -> tuple[dict, bytes]:
    data = Path(path).read_bytes()
    magic, _, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError(f"{Path(path).name}: not a GLB file")
    doc, blob, off = None, b"", 12
    while off < length:
        clen, ctype = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == b"JSON":
            doc = json.loads(chunk)
        elif ctype == b"BIN\x00":
            blob = bytes(chunk)
        off += 8 + clen
    if doc is None:
        raise ValueError(f"{Path(path).name}: GLB without JSON chunk")
    return doc, blob


def read_json_only(path: str | Path) -> dict:
    """The GLB's JSON chunk without loading the binary (fast library indexing)."""
    with open(path, "rb") as f:
        head = f.read(20)
        magic, _, _ = struct.unpack_from("<4sII", head, 0)
        if magic != b"glTF":
            raise ValueError("not a GLB file")
        clen, ctype = struct.unpack_from("<I4s", head, 12)
        if ctype != b"JSON":
            raise ValueError("GLB without JSON chunk first")
        return json.loads(f.read(clen))


def accessor(doc: dict, blob: bytes, index: int) -> np.ndarray:
    acc = doc["accessors"][index]
    n = _COMPONENTS[acc["type"]]
    dtype = np.dtype(_DTYPES[acc["componentType"]])
    count = acc["count"]
    if "bufferView" not in acc:
        out = np.zeros((count, n), dtype=np.float32)
    else:
        view = doc["bufferViews"][acc["bufferView"]]
        offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride") or n * dtype.itemsize
        if stride == n * dtype.itemsize:
            out = np.frombuffer(blob, dtype, count * n, offset).reshape(count, n)
        else:
            rows = [np.frombuffer(blob, dtype, n, offset + i * stride) for i in range(count)]
            out = np.stack(rows)
    out = out.astype(np.float64)
    if acc.get("normalized") and acc["componentType"] in _NORM:
        out = out / _NORM[acc["componentType"]]
    return out if n > 1 else out[:, 0]


def _trs_matrix(t: np.ndarray, q: np.ndarray, s: np.ndarray) -> np.ndarray:
    """(…,4,4) from translations (…,3), quaternions xyzw (…,4), scales (…,3)."""
    shape = t.shape[:-1]
    m = np.zeros(shape + (4, 4))
    m[..., :3, :3] = Rotation.from_quat(q.reshape(-1, 4)).as_matrix().reshape(shape + (3, 3)) * s[..., None, :]
    m[..., :3, 3] = t
    m[..., 3, 3] = 1.0
    return m


def _node_trs(node: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if "matrix" in node:
        m = np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
        s = np.linalg.norm(m[:3, :3], axis=0)
        r = m[:3, :3] / np.where(s == 0, 1, s)
        return m[:3, 3].copy(), Rotation.from_matrix(r).as_quat(), s
    return (np.asarray(node.get("translation", [0, 0, 0]), dtype=np.float64),
            np.asarray(node.get("rotation", [0, 0, 0, 1]), dtype=np.float64),
            np.asarray(node.get("scale", [1, 1, 1]), dtype=np.float64))


@dataclass
class GltfSkeleton:
    names: list[str]
    parents: list[int]                      # index into names, -1 for the top joint(s)
    rest: list[tuple[np.ndarray, np.ndarray, np.ndarray]]   # local TRS in the bind pose
    bind_world: np.ndarray                  # (J, 4, 4) world matrices of the bind pose
    above: list[np.ndarray]                 # per joint: static world matrix of its non-joint parent (top joints)
    nodes: list[int] = field(default_factory=list)  # glTF node index of each joint

    def index(self) -> dict[str, int]:
        return {n: i for i, n in enumerate(self.names)}

    @property
    def bind_positions(self) -> np.ndarray:
        return self.bind_world[:, :3, 3]

    def children(self, j: int) -> list[int]:
        return [k for k, p in enumerate(self.parents) if p == j]


@dataclass
class SampledClip:
    name: str
    times: np.ndarray        # (T,)
    world: np.ndarray        # (T, J, 4, 4)

    @property
    def duration(self) -> float:
        return float(self.times[-1]) if len(self.times) else 0.0


def _parents_of(doc: dict) -> dict[int, int]:
    parent = {}
    for i, nd in enumerate(doc.get("nodes", [])):
        for c in nd.get("children", []):
            parent[c] = i
    return parent


def _static_world(doc: dict, parent: dict[int, int], node: int | None) -> np.ndarray:
    m = np.eye(4)
    while node is not None:
        t, q, s = _node_trs(doc["nodes"][node])
        m = _trs_matrix(t, q, s) @ m
        node = parent.get(node)
    return m


def load_skeleton(doc: dict, blob: bytes, skin_index: int = 0) -> GltfSkeleton:
    if not doc.get("skins"):
        raise ValueError("the file has no skin (skeleton)")
    skin = doc["skins"][skin_index]
    joints = list(skin["joints"])
    parent = _parents_of(doc)
    pos = {node: k for k, node in enumerate(joints)}
    names = [doc["nodes"][j].get("name") or f"joint{k}" for k, j in enumerate(joints)]
    parents, above, rest = [], [], []
    for node in joints:
        p = parent.get(node)
        # the nearest ancestor that is a joint (skins may skip intermediate nodes)
        q = p
        chain_static = np.eye(4)
        while q is not None and q not in pos:
            t, r, s = _node_trs(doc["nodes"][q])
            chain_static = _trs_matrix(t, r, s) @ chain_static
            q = parent.get(q)
        if q is None:
            parents.append(-1)
            above.append(_static_world(doc, parent, p))
        else:
            parents.append(pos[q])
            above.append(chain_static)      # nodes between the joint and its joint parent (usually identity)
        rest.append(_node_trs(doc["nodes"][node]))
    world = np.zeros((len(joints), 4, 4))
    for k in _topological(parents):
        local = _trs_matrix(*rest[k])
        base = above[k] if parents[k] < 0 else world[parents[k]] @ above[k]
        world[k] = base @ local
    return GltfSkeleton(names, parents, rest, world, above, joints)


def _topological(parents: list[int]) -> list[int]:
    order, placed = [], set()
    while len(order) < len(parents):
        progressed = False
        for k, p in enumerate(parents):
            if k not in placed and (p < 0 or p in placed):
                order.append(k)
                placed.add(k)
                progressed = True
        if not progressed:
            raise ValueError("skeleton has a parent cycle")
    return order


def _sample_channel(times: np.ndarray, values: np.ndarray, interp: str, path: str, grid: np.ndarray) -> np.ndarray:
    if interp == "CUBICSPLINE":   # (in-tangent, value, out-tangent) triplets: keep the values
        values = values.reshape(len(times), 3, -1)[:, 1, :]
    if len(times) == 1:
        return np.repeat(values[:1], len(grid), axis=0)
    g = np.clip(grid, times[0], times[-1])
    if interp == "STEP":
        idx = np.clip(np.searchsorted(times, g, side="right") - 1, 0, len(times) - 1)
        return values[idx]
    if path == "rotation":
        quats = values / np.linalg.norm(values, axis=1, keepdims=True)
        return Slerp(times, Rotation.from_quat(quats))(g).as_quat()
    return np.stack([np.interp(g, times, values[:, c]) for c in range(values.shape[1])], axis=1)


def sample_clip(doc: dict, blob: bytes, skeleton: GltfSkeleton, anim_index: int, fps: float = 30.0) -> SampledClip:
    anim = doc["animations"][anim_index]
    joint_of = {node: k for k, node in enumerate(skeleton.nodes)}
    channels = []
    end = 0.0
    for ch in anim["channels"]:
        node = ch["target"].get("node")
        path = ch["target"]["path"]
        if node not in joint_of or path not in ("translation", "rotation", "scale"):
            continue
        smp = anim["samplers"][ch["sampler"]]
        times = accessor(doc, blob, smp["input"])
        values = accessor(doc, blob, smp["output"])
        if values.ndim == 1:
            values = values[:, None]
        channels.append((joint_of[node], path, times, values, smp.get("interpolation", "LINEAR")))
        end = max(end, float(times[-1]))
    n = max(2, int(round(end * fps)) + 1)
    grid = np.linspace(0.0, end, n) if end > 0 else np.zeros(1)
    J, T = len(skeleton.names), len(grid)
    trans = np.array([np.repeat(r[0][None], T, axis=0) for r in skeleton.rest])   # (J,T,3)
    rots = np.array([np.repeat(r[1][None], T, axis=0) for r in skeleton.rest])
    scales = np.array([np.repeat(r[2][None], T, axis=0) for r in skeleton.rest])
    for j, path, times, values, interp in channels:
        sampled = _sample_channel(times, values, interp, path, grid)
        {"translation": trans, "rotation": rots, "scale": scales}[path][j] = sampled
    local = _trs_matrix(trans, rots, scales)            # (J,T,4,4)
    world = np.zeros((T, J, 4, 4))
    for k in _topological(skeleton.parents):
        p = skeleton.parents[k]
        base = skeleton.above[k][None] if p < 0 else world[:, p] @ skeleton.above[k][None]
        world[:, k] = base @ local[k]
    return SampledClip(anim.get("name") or f"clip{anim_index}", grid, world)


class AnimationFile:
    """A GLB with a skinned skeleton and animations, loaded lazily."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.doc, self.blob = read_glb(self.path)
        self.skeleton = load_skeleton(self.doc, self.blob)
        self.clip_names = [a.get("name") or f"clip{i}" for i, a in enumerate(self.doc.get("animations", []))]

    def clip(self, name: str, fps: float = 30.0) -> SampledClip:
        if name not in self.clip_names:
            raise KeyError(f"{self.path.name} has no animation '{name}'")
        return sample_clip(self.doc, self.blob, self.skeleton, self.clip_names.index(name), fps)
