import json
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from forge_core.export import run_export
from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_2d import modality
from forge_2d.aseprite import AsepriteError, read_aseprite, write_aseprite
from forge_2d import spritesheet as ss


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


def _sq(color, size=(8, 8), at=(2, 2), box=(4, 4)):
    im = Image.new("RGBA", size)
    for y in range(at[1], at[1] + box[1]):
        for x in range(at[0], at[0] + box[0]):
            im.putpixel((x, y), color)
    return im


COLORS = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]


def _sheet(cols=4, rows=2, cell=8, gap=0):
    W, H = cols * cell + (cols - 1) * gap, rows * cell + (rows - 1) * gap
    im = Image.new("RGBA", (W, H))
    for i in range(cols * rows):
        c, r = i % cols, i // cols
        im.paste(_sq(COLORS[i % 4], (cell, cell), (1, 1), (cell - 2, cell - 2)), (c * (cell + gap), r * (cell + gap)))
    return im


# ---------------------------------------------------------------- aseprite

def test_aseprite_roundtrip(tmp_path):
    frames = [_sq(c) for c in COLORS]
    tags = [{"name": "run", "from": 0, "to": 1, "direction": "pingpong"}, {"name": "idle", "from": 2, "to": 3}]
    f = write_aseprite(tmp_path / "a.aseprite", frames, [100, 120, 80, 60], tags)
    ase = read_aseprite(f)
    assert (ase.width, ase.height, len(ase.frames)) == (8, 8, 4)
    assert ase.durations == [100, 120, 80, 60]
    assert [(t.name, t.from_, t.to, t.direction) for t in ase.tags] == [("run", 0, 1, "pingpong"), ("idle", 2, 3, "forward")]
    for i, fr in enumerate(frames):
        assert np.array_equal(np.asarray(ase.render_frame(i)), np.asarray(fr))


def test_aseprite_layers_opacity_hidden(tmp_path):
    bottom, top = _sq((255, 0, 0, 255), at=(0, 0), box=(8, 8)), _sq((0, 0, 255, 255), at=(0, 0), box=(8, 8))
    f = write_aseprite(tmp_path / "l.aseprite", [[("bottom", bottom), ("top", top)]])
    ase = read_aseprite(f)
    assert len(ase.layers) == 2
    assert ase.render_frame(0).getpixel((1, 1)) == (0, 0, 255, 255)
    ase.layers[1].flags = 0   # oculta o de cima
    assert ase.render_frame(0).getpixel((1, 1)) == (255, 0, 0, 255)
    assert ase.render_frame(0, include_hidden=True).getpixel((1, 1)) == (0, 0, 255, 255)
    ase.layers[1].flags, ase.layers[1].opacity = 1, 128
    p = ase.render_frame(0).getpixel((1, 1))
    assert 120 <= p[0] <= 135 and 120 <= p[2] <= 135


def test_aseprite_rejects_garbage(tmp_path):
    with pytest.raises(AsepriteError):
        read_aseprite(b"x" * 200)
    with pytest.raises(AsepriteError):
        read_aseprite(b"short")


def _ase_indexed(path):
    """Constrói à mão um .aseprite indexado 2x2 com cel raw e paleta antiga (0x0004), para validar o leitor."""
    pal = struct.pack("<H", 1) + bytes([0, 3]) + bytes([0, 0, 0, 255, 0, 0, 0, 255, 0])   # idx0 preto, idx1 vermelho, idx2 verde
    pal_chunk = struct.pack("<IH", 6 + len(pal), 0x0004) + pal
    layer = struct.pack("<HHHHHHB3x", 3, 0, 0, 0, 0, 0, 255) + struct.pack("<H", 1) + b"L"
    layer_chunk = struct.pack("<IH", 6 + len(layer), 0x2004) + layer
    cel = struct.pack("<HhhBHh5x", 0, 0, 0, 255, 0, 0) + struct.pack("<HH", 2, 2) + bytes([0, 1, 2, 1])
    cel_chunk = struct.pack("<IH", 6 + len(cel), 0x2005) + cel
    chunks = pal_chunk + layer_chunk + cel_chunk
    frame = struct.pack("<IHHHxxI", 16 + len(chunks), 0xF1FA, 3, 100, 0) + chunks
    header = struct.pack("<IHHHHHIH8xBxxxHBBhhHH84x", 128 + len(frame), 0xA5E0, 1, 2, 2, 8, 1, 100, 0, 3, 1, 1, 0, 0, 16, 16)
    path.write_bytes(header + frame)


