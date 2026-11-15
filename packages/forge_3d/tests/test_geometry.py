import numpy as np
import pytest
import trimesh
from PIL import Image

from forge_3d import geometry as g


def textured_sphere(subdiv=4) -> trimesh.Trimesh:
    mesh = trimesh.creation.uv_sphere(radius=1.0, count=[64, 64])
    uv = mesh.vertices[:, :2] * 0.5 + 0.5  # planar projection is enough for a test texture
    img = np.zeros((64, 64, 4), dtype=np.uint8)
    img[:, :32] = [220, 40, 40, 255]  # left half red
    img[:, 32:] = [40, 40, 220, 255]  # right half blue
    material = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(img, "RGBA"))
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    return mesh


def test_stats_on_uv_seamed_sphere_is_watertight():
    s = g.mesh_stats(textured_sphere())
    assert s["watertight"] and s["boundary_edges"] == 0 and s["components"] == 1
    assert s["has_uv"] and s["has_texture"]


def test_simplify_keeps_uv_and_hits_target():
    mesh = textured_sphere()
    out = g.simplify(mesh, target_faces=500)
    assert len(out.faces) <= 500
    assert len(out.faces) > 400
    assert out.visual.kind == "texture" and len(out.visual.uv) == len(out.vertices)
    assert g.mesh_stats(out)["watertight"]


def test_simplify_ratio():
    mesh = trimesh.creation.icosphere(subdivisions=5)
    out = g.simplify(mesh, ratio=0.1)
    assert abs(len(out.faces) - len(mesh.faces) * 0.1) < len(mesh.faces) * 0.02


@pytest.mark.parametrize("mode", ["vertex", "palette_texture", "none"])
def test_lowpoly_modes(mode, tmp_path):
    mesh = textured_sphere()
    out, info = g.lowpoly(mesh, target_faces=200, color_mode=mode, max_colors=4)
    assert info["faces"] <= 200
    if mode != "none":
        assert 1 <= len(info["palette"]) <= 4
        # the red/blue split of the source texture must survive
        reds = [c for c in info["palette"] if int(c[1:3], 16) > 150]
        blues = [c for c in info["palette"] if int(c[5:7], 16) > 150]
        assert reds and blues
    path = g.save_mesh(out, tmp_path / "lp.glb")
    assert g.load_mesh(path).faces.shape[0] == len(out.faces)


def test_lowpoly_snaps_to_project_palette():
    out, info = g.lowpoly(textured_sphere(), target_faces=200, palette=["#ff0000", "#0000ff", "#00ff00"])
    assert set(info["palette"]) <= {"#ff0000", "#0000ff", "#00ff00"}


def test_repair_merges_overlapping_shells_and_fills_holes():
    a = trimesh.creation.box([1, 1, 1])
    b = trimesh.creation.box([1, 1, 1])
    b.apply_translation([0.5, 0, 0])
    holed = trimesh.creation.icosphere(subdivisions=3)
    holed.apply_translation([3, 0, 0])
    holed.update_faces(np.arange(len(holed.faces)) > 3)  # punch a hole
    broken = trimesh.util.concatenate([a, b, holed])
    out, actions = g.make_printable(broken, min_component_ratio=0)
    assert g.is_printable_solid(out)
    assert any("close_holes" in a for a in actions)
    assert g.mesh_stats(out)["components"] == 2  # merged boxes + sphere


def test_voxel_fallback_for_open_surface():
    # an open cylinder tube cannot be closed by hole filling of small holes; voxel path must kick in
    tube = trimesh.creation.cylinder(radius=1, height=2, sections=32)
    tube.update_faces(tube.face_normals[:, 2] > -0.5)  # remove bottom cap -> big hole
    out, how = g.solidify(tube, voxel_resolution=64)
    assert "voxel" in how
    assert out.is_watertight
    assert np.allclose(out.extents, tube.extents, atol=0.15)


def test_print_optimize_units_orientation_and_report():
    # an "L" shape in meters: best orientation lays the long flat side on the bed
    a = trimesh.creation.box([0.10, 0.02, 0.02])
    b = trimesh.creation.box([0.02, 0.02, 0.06])
    b.apply_translation([0.04, 0, 0.04])
    mesh = trimesh.util.concatenate([a, b])
    out, report = g.print_optimize(mesh, source_units="m")
    assert report["output"]["watertight"]
    assert out.bounds[0][2] == pytest.approx(0, abs=1e-6)
    assert max(out.extents) == pytest.approx(100, rel=0.02)  # mm
    assert report["orientation"]["overhang_ratio"] < 0.05
    assert report["fits_build_volume"]
    assert report["walls"]["median_mm"] > 15


def test_print_optimize_target_height_and_thin_wall_warning():
    plate = trimesh.creation.box([1.0, 1.0, 0.004])
    out, report = g.print_optimize(plate, source_units="m", orient=False, target_height_mm=0.5)
    assert out.extents[2] == pytest.approx(0.5, rel=0.05)
    assert any("thinner" in w for w in report["warnings"])


