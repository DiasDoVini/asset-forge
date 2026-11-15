import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_pixel import modality
from forge_pixel import pixelmap as pm
from forge_pixel.api import validate_asset
from forge_pixel.fit import fit_to_spec
from forge_pixel.palette import darken, hex_to_rgb, nearest, quantize, ramp, rgb_to_oklab
from forge_pixel.preview import contact_sheet, side_by_side, zoom_preview
from forge_pixel.spec import AssetSpec, PaletteSet, default_specs, make_specs, resolve_spec, spec_name_for_file
from forge_pixel import validators as V

K = "#0b0a12"
PAL = PaletteSet.from_dict({
    "core": [K, "#1a1830", "#e8b45a", "#ffffff"],
    "groups": {"teste/carmim": {"cores": ["#7e1a2f", "#4f0f1e", "#a8324a"]},
               "teste/pedra": ["#3a3b4d", "#313243", "#45475c", "#3f4055", "#272836", "#4b4d65"]},
})
SPECS = default_specs()


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield r
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p", pixel={"palette": PAL.model_dump()})


def arr(img):
    return np.asarray(img.convert("RGBA"))


def from_rows(rows, legend, outline=False):
    return pm.render({"width": len(rows[0]), "height": len(rows), "legend": legend, "rows": rows,
                      "outline": {"auto": outline}})


def limb_map(side=None):
    """Segmento de membro como no gen-parts.js: layer(3, 9, ['dCr' x6, 'Ggg'])."""
    return {"format": "asset-forge-pixelmap/1", "width": 8, "height": 12,
            "legend": {"C": "#7e1a2f", "d": "#4f0f1e", "r": "#a8324a", "G": "#e8b45a"},
            "layers": [{"x0": 3, "y_top": 9, "rows": ["dCr"] * 6 + ["GGG"]}],
            "outline": {"auto": True, "color": K}, "side": side}


# ---------------------------------------------------------------- pixel map

def test_render_outline_matches_hand_made_array():
    m = {"width": 5, "height": 5, "legend": {"a": "#e8b45a"},
         "rows": [".....", ".....", "..a..", "..a..", "....."]}
    got = pm.from_image(pm.render(m), legend_hint={"a": "#e8b45a", "k": K}).rows
    assert got == [".....", "..k..", ".kak.", ".kak.", "..k.."]


def test_layers_count_y_from_bottom_and_clip():
    m = {"width": 4, "height": 4, "legend": {"b": "#ffffff"}, "outline": {"auto": False},
         "layers": [{"x0": 1, "y_top": 1, "rows": ["bb", "b"]}, {"x0": 3, "y_top": 9, "rows": ["b"]}]}
    g = pm.char_grid(pm.parse(m))
    assert ["".join(c or "." for c in r) for r in g] == ["....", "....", ".bb.", ".b.."]


def test_parse_errors_have_row_and_col():
    with pytest.raises(pm.PixelMapError) as e:
        pm.parse({"width": 3, "height": 2, "legend": {"a": "#fff"}, "rows": ["a.a", "aXa"]})
    msg = str(e.value)
    assert "linha 1, coluna 1" in msg and "'X'" in msg
    assert "cor de 'a' inválida" in msg
    with pytest.raises(pm.PixelMapError, match="rows tem 1 linhas"):
        pm.parse({"width": 2, "height": 2, "legend": {"a": "#ffffff"}, "rows": ["aa"]})


def test_shade_side_is_78_percent_except_outline():
    r = pm.render(limb_map())
    left = pm.render(limb_map(side="L"))
    a, b = arr(r), arr(left)
    assert (a[..., 3] == b[..., 3]).all()
    ok = V.check_lr_pair(left, r)
    assert ok.ok, ok.detail
    # mesma regra do gen-parts.js: Math.round(c * 0.78)
    assert darken("#7e1a2f") == "#621425"
    k = (a[..., :3] == hex_to_rgb(K)).all(-1) & (a[..., 3] > 0)
    assert (b[k, :3] == hex_to_rgb(K)).all()


def test_from_image_roundtrip_and_mirror():
    img = pm.render(limb_map())
    m = pm.from_image(img)
    assert m.legend["k"] == K and not m.outline.auto
    assert pm.render(m).tobytes() == img.tobytes()
    mir = pm.mirror_map(m)
    assert pm.render(mir).tobytes() == pm.mirror_image(img).tobytes()


def test_render_is_deterministic():
    a = pm.render(limb_map())
    b = pm.render(json.loads(json.dumps(limb_map())))
    assert a.tobytes() == b.tobytes()


# ---------------------------------------------------------------- paleta

def test_oklab_and_quantize():
    assert np.allclose(rgb_to_oklab(np.array([255, 255, 255]))[0], 1.0, atol=1e-3)
    assert nearest("#800000", ["#7e1a2f", "#2d4a7a", "#ffffff"]) == "#7e1a2f"
    img = Image.new("RGBA", (2, 1))
    img.putpixel((0, 0), (250, 250, 250, 200))
    img.putpixel((1, 0), (10, 10, 10, 100))
    q = arr(quantize(img, ["#ffffff", "#000000"]))
    assert tuple(q[0, 0]) == (255, 255, 255, 255) and q[0, 1, 3] == 0
    r = ramp("#7e1a2f", 3)
    assert len(r) == 3 and r[1] == "#7e1a2f"


