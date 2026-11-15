"""Cores: hex <-> RGB, OKLab (numpy), quantização exata sem pontilhado, rampas e escurecimento.

A quantização mapeia cada pixel opaco para a cor permitida mais próxima em OKLab (distância
perceptual). Não há dithering: o resultado é determinístico e cada pixel recebe uma cor da lista.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
from PIL import Image

from .spec import norm_hex


def hex_to_rgb(c: str) -> tuple[int, int, int]:
    h = norm_hex(c)
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def rgb_to_hex(rgb: Iterable[int]) -> str:
    r, g, b = (int(v) for v in list(rgb)[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def js_round(x: np.ndarray | float) -> np.ndarray:
    """Math.round do JavaScript (meio arredonda para cima), usado pelo art/tools/gen-parts.js."""
    return np.floor(np.asarray(x, dtype=np.float64) + 0.5)


def darken(c: str, factor: float = 0.78) -> str:
    """Multiplica cada canal (arredondamento igual ao gen-parts.js: Math.round(c * f))."""
    rgb = np.array(hex_to_rgb(c), dtype=np.float64)
    return rgb_to_hex(np.clip(js_round(rgb * factor), 0, 255).astype(int))


# ---------------------------------------------------------------- OKLab

def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])


def rgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    """rgb (..., 3) em 0..255 -> OKLab (..., 3)."""
    lin = _srgb_to_linear(np.asarray(rgb, dtype=np.float64) / 255.0)
    lms = lin @ _M1.T
    return np.cbrt(lms) @ _M2.T


def oklab_to_rgb(lab: np.ndarray) -> np.ndarray:
    lms_ = np.asarray(lab, dtype=np.float64) @ np.linalg.inv(_M2).T
    lin = (lms_ ** 3) @ np.linalg.inv(_M1).T
    return np.clip(js_round(_linear_to_srgb(lin) * 255.0), 0, 255).astype(np.uint8)


def color_distance(a: str, b: str) -> float:
    la, lb = rgb_to_oklab(np.array(hex_to_rgb(a))), rgb_to_oklab(np.array(hex_to_rgb(b)))
    return float(np.linalg.norm(la - lb))


def sorted_colors(colors: Iterable[str]) -> list[str]:
    """Ordem estável (hex normalizado, ordenado) para a quantização ser determinística."""
    return sorted({norm_hex(c) for c in colors})


def nearest(color: str, colors: Iterable[str]) -> str:
    cs = sorted_colors(colors)
    if not cs:
        raise ValueError("paleta vazia")
    lab = rgb_to_oklab(np.array([hex_to_rgb(c) for c in cs]))
    d = np.linalg.norm(lab - rgb_to_oklab(np.array(hex_to_rgb(color))), axis=1)
    return cs[int(np.argmin(d))]


def quantize_array(rgba: np.ndarray, colors: Iterable[str], alpha_threshold: int = 128) -> np.ndarray:
    """Quantiza os pixels opacos (alfa ≥ limiar) para a cor mais próxima (OKLab). Alfa vira binário."""
    cs = sorted_colors(colors)
    if not cs:
        raise ValueError("paleta vazia")
    arr = np.asarray(rgba, dtype=np.uint8).copy()
    opaque = arr[..., 3] >= alpha_threshold
    arr[..., 3] = np.where(opaque, 255, 0)
    arr[~opaque, :3] = 0
    if not opaque.any():
        return arr
    pal_rgb = np.array([hex_to_rgb(c) for c in cs], dtype=np.uint8)
    pal_lab = rgb_to_oklab(pal_rgb)
    # quantiza por cor única (rápido e idêntico pixel a pixel)
    src = arr[opaque, :3]
    uniq, inv = np.unique(src, axis=0, return_inverse=True)
    lab = rgb_to_oklab(uniq)
    d = ((lab[:, None, :] - pal_lab[None, :, :]) ** 2).sum(-1)
    idx = np.argmin(d, axis=1)
    arr[opaque, :3] = pal_rgb[idx][inv.reshape(-1)]
    return arr


def quantize(img: Image.Image, colors: Iterable[str], alpha_threshold: int = 128) -> Image.Image:
    return Image.fromarray(quantize_array(np.asarray(img.convert("RGBA")), colors, alpha_threshold), "RGBA")


def reduce_colors(rgba: np.ndarray, max_colors: int) -> np.ndarray:
    """Sem paleta do projeto: limita a N cores (median cut do Pillow, sem pontilhado). Determinístico."""
    arr = np.asarray(rgba, dtype=np.uint8).copy()
    opaque = arr[..., 3] == 255
    if not opaque.any():
        return arr
    uniq = np.unique(arr[opaque, :3], axis=0)
    if len(uniq) <= max_colors:
        return arr
    rgb = Image.fromarray(np.ascontiguousarray(arr[..., :3]), "RGB")
    q = rgb.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = np.array(q.getpalette()[: 3 * max_colors], dtype=np.uint8).reshape(-1, 3)
    # recalcula por vizinho mais próximo em OKLab com as cores escolhidas
    colors = [rgb_to_hex(c) for c in pal]
    out = quantize_array(arr, colors)
    return out


# ---------------------------------------------------------------- rampas

def ramp(color: str, steps: int = 3, spread: float = 0.12, hue_shift: float = 0.0) -> list[str]:
    """Rampa de tons em OKLab ao redor da cor (escuro -> claro), com deslocamento opcional de matiz
    nas pontas (sombra mais fria/violeta, luz mais quente). A cor base fica no meio quando `steps` é ímpar."""
    if steps < 1:
        return []
    lab = rgb_to_oklab(np.array(hex_to_rgb(color), dtype=np.float64))
    out = []
    mid = (steps - 1) / 2
    for i in range(steps):
        t = (i - mid) / (mid if mid else 1)
        L = lab[0] + t * spread
        a = lab[1] - t * hue_shift * 0.5
        b = lab[2] + t * hue_shift
        out.append(rgb_to_hex(oklab_to_rgb(np.array([L, a, b]))))
    return out


def colors_of(img: Image.Image | np.ndarray) -> dict[str, int]:
    """Cor -> número de pixels opacos (alfa > 0), ordenado por uso (desc) e hex."""
    arr = np.asarray(img.convert("RGBA") if isinstance(img, Image.Image) else img)
    op = arr[..., 3] > 0
    if not op.any():
        return {}
    uniq, counts = np.unique(arr[op, :3], axis=0, return_counts=True)
    items = sorted(((rgb_to_hex(c), int(n)) for c, n in zip(uniq, counts)), key=lambda t: (-t[1], t[0]))
    return dict(items)
