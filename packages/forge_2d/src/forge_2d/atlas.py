"""`2d.atlas`: empacota vários assets (imagens, sprites e cada quadro das animações) num atlas, com recorte da
transparência, espaçamento e extrusão de borda, e exporta para as engines:

- `atlas.json` no formato JSON-hash do TexturePacker (Unity com o TexturePacker Importer, Phaser, PixiJS, Cocos), com
  as animações em `meta.animations`;
- `atlas.tres`: SpriteFrames do Godot 4 (uma animação por asset animado, com durações; imagens viram animação de 1
  quadro), usando AtlasTexture com margem para manter o tamanho original dos quadros recortados.

Empacotamento MaxRects (melhor encaixe pelo lado curto); várias páginas se não couber em `max_size`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydantic import Field

from forge_core import OutputFile, Pipeline, PipelineParams
from forge_core.pipeline import Context


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int


class MaxRects:
    """MaxRects-BSSF (sem rotação: as engines leem os quadros na orientação original)."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.free = [Rect(0, 0, w, h)]

    def insert(self, w: int, h: int) -> Rect | None:
        best, score = None, (1 << 30, 1 << 30)
        for f in self.free:
            if w <= f.w and h <= f.h:
                s = (min(f.w - w, f.h - h), max(f.w - w, f.h - h))
                if s < score:
                    best, score = Rect(f.x, f.y, w, h), s
        if best is None:
            return None
        new_free = []
        for f in self.free:
            if best.x >= f.x + f.w or best.x + best.w <= f.x or best.y >= f.y + f.h or best.y + best.h <= f.y:
                new_free.append(f)
                continue
            if best.x > f.x:
                new_free.append(Rect(f.x, f.y, best.x - f.x, f.h))
            if best.x + best.w < f.x + f.w:
                new_free.append(Rect(best.x + best.w, f.y, f.x + f.w - best.x - best.w, f.h))
            if best.y > f.y:
                new_free.append(Rect(f.x, f.y, f.w, best.y - f.y))
            if best.y + best.h < f.y + f.h:
                new_free.append(Rect(f.x, best.y + best.h, f.w, f.y + f.h - best.y - best.h))
        # remove retângulos contidos em outros (de duplicados, fica o primeiro)
        pruned = []
        for i, a in enumerate(new_free):
            contained = False
            for j, b in enumerate(new_free):
                if i == j or not (a.x >= b.x and a.y >= b.y and a.x + a.w <= b.x + b.w and a.y + a.h <= b.y + b.h):
                    continue
                if (a.x, a.y, a.w, a.h) == (b.x, b.y, b.w, b.h) and i < j:
                    continue
                contained = True
                break
            if not contained:
                pruned.append(a)
        self.free = pruned
        return best


@dataclass
class Sprite:
    name: str
    image: Image.Image          # já recortado
    source_size: tuple[int, int]
    offset: tuple[int, int]     # posição do recorte dentro do quadro original
    anim: str | None = None
    duration: int = 100
    page: int = 0
    x: int = 0
    y: int = 0