# ---------------------------------------------------------------- spec / config

def test_specs_table_and_overrides():
    s = SPECS
    assert s["head"].canvas == (20, 20) and s["head"].pivot == (10, 0) and s["head"].safe_area == (2, 0, 16, 15)
    assert s["limb"].canvas == (8, 12) and s["limb"].pivot == (4, 10)
    assert s["cape"].pivot == (9, 12) and s["icon"].coverage == (11, 16) and s["tile"].opaque
    over = make_specs({"torso": {"safe_area": [2, 0, 12, 16]}, "lantern": {"kind": "icon", "canvas": [10, 10]}})
    assert over["torso"].safe_area == (2, 0, 12, 16) and over["torso"].canvas == (16, 18)
    assert over["lantern"].name == "lantern"
    inline = resolve_spec({"base": "limb", "palette_group": "teste/carmim"})
    assert inline.canvas == (8, 12) and inline.palette_group == "teste/carmim"
    assert spec_name_for_file("part_chest_vaniacoat_arm_up_R_v01.png") == "limb"
    assert spec_name_for_file("part_boots_leather_L_v01.png") == "boot"
    assert spec_name_for_file("part_cape_moon_1_v01.png") == "cape"
    assert spec_name_for_file("tile_catacumba_dirt_top_v01.png") == "tile_top"
    assert PAL.allowed("teste/carmim") >= {K, "#7e1a2f"} and PAL.allowed(None) == set(PAL.core)


# ---------------------------------------------------------------- validadores (passa / falha)

# carmim e ouro do teste só passam na regra de cor com as tags vania (carmim) e noble (ouro)
TAGS = ["vania", "noble"]
LIMB = SPECS["limb"].model_copy(update={"palette_group": "teste/carmim", "tags": TAGS})


def good_limb():
    return pm.render(limb_map())


def test_full_validation_passes_on_good_limb():
    rep = V.validate(good_limb(), LIMB, PAL, name="part_test_arm_up_R_v01.png")
    assert rep.ok, rep.to_markdown()
    assert "APROVADO" in rep.to_markdown()


def test_canvas():
    assert V.check_canvas(arr(good_limb()), LIMB).ok
    assert not V.check_canvas(np.zeros((12, 9, 4), np.uint8), LIMB).ok


def test_alpha():
    a = arr(good_limb()).copy()
    assert V.check_alpha(a, LIMB).ok
    a[5, 4, 3] = 128
    c = V.check_alpha(a, LIMB)
    assert not c.ok and c.data["count"] == 1


def test_palette_lists_off_colors():
    a = arr(good_limb()).copy()
    assert V.check_palette(a, LIMB, PAL).ok
    a[5, 4, :3] = (1, 2, 3)
    c = V.check_palette(a, LIMB, PAL)
    assert not c.ok and c.data["off_palette"] == {"#010203": 1}
    assert V.check_palette(a, LIMB, PaletteSet.only_outline()).skipped


def test_outline_required():
    a = arr(good_limb()).copy()
    assert V.check_outline(a, LIMB).ok
    hole = a.copy()
    hole[1, 3, :3] = hex_to_rgb("#4f0f1e")            # contorno do topo trocado por cor de corpo
    c = V.check_outline(hole, LIMB)
    assert not c.ok and c.data["missing"] >= 1
    thick = a.copy()
    ring = pm.outline_ring(thick[..., 3] > 0)
    thick[ring, :3] = hex_to_rgb(K)
    thick[ring, 3] = 255                              # 2ª camada de contorno
    c = V.check_outline(thick, LIMB)
    assert not c.ok and c.data["thick"] > 0


def test_outline_none_rejects_ring():
    spec = SPECS["tile_open"].model_copy(update={"outline": "none"})
    img = from_rows(["....", ".aa.", ".aa.", "...."], {"a": "#e8b45a"}, outline=True)
    assert not V.check_outline(arr(img), spec).ok
    assert V.check_outline(arr(from_rows(["....", ".aa.", ".aa.", "...."], {"a": "#e8b45a"})), spec).ok


def test_islands_and_sparkles():
    a = arr(good_limb()).copy()
    assert V.check_islands(a, LIMB).ok
    a[11, 0] = (255, 255, 255, 255)                    # pixel solto no canto
    c = V.check_islands(a, LIMB)
    assert not c.ok and c.data["islands"] == [(0, 0)]
    assert V.check_islands(a, LIMB.model_copy(update={"sparkle_colors": ["#ffffff"]})).ok


def test_safe_area():
    a = arr(good_limb()).copy()
    assert V.check_safe_area(a, LIMB).ok
    a[11, 4] = (*hex_to_rgb(K), 255)                   # y=0: abaixo da área (1,2,6,9)
    c = V.check_safe_area(a, LIMB)
    assert not c.ok and c.data["pixels"] == [(4, 0)]


