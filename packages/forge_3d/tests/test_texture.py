import numpy as np
import pytest
import trimesh
from PIL import Image

from forge_3d.geometry.texture import (View, align_view, bake_source_colors, depth_map, project_images, rasterize_uv,
                                       subject_mask, texel_points, unwrap, with_texture)


def colored_sphere():
    m = trimesh.creation.icosphere(subdivisions=3)
    colors = np.where(m.vertices[:, [0]] > 0, [[220, 30, 30, 255]], [[30, 30, 220, 255]]).astype(np.uint8)
    m.visual.vertex_colors = colors
    return m


def test_unwrap_and_bake_vertex_colors():
    m = colored_sphere()
    un = unwrap(m, resolution=256)
    assert max(un.size) == 256 and len(un.mesh.faces) == len(m.faces)
    assert un.mesh.visual.uv.min() >= 0 and un.mesh.visual.uv.max() <= 1
    texels = rasterize_uv(un.mesh, un.size)
    assert texels.mask.mean() > 0.4  # decent packing
    img = bake_source_colors(m, un, texels)
    rows, cols, pts, _ = texel_points(un.mesh, texels)
    right = pts[:, 0] > 0.3
    left = pts[:, 0] < -0.3
    assert img[rows[right], cols[right], 0].mean() > 180  # red where x > 0
    assert img[rows[left], cols[left], 2].mean() > 180    # blue where x < 0


def test_texture_roundtrip_through_glb(tmp_path):
    m = colored_sphere()
    un = unwrap(m, resolution=128)
    img = bake_source_colors(m, un, rasterize_uv(un.mesh, un.size))
    textured = with_texture(un.mesh, img)
    textured.export(tmp_path / "t.glb")
    back = trimesh.load(tmp_path / "t.glb", force="mesh")
    assert back.visual.kind == "texture"


def test_align_view_recovers_camera():
    # an asymmetric "L" so yaw is identifiable
    a = trimesh.creation.box((2.0, 0.4, 0.4))
    b = trimesh.creation.box((0.4, 1.2, 0.4), transform=trimesh.transformations.translation_matrix((0.8, 0.8, 0)))
    c = trimesh.creation.box((0.4, 0.4, 1.0), transform=trimesh.transformations.translation_matrix((-0.8, 0, 0.5)))
    m = trimesh.util.concatenate([a, b, c])
    truth = View(35.0, 15.0, 80.0, 128.0, 128.0, m.bounds.mean(axis=0))
    mask = np.isfinite(depth_map(m, truth, (256, 256)))
    view, iou = align_view(m, mask, yaws=range(0, 360, 15), pitches=(0, 15, 30))
    assert iou > 0.9
    assert abs(((view.yaw - 35 + 180) % 360) - 180) <= 6 and view.pitch == 15


def test_project_front_image_colors_the_front():
    m = trimesh.creation.box((1, 1, 1))
    un = unwrap(m, resolution=128)
    texels = rasterize_uv(un.mesh, un.size)
    base = np.full((un.size[1], un.size[0], 4), 128, np.uint8)
    view = View(0.0, 0.0, 100.0, 64.0, 64.0, m.bounds.mean(axis=0))
    front = Image.new("RGB", (128, 128), (255, 255, 255))
    front.paste((0, 200, 0), (14, 14, 114, 114))  # the box face seen from +Z
    out = project_images([un.mesh], [texels], [(front, view, 1.0)], [base])[0]
    rows, cols, pts, normals = texel_points(un.mesh, texels)
    facing = normals[:, 2] > 0.9
    assert out[rows[facing], cols[facing], 1].mean() > 180  # front face is green
    back = normals[:, 2] < -0.9
    assert out[rows[back], cols[back], 1].mean() > 150  # unseen faces filled from nearest seen colors


def test_subject_mask():
    img = Image.new("RGB", (32, 32), (255, 255, 255))
    img.paste((10, 10, 10), (8, 8, 24, 24))
    m = subject_mask(img)
    assert m[16, 16] and not m[2, 2]