def test_split_fits_bed_with_pins(tmp_path):
    bar = trimesh.creation.box([500, 60, 40])  # mm, too long for a 220 bed
    res = g.split_for_print(bar, build_volume_mm=(220, 220, 250), pin_diameter_mm=5, pin_length_mm=12)
    assert res.info["all_fit"]
    assert len(res.parts) == 3
    assert res.pin_count >= 2 and res.pin is not None
    total = sum(p.volume for p in res.parts)
    hole_vol = res.pin_count * np.pi * (2.5 + 0.2) ** 2 * (12 + 0.4)
    assert total == pytest.approx(bar.volume - hole_vol, rel=0.01)
    for p in res.parts:
        assert p.is_watertight
    g.save_mesh(g.exploded_scene(res.assembled, res.cuts, res.pin, res.pin_placements), tmp_path / "exploded.glb")


def test_split_prefers_rotation_over_cutting():
    bar = trimesh.creation.box([150, 300, 20])  # fits a 320x200 bed only when rotated
    res = g.split_for_print(bar, build_volume_mm=(320, 200, 100))
    assert res.info["rotated_90_z"] and len(res.parts) == 1


def test_split_skips_pins_where_section_is_too_thin():
    thin = trimesh.creation.box([300, 4, 4])
    res = g.split_for_print(thin, build_volume_mm=(200, 200, 200))
    assert len(res.parts) == 2 and res.pin_count == 0
    assert res.info["sections_without_pins"] == 1


def test_glb_without_material_gets_matte_default(tmp_path):
    import json
    import struct

    box = trimesh.creation.box()
    box.visual.vertex_colors = [200, 30, 30, 255]
    path = g.save_mesh(box, tmp_path / "b.glb")
    raw = path.read_bytes()
    n = struct.unpack("<I", raw[12:16])[0]
    doc = json.loads(raw[20:20 + n])
    mat = doc["materials"][doc["meshes"][0]["primitives"][0]["material"]]
    assert mat["pbrMetallicRoughness"]["metallicFactor"] == 0.0
    assert len(g.load_mesh(path).faces) == 12  # still a valid file