def test_anchor():
    assert V.check_anchor(arr(good_limb()), LIMB).ok
    m = limb_map()
    m["layers"][0]["y_top"] = 6                       # segmento 3 px abaixo do pivô
    c = V.check_anchor(arr(pm.render(m)), LIMB)
    assert not c.ok and c.data["distance"] > 1


def _noise_tile(seed=0, n=16):
    rng = np.random.default_rng(seed)
    cols = np.array([hex_to_rgb(c) for c in PAL.groups["teste/pedra"]], np.uint8)
    a = np.zeros((n, n, 4), np.uint8)
    a[..., :3] = cols[rng.integers(0, len(cols), (n, n))]
    a[..., 3] = 255
    return a


def test_tileable_noise_passes_hard_seam_fails():
    spec = SPECS["tile"].model_copy(update={"palette_group": "teste/pedra"})
    noise = _noise_tile()
    assert V.check_tileable(noise, spec).ok
    assert V.validate(noise, spec, PAL, name="tile_catacumba_pedra_v01.png").ok
    seam = noise.copy()
    seam[:, 15, :3] = np.minimum(255, seam[:, 15, :3].astype(int) + 120).astype(np.uint8)  # só a última coluna destoa
    assert not V.check_tileable(seam, spec).ok
    grad = noise.copy()
    grad[..., :3] = np.clip(np.arange(16)[None, :, None] * 12 + 20, 0, 255).astype(np.uint8)  # degradê
    assert not V.check_tileable(grad, spec).ok
    framed = noise.copy()
    framed[:, 0, :3] = 250
    framed[:, 15, :3] = 250                           # "grade" visível nas bordas
    assert not V.check_tileable(framed, spec).ok
    top = SPECS["tile_top"]
    assert V.check_tileable(grad.transpose(1, 0, 2).copy(), top).ok   # degradê vertical: só x é exigido


def test_opaque_and_max_colors():
    spec = SPECS["tile"]
    a = _noise_tile()
    assert V.check_opaque(a, spec).ok and V.check_max_colors(a, spec).ok
    b = a.copy()
    b[3, 3, 3] = 0
    assert not V.check_opaque(b, spec).ok
    c = a.copy()
    c[0, :9, :3] = np.arange(9)[:, None] + 100
    assert not V.check_max_colors(c, spec).ok


def test_icon_coverage():
    spec = SPECS["icon"]
    big = Image.new("RGBA", (16, 16))
    ImageDraw.Draw(big).rectangle([2, 2, 14, 13], fill=hex_to_rgb("#e8b45a") + (255,))
    assert V.check_coverage(arr(big), spec).ok
    small = Image.new("RGBA", (16, 16))
    ImageDraw.Draw(small).rectangle([5, 5, 9, 9], fill=hex_to_rgb("#e8b45a") + (255,))
    assert not V.check_coverage(arr(small), spec).ok


def test_name_patterns():
    assert V.check_name("part_chest_vaniacoat_arm_up_L_v01.png", SPECS["limb"]).ok
    assert not V.check_name("part_arm.png", SPECS["limb"]).ok
    assert not V.check_name("part_body_arm_up_R_v1.png", SPECS["limb"]).ok
    assert V.check_name("tile_catacumba_stone_b_v02.png", SPECS["tile"]).ok
    assert not V.check_name("stone.png", SPECS["tile"]).ok
    assert V.check_name("enemy_catacumba_esqueleto_v01_3.png", SPECS["enemy"]).ok
    assert V.check_name("icon_pickaxe_iron_v01.png", SPECS["icon"]).ok
    assert V.check_name(None, SPECS["icon"]).skipped


def test_lr_pair_fails_on_wrong_shade_and_mask():
    r = pm.render(limb_map())
    wrong = pm.shade_side(r, 0.6)
    assert not V.check_lr_pair(wrong, r).ok
    moved = pm.render(limb_map(side="L"))
    a = arr(moved).copy()
    a[11, 0] = (0, 0, 0, 255)
    assert V.check_lr_pair(a, r).data["mask_diff"] == 1
    # paleta do lado L aceita os tons a 78%
    assert V.check_palette(arr(moved), LIMB, PAL, side="L").ok
    assert not V.check_palette(arr(moved), LIMB, PAL, side="R").ok


