"""2d.slice — recorta sprites de uma imagem (folha de sprites) por grade, detecção de ilhas ou retângulos.

Grava, na versão do asset resultante (kind=spriteset):
    overlay.png      primary — a folha com os recortes marcados
    source.<ext>     source  — a imagem de entrada
    slices.json      source  — retângulos, nomes, seleção e opções usadas
    parts/NNN_nome.png  part — um PNG por recorte (RGBA, sem reamostrar)

E, conforme os parâmetros, cria ASSETS separados:
    sprites=True     um asset (kind=sprite/image) por recorte, na pasta <pasta da folha>/<slug do resultado>
    animation=True   uma animação (frames na ordem dos recortes) para usar nos exportadores e no editor de frames
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from forge_core import OutputFile, Pipeline, PipelineParams, slugify
from forge_core.pipeline import Context

from . import spritesheet as ss
from .animation import Animation


class SliceRect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    w: int = Field(..., ge=1)
    h: int = Field(..., ge=1)
    name: str | None = None


class SliceParams(PipelineParams):
    mode: Literal["grid", "auto", "rects"] = Field(
        "grid", description="grid: grade regular; auto: ilhas de conteúdo; rects: retângulos informados")
    grid: ss.GridSpec | None = Field(None, description="mode=grid: colunas/linhas ou tamanho da célula, margem, espaçamento")
    rects: list[SliceRect] | None = Field(None, description="mode=rects: [{x, y, w, h, name?}]")
    selected: list[int] | None = Field(None, description="índices (ordem de leitura) dos recortes a manter; vazio = todos")
    names: list[str] | None = Field(None, description="nomes, na ordem dos recortes escolhidos")
    prefix: str | None = Field(None, description="prefixo dos nomes (padrão: slug da folha)")
    skip_empty: bool = Field(True, description="descartar recortes totalmente transparentes")
    trim: bool = Field(False, description="aparar cada recorte ao conteúdo")
    pad: int = Field(0, ge=0, le=64, description="margem transparente ao redor de cada recorte")
    min_area: int = Field(4, ge=1, description="mode=auto: ignora ilhas com menos pixels que isto")
    merge_gap: int = Field(0, ge=0, le=64, description="mode=auto: junta ilhas separadas por até N px")
    sprites: bool = Field(True, description="criar um asset por recorte")
    animation: bool = Field(False, description="criar também uma animação com os recortes, na ordem")
    fps: int = Field(10, ge=1, le=60)
    loop: bool = True
    anchor: Literal["center", "bottom", "none"] = Field(
        "center", description="como alinhar recortes de tamanhos diferentes na animação")

    @model_validator(mode="after")
    def _check(self) -> "SliceParams":
        if self.mode == "grid" and self.grid is None:
            raise ValueError("mode=grid precisa de `grid`")
        if self.mode == "rects" and not self.rects:
            raise ValueError("mode=rects precisa de `rects`")
        return self


def _safe_names(raw: list[str | None], prefix: str) -> list[str]:
    """Nomes de arquivo únicos e seguros; sem nome: prefixo-01, prefixo-02…"""
    out, seen = [], set()
    width = max(2, len(str(len(raw))))
    for i, n in enumerate(raw):
        base = slugify(n) if n else f"{prefix}-{i + 1:0{width}d}"
        name, k = base, 2
        while name in seen:
            name = f"{base}-{k}"
            k += 1
        seen.add(name)
        out.append(name)
    return out


class SliceSheet(Pipeline):
    id = "2d.slice"
    title = "Recortar sprites"
    description = ("Recorta sprites de uma imagem (folha de sprites): grade com colunas/linhas ou tamanho de célula, "
                   "margem e espaçamento; detecção automática de ilhas; ou retângulos manuais. Cria um asset por recorte "
                   "e/ou uma animação, sem reamostrar os pixels. O editor visual está na aba Recortar do asset.")
    resources = {"cpu"}
    kind = "spriteset"
    Params = SliceParams
    requires_input = True

    def default_slug(self, params: SliceParams) -> str:  # type: ignore[override]
        ref = (params.input_asset or "").split("@")[0]
        base = Path(ref).stem if ("/" in ref or "\\" in ref) else ref
        return f"{base} slices" if base else "slices"

    def run(self, ctx: Context, p: SliceParams) -> list[OutputFile]:
        files_in = ctx.input_files("primary") or ctx.input_files()
        imgs = [f for f in files_in if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")]
        if not imgs:
            raise ValueError("2d.slice precisa de um input_asset de imagem (a folha de sprites)")
        with Image.open(imgs[0]) as im:
            im.load()
            sheet = im.convert("RGBA")
        W, H = sheet.size
        ctx.progress(0.1, "calculando recortes")

        if p.mode == "grid":
            assert p.grid is not None
            rects = ss.grid_cells(sheet.size, p.grid)
        elif p.mode == "auto":
            rects = ss.detect_sprites(sheet, p.min_area, p.merge_gap)
        else:
            rects = [(r.x, r.y, r.w, r.h) for r in p.rects or []]
            for (x, y, w, h) in rects:
                if x < 0 or y < 0 or x + w > W or y + h > H:
                    raise ValueError(f"retângulo fora da imagem ({W}×{H}): {(x, y, w, h)}")
        explicit_names = [r.name for r in p.rects] if (p.mode == "rects" and p.rects) else [None] * len(rects)
        indices = list(range(len(rects)))
        if p.selected:
            bad = [i for i in p.selected if not 0 <= i < len(rects)]
            if bad:
                raise ValueError(f"índices fora do intervalo 0..{len(rects) - 1}: {bad}")
            indices = list(dict.fromkeys(p.selected))
        mask = ss.content_mask(sheet)
        if p.skip_empty:
            indices = [i for i in indices if not ss.is_empty(mask, rects[i])]
        if not indices:
            raise ValueError("nenhum recorte para criar (grade vazia, tudo transparente ou seleção vazia)")
        chosen = [rects[i] for i in indices]
        if p.trim:
            chosen = [ss.content_bbox(mask, r) or r for r in chosen]
        prefix = slugify(p.prefix) if p.prefix else slugify(ctx.input.slug if ctx.input else imgs[0].stem)
        given = p.names or []
        raw = [given[k] if k < len(given) and given[k] else explicit_names[i] for k, i in enumerate(indices)]
        names = _safe_names(raw, prefix)

        crops = ss.crop_frames(sheet, chosen)
        if p.pad:
            crops = [_pad(c, p.pad) for c in crops]

        d = ctx.version_dir
        (d / "parts").mkdir(exist_ok=True)
        out: list[OutputFile] = []
        part_paths: list[Path] = []
        for k, (c, n) in enumerate(zip(crops, names)):
            path = d / "parts" / f"{k + 1:03d}_{n}.png"
            c.save(path)
            part_paths.append(path)
            out.append(OutputFile(path, "part", "image/png"))
        ov = ss.overlay(sheet, rects, set(indices))
        ov.save(d / "overlay.png")
        src_copy = d / ("source" + imgs[0].suffix.lower())
        shutil.copyfile(imgs[0], src_copy)
        info = {"mode": p.mode, "source_size": [W, H], "rects": [list(r) for r in rects], "selected": indices,
                "names": names, "trim": p.trim, "pad": p.pad,
                "grid": p.grid.model_dump() if p.grid else None}
        (d / "slices.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        out = [OutputFile(d / "overlay.png", "primary", "image/png"),
               OutputFile(src_copy, "source", "image/png" if src_copy.suffix == ".png" else "image/jpeg"),
               OutputFile(d / "slices.json", "source", "application/json"), *out]

        created: list[str] = []
        store = ctx.project.store
        parent_ref = ctx.input.ref if ctx.input else None
        if ctx.input is not None:
            base_folder = store.get_meta(ctx.input.slug).folder
            category = store.get_meta(ctx.input.slug).category
        else:
            base_folder, category = "", None
        folder = f"{base_folder}/{ctx.slug}" if base_folder else ctx.slug
        child_kind = "sprite" if (ctx.input is not None and ctx.input.kind == "sprite") else "image"
        if p.sprites:
            ctx.progress(0.5, "criando sprites")
            for k, (path, n) in enumerate(zip(part_paths, names)):
                ctx.check_cancelled()
                av = store.import_file(path, slug=store.unique_slug(f"{prefix}-{n}" if not n.startswith(prefix) else n),
                                       modality="2d", kind=child_kind, parent=parent_ref, name=n,
                                       folder=folder, category=category)
                created.append(av.ref)
                ctx.progress(0.5 + 0.4 * (k + 1) / len(part_paths))
        if p.animation:
            ctx.progress(0.9, "criando animação")
            frames = crops
            if any(f.size != frames[0].size for f in frames):
                frames = ss.uniform_canvas(frames, "bottom" if p.anchor == "bottom" else "center")
            anim = Animation(frames, p.fps, p.loop, None, {"base": parent_ref, "anchor": p.anchor, "method": "slice",
                                                           "source": imgs[0].name})
            slug = store.unique_slug(f"{prefix}-anim")
            n, vd = store.reserve_version(slug)
            try:
                saved = anim.save(vd)
                av = store.finalize(slug, n, [(o.path, o.role, o.mime) for o in saved], modality="2d", kind="animation",
                                    name=f"{prefix} (animação)", parent=parent_ref, pipeline=self.id,
                                    extra={"frames": len(frames), "size": list(frames[0].size), "slice_of": ctx.slug})
                store.set_meta(slug, folder=folder, category=category)
                created.append(av.ref)
            except Exception:
                shutil.rmtree(vd, ignore_errors=True)
                raise

        ctx.meta.setdefault("extra", {}).update({"slice": {**info, "count": len(crops), "created": created}})
        ctx.log(f"{len(crops)} recortes; criados: {len(created)} asset(s) em '{folder}'")
        return out


def _pad(img: Image.Image, pad: int) -> Image.Image:
    c = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad))
    c.paste(img, (pad, pad))
    return c


SLICE_PIPELINES: list[Pipeline] = [SliceSheet()]