def _with_fin(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Attach an extra triangle to an existing edge -> that edge has 3 faces (non-manifold)."""
    a, b = mesh.edges_unique[0]
    apex = mesh.vertices[[a, b]].mean(axis=0) + mesh.face_normals[0] * 0.5
    verts = np.vstack([mesh.vertices, apex])
    return trimesh.Trimesh(verts, np.vstack([mesh.faces, [a, b, len(verts) - 1]]), process=False)


def test_clean_skips_hole_filling_on_non_manifold_and_simplify_survives():
    sphere = trimesh.creation.icosphere(subdivisions=4)
    sphere.update_faces(np.arange(len(sphere.faces)) > 5)
    messy = _with_fin(sphere)
    out, actions = g.clean(messy)
    simplified = g.simplify(out, target_faces=300)  # used to crash the process after unsafe hole filling
    assert len(simplified.faces) <= 300
    assert all("access" not in a for a in actions)


def test_reconstruct_surface_gives_closed_mesh_of_same_size():
    sphere = trimesh.creation.icosphere(subdivisions=4)
    sphere.update_faces(np.arange(len(sphere.faces)) > 20)  # open + non-manifold fin
    messy = _with_fin(sphere)
    out, actions = g.reconstruct_surface(messy, depth=7)
    st = g.mesh_stats(out)
    assert st["boundary_edges"] == 0 and st["components"] == 1
    assert np.allclose(out.extents, 2.0, atol=0.15)
    assert "poisson" in actions[0]


def test_pin_holes_are_empty_where_pins_go_and_size_scales():
    bar = trimesh.creation.box([500, 80, 60])
    res = g.split_for_print(bar, build_volume_mm=(220, 220, 250), orient_parts=False)
    assert res.info["pin"]["diameter_mm"] == 10  # auto: big model -> 10 mm dowels
    assert len(res.pin_placements) == res.pin_count >= 2
    for point, cut in res.pin_placements:
        assert abs(point[cut.axis] - cut.offset) < 1e-6  # centered on the cut plane
        probe = np.array([point[:3] + np.eye(3)[cut.axis] * d for d in (-3, 3)])  # inside the hole on both sides
        for part in res.assembled:
            assert not part.contains(probe).any()
    small = g.split_for_print(trimesh.creation.box([150, 30, 30]), build_volume_mm=(100, 100, 100), orient_parts=False)
    assert small.info["pin"]["diameter_mm"] == 4


def test_exploded_scene_shows_pins_between_parts():
    bar = trimesh.creation.box([500, 80, 60])
    res = g.split_for_print(bar, build_volume_mm=(220, 220, 250), orient_parts=False)
    scene = g.exploded_scene(res.assembled, res.cuts, res.pin, res.pin_placements)
    names = list(scene.geometry)
    assert sum(n.startswith("pin_") for n in names) == res.pin_count
    assert sum(n.startswith("part_") for n in names) == len(res.parts)
    # parts were pulled apart along the cut axis
    xs = sorted(scene.geometry[n].bounds[0][0] for n in names if n.startswith("part_"))
    original = sorted(p.bounds[0][0] for p in res.assembled)
    assert xs[0] < original[0] and xs[-1] > original[-1]

def test_pins_sit_well_inside_wide_sections():
    block = trimesh.creation.box([400, 200, 200])
    res = g.split_for_print(block, build_volume_mm=(250, 250, 250), orient_parts=False)
    for point, cut in res.pin_placements:
        y, z = point[1], point[2]
        # at least 25% of the 200 mm section away from the outer walls
        assert min(100 - abs(y), 100 - abs(z)) >= 0.25 * 200 - 1


def test_glb_export_has_smooth_normals_with_hard_creases(tmp_path):
    import json
    import struct

    sphere = trimesh.creation.icosphere(subdivisions=3)
    box = trimesh.creation.box((1, 1, 1))
    for name, mesh in (("sphere", sphere), ("box", box)):
        g.save_mesh(mesh, tmp_path / f"{name}.glb")
        data = (tmp_path / f"{name}.glb").read_bytes()
        n = struct.unpack("<I", data[12:16])[0]
        doc = json.loads(data[20:20 + n])
        assert "NORMAL" in doc["meshes"][0]["primitives"][0]["attributes"]
    # the box keeps hard edges: every exported normal is an axis
    raw = trimesh.load(tmp_path / "box.glb", force="mesh", process=False)
    assert np.allclose(np.abs(raw.vertex_normals).max(axis=1), 1.0)
    # reloading welds the split vertices back: still a closed solid
    assert g.load_mesh(tmp_path / "box.glb").is_watertight
    assert g.load_mesh(tmp_path / "sphere.glb").is_watertight


def test_shade_smooth_keeps_low_poly_flat():
    from forge_3d.geometry.io import shade_smooth

    flat = trimesh.creation.icosphere(subdivisions=1)
    flat.unmerge_vertices()
    out = shade_smooth(flat)
    assert np.allclose(out.vertex_normals, np.repeat(out.face_normals, 3, axis=0), atol=1e-6)


def test_remove_depth_tilt_stands_thin_objects_back_in_the_image_plane():
    staff = trimesh.creation.cylinder(radius=0.02, height=1.0)  # along Z
    staff.apply_transform(trimesh.transformations.rotation_matrix(np.radians(-90 + 40), [1, 0, 0]))  # 40 deg toward the camera
    fixed, tilt = g.remove_depth_tilt(staff)
    assert tilt == pytest.approx(40, abs=2)
    assert fixed.extents[1] == pytest.approx(1.0, abs=0.02) and fixed.extents[2] < 0.06
    chunky = trimesh.creation.box((1.0, 2.0, 0.8))
    chunky.apply_transform(trimesh.transformations.rotation_matrix(np.radians(30), [1, 0, 0]))
    assert g.remove_depth_tilt(chunky)[1] == 0.0  # not elongated: left alone


def test_vertex_colors_survive_glb_roundtrip(tmp_path):
    box = trimesh.creation.box((1, 1, 1))
    box.visual.vertex_colors = np.tile([200, 30, 30, 255], (len(box.vertices), 1))
    g.save_mesh(box, tmp_path / "red.glb")
    back = g.load_mesh(tmp_path / "red.glb")
    assert back.visual.kind == "vertex"
    assert np.allclose(back.visual.vertex_colors[:, :3].mean(axis=0), [200, 30, 30], atol=2)


def _inward_share(mesh):
    """Fraction of faces whose normal points toward their piece's center (inside out)."""
    welded = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    welded.merge_vertices()
    labels = trimesh.graph.connected_component_labels(welded.face_adjacency, node_count=len(welded.faces))
    inward = 0
    for label in np.unique(labels):
        idx = labels == label
        center = welded.triangles_center[idx].mean(axis=0)
        inward += ((welded.triangles_center[idx] - center) * welded.face_normals[idx]).sum(axis=1).__lt__(0).sum()
    return inward / len(welded.faces)


def test_orient_outward_flips_only_inside_out_pieces_even_when_open():
    good = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
    bad = trimesh.creation.box((0.6, 0.6, 0.6))
    bad.apply_translation((1.5, 0, 0))
    bad.invert()
    open_bad = trimesh.creation.icosphere(subdivisions=3, radius=0.4)
    open_bad.update_faces(open_bad.triangles_center[:, 1] < 0.3)        # an open cup, like an AI surface
    open_bad.apply_translation((-1.5, 0, 0))
    open_bad.invert()
    mesh = trimesh.util.concatenate([good, bad, open_bad])
    out, flipped = g.orient_outward(mesh)
    assert flipped == 2 and _inward_share(out) < 0.01


def test_clean_keeps_a_consistent_open_surface_facing_out():
    # an open, consistently wound surface with a non-manifold flap: re-orienting/volume-sign fixes turned such
    # meshes inside out (the "peeled" look of generated models)
    cup = trimesh.creation.icosphere(subdivisions=4, radius=0.5)
    cup.update_faces(cup.triangles_center[:, 1] < 0.35)
    cup.remove_unreferenced_vertices()
    out, _ = g.clean(cup, min_component_ratio=0.02, max_hole_edges=0)
    assert _inward_share(out) < 0.01
