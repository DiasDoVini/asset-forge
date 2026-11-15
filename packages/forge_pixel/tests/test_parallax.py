"""parallax.py (camadas de fundo que repetem em x), check_tileable_x, check_parallax_stack, presets de
spec e o pipeline pixel.parallax."""
import numpy as np
import pytest
from PIL import Image

from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_pixel import parallax as P
from forge_pixel import pixelmap as pmod
from forge_pixel import validators as V
from forge_pixel.spec import PaletteSet, default_specs, spec_name_for_file

COLS = ["#101010", "#202020", "#303030", "#404040", "#505050", "#7e1a2f"]


# ---------------------------------------------------------------- geradores periódicos

def test_periodic_profile_is_seamless_and_deterministic():
    p = P.periodic_profile(640, 7)
    assert p.shape == (640,)
    assert np.array_equal(p, P.periodic_profile(640, 7))
    assert not np.array_equal(p, P.periodic_profile(640, 8))
    # a "volta" (última -> primeira) é um passo comum, não um salto
    steps = np.abs(np.diff(p))
    assert abs(p[0] - p[-1]) <= steps.max() + 1e-9
    # ciclos fracionários quebrariam a emenda: recusados
    with pytest.raises(ValueError):
        P.periodic_profile(64, 1, ((1.5, 1.0),))


def test_periodic_scatter_one_per_slot():
    xs = P.periodic_scatter(640, 8, 3)
    assert len(xs) == 8 and all(0 <= x < 640 for x in xs)
    assert xs == P.periodic_scatter(640, 8, 3)
    assert P.periodic_scatter(640, 8, 3, jitter=0.0) == [40 + 80 * i for i in range(8)]
    assert P.periodic_scatter(640, 0, 3) == []


def test_banded_gradient_bands_and_wobble():
    g = P.banded_gradient(30, 8, [(0, 0), (10, 1), (20, 2)])
    assert (g[:10] == 0).all() and (g[10:20] == 1).all() and (g[20:] == 2).all()
    wob = np.array([0, 2, 0, -2, 0, 2, 0, -2])
    g2 = P.banded_gradient(30, 8, [(0, 0), (10, 1)], wob)
    assert g2[11, 1] == 0 and g2[12, 1] == 1 and g2[8, 3] == 1


# ---------------------------------------------------------------- tela circular

def test_canvas_wraps_x_and_keeps_binary_alpha():
    cv = P.LayerCanvas(32, 8, COLS)
    cv.rect(30, 2, 4, 2, "#303030")              # atravessa a borda direita
    a = np.asarray(cv.image())
    assert (a[2:4, 30:32, 3] == 255).all() and (a[2:4, 0:2, 3] == 255).all()
    assert set(np.unique(a[..., 3])) <= {0, 255}
    cv.poly([(28, 7), (36, 7), (32, 4)], "#202020")
    assert cv.idx[6, 0] == cv.ci("#202020") and cv.idx[6, 31] == cv.ci("#202020")
    with pytest.raises(KeyError):
        cv.ci("#123456")
    cv2 = P.LayerCanvas(16, 16, COLS, fill="#101010")
    cv2.disc(8, 8, 3, "#505050")
    cv2.disc(8, 8, 6, "#404040", only=["#101010"])     # halo não cobre o disco
    assert cv2.idx[8, 8] == cv2.ci("#505050") and cv2.idx[8, 3] == cv2.ci("#404040")
    assert cv2.used_colors() == ["#101010", "#404040", "#505050"]


def _random_layer(seed=1, w=128, h=48):
    cv = P.LayerCanvas(w, h, COLS, fill="#101010")
    tops = 24 + np.round(P.periodic_profile(w, seed) * 8)
    cv.silhouette(tops, "#303030")
    for x in P.periodic_scatter(w, 6, seed):
        cv.rect(x, 10, 5, 30, "#505050")
    return cv.image()


def test_check_tileable_x_passes_periodic_and_fails_breaks():
    img = _random_layer()
    c = V.check_tileable_x(img)
    assert c.ok, c.detail
    assert c.data["alpha_mismatch_rows"] == 0
    # rampa horizontal: cada coluna um tom, a emenda salta de claro para escuro
    a = np.zeros((48, 128, 4), np.uint8)
    a[..., :3] = np.linspace(0, 255, 128).astype(np.uint8)[None, :, None]
    a[..., 3] = 255
    assert not V.check_tileable_x(a).ok
    # silhueta que não fecha: metade direita alta, esquerda baixa
    b = np.asarray(img).copy()
    b[:, 120:, :3] = 255
    assert not V.check_tileable_x(b).ok


def _flat(color, w=32, h=32, noise=None):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., :3] = color
    a[..., 3] = 255
    if noise is not None:
        for (y, x), c in noise:
            a[y, x, :3] = c
    return a


