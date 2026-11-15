"""Formato "pixel map": sprite como texto (mapa ASCII + legenda), no estilo do art/tools/gen-parts.js.

    {
      "format": "asset-forge-pixelmap/1",
      "width": 8, "height": 12,
      "legend": {"C": "#7e1a2f", "d": "#4f0f1e", "r": "#a8324a"},
      "rows": ["........", "...dCr..", ...],         # de cima para baixo: exatamente `height` linhas de `width` chars
      "layers": [{"x0": 3, "y_top": 9, "rows": ["dCr", "dCr"]}],   # opcional; y contado de BAIXO para cima
      "outline": {"auto": true, "color": "#0b0a12"},
      "side": null,          # null | "R" | "L" (L = mesma forma 22% mais escura, menos o contorno)
      "shade_L": 0.78
    }

'.' e ' ' são transparentes. Cor da legenda: "#rrggbb" (opaca) ou "#rrggbbaa" (alfa parcial declarado, só para
assets translúcidos como a água do aceite §8). `rows` pode ser vazio (ou omitido) quando o desenho vem só de `layers`;
as camadas são aplicadas por cima das linhas, na ordem. O contorno automático pinta de `outline.color`
todo pixel transparente 4-adjacente a um preenchido, dentro do canvas (igual ao `outline()` do gen-parts.js).
"""
from __future__ import annotations

import json
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .palette import hex_to_rgb, js_round, rgb_to_hex
from .raster import outline_ring, to_image, to_rgba
from .spec import OUTLINE_COLOR, norm_hex

FORMAT = "asset-forge-pixelmap/1"
TRANSPARENT = {".", " "}
# caracteres usados por from_image (o contorno recebe 'k', como no gen-parts.js)
CHAR_POOL = ("kabcdefghijlmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
             "!#$%&*+-/:;<=>?@^_~=|()[]{}<>,'\"`")


def norm_color(c: str) -> str:
    """'#ABCDEF' -> '#abcdef'; '#ABCDEF80' -> '#abcdef80' (alfa); '#rrggbbff' vira '#rrggbb'."""
    s = str(c).strip().lower()
    if len(s.lstrip("#")) == 8:
        rgb, a = norm_hex("#" + s.lstrip("#")[:6]), s.lstrip("#")[6:]
        int(a, 16)
        return rgb if a == "ff" else rgb + a
    return norm_hex(s)


def color_alpha(c: str) -> tuple[str, int]:
    """'#rrggbb[aa]' -> ('#rrggbb', alfa 0..255)."""
    c = norm_color(c)
    return (c[:7], int(c[7:9], 16)) if len(c) == 9 else (c, 255)


class PixelMapError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("pixel map inválido:\n- " + "\n- ".join(errors))


class OutlineCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto: bool = True
    color: str = OUTLINE_COLOR


class Layer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x0: int
    y_top: int = Field(..., description="y da 1ª linha, contado de baixo para cima (como layer() do gen-parts.js)")
    rows: list[str]


class PixelMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["asset-forge-pixelmap/1"] = FORMAT
    width: int
    height: int
    legend: dict[str, str]
    rows: list[str] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    outline: OutlineCfg = Field(default_factory=OutlineCfg)
    side: Literal["L", "R"] | None = None
    shade_L: float = 0.78

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        if not d["layers"]:
            d.pop("layers")
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- parse / validação

