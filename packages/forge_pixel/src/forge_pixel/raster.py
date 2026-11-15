"""Operações de grade usadas por todo o pacote (sem dependência de scipy)."""
from __future__ import annotations

from collections import deque

import numpy as np
from PIL import Image


def to_rgba(img: Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(img, Image.Image):
        return np.asarray(img.convert("RGBA"), dtype=np.uint8).copy()
    a = np.asarray(img, dtype=np.uint8)
    if a.ndim == 3 and a.shape[2] == 3:
        a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], axis=2)
    return a.copy()


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(arr, dtype=np.uint8), "RGBA")


def neighbors4(mask: np.ndarray) -> np.ndarray:
    """True onde algum vizinho 4-conexo (dentro do canvas) é True."""
    m = np.asarray(mask, bool)
    out = np.zeros_like(m)
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def outline_ring(mask: np.ndarray) -> np.ndarray:
    """Pixels transparentes 4-adjacentes a preenchidos, dentro do canvas (o `outline()` do gen-parts.js)."""
    m = np.asarray(mask, bool)
    return neighbors4(m) & ~m


def border_pixels(mask: np.ndarray, edges: bool = False) -> np.ndarray:
    """Pixels opacos com algum vizinho 4-conexo transparente. edges=True: a borda do canvas conta como transparente."""
    m = np.asarray(mask, bool)
    if edges:
        pad = np.pad(m, 1, constant_values=False)
        inner = pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:]
    else:
        # fora do canvas = mesmo valor do pixel (não conta como transparente)
        up = np.vstack([m[:1], m[:-1]])
        down = np.vstack([m[1:], m[-1:]])
        left = np.hstack([m[:, :1], m[:, :-1]])
        right = np.hstack([m[:, 1:], m[:, -1:]])
        inner = up & down & left & right
    return m & ~inner


def components(mask: np.ndarray, connectivity: int = 8) -> list[np.ndarray]:
    """Componentes conexos de `mask`, em ordem de varredura (linha, coluna). Cada um = array (N, 2) de (y, x)."""
    m = np.asarray(mask, bool)
    h, w = m.shape
    seen = np.zeros_like(m)
    steps = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    if connectivity == 8:
        steps += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    out: list[np.ndarray] = []
    for y0, x0 in zip(*np.nonzero(m)):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        q = deque([(int(y0), int(x0))])
        pts = []
        while q:
            y, x = q.popleft()
            pts.append((y, x))
            for dy, dx in steps:
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and m[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    q.append((yy, xx))
        out.append(np.array(pts, dtype=int))
    return out


def bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) exclusivos em coordenadas de imagem, ou None se vazio."""
    ys, xs = np.nonzero(np.asarray(mask, bool))
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def hex_mask(arr: np.ndarray, color: str) -> np.ndarray:
    from .palette import hex_to_rgb

    r, g, b = hex_to_rgb(color)
    return (arr[..., 3] > 0) & (arr[..., 0] == r) & (arr[..., 1] == g) & (arr[..., 2] == b)


def save_png(img: Image.Image, path) -> None:
    """PNG sem metadados (byte a byte determinístico)."""
    img.save(path, format="PNG", optimize=False, compress_level=9)
