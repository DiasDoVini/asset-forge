"""2d.soul com ComfyUI simulado: refs certas no grafo, instrução de restyle/derive e pixelização da Soul."""
import numpy as np
import pytest
from PIL import Image

from forge_core.config import get_settings
from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_core.souls import get_library
from forge_core.souls.ingest import build
from forge_2d import modality, soul_pipelines


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_SOULS_DIR", str(tmp_path / "souls"))
    get_settings.cache_clear()
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield
    set_registry(None)
    get_settings.cache_clear()


@pytest.fixture
def fake_comfy(monkeypatch):
    calls = {"uploads": [], "graphs": []}

    def upload(ctx, img, name):
        calls["uploads"].append((name, img.size))
        return name

    def run(ctx, graph, want, span=(0, 1), label=""):
        calls["graphs"].append(graph.to_dict())
        img = Image.new("RGB", (512, 512), (200, 200, 200))
        img.paste((40, 140, 230), (156, 156, 356, 356))
        mask = Image.new("RGB", (512, 512), 0)
        mask.paste((255, 255, 255), (156, 156, 356, 356))
        return {w: (mask if i == 1 else img) for i, w in enumerate(want) if w}

    monkeypatch.setattr(soul_pipelines, "_upload", upload)
    monkeypatch.setattr(soul_pipelines, "_run", run)
    monkeypatch.setattr(soul_pipelines, "_ensure_comfy", lambda ctx: None)
    return calls


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "refs"
    d.mkdir()
    for stem, c in {"Item_1": (230, 120, 30), "Item_2": (80, 160, 240)}.items():
        a = np.zeros((12, 12, 4), np.uint8)
        a[1:-1, 1:-1] = (*c, 255)
        a[1, 1:-1] = a[-2, 1:-1] = a[1:-1, 1] = a[1:-1, -2] = (20, 20, 20, 255)
        Image.fromarray(a).resize((24, 24), Image.Resampling.NEAREST).save(d / f"{stem}.png")
    (d / "CATALOG.md").write_text("## Item (2)\n\n| Asset | Name | Size | Frames | Colour |\n|---|---|---|---|---|\n"
                                  "| `Item_1` | FieryGreatsword | 24x24 | 1 | orange |\n"
                                  "| `Item_2` | IceStaff | 24x24 | 1 | blue |\n", encoding="utf-8")
    soul = build(get_library().create("Mini"), d, describe=False)
    p = Project.create(tmp_path / "proj")
    p.attach_soul(soul.id, 0.8)
    return p


def test_restyle_uses_retrieved_refs_and_soul_pixels(project, fake_comfy):
    av = run_pipeline(project, "2d.soul", {"prompt": "a frost staff with a blue crystal", "seed": 1})
    assert av.extra["references"][0]["name"] == "IceStaff" and av.extra["strategy"] == "restyle"
    zimage, qwen = fake_comfy["graphs"]
    assert any(n["class_type"] == "UNETLoader" for n in zimage.values())
    loads = [n for n in qwen.values() if n["class_type"] == "LoadImage"]
    assert [n["inputs"]["image"] for n in loads] == ["draft.png", "ref0.png", "ref1.png"]
    assert "exact art style" in av.extra["instruction"]
    assert av.kind == "sprite"
    px = av.extra["pixel"]
    assert px["scale"] == 2 and px["outline"] == "#141414" and px["size"] == 12  # tamanho da ref em pixels de arte


def test_derive_and_explicit_refs(project, fake_comfy):
    soul_id = project.config.souls[0].id
    ref = next(e["id"] for e in get_library().get(soul_id).entries() if e["name"] == "FieryGreatsword")
    av = run_pipeline(project, "2d.soul", {"prompt": "a lava hammer", "strategy": "derive",
                                           "references": [f"soul:{soul_id}/{ref}"], "pixelize": False})
    (qwen,) = fake_comfy["graphs"]
    assert [n["inputs"]["image"] for n in qwen.values() if n["class_type"] == "LoadImage"] == ["ref0.png"]
    assert "a lava hammer" in av.extra["instruction"] and av.kind == "image"


def test_requires_soul_or_refs(tmp_path, fake_comfy):
    with pytest.raises(ValueError, match="Soul"):
        run_pipeline(Project.create(tmp_path / "vazio"), "2d.soul", {"prompt": "sword"})