def test_check_parallax_stack_rules():
    rng = np.random.default_rng(0)
    solid = np.zeros((16, 16, 4), np.uint8)
    solid[..., 3] = 255
    solid[..., :3] = np.array([[0x3a, 0x3b, 0x4d], [0x5a, 0x5d, 0x7a], [0x27, 0x28, 0x36]])[rng.integers(0, 3, (16, 16))]

    def layer(dark, light, frac):
        a = _flat(dark, 40, 40)
        n = int(1600 * frac)
        a.reshape(-1, 4)[:n, :3] = light
        return a
    far = layer((0x1f, 0x20, 0x29), (0x24, 0x25, 0x36), 0.10)
    mid = layer((0x1f, 0x20, 0x29), (0x27, 0x28, 0x36), 0.25)
    near = layer((0x1f, 0x20, 0x29), (0x31, 0x32, 0x43), 0.35)
    c = V.check_parallax_stack({"far": far, "mid": mid, "near": near}, [solid])
    assert c.ok, c.detail
    assert c.data["order"] == ["far", "mid", "near"]
    assert all(v > 0 for m in c.data["margins"].values() for v in m.values())
    # ordem trocada: o distante mais contrastado que o próximo
    bad = V.check_parallax_stack({"far": near, "mid": mid, "near": far}, [solid])
    assert not bad.ok and "não cresce" in bad.detail
    # croma igual entre camadas passa (não decrescente); croma que diminui reprova
    grey_lo = layer((0x20, 0x20, 0x20), (0x28, 0x28, 0x28), 0.10)
    grey_hi = layer((0x20, 0x20, 0x20), (0x40, 0x40, 0x40), 0.30)
    same = V.check_parallax_stack([grey_lo, grey_hi], [solid])
    assert same.ok, same.detail
    assert same.data["layers"]["camada 0"]["chroma"] == same.data["layers"]["camada 1"]["chroma"]
    less = V.check_parallax_stack([layer((0x1f, 0x20, 0x29), (0x24, 0x25, 0x36), 0.10), grey_hi], [solid])
    assert not less.ok and "croma diminui" in less.detail
    # camada mais clara que o sólido
    bright = layer((0x8a, 0x8f, 0xa6), (0xb3, 0xb9, 0xd0), 0.5)
    assert not V.check_parallax_stack([far, bright], [solid]).ok
    # transparência não conta: só os pixels opacos entram na medida
    tr = near.copy()
    tr[:20, :, 3] = 0
    assert V.check_parallax_stack([far, tr], [solid]).data["layers"]["camada 1"]["contrast"] >= 0
    assert not V.check_parallax_stack([], [solid]).ok


# ---------------------------------------------------------------- spec e pranchas

def test_parallax_specs_and_names():
    s = default_specs()
    assert s["parallax"].canvas == (640, 360) and s["parallax"].opaque and s["parallax"].tile_axes == "x"
    assert not s["parallax_open"].opaque and s["parallax_open"].kind == "bg"
    assert spec_name_for_file("bg_castelo_far_v01.png") == "parallax"
    assert spec_name_for_file("bg_castelo_mid_v01.png") == "parallax_open"
    assert spec_name_for_file("bg_caverna_near_v01.png") == "parallax_open"
    assert V.check_name("bg_caverna_near_v01.png", s["parallax"]).ok
    assert not V.check_name("fundo_caverna.png", s["parallax"]).ok


def test_composite_and_scroll_offsets_wrap():
    far = _random_layer(1)
    near = P.LayerCanvas(128, 48, COLS)
    near.rect(0, 0, 4, 48, "#7e1a2f")
    near = near.image()
    assert P.scroll_offsets(100, [0.08, 0.5]) == [-8, -50]
    frames = P.scroll_frames([far, near], [0.08, 0.5], [0, 10])
    assert len(frames) == 2 and frames[0].size == (128, 48)
    # a coluna carmim anda 5 px para a esquerda (0,5 x 10), com volta
    a = np.asarray(frames[1])
    assert tuple(a[0, 123, :3]) == (0x7e, 0x1a, 0x2f)
    t = P.tile_x(far, 2)
    assert t.size == (256, 48) and np.array_equal(np.asarray(t)[:, :128], np.asarray(t)[:, 128:])
    assert P.upscale(far, 2).size == (256, 96)
    assert P.stack_sheet([far, near], ["far", "near"]).width == 128


# ---------------------------------------------------------------- pipeline

PAL = PaletteSet(core=["#0b0a12"], groups={"cenario/a": COLS[:5], "cenario/b": COLS[5:]})


def test_pixel_parallax_pipeline(tmp_path):
    proj = Project.create(tmp_path / "p", pixel={"palette": PAL.model_dump()})
    img = _random_layer(3, 640, 360)
    m = pmod.from_image(img).to_dict()
    tile = _flat((0x5a, 0x5d, 0x7a), 16, 16, [((y, x), (0x3a, 0x3b, 0x4d)) for y in range(16) for x in range(0, 16, 2)])
    tpath = tmp_path / "solid.png"
    Image.fromarray(tile, "RGBA").save(tpath)
    av = run_pipeline(proj, "pixel.parallax", {"map": m, "file_name": "bg_teste_far_v01.png",
                                               "borrow_groups": ["cenario/a"], "solid_assets": [str(tpath)]})
    ex = av.extra
    assert ex["spec"]["name"] == "parallax" and ex["pixel"]["palette_group"] == "fundo/teste"
    assert ex["validation"]["checks"]["tileable_x"] is True
    assert "parallax_stack" in ex
    rep = (proj.store.files_of(av, "report"))
    assert any(f.name == "report.md" for f in rep)
    # cor de outro grupo não emprestado reprova a paleta
    cv = P.LayerCanvas(640, 360, COLS, fill="#7e1a2f")
    av2 = run_pipeline(proj, "pixel.parallax", {"map": pmod.from_image(cv.image()).to_dict(),
                                                "file_name": "bg_teste_far_v01.png", "borrow_groups": ["cenario/a"]})
    assert "palette" in av2.extra["validation"]["failed"]
    with pytest.raises(Exception):
        run_pipeline(proj, "pixel.parallax", {"map": m, "file_name": "bg_teste_far_v01.png",
                                              "borrow_groups": ["nao/existe"]})
