import numpy as np
import pytest
import trimesh
from forge_core import Project, run_pipeline
from forge_core.pipeline import Registry, set_registry
from PIL import Image

import forge_3d
from forge_3d import geometry as g
from forge_3d.gen_pipelines import TextTo3DParams, concept_prompt
from forge_3d.generation import DEFAULT_MODELS, has_alpha, hunyuan_image_to_mesh, prepare_subject


def test_prepare_subject_crops_centers_and_whitens():
    img = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    img.paste((255, 0, 0, 255), (300, 20, 380, 180))  # subject off-center at the right
    out = prepare_subject(img, size=256)
    a = np.asarray(out)
    assert out.size == (256, 256) and out.mode == "RGB"
    assert (a[0, 0] == 255).all()  # white corners
    red = np.nonzero((a[:, :, 0] > 200) & (a[:, :, 1] < 60))
    cy, cx = red[0].mean(), red[1].mean()
    assert abs(cx - 128) < 4 and abs(cy - 128) < 4  # centered
    assert red[0].max() - red[0].min() < 256 * 0.8  # keeps a margin


def test_prepare_subject_uses_mask():
    img = Image.new("RGB", (100, 100), (0, 0, 255))
    mask = Image.new("L", (100, 100), 0)
    mask.paste(255, (10, 10, 30, 30))
    out = np.asarray(prepare_subject(img, mask, size=64))
    assert (out[0, 0] == 255).all() and out[32, 32, 2] > 200


def test_has_alpha():
    assert not has_alpha(Image.new("RGB", (4, 4)))
    assert not has_alpha(Image.new("RGBA", (4, 4), (1, 2, 3, 255)))
    assert has_alpha(Image.new("RGBA", (4, 4), (1, 2, 3, 0)))


def test_hunyuan_graph_matches_template():
    graph, save_id = hunyuan_image_to_mesh("forge/x.png", 7, models=DEFAULT_MODELS)
    nodes = graph.to_dict()
    types = {n["class_type"] for n in nodes.values()}
    assert {"ImageOnlyCheckpointLoader", "CLIPVisionEncode", "Hunyuan3Dv2Conditioning", "EmptyLatentHunyuan3Dv2",
            "KSampler", "VAEDecodeHunyuan3D", "VoxelToMesh", "SaveGLB"} <= types
    assert nodes[save_id]["class_type"] == "SaveGLB"
    ks = next(n for n in nodes.values() if n["class_type"] == "KSampler")["inputs"]
    assert ks["seed"] == 7 and ks["positive"][1] == 0 and ks["negative"][1] == 1


def test_concept_prompt_appends_style_and_view():
    p = TextTo3DParams(prompt="a wooden treasure chest", asset_type="object")
    s = concept_prompt(p, {"prompt": "stylized hand-painted, chunky proportions"})
    assert s.startswith("a single game prop. a wooden treasure chest.")
    assert "white background" in s and "hand-painted" in s
    assert concept_prompt(TextTo3DParams(prompt="x", raw_prompt=True), {"prompt": "y"}) == "x"


@pytest.fixture
def project(tmp_path):
    reg = Registry()
    reg.add(forge_3d.modality)
    set_registry(reg)
    proj = Project.create(tmp_path / "proj", name="T")
    yield proj
    set_registry(None)


def test_glb_is_treated_as_y_up_for_printing(project, tmp_path):
    tower = trimesh.creation.box([0.02, 0.10, 0.02])  # tall along Y, like a glTF character
    src = tmp_path / "tower.glb"
    g.save_mesh(tower, src)
    project.store.import_file(src, slug="tower", modality="3d", kind="mesh")
    av = run_pipeline(project, "3d.print", {"input_asset": "tower", "orient": False, "check_walls": False})
    stl = g.load_mesh(project.store.files_of(av, "primary")[0])
    assert stl.extents[2] == pytest.approx(100, rel=0.02)  # became Z-up, 100 mm tall


def test_convert_glb_to_stl_rotates_to_z_up(project, tmp_path):
    tower = trimesh.creation.box([1, 3, 1])
    src = tmp_path / "t.glb"
    g.save_mesh(tower, src)
    project.store.import_file(src, slug="t", modality="3d", kind="mesh")
    av = run_pipeline(project, "3d.convert", {"input_asset": "t", "format": "stl"})
    assert g.load_mesh(project.store.files_of(av, "primary")[0]).extents[2] == pytest.approx(3)


def test_generation_pipelines_registered():
    ids = {p.id for p in forge_3d.modality.pipelines}
    assert {"3d.text_to_3d", "3d.image_to_3d"} <= ids


