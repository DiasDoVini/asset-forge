"""Camadas de parallax (fundos distantes / 2.5D) em pixel art determinística, sem IA.

Tudo aqui repete na horizontal POR CONSTRUÇÃO: o desenho acontece num `LayerCanvas` cujo eixo x é
circular (x módulo largura), os perfis de altura são somas de senos com número INTEIRO de ciclos por
largura e os detalhes espalhados caem em posições módulo largura. A emenda (coluna última × 0) é,
portanto, igual a qualquer fronteira interna.

Peças:
    LayerCanvas        tela indexada (paleta travada, -1 = transparente) com x circular: rect, column,
                       silhouette, disc, ring, poly, put; image() -> RGBA de alfa binário
    periodic_profile   perfil 1D periódico (alturas de silhueta, ondulação de faixas)
    periodic_scatter   posições x espalhadas por semente, periódicas (uma por "fatia" da largura)
    banded_gradient    gradiente vertical por faixas de cor da paleta (sem pontilhado), borda opcional
                       ondulada e periódica
    tile_x / composite / scroll_frames / stack_sheet / upscale
                       montagem das pranchas de revisão (pilha 1x/2x, rolagem, emenda 2x)

Convenção: (x, y) em coordenadas de IMAGEM (y = 0 no topo), diferente do `.meta`; as funções de
desenho recebem y de cima para baixo porque o fundo é pensado como "céu em cima, chão embaixo".
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw

from .palette import hex_to_rgb
from .spec import norm_hex

# velocidades de parallax assumidas (fração do deslocamento da câmera), do protótipo web:
# layer(FAR, .08, .04); layer(MID, .25, .12). A camada "near" é nova: 0,5 x / 0,25 y.
DEFAULT_SPEEDS: dict[str, tuple[float, float]] = {"far": (0.08, 0.04), "mid": (0.25, 0.12), "near": (0.5, 0.25)}
VIEW = (640, 360)          # câmera do jogo: ortográfica, 360 px de altura (orthographicSize 11,25 @ 16 PPU), 16:9


# ---------------------------------------------------------------- aleatoriedade determinística

def rng(seed: int) -> np.random.Generator:
    """Gerador PCG64 com semente: mesma semente = mesmos números em qualquer máquina."""
    return np.random.default_rng(int(seed) & 0xFFFFFFFF)


def periodic_profile(width: int, seed: int, terms: Sequence[tuple[int, float]] = ((1, 0.5), (3, 0.3), (7, 0.2)),
                     ) -> np.ndarray:
    """Perfil 1D (largura,) em ~[-1, 1]: soma de senos com `k` ciclos INTEIROS por largura (então o
    valor em x = largura é o mesmo de x = 0) e fase sorteada pela semente. terms = ((k, amplitude), ...)."""
    r = rng(seed)
    x = np.arange(width, dtype=np.float64) / width
    out = np.zeros(width)
    total = sum(abs(a) for _, a in terms) or 1.0
    for k, a in terms:
        if int(k) != k or k < 1:
            raise ValueError(f"k deve ser inteiro ≥ 1 (ciclos por largura), veio {k!r}")
        out += a * np.sin(2 * math.pi * (k * x + r.random()))
    return out / total


def periodic_scatter(width: int, count: int, seed: int, jitter: float = 0.8) -> list[int]:
    """`count` posições x em [0, largura): uma por fatia de largura/count, deslocada ao acaso dentro
    da fatia (jitter 0 = espaçamento regular). Periódicas: a distância da última para a primeira
    (passando pela emenda) segue a mesma regra das internas."""
    if count <= 0:
        return []
    r = rng(seed)
    slot = width / count
    out = []
    for i in range(count):
        off = (r.random() - 0.5) * jitter * slot
        out.append(int(math.floor(i * slot + slot / 2 + off)) % width)
    return out


def banded_gradient(height: int, width: int, stops: Sequence[tuple[int, int]], wobble: np.ndarray | None = None,
                    ) -> np.ndarray:
    """Gradiente vertical por faixas: stops = [(y_inicio, índice_de_cor), ...] em ordem crescente de y.
    Cada faixa vai do seu y até o próximo stop. `wobble` (largura,) desloca as fronteiras por coluna
    (inteiro, em px; use periodic_profile para manter a emenda). Devolve índices (altura, largura)."""
    ys = np.arange(height)[:, None]
    shift = np.zeros(width, dtype=np.int64) if wobble is None else np.round(wobble).astype(np.int64)
    out = np.full((height, width), stops[0][1], dtype=np.int16)
    for y0, ci in stops[1:]:
        out[ys >= (y0 + shift)[None, :]] = ci
    return out


# ---------------------------------------------------------------- tela com x circular

class LayerCanvas:
    """Tela indexada na paleta da camada. Índice -1 = transparente. Todo x é tomado módulo a largura,
    então qualquer forma que atravesse a borda direita continua na esquerda (repetição sem emenda)."""

    def __init__(self, width: int, height: int, colors: Sequence[str], fill: str | None = None):
        self.width, self.height = int(width), int(height)
        self.colors: list[str] = [norm_hex(c) for c in colors]
        self._index = {c: i for i, c in enumerate(self.colors)}
        self.idx = np.full((self.height, self.width), -1, dtype=np.int16)
        if fill is not None:
            self.idx[:] = self.ci(fill)

    def ci(self, color: str | int) -> int:
        """Índice da cor (aceita índice direto). Cor fora da paleta da camada levanta KeyError."""
        if isinstance(color, (int, np.integer)):
            return int(color)
        c = norm_hex(color)
        if c not in self._index:
            raise KeyError(f"cor {c} fora da paleta da camada: {self.colors}")
        return self._index[c]

    # ---- primitivas (y de cima para baixo; x circular)
    def put(self, x: int, y: int, color: str | int) -> None:
        if 0 <= y < self.height:
            self.idx[y, int(x) % self.width] = self.ci(color)

    def column(self, x: int, y0: int, y1: int, color: str | int) -> None:
        """Pinta a coluna x de y0 até y1 (inclusivo), recortada na altura."""
        a, b = max(0, int(min(y0, y1))), min(self.height - 1, int(max(y0, y1)))
        if a <= b:
            self.idx[a:b + 1, int(x) % self.width] = self.ci(color)

    def rect(self, x: int, y: int, w: int, h: int, color: str | int) -> None:
        if w <= 0 or h <= 0:
            return
        y0, y1 = max(0, int(y)), min(self.height, int(y + h))
        if y0 >= y1:
            return
        cols = (np.arange(int(x), int(x) + int(w)) % self.width)
        self.idx[y0:y1][:, cols] = self.ci(color)

    def silhouette(self, tops: np.ndarray, color: str | int, bottom: int | None = None) -> None:
        """Preenche cada coluna x de tops[x] até `bottom` (padrão: o fim da tela). tops com a largura da tela."""
        c = self.ci(color)
        ys = np.arange(self.height)[:, None]
        t = np.round(np.asarray(tops)).astype(np.int64)[None, :]
        m = ys >= t
        if bottom is not None:
            m &= ys <= bottom
        self.idx[m] = c

    def disc(self, cx: float, cy: float, r: float, color: str | int, only: Iterable[str | int] | None = None) -> None:
        """Disco de raio r (centro em pixel + 0,5). only = só repinta pixels destas cores (halo sobre o céu)."""
        self._region(lambda dx, dy: dx * dx + dy * dy <= r * r, cx, cy, r, color, only)

    def ring(self, cx: float, cy: float, r_out: float, r_in: float, color: str | int,
             only: Iterable[str | int] | None = None) -> None:
        self._region(lambda dx, dy: r_in * r_in < dx * dx + dy * dy <= r_out * r_out, cx, cy, r_out, color, only)

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, color: str | int,
                only: Iterable[str | int] | None = None) -> None:
        self._region(lambda dx, dy: (dx / rx) ** 2 + (dy / ry) ** 2 <= 1.0, cx, cy, max(rx, ry), color, only)

    def _region(self, inside, cx, cy, r, color, only) -> None:
        c = self.ci(color)
        allow = None if only is None else {self.ci(o) for o in only}
        x0, x1 = int(math.floor(cx - r - 1)), int(math.ceil(cx + r + 1))
        y0, y1 = max(0, int(math.floor(cy - r - 1))), min(self.height, int(math.ceil(cy + r + 1)))
        for y in range(y0, y1):
            for x in range(x0, x1 + 1):
                if inside(x + 0.5 - cx, y + 0.5 - cy):
                    xx = x % self.width
                    if allow is None or int(self.idx[y, xx]) in allow:
                        self.idx[y, xx] = c

    def poly(self, pts: Sequence[tuple[float, float]], color: str | int) -> None:
        """Polígono preenchido (regra par-ímpar, centro do pixel), com x circular."""
        c = self.ci(color)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bx0, bx1 = int(math.floor(min(xs))), int(math.ceil(max(xs)))
        by0, by1 = max(0, int(math.floor(min(ys)))), min(self.height, int(math.ceil(max(ys))) + 1)
        n = len(pts)
        for y in range(by0, by1):
            py = y + 0.5
            nodes = []
            for i in range(n):
                (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
                if (ya <= py < yb) or (yb <= py < ya):
                    nodes.append(xa + (py - ya) / (yb - ya) * (xb - xa))
            nodes.sort()
            for a, b in zip(nodes[0::2], nodes[1::2]):
                for x in range(max(bx0, int(math.ceil(a - 0.5))), min(bx1, int(math.floor(b - 0.5))) + 1):
                    self.idx[y, x % self.width] = c

    def paint_mask(self, mask: np.ndarray, color: str | int) -> None:
        self.idx[mask] = self.ci(color)

    def replace(self, old: str | int, new: str | int, mask: np.ndarray | None = None) -> None:
        m = self.idx == self.ci(old)
        if mask is not None:
            m &= mask
        self.idx[m] = self.ci(new)

    def image(self) -> Image.Image:
        """RGBA de alfa binário (0 ou 255)."""
        lut = np.zeros((len(self.colors) + 1, 4), np.uint8)
        for i, c in enumerate(self.colors):
            lut[i, :3] = hex_to_rgb(c)
            lut[i, 3] = 255
        arr = lut[np.where(self.idx < 0, len(self.colors), self.idx)]
        return Image.fromarray(arr, "RGBA")

    def used_colors(self) -> list[str]:
        return [self.colors[i] for i in sorted(set(int(v) for v in np.unique(self.idx)) - {-1})]


# ---------------------------------------------------------------- montagem / pranchas

def _rgba(img: Image.Image) -> Image.Image:
    return img if img.mode == "RGBA" else img.convert("RGBA")


def upscale(img: Image.Image, k: int) -> Image.Image:
    """Ampliação inteira por vizinho mais próximo (só para revisão; o PNG do jogo não é ampliado)."""
    return img if k == 1 else img.resize((img.width * k, img.height * k), Image.Resampling.NEAREST)


def tile_x(img: Image.Image, n: int = 2) -> Image.Image:
    """A camada repetida n vezes lado a lado (prova da emenda)."""
    img = _rgba(img)
    out = Image.new("RGBA", (img.width * n, img.height))
    for i in range(n):
        out.paste(img, (i * img.width, 0))
    return out


def shift_x(img: Image.Image, dx: int) -> Image.Image:
    """Desloca com volta (como a câmera rolando sobre a camada repetida): o pixel x vai para x + dx."""
    arr = np.asarray(_rgba(img))
    return Image.fromarray(np.roll(arr, int(dx), axis=1), "RGBA")


def composite(layers: Sequence[Image.Image], offsets: Sequence[int] | None = None, background: str = "#0b0a12",
              size: tuple[int, int] | None = None) -> Image.Image:
    """Empilha do mais distante (primeiro) ao mais próximo. offsets[i] = deslocamento x (com volta) da
    camada i; size recorta a janela da câmera (padrão: o tamanho da primeira camada)."""
    w, h = size or layers[0].size
    out = Image.new("RGBA", (w, h), hex_to_rgb(background) + (255,))
    for i, lay in enumerate(layers):
        dx = (offsets[i] if offsets else 0)
        lay = shift_x(lay, dx)
        if lay.width < w:
            lay = tile_x(lay, -(-w // lay.width))
        out.alpha_composite(lay.crop((0, 0, w, h)))
    return out


def scroll_offsets(camera_x: float, speeds: Sequence[float]) -> list[int]:
    """Deslocamento de cada camada para a câmera em camera_x (px do mundo): -round(x × velocidade)."""
    return [-int(round(camera_x * s)) for s in speeds]


def scroll_frames(layers: Sequence[Image.Image], speeds: Sequence[float], camera_xs: Sequence[float],
                  foreground: Image.Image | None = None, background: str = "#0b0a12") -> list[Image.Image]:
    """Quadros da simulação de rolagem: para cada x da câmera, as camadas deslocadas pela sua
    velocidade; `foreground` (cena de jogo, velocidade 1) por cima, também deslocado."""
    frames = []
    for cx in camera_xs:
        offs = scroll_offsets(cx, speeds)
        f = composite(layers, offs, background)
        if foreground is not None:
            fg = shift_x(foreground, -int(round(cx)))
            f.alpha_composite(fg.crop((0, 0, f.width, f.height)))
        frames.append(f)
    return frames


def save_gif(frames: Sequence[Image.Image], path, ms: int = 120, scale: int = 1) -> None:
    """GIF em loop (paleta exata: a arte tem poucas cores, sem pontilhado)."""
    fr = [upscale(f.convert("RGB"), scale) for f in frames]
    pal = [f.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in fr]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=ms, loop=0, optimize=False, disposal=1)


def ascii_text(s: str) -> str:
    """Tira acentos (a fonte padrão do PIL não tem glifos para eles): 'câmera' -> 'camera'."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).encode(
        "ascii", "replace").decode("ascii")


