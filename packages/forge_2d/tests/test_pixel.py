import numpy as np
from PIL import Image

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_2d import modality
from forge_2d.pixel import detect_grid, hex_to_rgb, pixelize


def _blocky(cell=8, art=16, bg=(255, 255, 255)):
    """Sprite 16x16 ampliado x`cell` com borrão leve, sobre fundo branco."""
    rng = np.random.default_rng(1)
    art_img = np.full((art, art, 3), bg, np.uint8)
    art_img[3:13, 5:11] = (200, 40, 40)
    art_img[5:8, 6:10] = (30, 30, 160)
    big = np.kron(art_img, np.ones((cell, cell, 1), np.uint8)).astype(np.int16)
    big += rng.integers(-6, 7, big.shape, dtype=np.int16)
    return Image.fromarray(np.clip(big, 0, 255).astype(np.uint8))


def test_detect_grid():
    assert detect_grid(_blocky(8)).cell == 8


def test_pixelize_grid_mode():
    res = pixelize(_blocky(8), mode="grid", colors=2, crop=True)
    assert res.sprite.size == (6, 10)
    a = np.asarray(res.sprite)
    assert set(np.unique(a[..., 3])) <= {0, 255}
    assert res.colors == 2


def test_pixelize_downscale_palette_outline():
    img = _blocky(8).convert("RGBA")
    pal = hex_to_rgb(["#ff0000", "#0000ff", "#000000"])
    res = pixelize(img, size=16, palette=pal, remove_bg=True, outline_color="#000000", crop=True, scale=2)
    colors = {tuple(c) for c in np.asarray(res.sprite)[..., :3][np.asarray(res.sprite)[..., 3] > 0]}
    assert colors <= {(255, 0, 0), (0, 0, 255), (0, 0, 0)}
    assert res.scaled.size == (res.sprite.width * 2, res.sprite.height * 2)


def test_pixelize_pipeline(tmp_path):
    reg = Registry()
    reg.add(modality)
    set_registry(reg)
    try:
        p = Project.create(tmp_path / "proj")
        p.set_value("style.palette", ["#c83228", "#1e1ea0"])
        src = tmp_path / "espada.png"
        _blocky(8).save(src)
        av0 = p.store.import_file(src, slug="espada")
        av = run_pipeline(p, "2d.pixelize", {"input_asset": "espada", "size": 16, "remove_bg": "yes"})
        assert av.ref == "espada@v002" and av.kind == "sprite" and av.parent == av0.ref
        roles = {f.role for f in av.files}
        assert roles == {"primary", "preview", "source"}
        assert av.extra["pixel"]["colors_used"] <= 2
        # pixelizar de novo um sprite não pode sobrescrever o sprite novo com a fonte
        av3 = run_pipeline(p, "2d.pixelize", {"input_asset": "espada", "size": 8})
        assert Image.open(p.store.files_of(av3, "primary")[0]).size[0] <= 8
    finally:
        set_registry(None)