def test_aseprite_indexed_old_palette(tmp_path):
    _ase_indexed(tmp_path / "i.aseprite")
    img = read_aseprite(tmp_path / "i.aseprite").render_frame(0)
    assert img.getpixel((0, 0))[3] == 0                      # índice transparente
    assert img.getpixel((1, 0)) == (255, 0, 0, 255)
    assert img.getpixel((0, 1)) == (0, 255, 0, 255)


def test_import_aseprite_animation(project, tmp_path):
    f = write_aseprite(tmp_path / "hero.aseprite", [_sq(c) for c in COLORS], [100, 100, 200, 200],
                       [{"name": "all", "from": 0, "to": 3}])
    av = project.store.import_file(f)
    assert (av.modality, av.kind) == ("2d", "animation")
    assert av.extra["frames"] == 4 and av.extra["import"]["importer"] == "2d.aseprite"
    assert av.extra["aseprite"]["tags"][0]["name"] == "all"
    assert {x.role for x in av.files} >= {"primary", "preview", "texture", "part", "source"}
    out = run_export(project, av.slug, "2d.aseprite", {}, str(tmp_path / "out"))
    again = read_aseprite(out[0])
    assert len(again.frames) == 4 and again.tags[0].name == "all"
    assert again.durations == [100, 100, 200, 200]


def test_import_aseprite_single_frame_is_image(project, tmp_path):
    f = write_aseprite(tmp_path / "icon.aseprite", [_sq(COLORS[0])])
    av = project.store.import_file(f)
    assert av.kind == "image" and av.primary().path == "image.png"
    assert Image.open(project.store.files_of(av, "primary")[0]).getpixel((3, 3)) == COLORS[0]


def test_import_aseprite_tag_option(project, tmp_path):
    f = write_aseprite(tmp_path / "t.aseprite", [_sq(c) for c in COLORS], [100] * 4,
                       [{"name": "a", "from": 0, "to": 1}, {"name": "b", "from": 2, "to": 3}])
    av = project.store.import_file(f, options={"tag": "b"})
    assert av.extra["frames"] == 2
    with pytest.raises(ValueError, match="tag não encontrada"):
        project.store.import_file(f, options={"tag": "zzz"})
    assert not (project.store.root / "t-2").exists()   # falha não deixa asset pela metade


# ---------------------------------------------------------------- gif / formatos

def test_import_animated_gif_and_static_gif(project, tmp_path):
    frames = [_sq(c) for c in COLORS[:3]]
    gif = tmp_path / "blink.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=[50, 70, 90], loop=0, disposal=2)
    av = project.store.import_file(gif)
    assert av.kind == "animation" and av.extra["frames"] == 3
    still = tmp_path / "still.gif"
    frames[0].save(still)
    av2 = project.store.import_file(still)
    assert (av2.kind, av2.primary().path) == ("image", "image.png")      # estático: convertido para PNG


def test_import_apng(project, tmp_path):
    frames = [_sq(c) for c in COLORS[:2]]
    p = tmp_path / "a.png"
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert project.store.import_file(p).kind == "animation"


def test_plain_png_still_raw(project, tmp_path):
    p = tmp_path / "plain.png"
    _sq(COLORS[0]).save(p)
    av = project.store.import_file(p)
    assert (av.kind, av.pipeline) == ("image", "core.import") and av.primary().path == "image.png"
    assert "import" not in av.extra