def test_nine_slice_and_cumulative_and_pose_set():
    panel = np.zeros((24, 24, 4), np.uint8)
    panel[..., 3] = 255
    panel[..., :3] = hex_to_rgb("#1a1830")
    panel[:8, :, :3] = hex_to_rgb("#e8b45a")
    panel[-8:, :, :3] = hex_to_rgb("#e8b45a")
    panel[:, :8, :3] = hex_to_rgb("#e8b45a")
    panel[:, -8:, :3] = hex_to_rgb("#e8b45a")
    assert V.check_nine_slice(panel, (8, 8, 8, 8)).ok
    bad = panel.copy()
    bad[8:16, 8:16, :3] = np.clip(np.arange(8)[None, :, None] * 30, 0, 255)   # miolo em degradê: emenda
    assert not V.check_nine_slice(bad, (8, 8, 8, 8)).ok
    assert not V.check_nine_slice(panel, (12, 8, 12, 8)).ok

    s1 = np.zeros((16, 16, 4), np.uint8)
    s1[8, 4:8] = (11, 10, 18, 255)
    s2 = s1.copy()
    s2[9, 7:11] = (11, 10, 18, 255)
    assert V.check_cumulative([s1, s2]).ok
    s3 = s2.copy()
    s3[8, 4] = 0
    assert not V.check_cumulative([s1, s2, s3]).ok

    base = pm.render(limb_map())
    a = arr(base).copy()
    b = np.roll(a, 1, axis=1)
    checks = {c.id: c for c in V.check_pose_set([a, b])}
    assert checks["same_canvas"].ok and checks["ground_line"].ok and checks["pose_palette"].ok
    lifted = np.roll(a, -1, axis=0)
    assert not {c.id: c for c in V.check_pose_set([a, lifted])}["ground_line"].ok
    assert not {c.id: c for c in V.check_pose_set([a, a])}["pose_change"].ok   # pose repetida


# ---------------------------------------------------------------- §7: specs ajustadas, material, espessura, cor

ICON = SPECS["icon"]


def icon(rows, legend):
    """Ícone 16x16 a partir de linhas (14x14 no miolo) com contorno automático."""
    full = ["." * 16] + ["." + r.ljust(14, ".") + "." for r in rows] + ["." * 16] * (15 - len(rows))
    return arr(from_rows(full, legend, outline=True))


def test_specs_after_art_review():
    s = SPECS
    assert s["hand"].safe_area == (2, 1, 5, 6) and s["torso"].safe_area == (2, 0, 12, 16)
    assert s["cape"].safe_area == (2, 0, 9, 13) and s["tile_top"].max_colors == 12 and s["icon"].coverage == (11, 16)
    lm = s["limb_metal"]
    assert lm.safe_area == (1, 2, 7, 10) and (lm.canvas, lm.pivot, lm.anchor) == (s["limb"].canvas, s["limb"].pivot, "top")
    assert spec_name_for_file("part_chest_steelplate_arm_up_R_v01.png") == "limb_metal"
    assert spec_name_for_file("part_chest_steelplate_arm_low_L_v01.png") == "limb"
    assert spec_name_for_file("part_chest_vaniacoat_arm_up_L_v01.png") == "limb"
    assert "danger" in s["enemy"].tags


def test_rgb_to_hsv_matches_colorsys():
    import colorsys
    cols = np.array([[[0xe8, 0x9a, 0x55], [0x2f, 0x8a, 0xa0], [0xc2, 0x26, 0x3f], [0x80, 0x80, 0x80], [0, 0, 0]]], np.uint8)
    h, s, v = V.rgb_to_hsv(cols)
    for i, c in enumerate(cols[0]):
        eh, es, ev = colorsys.rgb_to_hsv(*(c / 255))
        assert np.isclose(h[0, i], eh * 360) and np.isclose(s[0, i], es) and np.isclose(v[0, i], ev)


COPPER = {"a": "#e89a55", "b": "#a85a28", "c": "#f6c690"}
SOLID = ["aaaaaaaaaa", "abbbbbbbba", "abbbbbbbba", "abccccccba", "abbbbbbbba", "aaaaaaaaaa"]


def test_material_copper_pass_and_fail():
    good = icon(SOLID, COPPER)
    c = V.check_material_color(good, ICON, "copper")
    assert c.ok and c.elim and c.data["ratio"] >= 0.6, c.detail
    blue = icon(SOLID, {"a": "#2d4a7a", "b": "#3a5fa0", "c": "#f6c690"})          # "cobre" azul
    c = V.check_material_color(blue, ICON, "copper")
    assert not c.ok and c.data["forbidden_px"] and c.data["coords"] == "top-left"
    x, y = c.data["forbidden_px"][0]
    assert blue[y, x, 3] == 255 and tuple(blue[y, x, :3]) in {hex_to_rgb("#2d4a7a"), hex_to_rgb("#3a5fa0")}
    # sem material: ignorado; material sem regra: ignorado
    assert V.check_material_color(good, ICON, None).skipped
    assert V.check_material_color(good, ICON, "stone").skipped
    # a spec também pode declarar o material
    assert not V.check_material_color(blue, ICON.model_copy(update={"material": "copper"})).ok


