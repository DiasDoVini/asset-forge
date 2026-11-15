"""Autotile (aceite §B): máscaras, geração das 16 variantes, atlas, validadores de tile e o pipeline."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_pixel import autotile as at
from forge_pixel import modality
from forge_pixel import validators as V
from forge_pixel.spec import PaletteSet, default_specs, palette_group_for_file, spec_name_for_file

DIRT = ["#2e1e14", "#3a261a", "#47301f", "#563a2a", "#5e4030", "#664633", "#7b5c45"]
GREENS = ["#4d5b2f", "#66753f", "#86965a", "#9aae63", "#b6c97a"]
PAL = PaletteSet.from_dict({"core": ["#0b0a12", "#1a1830"],
                            "groups": {"tile/dirt": DIRT + GREENS,
                                       "parede/catacumba": ["#2d1f17", "#33241b", "#35261d", "#392920"]}})


def dirt_base(seed: int = 3) -> Image.Image:
    """Base tileável: ruído por pixel (periódico por construção) nas cores da terra."""
    rng = np.random.default_rng(seed)
    cols = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in DIRT[2:6]], np.uint8)
    idx = rng.integers(0, len(cols), (16, 16))
    a = np.full((16, 16, 4), 255, np.uint8)
    a[..., :3] = cols[idx]
    return Image.fromarray(a, "RGBA")


@pytest.fixture(scope="module")
def full():
    return at.make_autotile_set(dirt_base(), "grass", PAL.allowed("tile/dirt"), seed=1)


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield r
    set_registry(None)


# ---------------------------------------------------------------- máscaras

def test_mask_convention():
    assert (at.UP, at.RIGHT, at.DOWN, at.LEFT) == (1, 2, 4, 8)
    assert at.exposed(15) == {"up": False, "right": False, "down": False, "left": False}
    assert at.exposed(14) == {"up": True, "right": False, "down": False, "left": False}
    assert at.exposed(0) == {"up": True, "right": True, "down": True, "left": True}
    assert {k: m for k, (m, _) in at.MINIMAL_MASKS.items()} == {"top": 14, "top_l": 6, "top_r": 12, "top_b": 14}
    g = ["#.#",
         "###",
         "..#"]
    m = at.masks_of_grid(g)
    assert m.tolist() == [[4, -1, 4], [3, 10, 13], [-1, -1, 1]]
    assert at.masks_of_grid(["#"], outside_solid=True).tolist() == [[15]]


def test_default_test_grid_covers_all_16_masks():
    m = at.masks_of_grid(at.DEFAULT_TEST_GRID)
    assert m.shape == (8, 12)
    assert set(m.ravel()) - {-1} == set(range(16))


# ---------------------------------------------------------------- geração

def test_make_autotile_set_16_variants_edges_only(full):
    base = np.asarray(dirt_base())
    assert sorted(full) == list(range(16))
    assert np.array_equal(np.asarray(full[15]), base)
    for m, img in full.items():
        a = np.asarray(img)
        assert a.shape == (16, 16, 4)
        assert set(np.unique(a[..., 3])) <= {0, 255}
        ex = at.exposed(m)
        diff = (a != base).any(axis=2)
        # o miolo (longe de qualquer lado exposto) fica igual ao base
        assert not diff[7:9, 7:9].any()
        if not ex["up"]:
            assert not diff[0, 4:12].any(), m          # lado de cima sólido: linha 0 igual ao base
        else:
            assert diff[0, 4:12].all(), m              # topo exposto: crosta de grama
        if not ex["left"]:
            assert not diff[6:10, 0].any(), m
        else:
            assert diff[6:10, 0].any(), m              # aro escurecido
    # a crosta usa as cores da grama
    top = np.asarray(full[14])
    greens = {tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in GREENS}
    assert all(tuple(px[:3]) in greens for px in top[0])


def test_autotile_deterministic_and_seed_changes_details():
    a = at.make_autotile_set(dirt_base(), "grass", PAL.allowed("tile/dirt"), seed=1)
    b = at.make_autotile_set(dirt_base(), "grass", PAL.allowed("tile/dirt"), seed=1)
    assert all(np.array_equal(np.asarray(a[m]), np.asarray(b[m])) for m in range(16))
    c = at.make_autotile_set(dirt_base(), "grass", PAL.allowed("tile/dirt"), seed=2)
    assert any(not np.array_equal(np.asarray(a[m]), np.asarray(c[m])) for m in range(15))


def test_corner_modes():
    base = dirt_base()
    cut = at.make_variant(base, 0, {"preset": "grass", "corner": "cut", "corner_radius": 2})
    a = np.asarray(cut)
    assert a[0, 0, 3] == 0 and a[15, 15, 3] == 0 and a[0, 1, 3] == 255
    rnd = np.asarray(at.make_variant(base, 0, "grass"))
    assert (rnd[..., 3] == 255).all()                      # "round" é opaco
    assert tuple(rnd[0, 0, :3]) == (0x4d, 0x5b, 0x2f)       # canto de cima = corner_color
    none = np.asarray(at.make_variant(base, 6, {"preset": "grass", "corner": "none"}))
    assert tuple(none[0, 0, :3]) != (0x4d, 0x5b, 0x2f)


def test_minimal_set_names_and_variant_b(full):
    mini = at.minimal_set(dirt_base(), "grass", "dirt", PAL.allowed("tile/dirt"), seed=1, tiles_by_mask=full)
    assert list(mini) == ["dirt_top", "dirt_top_l", "dirt_top_r", "dirt_top_b"]
    assert mini["dirt_top"] is full[14] and mini["dirt_top_l"] is full[6] and mini["dirt_top_r"] is full[12]
    b = np.asarray(mini["dirt_top_b"])
    assert not np.array_equal(b, np.asarray(full[14]))
    assert np.array_equal(b[8:], np.asarray(full[14])[8:])       # só o topo muda


def test_edge_style_presets_and_errors():
    assert at.EdgeStyle.coerce("stone").top_rows
    assert at.EdgeStyle.coerce({"preset": "grass", "top_drip_max": 0}).top_drip_max == 0
    with pytest.raises(KeyError):
        at.EdgeStyle.coerce("lava")
    with pytest.raises(ValueError):
        at.make_variant(Image.new("RGBA", (8, 8)), 14, "grass")


def test_shade_toward_picks_darker_palette_color():
    assert at.shade_toward("#563a2a", DIRT, 0.8) == "#47301f"
    assert at.shade_toward("#2e1e14", DIRT, 0.8) == "#2e1e14"       # nada mais escuro: mantém
    assert at.shade_toward("#563a2a", DIRT, 1.2) in {"#5e4030", "#664633"}


def test_render_test_map(full):
    img = at.render_test_map(full)
    assert img.size == (12 * 16, 8 * 16)
    a = np.asarray(img)
    assert a[0:16, 16:32, 3].max() == 0                        # célula de ar
    assert np.array_equal(a[16 * 7:16 * 8, 16 * 5:16 * 6], np.asarray(full[11]))
    bg = np.asarray(at.render_test_map(full, background="#1a1830"))
    assert (bg[..., 3] == 255).all()
    with pytest.raises(KeyError):
        at.render_test_map({15: full[15]})


# ---------------------------------------------------------------- atlas

def test_atlas_metadata_and_json(tmp_path, full):
    tiles = {f"tile_x_dirt_{at.mask_name(m)}_v01.png": full[m] for m in range(16)}
    tiles["tile_x_dirt_top_l_v01.png"] = full[6]
    sheet, meta = at.atlas(tiles, columns=4)
    assert sheet.size == (64, 80) and (meta["width"], meta["height"]) == (64, 80)
    assert meta["tile_size"] == 16 and meta["ppu"] == 16 and meta["origin"] == "top-left"
    assert meta["rows"] == 5 and meta["columns"] == 4
    t5 = meta["tiles"][5]
    assert t5 == {"name": "tile_x_dirt_m05_v01.png", "mask": 5, "rect": [16, 16, 16, 16]}
    assert meta["tiles"][16]["mask"] == 6 and meta["tiles"][16]["rect"] == [0, 64, 16, 16]
    a = np.asarray(sheet)
    x, y, w, h = t5["rect"]
    assert np.array_equal(a[y:y + h, x:x + w], np.asarray(full[5]))
    p = at.write_atlas_json(meta, tmp_path / "atlas.json")
    assert json.loads(p.read_text(encoding="utf-8")) == meta
    _, m2 = at.atlas({"a": full[1], "b": full[2]}, columns=8, masks={"a": 1}, padding=2)
    assert m2["columns"] == 2 and m2["tiles"][1]["rect"] == [18, 0, 16, 16] and m2["tiles"][1]["mask"] is None
    with pytest.raises(ValueError):
        at.atlas({"x": Image.new("RGBA", (8, 8))})


# ---------------------------------------------------------------- validadores

def test_check_autotile_passes_on_generated_set(full):
    checks = {c.id: c for c in V.check_autotile(full)}
    assert all(c.ok for c in checks.values()), [c.detail for c in checks.values() if not c.ok]
    assert checks["autotile_seams"].data["pairs"] == 32
    assert V.autotile_report(full).ok


def test_check_autotile_missing_and_minimal_list(full):
    part = {m: full[m] for m in (15, 14, 6, 12)}
    c = {c.id: c for c in V.check_autotile(part)}
    assert not c["autotile_set"].ok and 0 in c["autotile_set"].data["missing"]
    ok = {c.id: c for c in V.check_autotile(part, required=[15, 14, 6, 12])}
    assert ok["autotile_set"].ok and ok["autotile_seams"].ok
    assert ok["autotile_map"].skipped
    no_base = {c.id: c for c in V.check_autotile({14: full[14]}, required=[14])}
    assert no_base["autotile_seams"].skipped


def test_check_autotile_fails_on_seam_and_alpha(full):
    bad = dict(full)
    a = np.asarray(full[11]).copy()
    a[:, 0, :3] = [0xb6, 0xc9, 0x7a]                  # coluna clara na junção esquerda (lado sólido)
    bad[11] = Image.fromarray(a, "RGBA")
    c = {c.id: c for c in V.check_autotile(bad)}
    assert not c["autotile_seams"].ok and "|11" in c["autotile_seams"].detail
    semi = dict(full)
    s = np.asarray(full[3]).copy()
    s[0, 0, 3] = 128
    semi[3] = Image.fromarray(s, "RGBA")
    assert not {c.id: c for c in V.check_autotile(semi)}["autotile_alpha"].ok
    small = dict(full)
    small[2] = Image.new("RGBA", (8, 8))
    assert not {c.id: c for c in V.check_autotile(small)}["autotile_canvas"].ok


def test_check_autotile_map_detects_step_and_orphan(full):
    # degrau: a crosta da máscara 6 (topo e esquerda expostos) desce até a linha 10 na coluna da junção com o 14
    step = dict(full)
    a = np.asarray(full[6]).copy()
    a[0:11, 15, :3] = [0x9a, 0xae, 0x63]
    step[6] = Image.fromarray(a, "RGBA")
    c = {c.id: c for c in V.check_autotile(step)}["autotile_map"]
    assert not c.ok and "degrau" in c.detail
    # órfão: base liso com um par de pixels X que só se encontra atravessando a emenda (col 15 | col 0);
    # se a máscara 13 perde o seu X da col 0, o X da col 15 da vizinha 7 fica sozinho no mapa
    flat = np.full((16, 16, 4), 255, np.uint8)
    flat[..., :3] = [0x56, 0x3a, 0x2a]
    flat[8, 0, :3] = flat[8, 15, :3] = [0x7b, 0x5c, 0x45]
    good = at.make_autotile_set(Image.fromarray(flat, "RGBA"), "grass", PAL.allowed("tile/dirt"), seed=1)
    assert {c.id: c for c in V.check_autotile(good)}["autotile_map"].ok
    o = np.asarray(good[13]).copy()
    o[8, 0, :3] = [0x56, 0x3a, 0x2a]
    good[13] = Image.fromarray(o, "RGBA")
    c = {c.id: c for c in V.check_autotile(good)}["autotile_map"]
    assert not c.ok and "órfão" in c.detail and c.data["orphan_px"]
    # ilha: canto cortado deixando um pixel opaco solto
    island = dict(full)
    i = np.asarray(full[0]).copy()
    i[0:3, 0:3, 3] = 0
    i[0, 0, 3] = 255
    island[0] = Image.fromarray(i, "RGBA")
    assert "isolado" in {c.id: c for c in V.check_autotile(island)}["autotile_map"].detail


def _solid(colors, seed=0):
    rng = np.random.default_rng(seed)
    cols = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in colors], np.uint8)
    a = np.full((16, 16, 4), 255, np.uint8)
    a[..., :3] = cols[rng.integers(0, len(cols), (16, 16))]
    return Image.fromarray(a, "RGBA")


def test_check_wall_contrast_pass_and_fail():
    solid = _solid(["#47301f", "#563a2a", "#664633", "#7b5c45"])
    wall = _solid(["#33241b", "#35261d", "#392920"])
    c = V.check_wall_contrast(solid, wall)
    assert c.ok and c.elim and c.data["wall"]["luminance"] < c.data["solid"]["luminance"]
    bright = V.check_wall_contrast(wall, solid)               # invertido: parede mais clara
    assert not bright.ok and "luminância" in bright.detail
    # escura mas com o mesmo contraste interno que o sólido: reprova no contraste
    flat_solid = _solid(["#563a2a"])
    harsh_wall = _solid(["#0b0a12", "#392920"])
    hc = V.check_wall_contrast(flat_solid, harsh_wall)
    assert not hc.ok and "contraste" in hc.detail


def test_check_ore_on_base_pass_and_fail():
    base = _solid(["#3a3b4d", "#45475c", "#313243"])
    ore = np.asarray(base).copy()
    ore[2:6, 2:6, :3] = [0xc8, 0x70, 0x2f]                       # 16 px de cobre = 94% igual
    c = V.check_ore_on_base(Image.fromarray(ore, "RGBA"), base)
    assert c.ok and c.data["shared"] == pytest.approx(240 / 256, abs=1e-3)
    floating = ore.copy()
    floating[10:12, 10:12, 3] = 0
    f = V.check_ore_on_base(Image.fromarray(floating, "RGBA"), base)
    assert not f.ok and "transparentes" in f.detail
    other = _solid(["#3a3b4d", "#45475c", "#313243"], seed=9)     # outra pedra: < 60% igual
    assert not V.check_ore_on_base(other, base).ok
    assert not V.check_ore_on_base(Image.new("RGBA", (8, 8)), base).ok


def test_tile_specs_by_file_name():
    assert spec_name_for_file("tile_catacumba_dirt_m03_v01.png") == "tile_edge"
    assert spec_name_for_file("tile_catacumba_dirt_m14_v01.png") == "tile_top"
    assert spec_name_for_file("tile_catacumba_dirt_m15_v01.png") == "tile"
    assert spec_name_for_file("tile_catacumba_dirt_top_l_v01.png") == "tile_edge"
    assert spec_name_for_file("tile_catacumba_dirt_top_b_v01.png") == "tile_top"
    assert palette_group_for_file("tile_catacumba_stone_m07_v01.png") == "tile/stone"
    edge = default_specs()["tile_edge"]
    assert not edge.tileable and edge.opaque and edge.max_colors == 12


# ---------------------------------------------------------------- pipeline

def _base_asset(project, tmp_path):
    rows = []
    a = np.asarray(dirt_base())
    legend, inv = {}, {}
    chars = "abcdefgh"
    for y in range(16):
        r = ""
        for x in range(16):
            hx = "#%02x%02x%02x" % tuple(int(v) for v in a[y, x, :3])
            if hx not in inv:
                inv[hx] = chars[len(inv)]
                legend[inv[hx]] = hx
            r += inv[hx]
        rows.append(r)
    m = {"width": 16, "height": 16, "legend": legend, "rows": rows, "outline": {"auto": False}}
    return run_pipeline(project, "pixel.map", {"map": m, "spec": "tile", "palette_group": "tile/dirt",
                                               "file_name": "tile_catacumba_dirt_v01.png"}, asset="dirt-base")


def test_autotile_pipeline_outputs_and_determinism(tmp_path):
    project = Project.create(tmp_path / "p", pixel={"palette": PAL.model_dump()})
    base = _base_asset(project, tmp_path)
    assert base.extra["validation"]["ok"], base.extra["validation"]
    params = {"input_asset": base.ref, "edge_style": "grass", "set": "both", "prefix": "tile_catacumba_dirt"}
    av = run_pipeline(project, "pixel.autotile", params, asset="dirt-auto")
    files = {f.name: f for f in project.store.files_of(av)}
    for m in range(16):
        assert f"tile_catacumba_dirt_m{m:02d}_v01.png" in files
    for s in ("top", "top_l", "top_r", "top_b"):
        assert f"tile_catacumba_dirt_{s}_v01.png" in files
    roles = {f.role for f in av.files}
    assert roles == {"primary", "part", "source", "preview", "report"}
    meta = json.loads(files["atlas.json"].read_text(encoding="utf-8"))
    assert len(meta["tiles"]) == 20 and meta["ppu"] == 16
    assert {t["name"]: t["mask"] for t in meta["tiles"]}["tile_catacumba_dirt_top_r_v01.png"] == 12
    rep = json.loads(files["report.json"].read_text(encoding="utf-8"))
    assert rep["ok"], av.extra["validation"]
    assert av.extra["validation"]["ok_all"] is True
    with Image.open(files["test_map.png"]) as tm:
        assert tm.size == (192, 128)
    av2 = run_pipeline(project, "pixel.autotile", params, asset="dirt-auto-2")
    f2 = {f.name: f for f in project.store.files_of(av2)}
    for name in ("atlas.png", "tile_catacumba_dirt_m06_v01.png", "tile_catacumba_dirt_top_b_v01.png", "test_map.png"):
        assert files[name].read_bytes() == f2[name].read_bytes(), name
    mini = run_pipeline(project, "pixel.autotile", {**params, "set": "minimal"}, asset="dirt-mini")
    names = {f.name for f in project.store.files_of(mini, "part")}
    assert names == {f"tile_catacumba_dirt_{s}_v01.png" for s in ("top", "top_l", "top_r", "top_b")}
    from forge_pixel.api import validate_autotile_asset

    assert validate_autotile_asset(project, av.ref).ok
    with pytest.raises(ValueError):
        validate_autotile_asset(project, mini.ref)


def test_autotile_pipeline_rejects_wrong_size(tmp_path):
    project = Project.create(tmp_path / "p", pixel={"palette": PAL.model_dump()})
    src = tmp_path / "big.png"
    Image.new("RGBA", (32, 32), (86, 58, 42, 255)).save(src)
    with pytest.raises(ValueError, match="16x16"):
        run_pipeline(project, "pixel.autotile", {"input_asset": str(src)})