@pytest.mark.parametrize("ext,fmt", [(".bmp", "BMP"), (".tga", "TGA"), (".tif", "TIFF")])
def test_import_convert_formats(project, tmp_path, ext, fmt):
    p = tmp_path / f"pic{ext}"
    _sq(COLORS[1]).save(p, format=fmt)
    av = project.store.import_file(p)
    assert av.primary().path == "image.png" and av.primary().mime == "image/png"
    assert any(f.role == "source" for f in av.files)


# ---------------------------------------------------------------- folhas

def test_sheet_grid_import(project, tmp_path):
    p = tmp_path / "sheet.png"
    _sheet(4, 2).save(p)
    av = project.store.import_file(p, options={"columns": 4, "rows": 2, "fps": 12})
    assert av.kind == "animation" and av.extra["frames"] == 8 and av.extra["size"] == [8, 8]
    assert av.extra["sheet"]["mode"] == "grid"


def test_sheet_cell_size_and_skip_empty(project, tmp_path):
    im = _sheet(4, 2)
    im.paste(Image.new("RGBA", (8, 8)), (8, 0))     # esvazia a 2ª célula
    p = tmp_path / "s.png"
    im.save(p)
    av = project.store.import_file(p, options={"cell_width": 8, "cell_height": 8})
    assert av.extra["frames"] == 7
    av2 = project.store.import_file(p, options={"cell_width": 8, "cell_height": 8, "skip_empty": False}, slug="s-all")
    assert av2.extra["frames"] == 8


def test_sheet_margin_spacing(project, tmp_path):
    im = Image.new("RGBA", (2 + 3 * 8 + 2 * 2, 2 + 8))
    for i in range(3):
        im.paste(_sq(COLORS[i], (8, 8), (0, 0), (8, 8)), (2 + i * 10, 2))
    p = tmp_path / "m.png"
    im.save(p)
    av = project.store.import_file(p, options={"cell_width": 8, "cell_height": 8, "margin": 2, "spacing": 2})
    assert av.extra["frames"] == 3


def test_sheet_auto_islands(project, tmp_path):
    im = Image.new("RGBA", (40, 20))
    im.paste(_sq(COLORS[0], (6, 6), (0, 0), (6, 6)), (2, 2))
    im.paste(_sq(COLORS[1], (10, 8), (0, 0), (10, 8)), (14, 3))
    im.paste(_sq(COLORS[2], (5, 5), (0, 0), (5, 5)), (30, 12))
    p = tmp_path / "auto.png"
    im.save(p)
    av = project.store.import_file(p, options={"mode": "auto"})
    assert av.extra["frames"] == 3
    assert av.extra["sheet"]["rects"][0] == [2, 2, 6, 6]


def test_sheet_json_sibling_aseprite_format(project, tmp_path):
    from forge_2d.animation import aseprite_json

    sheet = _sheet(3, 1)
    sheet.save(tmp_path / "walk.png")
    rects = [(i * 8, 0, 8, 8) for i in range(3)]
    data = aseprite_json("walk.png", sheet.size, rects, [50, 60, 70], [{"name": "walk", "from": 0, "to": 2}])
    (tmp_path / "walk.json").write_text(json.dumps(data))
    av = project.store.import_file(tmp_path / "walk.png")
    assert av.kind == "animation" and av.extra["frames"] == 3
    assert av.extra["sheet"]["mode"] == "descriptor"