def test_material_iron_soul_wood():
    stone = "#45475c"                                    # escuro-neutro: base, não entra nos destaques
    iron = icon(["sssssssss", "sbbbbbbbs", "sbcccccbs", "sbbbbbbbs", "sssssssss"],
                {"s": stone, "b": "#b3b9d0", "c": "#6b7089"})
    assert V.check_material_color(iron, ICON, "iron").ok
    rusty = icon(["sssssssss", "sbbbbbbbs", "sbcccccbs", "sbbbbbbbs", "sssssssss"],
                 {"s": stone, "b": "#b3b9d0", "c": "#e89a55"})          # cobre claro no "ferro"
    c = V.check_material_color(rusty, ICON, "iron")
    assert not c.ok and len(c.data["forbidden_px"]) == 5
    soul = icon(["sssssssss", "sbbbbbbbs", "sbcccccbs", "sbbbbbbbs", "sssssssss"],
                {"s": stone, "b": "#2f8aa0", "c": "#5fd0d8"})
    assert V.check_material_color(soul, ICON, "soul").ok
    dim = icon(["sssssssss", "sbbbbbbbs", "sbbbbbbbs", "sssssssss"], {"s": stone, "b": "#2f8aa0"})   # V 63%
    c = V.check_material_color(dim, ICON, "soul")
    assert not c.ok and "brilho" in c.detail
    gold = icon(["sssssssss", "sbbbbbbbs", "sgggggggs", "sbbbbbbbs", "sssssssss"],
                {"s": stone, "b": "#5fd0d8", "g": "#e8b45a"})            # ouro em 30% dos destaques
    c = V.check_material_color(gold, ICON, "soul")
    assert not c.ok and c.data["gold_ratio"] > 0.15 and c.data["gold_px"]
    wood = icon(SOLID, {"a": "#6f4f36", "b": "#8f6a48", "c": "#7b5c45"})
    assert V.check_material_color(wood, ICON, "wood").ok
    assert not V.check_material_color(wood, ICON, "iron").ok


def test_thickness_solid_dotted_and_thin():
    good = icon(SOLID, COPPER)
    c = V.check_thickness(good, ICON)
    assert c.ok and c.elim and c.data["blocks"] >= 3
    # pontilhado: nenhuma cor encosta em si mesma (nem na diagonal)
    dotted = icon(["abababab", "cdcdcdcd", "abababab", "cdcdcdcd"],
                  {"a": "#e89a55", "b": "#a85a28", "c": "#f6c690", "d": "#7a3a18"})
    c = V.check_thickness(dotted, ICON)
    assert not c.ok and c.data["lonely_ratio"] > 0.15 and c.data["lonely_px"]
    x, y = c.data["lonely_px"][0]
    assert dotted[y, x, 3] == 255
    # brilhos declarados não contam como soltos
    spark = ICON.model_copy(update={"sparkle_colors": ["#e89a55", "#a85a28", "#f6c690", "#7a3a18"]})
    assert V.check_thickness(dotted, spark).data["lonely"] == 0
    thin = icon(["a", "a", "a", "a", "a", "a", "a", "a", "a", "a", "a"], {"a": "#e89a55"})
    c = V.check_thickness(thin, ICON)
    assert not c.ok and c.data["blocks"] == 0 and "fino" in c.detail
    # parte do personagem: só aviso
    assert not V.check_thickness(thin, SPECS["limb"].model_copy(update={"canvas": (16, 16)})).elim
    # cabo diagonal de 2 px (cores em faixas diagonais) + cabeça sólida passa
    pick = icon(["aaaaaaaa", "abbbbbba", "aaaaaaaa", "....cd", "...cd", "..cd", ".cd", "cd"],
                {"a": "#e89a55", "b": "#a85a28", "c": "#8f6a48", "d": "#6f4f36"})
    assert V.check_thickness(pick, ICON).ok


def test_color_rules_crimson_and_gold_need_tags():
    crim = icon(SOLID, {"a": "#c2263f", "b": "#7e1a2f", "c": "#ff8a96"})
    c = V.check_color_rules(crim, ICON, [])
    assert not c.ok and c.elim and len(c.data["crimson_px"]) == int(((crim[..., 3] > 0) & ~(crim[..., :3] == hex_to_rgb(K)).all(-1)).sum())
    x, y = c.data["crimson_px"][0]
    assert tuple(crim[y, x, :3]) != hex_to_rgb(K)
    for tag in ("danger", "blood", "castle", "vania", "life"):
        assert V.check_color_rules(crim, ICON, [tag]).ok
    assert not V.check_color_rules(crim, ICON, ["noble"]).ok
    gold = icon(SOLID, {"a": "#e8b45a", "b": "#b8863a", "c": "#45475c"})
    c = V.check_color_rules(gold, ICON)
    assert not c.ok and c.data["gold_px"] and not c.data["crimson_px"]
    for tag in ("soul", "reward", "noble", "fire", "light"):
        assert V.check_color_rules(gold, ICON, [tag.upper()]).ok
    assert V.check_color_rules(gold, ICON.model_copy(update={"tags": ["reward"]})).ok
    # o contorno #0b0a12 e cores neutras não contam
    assert V.check_color_rules(icon(SOLID, {"a": "#45475c", "b": "#5a5d7a", "c": "#b3b9d0"}), ICON).ok


