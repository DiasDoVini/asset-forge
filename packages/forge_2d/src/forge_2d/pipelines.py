"""Pipelines 2D.

Bloco "2d" do project.yaml (todos opcionais):
    2d:
      width: 1024            # resolução de geração
      height: 1024
      transparent: true      # remove fundo por padrão (exceto tile/background)
      pixel_art: false       # pixeliza as saídas por padrão
      pixel: {size: 64, colors: 16, outline: null, dither: false, scale: 1}
      models: {zimage_unet: ..., qwen_edit_unet: ...}   # sobrescreve graphs.DEFAULT_MODELS
      loras: [{name: x.safetensors, strength: 0.8}]      # LoRAs de estilo para o Z-Image
      engine: z-image        # motor do Text → 2D: z-image | qwen-image-2.1 | qwen-image-2.1-full
"""
from __future__ import annotations

import io
import random
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import Field

from forge_core import ComfyError, OutputFile, Pipeline, PipelineParams
from forge_core.pipeline import Context
from forge_core.runner import resolve_input

from . import graphs, imaging, prompts
from .pixel import extract_palette, hex_to_rgb, pixelize

PIXEL_DEFAULTS = {"size": 64, "colors": 16, "outline": None, "dither": False, "scale": 1, "mode": "downscale"}


# ---------------------------------------------------------------- helpers

def _models(style: dict[str, Any]) -> dict[str, str]:
    m = dict(graphs.DEFAULT_MODELS)
    m.update(style.get("models") or {})
    return m


def _seed(seed: int | None) -> int:
    return seed if seed is not None and seed >= 0 else random.randint(0, 2**31 - 1)


