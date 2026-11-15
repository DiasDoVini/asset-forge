"""Fatiador: recorta peças de uma imagem inteira (ex.: o personagem todo gerado por IA) e encaixa cada uma
no canvas exato da sua spec, sem IA, a partir de um JSON de fatiamento.

    {
      "format": "asset-forge-slice/1",
      "source_size": [384, 768],      # tamanho a que as coordenadas se referem (a imagem é reescalada se diferir)
      "background": "auto",           # auto (cor sólida dos cantos) | "#rrggbb" | "alpha" (já tem transparência)
      "tolerance": 40,
      "parts": [{
        "name": "torso",
        "file_name": "part_chest_vaniacoat_torso_v02.png",
        "spec": "torso", "palette_group": "personagem/chest", "tags": ["vania", "noble"],
        "rect": [64, 176, 320, 464],  # recorte na fonte (x0, y0, x1, y1; topo-esquerda; x1/y1 exclusivos)
        "polygon": [[x, y], ...],     # opcional: só o que está dentro (coords da fonte)
        "erase": [[[x, y], ...]],     # opcional: polígonos apagados (ex.: braço na frente do torso)
        "target": [0, 0, 16, 18]      # caixa no canvas (x, y, l, a; origem embaixo-esquerda) onde o rect cai
      }]
    }

O rect cai inteiro na caixa `target` (padrão: o canvas todo): com uma fonte registrada (montagem do rig
ampliada N vezes e a IA mantendo o enquadramento), o rect é exatamente a peça × N e a caixa é o canvas.
Para cada célula do alvo: opaca se ≥ `alpha_cover` da área da fonte é opaca; a cor é a MODA das cores da
fonte já quantizadas na paleta travada (núcleo + grupo, sem a cor do contorno), e os pixels quase pretos
da linha da IA só votam quando são a maioria (viram o tom mais escuro). Depois: recorte à área segura com
1 px para o contorno, borda escura da IA descascada, ilhas removidas, contorno de 1 px automático.
Determinístico.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from .fit import _palette_colors, key_background, remove_islands
from .palette import hex_to_rgb, quantize_array, rgb_to_hex, rgb_to_oklab
from .raster import outline_ring, to_image, to_rgba
from .spec import AssetSpec, PaletteSet, resolve_spec

FORMAT = "asset-forge-slice/1"
Point = tuple[float, float]


class SlicePart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    file_name: str | None = None
    spec: str | dict[str, Any]
    palette_group: str | None = None
    side: Literal["L", "R"] | None = None
    material: str | None = None
    tags: list[str] = Field(default_factory=list)
    rect: tuple[float, float, float, float]
    polygon: list[Point] | None = None
    erase: list[list[Point]] = Field(default_factory=list)
    erase_fill: bool = Field(True, description="células apagadas dentro da peça recebem a cor da vizinha mais "
                                               "próxima (o oclusor, ex.: o braço, não deixa buraco)")
    target: tuple[int, int, int, int] | None = None
    mask: str | None = Field(None, description="silhueta de referência (arquivo/asset no canvas da spec): só as "
                                               "células preenchidas dela ficam, e as que a fonte não cobre são "
                                               "preenchidas pela vizinha (prende as juntas do rig à peça atual)")
    clear: list[tuple[int, int, int, int]] = Field(default_factory=list,
                                                   description="caixas (x, y, l, a; origem embaixo) esvaziadas no alvo")
    colors: list[str] | None = Field(None, description="subconjunto da paleta travada usado na quantização (ex.: só "
                                                       "as cores do material, sem os violetas do núcleo)")
    alpha_cover: float = Field(0.5, gt=0, le=1)
    dark_threshold: float = Field(0.10, description="distância OKLab até a cor do contorno abaixo da qual o pixel "
                                                    "da fonte é 'linha da IA'")
    outline: bool = True


class SliceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["asset-forge-slice/1"] = FORMAT
    source_size: tuple[int, int] | None = None
    background: str = "auto"
    tolerance: float = 40.0
    parts: list[SlicePart] = Field(..., min_length=1)


class SliceResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    part: SlicePart
    spec: AssetSpec
    image: Any                       # PIL.Image (RGBA, canvas da spec)
    group: str | None


def _poly_mask(size: tuple[int, int], polys: list[list[Point]]) -> np.ndarray:
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for poly in polys:
        if len(poly) >= 3:
            d.polygon([tuple(p) for p in poly], fill=255)
    return np.asarray(m) > 0


def prepare_source(img: Image.Image, sl: SliceSpec) -> np.ndarray:
    """RGBA da fonte no tamanho `source_size`, com o fundo removido."""
    if sl.source_size and tuple(img.size) != tuple(sl.source_size):
        img = img.resize(tuple(sl.source_size), Image.Resampling.NEAREST)
    arr = to_rgba(img).copy()
    if sl.background == "alpha":
        arr[..., 3] = np.where(arr[..., 3] >= 128, 255, 0)
        return arr
    arr[..., 3] = 255
    if sl.background == "auto":
        return key_background(arr, sl.tolerance)
    bg = np.array(hex_to_rgb(sl.background), np.int32)
    close = np.sqrt(((arr[..., :3].astype(np.int32) - bg) ** 2).sum(-1)) <= sl.tolerance
    arr[close, 3] = 0
    return arr


def _fill_mask(ref: Image.Image, outline: str) -> np.ndarray:
    a = to_rgba(ref)
    oc = np.all(a[..., :3] == hex_to_rgb(outline), axis=2)
    return (a[..., 3] > 0) & ~oc


def slice_part(src: np.ndarray, part: SlicePart, spec: AssetSpec, palette: PaletteSet | None,
               group: str | None, mask_image: Image.Image | None = None) -> Image.Image:
    H, W = src.shape[:2]
    x0, y0, x1, y1 = part.rect
    keep = src[..., 3] > 0
    if part.polygon:
        keep &= _poly_mask((W, H), [part.polygon])
    erased = np.zeros_like(keep)
    if part.erase:
        erased = keep & _poly_mask((W, H), part.erase)
        keep &= ~erased
    tx, ty, tw, th = part.target or (0, 0, spec.width, spec.height)
    colors = _palette_colors(spec, palette, group)
    if part.colors:
        from .spec import norm_hex

        want = [norm_hex(c) for c in part.colors]
        off = [c for c in want if colors is not None and c not in colors]
        if off:
            raise ValueError(f"{part.name}: cores fora da paleta travada: {off}")
        colors = sorted(want)
    rgba = src.copy()
    rgba[..., 3] = np.where(keep, 255, 0)
    q = quantize_array(rgba, colors) if colors else rgba
    oc_lab = rgb_to_oklab(np.array(hex_to_rgb(spec.outline_color), np.float64))
    dark = np.linalg.norm(rgb_to_oklab(src[..., :3].astype(np.float64)) - oc_lab, axis=-1) < part.dark_threshold
    darkest = None
    if colors:
        darkest = min(colors, key=lambda c: float(rgb_to_oklab(np.array(hex_to_rgb(c), np.float64))[0]))
    small = np.zeros((th, tw, 4), np.uint8)
    to_fill = np.zeros((th, tw), bool)
    linecell = np.zeros((th, tw), bool)          # célula em que a linha escura da fonte é a maioria
    xs = np.linspace(x0, x1, tw + 1)
    ys = np.linspace(y0, y1, th + 1)
    for r in range(th):
        ra, rb = int(round(ys[r])), max(int(round(ys[r + 1])), int(round(ys[r])) + 1)
        for c in range(tw):
            ca, cb = int(round(xs[c])), max(int(round(xs[c + 1])), int(round(xs[c])) + 1)
            ra_, rb_ = max(ra, 0), min(rb, H)
            ca_, cb_ = max(ca, 0), min(cb, W)
            area = (rb - ra) * (cb - ca)
            if rb_ <= ra_ or cb_ <= ca_:
                continue
            k = keep[ra_:rb_, ca_:cb_]
            if k.sum() < part.alpha_cover * area:
                if part.erase_fill and (k.sum() + erased[ra_:rb_, ca_:cb_].sum()) >= part.alpha_cover * area:
                    to_fill[r, c] = True
                continue
            dk = dark[ra_:rb_, ca_:cb_] & k
            light = k & ~dk
            cell = q[ra_:rb_, ca_:cb_]
            if light.sum() >= dk.sum() or darkest is None:
                pick = cell[light] if light.any() else cell[k]
                cols, cnt = np.unique(pick[:, :3], axis=0, return_counts=True)
                order = sorted(range(len(cols)), key=lambda i: (-int(cnt[i]), rgb_to_hex(cols[i])))
                small[r, c, :3] = cols[order[0]]
            else:
                small[r, c, :3] = hex_to_rgb(darkest)
                linecell[r, c] = True
            small[r, c, 3] = 255
    # o contorno da fonte: células de linha na borda da silhueta saem (uma camada); o contorno é redesenhado
    op = small[..., 3] > 0
    pad = np.pad(op, 1, constant_values=False)
    edge = op & (~pad[:-2, 1:-1] | ~pad[2:, 1:-1] | ~pad[1:-1, :-2] | ~pad[1:-1, 2:])
    peel = linecell & edge
    if peel.sum() < op.sum():
        small[peel] = 0
    out = np.zeros((spec.height, spec.width, 4), np.uint8)
    fill = np.zeros((spec.height, spec.width), bool)
    top = spec.height - (ty + th)
    for r in range(th):
        for c in range(tw):
            rr, cc = top + r, tx + c
            if 0 <= rr < spec.height and 0 <= cc < spec.width:
                out[rr, cc] = small[r, c]
                fill[rr, cc] = to_fill[r, c]
    if mask_image is not None:
        m_ref = _fill_mask(mask_image, spec.outline_color)
        if m_ref.shape != out.shape[:2]:
            raise ValueError(f"{part.name}: a máscara tem {m_ref.shape[1]}x{m_ref.shape[0]}, o canvas {spec.width}x{spec.height}")
        out[~m_ref] = 0
        fill = m_ref & (out[..., 3] == 0)
    for x, y, w, h in part.clear:
        out[spec.height - (y + h):spec.height - y, x:x + w] = 0
        fill[spec.height - (y + h):spec.height - y, x:x + w] = False
    # preenche o que o oclusor apagou (ou o que a máscara pede e a fonte não cobriu) com a cor da célula
    # conhecida mais próxima, em anéis 4-vizinhos (determinístico)
    while fill.any():
        known = out[..., 3] > 0
        if not known.any():
            break
        grew = False
        for r, c in zip(*np.nonzero(fill)):
            for dr, dc in ((0, -1), (-1, 0), (0, 1), (1, 0)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < spec.height and 0 <= cc < spec.width and known[rr, cc]:
                    out[r, c] = out[rr, cc]
                    fill[r, c] = False
                    grew = True
                    break
        if not grew:
            break
    m = 1 if (part.outline and spec.outline == "required") else 0
    if spec.safe_area is not None:          # conteúdo + contorno dentro da área segura
        sx0, sr0, sx1, sr1 = spec.safe_box_rows()
        inside = np.zeros(out.shape[:2], bool)
        inside[max(sr0 + m, 0):max(sr1 - m, 0), max(sx0 + m, 0):max(sx1 - m, 0)] = True
        if m and spec.safe_area[1] == 0:    # borda de baixo do canvas: junta aberta, sem contorno
            inside[max(sr0 + m, 0):sr1, max(sx0 + m, 0):max(sx1 - m, 0)] = True
        out[~inside] = 0
    out = remove_islands(out, spec.min_island, spec.island_connectivity, set(spec.sparkle_colors))
    if m:
        ring = outline_ring(out[..., 3] > 0)
        out[ring, :3] = hex_to_rgb(spec.outline_color)
        out[ring, 3] = 255
    return to_image(out)


def slice_image(img: Image.Image, sl: SliceSpec | dict[str, Any], palette: PaletteSet | None = None,
                specs: dict[str, AssetSpec] | None = None, masks: dict[str, Image.Image] | None = None
                ) -> list[SliceResult]:
    """`masks`: {valor de part.mask: imagem} (o chamador resolve arquivos/assets; sem a chave, abre como arquivo)."""
    sl = sl if isinstance(sl, SliceSpec) else SliceSpec.model_validate(sl)
    src = prepare_source(img, sl)
    out = []
    for part in sl.parts:
        spec = resolve_spec(part.spec, specs)
        group = part.palette_group or spec.palette_group
        mimg = None
        if part.mask:
            mimg = (masks or {}).get(part.mask)
            if mimg is None:
                with Image.open(part.mask) as im:
                    mimg = im.convert("RGBA")
        out.append(SliceResult(part=part, spec=spec, image=slice_part(src, part, spec, palette, group, mimg),
                               group=group))
    return out


def overlay(img: Image.Image, sl: SliceSpec | dict[str, Any], scale: int = 1) -> Image.Image:
    """A fonte com o fundo removido sobre xadrez, com os retângulos (ciano), polígonos (verde), áreas
    apagadas (vermelho) e a grade das células do alvo (fina)."""
    sl = sl if isinstance(sl, SliceSpec) else SliceSpec.model_validate(sl)
    src = to_image(prepare_source(img, sl))
    W, H = src.size
    base = Image.new("RGBA", (W, H), (0x2a, 0x28, 0x44, 255))
    base.alpha_composite(src)
    if scale != 1:
        base = base.resize((W * scale, H * scale), Image.Resampling.NEAREST)
    d = ImageDraw.Draw(base)
    s = scale
    for part in sl.parts:
        x0, y0, x1, y1 = part.rect
        spec_w = part.target[2] if part.target else None
        spec_h = part.target[3] if part.target else None
        if spec_w is None:
            sp = resolve_spec(part.spec)
            spec_w, spec_h = sp.width, sp.height
        for i in range(spec_w + 1):
            x = x0 + (x1 - x0) * i / spec_w
            d.line([(x * s, y0 * s), (x * s, y1 * s)], fill=(0x9f, 0xe3, 0xec, 70))
        for j in range(spec_h + 1):
            y = y0 + (y1 - y0) * j / spec_h
            d.line([(x0 * s, y * s), (x1 * s, y * s)], fill=(0x9f, 0xe3, 0xec, 70))
        d.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], outline=(0x9f, 0xe3, 0xec, 255), width=2)
        if part.polygon:
            d.line([(x * s, y * s) for x, y in [*part.polygon, part.polygon[0]]], fill=(0x7c, 0xe0, 0x8a, 255), width=2)
        for poly in part.erase:
            d.line([(x * s, y * s) for x, y in [*poly, poly[0]]], fill=(0xff, 0x40, 0x40, 255), width=2)
        d.text((x0 * s + 3, y0 * s + 3), part.name, fill=(0x9f, 0xe3, 0xec, 255))
    return base
