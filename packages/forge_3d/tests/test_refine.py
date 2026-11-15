import numpy as np
import pytest
import trimesh

from forge_3d.geometry import refine as rf
from forge_3d.geometry.texture import View


def _bumpy_sphere(subdiv=5, bump=0.25):
    """Sphere with a bump (a "nose") at +Z: what the regenerated piece adds."""
    s = trimesh.creation.icosphere(subdivisions=subdiv)
    v = s.vertices
    v = v * (1 + bump * np.exp(-((v[:, 0]) ** 2 + (v[:, 1]) ** 2) / 0.02)[:, None] * (v[:, 2] > 0)[:, None])
    return trimesh.Trimesh(v, s.faces)


def test_region_selection_helpers():
    s = trimesh.creation.icosphere(subdivisions=3)
    near = rf.faces_near_points(s, [[0, 0, 1]], 0.3)
    assert near.any() and (s.triangles_center[near][:, 2] > 0.8).all()
    grown = rf.dilate_faces(s, near, 2)
    assert grown.sum() > near.sum() and (grown | near).sum() == grown.sum()
    view = View(0.0, 0.0, 100.0, 128.0, 128.0, np.zeros(3))
    rect = rf.crop_rect(s, near, view, (256, 256), margin=0.0)
    assert rect[0] < 128 < rect[2] and rect[1] < 128 < rect[3]
    assert rf.visible_area(s, near, view) > rf.visible_area(s, near, View(180.0, 0.0, 100.0, 128.0, 128.0, np.zeros(3)))


def test_place_part_recovers_position_and_scale():
    base = trimesh.creation.icosphere(subdivisions=4)
    base.apply_translation((0.3, 1.0, 0.0))
    # the "generated" piece: same shape, normalized differently (generators output their own frame)
    part = trimesh.creation.icosphere(subdivisions=4)
    part.apply_scale(0.37)
    part.apply_translation((5, -2, 1))
    view = View(0.0, 0.0, 100.0, 200.0, 200.0, np.array([0.3, 1.0, 0.0]))
    # where the subject sits in the image: the base's projected bounding box
    xy, _ = view.project(base.vertices)
    rect = (*xy.min(axis=0), *xy.max(axis=0))
    placed, info = rf.place_part(part, view, rect, base)
    assert np.allclose(placed.bounds.mean(axis=0), base.bounds.mean(axis=0), atol=0.03)
    assert placed.extents == pytest.approx(base.extents, rel=0.05)
    assert info.icp_cost < 1e-3


def test_merge_region_takes_the_part_inside_only():
    base = trimesh.creation.icosphere(subdivisions=3)            # coarse, no nose
    part = _bumpy_sphere()                                          # detailed, with a nose
    part.apply_translation((0.0, 0.0, 0.0))
    region = rf.faces_near_points(base, [[0, 0, 1]], 0.6)          # the front
    merged = rf.merge_region(base, region, part, depth=8, base_samples=60_000)
    assert merged.is_watertight
    tip = merged.vertices[:, 2].max()
    assert tip > 1.15                                               # the nose came in
    # elsewhere the base is kept (the back stays at radius 1)
    back = merged.vertices[merged.vertices[:, 2] < -0.5]
    assert np.allclose(np.linalg.norm(back, axis=1), 1.0, atol=0.03)