def _blob(w, h, color=(200, 40, 40)):
    img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (w, h), color + (255,)), ((300 - w) // 2, (300 - h) // 2))
    return img


def test_prepare_views_keeps_one_scale_across_views():
    from forge_3d.generation import prepare_views

    out = prepare_views({"front": (_blob(60, 120), None), "left": (_blob(200, 100), None)}, size=512)

    def bbox(img):
        ys, xs = np.nonzero(np.asarray(img.convert("L")) < 200)
        return xs.max() - xs.min() + 1, ys.max() - ys.min() + 1

    (fw, fh), (lw, lh) = bbox(out["front"]), bbox(out["left"])
    assert abs(fh - lh) <= 2             # same subject height in every view
    assert lw <= 512 * 0.76 + 2          # the widest view still fits the margin
    assert abs(lw / lh - 2.0) < 0.05 and abs(fw / fh - 0.5) < 0.05


class _FakeComfy:
    url = "fake://comfy"

    def __init__(self, models):
        self._models, self.uploads, self.graphs = models, [], []

    def models(self, folder):
        return self._models

    def upload_image(self, path):
        self.uploads.append(str(path))
        return f"up{len(self.uploads)}.png"

    def run_workflow(self, graph, output_nodes, on_progress=None):
        from types import SimpleNamespace

        self.graphs.append(graph)
        return [SimpleNamespace(node_id=output_nodes[0], filename="m.glb", subfolder="", type="output")]

    def download(self, filename, subfolder, type):
        return trimesh.creation.box((0.5, 1.0, 0.5)).export(file_type="glb")


def test_image_to_3d_with_side_views_uses_multiview(project, tmp_path, monkeypatch):
    import forge_3d.gen_pipelines as gp

    fake = _FakeComfy([DEFAULT_MODELS["hunyuan3d_mv"]])
    monkeypatch.setattr(gp, "comfy_for", lambda ctx, url=None: fake)
    for name, (w, h) in {"front": (60, 120), "back": (60, 120), "left": (150, 120)}.items():
        _blob(w, h).save(tmp_path / f"{name}.png")
    av = run_pipeline(project, "3d.image_to_3d", {
        "input_asset": str(tmp_path / "front.png"), "reconstruct": False,
        "side_views": {"back": str(tmp_path / "back.png"), "left": str(tmp_path / "left.png")}})
    assert av.workflow == "hunyuan3d-2mv"
    nodes = fake.graphs[-1].values()
    cond = next(n for n in nodes if n["class_type"] == "Hunyuan3Dv2ConditioningMultiView")
    assert set(cond["inputs"]) == {"front", "back", "left"}
    sources = {p.name for p in project.store.files_of(av, "source")}
    assert {"input.png", "view_back.png", "view_left.png"} <= sources


def test_image_to_3d_side_views_need_the_multiview_model(project, tmp_path, monkeypatch):
    import forge_3d.gen_pipelines as gp
    from forge_core.runner import PipelineError

    monkeypatch.setattr(gp, "comfy_for", lambda ctx, url=None: _FakeComfy([]))
    _blob(60, 120).save(tmp_path / "front.png")
    with pytest.raises((PipelineError, RuntimeError), match="download_models.py hunyuan3d-2mv"):
        run_pipeline(project, "3d.image_to_3d", {"input_asset": str(tmp_path / "front.png"),
                                                 "side_views": {"back": str(tmp_path / "front.png")}})


def test_trellis_graph_shapes():
    from forge_3d.generation import trellis_image_to_mesh

    g512, s512 = trellis_image_to_mesh("in.png", 3, resolution=512)
    types = [n["class_type"] for n in g512.to_dict().values()]
    assert "Trellis2ShapeCascadeGenerator" not in types and set(s512) == {"mesh"}
    g1536, s1536 = trellis_image_to_mesh("in.png", 3, resolution=1536, texture=True)
    nodes = g1536.to_dict()
    cascade = next(n for n in nodes.values() if n["class_type"] == "Trellis2ShapeCascadeGenerator")
    assert cascade["inputs"]["to_resolution"] == 1536
    assert set(s1536) == {"mesh", "textured"}
    load = next(n for n in nodes.values() if n["class_type"] == "Trellis2LoadModel")
    assert load["inputs"]["backend"] == "sdpa"  # no flash-attn on Windows


def test_prepare_subject_can_keep_transparency():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (40, 60), (200, 30, 30, 255)), (30, 20))
    out = prepare_subject(img, keep_alpha=True)
    assert out.mode == "RGBA"
    a = np.asarray(out.getchannel("A"))
    assert a[0, 0] == 0 and a[512, 512] == 255
