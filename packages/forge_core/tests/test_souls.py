"""Creator Souls: ingestão com catálogo, busca, análise de estilo, anexos, export/import e API."""
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from forge_core.config import Settings, get_settings
from forge_core.project import Project
from forge_core.souls import get_library, soul_context
from forge_core.souls.index import SearchIndex, expand_query, tokens
from forge_core.souls.ingest import build, category_of, first_frame, parse_catalog
from forge_core.souls.package import export_soul, import_soul

CATALOG = """# catálogo de teste

## Item (4)

| Asset | Name | Size | Frames | Colour |
|---|---|---|---|---|
| `Item_1` | FieryGreatsword | 16x16 | 1 | orange |
| `Item_2` | IceBlade | 16x16 | 1 | blue |
| `Item_3` | WoodenBow | 16x16 | 1 | brown |
| `Item_4` | None | 16x16 | 1 | gray |

## NPC (2)

| Asset | Name | Size | Frames | Colour |
|---|---|---|---|---|
| `NPC_1` | BlueSlime | 16x64 | 4 | blue |
| `NPC_2` | DemonEye | 16x16 | 1 | red |
"""


def _sprite(color, size=8, scale=2) -> Image.Image:
    """Sprite de pixel art 2x com contorno escuro."""
    a = np.zeros((size, size, 4), np.uint8)
    a[1:-1, 1:-1] = (*color, 255)
    a[1, 1:-1] = a[-2, 1:-1] = a[1:-1, 1] = a[1:-1, -2] = (20, 20, 20, 255)
    im = Image.fromarray(a, "RGBA")
    return im.resize((size * scale, size * scale), Image.Resampling.NEAREST)


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "refs"
    d.mkdir()
    for stem, c in {"Item_1": (230, 120, 30), "Item_2": (80, 160, 240), "Item_3": (140, 90, 40), "Item_4": (128, 128, 128),
                    "NPC_2": (200, 30, 30)}.items():
        _sprite(c).save(d / f"{stem}.png")
    strip = Image.new("RGBA", (16, 64))
    for i in range(4):
        strip.paste(_sprite((60, 120, 255)), (0, i * 16))
    strip.save(d / "NPC_1.png")
    Image.new("RGBA", (16, 16)).save(d / "Empty_1.png")  # vazia: ignorada
    (d / "CATALOG.md").write_text(CATALOG, encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def souls_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_SOULS_DIR", str(tmp_path / "souls"))
    get_settings.cache_clear()
    yield tmp_path / "souls"
    get_settings.cache_clear()


@pytest.fixture
def soul(folder):
    s = get_library().create("Teste")
    return build(s, folder, describe=False)


def test_catalog_and_names(folder):
    cat = parse_catalog(folder / "CATALOG.md")
    assert cat["Item_1"]["name"] == "FieryGreatsword" and cat["NPC_1"]["frames"] == 4 and cat["Item_4"]["name"] == ""
    assert cat["NPC_1"]["section"] == "NPC"
    assert category_of("Acc_Back_12") == "Acc_Back" and category_of("NPC_Head_Boss_3") == "NPC_Head_Boss"
    assert "sword" in tokens("FieryGreatsword") and "fiery" in tokens("FieryGreatsword")
    groups, cats = expand_query("espada de fogo inimigo")
    assert any("sword" in g for g in groups) and any("fire" in g for g in groups) and "npc" in cats
    fr = first_frame(Image.new("RGBA", (16, 64)), 4)
    assert fr.size == (16, 16)


def test_build_index_and_analysis(soul):
    m = soul.manifest
    assert m.status == "ready" and m.count == 6  # a vazia foi ignorada
    assert m.categories == {"Item": 4, "NPC": 2}
    assert m.pixel.is_pixel_art and m.pixel.scale == 2 and m.pixel.outline == "#141414"
    assert m.palette and soul.cover.exists() and all(soul.ref_path(e["id"]).exists() for e in soul.entries())
    assert "pixel art" in m.style.prompt and "2x" in m.style.prompt
    slime = next(e for e in soul.entries() if e["stem"] == "NPC_1")
    assert (slime["fw"], slime["fh"]) == (16, 16)  # 1º quadro do strip


def test_retrieval(soul):
    top = lambda q, **kw: [r["name"] for r in soul.retrieve(q, 2, **kw)]  # noqa: E731
    assert top("fire sword")[0] == "FieryGreatsword"
    assert top("espada de gelo")[0] == "IceBlade"
    assert top("slime azul")[0] == "BlueSlime"
    assert top("inimigo olho")[0] == "DemonEye"
    assert top("bow", categories=["NPC"]) == []
    soul.manifest.pinned = [next(e["id"] for e in soul.entries() if e["name"] == "WoodenBow")]
    assert top("xyzzy") == ["WoodenBow"]  # sem casamento: usa as fixadas


def test_bm25_prefers_full_coverage():
    idx = SearchIndex([{"id": "a", "name": "FlowerofFire", "category": "Item"},
                       {"id": "b", "name": "FieryGreatsword", "category": "Item"},
                       {"id": "c", "name": "CopperShortsword", "category": "Item"}])
    assert idx.search("fire sword", 1)[0]["id"] == "b"


def test_attach_style_merge_and_context(soul, tmp_path):
    p = Project.create(tmp_path / "proj")
    p.attach_soul(soul.id, 0.8)
    st = p.style_for("2d")
    assert soul.manifest.style.prompt in st["prompt"] and st["palette"] == soul.manifest.palette
    assert st["pixel"]["outline"] == "#141414" and st["pixel_art"] is True and st["souls"][0]["id"] == soul.id
    assert [s.id for s, _ in soul_context(p)] == [soul.id]
    p.update_soul(soul.id, enabled=False)
    assert soul_context(Project.load(p.root)) == [] and "souls" not in Project.load(p.root).style_for("2d")
    p.detach_soul(soul.id)
    assert Project.load(p.root).config.souls == []


def test_nsfw_soul_needs_nsfw_project(soul, tmp_path):
    soul.manifest.nsfw = True
    soul.save()
    p = Project.create(tmp_path / "p")
    with pytest.raises(PermissionError):
        p.attach_soul(soul.id)
    p.set_nsfw(True, confirm_adult=True)
    p.attach_soul(soul.id)


def test_soul_ref_as_pipeline_input(soul, tmp_path):
    from forge_core.runner import resolve_input

    rid = soul.entries()[0]["id"]
    av, path = resolve_input(Project.create(tmp_path / "p"), f"soul:{soul.id}/{rid}")
    assert av is None and path == soul.ref_path(rid)


def test_export_import_roundtrip(soul, tmp_path):
    f = export_soul(soul, tmp_path / "x.soul")
    s2 = import_soul(get_library(), f, "Cópia")
    assert s2.id != soul.id and s2.manifest.name == "Cópia" and s2.manifest.count == soul.manifest.count
    assert s2.retrieve("fire sword", 1)[0]["name"] == "FieryGreatsword"
    assert {s.id for s in get_library().list()} == {soul.id, s2.id}


def test_api(folder, tmp_path):
    from forge_core.server import create_app

    settings = Settings(projects_dir=tmp_path / "projects", comfy_url="http://127.0.0.1:1", host="127.0.0.1", port=0,
                        web_dist=None)
    with TestClient(create_app(settings, isolate_jobs=False, persist_jobs=False)) as c:
        r = c.post("/api/souls", json={"name": "API", "folder": str(folder), "describe": False})
        assert r.status_code == 202
        sid = r.json()["id"]
        for _ in range(100):
            if c.get(f"/api/souls/{sid}").json()["status"] == "ready":
                break
            import time
            time.sleep(0.1)
        j = c.get(f"/api/souls/{sid}").json()
        assert j["count"] == 6, (j["status"], j["error"], j.get("progress"))
        res = c.get(f"/api/souls/{sid}/search", params={"q": "fire sword"}).json()
        assert res[0]["name"] == "FieryGreatsword"
        assert c.get(f"/api/souls/{sid}/refs/{res[0]['id']}.png").status_code == 200
        c.post("/api/projects", json={"slug": "p"})
        assert c.post("/api/projects/p/souls", json={"id": sid, "strength": 0.6}).json()["souls"][0]["strength"] == 0.6
        refs = c.get("/api/projects/p/soul-refs", params={"q": "slime"}).json()
        assert refs[0]["name"] == "BlueSlime" and refs[0]["ref"].startswith(f"soul:{sid}/")
        exp = c.get(f"/api/souls/{sid}/export")
        assert exp.status_code == 200 and len(exp.content) > 100
        imp = c.post("/api/souls/import", files={"file": ("a.soul", exp.content)}, data={"name": "Importada"})
        assert imp.status_code == 201
        assert c.delete(f"/api/souls/{sid}").status_code == 200
        assert c.get("/api/projects/p").json()["souls"] == []
