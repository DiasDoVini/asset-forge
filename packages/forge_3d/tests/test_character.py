"""Character rigging with library animations: glTF animation reading, retargeting, skin weights at seams, the
3d.character_rig / 3d.retarget pipelines (no GPU) and the animation library lookup."""
import numpy as np
import pytest
import trimesh
from forge_core import Project, run_pipeline
from forge_core.pipeline import Registry, set_registry

import forge_3d
from forge_3d import animlib
from forge_3d import geometry as g
from forge_3d.character import square_up
from forge_3d.geometry import skeleton as sk
from forge_3d.geometry.anim import Clip, Key, Part, Rig, Skin, Track, export_glb, world_matrices
from forge_3d.geometry.gltf_anim import AnimationFile
from forge_3d.geometry.retarget import fit_character, retarget

from .test_skeleton import _box, _figure


def _tpose_figure() -> trimesh.Trimesh:
    parts = [_box((0.36, 0.55, 0.22), (0, 1.22, 0)), _box((0.30, 0.20, 0.20), (0, 0.90, 0)),
             trimesh.creation.icosphere(radius=0.12).apply_translation((0, 1.66, 0)), _box((0.08, 0.08, 0.08), (0, 1.52, 0))]
    for sign in (1, -1):
        parts += [_box((0.62, 0.09, 0.09), (sign * 0.50, 1.42, 0)), _box((0.13, 0.82, 0.13), (sign * 0.10, 0.41, 0)),
                  _box((0.11, 0.06, 0.24), (sign * 0.10, 0.03, 0.05))]
    m = trimesh.util.concatenate(parts)
    return trimesh.Trimesh(m.vertices, m.faces).subdivide().subdivide()


