"""Encaixa QUALQUER imagem (render de IA sem fundo, rascunho, foto de papel) na spec: canvas exato,
conteúdo na área segura ancorado no pivô, alfa binário, paleta travada, sem ilhas e contorno de 1 px.

Determinístico: a mesma entrada gera o mesmo PNG (sem aleatoriedade, sem pontilhado).

Regras de âncora (spec.anchor, vertical; spec.anchor_x, horizontal), com y a partir de baixo:
    bottom  a base da caixa (contorno incluso) fica na linha do pivô     (cabeça, torso: pescoço/quadril no pivô)
    top     o topo da caixa fica na linha do pivô                         (membros, mão, capa: pendurados no pivô)
    center  a caixa fica centrada no pivô                                 (pelve, ícone, item de costas)
    floor   a base da caixa fica na base da área segura                   (bota: sola em y=1)
    none    centrada na área segura
    anchor_x: center (centro no pivô) | right (borda direita do conteúdo na coluna do pivô) | left
Depois a caixa é empurrada para dentro da área segura (que é a regra dura).
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

from .palette import hex_to_rgb, quantize_array, reduce_colors, rgb_to_oklab, rgb_to_hex
from .raster import bbox, border_pixels, components, outline_ring, to_image, to_rgba
from .spec import AssetSpec, PaletteSet


def _round(x: float) -> int:
    return int(math.floor(x + 0.5))


def key_background(arr: np.ndarray, tolerance: float = 24.0) -> np.ndarray:
    """Imagem sem alfa: remove o fundo de cor sólida (a cor dos 4 cantos, se forem iguais),
    só a parte conectada às bordas (não fura o desenho)."""
    rgb = arr[..., :3].astype(np.int32)
    corners = np.array([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
    if np.abs(corners - corners[0]).max() > tolerance:
        return arr
    close = np.sqrt(((rgb - corners[0]) ** 2).sum(-1)) <= tolerance
    h, w = close.shape
    bg = np.zeros_like(close)
    for comp in components(close, 4):
        ys, xs = comp[:, 0], comp[:, 1]
        if ys.min() == 0 or xs.min() == 0 or ys.max() == h - 1 or xs.max() == w - 1:
            bg[ys, xs] = True
    out = arr.copy()
    out[bg, 3] = 0
    return out


def _resize(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Redução por área (BOX, alfa pré-multiplicado pelo Pillow) ou ampliação por vizinho mais próximo."""
    w, h = size
    src_h, src_w = arr.shape[:2]
    if (w, h) == (src_w, src_h):
        return arr.copy()
    img = to_image(arr)
    method = Image.Resampling.BOX if (w < src_w or h < src_h) else Image.Resampling.NEAREST
    return to_rgba(img.resize((w, h), method))


def _limit_colors(arr: np.ndarray, max_colors: int, colors: list[str] | None) -> np.ndarray:
    """Mantém as N cores mais usadas (empate: hex) e requantiza o resto para elas."""
    op = arr[..., 3] == 255
    if not op.any():
        return arr
    uniq, cnt = np.unique(arr[op, :3], axis=0, return_counts=True)
    if len(uniq) <= max_colors:
        return arr
    order = sorted(range(len(uniq)), key=lambda i: (-int(cnt[i]), rgb_to_hex(uniq[i])))
    keep = [rgb_to_hex(uniq[i]) for i in order[:max_colors]]
    return quantize_array(arr, keep)


def _peel_dark_border(arr: np.ndarray, outline_color: str, threshold: float = 0.12) -> np.ndarray:
    """Tira o contorno escuro que a imagem de origem já tinha (para não dobrar com o contorno novo).
    Só age se a maioria da borda da silhueta for escura."""
    op = arr[..., 3] == 255
    border = border_pixels(op, edges=True)
    if border.sum() == 0:
        return arr
    lab = rgb_to_oklab(arr[..., :3])
    ref = rgb_to_oklab(np.array(hex_to_rgb(outline_color)))
    dark = np.linalg.norm(lab - ref, axis=-1) < threshold
    if (dark & border).sum() < 0.5 * border.sum():
        return arr
    out = arr.copy()
    peel = dark & border
    if peel.sum() >= op.sum():  # não apaga o desenho inteiro
        return arr
    out[peel, 3] = 0
    out[peel, :3] = 0
    return out


def remove_islands(arr: np.ndarray, min_island: int, connectivity: int = 8, keep_colors: set[str] | None = None) -> np.ndarray:
    """Apaga grupos opacos menores que `min_island` (menos os feitos só de `keep_colors`)."""
    if min_island <= 1:
        return arr
    out = arr.copy()
    for comp in components(arr[..., 3] > 0, connectivity):
        if len(comp) >= min_island:
            continue
        cols = {rgb_to_hex(arr[y, x, :3]) for y, x in comp}
        if keep_colors and cols <= keep_colors:
            continue
        out[comp[:, 0], comp[:, 1]] = 0
    return out


def _palette_colors(spec: AssetSpec, palette: PaletteSet | None, group: str | None) -> list[str] | None:
    if palette is None or palette.fallback:
        return None
    cols = palette.allowed(group if group is not None else spec.palette_group)
    if spec.outline in ("required", "none") and len(cols) > 1:
        cols = cols - {spec.outline_color}   # a cor do contorno fica exclusiva do contorno
    return sorted(cols)


