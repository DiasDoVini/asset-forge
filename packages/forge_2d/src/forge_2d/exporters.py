"""Exportadores 2D: GIF, sprite sheet (+JSON Aseprite/TexturePacker), frames separados, sprite set e .aseprite."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field

from forge_core import ExportContext, Exporter, ExportParams

from .aseprite import write_aseprite
from .animation import Animation, aseprite_json, build_sheet, fit_canvas, save_gif, texturepacker_json


def _load(ctx: ExportContext) -> Animation:
    return Animation.load(ctx.version_dir)


def _name(ctx: ExportContext) -> str:
    return ctx.asset.slug


class GifParams(ExportParams):
    scale: int = Field(1, ge=1, le=16, description="ampliação nearest (pixel art)")
    fps: int | None = Field(None, ge=1, le=60, description="sobrescreve a velocidade (vazio = da animação)")
    background: str | None = Field(None, description="cor de fundo hex; vazio = transparente")
    loop: bool | None = None


class GifExporter(Exporter):
    id = "2d.gif"
    title = "GIF animado"
    kinds = {"animation"}
    Params = GifParams

    def run(self, ctx: ExportContext, p: GifParams) -> list[Path]:
        a = _load(ctx)
        durs = [round(1000 / p.fps)] * len(a.frames) if p.fps else a.durations
        out = ctx.out_dir / f"{_name(ctx)}.gif"
        save_gif(a.frames, durs, out, scale=p.scale, loop=a.loop if p.loop is None else p.loop, background=p.background)
        return [out]


class SheetParams(ExportParams):
    layout: Literal["row", "column", "grid"] = "row"
    columns: int | None = Field(None, ge=1, le=64, description="colunas no layout grid (vazio = quadrado)")
    padding: int = Field(0, ge=0, le=64, description="espaço entre frames")
    scale: int = Field(1, ge=1, le=16)
    json_format: Literal["aseprite", "texturepacker", "none"] = Field(
        "aseprite", description="aseprite: Phaser load.aseprite / Godot / Unity; texturepacker: atlas hash (Phaser, Pixi)")


class SheetExporter(Exporter):
    id = "2d.sheet"
    title = "Sprite sheet (grade)"
    description = "Todos os frames numa imagem em grade, com JSON de frames e durações."
    kinds = {"animation"}
    Params = SheetParams

    def run(self, ctx: ExportContext, p: SheetParams) -> list[Path]:
        a = _load(ctx)
        sheet, rects = build_sheet(a.frames, p.layout, p.columns, p.padding, p.scale)
        name = _name(ctx)
        png = ctx.out_dir / f"{name}.png"
        sheet.save(png)
        files = [png]
        if p.json_format != "none":
            if p.json_format == "aseprite":
                data = aseprite_json(png.name, sheet.size, rects, a.durations or [],
                                     a.meta.get("tags") or [{"name": name, "from": 0, "to": len(a.frames) - 1}],
                                     frame_prefix=name, slices=a.meta.get("slices"))
            else:
                data = texturepacker_json(png.name, sheet.size, rects, [f"{name}_{i:02d}" for i in range(len(rects))])
            js = ctx.out_dir / f"{name}.json"
            js.write_text(json.dumps(data, indent=2), encoding="utf-8")
            files.append(js)
        return files


class AsepriteParams(ExportParams):
    scale: int = Field(1, ge=1, le=16, description="ampliação nearest")


class AsepriteExporter(Exporter):
    id = "2d.aseprite"
    title = "Aseprite (.aseprite)"
    description = "Arquivo do Aseprite (RGBA, 1 camada) com durações e tags; imagens viram um arquivo de 1 frame."
    kinds = {"animation", "image", "sprite"}
    Params = AsepriteParams

    def run(self, ctx: ExportContext, p: AsepriteParams) -> list[Path]:
        if ctx.asset.kind == "animation":
            a = _load(ctx)
            frames, durs = a.frames, list(a.durations or [])
            tags = a.meta.get("tags") or [{"name": _name(ctx), "from": 0, "to": len(frames) - 1}]
        else:
            frames, durs, tags = [Image.open(ctx.files("primary")[0]).convert("RGBA")], [100], []
        if p.scale > 1:
            frames = [f.resize((f.width * p.scale, f.height * p.scale), Image.Resampling.NEAREST) for f in frames]
        out = ctx.out_dir / f"{_name(ctx)}.aseprite"
        write_aseprite(out, frames, durs, tags)
        return [out]


class FramesParams(ExportParams):
    scale: int = Field(1, ge=1, le=16)
    prefix: str | None = Field(None, description="prefixo dos arquivos (vazio = nome do asset)")


class FramesExporter(Exporter):
    id = "2d.frames"
    title = "Frames separados (PNG)"
    kinds = {"animation"}
    Params = FramesParams

    def run(self, ctx: ExportContext, p: FramesParams) -> list[Path]:
        a = _load(ctx)
        prefix = p.prefix or _name(ctx)
        d = ctx.out_dir / prefix
        d.mkdir(parents=True, exist_ok=True)
        out = []
        for i, f in enumerate(a.frames):
            if p.scale > 1:
                f = f.resize((f.width * p.scale, f.height * p.scale), Image.Resampling.NEAREST)
            path = d / f"{prefix}_{i:02d}.png"
            f.save(path)
            out.append(path)
        return out


class SpriteSetParams(ExportParams):
    include_base: bool = Field(True, description="primeira linha com o sprite base parado")
    include_views: bool = Field(True, description="incluir animações das vistas/direções derivadas (2d.view)")
    scale: int = Field(1, ge=1, le=16)
    padding: int = Field(0, ge=0, le=64)
    json_format: Literal["aseprite", "texturepacker", "none"] = "aseprite"


class SpriteSetExporter(Exporter):
    id = "2d.spriteset"
    title = "Sprite set do personagem"
    description = "Todas as animações deste sprite numa só sheet (uma linha por animação) com tags no JSON."
    kinds = {"image", "sprite"}
    Params = SpriteSetParams

    def run(self, ctx: ExportContext, p: SpriteSetParams) -> list[Path]:
        store = ctx.project.store
        rows = store.search(modality="2d", kind="animation", base=ctx.asset.slug)
        if p.include_views:  # animações das vistas derivadas (knight-back, knight-left...)
            for v in store.search(base=ctx.asset.slug):
                if v["kind"] in ("image", "sprite"):
                    rows += store.search(modality="2d", kind="animation", base=v["slug"])
        if not rows:
            raise ValueError(f"{ctx.asset.slug} não tem animações (crie com o pipeline 2d.animate)")
        anims = [(r["slug"], Animation.load(store.version_dir(r["slug"], r["version"]))) for r in sorted(rows, key=lambda r: r["slug"])]
        cw = max(a.size[0] for _, a in anims)
        ch = max(a.size[1] for _, a in anims)
        lines: list[tuple[str, list[Image.Image], list[int]]] = []
        if p.include_base:
            base_img = Image.open(ctx.files("primary")[0]).convert("RGBA")
            lines.append(("base", fit_canvas([base_img], (cw, ch))[: 1], [1000]))
        for slug, a in anims:
            lines.append((slug, fit_canvas(a.frames, (cw, ch), a.meta.get("anchor", "bottom")), list(a.durations or [])))
        cols = max(len(fr) for _, fr, _ in lines)
        sw, sh = cw * p.scale, ch * p.scale
        sheet = Image.new("RGBA", (cols * sw + (cols - 1) * p.padding, len(lines) * sh + (len(lines) - 1) * p.padding))
        rects, durs, tags, names = [], [], [], []
        for r, (tag, frames, dd) in enumerate(lines):
            start = len(rects)
            for c, f in enumerate(frames):
                x, y = c * (sw + p.padding), r * (sh + p.padding)
                sheet.paste(f.resize((sw, sh), Image.Resampling.NEAREST) if p.scale > 1 else f, (x, y))
                rects.append((x, y, sw, sh))
                names.append(f"{tag}_{c:02d}")
            durs += dd
            tags.append({"name": tag, "from": start, "to": len(rects) - 1})
        name = f"{ctx.asset.slug}_spriteset"
        png = ctx.out_dir / f"{name}.png"
        sheet.save(png)
        files = [png]
        if p.json_format == "aseprite":
            data = aseprite_json(png.name, sheet.size, rects, durs, tags, frame_prefix=ctx.asset.slug)
        elif p.json_format == "texturepacker":
            data = texturepacker_json(png.name, sheet.size, rects, names)
        else:
            data = None
        if data is not None:
            js = ctx.out_dir / f"{name}.json"
            js.write_text(json.dumps(data, indent=2), encoding="utf-8")
            files.append(js)
        return files


class PngParams(ExportParams):
    scale: int = Field(1, ge=1, le=16, description="ampliação nearest (sprites)")


class PngExporter(Exporter):
    id = "2d.png"
    title = "PNG (escala)"
    kinds = {"image", "sprite"}
    Params = PngParams

    def run(self, ctx: ExportContext, p: PngParams) -> list[Path]:
        img = Image.open(ctx.files("primary")[0])
        if p.scale > 1:
            img = img.resize((img.width * p.scale, img.height * p.scale), Image.Resampling.NEAREST)
        out = ctx.out_dir / f"{ctx.asset.slug}{'' if p.scale == 1 else f'@{p.scale}x'}.png"
        img.save(out)
        return [out]


EXPORTERS: list[Exporter] = [GifExporter(), SheetExporter(), AsepriteExporter(), FramesExporter(), SpriteSetExporter(),
                             PngExporter()]
