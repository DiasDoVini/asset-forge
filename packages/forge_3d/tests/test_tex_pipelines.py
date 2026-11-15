import numpy as np
import pytest
import trimesh
from forge_core import Project, run_pipeline
from forge_core.pipeline import Registry, set_registry
from PIL import Image

import forge_3d
from forge_3d import geometry as g
from forge_3d.geometry.texture import View, depth_map, texture_image


@pytest.fixture
def project(tmp_path):
    reg = Registry()
    reg.add(forge_3d.modality)
    set_registry(reg)
    proj = Project.create(tmp_path / "proj", name="Tex")
    # a "generated" asset: an L-shaped mesh plus the image it was made from (front view: left half red, right half blue)
    a = trimesh.creation.box((2.0, 0.6, 0.6))
    b = trimesh.creation.box((0.6, 1.4, 0.6), transform=trimesh.transformations.translation_matrix((0.7, 1.0, 0)))
    mesh = g.solidify(trimesh.util.concatenate([a, b]))[0]
    g.save_mesh(mesh, tmp_path / "model.glb")
    av = proj.store.import_file(tmp_path / "model.glb", slug="thing", modality="3d", kind="mesh")
    view = View(0.0, 0.0, 120.0, 256.0, 256.0, mesh.bounds.mean(axis=0))
    sil = np.isfinite(depth_map(mesh, view, (512, 512)))
    img = np.full((512, 512, 3), 255, np.uint8)
    img[sil] = (40, 40, 220)
    img[sil & (np.arange(512)[None, :] < 256)] = (220, 40, 40)
    vdir = proj.store.version_dir(av.slug, av.version)
    Image.fromarray(img).save(vdir / "input.png")
    av.files.append(type(av.files[0])(path="input.png", role="source", mime="image/png"))
    proj.store.write_meta(av)
    yield proj
    set_registry(None)


def test_unwrap_mesh_asset(project):
    av = run_pipeline(project, "3d.unwrap", {"input_asset": "thing", "resolution": 512})
    roles = {f.role for f in av.files}
    assert {"primary", "texture"} <= roles
    mesh = g.load_mesh(project.store.files_of(av, "primary")[0])
    assert texture_image(mesh) is not None


def test_texture_ai_projects_source_image(project):
    av = run_pipeline(project, "3d.texture_ai", {"input_asset": "thing", "views": [], "resolution": 512})
    info = av.extra["texture_ai"]
    assert info["source_view"]["match"] > 0.9
    assert abs(((info["source_view"]["yaw"] + 180) % 360) - 180) <= 6
    mesh = g.load_mesh(project.store.files_of(av, "primary")[0])
    tex = np.asarray(texture_image(mesh).convert("RGB"))
    from forge_3d.geometry.texture import rasterize_uv, texel_points

    texels = rasterize_uv(mesh, (tex.shape[1], tex.shape[0]))
    rows, cols, pts, normals = texel_points(mesh, texels)
    front = normals[:, 2] > 0.9
    left = front & (pts[:, 0] < -0.5)
    right = front & (pts[:, 0] > 0.3)
    assert tex[rows[left], cols[left], 0].mean() > 170   # red on the left of the front
    assert tex[rows[right], cols[right], 2].mean() > 170  # blue on the right


def test_texture_ai_on_animation_and_paint(project, tmp_path):
    rig = run_pipeline(project, "3d.rig", {"input_asset": "thing", "parts": [
        {"name": "post", "region": {"shape": "box", "size": [0.8, 1.6, 0.8], "matrix": [[1, 0, 0, 0.7], [0, 1, 0, 1.0], [0, 0, 1, 0], [0, 0, 0, 1]]}}]})
    textured = run_pipeline(project, "3d.texture_ai", {"input_asset": rig.ref, "views": []})
    assert textured.kind == "animation"
    vdir = project.store.version_dir(textured.slug, textured.version)
    for part in ("body", "post"):
        assert texture_image(g.load_mesh(vdir / "parts" / f"{part}.glb")) is not None
    # paint: replace the post's texture with solid green
    post_tex = texture_image(g.load_mesh(vdir / "parts" / "post.glb"))
    Image.new("RGBA", post_tex.size, (0, 200, 0, 255)).save(tmp_path / "green.png")
    painted = run_pipeline(project, "3d.apply_texture", {"input_asset": textured.ref, "textures": {"post": str(tmp_path / "green.png")}})
    pdir = project.store.version_dir(painted.slug, painted.version)
    img = np.asarray(texture_image(g.load_mesh(pdir / "parts" / "post.glb")).convert("RGB"))
    assert img[..., 1].mean() > 190
    with pytest.raises(Exception, match="unknown meshes"):
        run_pipeline(project, "3d.apply_texture", {"input_asset": painted.ref, "textures": {"nope": str(tmp_path / "green.png")}})


