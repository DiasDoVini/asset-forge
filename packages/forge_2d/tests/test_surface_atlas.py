"""Texturas sem emenda, material PBR, normal map de sprite e atlas (sem ComfyUI)."""
import json

import numpy as np
import pytest
from PIL import Image

from forge_2d import atlas as at
from forge_2d import surface as sf


def _noise(w=128, h=96, seed=0):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (h // 8, w // 8, 3)).astype(np.uint8)
    img = Image.fromarray(base).resize((w, h), Image.Resampling.BICUBIC)
    a = np.asarray(img).astype(np.float32)
    a += np.linspace(0, 120, w)[None, :, None]           # gradiente forte: bordas esquerda/direita bem diferentes
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")


def test_seamless_removes_the_seam():
    img = _noise()
    before = sf.seam_score(img)
    out = sf.make_seamless(img, 0.25)
    assert out.size == img.size
    a = np.asarray(out.convert("RGB")).astype(float)
    neighbours = (np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean()) / 2   # vizinhos dentro da imagem
    assert sf.seam_score(out) < before * 0.15
    assert sf.seam_score(out) < neighbours * 2                 # a volta não se distingue de dois vizinhos quaisquer
    only_x = np.asarray(sf.make_seamless(img, 0.25, "x")).astype(float)
    orig = np.asarray(img).astype(float)
    assert np.abs(only_x[:, 0] - only_x[:, -1]).mean() < np.abs(orig[:, 0] - orig[:, -1]).mean() * 0.15   # emenda horizontal resolvida


def test_material_maps_shapes_and_ranges():
    img = _noise(64, 64)
    h = sf.height_from_albedo(img)
    assert h.shape == (64, 64) and 0 <= h.min() and h.max() <= 1
    n = sf.normal_from_height(h, 4.0)
    assert n.shape == (64, 64, 3) and n.dtype == np.uint8
    assert abs(n[..., 2].astype(float).mean() - 128) > 40       # normais apontam para fora (azul)
    flat = sf.normal_from_height(np.zeros((8, 8)), 4.0)
    assert (flat == (128, 128, 255)).all()                      # plano = (0,0,1)
    ndx = sf.normal_from_height(h, 4.0, directx=True)
    assert np.allclose(ndx[..., 1].astype(int), 255 - n[..., 1].astype(int), atol=1)   # DirectX inverte o verde
    assert sf.ao_from_height(h).max() <= 1 and sf.roughness_from_albedo(img).min() >= 0


def test_sprite_normal_bevel_points_outward():
    im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    a = np.zeros((40, 40, 4), np.uint8)
    a[10:30, 10:30] = (200, 80, 80, 255)
    im = Image.fromarray(a, "RGBA")
    n = np.asarray(sf.sprite_normal(im, bevel=5, detail=0, strength=3)).astype(int)
    assert (n[0, 0] == (128, 128, 255, 0)).all()                # fora do sprite: reto e transparente
    assert n[20, 10, 0] < 110 and n[20, 29, 0] > 146            # borda esquerda aponta à esquerda, direita à direita
    assert n[10, 20, 1] > 146 and n[29, 20, 1] < 110            # OpenGL: topo verde alto, base verde baixo
    assert abs(n[20, 20, 0] - 128) < 6 and n[20, 20, 2] > 240   # miolo achatado
    q = np.asarray(sf.sprite_normal(im, bevel=5, detail=0, quantize=8)).astype(int)
    assert len({tuple(p) for p in q[10:30, 10:30, :3].reshape(-1, 3)}) <= 17   # pixel art: poucas direções


def test_maxrects_no_overlap_and_fits():
    rng = np.random.default_rng(3)
    mr = at.MaxRects(256, 256)
    placed = []
    for _ in range(60):
        w, h = int(rng.integers(8, 48)), int(rng.integers(8, 48))
        r = mr.insert(w, h)
        if r:
            placed.append(r)
    assert len(placed) > 30
    for i, a in enumerate(placed):
        assert a.x >= 0 and a.y >= 0 and a.x + a.w <= 256 and a.y + a.h <= 256
        for b in placed[i + 1:]:
            assert a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y


def _sprite(name, color, size=(32, 24), box=(4, 6, 20, 18), anim=None, dur=100):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), color), box[:2])
    t, off = at.trim(im)
    return at.Sprite(name, t, im.size, off, anim=anim, duration=dur)


def test_pack_exports_texturepacker_and_godot(tmp_path):
    sprites = [_sprite("chest", (200, 150, 50, 255)),
               _sprite("hero/000", (255, 0, 0, 255), anim="hero", dur=100),
               _sprite("hero/001", (0, 255, 0, 255), anim="hero", dur=200)]
    pages = at.pack(sprites, max_size=128, padding=2, extrude=1)
    assert len(pages) == 1 and pages[0].size[0] in (32, 64, 128)
    page = np.asarray(pages[0])
    for s in sprites:   # o pixel do canto de cada peça veio da peça certa
        assert tuple(page[s.y, s.x]) == tuple(np.asarray(s.image)[0, 0])
    js = at.texturepacker_json(sprites, pages[0], "atlas.png", 0)
    f = js["frames"]["chest"]
    assert f["trimmed"] and f["spriteSourceSize"]["x"] == 4 and f["sourceSize"] == {"w": 32, "h": 24}
    assert js["meta"]["animations"] == {"hero": ["hero/000", "hero/001"]}
    gd = at.godot_sprite_frames(sprites, ["atlas.png"], "res://sprites/")
    assert gd.startswith('[gd_resource type="SpriteFrames"') and 'path="res://sprites/atlas.png"' in gd
    assert 'name": &"hero"' in gd and 'name": &"chest"' in gd
    assert '"duration": 2.0' in gd and '"speed": 10.0' in gd    # 200 ms = 2 quadros de 100 ms a 10 fps
    assert "margin = Rect2(4, 6, 16, 12)" in gd                  # recorte mantém o tamanho original
    json.dumps(js)


def test_pack_rejects_too_big():
    with pytest.raises(ValueError, match="não cabe"):
        at.pack([_sprite("big", (1, 1, 1, 255), size=(300, 300), box=(0, 0, 300, 300))], max_size=256)
