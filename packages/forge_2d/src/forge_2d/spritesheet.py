"""Folhas de sprites: grade, detecção automática e descritores JSON (Aseprite / TexturePacker).

Usado pelo importador de folhas (`forge_2d.importers`), pelo pipeline `2d.slice` e pelo editor de recorte da UI.
Retângulos são (x, y, w, h) em pixels da imagem, origem no topo-esquerdo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

Rect = tuple[int, int, int, int]


class GridSpec(BaseModel):
    """Grade regular. Informe `columns`/`rows` (o tamanho da célula sai da imagem) e/ou `cell_width`/`cell_height`
    (as colunas/linhas saem da imagem). `margin_*` é a borda da imagem até a 1ª célula; `spacing_*` o vão entre células."""

    model_config = ConfigDict(extra="forbid")

    columns: int | None = Field(None, ge=1, le=1024)
    rows: int | None = Field(None, ge=1, le=1024)
    cell_width: int | None = Field(None, ge=1, le=16384)
    cell_height: int | None = Field(None, ge=1, le=16384)
    margin_x: int = Field(0, ge=0)
    margin_y: int = Field(0, ge=0)
    spacing_x: int = Field(0, ge=0)
    spacing_y: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _need_something(self) -> "GridSpec":
        if not (self.columns or self.cell_width):
            raise ValueError("a grade precisa de `columns` ou `cell_width`")
        if not (self.rows or self.cell_height):
            raise ValueError("a grade precisa de `rows` ou `cell_height`")
        return self


def resolve_grid(size: tuple[int, int], g: GridSpec) -> tuple[int, int, int, int]:
    """(colunas, linhas, largura da célula, altura da célula) para uma imagem do tamanho dado."""
    W, H = size

    def axis(total: int, count: int | None, cell: int | None, margin: int, spacing: int) -> tuple[int, int]:
        avail = total - margin
        if cell and count is None:
            count = max(1, (avail + spacing) // (cell + spacing))
        elif count and not cell:
            cell = max(1, (avail - (count - 1) * spacing) // count)
        assert count and cell
        return count, cell

    cols, cw = axis(W, g.columns, g.cell_width, g.margin_x, g.spacing_x)
    rows, ch = axis(H, g.rows, g.cell_height, g.margin_y, g.spacing_y)
    return cols, rows, cw, ch


def grid_cells(size: tuple[int, int], g: GridSpec) -> list[Rect]:
    """Células da grade, linha a linha. Células que passam da borda da imagem são descartadas."""
    W, H = size
    cols, rows, cw, ch = resolve_grid(size, g)
    out = []
    for r in range(rows):
        for c in range(cols):
            x = g.margin_x + c * (cw + g.spacing_x)
            y = g.margin_y + r * (ch + g.spacing_y)
            if x + cw <= W and y + ch <= H:
                out.append((x, y, cw, ch))
    return out


# ---------------------------------------------------------------- conteúdo

def content_mask(img: Image.Image, tolerance: int = 8) -> np.ndarray:
    """True onde há conteúdo: alfa > 0, ou, em imagens opacas, cor diferente da cor de fundo (a dos cantos)."""
    rgba = np.asarray(img.convert("RGBA"))
    a = rgba[..., 3]
    if (a < 255).any():
        return a > 0
    corners = np.array([rgba[0, 0, :3], rgba[0, -1, :3], rgba[-1, 0, :3], rgba[-1, -1, :3]], np.int16)
    vals, counts = np.unique(corners, axis=0, return_counts=True)
    bg = vals[counts.argmax()]
    return (np.abs(rgba[..., :3].astype(np.int16) - bg).max(axis=-1) > tolerance)


def is_empty(mask: np.ndarray, rect: Rect) -> bool:
    x, y, w, h = rect
    return not mask[y:y + h, x:x + w].any()


def content_bbox(mask: np.ndarray, rect: Rect) -> Rect | None:
    x, y, w, h = rect
    sub = mask[y:y + h, x:x + w]
    ys, xs = np.nonzero(sub)
    if len(xs) == 0:
        return None
    return x + int(xs.min()), y + int(ys.min()), int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1


# ---------------------------------------------------------------- detecção

def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Trechos [início, fim) consecutivos onde flags é True."""
    out, start = [], None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def _boundary_ratio(arr: np.ndarray, period: int, axis: int) -> float:
    """Contraste nas emendas de período `period` / contraste médio das outras emendas (>1 = a grade existe)."""
    a = arr.astype(np.float32)
    d = np.abs(np.diff(a, axis=axis)).mean(axis=tuple(i for i in range(a.ndim) if i != axis))  # diferença entre linhas/colunas vizinhas
    idx = np.arange(len(d))
    cut = (idx + 1) % period == 0
    if not cut.any() or cut.all():
        return 0.0
    other = d[~cut].mean() + 1e-3
    return float(d[cut].mean() / other)