def test_sheet_json_texturepacker_trimmed_rotated():
    sheet = Image.new("RGBA", (16, 16))
    sheet.paste(_sq(COLORS[0], (4, 2), (0, 0), (4, 2)), (0, 0))
    data = {"frames": {"a": {"frame": {"x": 0, "y": 0, "w": 4, "h": 2}, "rotated": False, "trimmed": True,
                              "spriteSourceSize": {"x": 3, "y": 5, "w": 4, "h": 2}, "sourceSize": {"w": 10, "h": 10}}},
            "meta": {"image": "x.png"}}
    fr = ss.descriptor_frames(sheet, ss.parse_descriptor(data))[0]
    assert fr.size == (10, 10) and fr.getpixel((3, 5)) == COLORS[0] and fr.getpixel((0, 0))[3] == 0


def test_unrelated_json_next_to_image_is_ignored(project, tmp_path):
    _sq(COLORS[0]).save(tmp_path / "x.png")
    (tmp_path / "x.json").write_text('{"hello": 1}')
    assert project.store.import_file(tmp_path / "x.png").kind == "image"


def test_detect_grid_gaps_and_contrast():
    g = ss.detect_grid(_sheet(4, 2, cell=16, gap=0))["grid"]
    assert (g["columns"], g["rows"], g["cell_width"], g["cell_height"]) == (4, 2, 16, 16)
    sep = _sheet(3, 2, cell=12, gap=4)
    g2 = ss.detect_grid(sep)["grid"]
    assert (g2["columns"], g2["rows"]) == (3, 2)
    rng = np.random.default_rng(1)   # tileset contínuo: só o contraste nas emendas revela a grade
    tiles = Image.new("RGBA", (64, 32))
    for c in range(4):
        for r in range(2):
            base = rng.integers(0, 255, 3)
            noise = rng.integers(-6, 6, (16, 16, 3))
            arr = np.clip(base + noise, 0, 255).astype(np.uint8)
            tiles.paste(Image.fromarray(np.dstack([arr, np.full((16, 16), 255, np.uint8)]), "RGBA"), (c * 16, r * 16))
    g3 = ss.detect_grid(tiles)["grid"]
    assert (g3["cell_width"], g3["cell_height"]) == (16, 16)


def test_zip_of_frames_and_zip_with_sheet(project, tmp_path):
    z = tmp_path / "frames.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in (10, 2, 1):   # ordem natural: 1, 2, 10
            p = tmp_path / f"walk_{i}.png"
            _sq(COLORS[i % 4]).save(p)
            zf.write(p, f"walk_{i}.png")
    av = project.store.import_file(z, options={"fps": 8})
    assert av.kind == "animation" and av.extra["frames"] == 3
    assert av.extra["frames_from"] == ["walk_1.png", "walk_2.png", "walk_10.png"]
    assert not list(project.store.version_dir(av.slug, av.version).glob("_zip_*"))


def test_bad_options_rejected(project, tmp_path):
    _sheet().save(tmp_path / "s.png")
    with pytest.raises(Exception):
        project.store.import_file(tmp_path / "s.png", options={"columns": 0})
    with pytest.raises(Exception):
        project.store.import_file(tmp_path / "s.png", options={"bogus": 1})


# ---------------------------------------------------------------- 2d.slice

from forge_core.runner import run_pipeline  # noqa: E402


def _import_sheet_asset(project, tmp_path, cols=4, rows=2):
    p = tmp_path / "hero.png"
    _sheet(cols, rows).save(p)
    return project.store.import_file(p, slug="hero", modality="2d", kind="image", folder="Personagens")


def test_slice_grid_creates_sprites_and_animation(project, tmp_path):
    src = _import_sheet_asset(project, tmp_path)
    av = run_pipeline(project, "2d.slice", {"input_asset": src.ref, "mode": "grid",
                                            "grid": {"columns": 4, "rows": 2}, "animation": True})
    assert av.kind == "spriteset" and av.primary().path == "overlay.png"
    assert av.extra["slice"]["count"] == 8 and len(av.extra["slice"]["created"]) == 9
    sprites = [project.store.get(r) for r in av.extra["slice"]["created"]]
    assert sum(1 for s in sprites if s.kind == "image") == 8 and sprites[-1].kind == "animation"
    assert all(s.parent == src.ref for s in sprites)
    assert project.store.get_meta(sprites[0].slug).folder == f"Personagens/{av.slug}"
    first = Image.open(project.store.files_of(sprites[0], "primary")[0])
    assert first.size == (8, 8) and first.getpixel((3, 3)) == COLORS[0]
    parts = [f for f in av.files if f.role == "part"]
    assert len(parts) == 8 and parts[0].path.startswith("parts/001_")


