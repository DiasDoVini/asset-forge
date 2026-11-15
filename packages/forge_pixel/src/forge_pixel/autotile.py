"""Autotile: as 16 variantes de borda de um tile base, mapa de teste e atlas.

Convenção da máscara (bitmask cardinal de 4 bits, igual ao aceite §B "cima, direita, baixo, esquerda"):

    bit 1 (UP)    = vizinho de CIMA é sólido
    bit 2 (RIGHT) = vizinho da DIREITA é sólido
    bit 4 (DOWN)  = vizinho de BAIXO é sólido
    bit 8 (LEFT)  = vizinho da ESQUERDA é sólido

    mask 15 = cercado pelos 4 lados = o tile base (nenhuma borda exposta)
    mask 14 = só o topo exposto (direita+baixo+esquerda sólidos) = `<mat>_top`
    mask  6 = topo e esquerda expostos (direita+baixo sólidos)    = `<mat>_top_l`
    mask 12 = topo e direita expostos (baixo+esquerda sólidos)    = `<mat>_top_r`
    mask  0 = bloco isolado (os 4 lados expostos)

Um lado "exposto" (bit 0) encosta no ar e recebe o tratamento de borda do `edge_style`:
o topo ganha a crosta (grama/musgo/luz de lua, com pontas pendentes), os lados e a base ganham o
aro escurecido, e os cantos (dois lados expostos que se encontram) são arredondados/cortados.
Lados sólidos ficam iguais ao tile base: por isso a coluna/linha da junção entre dois sólidos é a
mesma emenda do próprio base (repetição sem costura). Tudo depende só de (x, y, seed): determinístico.

Nome dos arquivos: `<prefixo>_mNN_v01.png` (NN = máscara com 2 dígitos) e a lista mínima do jogo
(`minimal_set`): `<mat>_top`, `<mat>_top_l`, `<mat>_top_r`, `<mat>_top_b`. No jogo atual `_top_b` é a
**variação b** do topo (grama mais curta com florzinhas), não "topo+base"; ver MINIMAL_MASKS.

Coordenadas do atlas: retângulos [x, y, l, a] com origem no canto SUPERIOR esquerdo da imagem.

Tamanho do tile: qualquer quadrado com lado ≥ MIN_TILE (16 px é o padrão histórico; os blocos de 11 px da
spec-blocos-11px.md usam os mesmos algoritmos). O tamanho vem sempre do próprio tile base; os estilos de
borda de 11 px têm presets próprios (`grass11`, `stone11`, `brick11`, ver `edge_preset_for_size`).

Formas de bloco (`shape_mask`): cheio, ladeira "\\" e "/", pico e meio-bloco, com a escada de 1 px da
hipotenusa definida aqui (o check_shape do validators compara o alfa com estas máscaras).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .palette import hex_to_rgb, js_round, rgb_to_hex, rgb_to_oklab
from .raster import to_image, to_rgba
from .spec import norm_hex

UP, RIGHT, DOWN, LEFT = 1, 2, 4, 8
ALL_MASKS: tuple[int, ...] = tuple(range(16))
BASE_MASK = 15
TILE = 16          # tamanho padrão (tiles de 16 px já entregues)
TILE11 = 11        # blocos de 11 px (World.T = 11)
MIN_TILE = 10      # lado mínimo: crosta, pontas e aro precisam de miolo
PPU = 16
# meio-bloco: linhas de baixo ocupadas (spec-blocos-11px.md §5: "half = linhas 5 a 10" -> 6 de 11);
# para outros tamanhos, a metade arredondada para cima
HALF_ROWS: dict[int, int] = {11: 6}

# lista mínima usada hoje pelo jogo: nome -> (máscara, variação). `top_b` = variação "b" do topo.
MINIMAL_MASKS: dict[str, tuple[int, int]] = {
    "top": (UP ^ 15, 0),            # 14
    "top_l": (RIGHT | DOWN, 0),     # 6
    "top_r": (DOWN | LEFT, 0),      # 12
    "top_b": (UP ^ 15, 1),          # 14, variação b
}


def mask_name(mask: int) -> str:
    """'m00'..'m15'."""
    return f"m{int(mask):02d}"


def exposed(mask: int) -> dict[str, bool]:
    """Lados expostos ao ar (bit 0) de uma máscara."""
    return {"up": not mask & UP, "right": not mask & RIGHT, "down": not mask & DOWN, "left": not mask & LEFT}


def describe_mask(mask: int) -> str:
    ex = [k for k, v in exposed(mask).items() if v]
    return "base (cercado)" if not ex else "expostos: " + ", ".join(ex)


# ---------------------------------------------------------------- formas de bloco

def half_rows(size: int) -> int:
    """Altura do meio-bloco em px (11 -> 6: linhas 5 a 10)."""
    return HALF_ROWS.get(int(size), (int(size) + 1) // 2)


def shape_mask(shape: str, size: int = TILE11) -> np.ndarray:
    """Máscara canônica (linhas, colunas) de bool, True = sólido; linha 0 = topo, coluna 0 = esquerda.

    full        tudo sólido;
    slope_down  "\\": ângulo reto embaixo à esquerda; a coluna x tem altura size - x (x=0 cheia, x=size-1
                1 px); sólido se y >= x. A hipotenusa é a escada regular de 1 px da diagonal (0,0)-(n-1,n-1);
    slope_up    "/": o espelho; sólido se y >= (size-1) - x;
    peak        triângulo com o vértice no topo da coluna central e a base inteira: a coluna x tem altura
                size - 2·|x - c| (c = (size-1)/2), escada de 2 px de altura por coluna; para 11 px as
                alturas são 1, 3, 5, 7, 9, 11, 9, 7, 5, 3, 1 (simétrico em volta da coluna 5);
    half        as `half_rows(size)` linhas de baixo (11 px: linhas 5 a 10)."""
    n = int(size)
    y, x = np.mgrid[0:n, 0:n]
    if shape == "full":
        return np.ones((n, n), bool)
    if shape == "slope_down":
        return y >= x
    if shape == "slope_up":
        return y >= (n - 1) - x
    if shape == "peak":
        c = (n - 1) / 2
        return y >= np.ceil(2 * np.abs(x - c)).astype(int)
    if shape == "half":
        return y >= n - half_rows(n)
    raise KeyError(f"forma desconhecida: {shape!r} (use full, slope_down, slope_up, peak, half)")


def shape_surface(shape: str, size: int = TILE11) -> np.ndarray:
    """Pixels sólidos da borda exposta da forma (encostam no ar por cima ou pela diagonal): onde vai a
    crosta/realce de topo. 4-vizinhança contra o ar acima, à esquerda (slope_up/peak) ou à direita
    (slope_down/peak); as arestas que encostam em bloco (base, lado reto) não contam."""
    m = shape_mask(shape, size)
    air = ~m
    # o pixel mais alto de cada coluna encosta no ar de cima (no full, a linha 0: topo exposto)
    top = m & (np.cumsum(m, axis=0) == 1)
    left = np.zeros_like(m)
    left[:, 1:] = air[:, :-1]
    right = np.zeros_like(m)
    right[:, :-1] = air[:, 1:]
    return top | (m & (left | right))


# ---------------------------------------------------------------- estilo de borda

class EdgeStyle(BaseModel):
    """Tratamento das bordas expostas. Cores em #rrggbb; listas por profundidade (0 = encostado no ar).

    top_rows:   crosta do topo, uma lista de cores por linha (a cor de cada pixel é sorteada por x na lista
                da linha). Ex. grama: [["#b6c97a", "#9aae63", "#9aae63"], ["#86965a"], ["#86965a", "#74854b"]].
    top_drip:   pontas pendentes abaixo da crosta (cores do topo ao fim da ponta); top_drip_max = 0..n px.
    top_lip:    cor de 1 px logo abaixo da crosta/pontas (sombra do corte), opcional.
    top_accent: cores de detalhe sobre a 1ª linha (flores, brilho), com probabilidade top_accent_rate
                (só nas colunas 2..13: a junção entre dois topos fica igual).
    side_wrap:  quanto a crosta do topo desce pelos lados expostos: largura por linha a partir do topo
                (ex. [2, 2, 2, 1, 1, 1, 1]); a cor segue top_rows pela distância ao lado exposto.
    side_shade: aro dos lados, fator de escurecimento por coluna (da borda para dentro); a cor vira a
                cor da paleta mais próxima do tom escurecido, entre as mais escuras (`shade_toward`).
                side_width (opcional) limita a largura.
    bottom_shade: aro de baixo, fator por linha (da borda para dentro; a largura é o tamanho da lista).
    side_rows / bottom_rows: cores fixas por profundidade no lugar do escurecimento (opcional).
    variants:   sobrescritas de campos por variação (variant=1 usa variants[0]...), mesma superfície.
    corner:     "round" = canto arredondado opaco (pixel do canto vira corner_color nos cantos de cima,
                e o aro mais escuro nos de baixo);
                "cut" = corta o canto em transparente (raio corner_radius, dx+dy < raio);
                "none" = canto reto. Só onde DOIS lados expostos se encontram.
    """

    model_config = ConfigDict(extra="forbid")

    top_rows: list[list[str]] = Field(default_factory=list)
    top_drip: list[str] = Field(default_factory=list)
    top_drip_max: int = 0
    top_lip: str | None = None
    top_accent: list[str] = Field(default_factory=list)
    top_accent_rate: float = 0.0
    side_wrap: list[int] = Field(default_factory=list)
    side_shade: list[float] = Field(default_factory=lambda: [0.80])
    side_width: int | None = None
    side_rows: list[list[str]] = Field(default_factory=list)
    bottom_shade: list[float] = Field(default_factory=lambda: [0.70, 0.85])
    bottom_rows: list[list[str]] = Field(default_factory=list)
    corner: str = "round"
    corner_radius: int = 2
    corner_color: str | None = None
    # sobrescritas por variação (variant 1 = 1º item): ex. a `_top_b` com flores e grama mais curta
    variants: list[dict[str, Any]] = Field(default_factory=list)

    def for_variant(self, variant: int) -> "EdgeStyle":
        if variant <= 0 or variant > len(self.variants):
            return self
        return EdgeStyle.model_validate({**self.model_dump(exclude={"variants"}), **self.variants[variant - 1]})

    @classmethod
    def coerce(cls, v: "EdgeStyle | dict[str, Any] | str | None") -> "EdgeStyle":
        if isinstance(v, EdgeStyle):
            return v
        if v is None:
            return cls()
        if isinstance(v, str):
            if v not in EDGE_PRESETS:
                raise KeyError(f"estilo de borda desconhecido: {v!r}. Disponíveis: {sorted(EDGE_PRESETS)}")
            return cls.model_validate(EDGE_PRESETS[v])
        d = dict(v)
        base = d.pop("preset", None)
        if base:
            d = {**EDGE_PRESETS[base], **d}
        return cls.model_validate(d)

    def colors(self) -> set[str]:
        out: set[str] = set()
        for rows in (self.top_rows, self.side_rows, self.bottom_rows):
            for r in rows:
                out |= {norm_hex(c) for c in r}
        out |= {norm_hex(c) for c in [*self.top_drip, *self.top_accent]}
        for c in (self.top_lip, self.corner_color):
            if c:
                out.add(norm_hex(c))
        return out


# presets de referência (paletas tile/dirt, tile/stone, tile/brick do paletas.json)
EDGE_PRESETS: dict[str, dict[str, Any]] = {
    "grass": {
        "top_rows": [["#9aae63", "#9aae63", "#b6c97a"], ["#86965a", "#9aae63"], ["#86965a", "#66753f"]],
        "top_drip": ["#66753f", "#66753f", "#4d5b2f"], "top_drip_max": 3, "top_lip": "#3a261a",
        "side_wrap": [3, 3, 2, 2, 1, 1, 1], "side_shade": [0.80], "bottom_shade": [0.72, 0.88],
        "corner": "round", "corner_color": "#4d5b2f",
        # _top_b: grama mais curta e lisa, com tufos claros esparsos (sem ouro: ouro é só alma/recompensa)
        "variants": [{"top_rows": [["#9aae63", "#9aae63", "#86965a"], ["#86965a", "#9aae63"], ["#66753f", "#86965a"]],
                      "top_drip_max": 1, "top_accent": ["#b6c97a"], "top_accent_rate": 0.3}],
    },
    "stone": {
        "top_rows": [["#5a5d7a", "#4b4d65", "#4b4d65"], ["#45475c", "#4b4d65"]],
        "top_lip": None, "side_wrap": [2, 1, 1], "side_shade": [0.80], "bottom_shade": [0.72, 0.86],
        "corner": "round", "corner_color": "#272836",
    },
    "brick": {
        "top_rows": [["#b02a44"], ["#7e1a2f"], ["#7e1a2f"], ["#4e0f1d"]],
        "side_wrap": [], "side_shade": [0.80], "bottom_shade": [0.75],
        "corner": "none",
    },
    # ---- blocos de 11 px: as mesmas cores, crosta/aro mais rasos (1 px de borda + miolo 9x9; no máximo
    # 1 px de aresta clara). A grama tem 2 linhas + pontas de até 2 px; desce 2 px pelos lados expostos.
    "grass11": {
        "top_rows": [["#9aae63", "#9aae63", "#b6c97a"], ["#86965a", "#66753f"]],
        "top_drip": ["#66753f", "#4d5b2f"], "top_drip_max": 2, "top_lip": "#3a261a",
        "side_wrap": [2, 2, 1, 1], "side_shade": [0.80], "bottom_shade": [0.72],
        "corner": "round", "corner_color": "#4d5b2f",
        "variants": [{"top_rows": [["#9aae63", "#9aae63", "#86965a"], ["#86965a", "#66753f"]],
                      "top_drip_max": 1, "top_accent": ["#b6c97a"], "top_accent_rate": 0.3}],
    },
    "stone11": {
        # 1 linha de quina clara (a mesma mistura do topo de 16 px: #5a5d7a com #4b4d65)
        "top_rows": [["#5a5d7a", "#4b4d65", "#4b4d65"]],
        "top_lip": None, "side_wrap": [1, 1], "side_shade": [0.80], "bottom_shade": [0.72],
        "corner": "round", "corner_color": "#272836",
    },
    "brick11": {
        "top_rows": [["#b02a44"], ["#7e1a2f"], ["#4e0f1d"]],
        "side_wrap": [], "side_shade": [0.80], "bottom_shade": [0.75],
        "corner": "none",
    },
}


def edge_preset_for_size(name: str, size: int) -> str:
    """Preset de borda adequado ao tamanho do tile: 'grass' num base de 11 px vira 'grass11' (se existir)."""
    if size != TILE and not name.endswith(str(size)) and f"{name}{size}" in EDGE_PRESETS:
        return f"{name}{size}"
    return name


# ---------------------------------------------------------------- utilidades de cor

def _hash(x: int, y: int, s: int) -> int:
    """Mesmo hash do art/tools/gen-world.js (32 bits, determinístico)."""
    def imul(a: int, b: int) -> int:
        return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF

    h = imul(x, 73856093) ^ imul(y, 19349663) ^ imul(s, 83492791)
    h ^= h >> 13
    h = imul(h, 1274126177)
    return ((h ^ (h >> 16)) & 0xFFFFFFFF) & 0x7FFFFFFF


def shade_toward(color: str, palette: Iterable[str], factor: float) -> str:
    """Cor da paleta mais próxima (OKLab) de `darken(color, factor)`, entre as estritamente mais escuras
    que `color` (factor < 1) ou mais claras (factor > 1). Sem candidata: a própria cor."""
    c = norm_hex(color)
    rgb = np.array(hex_to_rgb(c), dtype=np.float64)
    target = np.clip(js_round(rgb * factor), 0, 255)
    L0 = rgb_to_oklab(rgb)[0]
    cands = sorted({norm_hex(p) for p in palette})
    if not cands:
        return rgb_to_hex(target.astype(int))
    labs = rgb_to_oklab(np.array([hex_to_rgb(p) for p in cands], dtype=np.float64))
    ok = labs[:, 0] < L0 - 1e-6 if factor < 1 else labs[:, 0] > L0 + 1e-6
    if not ok.any():
        return c
    t = rgb_to_oklab(target)
    d = np.linalg.norm(labs - t, axis=1)
    d[~ok] = np.inf
    return cands[int(np.argmin(d))]


def _pick(colors: Sequence[str], x: int, y: int, seed: int) -> str:
    return colors[_hash(x, y, seed) % len(colors)]


def _drip_profile(n: int, max_drip: int, seed: int) -> list[int]:
    """Comprimento das pontas por coluna (0..max), periódico: col n-1 e col 0 diferem no máximo 1 px
    (a ponta não "quebra" na junção entre dois topos)."""
    if max_drip <= 0:
        return [0] * n
    prof = [_hash(x, 91, seed) % (max_drip + 1) for x in range(n)]
    # sem ponta isolada de 2 px entre vizinhas 0: vira 1 (forma de gota, não agulha)
    for x in range(n):
        lft, rgt = prof[(x - 1) % n], prof[(x + 1) % n]
        if prof[x] >= 2 and lft == 0 and rgt == 0:
            prof[x] = 1
    if abs(prof[-1] - prof[0]) > 1:
        prof[-1] = prof[0] + (1 if prof[-1] > prof[0] else -1)
    return prof


# ---------------------------------------------------------------- geração das 16 variantes

def tile_size_of(img: Image.Image | np.ndarray) -> int:
    """Lado do tile (quadrado, ≥ MIN_TILE); ValueError se não for um tile."""
    a = to_rgba(img)
    h, w = a.shape[:2]
    if h != w or w < MIN_TILE:
        raise ValueError(f"o tile deve ser quadrado com lado ≥ {MIN_TILE} px, ex. {TILE}x{TILE} ou "
                         f"{TILE11}x{TILE11} (veio {w}x{h})")
    return w


def _base_rgba(base: Image.Image | np.ndarray) -> np.ndarray:
    arr = to_rgba(base)
    tile_size_of(arr)
    return arr


def _set(arr: np.ndarray, x: int, y: int, color: str) -> None:
    arr[y, x, :3] = hex_to_rgb(color)
    arr[y, x, 3] = 255


def make_variant(base: Image.Image | np.ndarray, mask: int, edge_style: EdgeStyle | dict[str, Any] | str,
                 palette: Iterable[str] | None = None, seed: int = 1, variant: int = 0) -> Image.Image:
    """Uma variante (máscara 0..15) do tile base. `variant` > 0 muda só os detalhes do topo (pontas,
    flores) mantendo a linha de superfície — para `_top_b`."""
    st = EdgeStyle.coerce(edge_style).for_variant(variant)
    src = _base_rgba(base)
    arr = src.copy()
    # o aro escurece só para cores que o conjunto já usa (as do base + as do estilo): o número de cores das
    # variantes não passa de base + estilo. `palette` (lista travada do grupo) filtra as candidatas.
    own = {rgb_to_hex(c) for c in src[src[..., 3] > 0][:, :3]} | st.colors()
    allowed = {norm_hex(c) for c in (palette or [])}
    pal = sorted(own & allowed if allowed and own & allowed else own)
    ex = exposed(mask)
    n = src.shape[0]                     # 16 ou 11 (o tamanho vem do próprio base)
    s_detail = seed + 7919 * variant

    def shade(x: int, y: int, f: float) -> None:
        if arr[y, x, 3] == 0:
            return
        c = rgb_to_hex(src[y, x, :3])
        _set(arr, x, y, shade_toward(c, pal, f))

    # 1) base: aro de baixo
    if ex["down"]:
        for d in range(max(len(st.bottom_rows), len(st.bottom_shade))):
            y = n - 1 - d
            for x in range(n):
                if st.bottom_rows and d < len(st.bottom_rows):
                    _set(arr, x, y, _pick(st.bottom_rows[d], x, y, seed + 3))
                elif d < len(st.bottom_shade):
                    shade(x, y, st.bottom_shade[d])
    # 2) lados: aro escurecido (em todas as linhas; o topo sobrescreve depois)
    width = st.side_width if st.side_width is not None else max(len(st.side_shade), len(st.side_rows))
    for side in ("left", "right"):
        if not ex[side]:
            continue
        for d in range(width):
            x = d if side == "left" else n - 1 - d
            for y in range(n):
                if st.side_rows and d < len(st.side_rows):
                    _set(arr, x, y, _pick(st.side_rows[d], x, y, seed + 5))
                elif d < len(st.side_shade):
                    f = st.side_shade[d]
                    if ex["down"] and y >= n - len(st.bottom_shade):   # canto de baixo: escurece mais
                        f *= st.bottom_shade[min(n - 1 - y, len(st.bottom_shade) - 1)]
                    # o aro parte do base (não do já escurecido pela base) para ficar igual em toda a coluna
                    if arr[y, x, 3]:
                        c = rgb_to_hex(src[y, x, :3])
                        _set(arr, x, y, shade_toward(c, pal, f))
    # 3) topo: crosta + pontas + sombra do corte; desce pelos lados expostos (side_wrap)
    if ex["up"] and st.top_rows:
        depth = len(st.top_rows)
        drip = _drip_profile(n, st.top_drip_max, s_detail)
        for x in range(n):
            for d in range(depth):
                _set(arr, x, d, _pick(st.top_rows[d], x, d, seed + 11 + d))
            k = drip[x]
            for j in range(k):
                c = st.top_drip[min(j, len(st.top_drip) - 1)] if st.top_drip else st.top_rows[-1][0]
                if j == k - 1 and st.top_drip:
                    c = st.top_drip[-1]
                _set(arr, x, depth + j, c)
            if st.top_lip and depth + k < n:
                # sombra do corte só onde há ponta curta (dá volume sem virar uma faixa reta)
                if k < st.top_drip_max or st.top_drip_max == 0:
                    if _hash(x, 97, s_detail) % 3 != 0:
                        _set(arr, x, depth + k, st.top_lip)
            if 2 <= x < n - 2 and st.top_accent and st.top_accent_rate > 0 and _hash(x, 0, s_detail + 13) % 1000 < st.top_accent_rate * 1000:
                _set(arr, x, 0, _pick(st.top_accent, x, 0, s_detail + 17))
        for side in ("left", "right"):
            if not ex[side]:
                continue
            for y, w in enumerate(st.side_wrap):
                if y >= n:
                    break
                for d in range(w):
                    x = d if side == "left" else n - 1 - d
                    row = st.top_rows[min(d, depth - 1)]
                    _set(arr, x, y, _pick(row, x, y, seed + 23 + d))
            # ponta da descida: a cor final das pontas logo abaixo da última linha com crosta
            yw = len(st.side_wrap)
            if st.side_wrap and st.top_drip and yw < n:
                x = 0 if side == "left" else n - 1
                _set(arr, x, yw, st.top_drip[-1])
    # 4) cantos: só onde dois lados expostos se encontram
    corners = {(0, 0): ("up", "left"), (n - 1, 0): ("up", "right"),
               (0, n - 1): ("down", "left"), (n - 1, n - 1): ("down", "right")}
    r = max(1, st.corner_radius)
    for (cx, cy), (a, b) in corners.items():
        if not (ex[a] and ex[b]) or st.corner == "none":
            continue
        for dy in range(r):
            for dx in range(r):
                if dx + dy >= r:
                    continue
                x = cx + dx if cx == 0 else cx - dx
                y = cy + dy if cy == 0 else cy - dy
                if st.corner == "cut" and (dx + dy < r - 1 or r == 1):
                    arr[y, x] = 0
                elif st.corner == "round" and dx + dy == 0:
                    # canto de cima: cor de sombra da crosta; canto de baixo: o aro ainda mais escuro
                    col = (st.corner_color if a == "up" and st.corner_color else
                           shade_toward(rgb_to_hex(arr[y, x, :3]), pal, 0.75))
                    _set(arr, x, y, col)
                elif st.corner == "cut":
                    # pixel da nova borda: cor de superfície (topo) ou de sombra (baixo)
                    if a == "up" and st.top_rows:
                        _set(arr, x, y, st.top_rows[0][0])
                    else:
                        shade(x, y, min(st.bottom_shade or [0.7]))
    return to_image(arr)


def make_autotile_set(base: Image.Image | np.ndarray, edge_style: EdgeStyle | dict[str, Any] | str,
                      palette: Iterable[str] | None = None, seed: int = 1) -> dict[int, Image.Image]:
    """As 16 variantes {máscara: imagem}; a 15 é o próprio base (cópia exata). Determinístico."""
    pal = list(palette or [])
    out: dict[int, Image.Image] = {}
    for m in ALL_MASKS:
        out[m] = to_image(_base_rgba(base)) if m == BASE_MASK else make_variant(base, m, edge_style, pal, seed)
    return out


def minimal_set(base: Image.Image | np.ndarray, edge_style: EdgeStyle | dict[str, Any] | str, material: str,
                palette: Iterable[str] | None = None, seed: int = 1,
                tiles_by_mask: dict[int, Image.Image] | None = None) -> dict[str, Image.Image]:
    """Lista mínima com os nomes do jogo: {'<mat>_top': img, '<mat>_top_l', '<mat>_top_r', '<mat>_top_b'}.
    As três primeiras são as máscaras 14, 6 e 12 do conjunto completo; `_top_b` é a variação b do topo
    (mesma linha de superfície, pontas/flores diferentes)."""
    pal = list(palette or [])
    full = tiles_by_mask or {}
    out: dict[str, Image.Image] = {}
    for suffix, (mask, var) in MINIMAL_MASKS.items():
        if var == 0 and mask in full:
            out[f"{material}_{suffix}"] = full[mask]
        else:
            out[f"{material}_{suffix}"] = make_variant(base, mask, edge_style, pal, seed, variant=var)
    return out


# ---------------------------------------------------------------- mapa de teste

# 12x8 com buracos, degraus, coluna fina, cruz flutuante, plataforma e bloco isolado: cobre as 16 máscaras
DEFAULT_TEST_GRID: tuple[str, ...] = (
    "#.##.....#..",
    "#..#..#.....",
    "##.#.###.#..",
    "##....#..##.",
    "###.......##",
    "##..####.###",
    "###.####.###",
    "############",
)


def grid_to_bool(grid: Sequence[str] | np.ndarray) -> np.ndarray:
    """Grade de texto ('#' sólido, '.' ar) ou array booleano -> array (linhas, colunas) de bool."""
    if isinstance(grid, np.ndarray):
        return grid.astype(bool)
    rows = list(grid)
    if not rows or any(len(r) != len(rows[0]) for r in rows):
        raise ValueError("a grade precisa de linhas do mesmo tamanho")
    return np.array([[ch not in ".  " for ch in r] for r in rows], dtype=bool)


def masks_of_grid(grid: Sequence[str] | np.ndarray, outside_solid: bool = False) -> np.ndarray:
    """Máscara de cada célula sólida a partir dos vizinhos (−1 = ar). Fora do mapa = ar (ou sólido)."""
    g = grid_to_bool(grid)
    pad = np.pad(g, 1, constant_values=outside_solid)
    up, down = pad[:-2, 1:-1], pad[2:, 1:-1]
    left, right = pad[1:-1, :-2], pad[1:-1, 2:]
    m = up * UP + right * RIGHT + down * DOWN + left * LEFT
    return np.where(g, m, -1).astype(int)


def render_test_map(tiles_by_mask: dict[int, Image.Image | np.ndarray], grid: Sequence[str] | np.ndarray | None = None,
                    outside_solid: bool = False, background: str | None = None) -> Image.Image:
    """Monta o mapa (cada célula sólida recebe a variante da sua máscara). RGBA; `background` pinta o ar."""
    masks = masks_of_grid(grid if grid is not None else DEFAULT_TEST_GRID, outside_solid)
    rows, cols = masks.shape
    tiles = {int(k): to_rgba(v) for k, v in tiles_by_mask.items()}
    first = tiles.get(BASE_MASK, next(iter(tiles.values()), None))
    T = first.shape[0] if first is not None else TILE      # tamanho do tile (16 ou 11)
    out = np.zeros((rows * T, cols * T, 4), np.uint8)
    if background:
        out[..., :3] = hex_to_rgb(background)
        out[..., 3] = 255
    for r in range(rows):
        for c in range(cols):
            m = int(masks[r, c])
            if m < 0:
                continue
            if m not in tiles:
                raise KeyError(f"falta a variante da máscara {m} ({describe_mask(m)})")
            t = tiles[m]
            region = out[r * T:(r + 1) * T, c * T:(c + 1) * T]
            op = t[..., 3] > 0
            region[op] = t[op]
    return to_image(out)


# ---------------------------------------------------------------- atlas

_MASK_IN_NAME = re.compile(r"_m(\d{2})(?:_v\d{2})?(?:\.png)?$")


def mask_from_name(name: str) -> int | None:
    m = _MASK_IN_NAME.search(name)
    if m:
        return int(m.group(1))
    for suffix, (mask, var) in MINIMAL_MASKS.items():
        if re.search(rf"_{suffix}(?:_v\d{{2}})?(?:\.png)?$", name):
            return mask
    return None


def atlas(tiles: dict[str, Image.Image | np.ndarray], columns: int = 4,
          masks: dict[str, int] | None = None, padding: int = 0) -> tuple[Image.Image, dict[str, Any]]:
    """Empacota os tiles (na ordem do dict) numa grade de `columns` colunas.

    Metadados: {tile_size, ppu, columns, rows, width, height, padding, origin: "top-left",
    tiles: [{name, mask, rect: [x, y, l, a]}]}. `mask` vem de `masks` ou do nome (`_mNN`, `_top_l`...)."""
    if not tiles:
        raise ValueError("atlas vazio")
    cols = max(1, min(int(columns), len(tiles)))
    rows = (len(tiles) + cols - 1) // cols
    T = tile_size_of(next(iter(tiles.values())))        # todos do tamanho do 1º (16 ou 11)
    step = T + padding
    W, H = cols * step - padding, rows * step - padding
    out = np.zeros((H, W, 4), np.uint8)
    meta_tiles = []
    for i, (name, img) in enumerate(tiles.items()):
        a = to_rgba(img)
        if a.shape[:2] != (T, T):
            raise ValueError(f"{name}: tile de {a.shape[1]}x{a.shape[0]} (esperado {T}x{T})")
        x, y = (i % cols) * step, (i // cols) * step
        out[y:y + T, x:x + T] = a
        mk = (masks or {}).get(name)
        if mk is None:
            mk = mask_from_name(name)
        meta_tiles.append({"name": name, "mask": mk, "rect": [x, y, T, T]})
    meta = {"format": "asset-forge-atlas/1", "tile_size": T, "ppu": PPU, "columns": cols, "rows": rows,
            "width": W, "height": H, "padding": padding, "origin": "top-left",
            "mask_bits": {"up": UP, "right": RIGHT, "down": DOWN, "left": LEFT}, "tiles": meta_tiles}
    return to_image(out), meta


def write_atlas_json(meta: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