def detect_grid(img: Image.Image) -> dict[str, Any]:
    """Sugere uma grade para a folha. Retorna {"grid": GridSpec dict | None, "method", "confidence", "candidates"}.

    1. Se os sprites estão separados por vãos vazios (linhas/colunas sem conteúdo), a grade sai dos trechos de conteúdo.
    2. Senão (tilesets contínuos), testa os tamanhos de célula que dividem a imagem e escolhe o de maior contraste
       nas emendas entre células."""
    W, H = img.size
    mask = content_mask(img)
    if not mask.any():
        return {"grid": None, "method": "empty", "confidence": 0.0, "candidates": []}
    col_runs = _runs(mask.any(axis=0))
    row_runs = _runs(mask.any(axis=1))

    def period(runs: list[tuple[int, int]]) -> tuple[int, int, int]:
        """(período, margem, vão) dos trechos."""
        if len(runs) < 2:
            return 0, 0, 0
        starts = np.array([r[0] for r in runs])
        widths = np.array([r[1] - r[0] for r in runs])
        step = int(np.median(np.diff(starts)))
        if step <= 0 or np.abs(np.diff(starts) - step).max() > max(2, step // 8):
            return 0, 0, 0
        cell = int(widths.max())
        return step, int(starts[0]), max(0, step - cell)

    px, mx, sx = period(col_runs)
    py, my, sy = period(row_runs)
    if px or py:
        cols = len(col_runs) if px else 1
        rows = len(row_runs) if py else 1
        cw = (px - sx) if px else W
        ch = (py - sy) if py else H
        # sem vãos entre sprites encostados: a célula é o período inteiro
        if px and sx == 0:
            mx = 0
        if py and sy == 0:
            my = 0
        g = {"columns": cols, "rows": rows, "cell_width": cw, "cell_height": ch, "margin_x": mx if sx else 0,
             "margin_y": my if sy else 0, "spacing_x": sx, "spacing_y": sy}
        # vãos só ao redor do conteúdo (sprites menores que a célula): usa o período como célula
        if px and sx > 0 and mx < sx:
            g.update(cell_width=px, margin_x=0, spacing_x=0)
        if py and sy > 0 and my < sy:
            g.update(cell_height=py, margin_y=0, spacing_y=0)
        return {"grid": g, "method": "gaps", "confidence": 0.8, "candidates": [g]}

    rgba = np.asarray(img.convert("RGBA"))
    cands = []
    divx = [d for d in range(4, W) if W % d == 0]
    divy = [d for d in range(4, H) if H % d == 0]
    rx = {d: _boundary_ratio(rgba, d, 1) for d in divx}
    ry = {d: _boundary_ratio(rgba, d, 0) for d in divy}

    def best(r: dict[int, float], total: int) -> int:
        good = {d: v for d, v in r.items() if v > 1.5}
        if not good:
            return total
        top = max(good.values())
        # entre os que chegam perto do melhor, o menor período que ainda é múltiplo dos demais
        close = sorted(d for d, v in good.items() if v >= 0.85 * top)
        return close[0]

    cw, ch = best(rx, W), best(ry, H)
    g = {"columns": W // cw, "rows": H // ch, "cell_width": cw, "cell_height": ch, "margin_x": 0, "margin_y": 0,
         "spacing_x": 0, "spacing_y": 0}
    conf = float(min(1.0, (rx.get(cw, 0) + ry.get(ch, 0)) / 6)) if (cw != W or ch != H) else 0.1
    cands.append(g)
    for sz in (8, 16, 24, 32, 48, 64, 96, 128):
        if W % sz == 0 and H % sz == 0 and (sz != cw or sz != ch):
            cands.append({"columns": W // sz, "rows": H // sz, "cell_width": sz, "cell_height": sz, "margin_x": 0,
                          "margin_y": 0, "spacing_x": 0, "spacing_y": 0})
    return {"grid": g, "method": "contrast", "confidence": round(conf, 2), "candidates": cands[:6]}


def detect_sprites(img: Image.Image, min_area: int = 4, merge_gap: int = 0) -> list[Rect]:
    """Retângulos das ilhas de conteúdo (componentes conexos de 8 vizinhos), em ordem de leitura.
    `merge_gap` junta ilhas separadas por até N pixels (ex.: um olho solto no rosto)."""
    from scipy import ndimage

    mask = content_mask(img)
    work = ndimage.binary_dilation(mask, structure=np.ones((3, 3), bool), iterations=merge_gap) if merge_gap > 0 else mask
    labels, n = ndimage.label(work, structure=np.ones((3, 3), bool))
    rects: list[Rect] = []
    for sl in ndimage.find_objects(labels):
        if sl is None:
            continue
        ys, xs = sl
        x, y, w, h = xs.start, ys.start, xs.stop - xs.start, ys.stop - ys.start
        if merge_gap > 0:  # encolhe de volta ao conteúdo real
            bb = content_bbox(mask, (x, y, w, h))
            if bb is None:
                continue
            x, y, w, h = bb
        if int(mask[y:y + h, x:x + w].sum()) >= min_area:
            rects.append((int(x), int(y), int(w), int(h)))
    return reading_order(rects)


def reading_order(rects: list[Rect]) -> list[Rect]:
    """Ordena em linhas (retângulos que se sobrepõem na vertical formam uma linha), da esquerda para a direita."""
    rest = sorted(rects, key=lambda r: (r[1], r[0]))
    rows: list[list[Rect]] = []
    for r in rest:
        for row in rows:
            top = min(q[1] for q in row)
            bot = max(q[1] + q[3] for q in row)
            if r[1] < bot and r[1] + r[3] > top and (r[1] + r[3] / 2) < bot + (bot - top) * 0.25:
                row.append(r)
                break
        else:
            rows.append([r])
    return [r for row in sorted(rows, key=lambda row: min(q[1] for q in row)) for r in sorted(row, key=lambda q: q[0])]


# ---------------------------------------------------------------- descritores JSON

@dataclass
class DescriptorFrame:
    name: str
    rect: Rect                    # na imagem da folha
    offset: tuple[int, int] = (0, 0)   # onde o recorte cai no quadro original (frames aparados)
    source_size: tuple[int, int] | None = None
    rotated: bool = False
    duration: int | None = None


@dataclass
class Descriptor:
    frames: list[DescriptorFrame] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    image: str | None = None
    app: str = ""


def _rect(d: dict[str, Any]) -> Rect:
    return int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"])


def parse_descriptor(data: dict[str, Any]) -> Descriptor:
    """Lê o JSON que acompanha uma folha: Aseprite (frames em lista ou hash + meta.frameTags) ou TexturePacker (hash/array).
    Levanta ValueError se não parecer nenhum dos dois."""
    frames = data.get("frames")
    if frames is None:
        raise ValueError("JSON sem 'frames': não é um descritor de folha de sprites")
    meta = data.get("meta") or {}
    items: list[tuple[str, dict[str, Any]]]
    if isinstance(frames, dict):
        items = [(str(k), v) for k, v in frames.items()]
    elif isinstance(frames, list):
        items = [(str(v.get("filename", f"frame {i}")), v) for i, v in enumerate(frames)]
    else:
        raise ValueError("'frames' deve ser lista ou objeto")
    out = Descriptor(image=meta.get("image"), app=str(meta.get("app", "")))
    for name, f in items:
        if "frame" not in f:
            raise ValueError(f"frame sem retângulo: {name}")
        src = f.get("sourceSize")
        sprite = f.get("spriteSourceSize") or {}
        out.frames.append(DescriptorFrame(
            name=name, rect=_rect(f["frame"]),
            offset=(int(sprite.get("x", 0)), int(sprite.get("y", 0))) if f.get("trimmed") else (0, 0),
            source_size=(int(src["w"]), int(src["h"])) if src else None,
            rotated=bool(f.get("rotated")), duration=int(f["duration"]) if "duration" in f else None))
    for t in meta.get("frameTags") or []:
        out.tags.append({"name": str(t["name"]), "from": int(t["from"]), "to": int(t["to"]),
                         "direction": str(t.get("direction", "forward")).replace("-", "")})
    return out


def descriptor_frames(sheet: Image.Image, desc: Descriptor) -> list[Image.Image]:
    """Recorta os frames da folha e devolve cada um no tamanho original (desfaz o aparo e a rotação)."""
    sheet = sheet.convert("RGBA")
    imgs = []
    for f in desc.frames:
        x, y, w, h = f.rect
        if f.rotated:   # TexturePacker grava girado 90° (sentido horário): w/h são os da folha
            crop = sheet.crop((x, y, x + h, y + w)).transpose(Image.Transpose.ROTATE_90)
        else:
            crop = sheet.crop((x, y, x + w, y + h))
        if f.source_size and f.source_size != crop.size:
            canvas = Image.new("RGBA", f.source_size)
            canvas.paste(crop, f.offset)
            crop = canvas
        imgs.append(crop)
    return imgs


def natural_key(s: str) -> list[Any]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def crop_frames(img: Image.Image, rects: list[Rect]) -> list[Image.Image]:
    img = img.convert("RGBA")
    return [img.crop((x, y, x + w, y + h)) for x, y, w, h in rects]


def uniform_canvas(frames: list[Image.Image], anchor: str = "center") -> list[Image.Image]:
    """Põe frames de tamanhos diferentes num canvas comum (centro, ou pés alinhados em 'bottom')."""
    w = max(f.width for f in frames)
    h = max(f.height for f in frames)
    out = []
    for f in frames:
        c = Image.new("RGBA", (w, h))
        x = (w - f.width) // 2
        y = h - f.height if anchor == "bottom" else (h - f.height) // 2
        c.paste(f, (x, y))
        out.append(c)
    return out


def overlay(img: Image.Image, rects: list[Rect], selected: set[int] | None = None, scale: int = 1) -> Image.Image:
    """Imagem com os retângulos desenhados (prévia/depuração)."""
    from PIL import ImageDraw

    base = img.convert("RGBA")
    if scale > 1:
        base = base.resize((base.width * scale, base.height * scale), Image.Resampling.NEAREST)
    d = ImageDraw.Draw(base)
    for i, (x, y, w, h) in enumerate(rects):
        on = selected is None or i in selected
        d.rectangle((x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1),
                    outline=(255, 64, 64, 255) if on else (128, 128, 128, 160))
    return base


__all__ = ["GridSpec", "Rect", "resolve_grid", "grid_cells", "content_mask", "is_empty", "content_bbox", "detect_grid",
           "detect_sprites", "reading_order", "Descriptor", "DescriptorFrame", "parse_descriptor", "descriptor_frames",
           "natural_key", "crop_frames", "uniform_canvas", "overlay"]