def test_slice_selected_names_and_no_sprites(project, tmp_path):
    src = _import_sheet_asset(project, tmp_path)
    av = run_pipeline(project, "2d.slice", {"input_asset": src.ref, "grid": {"cell_width": 8, "cell_height": 8},
                                            "selected": [5, 1], "names": ["idle", "walk"], "sprites": False})
    assert av.extra["slice"]["names"] == ["idle", "walk"] and av.extra["slice"]["selected"] == [5, 1]
    assert av.extra["slice"]["created"] == []
    assert Image.open(project.store.files_of(av, "part")[0]).getpixel((3, 3)) == COLORS[1]   # índice 5 -> cor 1


def test_slice_rects_trim_and_validation(project, tmp_path):
    src = _import_sheet_asset(project, tmp_path)
    av = run_pipeline(project, "2d.slice", {"input_asset": src.ref, "mode": "rects", "trim": True, "sprites": False,
                                            "rects": [{"x": 0, "y": 0, "w": 8, "h": 8, "name": "a"}]})
    assert Image.open(project.store.files_of(av, "part")[0]).size == (6, 6)       # sem a borda transparente de 1 px
    with pytest.raises(Exception, match="fora da imagem"):
        run_pipeline(project, "2d.slice", {"input_asset": src.ref, "mode": "rects", "rects": [{"x": 30, "y": 0, "w": 8, "h": 8}]})
    with pytest.raises(Exception):
        run_pipeline(project, "2d.slice", {"input_asset": src.ref, "mode": "grid"})
    with pytest.raises(Exception, match="intervalo"):
        run_pipeline(project, "2d.slice", {"input_asset": src.ref, "grid": {"columns": 4, "rows": 2}, "selected": [99]})


def test_slice_auto_and_mixed_sizes_animation(project, tmp_path):
    im = Image.new("RGBA", (40, 20))
    im.paste(_sq(COLORS[0], (6, 6), (0, 0), (6, 6)), (2, 2))
    im.paste(_sq(COLORS[1], (10, 8), (0, 0), (10, 8)), (20, 3))
    p = tmp_path / "mix.png"
    im.save(p)
    src = project.store.import_file(p, slug="mix", modality="2d", kind="sprite")
    av = run_pipeline(project, "2d.slice", {"input_asset": src.ref, "mode": "auto", "sprites": False, "animation": True})
    anim = project.store.get(av.extra["slice"]["created"][0])
    assert anim.kind == "animation" and anim.extra["size"] == [10, 8]      # canvas comum ao maior recorte


def test_aseprite_group_hierarchy_and_opacity():
    """No arquivo o grupo vem ANTES dos filhos; grupo oculto esconde o filho e a opacidade do grupo se aplica."""
    from forge_2d.aseprite import AseCel, AseFile, AseLayer

    ase = AseFile(2, 1, 32)
    ase.layers = [AseLayer("fundo", 1, 0, 0, 0, 255), AseLayer("G", 1, 1, 0, 0, 128), AseLayer("filho", 1, 0, 1, 0, 255)]
    ase.frames = [[AseCel(0, 0, 0, 255, 0, Image.new("RGBA", (2, 1), (255, 0, 0, 255))),
                   AseCel(2, 0, 0, 255, 0, Image.new("RGBA", (2, 1), (0, 0, 255, 255)))]]
    ase.durations = [100]
    assert ase.parents(2) == [1] and ase.parents(0) == []
    r, g, b, a = ase.render_frame(0).getpixel((0, 0))
    assert 120 <= r <= 135 and 120 <= b <= 135
    ase.layers[1].flags = 0
    assert ase.render_frame(0).getpixel((0, 0)) == (255, 0, 0, 255)