def labeled(img: Image.Image, label: str, bg: str = "#1a1830", fg: str = "#cdc9df", pad: int = 4) -> Image.Image:
    """Imagem com uma faixa de título em cima (texto da fonte padrão do PIL)."""
    img = _rgba(img)
    out = Image.new("RGBA", (img.width, img.height + 14 + pad), hex_to_rgb(bg) + (255,))
    ImageDraw.Draw(out).text((pad, 2), ascii_text(label), fill=hex_to_rgb(fg))
    out.alpha_composite(img, (0, 14 + pad))
    return out


def stack_vertical(images: Sequence[Image.Image], gap: int = 6, bg: str = "#1a1830") -> Image.Image:
    w = max(i.width for i in images)
    h = sum(i.height for i in images) + gap * (len(images) - 1)
    out = Image.new("RGBA", (w, h), hex_to_rgb(bg) + (255,))
    y = 0
    for i in images:
        out.alpha_composite(_rgba(i), (0, y))
        y += i.height + gap
    return out


def stack_horizontal(images: Sequence[Image.Image], gap: int = 6, bg: str = "#1a1830") -> Image.Image:
    w = sum(i.width for i in images) + gap * (len(images) - 1)
    h = max(i.height for i in images)
    out = Image.new("RGBA", (w, h), hex_to_rgb(bg) + (255,))
    x = 0
    for i in images:
        out.alpha_composite(_rgba(i), (x, 0))
        x += i.width + gap
    return out