def validate_map(data: dict[str, Any] | PixelMap) -> list[str]:
    """Lista de erros legíveis (vazia = ok). Linhas e colunas são numeradas a partir de 0."""
    errs: list[str] = []
    if isinstance(data, PixelMap):
        data = data.model_dump()
    if not isinstance(data, dict):
        return ["o pixel map deve ser um objeto JSON"]
    fmt = data.get("format", FORMAT)
    if fmt != FORMAT:
        errs.append(f"format deve ser {FORMAT!r} (veio {fmt!r})")
    w, h = data.get("width"), data.get("height")
    if not isinstance(w, int) or not isinstance(h, int) or w < 1 or h < 1:
        errs.append(f"width/height devem ser inteiros ≥ 1 (veio {w!r} x {h!r})")
        return errs
    if w > 1024 or h > 1024:
        errs.append(f"canvas grande demais para pixel map: {w}x{h} (máx. 1024)")
    legend = data.get("legend") or {}
    if not isinstance(legend, dict) or not legend:
        errs.append("legend deve ser um objeto {char: '#rrggbb'} não vazio")
        legend = {}
    for k, v in legend.items():
        if len(k) != 1:
            errs.append(f"legenda: chave {k!r} deve ter exatamente 1 caractere")
        elif k in TRANSPARENT:
            errs.append(f"legenda: {k!r} é reservado para transparente")
        try:
            norm_color(v)
        except (ValueError, TypeError):
            errs.append(f"legenda: cor de {k!r} inválida: {v!r} (use #rrggbb ou #rrggbbaa)")
    outline = data.get("outline") or {}
    if isinstance(outline, dict) and outline.get("color") is not None:
        try:
            norm_hex(outline["color"])
        except (ValueError, TypeError):
            errs.append(f"outline.color inválida: {outline['color']!r}")
    rows = data.get("rows") or []
    layers = data.get("layers") or []
    if not rows and not layers:
        errs.append("o mapa precisa de rows (ou de layers)")
    if rows:
        if not isinstance(rows, list) or not all(isinstance(r, str) for r in rows):
            errs.append("rows deve ser uma lista de strings")
            rows = []
        elif len(rows) != h:
            errs.append(f"rows tem {len(rows)} linhas; height = {h}")
        for i, r in enumerate(rows):
            if len(r) != w:
                errs.append(f"linha {i}: {len(r)} caracteres; width = {w}")
            for j, ch in enumerate(r):
                if ch not in TRANSPARENT and ch not in legend:
                    errs.append(f"linha {i}, coluna {j}: caractere {ch!r} fora da legenda")
    for n, lay in enumerate(layers):
        if not isinstance(lay, dict) or "rows" not in lay or "x0" not in lay or "y_top" not in lay:
            errs.append(f"layers[{n}]: precisa de x0, y_top e rows")
            continue
        for i, r in enumerate(lay["rows"]):
            for j, ch in enumerate(r):
                if ch not in TRANSPARENT and ch not in legend:
                    errs.append(f"layers[{n}] linha {i}, coluna {j}: caractere {ch!r} fora da legenda")
    side = data.get("side")
    if side not in (None, "L", "R"):
        errs.append(f"side deve ser null, 'L' ou 'R' (veio {side!r})")
    return errs


def parse(data: dict[str, Any] | str | PixelMap) -> PixelMap:
    """Valida e devolve o PixelMap (aceita dict, string JSON ou PixelMap). Levanta PixelMapError."""
    if isinstance(data, PixelMap):
        data = data.model_dump()
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise PixelMapError([f"JSON inválido: {e}"]) from None
    errs = validate_map(data)
    if errs:
        raise PixelMapError(errs)
    d = dict(data)
    d["legend"] = {k: norm_color(v) for k, v in d["legend"].items()}
    d.setdefault("format", FORMAT)
    if isinstance(d.get("outline"), dict):
        o = dict(d["outline"])
        if "color" in o:
            o["color"] = norm_hex(o["color"])
        d["outline"] = o
    return PixelMap.model_validate(d)


# ---------------------------------------------------------------- render

def char_grid(pm: PixelMap) -> np.ndarray:
    """Grade (H, W) de caracteres; '' = transparente. Camadas por cima das linhas, recortadas no canvas."""
    g = np.full((pm.height, pm.width), "", dtype="<U1")
    for i, r in enumerate(pm.rows):
        for j, ch in enumerate(r):
            if ch not in TRANSPARENT:
                g[i, j] = ch
    for lay in pm.layers:
        for i, r in enumerate(lay.rows):
            y = lay.y_top - i
            row = pm.height - 1 - y
            for j, ch in enumerate(r):
                x = lay.x0 + j
                if ch in TRANSPARENT or not (0 <= x < pm.width and 0 <= row < pm.height):
                    continue
                g[row, x] = ch
    return g


def render(pm: PixelMap | dict[str, Any], side: Literal["L", "R"] | None = None, outline: bool | None = None) -> Image.Image:
    """Desenha o mapa em RGBA (alfa binário, salvo cores "#rrggbbaa" da legenda). side=None usa pm.side;
    outline=None usa pm.outline.auto."""
    pm = parse(pm) if not isinstance(pm, PixelMap) else pm
    g = char_grid(pm)
    arr = np.zeros((pm.height, pm.width, 4), np.uint8)
    for ch, col in pm.legend.items():
        m = g == ch
        if m.any():
            rgb, a = color_alpha(col)
            arr[m, :3] = hex_to_rgb(rgb)
            arr[m, 3] = a
    if pm.outline.auto if outline is None else outline:
        ring = outline_ring(arr[..., 3] > 0)
        arr[ring, :3] = hex_to_rgb(pm.outline.color)
        arr[ring, 3] = 255
    img = to_image(arr)
    if (side or pm.side) == "L":
        img = shade_side(img, pm.shade_L, keep=pm.outline.color)
    return img