def test_texture_ai_without_source_image_explains(project, tmp_path):
    g.save_mesh(trimesh.creation.box(), tmp_path / "plain.glb")
    project.store.import_file(tmp_path / "plain.glb", slug="plain", modality="3d", kind="mesh")
    with pytest.raises(Exception, match="no source image"):
        run_pipeline(project, "3d.texture_ai", {"input_asset": "plain", "views": []})


def test_left_view_looks_at_the_objects_left_side():
    # objects face +Z, so their left side is +X: the "left" camera sits on +X (same as Hunyuan3D-2mv's "left")
    from forge_3d.geometry.texture import View
    from forge_3d.tex_pipelines import VIEW_YAW

    _, _, fwd = View(VIEW_YAW["left"], 0, 1, 0, 0, np.zeros(3)).basis()
    assert fwd[0] < -0.99  # camera -> scene points to -X, i.e. the camera is on +X


def _back_view(project, color, path):
    mesh = g.load_mesh(project.store.files_of(project.store.get("thing"), "primary")[0])
    view = View(180.0, 0.0, 120.0, 256.0, 256.0, mesh.bounds.mean(axis=0))
    sil = np.isfinite(depth_map(mesh, view, (512, 512)))
    img = np.full((512, 512, 3), 255, np.uint8)
    img[sil] = color
    Image.fromarray(img).save(path)
    return mesh


def _back_texels(av, project):
    from forge_3d.geometry.texture import rasterize_uv, texel_points

    mesh = g.load_mesh(project.store.files_of(av, "primary")[0])
    tex = np.asarray(texture_image(mesh).convert("RGB"))
    rows, cols, _, normals = texel_points(mesh, rasterize_uv(mesh, (tex.shape[1], tex.shape[0])))
    return tex[rows[normals[:, 2] < -0.9], cols[normals[:, 2] < -0.9]]


def test_texture_ai_uses_real_side_views_instead_of_generating(project, tmp_path, monkeypatch):
    from forge_3d.tex_pipelines import TextureAI

    def no_generation(*a, **k):
        raise AssertionError("must not generate a view that was given")

    monkeypatch.setattr(TextureAI, "_generate_views", staticmethod(no_generation))
    _back_view(project, (30, 200, 30), tmp_path / "back.png")
    av = run_pipeline(project, "3d.texture_ai", {"input_asset": "thing", "views": ["back"], "resolution": 512,
                                                 "side_views": {"back": str(tmp_path / "back.png")}})
    back = _back_texels(av, project)
    assert back[:, 1].mean() > 150 and back[:, 0].mean() < 100  # green on the back
    assert av.extra["texture_ai"]["real_views"] == {"back": "back.png"}


def test_texture_ai_picks_up_multiview_source_views(project, monkeypatch):
    from forge_3d.tex_pipelines import TextureAI

    monkeypatch.setattr(TextureAI, "_generate_views", staticmethod(lambda *a, **k: []))
    av = project.store.get("thing")
    vdir = project.store.version_dir(av.slug, av.version)
    _back_view(project, (30, 200, 30), vdir / "view_back.png")
    av.files.append(type(av.files[0])(path="view_back.png", role="source", mime="image/png"))
    av.pipeline = "3d.image_to_3d"
    project.store.write_meta(av)
    out = run_pipeline(project, "3d.texture_ai", {"input_asset": "thing", "views": [], "resolution": 512})
    assert out.extra["texture_ai"]["real_views"] == {"back": "view_back.png"}
    assert _back_texels(out, project)[:, 1].mean() > 150


def test_texture_ai_writes_the_four_pbr_maps(project):
    av = run_pipeline(project, "3d.texture_ai", {"input_asset": "thing", "views": [], "resolution": 512})
    names = {p.name for p in project.store.files_of(av, "texture")}
    assert {"basecolor.png", "metallic.png", "roughness.png"} <= names   # no dense surface here -> no normal.png
    from forge_3d.geometry import pbr

    maps = pbr.maps_of(g.load_mesh(project.store.files_of(av, "primary")[0]))
    assert maps.metallic is not None and maps.roughness is not None