def test_color_rules_soul_tag_allows_crimson_of_offensive_soul():
    # alma ofensiva: carmim é a cor do tipo; a tag soul basta (sem danger)
    crim = icon(SOLID, {"a": "#c2263f", "b": "#7e1a2f", "c": "#ff8a96"})
    c = V.check_color_rules(crim, ICON, ["soul"])
    assert c.ok and c.data["crimson"] > 0, c.detail
    assert not V.check_color_rules(crim, ICON, ["reward"]).ok   # ouro de recompensa não libera carmim


def test_validate_runs_new_checks_with_material_and_tags():
    img = icon(SOLID, COPPER)
    rep = V.validate(img, ICON, name="icon_ore_copper_v01.png", material="copper")
    ids = {c.id for c in rep.checks}
    assert {"material_color", "thickness", "color_rules"} <= ids and rep.ok, rep.to_markdown()
    rep = V.validate(img, ICON, material="iron")
    assert not rep.ok and "material_color" in rep.summary()["failed"]
    rep = V.validate(icon(SOLID, {"a": "#c2263f", "b": "#7e1a2f", "c": "#ff8a96"}), ICON, tags=["life"])
    assert rep.ok, rep.to_markdown()


def test_pose_classes_compare_only_same_class():
    base = pm.render(limb_map())
    a = arr(base).copy()

    def step(x, src, dst):                             # recolore uma cor (≈ 13% do corpo)
        y = x.copy()
        y[(x[..., :3] == src).all(-1) & (x[..., 3] > 0), :3] = dst
        return y

    b = step(a, hex_to_rgb("#4f0f1e"), hex_to_rgb("#a8324a"))
    c = a.copy()
    c[..., :3] = 255 - c[..., :3]                      # "golpe": outra cor em tudo
    c[..., 3] = a[..., 3]
    d = step(c, tuple(255 - v for v in hex_to_rgb("#4f0f1e")), tuple(255 - v for v in hex_to_rgb("#a8324a")))
    flat = {x.id: x for x in V.check_pose_set([a, b, c, d])}
    assert not flat["pose_change"].ok and not flat["pose_palette"].ok
    cls = {x.id: x for x in V.check_pose_set([a, b, c, d], classes=["loc", "loc", "atk", "atk"])}
    assert cls["pose_change"].ok and cls["pose_palette"].ok, (cls["pose_change"].detail, cls["pose_palette"].detail)
    assert [(p["from"], p["to"]) for p in cls["pose_change"].data["pairs"]] == [(0, 1), (2, 3)]
    as_dict = {x.id: x for x in V.check_pose_set([a, b, c, d], classes={"loc": [0, 1], "atk": [2, 3]})}
    assert as_dict["pose_change"].ok and as_dict["pose_palette"].ok
    assert V.pose_class_groups(3, {"voo": [0, 2]}) == {"voo": [0, 2], "#1": [1]}
    with pytest.raises(ValueError):
        V.check_pose_set([a, b], classes=["x"])
    # voador: centro de massa só dentro da classe
    up = np.zeros_like(a)
    up[:-3] = a[3:]                                    # 3 px acima (sem dar a volta)
    assert not {x.id: x for x in V.check_pose_set([a, up], mode="flyer")}["center_of_mass"].ok
    assert {x.id: x for x in V.check_pose_set([a, up], mode="flyer", classes=["voo", "repouso"])}["center_of_mass"].ok


def test_cumulative_only_dark_core():
    core, hi = (11, 10, 18, 255), (154, 150, 184, 255)
    s1 = np.zeros((16, 16, 4), np.uint8)
    s1[8, 4:8] = core
    s1[7, 5] = hi
    s2 = s1.copy()
    s2[9, 7:11] = core
    s2[7, 5] = 0                                       # o reflexo some: permitido
    s2[8, 4] = hi                                      # ...mas o núcleo vira reflexo: não
    c = V.check_cumulative([s1, s2])
    assert not c.ok and c.data["lost"][0]["px"] == [[4, 8]]
    s2[8, 4] = core
    s2[6, 9] = hi                                      # reflexo novo em outro lugar
    c = V.check_cumulative([s1, s2])
    assert c.ok and c.data["core_colors"] == ["#0b0a12"], c.detail
    assert not V.check_cumulative([s1, s1]).ok         # estágio repetido
    assert not V.check_cumulative([s1, s2], core_colors=["#9a96b8"]).ok   # núcleo declarado = o reflexo


def test_pipelines_store_material_and_tags(project, tmp_path):
    params = {"map": limb_map(), "spec": {"base": "icon", "canvas": [8, 12], "coverage": None, "palette_group": "teste/carmim"},
              "material": "copper", "tags": TAGS}
    av = run_pipeline(project, "pixel.map", params)
    assert av.extra["pixel"]["material"] == "copper" and av.extra["pixel"]["tags"] == TAGS
    assert "material_color" in av.extra["validation"]["checks"]
    # edição herda material e tags
    av2 = run_pipeline(project, "pixel.map", {"input_asset": av.ref})
    assert av2.extra["pixel"]["material"] == "copper" and av2.extra["pixel"]["tags"] == TAGS
    rep = validate_asset(project, av.ref)
    assert rep.get("material_color").data["material"] == "copper" and rep.get("color_rules").data["tags"] == sorted(TAGS)
    assert not validate_asset(project, av.ref, tags=[]).get("color_rules").ok
    src = tmp_path / "render.png"
    ai_render().save(src)
    av3 = run_pipeline(project, "pixel.fit", {"input_asset": str(src), "spec": "limb", "palette_group": "teste/carmim",
                                              "material": "leather", "tags": ["vania"]})
    assert av3.extra["pixel"]["material"] == "leather" and av3.extra["pixel"]["tags"] == ["vania"]


