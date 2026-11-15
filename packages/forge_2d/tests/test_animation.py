import json

import numpy as np
import pytest
from PIL import Image

from forge_core.export import run_export
from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import PipelineError, run_pipeline
from forge_2d import modality
from forge_2d.animation import Animation, align_frames, build_sheet, sample_indices, save_gif


def _figure(x: int, y: int, h: int = 20, size=(64, 64), color=(200, 40, 40, 255)) -> Image.Image:
    """Retângulo 'personagem' com pés em y, centro em x."""
    im = Image.new("RGBA", size)
    for yy in range(y - h, y):
        for xx in range(x - 4, x + 4):
            im.putpixel((xx, yy), color)
    return im


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p")


def _make_anim(project, slug="hero-walk", base="hero@v001", n=4):
    frames = [_figure(20 + 5 * i, 50 + (i % 2), h=20 + i) for i in range(n)]
    anim = Animation(align_frames(frames, "bottom", pad=2), fps=8, loop=True, meta={"base": base, "anchor": "bottom"})
    n_, d = project.store.reserve_version(slug)
    files = anim.save(d)
    return project.store.finalize(slug, n_, [(f.path, f.role, f.mime) for f in files], modality="2d", kind="animation",
                                  pipeline="2d.animate", parent=base, extra={"base": base})


def test_align_bottom_in_place():
    frames = [_figure(10, 40), _figure(40, 60, h=30)]
    out = align_frames(frames, "bottom", pad=0)
    assert out[0].size == out[1].size
    for f in out:
        a = np.asarray(f)[..., 3]
        ys, xs = np.nonzero(a)
        assert ys.max() == f.height - 1                       # pés na base
        assert abs((xs.min() + xs.max()) / 2 - f.width / 2) <= 1  # centrado


def test_align_union_keeps_motion():
    frames = [_figure(30, 60), _figure(30, 40)]  # pulo: sobe 20px
    out = align_frames(frames, "union", pad=0)
    bottoms = [np.nonzero(np.asarray(f)[..., 3])[0].max() for f in out]
    assert bottoms[0] - bottoms[1] == 20


def test_sheet_and_samples():
    frames = [_figure(32, 50) for _ in range(5)]
    sheet, rects = build_sheet(frames, "grid", columns=3, padding=2, scale=2)
    assert sheet.size == (3 * 128 + 4, 2 * 128 + 2) and rects[4] == (130, 130, 128, 128)
    assert sample_indices(33, 8, loop=True) == [0, 4, 8, 12, 16, 20, 24, 28]
    assert sample_indices(17, 4, loop=False) == [0, 5, 11, 16]


def test_gif_transparency(tmp_path):
    frames = [_figure(20 + 10 * i, 50) for i in range(3)]
    p = save_gif(frames, [100] * 3, tmp_path / "a.gif", scale=2)
    g = Image.open(p)
    assert g.n_frames == 3 and g.size == (128, 128)
    assert g.info.get("transparency") is not None


def test_roundtrip_and_index(project):
    av = _make_anim(project)
    assert av.kind == "animation" and av.primary().path == "animation.json"
    roles = sorted({f.role for f in av.files})
    assert roles == ["part", "preview", "primary", "texture"]
    a = Animation.load(project.store.version_dir(av.slug, av.version))
    assert len(a.frames) == 4 and a.fps == 8 and a.meta["base"] == "hero@v001"
    assert [r["slug"] for r in project.store.search(kind="animation", base="hero")] == ["hero-walk"]


def test_anim_edit_ops(project, tmp_path):
    _make_anim(project)
    extra = tmp_path / "x.png"
    _figure(30, 60, color=(0, 0, 255, 255)).save(extra)
    ops = [{"op": "delete", "frames": [3]}, {"op": "duplicate", "frame": 0}, {"op": "move", "frame": 1, "to": 3},
           {"op": "duration", "frames": [0], "ms": 300}, {"op": "insert", "at": 0, "image": str(extra)},
           {"op": "flip_h"}, {"op": "pingpong"}]
    av = run_pipeline(project, "2d.anim_edit", {"input_asset": "hero-walk", "ops": ops})
    assert av.ref == "hero-walk@v002" and av.parent == "hero-walk@v001" and av.extra["base"] == "hero@v001"
    a = Animation.load(project.store.version_dir("hero-walk", 2))
    assert len(a.frames) == 5 + 3  # 5 frames + ping-pong (sem repetir as pontas)
    assert a.durations[1] == 300
    assert {f.size for f in a.frames} == {a.size}
    with pytest.raises(ValueError):
        run_pipeline(project, "2d.anim_edit", {"input_asset": "hero-walk", "ops": [{"op": "delete", "frames": list(range(8))}]})


def test_exporters(project, tmp_path):
    _make_anim(project)
    _make_anim(project, slug="hero-idle", n=2)
    src = tmp_path / "hero.png"
    _figure(32, 60).save(src)
    project.store.import_file(src, slug="hero")

    gif = run_export(project, "hero-walk", "2d.gif", {"scale": 3}, tmp_path / "o1")
    assert gif[0].suffix == ".gif" and Image.open(gif[0]).n_frames == 4

    files = run_export(project, "hero-walk", "2d.sheet", {"layout": "grid", "columns": 2}, tmp_path / "o2")
    data = json.loads(files[1].read_text())
    assert len(data["frames"]) == 4 and data["meta"]["frameTags"][0]["name"] == "hero-walk"
    assert data["meta"]["size"]["w"] == Image.open(files[0]).width

    frames = run_export(project, "hero-walk", "2d.frames", {"scale": 2}, tmp_path / "o3")
    assert len(frames) == 4 and frames[0].name == "hero-walk_00.png"

    ss = run_export(project, "hero", "2d.spriteset", {}, tmp_path / "o4")
    tags = [t["name"] for t in json.loads(ss[1].read_text())["meta"]["frameTags"]]
    assert tags == ["base", "hero-idle", "hero-walk"]

    with pytest.raises(PipelineError):  # gif não se aplica a imagem
        run_export(project, "hero", "2d.gif", {}, tmp_path / "o5")


def test_video_status_detects_partial_download(tmp_path, monkeypatch):
    from forge_2d import anim_pipelines as ap
    from forge_2d.graphs import DEFAULT_MODELS

    monkeypatch.setenv("FORGE_COMFY_MODELS_DIR", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(ap, "_comfy_has", lambda ctx, folder, name: True)
    (tmp_path / "diffusion_models").mkdir()
    marker = tmp_path / "diffusion_models" / (DEFAULT_MODELS["wan_low"] + ".dl")
    marker.touch()
    ok, why = ap.video_status(None, dict(DEFAULT_MODELS))
    assert not ok and "baixando" in why
    marker.unlink()
    assert ap.video_status(None, dict(DEFAULT_MODELS)) == (True, "")
    monkeypatch.setattr(ap, "_comfy_has", lambda ctx, folder, name: folder != "loras")
    assert not ap.video_status(None, dict(DEFAULT_MODELS))[0]
