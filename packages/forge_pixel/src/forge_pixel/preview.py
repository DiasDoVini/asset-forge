"""Prévias ampliadas (só para conferir; o PNG final nunca é ampliado).

- zoom_preview: ampliação pixel perfeita sobre xadrez, grade de 1 px (linha mais forte a cada 4 px),
  área segura em verde e pivô como cruz carmim com centro dourado — o mesmo código visual dos
  modelos em art/guia-de-partes/. O pivô é medido a partir do canto INFERIOR esquerdo.
- contact_sheet: folha em grade com rótulos.
- side_by_side: folha lado a lado igual ao art/tools/preview.js (fundo #1a1830, margem de 4 px).
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .palette import hex_to_rgb
from .raster import to_rgba
from .spec import AssetSpec

CHECKER = ((0x2a, 0x28, 0x3c), (0x34, 0x32, 0x48))
GRID = (0x10, 0x0f, 0x18)
GRID_STRONG = (0x5a, 0x58, 0x78)
SAFE = (0x3c, 0xc8, 0x6e)
PIVOT = hex_to_rgb("#a8324a")
PIVOT_CENTER = hex_to_rgb("#e8b45a")
BG = hex_to_rgb("#1a1830")


def _composite(arr: np.ndarray, bg: np.ndarray) -> np.ndarray:
    a = arr[..., 3:4].astype(np.float64) / 255.0
    return np.floor(arr[..., :3] * a + bg * (1 - a) + 0.5).astype(np.uint8)


def zoom_preview(img: Image.Image | np.ndarray, spec: AssetSpec | None = None, scale: int = 12, grid: bool = True,
                 pivot: bool = True, safe: bool = True) -> Image.Image:
    arr = to_rgba(img)
    h, w = arr.shape[:2]
    s = max(1, int(scale))
    big = np.repeat(np.repeat(arr, s, axis=0), s, axis=1)
    # xadrez: cada pixel de arte vira 2x2 casas
    half = max(1, s // 2)
    yy, xx = np.mgrid[0:h * s, 0:w * s]
    chk = ((yy // half + xx // half) % 2).astype(bool)
    bg = np.where(chk[..., None], np.array(CHECKER[1]), np.array(CHECKER[0])).astype(np.float64)
    out = _composite(big, bg)
    if spec is not None and safe and spec.safe_area is not None and (w, h) == tuple(spec.canvas):
        x0, r0, x1, r1 = spec.safe_box_rows()
        region = out[r0 * s:r1 * s, x0 * s:x1 * s].astype(np.float64)
        out[r0 * s:r1 * s, x0 * s:x1 * s] = np.floor(region * 0.82 + np.array(SAFE) * 0.18 + 0.5).astype(np.uint8)
    im = Image.fromarray(out, "RGB")
    d = ImageDraw.Draw(im)
    if grid and s >= 4:
        for x in range(0, w + 1):
            d.line([(min(x * s, w * s - 1), 0), (min(x * s, w * s - 1), h * s - 1)], fill=GRID_STRONG if x % 4 == 0 else GRID)
        for y in range(0, h + 1):
            # linhas fortes a cada 4 px contadas de BAIXO (como o pivô)
            yb = h - y
            d.line([(0, min(y * s, h * s - 1)), (w * s - 1, min(y * s, h * s - 1))], fill=GRID_STRONG if yb % 4 == 0 else GRID)
    if spec is not None and safe and spec.safe_area is not None and (w, h) == tuple(spec.canvas):
        x0, r0, x1, r1 = spec.safe_box_rows()
        d.rectangle([x0 * s, r0 * s, x1 * s - 1, r1 * s - 1], outline=SAFE, width=max(1, s // 6))
    if spec is not None and pivot:
        px, py = spec.pivot_xy()
        cx, cy = px * s, (h - py) * s
        arm = max(3, s)
        t = max(1, s // 6)
        d.line([(cx - arm, cy), (cx + arm, cy)], fill=PIVOT, width=t + 1)
        d.line([(cx, cy - arm), (cx, cy + arm)], fill=PIVOT, width=t + 1)
        r = max(1, s // 4)
        d.rectangle([cx - r, cy - r, cx + r, cy + r], fill=PIVOT_CENTER)
    return im


def side_by_side(candidate: Image.Image | np.ndarray, references: Sequence[Image.Image | np.ndarray] = (),
                 scale: int = 12, bg: str = "#1a1830", pad: int = 4) -> Image.Image:
    """Igual ao art/tools/preview.js: células lado a lado, ampliação inteira, alfa composto no fundo."""
    imgs = [to_rgba(candidate), *[to_rgba(r) for r in references]]
    bgc = np.array(hex_to_rgb(bg), dtype=np.float64)
    W = pad + sum(i.shape[1] * scale + pad for i in imgs)
    H = max(i.shape[0] for i in imgs) * scale + pad * 2
    out = np.empty((H, W, 3), np.uint8)
    out[:] = bgc.astype(np.uint8)
    ox = pad
    for im in imgs:
        big = np.repeat(np.repeat(im, scale, axis=0), scale, axis=1)
        h, w = big.shape[:2]
        out[pad:pad + h, ox:ox + w] = _composite(big, np.broadcast_to(bgc, (h, w, 3)))
        ox += w + pad
    return Image.fromarray(out, "RGB")


def contact_sheet(items: Sequence[tuple[Image.Image | np.ndarray, str] | Image.Image], scale: int = 8, columns: int = 6,
                  bg: str = "#1a1830", pad: int = 8, label_h: int = 14) -> Image.Image:
    """Grade de sprites ampliados com rótulo embaixo de cada um."""
    norm: list[tuple[np.ndarray, str]] = []
    for it in items:
        if isinstance(it, tuple):
            norm.append((to_rgba(it[0]), str(it[1])))
        else:
            norm.append((to_rgba(it), ""))
    if not norm:
        return Image.new("RGB", (1, 1), hex_to_rgb(bg))
    cols = max(1, min(columns, len(norm)))
    cell_w = max(a.shape[1] for a, _ in norm) * scale + pad
    cell_h = max(a.shape[0] for a, _ in norm) * scale + pad + label_h
    rows = (len(norm) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w + pad, rows * cell_h + pad), hex_to_rgb(bg))
    d = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    bgc = np.array(hex_to_rgb(bg), dtype=np.float64)
    for n, (a, label) in enumerate(norm):
        cx, cy = pad + (n % cols) * cell_w, pad + (n // cols) * cell_h
        big = np.repeat(np.repeat(a, scale, axis=0), scale, axis=1)
        tile = Image.fromarray(_composite(big, np.broadcast_to(bgc, big.shape[:2] + (3,))), "RGB")
        sheet.paste(tile, (cx, cy))
        if label:
            d.text((cx, cy + big.shape[0] + 2), label[:40], fill=(0xcd, 0xc9, 0xdf), font=font)
    return sheet
