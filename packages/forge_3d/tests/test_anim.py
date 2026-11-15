import json
import struct

import numpy as np
import pytest
import trimesh

from forge_3d.geometry.anim import (Attach, Clip, Key, Part, Rig, Track, export_glb, preset_keys, validate_clip,
                                    world_matrices)


def chest_rig():
    # body at the origin, lid hinged at the back-top edge (z=-0.5, y=1), a key prop lying on the floor
    rig = Rig(parts=[
        Part(name="body", file="body.glb", pivot=(0, 0, 0)),
        Part(name="lid", file="lid.glb", pivot=(0, 1, -0.5), parent="body"),
        Part(name="key", file="key.glb", pivot=(2, 0, 0)),
    ])
    meshes = {
        "body": trimesh.creation.box((1, 1, 1), transform=trimesh.transformations.translation_matrix((0, 0.5, 0))),
        "lid": trimesh.creation.box((1, 0.2, 1), transform=trimesh.transformations.translation_matrix((0, 1.1, 0))),
        "key": trimesh.creation.box((0.2, 0.05, 0.05), transform=trimesh.transformations.translation_matrix((2, 0, 0))),
    }
    return rig, meshes


def apply(m, p):
    return (m @ np.append(p, 1.0))[:3]


def test_rig_validation():
    with pytest.raises(ValueError, match="unknown parent"):
        Rig(parts=[Part(name="a", file="a.glb", parent="ghost")])
    with pytest.raises(ValueError, match="unique"):
        Rig(parts=[Part(name="a", file="a.glb"), Part(name="a", file="b.glb")])
    rig, _ = chest_rig()
    assert validate_clip(rig, Clip(name="x", duration=1, tracks=[Track(part="nope", keys=[Key(t=0)])]))
    assert validate_clip(rig, Clip(name="x", duration=1, attach=[Attach(part="lid", to="body", start=0)]))  # lid has a parent


def test_hinge_rotates_around_pivot_and_children_follow_parent():
    rig, _ = chest_rig()
    clip = Clip(name="open", duration=1, tracks=[
        Track(part="lid", keys=[Key(t=0), Key(t=1, rotation_deg=(-90, 0, 0))]),
        Track(part="body", keys=[Key(t=0), Key(t=1, position=(5, 0, 0))]),
    ])
    w = world_matrices(rig, clip, 1.0)
    lid = w["lid"]
    # the pivot (node origin) stays on the hinge, moved with the body
    assert np.allclose(apply(lid, (0, 0, 0)), (5, 1, -0.5), atol=1e-9)
    # the lid's front edge (world (0, 1, 0.5) at rest, local (0, 0, 1)) swings up to above the hinge
    assert np.allclose(apply(lid, (0, 0, 1)), (5, 2, -0.5), atol=1e-9)
    half = world_matrices(rig, clip, 0.5)["lid"]
    assert apply(half, (0, 0, 1))[1] == pytest.approx(1 + np.sin(np.radians(45)), abs=1e-6)


def test_attachment_follows_holder_then_stays_where_released():
    rig, _ = chest_rig()
    clip = Clip(name="grab", duration=3, tracks=[
        Track(part="body", keys=[Key(t=0), Key(t=1), Key(t=2, position=(0, 0, 4)), Key(t=3, position=(0, 0, 4))]),
    ], attach=[Attach(part="key", to="body", start=1, end=2)])
    from forge_3d.geometry.anim import attached_pose

    assert np.allclose(attached_pose(rig, clip, "key", 0.5)[:3, 3], (2, 0, 0))   # before: rest
    assert np.allclose(attached_pose(rig, clip, "key", 1.5)[:3, 3], (2, 0, 2))   # carried along +z
    assert np.allclose(attached_pose(rig, clip, "key", 2.5)[:3, 3], (2, 0, 4))   # released, stays


def test_presets():
    spin = preset_keys("spin", 2, "y", 1)  # one turn
    assert spin[-1].rotation_deg[1] == pytest.approx(360) and spin[-1].t == pytest.approx(2)
    assert max(abs(b.rotation_deg[1] - a.rotation_deg[1]) for a, b in zip(spin, spin[1:])) <= 90
    hinge = preset_keys("hinge", 1, "x", -100)
    assert min(k.rotation_deg[0] for k in hinge) == -100
    with pytest.raises(ValueError):
        preset_keys("moonwalk", 1)


def _read_glb(path):
    raw = path.read_bytes()
    n = struct.unpack("<I", raw[12:16])[0]
    return json.loads(raw[20:20 + n]), raw


def test_export_glb_has_hierarchy_and_animations(tmp_path):
    rig, meshes = chest_rig()
    clips = [
        Clip(name="open", duration=1, tracks=[Track(part="lid", keys=preset_keys("hinge", 1, "x", -100))]),
        Clip(name="grab", duration=2, tracks=[Track(part="body", keys=[Key(t=0), Key(t=2, position=(1, 0, 0))])],
             attach=[Attach(part="key", to="body", start=0.5)]),
    ]
    path = export_glb(rig, meshes, clips, tmp_path / "anim.glb")
    doc, raw = _read_glb(path)
    names = [n.get("name") for n in doc["nodes"]]
    assert {"body", "lid", "key"} <= set(names)
    body = doc["nodes"][names.index("body")]
    assert names.index("lid") in body.get("children", [])
    assert [a["name"] for a in doc["animations"]] == ["open", "grab"]
    for anim in doc["animations"]:
        for s in anim["samplers"]:
            inp = doc["accessors"][s["input"]]
            assert "min" in inp and "max" in inp
            assert doc["accessors"][s["output"]]["count"] == inp["count"]
    assert doc["buffers"][0]["byteLength"] % 4 == 0
    # the file is still loadable as geometry
    scene = trimesh.load(path)
    assert len(scene.geometry) == 3
    # lid mesh is stored relative to its pivot
    lid_bounds = scene.geometry["lid"].bounds
    assert lid_bounds[0][1] == pytest.approx(0.0, abs=1e-6)  # y 1.0..1.2 minus pivot y 1


def test_euler_convention_matches_threejs_intrinsic_xyz():
    from scipy.spatial.transform import Rotation

    a, b = np.radians(30), np.radians(50)
    rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    # three.js Euler 'XYZ' => R = Rx * Ry * Rz
    rig = Rig(parts=[Part(name="p", file="p.glb")])
    clip = Clip(name="c", duration=1, tracks=[Track(part="p", keys=[Key(t=0, rotation_deg=(30, 50, 0))])])
    m = world_matrices(rig, clip, 0.0)["p"][:3, :3]
    assert np.allclose(m, rx @ ry, atol=1e-9)
    assert np.allclose(Rotation.from_euler("XYZ", [30, 50, 0], degrees=True).as_matrix(), rx @ ry)