def _quantize(arr: np.ndarray, spec: AssetSpec, palette: PaletteSet | None, group: str | None) -> np.ndarray:
    colors = _palette_colors(spec, palette, group)
    reserve = 1 if spec.outline == "required" else 0
    limit = spec.max_colors - reserve if spec.max_colors else None
    if colors is not None:
        arr = quantize_array(arr, colors)
    else:
        arr = reduce_colors(arr, limit or 16)
    if limit and limit > 0:
        arr = _limit_colors(arr, limit, colors)
    return arr


def _box_origin(spec: AssetSpec, bw: int, bh: int, cw: int, margin: int) -> tuple[int, int]:
    """Canto inferior esquerdo (x, y, origem embaixo) da caixa de conteúdo+contorno."""
    sx, sy, sw, sh = spec.safe_rect()
    px, py = spec.pivot_xy()
    if spec.anchor == "bottom":
        by = _round(py)
    elif spec.anchor == "top":
        by = _round(py) - bh + 1 if py < spec.height else spec.height - bh
    elif spec.anchor == "floor":
        by = sy
    elif spec.anchor == "center":
        by = _round(py - bh / 2)
    else:
        by = sy + (sh - bh) // 2
    if spec.anchor == "none":
        bx = sx + (sw - bw) // 2
    elif spec.anchor_x == "right":
        bx = _round(px) - cw - margin + 1
    elif spec.anchor_x == "left":
        bx = _round(px) - margin
    else:
        bx = _round(px - bw / 2)
    # a área segura manda
    bx = min(max(bx, sx), sx + sw - bw)
    by = min(max(by, sy), sy + sh - bh)
    return bx, by


def fit_to_spec(img: Image.Image | np.ndarray, spec: AssetSpec, palette: PaletteSet | None = None, *,
                palette_group: str | None = None, upscale: bool = False, alpha_threshold: int = 128,
                key_bg: bool = True) -> Image.Image:
    """Devolve um RGBA com EXATAMENTE spec.canvas, pronto para os validadores. Ver docstring do módulo."""
    arr = to_rgba(img)
    if key_bg and not spec.opaque and (arr[..., 3] == 255).all():
        arr = key_background(arr)
    mask = arr[..., 3] >= alpha_threshold
    b = bbox(mask)
    if b is None:
        raise ValueError("a imagem não tem pixels opacos")
    W, H = spec.canvas

    if spec.opaque:  # tile sólido: preenche o canvas todo (recorte central na proporção do canvas)
        x0, y0, x1, y1 = b
        cw, ch = x1 - x0, y1 - y0
        target = W / H
        if cw / ch > target:
            nw = max(1, _round(ch * target))
            x0 += (cw - nw) // 2
            x1 = x0 + nw
        else:
            nh = max(1, _round(cw / target))
            y0 += (ch - nh) // 2
            y1 = y0 + nh
        crop = arr[y0:y1, x0:x1].copy()
        crop[..., 3] = 255
        out = _resize(crop, (W, H))
        out[..., 3] = 255
        out = _quantize(out, spec, palette, palette_group)
        out[..., 3] = 255
        return to_image(out)

    crop = arr[b[1]:b[3], b[0]:b[2]].copy()
    crop[..., 3] = np.where(crop[..., 3] >= alpha_threshold, 255, 0)
    ch, cw = crop.shape[:2]
    m = 1 if spec.outline == "required" else 0
    sx, sy, sw, sh = spec.safe_rect()
    avail_w, avail_h = sw - 2 * m, sh - 2 * m
    if spec.coverage:
        avail_w = min(avail_w, spec.coverage[1] - 2 * m)
        avail_h = min(avail_h, spec.coverage[1] - 2 * m)
    if avail_w < 1 or avail_h < 1:
        raise ValueError(f"área segura pequena demais para a spec {spec.name}")
    scale = min(avail_w / cw, avail_h / ch)
    if scale >= 1:
        need_cov = spec.coverage and max(cw, ch) + 2 * m < spec.coverage[0]
        scale = float(math.floor(scale)) if (upscale or need_cov) else 1.0
    nw = min(avail_w, max(1, _round(cw * scale)))
    nh = min(avail_h, max(1, _round(ch * scale)))
    small = _resize(crop, (nw, nh))
    small[..., 3] = np.where(small[..., 3] >= alpha_threshold, 255, 0)
    small[small[..., 3] == 0, :3] = 0
    if not (small[..., 3] > 0).any():  # tudo sumiu na redução: usa vizinho mais próximo
        small = to_rgba(to_image(crop).resize((nw, nh), Image.Resampling.NEAREST))
        small[..., 3] = np.where(small[..., 3] >= alpha_threshold, 255, 0)
    small = _quantize(small, spec, palette, palette_group)
    if m:
        small = _peel_dark_border(small, spec.outline_color)
    small = remove_islands(small, spec.min_island, spec.island_connectivity, set(spec.sparkle_colors))
    b2 = bbox(small[..., 3] > 0)
    if b2 is None:
        raise ValueError("nada sobrou depois de reduzir/limpar a imagem (tente outra entrada ou spec maior)")
    small = small[b2[1]:b2[3], b2[0]:b2[2]]
    ch, cw = small.shape[:2]
    bw, bh = cw + 2 * m, ch + 2 * m
    bx, by = _box_origin(spec, bw, bh, cw, m)
    out = np.zeros((H, W, 4), np.uint8)
    top = H - (by + bh) + m                      # linha de imagem do topo do conteúdo
    left = bx + m
    out[top:top + ch, left:left + cw] = small
    if m:
        ring = outline_ring(out[..., 3] > 0)
        out[ring, :3] = hex_to_rgb(spec.outline_color)
        out[ring, 3] = 255
    return to_image(out)