def shade_side(img: Image.Image, factor: float = 0.78, keep: str | None = OUTLINE_COLOR) -> Image.Image:
    """Lado de trás (_L): multiplica RGB por `factor` (Math.round, como o gen-parts.js), menos a cor `keep`."""
    arr = to_rgba(img)
    op = arr[..., 3] > 0
    keep_m = np.zeros_like(op)
    if keep:
        r, g, b = hex_to_rgb(keep)
        keep_m = (arr[..., 0] == r) & (arr[..., 1] == g) & (arr[..., 2] == b)
    m = op & ~keep_m
    arr[m, :3] = np.clip(js_round(arr[m, :3].astype(np.float64) * factor), 0, 255).astype(np.uint8)
    return to_image(arr)


# ---------------------------------------------------------------- imagem -> mapa

def from_image(img: Image.Image, legend_hint: dict[str, str] | None = None, outline_color: str = OUTLINE_COLOR,
               alpha_threshold: int = 128) -> PixelMap:
    """Converte um PNG em pixel map editável. Cada cor distinta recebe um caractere em ordem estável
    (primeira aparição, de cima para baixo e da esquerda para a direita). `legend_hint` ({char: hex})
    reaproveita os caracteres de uma legenda existente. O contorno já está no desenho: outline.auto = False."""
    arr = to_rgba(img)
    h, w = arr.shape[:2]
    op = arr[..., 3] >= alpha_threshold
    by_hex: dict[str, str] = {}
    for ch, col in (legend_hint or {}).items():
        by_hex.setdefault(norm_color(col), ch)
    used = set(by_hex.values())
    oc = norm_hex(outline_color)
    # 'k' fica reservado para o contorno (se a legenda dada não o usar)
    if oc not in by_hex and "k" not in used:
        by_hex[oc] = "k"
        used.add("k")
    pool = [c for c in dict.fromkeys(CHAR_POOL) if c not in used and c not in TRANSPARENT]
    rows: list[str] = []
    legend: dict[str, str] = {}
    for y in range(h):
        line = []
        for x in range(w):
            if not op[y, x]:
                line.append(".")
                continue
            hx = rgb_to_hex(arr[y, x, :3])
            if arr[y, x, 3] < 255:                         # alfa parcial (água): entra na cor da legenda
                hx = f"{hx}{int(arr[y, x, 3]):02x}"
            ch = by_hex.get(hx)
            if ch is None:
                if not pool:
                    raise PixelMapError([f"cores demais para um pixel map ({len(by_hex) + 1}+)"])
                ch = pool.pop(0)
                by_hex[hx] = ch
            legend[ch] = hx
            line.append(ch)
        rows.append("".join(line))
    if not legend:  # imagem vazia: legenda mínima válida
        legend = {"k": oc}
    return PixelMap(width=w, height=h, legend=legend, rows=rows, outline=OutlineCfg(auto=False, color=oc))


# ---------------------------------------------------------------- utilidades

def flatten(pm: PixelMap) -> PixelMap:
    """Mapa equivalente só com `rows` (camadas aplicadas)."""
    g = char_grid(pm)
    rows = ["".join(c or "." for c in r) for r in g]
    return pm.model_copy(update={"rows": rows, "layers": []})


def mirror_map(pm: PixelMap, axis: Literal["x", "y"] = "x") -> PixelMap:
    """Espelha horizontalmente (x) ou verticalmente (y). Camadas são achatadas antes."""
    f = flatten(pm)
    rows = [r[::-1] for r in f.rows] if axis == "x" else list(reversed(f.rows))
    return f.model_copy(update={"rows": rows})


def mirror_image(img: Image.Image, axis: Literal["x", "y"] = "x") -> Image.Image:
    return img.transpose(Image.Transpose.FLIP_LEFT_RIGHT if axis == "x" else Image.Transpose.FLIP_TOP_BOTTOM)
