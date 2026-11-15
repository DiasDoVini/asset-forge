"""Fatiador (forge_pixel.slice) e o pipeline pixel.slice."""
import json

import numpy as np
import pytest
from PIL import Image

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_pixel import modality
from forge_pixel import pixelmap as pm
from forge_pixel import slice as SL
from forge_pixel.spec import PaletteSet, default_specs

K = "#0b0a12"
PAL = PaletteSet.from_dict({"core": [K, "#1a1830", "#e8b45a"],
                            "groups": {"teste/carmim": ["#7e1a2f", "#4f0f1e", "#a8324a"]}})
LIMB = {"format": "asset-forge-pixelmap/1", "width": 8, "height": 12,
        "legend": {"C": "#7e1a2f", "d": "#4f0f1e", "r": "#a8324a", "G": "#e8b45a"},
        "layers": [{"x0": 3, "y_top": 9, "rows": ["dCr"] * 6 + ["GGG"]}], "outline": {"auto": True, "color": K}}


def _source(scale=8, jitter=False):
    """A peça ampliada `scale` vezes sobre fundo branco (como uma geração registrada); jitter = ruído leve."""
    big = pm.render(LIMB).resize((8 * scale, 12 * scale), Image.Resampling.NEAREST)
    bg = Image.new("RGBA", big.size, (255, 255, 255, 255))
    bg.alpha_composite(big)
    a = np.asarray(bg.convert("RGB")).astype(int)
    if jitter:
        rng = np.random.default_rng(1)
        a = np.clip(a + rng.integers(-12, 13, a.shape), 0, 255)
        a[np.all(np.asarray(bg.convert("RGB")) == 255, axis=2)] = 255
    return Image.fromarray(a.astype(np.uint8), "RGB")


def _spec(**kw):
    part = {"name": "limb", "file_name": "part_t_arm_up_R_v02.png", "spec": "limb", "palette_group": "teste/carmim",
            "rect": [0, 0, 64, 96], "tags": ["vania", "noble"]}
    part.update(kw)
    return {"format": "asset-forge-slice/1", "source_size": [64, 96], "parts": [part]}


def test_round_trip_registered_source_is_pixel_exact():
    want = np.asarray(pm.render(LIMB))
    for jitter in (False, True):
        got = SL.slice_image(_source(jitter=jitter), _spec(), PAL, default_specs())[0].image
        assert np.array_equal(np.asarray(got), want)


def test_source_size_rescales_and_target_box():
    # fonte em 4x mas coordenadas em 8x (source_size); o rect da metade de cima cai numa caixa 8x6 do topo
    src = _source(4)
    res = SL.slice_image(src, {**_spec(rect=[0, 0, 64, 48], target=[0, 6, 8, 6]), "source_size": [64, 96]},
                         PAL, default_specs())[0].image
    a = np.asarray(res)
    assert a[6:, :, 3].sum() == 0 or a[7:, :, 3].sum() == 0          # metade de baixo vazia (só contorno na borda)
    assert a[:6, :, 3].any()


def test_erase_fills_from_neighbours_and_polygon_cuts():
    # apaga uma faixa no meio do membro: sem erase_fill fica buraco (vira contorno), com erase_fill não
    erase = [[[24, 40], [48, 40], [48, 56], [24, 56]]]
    hole = SL.slice_image(_source(), _spec(erase=erase, erase_fill=False), PAL, default_specs())[0].image
    full = SL.slice_image(_source(), _spec(erase=erase), PAL, default_specs())[0].image
    assert np.asarray(hole)[6, 3:6, 3].tolist() == [255, 255, 255]   # o buraco ganhou contorno (opaco, preto)
    assert tuple(np.asarray(hole)[6, 4, :3]) == (11, 10, 18)
    assert tuple(np.asarray(full)[6, 4, :3]) != (11, 10, 18)
    cut = SL.slice_image(_source(), _spec(polygon=[[0, 0], [64, 0], [64, 48], [0, 48]]), PAL, default_specs())[0].image
    assert np.asarray(cut)[8:, :, 3].sum() == 0


def test_mask_and_clear_and_colors(tmp_path):
    ref = pm.render({**LIMB, "layers": [{"x0": 3, "y_top": 9, "rows": ["dC"] * 7}]})   # silhueta 2 px de largura
    ref.save(tmp_path / "ref.png")
    res = SL.slice_image(_source(), _spec(mask=str(tmp_path / "ref.png"), clear=[[3, 3, 2, 1]]), PAL,
                         default_specs())[0].image
    a = np.asarray(res)
    fill = (a[..., 3] > 0) & ~np.all(a[..., :3] == (11, 10, 18), axis=2)
    assert not fill[:, 5].any() and fill[2:8, 3:5].all() and not fill[8, 3:5].any()
    only = SL.slice_image(_source(), _spec(colors=["#7e1a2f", "#4f0f1e"]), PAL, default_specs())[0].image
    cols = {tuple(c) for c in np.asarray(only)[..., :3][np.asarray(only)[..., 3] > 0]}
    assert (0xa8, 0x32, 0x4a) not in cols and (0xe8, 0xb4, 0x5a) not in cols
    with pytest.raises(ValueError):
        SL.slice_image(_source(), _spec(colors=["#123456"]), PAL, default_specs())


def test_overlay_draws_on_source():
    ov = SL.overlay(_source(), _spec(polygon=[[0, 0], [64, 0], [64, 96]], erase=[[[1, 1], [9, 1], [9, 9]]]), 2)
    assert ov.size == (128, 192)


@pytest.fixture
def project(tmp_path):
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield Project.create(tmp_path / "p", pixel={"palette": PAL.model_dump()})
    set_registry(None)


def test_pipeline_slice_keeps_source_and_validates(project, tmp_path):
    src = tmp_path / "gen.png"
    _source(jitter=True).save(src)
    av = run_pipeline(project, "pixel.slice", {"input_asset": str(src), "slice": _spec()})
    names = {f.name for f in project.store.files_of(av)}
    assert {"sprite.png", "part_t_arm_up_R_v02.png", "source.png", "slice.json", "overlay.png", "report.json"} <= names
    assert av.extra["pixel"]["file_name"] == "part_t_arm_up_R_v02.png"
    assert av.extra["validation"]["ok"]
    d = project.store.files_of(av, "primary")[0].parent
    assert np.array_equal(np.asarray(Image.open(d / "sprite.png").convert("RGBA")), np.asarray(pm.render(LIMB)))
    assert Image.open(d / "source.png").size == (64, 96)
    assert json.loads((d / "slice.json").read_text(encoding="utf-8"))["parts"][0]["name"] == "limb"
