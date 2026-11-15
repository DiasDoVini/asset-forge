"""Efeitos de camada não destrutivos do editor de sprite (mesma matemática de web/src/modalities/2d/sprite/color.ts).

O editor guarda os efeitos no texto de "user data" de cada camada do .aseprite (prefixo "asset-forge:"); o leitor
(`forge_2d.aseprite`) aplica-os na composição, para a versão gerada no servidor ser igual ao que se vê no editor.
Arredondamento como o `Math.round` do JavaScript (floor(x + 0.5)).
"""
from __future__ import annotations

from typing import Any

import numpy as np

AF_PREFIX = "asset-forge:"


def _r(x: np.ndarray) -> np.ndarray:
    return np.floor(x + 0.5)


def _clamp(x: np.ndarray) -> np.ndarray:
    return np.clip(_r(x), 0, 255)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return _r(0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])


def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    c = rgb / 255.0
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    mx = c.max(-1)
    mn = c.min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 0
    rm = nz & (mx == r)
    gm = nz & (mx == g) & ~rm
    bm = nz & ~rm & ~gm
    h[rm] = np.mod((g[rm] - b[rm]) / d[rm], 6)
    h[gm] = (b[gm] - r[gm]) / d[gm] + 2
    h[bm] = (r[bm] - g[bm]) / d[bm] + 4
    h = h * 60
    h[h < 0] += 360
    s = np.where(mx > 0, d / np.where(mx > 0, mx, 1), 0)
    return h, s, mx


def _hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    h = np.mod(np.mod(h, 360) + 360, 360)
    c = v * s
    x = c * (1 - np.abs(np.mod(h / 60, 2) - 1))
    m = v - c
    z = np.zeros_like(h)
    conds = [h < 60, h < 120, h < 180, h < 240, h < 300]
    r = np.select(conds, [c, x, z, z, x], c)
    g = np.select(conds, [x, c, c, x, z], z)
    b = np.select(conds, [z, z, x, c, c], x)
    return np.stack([_r((r + m) * 255), _r((g + m) * 255), _r((b + m) * 255)], -1)


def _lin(c: np.ndarray) -> np.ndarray:
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _oklab(rgb: np.ndarray) -> np.ndarray:
    R, G, B = _lin(rgb[..., 0]), _lin(rgb[..., 1]), _lin(rgb[..., 2])
    l = np.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B)
    m = np.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B)
    s = np.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B)
    return np.stack([0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
                     1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
                     0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s], -1)


_BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float64)


def _quantize(img: np.ndarray, palette: list[list[int]], dither: bool) -> np.ndarray:
    out = img.copy()
    pal = np.array([c for c in palette], np.float64).reshape(-1, 4)
    opaque = np.nonzero(pal[:, 3] > 0)[0]
    if not len(opaque):
        return out
    labs = _oklab(pal[:, :3])
    a = img[..., 3]
    out[a < 128] = 0
    sel = a >= 128
    if not sel.any():
        return out
    px = img[sel][:, :3].astype(np.float64)
    lab = _oklab(px)
    d = ((lab[:, None, :] - labs[None, opaque, :]) ** 2).sum(-1)
    order = np.argsort(d, axis=1)
    k = opaque[order[:, 0]]
    if dither and len(opaque) > 1:
        k2 = opaque[order[:, 1]]
        d1 = np.sqrt(np.take_along_axis(d, order[:, :1], 1)[:, 0])
        d2 = np.sqrt(np.take_along_axis(d, order[:, 1:2], 1)[:, 0])
        den = d1 + d2
        t = np.where(den > 0, d1 / np.where(den > 0, den, 1), 0)
        ys, xs = np.nonzero(sel)
        thr = (_BAYER4[ys % 4, xs % 4] + 0.5) / 16
        k = np.where(t > thr, k2, k)
    out[sel] = np.concatenate([pal[k, :3], np.full((len(k), 1), 255)], 1).astype(np.uint8)
    return out