def test_transfer_texture_from_merged_atlas_to_part():
    from forge_3d.geometry.texture import transfer_texture

    a = trimesh.creation.box((1, 1, 1))
    b = trimesh.creation.box((1, 1, 1), transform=trimesh.transformations.translation_matrix((3, 0, 0)))
    a.visual.vertex_colors = [200, 20, 20, 255]
    b.visual.vertex_colors = [20, 20, 200, 255]
    merged = trimesh.util.concatenate([a, b])
    un = unwrap(merged, resolution=256)
    merged_img = Image.fromarray(bake_source_colors(merged, un, rasterize_uv(un.mesh, un.size)), "RGBA")
    # part b with its own atlas
    part = unwrap(b, resolution=128)
    part_texels = rasterize_uv(part.mesh, part.size)
    base = np.zeros((part.size[1], part.size[0], 4), np.uint8)
    out = transfer_texture(part.mesh, part_texels, base, un.mesh, merged_img, face_offset=len(a.faces))
    covered = out[part_texels.mask]
    assert covered[:, 2].mean() > 170 and covered[:, 0].mean() < 60  # part b is blue, not red


def test_hunyuan_paint_graph():
    from forge_3d.generation import DEFAULT_MODELS, hunyuan_paint

    graph, nodes = hunyuan_paint("forge/m.glb", "forge/r.png", 5, models=DEFAULT_MODELS)
    g = graph.to_dict()
    types = [n["class_type"] for n in g.values()]
    for t in ("ForgeLoad3DFile", "Get3DComponents", "Hunyuan3DPaintModelLoader", "Hunyuan3DPaintConditioning",
              "KSampler", "Hunyuan3DPaintSplitLatent", "Hunyuan3DBakeMultiView"):
        assert t in types
    assert types.count("VAEDecode") == 2
    ks = next(n for n in g.values() if n["class_type"] == "KSampler")["inputs"]
    assert ks["scheduler"] == "sgm_uniform" and ks["cfg"] == 6.0 and ks["seed"] == 5
    assert {g[nodes[k]]["class_type"] for k in nodes} == {"SaveImage"}


def test_strip_outline_removes_sticker_border_only():
    from PIL import ImageDraw

    from forge_3d.geometry.texture import strip_outline

    plain = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(plain).ellipse((50, 50, 150, 150), fill=(200, 40, 40))
    _, removed = strip_outline(plain)
    assert removed == 0
    sticker = Image.new("RGB", (200, 200), "white")
    d = ImageDraw.Draw(sticker)
    d.ellipse((40, 40, 160, 160), fill=(180, 160, 230))
    d.ellipse((50, 50, 150, 150), fill=(200, 40, 40))
    clean, removed = strip_outline(sticker)
    assert 8 <= removed <= 12
    px = np.asarray(clean.convert("RGB"))
    assert (px[100, 45] > 240).all()      # former halo is background now
    assert px[100, 100, 0] > 180         # subject kept


def test_sampling_mask_keeps_thin_parts():
    from forge_3d.geometry.texture import sampling_mask

    img = Image.new("RGB", (1024, 1024), "white")
    img.paste(Image.new("RGB", (14, 600), (30, 30, 200)), (500, 200))      # a 14 px staff
    img.paste(Image.new("RGB", (300, 300), (200, 30, 30)), (100, 100))     # a big block
    m = sampling_mask(img)
    assert m[500:800, 504:510].mean() > 0.9           # the staff's middle survives
    assert not m[100:110, 100:400].any()               # the big block still loses its edge (bleed)
    assert m[250, 250]


def test_local_align_moves_a_misplaced_head():
    from forge_3d.geometry.texture import View, local_align, silhouette

    body = trimesh.creation.box((0.6, 1.2, 0.4))
    head = trimesh.creation.icosphere(subdivisions=2, radius=0.2)
    head.apply_translation((0, 0.85, 0))
    mesh = trimesh.util.concatenate([body, head])
    view = View(0.0, 0.0, 200.0, 256.0, 256.0, mesh.bounds.mean(axis=0))
    sil = silhouette(mesh, view, (512, 512))
    img = np.full((512, 512, 3), 255, np.uint8)
    img[sil] = (90, 90, 90)
    # the drawing's head sits 10 px to the right of the model's
    ys, xs = np.nonzero(sil[:150])
    img[:150][sil[:150]] = 255
    img[ys, np.clip(xs + 10, 0, 511)] = (90, 90, 90)
    warped, shift = local_align(mesh, view, Image.fromarray(img))
    assert shift >= 6
    out = np.asarray(warped.convert("L")) < 200
    before = (np.asarray(Image.fromarray(img).convert("L")) < 200)[:150]
    iou = lambda a, b: (a & b).sum() / max((a | b).sum(), 1)
    assert iou(out[:150], sil[:150]) > iou(before, sil[:150]) + 0.1