def _clips() -> list[Clip]:
    wave = Clip(name="Wave_Loop", duration=1.0, loop=True, tracks=[
        Track(part="upperarm_l", keys=[Key(t=0), Key(t=0.5, rotation_deg=(0, 0, 50)), Key(t=1.0)]),
        Track(part="lowerarm_l", keys=[Key(t=0), Key(t=0.5, rotation_deg=(0, 40, 0)), Key(t=1.0)])])
    walk = Clip(name="Walk_Loop", duration=1.0, loop=True, tracks=[
        Track(part="pelvis", keys=[Key(t=0), Key(t=0.5, position=(0, -0.05, 0.1)), Key(t=1.0)]),
        Track(part="thigh_l", keys=[Key(t=0, rotation_deg=(-25, 0, 0)), Key(t=0.5, rotation_deg=(25, 0, 0)),
                                    Key(t=1.0, rotation_deg=(-25, 0, 0))])])
    return [wave, walk]


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A tiny animation library in the Unreal/UAL layout: a T-posed figure with two clips."""
    mesh = _tpose_figure()
    skel = fit_character(mesh)
    rig = Rig(parts=[Part(name=j.name, pivot=tuple(j.position), parent=j.parent, source="bone", deform=j.deform)
                     for j in skel.joints], skin=Skin())
    joints, weights = sk.skin_weights(mesh, skel, method="heat")
    order = {b.name: i for i, b in enumerate(rig.bones)}
    remap = np.array([order[j.name] for j in skel.joints], dtype=np.uint16)
    folder = tmp_path / "lib"
    folder.mkdir()
    path = export_glb(rig, {"skin": mesh}, _clips(), folder / "TestLib.glb", skin=(remap[joints], weights))
    monkeypatch.setattr(animlib, "folders", lambda: [folder])     # not the real installation's library
    return path, rig


def test_gltf_animation_roundtrip(library):
    path, rig = library
    f = AnimationFile(path)
    assert f.clip_names == ["Wave_Loop", "Walk_Loop"]
    idx = f.skeleton.index()
    clip = f.clip("Walk_Loop")
    src = next(c for c in _clips() if c.name == "Walk_Loop")
    for k in (0, len(clip.times) // 2, len(clip.times) - 1):
        world = world_matrices(rig, src, float(clip.times[k]))
        for name in ("pelvis", "thigh_l", "calf_l", "foot_l"):
            assert np.allclose(clip.world[k, idx[name], :3, 3], world[name][:3, 3], atol=1e-4)


def test_retarget_drives_an_a_posed_character(library):
    path, rig = library
    f = AnimationFile(path)
    target = fit_character(_figure())                     # arms down at 35°: the library is in T-pose
    tr = Rig(parts=[Part(name=j.name, pivot=tuple(j.position), parent=j.parent, source="bone") for j in target.joints], skin=Skin())
    for name in ("Wave_Loop", "Walk_Loop"):
        src = f.clip(name)
        clip = retarget(f.skeleton, src, target)
        idx = f.skeleton.index()
        for k in range(0, len(src.times), 5):
            w = world_matrices(tr, clip, float(src.times[k]))
            for a, b in (("upperarm_l", "lowerarm_l"), ("lowerarm_l", "hand_l"), ("thigh_l", "calf_l")):
                ds = src.world[k, idx[b], :3, 3] - src.world[k, idx[a], :3, 3]
                dt = w[b][:3, 3] - w[a][:3, 3]
                assert np.dot(ds, dt) / (np.linalg.norm(ds) * np.linalg.norm(dt)) > 0.995, (name, k, a)
    # the hips move by the leg-length ratio
    walk = retarget(f.skeleton, f.clip("Walk_Loop"), target)
    pelvis = next(t for t in walk.tracks if t.part == "pelvis")
    assert max(abs(k.position[2]) for k in pelvis.keys) > 0.05


def test_retarget_onto_mixamo_names(library):
    path, _ = library
    f = AnimationFile(path)
    target = sk.fit_humanoid(_figure())
    clip = retarget(f.skeleton, f.clip("Wave_Loop"), target)
    assert {t.part for t in clip.tracks} >= {"LeftArm", "LeftForeArm"}


def test_skin_weights_match_across_uv_seams():
    cyl = trimesh.creation.cylinder(radius=0.1, height=2.0, sections=24)
    cyl = trimesh.Trimesh(cyl.vertices, cyl.faces).subdivide().subdivide()
    split = cyl.copy()
    split.unmerge_vertices()                                # every face has its own vertices, as at UV seams
    skel = sk.Skeleton([sk.Joint("Root", None, np.array([0, 0, -1.0])), sk.Joint("Mid", "Root", np.array([0, 0, 0.0])),
                        sk.Joint("Tip_End", "Mid", np.array([0, 0, 1.0]), deform=False)])
    J, W = sk.skin_weights(split, skel, method="heat")
    mid = np.where(J == 1, W, 0).sum(axis=1)
    key = np.round(split.vertices, 6)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    for group in range(inv.max() + 1):
        vals = mid[inv.ravel() == group]
        assert np.ptp(vals) < 1e-6


def test_square_up_turns_a_rotated_character_back():
    m = _tpose_figure()
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(25), [0, 1, 0]))
    yaw = square_up(m)
    assert abs(abs(yaw) - 25) < 2
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(yaw), [0, 1, 0]))
    assert square_up(m) == 0.0


def test_library_lookup(library):
    assert [f.id for f in animlib.index()] == ["TestLib"]
    assert animlib.expand(["basic"], ["Wave_Loop"]) == ["Walk_Loop", "Wave_Loop"]
    assert animlib.resolve(["TestLib/Wave_Loop"])[0][1] == "Wave_Loop"
    with pytest.raises(ValueError, match="Available"):
        animlib.resolve(["Backflip"])


@pytest.fixture
def project(tmp_path, library):
    reg = Registry()
    reg.add(forge_3d.modality)
    set_registry(reg)
    proj = Project.create(tmp_path / "proj", name="Char")
    m = _figure()
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(20), [0, 1, 0]))
    g.save_mesh(m, tmp_path / "hero.glb")
    proj.store.import_file(tmp_path / "hero.glb", slug="hero", modality="3d", kind="mesh")
    yield proj
    set_registry(None)


def test_character_rig_and_retarget_pipelines(project):
    av = run_pipeline(project, "3d.character_rig", {"input_asset": "hero", "animations": {"sets": [], "names": ["Wave_Loop"]},
                                                    "rig": {"height": 1.8}})
    assert av.kind == "animation" and av.extra["animation"]["type"] == "skeleton"
    info = av.extra["character"]
    assert info["skeleton"] == "humanoid-unreal" and info["deformation"]["grade"] in ("ok", "warn", "bad")
    names = {f.name for f in project.store.files_of(av)}
    assert {"animated.glb", "poses.png", "deformation.json", "rig.json"} <= names
    assert [c["name"] for c in av.extra["animation"]["clips"]] == ["Wave_Loop"]
    bones = {p["name"] for p in av.extra["animation"]["parts"]}
    assert {"root", "pelvis", "upperarm_l", "hand_leaf_l"} <= bones

    more = run_pipeline(project, "3d.retarget", {"input_asset": av.ref, "animations": {"names": ["Walk_Loop"]}})
    assert more.slug == av.slug
    assert [c["name"] for c in more.extra["animation"]["clips"]] == ["Wave_Loop", "Walk_Loop"]
    with pytest.raises(ValueError, match="unknown animation"):
        run_pipeline(project, "3d.retarget", {"input_asset": av.ref, "animations": {"names": ["Backflip"]}})