def test_aseprite_tilemap_and_slices_from_editor(project, tmp_path):
    """Arquivo gravado pelo editor do navegador: camada de tilemap (tileset embutido), camada comum e fatia 9-slice."""
    from pathlib import Path

    from forge_2d.aseprite import read_aseprite

    fx = Path(__file__).parent / "fixtures" / "tilemap.aseprite"
    ase = read_aseprite(fx)
    assert ase.skipped == []
    assert [ly.type for ly in ase.layers] == [2, 0]
    assert ase.tilesets and next(iter(ase.tilesets.values()))[:2] == (4, 4)
    img = ase.render_frame(0)
    assert img.getpixel((0, 3)) == (255, 0, 0, 255) and img.getpixel((7, 0)) == (0, 0, 255, 255)
    assert img.getpixel((1, 1)) == (0, 255, 0, 255)   # camada comum por cima
    sl = ase.slices[0]
    assert (sl["name"], sl["center"], sl["pivot"], sl["color"]) == ("botao", {"x": 2, "y": 0, "w": 2, "h": 2}, {"x": 3, "y": 1}, "#ff8800")
    av = project.store.import_file(fx)   # 1 quadro: vira imagem com a composição
    assert Image.open(project.store.files_of(av, "primary")[0]).getpixel((5, 2)) == (0, 0, 255, 255)


def test_layer_effects_and_clip_match_the_editor():
    """Efeitos não destrutivos e máscara de recorte: o servidor compõe igual ao editor (pixels gerados pelo navegador)."""
    from pathlib import Path

    from forge_2d.aseprite import read_aseprite

    fx = Path(__file__).parent / "fixtures"
    ase = read_aseprite(fx / "effects.aseprite")
    assert [bool(ly.effects) for ly in ase.layers] == [True, True, True] and ase.layers[1].clip
    got = np.asarray(ase.render_frame(0), np.int16)
    want = np.frombuffer((fx / "effects.rgba").read_bytes(), np.uint8).reshape(got.shape).astype(np.int16)
    assert np.abs(got - want).max() <= 1


FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- Pixelorama / Krita (achatados no servidor)

def test_import_pixelorama_flattens_layers_and_keeps_tags(project, tmp_path):
    from forge_2d.layered import read_pxo

    src = FIXTURES / "sample.pxo"
    frames, durations, tags, _info = read_pxo(src.read_bytes())
    assert durations == [100, 200] and tags[0]["name"] == "walk" and (tags[0]["from"], tags[0]["to"]) == (0, 1)
    r, g, b, a = frames[0].getpixel((0, 0))   # azul a 50% sobre vermelho
    assert abs(r - 128) <= 1 and g == 0 and abs(b - 127) <= 1 and a == 255
    assert frames[1].getpixel((0, 0)) == (255, 0, 0, 255)
    av = project.store.import_file(src)
    assert (av.kind, av.extra["import"]["importer"], av.extra["frames"]) == ("animation", "2d.pixelorama", 2)
    assert "source" in {x.role for x in av.files}


def test_import_krita_uses_merged_image(project):
    av = project.store.import_file(FIXTURES / "sample.kra")
    assert (av.kind, av.extra["import"]["importer"]) == ("image", "2d.krita")
    assert Image.open(project.store.files_of(av, "primary")[0]).getpixel((0, 0)) == (0, 255, 0, 255)


def test_import_psd_still_flattens_on_server(project):
    av = project.store.import_file(FIXTURES / "sample.psd")
    assert av.kind == "image" and av.extra["import"]["importer"] == "2d.image_convert"