def seam_marks(img: Image.Image, xs: Sequence[int], color: str = "#e8b45a", tick: int = 6) -> Image.Image:
    """Margem acima e abaixo com marcas nas colunas xs (onde fica a emenda), sem pintar sobre a arte."""
    img = _rgba(img)
    out = Image.new("RGBA", (img.width, img.height + 2 * tick), (0x1a, 0x18, 0x30, 255))
    out.alpha_composite(img, (0, tick))
    d = ImageDraw.Draw(out)
    for x in xs:
        d.line([(x, 0), (x, tick - 2)], fill=hex_to_rgb(color), width=1)
        d.line([(x, img.height + tick + 1), (x, img.height + 2 * tick - 1)], fill=hex_to_rgb(color), width=1)
    return out


def stack_sheet(layers: Sequence[Image.Image], names: Sequence[str], background: str = "#0b0a12") -> Image.Image:
    """Prancha da pilha: cada camada sozinha (a de transparência sobre o fundo escuro) e a pilha
    composta a 1x; a 2x vai em prancha separada (é grande)."""
    parts = [labeled(composite([lay], None, background), f"{n} (1x)") for lay, n in zip(layers, names)]
    parts.append(labeled(composite(list(layers), None, background), "pilha composta (1x)"))
    return stack_vertical(parts)