def trim(img: Image.Image) -> tuple[Image.Image, tuple[int, int]]:
    a = np.asarray(img.convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(a > 0)
    if len(xs) == 0:
        return img.crop((0, 0, 1, 1)), (0, 0)
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return img.crop(box), (box[0], box[1])


def _extrude(page: Image.Image, s: Sprite, n: int) -> None:
    """Repete os pixels da borda `n` vezes para fora (evita linhas de outra peça ao filtrar/escalar)."""
    if n <= 0:
        return
    w, h = s.image.size
    x, y = s.x, s.y
    for i in range(1, n + 1):
        page.paste(s.image.crop((0, 0, w, 1)), (x, y - i))
        page.paste(s.image.crop((0, h - 1, w, h)), (x, y + h - 1 + i))
        page.paste(s.image.crop((0, 0, 1, h)), (x - i, y))
        page.paste(s.image.crop((w - 1, 0, w, h)), (x + w - 1 + i, y))


def _pot(v: int) -> int:
    p = 1
    while p < v:
        p <<= 1
    return p


def pack(sprites: list[Sprite], max_size: int = 4096, padding: int = 2, extrude: int = 1,
         power_of_two: bool = True) -> list[Image.Image]:
    """Posiciona os sprites (maiores primeiro) e devolve as páginas."""
    gap = padding + 2 * extrude
    order = sorted(sprites, key=lambda s: (max(s.image.size), s.image.size[0] * s.image.size[1]), reverse=True)
    for s in order:
        if s.image.width + gap > max_size or s.image.height + gap > max_size:
            raise ValueError(f"{s.name} ({s.image.width}×{s.image.height}) não cabe numa página de {max_size} px")
    pages: list[MaxRects] = []
    used: list[tuple[int, int]] = []
    for s in order:
        w, h = s.image.width + gap, s.image.height + gap
        for i, pg in enumerate(pages):
            r = pg.insert(w, h)
            if r:
                break
        else:
            pages.append(MaxRects(max_size, max_size))
            used.append((0, 0))
            i = len(pages) - 1
            r = pages[i].insert(w, h)
        assert r is not None
        s.page, s.x, s.y = i, r.x + extrude + padding // 2, r.y + extrude + padding // 2
        used[i] = (max(used[i][0], r.x + r.w), max(used[i][1], r.y + r.h))
    images = []
    for i, (uw, uh) in enumerate(used):
        W, H = (_pot(uw), _pot(uh)) if power_of_two else (uw, uh)
        page = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for s in sprites:
            if s.page == i:
                page.paste(s.image, (s.x, s.y))
                _extrude(page, s, extrude)
        images.append(page)
    return images


def texturepacker_json(sprites: list[Sprite], page: Image.Image, page_name: str, index: int) -> dict[str, Any]:
    frames = {}
    for s in sprites:
        if s.page != index:
            continue
        w, h = s.image.size
        frames[s.name] = {"frame": {"x": s.x, "y": s.y, "w": w, "h": h}, "rotated": False,
                          "trimmed": (w, h) != s.source_size,
                          "spriteSourceSize": {"x": s.offset[0], "y": s.offset[1], "w": w, "h": h},
                          "sourceSize": {"w": s.source_size[0], "h": s.source_size[1]}, "duration": s.duration}
    anims: dict[str, list[str]] = {}
    for s in sprites:
        if s.anim and s.page == index:
            anims.setdefault(s.anim, []).append(s.name)
    return {"frames": frames, "meta": {"app": "Asset Forge", "version": "1.0", "image": page_name, "format": "RGBA8888",
                                       "size": {"w": page.width, "h": page.height}, "scale": "1", "animations": anims}}


def _gd_name(s: str) -> str:
    return re.sub(r'["\\]', "_", s)


def godot_sprite_frames(sprites: list[Sprite], page_names: list[str], res_dir: str = "res://") -> str:
    """SpriteFrames (Godot 4, formato texto). Quadros recortados mantêm o tamanho original via AtlasTexture.margin."""
    lines = [f'[gd_resource type="SpriteFrames" load_steps={len(page_names) + len(sprites) + 1} format=3]', ""]
    for i, name in enumerate(page_names):
        lines.append(f'[ext_resource type="Texture2D" path="{res_dir}{name}" id="{i + 1}"]')
    lines.append("")
    for k, s in enumerate(sprites):
        w, h = s.image.size
        ml, mt = s.offset
        lines += [f'[sub_resource type="AtlasTexture" id="AtlasTexture_{k}"]', f'atlas = ExtResource("{s.page + 1}")',
                  f"region = Rect2({s.x}, {s.y}, {w}, {h})",
                  f"margin = Rect2({ml}, {mt}, {s.source_size[0] - w}, {s.source_size[1] - h})", ""]
    groups: dict[str, list[tuple[int, Sprite]]] = {}
    for k, s in enumerate(sprites):
        groups.setdefault(s.anim or s.name, []).append((k, s))
    anims = []
    for name, items in groups.items():
        durs = [s.duration for _, s in items]
        base = min(durs) or 100
        fps = round(1000 / base, 3)
        frames = ", ".join(f'{{"duration": {round(s.duration / base, 3)}, "texture": SubResource("AtlasTexture_{k}")}}' for k, s in items)
        anims.append(f'{{"frames": [{frames}], "loop": true, "name": &"{_gd_name(name)}", "speed": {fps}}}')
    lines += ["[resource]", "animations = [" + ", ".join(anims) + "]", ""]
    return "\n".join(lines)


def collect(ctx: Context, refs: list[str]) -> list[Sprite]:
    """Sprites a partir de refs de assets: imagens/sprites viram 1 quadro; animações, um por quadro (com duração)."""
    from forge_core.runner import resolve_input

    from .animation import Animation

    out: list[Sprite] = []
    seen: set[str] = set()
    for ref in refs:
        av, path = resolve_input(ctx.project, ref)
        if av is None:
            raise ValueError(f"asset não encontrado: {ref}")
        base = av.slug
        if base in seen:
            continue
        seen.add(base)
        vdir = ctx.project.store.version_dir(av.slug, av.version)
        if av.kind == "animation" and (vdir / "animation.json").exists():
            anim = Animation.load(vdir)
            for i, (fr, dur) in enumerate(zip(anim.frames, anim.durations or [])):
                t, off = trim(fr)
                out.append(Sprite(f"{base}/{i:03d}", t, fr.size, off, anim=base, duration=int(dur)))
        else:
            f = av.primary()
            if f is None or not f.path.lower().endswith((".png", ".webp", ".jpg", ".jpeg", ".gif")):
                continue
            im = Image.open(ctx.project.store.file_path(av, f))
            im.load()
            im = im.convert("RGBA")
            t, off = trim(im)
            out.append(Sprite(base, t, im.size, off))
    return out


class AtlasParams(PipelineParams):
    assets: list[str] = Field(default_factory=list, description="refs dos assets ('slug' ou 'slug@v003')")
    folder: str | None = Field(None, description="em vez de listar: todos os assets 2D desta pasta (e subpastas)")
    name: str = Field("atlas", description="nome dos arquivos (atlas.png, atlas.json, atlas.tres)")
    padding: int = Field(2, ge=0, le=32)
    extrude: int = Field(1, ge=0, le=8, description="repete a borda de cada peça (evita sangrar ao escalar)")
    max_size: int = Field(4096, ge=256, le=16384, description="lado máximo de cada página")
    power_of_two: bool = True
    godot_path: str = Field("res://sprites/", description="pasta do projeto Godot onde as páginas vão ficar")


class Atlas(Pipeline):
    id = "2d.atlas"
    title = "Atlas de sprites (Godot / Unity / Phaser)"
    description = ("Empacota vários assets (imagens, sprites e todos os quadros das animações) num atlas recortado, e "
                   "exporta JSON do TexturePacker (Unity com TexturePacker Importer, Phaser, PixiJS) e SpriteFrames do "
                   "Godot 4 (.tres) com as animações e durações.")
    resources = {"cpu"}
    kind = "image"
    Params = AtlasParams

    def default_slug(self, params: AtlasParams) -> str:
        return re.sub(r"[^a-z0-9-]+", "-", (params.name or "atlas").lower()).strip("-") or "atlas"

    def run(self, ctx: Context, p: AtlasParams) -> list[OutputFile]:
        refs = list(p.assets)
        if p.folder is not None and not refs:
            rows = ctx.project.store.search(folder=p.folder, recursive=True)
            refs = [r["slug"] for r in rows if r.get("modality") == "2d" and r.get("kind") in ("image", "sprite", "animation")]
        if not refs:
            raise ValueError("informe assets (lista de refs) ou folder")
        ctx.progress(0.1, "lendo os assets")
        sprites = collect(ctx, refs)
        if not sprites:
            raise ValueError("nenhuma imagem/sprite/animação utilizável nesses assets")
        ctx.progress(0.5, f"empacotando {len(sprites)} peças")
        pages = pack(sprites, p.max_size, p.padding, p.extrude, p.power_of_two)
        d = ctx.version_dir
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", p.name) or "atlas"
        names = [f"{safe}.png" if len(pages) == 1 else f"{safe}_{i}.png" for i in range(len(pages))]
        files: list[OutputFile] = []
        for i, (pg, nm) in enumerate(zip(pages, names)):
            pg.save(d / nm)
            files.append(OutputFile(d / nm, "primary" if i == 0 else "texture"))
            js = texturepacker_json(sprites, pg, nm, i)
            jn = nm.replace(".png", ".json")
            (d / jn).write_text(json.dumps(js, indent=1), encoding="utf-8")
            files.append(OutputFile(d / jn, "part", "application/json"))
        gd = godot_sprite_frames(sprites, names, p.godot_path if p.godot_path.endswith("/") else p.godot_path + "/")
        (d / f"{safe}.tres").write_text(gd, encoding="utf-8")
        files.append(OutputFile(d / f"{safe}.tres", "part", "text/plain"))
        prev = pages[0].copy()
        prev.thumbnail((1024, 1024))
        prev.save(d / "preview.png")
        files.append(OutputFile(d / "preview.png", "preview"))
        used = sum(s.image.width * s.image.height for s in sprites)
        total = sum(pg.width * pg.height for pg in pages)
        ctx.meta["kind"] = "image"
        ctx.meta.setdefault("extra", {}).update({"atlas": {"pages": len(pages), "sprites": len(sprites), "assets": refs,
                                                           "size": [pages[0].width, pages[0].height],
                                                           "occupancy": round(used / max(1, total), 3),
                                                           "animations": sorted({s.anim for s in sprites if s.anim})}})
        return files


ATLAS_PIPELINES: list[Pipeline] = [Atlas()]
