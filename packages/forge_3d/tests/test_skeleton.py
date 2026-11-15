import json
import struct

import numpy as np
import pytest
import trimesh
from forge_core import Project, run_pipeline
from forge_core.pipeline import Registry, set_registry

import forge_3d
from forge_3d import geometry as g
from forge_3d.geometry import skeleton as sk


def _box(size, center):
    b = trimesh.creation.box(size)
    b.apply_translation(center)
    return b


def _figure() -> trimesh.Trimesh:
    """A blocky 1.8 m character in A-pose, facing +Z (left side = +X)."""
    parts = [
        _box((0.36, 0.55, 0.22), (0, 1.22, 0)),          # torso
        _box((0.30, 0.20, 0.20), (0, 0.90, 0)),          # pelvis
        trimesh.creation.icosphere(radius=0.12).apply_translation((0, 1.66, 0)),
        _box((0.08, 0.08, 0.08), (0, 1.52, 0)),          # neck
    ]
    for sign in (1, -1):
        arm = _box((0.55, 0.09, 0.09), (0, 0, 0))
        arm.apply_transform(trimesh.transformations.rotation_matrix(np.radians(-35 * sign), (0, 0, 1)))
        arm.apply_translation((sign * 0.42, 1.30, 0))
        parts.append(arm)
        parts.append(_box((0.13, 0.82, 0.13), (sign * 0.10, 0.41, 0)))       # leg
        parts.append(_box((0.11, 0.06, 0.24), (sign * 0.10, 0.03, 0.05)))    # foot
    mesh = trimesh.util.concatenate(parts)
    return trimesh.Trimesh(mesh.vertices, mesh.faces).subdivide().subdivide()


def test_fit_humanoid_finds_hands_feet_and_shoulders():
    m = _figure()
    s = sk.fit_humanoid(m)
    pos = {j.name: j.position for j in s.joints}
    assert [j.name for j in s.joints][:3] == ["Hips", "Spine", "Spine1"]
    assert pos["LeftHand"][0] > 0.45 and pos["RightHand"][0] < -0.45          # hands at the arm ends, left = +X
    assert pos["LeftFoot"][1] < 0.15 and abs(pos["LeftFoot"][0] - 0.10) < 0.05
    assert 1.25 < pos["LeftArm"][1] < 1.55                                    # shoulders at the top of the torso
    assert pos["Head"][1] > pos["Neck"][1] > pos["Spine2"][1] > pos["Hips"][1]
    assert not s.by_name()["HeadTop_End"].deform


def test_skin_weights_blend_across_a_joint():
    cyl = trimesh.creation.cylinder(radius=0.1, height=2.0, sections=24)
    cyl = trimesh.Trimesh(cyl.vertices, cyl.faces).subdivide().subdivide()
    skel = sk.Skeleton([sk.Joint("Root", None, np.array([0, 0, -1.0])), sk.Joint("Mid", "Root", np.array([0, 0, 0.0])),
                        sk.Joint("Tip_End", "Mid", np.array([0, 0, 1.0]), deform=False)])
    J, W = sk.skin_weights(cyl, skel)
    assert np.allclose(W.sum(axis=1), 1.0, atol=1e-5)
    z = cyl.vertices[:, 2]
    mid_w = np.where(J == 1, W, 0).sum(axis=1)                               # weight of the "Mid" bone
    assert mid_w[z > 0.6].mean() > 0.9 and mid_w[z < -0.6].mean() < 0.1
    near = np.abs(z) < 0.1
    assert 0.2 < mid_w[near].mean() < 0.8                                    # blended at the joint
    assert not (J == 2).any()                                                # end markers carry no weight


@pytest.fixture
def project(tmp_path):
    reg = Registry()
    reg.add(forge_3d.modality)
    set_registry(reg)
    proj = Project.create(tmp_path / "proj", name="Skel")
    g.save_mesh(_figure(), tmp_path / "hero.glb")
    proj.store.import_file(tmp_path / "hero.glb", slug="hero", modality="3d", kind="mesh")
    yield proj
    set_registry(None)


def _gltf(path):
    b = path.read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + n])


def test_skeleton_pipeline_animate_reskin_and_export(project, tmp_path):
    av = run_pipeline(project, "3d.skeleton", {"input_asset": "hero", "template": "humanoid"})
    assert av.kind == "animation" and av.extra["animation"]["type"] == "skeleton"
    vdir = project.store.version_dir(av.slug, av.version)
    doc = _gltf(vdir / "animated.glb")
    assert doc["skins"] and len(doc["skins"][0]["joints"]) == len(sk.HUMANOID)
    prim = next(p for m in doc["meshes"] for p in m["primitives"] if "JOINTS_0" in p["attributes"])
    assert "WEIGHTS_0" in prim["attributes"]

    clip = {"name": "wave", "duration": 1.0, "loop": True, "tracks": [
        {"part": "LeftArm", "keys": [{"t": 0, "rotation_deg": [0, 0, 0]}, {"t": 1, "rotation_deg": [0, 0, 60]}]}]}
    animated = run_pipeline(project, "3d.animate", {"input_asset": av.ref, "clip": clip}, asset=av.slug)
    assert _gltf(project.store.version_dir(animated.slug, animated.version) / "animated.glb")["animations"]

    # edited joints (moved hand) re-skin the same asset and keep the clip
    joints = [{"name": p["name"], "parent": p["parent"], "position": p["pivot"], "deform": p["deform"]}
              for p in animated.extra["animation"]["parts"] if p["bone"]]
    for j in joints:
        if j["name"] == "LeftHand":
            j["position"] = [j["position"][0] + 0.02, j["position"][1], j["position"][2]]
    reskinned = run_pipeline(project, "3d.skeleton", {"input_asset": animated.ref, "joints": joints}, asset=av.slug)
    assert reskinned.slug == av.slug
    assert [c["name"] for c in reskinned.extra["animation"]["clips"]] == ["wave"]

    out = forge_3d.modality.exporters
    exporter = next(e for e in out if e.id == "3d.animation_glb")
    from forge_core.pipeline import ExportContext

    ctx = ExportContext(project=project, asset=reskinned, out_dir=tmp_path)
    path = exporter.run(ctx, exporter.Params())[0]
    assert _gltf(path)["skins"]
