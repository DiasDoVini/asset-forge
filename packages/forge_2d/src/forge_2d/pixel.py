"""Pixelizer (CPU, numpy/Pillow puro).

Dois modos:
- "downscale": qualquer imagem → pixel art. Reduz para N pixels de arte (média com alpha
  pré-multiplicado), binariza alpha, quantiza (k-means / paleta do projeto), contorno opcional.
- "grid": imagem que já "parece" pixel art (saída de IA, com blocos borrados) → sprite
  pixel-perfect. Detecta a grade, pega uma cor por célula, remove fundo e quantiza.
  Portado do Pixel-LLM-Gen (pixelgen/post/pixelize.py).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- paletas


def hex_to_rgb(colors: list[str]) -> np.ndarray:
    out = []
    for c in colors:
        c = c.strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        out.append([int(c[i:i + 2], 16) for i in (0, 2, 4)])
    return np.asarray(out, np.uint8).reshape(-1, 3)


def rgb_to_hex(pal: np.ndarray) -> list[str]:
    return ["#%02x%02x%02x" % tuple(int(v) for v in c) for c in pal]


def extract_palette(images: list[Image.Image], colors: int = 16) -> np.ndarray:
    """Paleta de N cores a partir dos pixels opacos de imagens de referência."""
    px = []
    for im in images:
        a = np.asarray(im.convert("RGBA"))
        px.append(a[a[..., 3] > 127][:, :3])
    allpx = np.concatenate(px) if px else np.zeros((0, 3), np.uint8)
    if len(allpx) == 0:
        return np.zeros((0, 3), np.uint8)
    if len(allpx) > 200_000:
        allpx = allpx[np.random.default_rng(0).choice(len(allpx), 200_000, replace=False)]
    q = Image.fromarray(allpx.reshape(1, -1, 3)).quantize(colors=colors, method=Image.Quantize.MEDIANCUT, kmeans=4)
    pal = np.asarray(q.getpalette()[: colors * 3], np.uint8).reshape(-1, 3)
    return pal[np.unique(np.asarray(q)).astype(int)]


def _dist(cells: np.ndarray, color: np.ndarray) -> np.ndarray:
    return np.sqrt(((cells.astype(np.float64) - color) ** 2).sum(axis=-1))


def quantize(cells: np.ndarray, opaque: np.ndarray, colors: int = 16, palette: np.ndarray | None = None,
             dither: bool = False) -> np.ndarray:
    """Reduz as cores das células opacas. Com `palette` (K,3) mapeia para a cor mais próxima."""
    out = cells.copy()
    if not opaque.any():
        return out
    if palette is not None and len(palette):
        pal = np.asarray(palette, np.uint8)
        if dither:
            pimg = Image.new("P", (1, 1))
            flat = pal.reshape(-1).tolist()
            pimg.putpalette(flat + flat[:3] * (256 - len(pal)))
            q = Image.fromarray(cells).quantize(palette=pimg, dither=Image.Dither.FLOYDSTEINBERG)
            mapped = np.asarray(q.convert("RGB"))
            out[opaque] = mapped[opaque]
            return out
        px = cells[opaque].astype(np.float64)
        idx = np.argmin(((px[:, None, :] - pal[None].astype(np.float64)) ** 2).sum(-1), axis=1)
        out[opaque] = pal[idx]
        return out
    px = cells[opaque]
    strip = Image.fromarray(px.reshape(1, -1, 3))
    q = strip.quantize(colors=max(2, min(colors, 256)), method=Image.Quantize.MEDIANCUT, kmeans=4,
                       dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    out[opaque] = np.asarray(q.convert("RGB")).reshape(-1, 3)
    return out


# ---------------------------------------------------------------- máscaras


def background_mask(cells: np.ndarray, tight: float = 48.0, loose: float = 110.0) -> np.ndarray:
    """True = fundo. Cor de fundo = mediana da borda. `tight` vale em qualquer lugar (buracos);
    `loose` só se conectado à borda (halo/degradê)."""
    border = np.concatenate([cells[0], cells[-1], cells[:, 0], cells[:, -1]])
    bg = np.median(border, axis=0)
    d = _dist(cells, bg)
    mask = d < tight
    candidate = d < loose
    rows, cols = d.shape
    seen = np.zeros_like(mask)
    q = deque((j, i) for j in range(rows) for i in range(cols)
              if (j in (0, rows - 1) or i in (0, cols - 1)) and candidate[j, i])
    for p in q:
        seen[p] = True
    while q:
        j, i = q.popleft()
        mask[j, i] = True
        for nj, ni in ((j + 1, i), (j - 1, i), (j, i + 1), (j, i - 1)):
            if 0 <= nj < rows and 0 <= ni < cols and candidate[nj, ni] and not seen[nj, ni]:
                seen[nj, ni] = True
                q.append((nj, ni))
    return mask


def _components(opaque: np.ndarray) -> list[list[tuple[int, int]]]:
    rows, cols = opaque.shape
    seen = np.zeros_like(opaque)
    comps = []
    for j in range(rows):
        for i in range(cols):
            if not opaque[j, i] or seen[j, i]:
                continue
            comp, q = [], deque([(j, i)])
            seen[j, i] = True
            while q:
                y, x = q.popleft()
                comp.append((y, x))
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < rows and 0 <= nx < cols and opaque[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            comps.append(comp)
    return comps


def clean_mask(opaque: np.ndarray, min_size: int = 4, min_frac: float = 0.05) -> np.ndarray:
    """Remove ilhas opacas pequenas (ruído)."""
    comps = _components(opaque)
    if not comps:
        return opaque
    largest = max(len(c) for c in comps)
    keep = np.zeros_like(opaque)
    for c in comps:
        if len(c) >= min_size and len(c) >= min_frac * largest:
            for p in c:
                keep[p] = True
    return keep


def outline(cells: np.ndarray, opaque: np.ndarray, color: np.ndarray, diagonal: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Contorno externo de 1 px (expande o canvas em 1 px de cada lado)."""
    h, w = opaque.shape
    c2 = np.zeros((h + 2, w + 2, 3), np.uint8)
    o2 = np.zeros((h + 2, w + 2), bool)
    c2[1:-1, 1:-1], o2[1:-1, 1:-1] = cells, opaque
    grown = o2.copy()
    shifts = [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (1, -1), (-1, 1), (-1, -1)] if diagonal else [])
    for dy, dx in shifts:
        grown |= np.roll(np.roll(o2, dy, 0), dx, 1)
    ring = grown & ~o2
    c2[ring] = color
    return c2, grown


