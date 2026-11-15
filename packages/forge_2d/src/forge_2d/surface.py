"""Texturas e luz 2D, sem modelos novos (numpy/scipy):

- `2d.seamless`: deixa uma imagem repetível sem emenda (ou gera do texto e já deixa repetível);
- `2d.material`: conjunto PBR a partir de uma textura (altura, normal, AO, rugosidade) com prévia em mosaico;
- `2d.normalmap`: normal map de sprite para a luz 2D do Godot/Unity (chanfro pela transparência + detalhe da
  luminância), quadro a quadro em animações.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import Field
from scipy import ndimage

from forge_core import OutputFile, Pipeline, PipelineParams
from forge_core.pipeline import Context

# ---------------------------------------------------------------- núcleo (testável sem ComfyUI)


def make_seamless(img: Image.Image, blend: float = 0.25, axes: str = "both") -> Image.Image:
    """Mistura a imagem com ela mesma deslocada meio quadro, com peso que zera nas bordas: as bordas do resultado
    vêm do miolo da original, então ladrilhar não mostra emenda. `blend` = fração do lado usada na transição."""
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    h, w = a.shape[:2]
    shifted = a
    if axes in ("both", "x"):
        shifted = np.roll(shifted, w // 2, axis=1)
    if axes in ("both", "y"):
        shifted = np.roll(shifted, h // 2, axis=0)

    def ramp(n: int, on: bool) -> np.ndarray:
        if not on:
            return np.ones(n, np.float32)
        t = np.minimum(np.arange(n), np.arange(n)[::-1]).astype(np.float32) / max(1.0, n * max(0.02, min(0.5, blend)))
        t = np.clip(t, 0, 1)
        return 0.5 - 0.5 * np.cos(np.pi * t)        # 0 na borda, 1 no miolo, suave

    wgt = ramp(h, axes in ("both", "y"))[:, None] * ramp(w, axes in ("both", "x"))[None, :]
    out = a * wgt[..., None] + shifted * (1 - wgt[..., None])
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA")


def seam_score(img: Image.Image) -> float:
    """Diferença média entre bordas opostas (0 = sem emenda), em níveis de 0–255."""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    return float((np.abs(a[0] - a[-1]).mean() + np.abs(a[:, 0] - a[:, -1]).mean()) / 2)


def tiled(img: Image.Image, n: int = 3) -> Image.Image:
    w, h = img.size
    out = Image.new("RGBA", (w * n, h * n))
    for y in range(n):
        for x in range(n):
            out.paste(img.convert("RGBA"), (x * w, y * h))
    return out


def _gray(a: np.ndarray) -> np.ndarray:
    rgb = a[..., :3].astype(np.float32) / 255.0
    return rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def _norm01(x: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(x, 1), np.percentile(x, 99)
    return np.clip((x - lo) / max(1e-6, hi - lo), 0, 1)


def height_from_albedo(img: Image.Image, detail: float = 1.0, blur: float = 1.0, wrap: bool = True) -> np.ndarray:
    """Altura 0..1 a partir da luminância: passa-alta (tira a iluminação geral) + suavização leve."""
    g = _gray(np.asarray(img.convert("RGBA")))
    mode = "wrap" if wrap else "reflect"
    low = ndimage.gaussian_filter(g, sigma=max(2.0, min(g.shape) / 16), mode=mode)
    h = g - low * 0.85
    if blur > 0:
        h = ndimage.gaussian_filter(h, sigma=blur, mode=mode)
    return _norm01(h) ** (1.0 / max(0.25, detail))


def normal_from_height(h: np.ndarray, strength: float = 4.0, wrap: bool = True, directx: bool = False) -> np.ndarray:
    """Normal (RGB 0..255) a partir da altura, com Sobel; OpenGL (Y para cima, Godot/Blender) ou DirectX (Unity/Unreal)."""
    mode = "wrap" if wrap else "nearest"
    dx = ndimage.sobel(h, axis=1, mode=mode) / 8.0
    dy = ndimage.sobel(h, axis=0, mode=mode) / 8.0
    nx, ny, nz = -dx * strength, dy * strength, np.ones_like(h)
    if directx:
        ny = -ny
    n = np.stack([nx, ny, nz], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return ((n * 0.5 + 0.5) * 255 + 0.5).astype(np.uint8)


def ao_from_height(h: np.ndarray, radius: float = 6.0, strength: float = 1.5, wrap: bool = True) -> np.ndarray:
    """Oclusão aproximada: o quanto cada ponto está abaixo da vizinhança (cavidades escurecem)."""
    around = ndimage.gaussian_filter(h, sigma=radius, mode="wrap" if wrap else "reflect")
    return np.clip(1.0 - np.maximum(0.0, around - h) * strength * 3, 0, 1)


def roughness_from_albedo(img: Image.Image, base: float = 0.65, wrap: bool = True) -> np.ndarray:
    """Rugosidade: mais áspero onde há variação local e escuro; superfícies lisas e claras ficam mais polidas."""
    g = _gray(np.asarray(img.convert("RGBA")))
    mode = "wrap" if wrap else "reflect"
    mean = ndimage.uniform_filter(g, 5, mode=mode)
    var = ndimage.uniform_filter(g * g, 5, mode=mode) - mean * mean
    rough = base + 0.35 * _norm01(np.sqrt(np.maximum(var, 0))) - 0.2 * (_norm01(g) - 0.5)
    return np.clip(rough, 0, 1)


def sprite_height(img: Image.Image, bevel: float = 6.0, detail: float = 0.35) -> np.ndarray:
    """Altura de um sprite: chanfro a partir da borda da transparência (forma arredondada) + detalhe da luminância."""
    a = np.asarray(img.convert("RGBA"))
    inside = a[..., 3] > 16
    dist = ndimage.distance_transform_edt(inside)
    t = np.clip(dist / max(1.0, bevel), 0, 1)
    shape = np.sin(t * np.pi / 2)                      # sobe rápido na borda e achata no miolo
    g = _gray(a)
    det = g - ndimage.gaussian_filter(g, sigma=2)
    # altura em escala de pixels: a inclinação da borda fica igual com chanfro de 4 ou de 60 px
    return (shape * (bevel / 6.0) + detail * det * inside) * inside


def sprite_normal(img: Image.Image, bevel: float = 6.0, detail: float = 0.35, strength: float = 3.0,
                  directx: bool = False, quantize: int = 0) -> Image.Image:
    """Normal map RGBA do sprite (fora do sprite: normal reta e alpha 0, como o Godot espera)."""
    a = np.asarray(img.convert("RGBA"))
    h = sprite_height(img, bevel, detail)
    n = normal_from_height(h, strength, wrap=False, directx=directx).astype(np.float32)
    if quantize:   # pixel art: poucas direções, sem gradiente borrado
        v = n / 127.5 - 1
        ang = np.round(np.arctan2(v[..., 1], v[..., 0]) / (2 * np.pi / quantize)) * (2 * np.pi / quantize)
        tilt = np.round(np.sqrt(v[..., 0] ** 2 + v[..., 1] ** 2) * 2) / 2
        vx, vy = np.cos(ang) * tilt, np.sin(ang) * tilt
        vz = np.sqrt(np.clip(1 - vx * vx - vy * vy, 0, 1))
        n = (np.stack([vx, vy, vz], -1) * 0.5 + 0.5) * 255
    out = np.dstack([n.astype(np.uint8), a[..., 3]])
    out[a[..., 3] <= 16, :3] = (128, 128, 255)
    return Image.fromarray(out, "RGBA")


def lit_preview(albedo: Image.Image, normal: Image.Image, light=(-0.6, -0.6, 0.55)) -> Image.Image:
    """Sombreamento simples (lambert) para conferir o normal map."""
    al = np.asarray(albedo.convert("RGBA")).astype(np.float32) / 255
    nm = np.asarray(normal.convert("RGB")).astype(np.float32) / 127.5 - 1
    nm[..., 1] = -nm[..., 1]
    lv = np.array(light, np.float32)
    lv /= np.linalg.norm(lv)
    shade = np.clip((nm * lv).sum(-1), 0, 1) * 0.85 + 0.25
    rgb = np.clip(al[..., :3] * shade[..., None], 0, 1)
    return Image.fromarray((np.dstack([rgb, al[..., 3:]]) * 255 + 0.5).astype(np.uint8), "RGBA")


# ---------------------------------------------------------------- pipelines

def _input(ctx: Context) -> Image.Image:
    files = ctx.input_files("primary") or ctx.input_files()
    if not files:
        raise ValueError("asset de entrada sem arquivo")
    im = Image.open(files[0])
    im.load()
    return im.convert("RGBA")


class SeamlessParams(PipelineParams):
    input_asset: str | None = Field(None, description="imagem a deixar repetível (ou use prompt para gerar)")
    prompt: str | None = Field(None, description="sem input_asset: descreve a textura a gerar (ex.: mossy stone bricks)")
    blend: float = Field(0.25, ge=0.05, le=0.5, description="largura da transição, em fração do lado")
    axes: Literal["both", "x", "y"] = Field("both", description="repetir nos dois eixos ou só em um")
    size: int = Field(1024, ge=256, le=2048, description="lado da textura gerada (com prompt)")
    seed: int | None = None


class Seamless(Pipeline):
    id = "2d.seamless"
    title = "Textura sem emenda"
    description = ("Deixa uma textura repetível sem emenda (chão, parede, terreno) ou gera do texto e já deixa repetível; "
                   "grava a prévia em mosaico 3×3 e a medida da emenda.")
    resources = {"cpu"}
    kind = "image"
    accepts_input = True
    Params = SeamlessParams

    def default_slug(self, params: SeamlessParams) -> str:
        if params.input_asset:
            return Path(params.input_asset.split("@")[0]).stem + "-seamless"
        return super().default_slug(params)

    def run(self, ctx: Context, p: SeamlessParams) -> list[OutputFile]:
        if ctx.input is not None or ctx.input_path is not None:
            img = _input(ctx)
        elif p.prompt:
            from . import graphs, prompts
            from .pipelines import _ensure_comfy, _models, _run, _seed

            style = ctx.style()
            full = prompts.compose(f"seamless tileable texture, {p.prompt}, flat even lighting, top-down, no shadows, "
                                   "fills the whole frame", style, "tile", False, True)
            models = _models(style)
            _ensure_comfy(ctx, {"diffusion_models": models["zimage_unet"], "text_encoders": models["zimage_clip"], "vae": models["zimage_vae"]})
            seed = _seed(p.seed)
            g, img_id, _ = graphs.text2img_zimage(full, p.size, p.size, seed, 8, models=models, remove_bg=False)
            img = _run(ctx, g, [img_id], label="gerando a textura")[img_id].convert("RGBA")
            ctx.meta.update(seed=seed, prompt=p.prompt)
        else:
            raise ValueError("informe input_asset (imagem) ou prompt (gerar a textura)")
        ctx.progress(0.9, "tirando a emenda")
        before = seam_score(img)
        out = make_seamless(img, p.blend, p.axes)
        d = ctx.version_dir
        out.save(d / "texture.png")
        prev = tiled(out, 3)
        prev.thumbnail((1536, 1536))
        prev.save(d / "preview.png")
        ctx.meta["kind"] = "image"
        ctx.meta.setdefault("extra", {}).update({"seamless": {"axes": p.axes, "blend": p.blend, "seam_before": round(before, 2),
                                                              "seam_after": round(seam_score(out), 2)}, "size": list(out.size)})
        return [OutputFile(d / "texture.png", "primary"), OutputFile(d / "preview.png", "preview")]


class MaterialParams(PipelineParams):
    input_asset: str = Field(..., description="textura (de preferência sem emenda: rode 2d.seamless antes)")
    strength: float = Field(4.0, ge=0.5, le=20, description="intensidade do relevo no normal map")
    detail: float = Field(1.0, ge=0.25, le=3, description="detalhe da altura (mais = relevo mais fino)")
    roughness: float = Field(0.65, ge=0, le=1, description="rugosidade de base")
    normal_format: Literal["opengl", "directx"] = Field("opengl", description="opengl: Godot/Blender; directx: Unity/Unreal")
    seamless: bool = Field(True, description="calcular como textura repetível (bordas dão a volta)")


class Material(Pipeline):
    id = "2d.material"
    title = "Material PBR da textura"
    description = ("Gera altura, normal, oclusão (AO) e rugosidade a partir de uma textura, prontos para Godot/Unity/"
                   "Blender, com prévia iluminada em mosaico. Roda em CPU.")
    resources = {"cpu"}
    kind = "image"
    requires_input = True
    Params = MaterialParams

    def default_slug(self, params: MaterialParams) -> str:
        return Path(params.input_asset.split("@")[0]).stem + "-material"

    def run(self, ctx: Context, p: MaterialParams) -> list[OutputFile]:
        img = _input(ctx).convert("RGB").convert("RGBA")
        d = ctx.version_dir
        ctx.progress(0.2, "altura")
        h = height_from_albedo(img, p.detail, wrap=p.seamless)
        ctx.progress(0.5, "normal, AO e rugosidade")
        normal = Image.fromarray(normal_from_height(h, p.strength, wrap=p.seamless, directx=p.normal_format == "directx"), "RGB")
        ao = ao_from_height(h, wrap=p.seamless)
        rough = roughness_from_albedo(img, p.roughness, wrap=p.seamless)
        files = []
        for name, im, role in (("albedo.png", img.convert("RGB"), "primary"),
                               ("normal.png", normal, "texture"),
                               ("height.png", Image.fromarray((h * 255 + 0.5).astype(np.uint8), "L"), "texture"),
                               ("ao.png", Image.fromarray((ao * 255 + 0.5).astype(np.uint8), "L"), "texture"),
                               ("roughness.png", Image.fromarray((rough * 255 + 0.5).astype(np.uint8), "L"), "texture")):
            im.save(d / name)
            files.append(OutputFile(d / name, role))
        nm_gl = normal if p.normal_format == "opengl" else Image.fromarray(normal_from_height(h, p.strength, wrap=p.seamless), "RGB")
        lit = lit_preview(img, nm_gl)
        aoim = np.asarray(lit).astype(np.float32)
        aoim[..., :3] *= ao[..., None] * 0.5 + 0.5
        lit = Image.fromarray(np.clip(aoim, 0, 255).astype(np.uint8), "RGBA")
        prev = tiled(lit, 2 if p.seamless else 1)
        prev.thumbnail((1024, 1024))
        prev.save(d / "preview.png")
        files.append(OutputFile(d / "preview.png", "preview"))
        (d / "material.json").write_text(json.dumps({"albedo": "albedo.png", "normal": "normal.png", "normal_format": p.normal_format,
                                                     "height": "height.png", "ao": "ao.png", "roughness": "roughness.png"}, indent=2), encoding="utf-8")
        files.append(OutputFile(d / "material.json", "part", "application/json"))
        ctx.meta["kind"] = "image"
        ctx.meta.setdefault("extra", {}).update({"material": {"normal_format": p.normal_format, "strength": p.strength}, "size": list(img.size)})
        return files


class NormalMapParams(PipelineParams):
    input_asset: str = Field(..., description="sprite, imagem com transparência ou animação")
    bevel: float | None = Field(None, ge=1, le=256, description="largura do chanfro a partir da borda, em pixels "
                                "(vazio = 6% do menor lado do sprite, no mínimo 3)")
    detail: float = Field(0.35, ge=0, le=2, description="quanto da luminância vira relevo")
    strength: float = Field(3.0, ge=0.5, le=12)
    normal_format: Literal["opengl", "directx"] = Field("opengl", description="opengl: Godot; directx: Unity")
    pixel_art: bool | None = Field(None, description="normais em poucas direções, sem borrar (vazio = automático para sprites)")


class NormalMap(Pipeline):
    id = "2d.normalmap"
    title = "Normal map de sprite (luz 2D)"
    description = ("Gera o normal map de um sprite ou de cada quadro de uma animação para a luz 2D do Godot "
                   "(CanvasTexture) e do Unity (Sprite Lit), com prévia iluminada. Roda em CPU.")
    resources = {"cpu"}
    kind = "image"
    requires_input = True
    Params = NormalMapParams

    def default_slug(self, params: NormalMapParams) -> str:
        return Path(params.input_asset.split("@")[0]).stem + "-normal"

    def run(self, ctx: Context, p: NormalMapParams) -> list[OutputFile]:
        d = ctx.version_dir
        inp = ctx.input
        pixel = p.pixel_art if p.pixel_art is not None else bool(inp is not None and (inp.kind == "sprite" or (inp.extra or {}).get("pixel")))
        q = 8 if pixel else 0
        dx = p.normal_format == "directx"

        def bevel_for(im: Image.Image) -> float:
            if p.bevel:
                return p.bevel
            box = im.getbbox() or (0, 0, *im.size)
            return max(3.0, 0.06 * min(box[2] - box[0], box[3] - box[1]))

        if inp is not None and inp.kind == "animation":
            from .animation import Animation

            src_dir = ctx.project.store.version_dir(inp.slug, inp.version)
            anim = Animation.load(src_dir)
            normals = []
            for i, fr in enumerate(anim.frames):
                ctx.progress(0.1 + 0.8 * i / max(1, len(anim.frames)), f"quadro {i + 1}/{len(anim.frames)}")
                normals.append(sprite_normal(fr, bevel_for(fr), p.detail, p.strength, dx, q))
            (d / "frames").mkdir(exist_ok=True)
            files = []
            for i, n in enumerate(normals):
                n.save(d / f"frames/{i:03d}_n.png")
                files.append(OutputFile(d / f"frames/{i:03d}_n.png", "part"))
            from .animation import build_sheet

            sheet, _ = build_sheet(normals, layout="row")
            sheet.save(d / "sheet_n.png")
            if (src_dir / "sheet.png").exists():
                shutil.copy2(src_dir / "sheet.png", d / "sheet.png")
                files.append(OutputFile(d / "sheet.png", "texture"))
            lit = lit_preview(anim.frames[0], sprite_normal(anim.frames[0], bevel_for(anim.frames[0]), p.detail, p.strength, False, q))
            files.insert(0, OutputFile(d / "sheet_n.png", "primary"))
            ctx.meta.setdefault("extra", {})["frames"] = len(normals)
        else:
            img = _input(ctx)
            bv = bevel_for(img)
            n = sprite_normal(img, bv, p.detail, p.strength, dx, q)
            n.save(d / "normal.png")
            img.save(d / "albedo.png")
            lit = lit_preview(img, sprite_normal(img, bv, p.detail, p.strength, False, q))
            files = [OutputFile(d / "normal.png", "primary"), OutputFile(d / "albedo.png", "texture")]
        scale = max(1, 384 // max(lit.size))
        if scale > 1:
            lit = lit.resize((lit.width * scale, lit.height * scale), Image.Resampling.NEAREST)
        lit.save(d / "preview.png")
        files.append(OutputFile(d / "preview.png", "preview"))
        ctx.meta["kind"] = "image"
        ctx.meta.setdefault("extra", {}).update({"normalmap": {"format": p.normal_format, "bevel": p.bevel or "auto", "pixel_art": pixel}})
        return files


SURFACE_PIPELINES: list[Pipeline] = [Seamless(), Material(), NormalMap()]