# ---------------------------------------------------------------- fit

def ai_render(size=512, kind="limb"):
    """Imitação de um render de IA sem fundo: forma grande com antisserrilhado e contorno escuro grosso."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "limb":
        d.rounded_rectangle([200, 40, 320, 470], 30, fill=(20, 18, 25, 255))
        d.rounded_rectangle([212, 52, 308, 458], 24, fill=(130, 30, 50, 255))
        d.rectangle([212, 400, 308, 458], fill=(230, 180, 90, 255))
    else:
        d.ellipse([60, 60, 450, 450], fill=(20, 18, 25, 255))
        d.ellipse([75, 75, 435, 435], fill=(170, 50, 75, 255))
        d.ellipse([150, 120, 260, 220], fill=(250, 250, 250, 255))
    return img.resize((size // 2, size // 2), Image.Resampling.LANCZOS)    # bordas semitransparentes


@pytest.mark.parametrize("spec_name", ["limb", "hand", "torso", "head", "pelvis", "cape", "back", "boot", "icon"])
def test_fit_to_spec_exact_canvas_pivot_palette(spec_name):
    spec = SPECS[spec_name].model_copy(update={"palette_group": "teste/carmim", "tags": TAGS})
    out = fit_to_spec(ai_render(kind="limb" if spec_name in ("limb", "hand", "cape", "back", "boot") else "round"), spec, PAL)
    assert out.size == spec.canvas
    rep = V.validate(out, spec, PAL)
    assert rep.ok, rep.to_markdown()
    # determinístico
    out2 = fit_to_spec(ai_render(kind="limb" if spec_name in ("limb", "hand", "cape", "back", "boot") else "round"), spec, PAL)
    assert out.tobytes() == out2.tobytes()


def test_fit_keys_solid_background_and_tile():
    img = Image.new("RGB", (300, 300), (0, 200, 0))
    ImageDraw.Draw(img).ellipse([50, 50, 250, 250], fill=(130, 30, 50))
    spec = SPECS["icon"].model_copy(update={"palette_group": "teste/carmim", "tags": TAGS})
    out = fit_to_spec(img, spec, PAL)
    assert V.validate(out, spec, PAL).ok
    rng = np.random.default_rng(1)
    noise = Image.fromarray(rng.integers(40, 90, (128, 128, 3), dtype=np.uint8), "RGB")
    tspec = SPECS["tile"].model_copy(update={"palette_group": "teste/pedra", "tileable": False})
    t = fit_to_spec(noise, tspec, PAL)
    r = V.validate(t, tspec, PAL)
    assert t.size == (16, 16) and r.ok, r.to_markdown()


# ---------------------------------------------------------------- prévia

def test_previews():
    spec = SPECS["limb"]
    z = zoom_preview(good_limb(), spec, scale=12)
    assert z.size == (96, 144) and z.mode == "RGB"
    # pivô (4, 10) a partir de baixo -> pixel (48, 24) da prévia é o centro dourado
    assert z.getpixel((48, 24)) == hex_to_rgb("#e8b45a")
    s = side_by_side(good_limb(), [good_limb()], scale=8)
    assert s.size == (4 + 2 * (8 * 8 + 4), 12 * 8 + 8)
    assert contact_sheet([(good_limb(), "a"), (good_limb(), "b")], scale=4, columns=2).width > 0


# ---------------------------------------------------------------- pipelines

def test_pixel_map_pipeline_outputs_and_determinism(project):
    params = {"map": limb_map(), "spec": "limb", "palette_group": "teste/carmim", "side": "both",
              "file_name": "part_test_arm_up_R_v01.png", "tags": TAGS}
    av = run_pipeline(project, "pixel.map", params)
    store = project.store
    roles = sorted(f.role for f in av.files)
    assert roles.count("primary") == 1 and "part" in roles and "source" in roles and "preview" in roles
    assert roles.count("report") == 2
    prim = store.files_of(av, "primary")[0]
    with Image.open(prim) as im:
        assert im.size == (8, 12)
    assert av.kind == "part"
    assert av.extra["validation"]["ok"] and av.extra["validation_L"]["ok"]
    assert av.extra["spec"]["name"] == "limb"
    rep = json.loads(next(p for p in store.files_of(av, "report") if p.suffix == ".json").read_text(encoding="utf-8"))
    assert rep["ok"] and rep["L"]["side"] == "L"
    av2 = run_pipeline(project, "pixel.map", params)
    assert prim.read_bytes() == store.files_of(av2, "primary")[0].read_bytes()
    # edição: sem map, reaproveita o pixelmap.json e a spec do asset (mesmo asset, nova versão)
    av3 = run_pipeline(project, "pixel.map", {"input_asset": av.ref, "side": "R"})
    assert av3.slug == av.slug and av3.version == av.version + 1
    assert store.files_of(av3, "primary")[0].read_bytes() == prim.read_bytes()


def test_pixel_map_quantizes_off_palette_and_rejects_wrong_canvas(project):
    m = limb_map()
    m["legend"]["C"] = "#800020"
    av = run_pipeline(project, "pixel.map", {"map": m, "spec": "limb", "palette_group": "teste/carmim", "tags": TAGS})
    assert av.extra["validation"]["ok"]
    assert av.extra["pixel"]["quantized"] == {"#800020": "#7e1a2f"}
    from forge_core.runner import PipelineError  # noqa: F401
    with pytest.raises(ValueError, match="exige 20x20"):
        run_pipeline(project, "pixel.map", {"map": limb_map(), "spec": "head"})


def test_pixel_fit_pipeline_and_validate_api(project, tmp_path):
    src = tmp_path / "render.png"
    ai_render().save(src)
    av = run_pipeline(project, "pixel.fit", {"input_asset": str(src), "spec": "limb", "palette_group": "teste/carmim",
                                             "file_name": "part_test_shin_R_v01.png", "tags": TAGS})
    assert av.extra["validation"]["ok"], av.extra["validation"]
    srcmap = project.store.files_of(av, "source")[0]
    m = pm.parse(srcmap.read_text(encoding="utf-8"))
    assert (m.width, m.height) == (8, 12)
    rep = validate_asset(project, av.ref)
    assert rep.ok and rep.spec == "limb" and rep.get("name").ok
    bad = validate_asset(project, av.ref, spec="head")
    assert not bad.ok and not bad.get("canvas").ok
    # par L/R via referências
    lfile = tmp_path / "part_test_shin_L_v01.png"
    pm.shade_side(Image.open(project.store.files_of(av, "primary")[0]), 0.78).save(lfile)
    rep_l = validate_asset(project, str(lfile), spec={"base": "limb", "palette_group": "teste/carmim"},
                           references=[av.ref], tags=TAGS)
    assert rep_l.get("lr_pair") is not None and rep_l.get("lr_pair").ok and rep_l.ok, rep_l.to_markdown()


def test_project_without_palette_skips_palette_check(tmp_path):
    p = Project.create(tmp_path / "nopal")
    av = run_pipeline(p, "pixel.map", {"map": limb_map(), "spec": "limb"})
    assert av.extra["validation"]["checks"]["palette"] == "skip"
    assert av.extra["pixel"]["palette"] == "fallback"


def test_body_coherence_ignores_outside_box():
    import numpy as np

    from forge_pixel import validators as V

    base = np.zeros((24, 32, 4), np.uint8)
    base[6:19, 14:22] = (120, 40, 60, 255)        # corpo
    wing_up, wing_down = base.copy(), base.copy()
    wing_up[2:6, 0:12] = (30, 30, 50, 255)        # asas fora da caixa
    wing_down[16:22, 22:32] = (30, 30, 50, 255)
    checks = {c.id: c for c in V.check_body_coherence([wing_up, wing_down], (13, 4, 22, 18))}
    assert all(c.ok for c in checks.values()), [c.detail for c in checks.values()]
    shrunk = base.copy()
    shrunk[6:19, 14:22] = 0
    shrunk[12:19, 16:20] = (120, 40, 60, 255)
    checks = {c.id: c for c in V.check_body_coherence([base, shrunk], (13, 4, 22, 18))}
    assert not checks["body_mask"].ok and not checks["body_head"].ok


def test_water_translucent_alpha_rule():
    import numpy as np

    from forge_pixel import validators as V
    from forge_pixel.spec import default_specs, spec_name_for_file

    specs = default_specs()
    assert spec_name_for_file("tile_catacumba_water_v01.png") == "tile_water"
    assert spec_name_for_file("tile_catacumba_water_surface_v01.png") == "tile_water_surface"
    ok = np.zeros((16, 16, 4), np.uint8)
    ok[..., :3] = (40, 60, 90)
    ok[..., 3] = 160
    ok[0, :, 3] = 192                                   # brilho da superfície
    assert V.check_alpha(ok, specs["tile_water"]).ok
    holed = ok.copy()
    holed[5, 5, 3] = 0                                  # pixel vazio
    assert not V.check_alpha(holed, specs["tile_water"]).ok
    many = ok.copy()
    many[1, :5, 3] = [161, 162, 163, 164, 165]          # níveis demais
    assert not V.check_alpha(many, specs["tile_water"]).ok
    solid = ok.copy()
    solid[2, 2, 3] = 255                                # fora da faixa 160..192
    assert not V.check_alpha(solid, specs["tile_water"]).ok
