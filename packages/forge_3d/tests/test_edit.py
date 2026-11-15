import numpy as np
import pytest
import trimesh

from forge_3d import geometry as g
from forge_3d.geometry.edit import (CutOp, MeshOp, MirrorOp, PrimitiveOp, TextOp, TransformOp, apply_ops, primitive,
                                    text_solid)


def box(size=(2, 2, 2)):
    return trimesh.creation.box(size)


def translate(x=0, y=0, z=0):
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m.tolist()


def test_transform():
    out = apply_ops(box(), [TransformOp(matrix=translate(5, 0, 0))])
    assert out.bounds[0][0] == pytest.approx(4)


def test_cut_keeps_back_side_and_caps():
    out = apply_ops(box(), [CutOp(point=(0, 0.5, 0), normal=(0, 1, 0), keep="back")])
    assert out.is_watertight
    assert out.bounds[1][1] == pytest.approx(0.5)
    assert out.volume == pytest.approx(2 * 2 * 1.5)
    front = apply_ops(box(), [CutOp(point=(0, 0.5, 0), normal=(0, 1, 0), keep="front")])
    assert front.bounds[0][1] == pytest.approx(0.5)


def test_cut_open_mesh_without_cap():
    sphere = trimesh.creation.icosphere(subdivisions=3)
    sphere.update_faces(np.arange(len(sphere.faces)) > 3)  # open
    out = apply_ops(sphere, [CutOp(normal=(1, 0, 0), keep="back")])
    assert out.bounds[1][0] <= 1e-6 and len(out.faces) > 0


@pytest.mark.parametrize("shape,volume", [("box", 1.0), ("cylinder", np.pi * 0.25), ("sphere", 4 / 3 * np.pi * 0.125), ("cone", np.pi * 0.25 / 3)])
def test_primitive_shapes(shape, volume):
    solid = primitive(shape, (1, 1, 1), segments=128)
    assert solid.volume() == pytest.approx(volume, rel=0.02)
    bb = np.asarray(solid.bounding_box())
    assert np.allclose(bb[3:] - bb[:3], 1, atol=1e-3)


def test_cylinder_axis_is_y():
    bb = np.asarray(primitive("cylinder", (1, 3, 1)).bounding_box())
    assert bb[4] - bb[1] == pytest.approx(3)


def test_primitive_subtract_drills_a_hole():
    hole = PrimitiveOp(shape="cylinder", mode="subtract", size=(0.5, 3, 0.5))
    out = apply_ops(box(), [hole])
    assert out.is_watertight
    assert out.volume == pytest.approx(8 - np.pi * 0.25 ** 2 * 2, rel=0.02)
    assert out.euler_number == 0  # a through hole = genus 1


def test_primitive_add_on_open_mesh_merges():
    sphere = trimesh.creation.icosphere()
    sphere.update_faces(np.arange(len(sphere.faces)) > 3)
    out = apply_ops(sphere, [PrimitiveOp(shape="box", size=(0.2, 0.2, 0.2), matrix=translate(3, 0, 0))])
    assert len(out.faces) == len(sphere.faces) + 12


def test_text_solid_and_engrave():
    t = text_solid("AB 8", height=1.0, depth=0.2)
    bb = np.asarray(t.bounding_box())
    assert bb[5] - bb[2] == pytest.approx(0.2)
    assert bb[4] - bb[1] == pytest.approx(1.0, rel=0.02)
    assert t.genus() >= 2  # counters of A, B, 8 are holes
    plate = box((6, 2, 0.5))
    engraved = apply_ops(plate, [TextOp(text="HI", mode="subtract", height=1, depth=0.2, matrix=translate(0, 0, 0.1))])
    assert engraved.volume < plate.volume
    embossed = apply_ops(plate, [TextOp(text="HI", mode="add", height=1, depth=0.2, matrix=translate(0, 0, 0.25))])
    assert embossed.volume > plate.volume and embossed.bounds[1][2] == pytest.approx(0.45)


def test_mirror_and_symmetrize():
    lopsided = trimesh.util.concatenate([box(), trimesh.creation.box((1, 1, 1), transform=trimesh.transformations.translation_matrix((1.5, 0, 0)))])
    flipped = apply_ops(lopsided, [MirrorOp(axis="x")])
    assert flipped.bounds[0][0] == pytest.approx(-2)
    solid = g.solidify(lopsided)[0]
    sym = apply_ops(solid, [MirrorOp(axis="x", mode="symmetrize_positive")])
    assert sym.bounds[0][0] == pytest.approx(-2) and sym.bounds[1][0] == pytest.approx(2)
    assert sym.is_watertight


def test_mesh_op_unions_another_asset():
    handle = trimesh.creation.torus(0.6, 0.15)
    out = apply_ops(box(), [MeshOp(asset="handle", matrix=translate(0, 1, 0))], load_mesh=lambda ref: handle)
    assert out.is_watertight and out.bounds[1][1] == pytest.approx(1.75, abs=0.02)  # torus lies in XY: radius 0.75 above y=1


def test_ops_parse_from_json():
    from pydantic import TypeAdapter
    from forge_3d.geometry.edit import EditOp

    ops = TypeAdapter(list[EditOp]).validate_python([
        {"op": "primitive", "shape": "sphere", "mode": "subtract", "size": [1, 1, 1]},
        {"op": "mirror", "axis": "z"},
    ])
    assert isinstance(ops[0], PrimitiveOp) and isinstance(ops[1], MirrorOp)
