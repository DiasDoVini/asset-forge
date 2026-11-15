"""Importadores 2D: Aseprite, GIF/APNG/WebP animados, folhas de sprites (grade, auto, JSON), imagens de formatos
não-web (BMP, TIFF, TGA, PSD, ICO...) e ZIP de frames.

Todos gravam o mesmo que os pipelines 2D gravam (animação: animation.json + frames/ + sheet.png + preview.gif), então
viewer, editor de frames e exportadores funcionam sobre o asset importado sem diferença.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageSequence
from pydantic import BaseModel, ConfigDict, Field

from forge_core import ImportContext, Importer, NotApplicable, OutputFile

from . import spritesheet as ss
from .animation import Animation
from .aseprite import AsepriteError, read_aseprite

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}                       # a UI lê direto: cópia crua
CONVERT_EXTS = {".bmp", ".tif", ".tiff", ".tga", ".ico", ".psd", ".dds", ".gif", ".ppm", ".pgm", ".pbm", ".jfif",
                ".jpe", ".pcx", ".icns", ".avif"}                    # convertidos para PNG
ANIMATED_EXTS = {".gif", ".apng", ".png", ".webp"}
MAX_FRAMES = 2000


def _fps_of(durations: list[int]) -> int:
    ds = sorted(d for d in durations if d > 0) or [100]
    return max(1, min(60, round(1000 / ds[len(ds) // 2])))


def _save_animation(ctx: ImportContext, frames: list[Image.Image], durations: list[int], *, loop: bool = True,
                    tags: list[dict[str, Any]] | None = None, source: Path | None = None,
                    source_role_name: str | None = None, extra: dict[str, Any] | None = None,
                    anchor: str = "none", slices: list[dict[str, Any]] | None = None) -> list[OutputFile]:
    if not frames:
        raise ValueError("o arquivo não tem nenhum frame")
    if len(frames) > MAX_FRAMES:
        raise ValueError(f"frames demais ({len(frames)} > {MAX_FRAMES})")
    size = frames[0].size
    if any(f.size != size for f in frames):
        frames = ss.uniform_canvas(frames, "bottom" if anchor == "bottom" else "center")
    meta: dict[str, Any] = {"source": ctx.src.name, "anchor": anchor, "method": "import"}
    if tags:
        meta["tags"] = tags
    if slices:
        meta["slices"] = slices
    anim = Animation(frames, _fps_of(durations), loop, durations, meta)
    outs = anim.save(ctx.version_dir)
    if source is not None:
        keep = ctx.version_dir / (source_role_name or ("source" + source.suffix.lower()))
        shutil.copyfile(source, keep)
        outs.append(OutputFile(keep, "source"))
    ctx.meta["kind"] = "animation"
    ctx.extra().update({"frames": len(frames), "size": list(frames[0].size), **(extra or {})})
    return outs


# ---------------------------------------------------------------- Aseprite

class AsepriteOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_hidden: bool = Field(False, description="incluir camadas ocultas na composição")
    tag: str | None = Field(None, description="importar só os frames desta tag")
    layers: list[str] | None = Field(None, description="importar só estas camadas (nomes)")


class AsepriteImporter(Importer):
    id = "2d.aseprite"
    title = "Aseprite (.aseprite / .ase)"
    description = ("Lê o arquivo do Aseprite (RGBA, cinza ou indexado; camadas, grupos, opacidade, modos de mistura, "
                   "cels ligados, durações e tags). Vira animação (ou imagem, se tiver 1 frame); o arquivo original "
                   "fica guardado como fonte.")
    exts = {".aseprite", ".ase"}
    kind = "animation"
    Options = AsepriteOptions

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        o = AsepriteOptions.model_validate(ctx.options or {})
        try:
            ase = read_aseprite(ctx.src)
        except (AsepriteError, ValueError) as e:
            raise ValueError(f"arquivo Aseprite inválido: {e}") from e
        only = None
        if o.layers:
            names = set(o.layers)
            only = {i for i, ly in enumerate(ase.layers) if ly.name in names}
            if not only:
                raise ValueError(f"nenhuma camada com esses nomes: {sorted(names)} (há: {[ly.name for ly in ase.layers]})")
        idx = list(range(len(ase.frames)))
        tags = [{"name": t.name, "from": t.from_, "to": t.to, "direction": t.direction, "repeat": t.repeat}
                for t in ase.tags]
        if o.tag:
            t = next((t for t in tags if t["name"] == o.tag), None)
            if t is None:
                raise ValueError(f"tag não encontrada: {o.tag} (há: {[t['name'] for t in tags]})")
            idx = list(range(t["from"], t["to"] + 1))
            tags = [{**t, "from": 0, "to": len(idx) - 1}]
        frames = [ase.render_frame(i, include_hidden=o.include_hidden, only_layers=only) for i in idx]
        durations = [ase.durations[i] for i in idx]
        info = {"aseprite": {"size": [ase.width, ase.height], "color_depth": ase.color_depth,
                             "layers": [{"name": ly.name, "visible": ly.visible, "blend": ly.blend, "opacity": ly.opacity,
                                         "group": ly.type == 1} for ly in ase.layers],
                             "tags": tags, "slices": ase.slices, "skipped": ase.skipped,
                             "palette": [f"#{r:02x}{g:02x}{b:02x}" for r, g, b, _a in ase.palette[:256]]}}
        if len(frames) == 1:
            ctx.meta["kind"] = "image"
            img = ctx.version_dir / "image.png"
            frames[0].save(img)
            keep = ctx.version_dir / "source.aseprite"
            shutil.copyfile(ctx.src, keep)
            ctx.extra().update(info)
            return [OutputFile(img, "primary", "image/png"), OutputFile(keep, "source")]
        loop = not (len(tags) == 1 and tags[0]["direction"] == "forward" and tags[0].get("repeat") == 1)
        return _save_animation(ctx, frames, durations, loop=loop, tags=tags, source=ctx.src,
                               source_role_name="source.aseprite", extra=info, anchor="none", slices=ase.slices)


# ---------------------------------------------------------------- GIF / APNG / WebP animados

class AnimatedImageImporter(Importer):
    id = "2d.animated_image"
    title = "GIF / APNG / WebP animado"
    description = "Extrai os frames (com a duração de cada um) de uma imagem animada e cria uma animação de sprites."
    exts = ANIMATED_EXTS
    kind = "animation"
    priority = 10

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        try:
            im = Image.open(ctx.src)
        except Exception as e:
            raise NotApplicable() from e
        if getattr(im, "n_frames", 1) <= 1:
            raise NotApplicable()
        frames, durations = [], []
        for fr in ImageSequence.Iterator(im):
            frames.append(fr.convert("RGBA"))
            durations.append(int(fr.info.get("duration", 100)) or 100)
        loop = im.info.get("loop", 0) != 1
        return _save_animation(ctx, frames, durations, loop=loop, source=ctx.src,
                               source_role_name="source" + ctx.src.suffix.lower())


# ---------------------------------------------------------------- imagens de formatos não-web

class ImageConvertImporter(Importer):
    id = "2d.image_convert"
    title = "Imagem (BMP, TIFF, TGA, PSD, ICO, DDS…)"
    description = "Converte para PNG (a UI só lê PNG/JPG/WebP) e guarda o original como fonte."
    exts = CONVERT_EXTS
    kind = "image"
    priority = 5

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        try:
            im = Image.open(ctx.src)
            im.load()
        except Exception as e:
            raise ValueError(f"não consegui abrir a imagem ({ctx.src.suffix}): {e}") from e
        out = ctx.version_dir / "image.png"
        im.convert("RGBA").save(out)
        keep = ctx.version_dir / ("source" + ctx.src.suffix.lower())
        shutil.copyfile(ctx.src, keep)
        ctx.extra().update({"original_format": (im.format or ctx.src.suffix.lstrip(".")).upper(), "size": list(im.size)})
        return [OutputFile(out, "primary", "image/png"), OutputFile(keep, "source")]


# ---------------------------------------------------------------- folha de sprites

class SheetOptions(BaseModel):
    """Qualquer um destes campos presentes ativa a importação como folha de sprites."""

    model_config = ConfigDict(extra="forbid")

    mode: str | None = Field(None, description="grid | auto | descriptor | rects (padrão: pelo que for informado)")
    columns: int | None = Field(None, ge=1, le=1024)
    rows: int | None = Field(None, ge=1, le=1024)
    cell_width: int | None = Field(None, ge=1)
    cell_height: int | None = Field(None, ge=1)
    margin_x: int = Field(0, ge=0)
    margin_y: int = Field(0, ge=0)
    spacing_x: int = Field(0, ge=0)
    spacing_y: int = Field(0, ge=0)
    margin: int | None = Field(None, ge=0, description="atalho: margin_x = margin_y")
    spacing: int | None = Field(None, ge=0, description="atalho: spacing_x = spacing_y")
    skip_empty: bool = Field(True, description="pular células vazias (transparentes)")
    count: int | None = Field(None, ge=1, description="usar só os N primeiros frames")
    fps: int | None = Field(None, ge=1, le=60)
    loop: bool = True
    anchor: str = Field("center", description="center | bottom: como alinhar recortes de tamanhos diferentes (mode=auto)")
    merge_gap: int = Field(0, ge=0, le=64, description="mode=auto: junta ilhas separadas por até N px")
    min_area: int = Field(4, ge=1, description="mode=auto: ignora ilhas com menos pixels que isto")
    rects: list[list[int]] | None = Field(None, description="mode=rects: [[x, y, w, h], ...]")
    descriptor: dict[str, Any] | None = Field(None, description="JSON Aseprite/TexturePacker (conteúdo)")
    descriptor_file: str | None = Field(None, description="caminho do JSON Aseprite/TexturePacker")
    auto_descriptor: bool = Field(True, description="usar o <nome>.json ao lado da imagem, se existir")


_SHEET_KEYS = {"mode", "columns", "rows", "cell_width", "cell_height", "rects", "descriptor", "descriptor_file"}


class SpriteSheetImporter(Importer):
    id = "2d.spritesheet"
    title = "Folha de sprites (PNG em grade)"
    description = ("PNG/WebP/JPG com vários sprites: recorta por grade (colunas/linhas ou tamanho da célula, margem e "
                   "espaçamento), por detecção automática de ilhas, por retângulos ou pelo JSON Aseprite/TexturePacker "
                   "ao lado, e cria uma animação. Para recortes avulsos use a aba Recortar (pipeline 2d.slice).")
    exts = IMAGE_EXTS | {".gif", ".bmp", ".tga", ".tif", ".tiff"}
    kind = "animation"
    priority = 20
    Options = SheetOptions

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        raw = dict(ctx.options or {})
        unknown = set(raw) - set(SheetOptions.model_fields)
        if unknown:
            raise ValueError(f"opções desconhecidas para folha de sprites: {sorted(unknown)} "
                             f"(válidas: {sorted(SheetOptions.model_fields)})")
        sibling = None
        if not (_SHEET_KEYS & raw.keys()):
            cand = ctx.src.with_suffix(".json")
            if raw.get("auto_descriptor", True) and cand.is_file():
                sibling = cand
            else:
                raise NotApplicable()
        o = SheetOptions.model_validate(raw)
        if o.margin is not None:
            o.margin_x = o.margin_y = o.margin
        if o.spacing is not None:
            o.spacing_x = o.spacing_y = o.spacing
        sheet = Image.open(ctx.src)
        sheet.load()
        sheet = sheet.convert("RGBA")

        desc: ss.Descriptor | None = None
        if o.descriptor is not None or o.descriptor_file or sibling or o.mode == "descriptor":
            data = o.descriptor
            if data is None:
                f = Path(o.descriptor_file) if o.descriptor_file else sibling
                if f is None or not f.is_file():
                    raise ValueError("mode=descriptor sem `descriptor`/`descriptor_file`")
                data = json.loads(f.read_text(encoding="utf-8-sig"))
            try:
                desc = ss.parse_descriptor(data)
            except ValueError as e:
                if sibling is not None and o.descriptor is None and not o.descriptor_file and o.mode != "descriptor":
                    raise NotApplicable() from e   # JSON qualquer ao lado da imagem: não é descritor
                raise
        tags: list[dict[str, Any]] = []
        durations: list[int] = []
        info: dict[str, Any]
        if desc is not None:
            frames = ss.descriptor_frames(sheet, desc)
            durations = [f.duration or 0 for f in desc.frames]
            tags = desc.tags
            info = {"sheet": {"mode": "descriptor", "app": desc.app, "frames": len(frames)}}
        else:
            mode = o.mode or ("rects" if o.rects else "auto" if o.columns is None and o.cell_width is None else "grid")
            if mode == "rects":
                if not o.rects:
                    raise ValueError("mode=rects precisa de `rects`")
                rects = [tuple(int(v) for v in r) for r in o.rects]   # type: ignore[misc]
            elif mode == "auto":
                rects = ss.detect_sprites(sheet, o.min_area, o.merge_gap)
            elif mode == "grid":
                g = ss.GridSpec(columns=o.columns, rows=o.rows, cell_width=o.cell_width, cell_height=o.cell_height,
                                margin_x=o.margin_x, margin_y=o.margin_y, spacing_x=o.spacing_x, spacing_y=o.spacing_y) \
                    if (o.columns or o.cell_width) and (o.rows or o.cell_height) else _grid_fallback(sheet, o)
                rects = ss.grid_cells(sheet.size, g)
            else:
                raise ValueError(f"mode inválido: {mode}")
            if o.skip_empty:
                mask = ss.content_mask(sheet)
                rects = [r for r in rects if not ss.is_empty(mask, r)]
            if not rects:
                raise ValueError("a folha não rendeu nenhum frame (confira a grade; células vazias são puladas)")
            frames = ss.crop_frames(sheet, rects)
            info = {"sheet": {"mode": mode, "rects": [list(r) for r in rects]}}
        if o.count:
            frames = frames[: o.count]
            durations = durations[: o.count]
        fps = o.fps or 10
        if not any(durations) or len(durations) != len(frames):
            durations = [round(1000 / fps)] * len(frames)
        else:
            durations = [d or round(1000 / fps) for d in durations]
        return _save_animation(ctx, frames, durations, loop=o.loop, tags=tags, source=ctx.src,
                               source_role_name="source" + ctx.src.suffix.lower(), extra=info,
                               anchor="bottom" if o.anchor == "bottom" else "none")


def _grid_fallback(sheet: Image.Image, o: SheetOptions) -> ss.GridSpec:
    """Só uma das metades da grade informada (ex.: só `columns`): a outra vem da detecção automática."""
    det = ss.detect_grid(sheet).get("grid")
    if not det:
        raise ValueError("informe columns/rows ou cell_width/cell_height")
    d = {k: v for k, v in det.items()}
    cols = o.columns or (None if o.cell_width else d["columns"])
    rows = o.rows or (None if o.cell_height else d["rows"])
    return ss.GridSpec(columns=cols, rows=rows, cell_width=o.cell_width, cell_height=o.cell_height,
                       margin_x=o.margin_x, margin_y=o.margin_y, spacing_x=o.spacing_x, spacing_y=o.spacing_y)


# ---------------------------------------------------------------- ZIP

class ZipOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fps: int = Field(10, ge=1, le=60)
    loop: bool = True


class ZipFramesImporter(Importer):
    id = "2d.zip_frames"
    title = "ZIP de frames ou folha + JSON"
    description = ("ZIP com uma sequência de PNGs (ordem natural: walk_2 antes de walk_10) vira animação; ZIP com uma "
                   "folha + JSON Aseprite/TexturePacker vira animação pelo descritor; um .aseprite dentro do ZIP é lido.")
    exts = {".zip"}
    kind = "animation"
    Options = ZipOptions

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        o = ZipOptions.model_validate({k: v for k, v in (ctx.options or {}).items() if k in ZipOptions.model_fields})
        try:
            zf = zipfile.ZipFile(ctx.src)
        except zipfile.BadZipFile as e:
            raise NotApplicable() from e
        with zf, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [n for n in zf.namelist() if not n.endswith("/") and "__MACOSX" not in n and not Path(n).name.startswith(".")]
            for n in names:   # extração segura: só o nome do arquivo, sem caminhos
                (root / Path(n).name).write_bytes(zf.read(n))
            files = sorted((root / Path(n).name for n in names), key=lambda p: ss.natural_key(p.name))
            files = list(dict.fromkeys(files))
            ase = [f for f in files if f.suffix.lower() in (".aseprite", ".ase")]
            if ase:
                return AsepriteImporter().run(_sub(ctx, ase[0]))
            jsons = [f for f in files if f.suffix.lower() == ".json"]
            imgs = [f for f in files if f.suffix.lower() in IMAGE_EXTS | {".gif", ".bmp", ".tga"}]
            for j in jsons:
                try:
                    desc = ss.parse_descriptor(json.loads(j.read_text(encoding="utf-8-sig")))
                except ValueError:
                    continue
                img = next((f for f in imgs if desc.image and f.name == Path(desc.image).name), None) or \
                    next((f for f in imgs if f.stem == j.stem), None) or (imgs[0] if len(imgs) == 1 else None)
                if img is None:
                    continue
                sub = _sub(ctx, img, {"descriptor": json.loads(j.read_text(encoding="utf-8-sig")), "fps": o.fps, "loop": o.loop})
                return SpriteSheetImporter().run(sub)
            if not imgs:
                raise ValueError("o ZIP não tem imagens, folha+JSON nem .aseprite")
            frames = [Image.open(f).convert("RGBA") for f in imgs]
            for fr in frames:
                fr.load()
            return _save_animation(ctx, frames, [round(1000 / o.fps)] * len(frames), loop=o.loop, source=ctx.src,
                                   source_role_name="source.zip", extra={"frames_from": [f.name for f in imgs]})


def _sub(ctx: ImportContext, src: Path, options: dict[str, Any] | None = None) -> ImportContext:
    """Contexto para um arquivo extraído do ZIP (ainda na pasta temporária), gravando na mesma versão."""
    return ImportContext(project=ctx.project, src=src, version_dir=ctx.version_dir, slug=ctx.slug, version=ctx.version,
                         name=ctx.name, options=options or {}, meta=ctx.meta)


from .layered import KritaImporter, PixeloramaImporter  # noqa: E402  (usa _save_animation daqui)

IMPORTERS: list[Importer] = [AsepriteImporter(), AnimatedImageImporter(), ImageConvertImporter(), SpriteSheetImporter(),
                             ZipFramesImporter(), PixeloramaImporter(), KritaImporter()]