def _pixel_cfg(style: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    cfg = {**PIXEL_DEFAULTS, **(style.get("pixel") or {})}
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


def _palette(style: dict[str, Any], explicit: list[str] | None = None, use_project: bool = True) -> np.ndarray | None:
    if explicit:
        return hex_to_rgb(explicit)
    if use_project and style.get("palette"):
        return hex_to_rgb(style["palette"])
    return None


def _open(path: Path) -> Image.Image:
    im = Image.open(path)
    im.load()
    return im


def _resolve_image(ctx: Context, ref: str) -> Image.Image:
    av, path = resolve_input(ctx.project, ref)
    if av is not None:
        files = ctx.project.store.files_of(av, "primary") or ctx.project.store.files_of(av)
        path = files[0]
    return _open(path)


def _input_image(ctx: Context) -> Image.Image:
    files = ctx.input_files("primary") or ctx.input_files()
    if not files:
        raise ValueError("asset de entrada sem arquivo")
    return _open(files[0])


def _ensure_comfy(ctx: Context, files: dict[str, str] | None = None) -> None:
    """O ComfyUI configurado (2D, :8188) se ele enxergar os modelos; senão outra instância do Asset Forge no ar."""
    _use_comfy_with(ctx, (), files, "este pipeline")


ENGINES = ("z-image", "qwen-image-2.1", "qwen-image-2.1-full")
EngineName = Literal["z-image", "qwen-image-2.1", "qwen-image-2.1-full"]


def _use_comfy_with(ctx: Context, nodes: tuple[str, ...], files: dict[str, str] | None, what: str) -> None:
    """Usa uma instância do ComfyUI com os `nodes` e os arquivos (`{pasta: arquivo}`): a configurada primeiro, depois
    as outras do Asset Forge (ex.: o Qwen-Image 2.1 precisa do ComfyUI 0.38+, que a instância TRELLIS tem)."""
    from forge_core.comfy_select import use_instance

    use_instance(ctx, nodes=nodes, files=files, what=what)


def _upload(ctx: Context, img: Image.Image, name: str = "in.png") -> str:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        img.save(p)
        return ctx.comfy.upload_image(p)


def _run(ctx: Context, graph: graphs.Graph, want: list[str | None], span: tuple[float, float] = (0.05, 0.9),
         label: str = "gerando") -> dict[str, Image.Image]:
    ctx.check_cancelled()
    a, b = span
    ctx.progress(a, label)
    outs = ctx.comfy.run_workflow(graph.to_dict(), on_progress=lambda v, m: ctx.progress(a + (b - a) * v, label))
    result: dict[str, Image.Image] = {}
    for o in outs:
        if o.node_id in want and o.node_id not in result:
            result[o.node_id] = _open(io.BytesIO(ctx.comfy.download(o.filename, o.subfolder, o.type)))
    missing = [w for w in want if w and w not in result]
    if missing:
        raise ComfyError(f"workflow não produziu saída nos nós {missing}")
    ctx.progress(b, "pós-processando")
    return result


def _finish(ctx: Context, img: Image.Image, *, mask: Image.Image | None = None, trim: bool = True,
            pixel: dict[str, Any] | None = None, palette: np.ndarray | None = None) -> list[OutputFile]:
    """Aplica alpha/recorte/pixelização e grava arquivos. Define ctx.meta['kind']."""
    d = ctx.version_dir
    if mask is not None:
        img = imaging.apply_mask(img, mask)
        if trim:
            img = imaging.trim(img, pad=8)
    if pixel is None:
        out = d / "image.png"
        img.save(out)
        ctx.meta["kind"] = "image"
        files = [OutputFile(out, "primary")]
        if max(img.size) < 256:
            imaging.preview(img).save(d / "preview.png")
            files.append(OutputFile(d / "preview.png", "preview"))
        return files

    src = d / "source.png"
    img.save(src)
    res = pixelize(img, mode=pixel["mode"], size=pixel["size"], colors=pixel["colors"], palette=palette,
                   dither=bool(pixel["dither"]), outline_color=pixel["outline"], scale=int(pixel["scale"] or 1),
                   crop=True, remove_bg="auto")
    sprite = d / "sprite.png"
    res.scaled.save(sprite)
    prev = d / "preview.png"
    imaging.preview(res.sprite, 512).save(prev)
    ctx.meta["kind"] = "sprite"
    ctx.meta.setdefault("extra", {}).update({"pixel": {**pixel, "art_size": list(res.sprite.size), "colors_used": res.colors}})
    return [OutputFile(sprite, "primary"), OutputFile(prev, "preview"), OutputFile(src, "source")]


def _ref_images(ctx: Context, style: dict[str, Any], use_project_refs: bool, extra: list[str], limit: int = 2,
                query: str | None = None) -> list[Image.Image]:
    """Refs explícitas > refs do projeto > refs das Creator Souls anexadas (busca pelo `query`)."""
    imgs = [_resolve_image(ctx, r) for r in extra[:limit]]
    if use_project_refs:
        for p in style.get("refs", []):
            if len(imgs) >= limit:
                break
            imgs.append(_open(Path(p)))
        if len(imgs) < limit and query and style.get("souls"):
            from .soul_pipelines import ref_canvas, soul_refs

            found = soul_refs(ctx, query, limit - len(imgs))
            if found:
                ctx.meta.setdefault("extra", {})["soul_references"] = [
                    {"ref": f"soul:{r['soul'].id}/{r['id']}", "name": r.get("name") or r["stem"]} for r in found]
            imgs += [ref_canvas(Image.open(r["soul"].ref_path(r["id"]))) for r in found]
    return [imaging.flatten(i) for i in imgs[:limit]]


# ---------------------------------------------------------------- params comuns

class _GenParams(PipelineParams):
    seed: int | None = Field(None, description="semente (vazio = aleatória)")
    transparent: bool | None = Field(None, description="remover fundo (vazio = padrão do projeto/tipo)")
    pixelize: bool | None = Field(None, description="pixelizar a saída (vazio = 2d.pixel_art do projeto)")
    pixel_size: int | None = Field(None, description="maior lado em pixels de arte ao pixelizar")
    pixel_colors: int | None = Field(None, description="nº de cores ao pixelizar")


def _pixel_or_none(style: dict[str, Any], p: _GenParams, force: bool | None = None) -> dict[str, Any] | None:
    on = force if force is not None else (p.pixelize if p.pixelize is not None else bool(style.get("pixel_art")))
    return _pixel_cfg(style, size=p.pixel_size, colors=p.pixel_colors) if on else None


# ---------------------------------------------------------------- Text → 2D

class Text2DParams(_GenParams):
    prompt: str = Field(..., description="o que desenhar (só o conteúdo; o estilo do projeto é adicionado)")
    asset_type: prompts.AssetType = Field("object", description="object, character, creature, tile, icon, ui, background, effect, free")
    width: int | None = Field(None, ge=256, le=2048)
    height: int | None = Field(None, ge=256, le=2048)
    steps: int = Field(8, ge=1, le=50, description="passos (Z-Image e Qwen-Image completo; o Turbo usa sempre 8)")
    use_style: bool = Field(True, description="anexar o estilo global do projeto")
    raw_prompt: bool = Field(False, description="usar o prompt literalmente, sem composição")
    engine: EngineName | None = Field(None, description="motor: z-image (rápido), qwen-image-2.1 (Turbo, 8 passos, "
                                      "melhor com texto e composição) ou qwen-image-2.1-full (25 passos). Vazio = 2d.engine "
                                      "do projeto, senão z-image")
    negative: str = Field("", description="prompt negativo (só Qwen-Image 2.1)")


class Text2D(Pipeline):
    id = "2d.text2d"
    title = "Text → 2D"
    description = ("Gera um asset 2D a partir de texto (Z-Image Turbo ou Qwen-Image 2.1), com o estilo do projeto, fundo "
                   "transparente e pixelização opcional.")
    resources = {"gpu"}
    kind = "image"
    Params = Text2DParams
    force_pixel: bool | None = None

    def run(self, ctx: Context, p: Text2DParams) -> list[OutputFile]:
        style = ctx.style()
        pixel = _pixel_or_none(style, p, self.force_pixel)
        transparent = p.transparent if p.transparent is not None else (
            bool(style.get("transparent", True)) and p.asset_type not in prompts.OPAQUE_TYPES)
        full = p.prompt if p.raw_prompt else prompts.compose(p.prompt, style, p.asset_type, pixel is not None, p.use_style)
        w = imaging.snap(p.width or int(style.get("width", 1024)))
        h = imaging.snap(p.height or int(style.get("height", 1024)))
        seed = _seed(p.seed)
        ctx.log(f"prompt: {full}")
        engine = p.engine or style.get("engine") or "z-image"
        if engine not in ENGINES:
            raise ValueError(f"motor desconhecido: {engine} (há: {', '.join(ENGINES)})")
        models = _models(style)
        if engine == "z-image":
            need = {"diffusion_models": models["zimage_unet"], "text_encoders": models["zimage_clip"], "vae": models["zimage_vae"]}
            if transparent:
                need["background_removal"] = models["bg_removal"]
            _ensure_comfy(ctx, need)
            g, img_id, mask_id = graphs.text2img_zimage(full, w, h, seed, p.steps, models=models,
                                                        remove_bg=transparent, loras=style.get("loras") or [])
            model_name = "z-image-turbo"
        else:
            turbo = engine == "qwen-image-2.1"
            unet = models["qi21_unet" if turbo else "qi21_full_unet"]
            need = {"diffusion_models": unet, "text_encoders": models["qi21_clip"], "vae": models["qi21_vae"]}
            if transparent:
                need["background_removal"] = models["bg_removal"]
            _use_comfy_with(ctx, graphs.QI21_NODES, need, "o Qwen-Image 2.1")
            g, img_id, mask_id = graphs.text2img_qwen21(full, w, h, seed, models=models, turbo=turbo,
                                                        steps=p.steps if p.steps != 8 else 25, negative=p.negative,
                                                        remove_bg=transparent, loras=style.get("qi21_loras") or [])
            model_name = "qwen-image-2.1-turbo" if turbo else "qwen-image-2.1"
        out = _run(ctx, g, [img_id, mask_id])
        ctx.meta.update(seed=seed, prompt=p.prompt, extra={"full_prompt": full, "model": model_name,
                                                           "width": w, "height": h, "asset_type": p.asset_type})
        return _finish(ctx, out[img_id], mask=out.get(mask_id) if mask_id else None, pixel=pixel,
                       palette=_palette(style))


class Text2Pixel(Text2D):
    id = "2d.text2pixel"
    title = "Text → Pixel Art"
    description = "Text → 2D já pixelizado (grade, paleta do projeto e alpha binário)."
    kind = "sprite"
    force_pixel = True


# ---------------------------------------------------------------- Image → 2D

class Image2DParams(_GenParams):
    prompt: str = Field("", description="descrição/ajustes extras para o redesenho")
    asset_type: prompts.AssetType = "object"
    use_refs: bool = Field(True, description="usar as imagens de referência do projeto como guia de estilo")
    reference_assets: list[str] = Field(default_factory=list, description="até 2 refs extras (asset ou arquivo)")
    steps: int = Field(4, ge=1, le=50)
    megapixels: float = Field(1.0, ge=0.25, le=2.0)


class Image2D(Pipeline):
    id = "2d.image2d"
    title = "Image → 2D"
    description = "Redesenha uma imagem (foto, rascunho, render) como asset 2D no estilo do projeto (Qwen-Image-Edit)."
    resources = {"gpu"}
    requires_input = True
    Params = Image2DParams

    def default_slug(self, params: Image2DParams) -> str:
        return Path((params.input_asset or "asset").split("@")[0]).stem + "-2d"

    def run(self, ctx: Context, p: Image2DParams) -> list[OutputFile]:
        style = ctx.style()
        pixel = _pixel_or_none(style, p)
        transparent = p.transparent if p.transparent is not None else (
            bool(style.get("transparent", True)) and p.asset_type not in prompts.OPAQUE_TYPES)
        src = imaging.flatten(_input_image(ctx))
        refs = _ref_images(ctx, style, p.use_refs, p.reference_assets,
                           query=p.prompt or (ctx.input.prompt if ctx.input else None) or (ctx.input.name if ctx.input else None))
        instruction = prompts.to2d_instruction(p.prompt, style, p.asset_type, bool(refs), pixel is not None)
        seed = _seed(p.seed)
        _ensure_comfy(ctx)
        names = [_upload(ctx, src, "src.png")] + [_upload(ctx, r, f"ref{i}.png") for i, r in enumerate(refs)]
        ctx.log(f"instrução: {instruction}")
        g, img_id, mask_id = graphs.qwen_edit(instruction, names, seed, p.steps, models=_models(style),
                                              megapixels=p.megapixels, remove_bg=transparent)
        out = _run(ctx, g, [img_id, mask_id])
        ctx.meta.update(seed=seed, prompt=p.prompt or None,
                        extra={"instruction": instruction, "model": "qwen-image-edit-2511", "refs": len(refs)})
        return _finish(ctx, out[img_id], mask=out.get(mask_id) if mask_id else None, pixel=pixel,
                       palette=_palette(style))


# ---------------------------------------------------------------- Edit

class EditParams(PipelineParams):
    instruction: str = Field(..., description='o que mudar, ex.: "make the sword blade blue and add frost"')
    reference_assets: list[str] = Field(default_factory=list, description="até 2 imagens de referência (asset ou arquivo)")
    mask: str | None = Field(None, description="máscara (arquivo/asset; branco = área editável). Fora dela o original é mantido")
    keep_style: bool = True
    seed: int | None = None
    steps: int = Field(4, ge=1, le=50)
    megapixels: float = Field(1.0, ge=0.25, le=2.0)
    transparent: bool | None = Field(None, description="vazio = manter transparência se a entrada tinha alpha")
    pixelize: bool | None = Field(None, description="vazio = pixelizar se a entrada era sprite")


class Edit(Pipeline):
    id = "2d.edit"
    title = "2D Edit"
    description = "Edita um asset por instrução em linguagem natural (Qwen-Image-Edit), com máscara e referências opcionais. Gera nova versão."
    resources = {"gpu"}
    requires_input = True
    same_asset = True
    Params = EditParams

    def run(self, ctx: Context, p: EditParams) -> list[OutputFile]:
        style = ctx.style()
        original = _input_image(ctx)
        was_sprite = ctx.input is not None and ctx.input.kind == "sprite"
        if was_sprite:  # edita a fonte em alta resolução quando existir
            srcs = ctx.input_files("source")
            if srcs:
                original = _open(srcs[0])
        transparent = p.transparent if p.transparent is not None else imaging.has_alpha(original)
        pixel_on = p.pixelize if p.pixelize is not None else was_sprite
        refs = [imaging.flatten(_resolve_image(ctx, r)) for r in p.reference_assets[:2]]
        instruction = prompts.edit_instruction(p.instruction, style, p.keep_style)
        seed = _seed(p.seed)
        _ensure_comfy(ctx)
        names = [_upload(ctx, imaging.flatten(original), "src.png")] + [_upload(ctx, r, f"ref{i}.png") for i, r in enumerate(refs)]
        ctx.log(f"instrução: {instruction}")
        g, img_id, mask_id = graphs.qwen_edit(instruction, names, seed, p.steps, models=_models(style),
                                              megapixels=p.megapixels, remove_bg=transparent)
        out = _run(ctx, g, [img_id, mask_id])
        edited = out[img_id]
        alpha = out.get(mask_id) if mask_id else None
        if p.mask:
            region = _resolve_image(ctx, p.mask)
            edited = imaging.composite_region(imaging.flatten(original), edited, region).convert("RGB")
            if alpha is not None:  # dentro da máscara usa alpha novo, fora mantém o original
                orig_a = original.convert("RGBA").split()[3]
                alpha = imaging.composite_region(orig_a.convert("RGB"), alpha.convert("RGB"), region).convert("L")
        elif edited.size != original.size:
            edited = edited.resize(original.size, Image.Resampling.LANCZOS)
            if alpha is not None:
                alpha = alpha.resize(original.size, Image.Resampling.BILINEAR)
        ctx.meta.update(seed=seed, prompt=p.instruction, extra={"instruction": instruction, "model": "qwen-image-edit-2511"})
        pixel = None
        if pixel_on:
            prev_pixel = (ctx.input.extra.get("pixel") if ctx.input else None) or {}
            pixel = _pixel_cfg(style, **{k: prev_pixel.get(k) for k in ("size", "colors", "outline", "dither", "scale", "mode")})
        return _finish(ctx, edited, mask=alpha, trim=not p.mask, pixel=pixel, palette=_palette(style))


# ---------------------------------------------------------------- Enhancement

class EnhanceParams(PipelineParams):
    method: Literal["auto", "esrgan", "seedvr2", "nearest"] = Field(
        "auto", description="auto: nearest para sprites/pixel art, esrgan para o resto; seedvr2 = máxima qualidade (lento)")
    scale: float = Field(2.0, ge=1.0, le=8.0)
    seed: int | None = None
    color_correction: Literal["lab", "wavelet", "adain", "none"] = "lab"


class Enhance(Pipeline):
    id = "2d.enhance"
    title = "2D Enhancement"
    description = "Aumenta resolução e nitidez (ESRGAN 4x-UltraSharp, SeedVR2 ou nearest para pixel art). Preserva transparência."
    resources = {"gpu"}
    requires_input = True
    same_asset = True
    Params = EnhanceParams

    def run(self, ctx: Context, p: EnhanceParams) -> list[OutputFile]:
        style = ctx.style()
        img = _input_image(ctx)
        is_sprite = ctx.input is not None and ctx.input.kind == "sprite"
        method = p.method
        if method == "auto":
            method = "nearest" if is_sprite or max(img.size) <= 128 else "esrgan"
        target = (round(img.width * p.scale), round(img.height * p.scale))
        alpha = img.convert("RGBA").split()[3] if imaging.has_alpha(img) else None
        ctx.meta.update(kind=ctx.input.kind if ctx.input else "image",
                        extra={"method": method, "scale": p.scale, "from": list(img.size), "to": list(target)})
        out_path = ctx.version_dir / ("sprite.png" if is_sprite else "image.png")

        if method == "nearest":
            k = max(1, round(p.scale))
            img.resize((img.width * k, img.height * k), Image.Resampling.NEAREST).save(out_path)
            return [OutputFile(out_path, "primary")]

        _ensure_comfy(ctx)
        name = _upload(ctx, imaging.flatten(img), "src.png")
        if method == "esrgan":
            g, nid = graphs.upscale_esrgan(name, p.scale, models=_models(style))
        else:
            g, nid = graphs.upscale_seedvr2(name, p.scale, _seed(p.seed), models=_models(style),
                                            color_correction=p.color_correction)
        up = _run(ctx, g, [nid], label=f"upscale {method}")[nid]
        if up.size != target:
            up = up.resize(target, Image.Resampling.LANCZOS)
        if alpha is not None:
            up = imaging.apply_mask(up, imaging.resize_alpha(alpha, up.size))
        up.save(out_path)
        return [OutputFile(out_path, "primary")]


# ---------------------------------------------------------------- Remover fundo

class RemoveBgParams(PipelineParams):
    hard_edges: bool = Field(False, description="alpha binário (bom para sprites)")
    threshold: int = Field(128, ge=1, le=254)
    trim: bool = Field(True, description="recortar no contorno do objeto")
    padding: int = Field(0, ge=0, le=256)


class RemoveBg(Pipeline):
    id = "2d.remove_bg"
    title = "Remover fundo"
    description = "Remove o fundo com BiRefNet e grava PNG transparente."
    resources = {"gpu"}
    requires_input = True
    same_asset = True
    Params = RemoveBgParams

    def run(self, ctx: Context, p: RemoveBgParams) -> list[OutputFile]:
        style = ctx.style()
        img = imaging.flatten(_input_image(ctx))
        _ensure_comfy(ctx)
        g, nid = graphs.remove_background(_upload(ctx, img, "src.png"), models=_models(style))
        mask = _run(ctx, g, [nid], label="removendo fundo")[nid]
        out = imaging.apply_mask(img, mask, hard=p.hard_edges, threshold=p.threshold)
        if p.trim:
            out = imaging.trim(out, p.padding)
        path = ctx.version_dir / "image.png"
        out.save(path)
        ctx.meta["kind"] = ctx.input.kind if ctx.input else "image"
        return [OutputFile(path, "primary")]


# ---------------------------------------------------------------- Pixelizer

class PixelizeParams(PipelineParams):
    mode: Literal["downscale", "grid"] = Field(
        "downscale", description="downscale: qualquer imagem; grid: imagem que já parece pixel art (detecta a grade)")
    size: int | None = Field(None, ge=4, le=512, description="maior lado em pixels de arte (padrão 2d.pixel.size ou 64)")
    cell: float | None = Field(None, ge=1, description="px da imagem por pixel de arte (sobrepõe size)")
    colors: int | None = Field(None, ge=2, le=256)
    palette: list[str] | None = Field(None, description="paleta hex explícita")
    use_project_palette: bool = True
    palette_from_refs: bool = Field(False, description="extrair paleta das refs do projeto")
    dither: bool | None = None
    outline: str | None = Field(None, description="cor hex do contorno externo, ex. #1a1c2c")
    remove_bg: Literal["auto", "yes", "no"] = "auto"
    crop: bool = True
    scale: int | None = Field(None, ge=1, le=16, description="fator nearest do arquivo final")
    min_island: int = Field(2, ge=1)


class Pixelize(Pipeline):
    id = "2d.pixelize"
    title = "2D Pixelizer"
    description = "Converte qualquer imagem em pixel art pixel-perfect (grade, paleta do projeto, alpha binário, contorno). Roda em CPU."
    resources = {"cpu"}
    kind = "sprite"
    requires_input = True
    same_asset = True
    Params = PixelizeParams

    def run(self, ctx: Context, p: PixelizeParams) -> list[OutputFile]:
        style = ctx.style()
        img = _input_image(ctx)
        cfg = _pixel_cfg(style, size=p.size, colors=p.colors, dither=p.dither, outline=p.outline, scale=p.scale, mode=p.mode)
        palette = _palette(style, p.palette, p.use_project_palette)
        if palette is None and p.palette_from_refs and style.get("refs"):
            palette = extract_palette([_open(Path(r)) for r in style["refs"]], cfg["colors"])
        ctx.progress(0.2, "pixelizando")
        res = pixelize(img, mode=cfg["mode"], size=None if p.cell else cfg["size"], cell=p.cell, colors=cfg["colors"],
                       palette=palette, dither=bool(cfg["dither"]), outline_color=cfg["outline"],
                       remove_bg={"auto": "auto", "yes": True, "no": False}[p.remove_bg], crop=p.crop,
                       scale=int(cfg["scale"] or 1), min_island=p.min_island)
        d = ctx.version_dir
        res.scaled.save(d / "sprite.png")
        imaging.preview(res.sprite, 512).save(d / "preview.png")
        ctx.meta["extra"] = {"pixel": {**cfg, "art_size": list(res.sprite.size), "colors_used": res.colors,
                                       "palette": res.palette(),
                                       "grid_cell": res.grid.cell if res.grid else None}}
        files = [OutputFile(d / "sprite.png", "primary"), OutputFile(d / "preview.png", "preview")]
        # mantém a fonte em alta para edições futuras
        src = (ctx.input_files("source") or ctx.input_files("primary"))[0]
        dest = d / f"source{src.suffix.lower()}"
        shutil.copy2(src, dest)
        files.append(OutputFile(dest, "source"))
        return files


# ---------------------------------------------------------------- Checkpoint (SD/SDXL/Pony/Illustrious)

class CheckpointParams(_GenParams):
    prompt: str = Field(..., description="o que desenhar; para Pony/Illustrious use tags (score_9, 1girl, ...)")
    negative: str = Field("", description="prompt negativo (vazio = 2d.checkpoint_negative do projeto ou padrão)")
    checkpoint: str = Field("", description="arquivo em models/checkpoints (vazio = 2d.checkpoint do projeto)")
    loras: list[dict[str, Any]] = Field(default_factory=list, description='[{"name": "x.safetensors", "strength": 0.8}]')
    asset_type: prompts.AssetType = "object"
    use_style: bool = Field(True, description="anexar o prompt de estilo do projeto")
    width: int | None = Field(None, ge=256, le=2048)
    height: int | None = Field(None, ge=256, le=2048)
    steps: int = Field(28, ge=1, le=100)
    cfg: float = Field(6.0, ge=1.0, le=20.0)
    sampler: str = "euler_ancestral"
    scheduler: str = "normal"
    clip_skip: int = Field(0, ge=0, le=4, description="2 para Pony/Illustrious/anime")


DEFAULT_NEGATIVE = "lowres, bad anatomy, bad hands, extra fingers, blurry, jpeg artifacts, watermark, text, signature"


class CheckpointText2D(Pipeline):
    id = "2d.checkpoint"
    title = "Text → 2D (checkpoint)"
    description = ("Gera com qualquer checkpoint SD/SDXL/Pony/Illustrious instalado no ComfyUI + LoRAs (ex.: modelos de "
                   "estilo ou modelos adultos em projetos +18). Fundo transparente e pixelização opcionais.")
    resources = {"gpu"}
    kind = "image"
    Params = CheckpointParams

    def run(self, ctx: Context, p: CheckpointParams) -> list[OutputFile]:
        style = ctx.style()
        ckpt = p.checkpoint or style.get("checkpoint") or ""
        _ensure_comfy(ctx)
        available = ctx.comfy.models("checkpoints")
        if not ckpt:
            raise ValueError(f"escolha um checkpoint (ou defina 2d.checkpoint no projeto). Instalados: {available}")
        if ckpt not in available:
            raise ValueError(f"checkpoint não encontrado no ComfyUI: {ckpt}. Instalados: {available}")
        loras = list(p.loras) + list(style.get("checkpoint_loras") or [])
        missing = [lr["name"] for lr in loras if lr["name"] not in ctx.comfy.models("loras")]
        if missing:
            raise ValueError(f"LoRAs não encontradas em models/loras: {missing}")
        pixel = _pixel_or_none(style, p)
        transparent = p.transparent if p.transparent is not None else (
            bool(style.get("transparent", True)) and p.asset_type not in prompts.OPAQUE_TYPES)
        parts = [p.prompt]
        if p.use_style and style.get("prompt"):
            parts.append(style["prompt"])
        if transparent:
            parts.append("simple background, white background")
        full = ", ".join(x for x in parts if x)
        negative = p.negative or style.get("checkpoint_negative") or DEFAULT_NEGATIVE
        is_xl = "xl" in ckpt.lower() or "pony" in ckpt.lower() or "illustrious" in ckpt.lower()
        side = 1024 if is_xl else 512
        w = imaging.snap(p.width or int(style.get("width", side)))
        h = imaging.snap(p.height or int(style.get("height", side)))
        seed = _seed(p.seed)
        ctx.log(f"{ckpt} | prompt: {full}")
        g, img_id, mask_id = graphs.text2img_checkpoint(full, negative, w, h, seed, checkpoint=ckpt, steps=p.steps,
                                                        cfg=p.cfg, sampler=p.sampler, scheduler=p.scheduler,
                                                        clip_skip=p.clip_skip, loras=loras, models=_models(style),
                                                        remove_bg=transparent)
        out = _run(ctx, g, [img_id, mask_id])
        ctx.meta.update(seed=seed, prompt=p.prompt, extra={"full_prompt": full, "negative": negative, "checkpoint": ckpt,
                                                           "loras": loras, "width": w, "height": h})
        return _finish(ctx, out[img_id], mask=out.get(mask_id) if mask_id else None, pixel=pixel, palette=_palette(style))


PIPELINES: list[Pipeline] = [Text2D(), Text2Pixel(), CheckpointText2D(), Image2D(), Edit(), Enhance(), RemoveBg(), Pixelize()]