def apply_adjust(img: np.ndarray, a: dict[str, Any]) -> np.ndarray:
    """img: uint8 H×W×4. Devolve uma cópia ajustada (só pixels com alfa > 0, exceto contorno/sombra)."""
    kind = a.get("kind")
    out = img.copy()
    alpha = img[..., 3]
    if kind == "fit_palette":
        return _quantize(img, a.get("palette") or [], bool(a.get("dither")))
    if kind in ("outline", "shadow"):
        op = alpha > 0
        h, w = op.shape

        def shifted(dx: int, dy: int) -> np.ndarray:
            s = np.zeros_like(op)
            ys = slice(max(0, dy), h + min(0, dy))
            yd = slice(max(0, -dy), h + min(0, -dy))
            xs = slice(max(0, dx), w + min(0, dx))
            xd = slice(max(0, -dx), w + min(0, -dx))
            s[ys, xs] = op[yd, xd]
            return s   # s[y, x] = op[y - dy, x - dx]

        color = np.array(a.get("color", [0, 0, 0, 255]), np.uint8)
        if kind == "shadow":
            m = ~op & shifted(int(a.get("dx", 1)), int(a.get("dy", 1)))
        else:
            nb = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            if a.get("diagonal"):
                nb += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
            # vizinho (x+dx, y+dy) opaco  ==  shifted(-dx, -dy)
            any_op = np.zeros_like(op)
            any_tr = np.zeros_like(op)
            for dx, dy in nb:
                s = shifted(-dx, -dy)
                inside = np.zeros_like(op)
                inside[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)] = True  # vizinho dentro da imagem
                any_op |= s
                any_tr |= ~s | ~inside
            m = (~op & any_op) if a.get("place", "outside") == "outside" else (op & any_tr)
        out[m] = color
        return out
    vis = alpha > 0
    rgb = img[..., :3].astype(np.float64)
    if kind == "hsv":
        h, s, v = _rgb_to_hsv(rgb)
        sat, val = float(a.get("sat", 0)), float(a.get("val", 0))
        s2 = np.clip(s + (1 - s) * (sat / 100) if sat >= 0 else s * (1 + sat / 100), 0, 1)
        v2 = np.clip(v + (1 - v) * (val / 100) if val >= 0 else v * (1 + val / 100), 0, 1)
        new = _hsv_to_rgb(h + float(a.get("hue", 0)), s2, v2)
    elif kind == "brightness":
        c, b = float(a.get("contrast", 0)), float(a.get("brightness", 0))
        k = (259 * (c * 2.55 + 255)) / (255 * (259 - c * 2.55))
        new = _clamp(k * (rgb - 128) + 128 + b * 2.55)
    elif kind == "invert":
        new = 255 - rgb
    elif kind == "desaturate":
        new = np.repeat(_luma(rgb)[..., None], 3, -1)
    elif kind == "posterize":
        lv = max(2, int(a.get("levels", 4)))
        new = _clamp(_r(rgb / 255 * (lv - 1)) * (255 / (lv - 1)))
    elif kind == "replace":
        frm = np.array(a.get("from", [0, 0, 0, 255]), np.float64)
        to = np.array(a.get("to", [0, 0, 0, 255]), np.uint8)
        d = np.abs(img.astype(np.float64) - frm).max(-1)
        m = vis & (d <= float(a.get("tolerance", 0)))
        out[m] = to
        return out
    elif kind == "gradient_map":
        cols = np.array(a.get("colors") or [], np.float64).reshape(-1, 4)
        if not len(cols):
            return out
        idx = np.minimum(len(cols) - 1, _r(_luma(rgb) / 255 * (len(cols) - 1)).astype(int))
        new = cols[idx, :3]
    else:
        return out
    out[..., :3] = np.where(vis[..., None], np.clip(new, 0, 255), img[..., :3]).astype(np.uint8)
    return out


def apply_effects(img: np.ndarray, effects: list[dict[str, Any]]) -> np.ndarray:
    for e in effects or []:
        if not e.get("enabled", True):
            continue
        n = max(1, min(8, int(e.get("thickness") or 1))) if e.get("adjust", {}).get("kind") == "outline" else 1
        for _ in range(n):
            img = apply_adjust(img, e.get("adjust", {}))
    return img