# ---------------------------------------------------------------- grade (modo "grid")


@dataclass(frozen=True)
class Grid:
    cell: float
    off_x: float
    off_y: float

    def shape(self, w: int, h: int) -> tuple[int, int]:
        return int((h - self.off_y) // self.cell), int((w - self.off_x) // self.cell)


def _edge_profile(rgb: np.ndarray, axis: int) -> np.ndarray:
    d = np.abs(np.diff(rgb.astype(np.int32), axis=axis)).sum(axis=2)
    prof = d.sum(axis=0 if axis == 1 else 1).astype(np.float64)
    return np.concatenate([[0.0], prof])


def _best_period(prof: np.ndarray, sizes: range, keep: float = 0.85) -> tuple[float, float, float]:
    """(tamanho, offset, fração explicada).

    Bordas = picos acima do ruído (mediana + 4·MAD), com peso saturado para que contraste
    alto não domine. Para cada período s, a melhor fase explica uma fração das bordas
    (tolerância de 1 px para fronteiras borradas). Divisores do período real explicam tudo
    mas são menores; múltiplos explicam só parte. Escolhe o MAIOR s que explica >= `keep`
    do máximo observado.
    """
    med = np.median(prof)
    mad = np.median(np.abs(prof - med)) * 1.4826
    p = np.maximum(prof - med - 4.0 * mad, 0.0)
    idx = np.nonzero(p)[0]
    if len(idx) < 2:
        return float(sizes[0]), 0.0, 0.0
    cap = 0.3 * p[idx].max()
    w = np.minimum(p[idx] / cap, 1.0)
    total = w.sum()
    per_size: list[tuple[int, int, float]] = []
    for s in sizes:
        tol = 1 if s >= 6 else 0
        r = idx % s
        best_o, best_f = 0, -1.0
        for o in range(s):
            d = np.abs(r - o)
            d = np.minimum(d, s - d)
            f = w[d <= tol].sum() / total
            if f > best_f:
                best_o, best_f = o, f
        per_size.append((s, best_o, best_f))
    top = max(f for _, _, f in per_size)
    s, o, f = max((c for c in per_size if c[2] >= keep * top), key=lambda c: c[0])
    return float(s), float(o), f


def detect_grid(img: Image.Image, min_cell: int = 2, max_cell: int = 48) -> Grid:
    rgb = np.asarray(img.convert("RGB"))
    sizes = range(min_cell, min(max_cell, min(rgb.shape[:2]) // 4) + 1)
    sx, ox, _ = _best_period(_edge_profile(rgb, axis=1), sizes)
    sy, oy, _ = _best_period(_edge_profile(rgb, axis=0), sizes)
    cell = min(sx, sy)
    return Grid(cell, ox % cell, oy % cell)


def sample_cells(img: Image.Image, grid: Grid, inner: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """(cores, alpha) por célula: mediana do miolo de cada célula."""
    rgba = np.asarray(img.convert("RGBA"))
    h, w = rgba.shape[:2]
    rows, cols = grid.shape(w, h)
    r = max(1, int(grid.cell * inner / 2))
    out = np.zeros((rows, cols, 4), np.uint8)
    for j in range(rows):
        cy = int(grid.off_y + (j + 0.5) * grid.cell)
        for i in range(cols):
            cx = int(grid.off_x + (i + 0.5) * grid.cell)
            patch = rgba[max(0, cy - r):cy + r, max(0, cx - r):cx + r].reshape(-1, 4)
            out[j, i] = np.median(patch, axis=0)
    return out[..., :3], out[..., 3]


# ---------------------------------------------------------------- API


@dataclass
class PixelResult:
    sprite: Image.Image          # resolução de arte (1 px = 1 pixel de jogo)
    scaled: Image.Image          # ampliado com nearest (para visualização/uso direto)
    colors: int
    grid: Grid | None = None

    def palette(self) -> list[str]:
        a = np.asarray(self.sprite.convert("RGBA"))
        return rgb_to_hex(np.unique(a[a[..., 3] > 0][:, :3], axis=0))


def _downscale(img: Image.Image, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Média de área com alpha pré-multiplicado (borda não escurece)."""
    rgba = np.asarray(img.convert("RGBA")).astype(np.float32)
    a = rgba[..., 3:4] / 255.0
    pre = Image.fromarray((rgba[..., :3] * a).astype(np.uint8))
    rgb = np.asarray(pre.resize(size, Image.Resampling.BOX), np.float32)
    al = np.asarray(Image.fromarray(rgba[..., 3].astype(np.uint8)).resize(size, Image.Resampling.BOX), np.float32)
    rgb = rgb / np.maximum(al[..., None] / 255.0, 1e-3)
    return np.clip(rgb, 0, 255).astype(np.uint8), al.astype(np.uint8)


def _target_size(w: int, h: int, size: int | None, cell: float | None) -> tuple[int, int]:
    if cell:
        return max(1, round(w / cell)), max(1, round(h / cell))
    size = size or 64
    k = size / max(w, h)
    return max(1, round(w * k)), max(1, round(h * k))


def _crop(cells: np.ndarray, opaque: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.nonzero(opaque)
    if len(ys) == 0:
        return cells, opaque
    sl = np.s_[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return cells[sl], opaque[sl]


def _has_alpha(img: Image.Image) -> bool:
    if img.mode not in ("RGBA", "LA", "PA") and "transparency" not in img.info:
        return False
    a = np.asarray(img.convert("RGBA"))[..., 3]
    return bool((a < 250).mean() > 0.01)


def pixelize(img: Image.Image, *, mode: str = "downscale", size: int | None = 64, cell: float | None = None,
             colors: int = 16, palette: np.ndarray | None = None, dither: bool = False,
             remove_bg: str | bool = "auto", alpha_threshold: int = 128, min_island: int = 2,
             outline_color: str | None = None, crop: bool = False, scale: int = 1,
             bg_tight: float = 48.0, bg_loose: float = 110.0) -> PixelResult:
    """Converte uma imagem em pixel art.

    size: maior lado em pixels de arte (modo downscale). cell: px da imagem por pixel de arte
    (fixa a grade; no modo grid pula a detecção). remove_bg: "auto" = só se a imagem não tiver
    alpha; True/False força. scale: fator nearest da versão `scaled`.
    """
    grid = None
    if mode == "grid":
        grid = Grid(float(cell), 0.0, 0.0) if cell else detect_grid(img)
        cells, alpha = sample_cells(img, grid)
    elif mode == "downscale":
        cells, alpha = _downscale(img, _target_size(img.width, img.height, size, cell))
    else:
        raise ValueError(f"modo desconhecido: {mode}")

    opaque = alpha >= alpha_threshold
    use_bg = (not _has_alpha(img)) if remove_bg == "auto" else bool(remove_bg)
    if use_bg:
        opaque &= ~background_mask(cells, bg_tight, bg_loose)
    if min_island > 1:
        opaque = clean_mask(opaque, min_size=min_island)
    if crop:
        cells, opaque = _crop(cells, opaque)
    cells = quantize(cells, opaque, colors=colors, palette=palette, dither=dither)
    if outline_color:
        cells, opaque = outline(cells, opaque, hex_to_rgb([outline_color])[0])

    rgba = np.zeros((*cells.shape[:2], 4), np.uint8)
    rgba[..., :3] = np.where(opaque[..., None], cells, 0)
    rgba[..., 3] = np.where(opaque, 255, 0)
    sprite = Image.fromarray(rgba, "RGBA")
    scaled = upscale(sprite, scale) if scale > 1 else sprite
    n = len(np.unique(rgba[..., :3][opaque], axis=0)) if opaque.any() else 0
    return PixelResult(sprite, scaled, n, grid)


def upscale(img: Image.Image, factor: int) -> Image.Image:
    return img.resize((img.width * factor, img.height * factor), Image.Resampling.NEAREST)
