"""Conjuntos de poses (aceite §C, inimigos em N poses): folha exportável, silhueta de dano e folhas de revisão.

- pose_sheet: as poses lado a lado (strip) ou em grade, 1:1 (16 PPU), + metadados JSON com o retângulo
  de cada quadro, o pivô (px a partir do canto INFERIOR esquerdo, como o `.meta`, e normalizado) e a
  classe de pose — pronto para um importador fatiar no Unity.
- hit_silhouette: cópia chapada (#ffffff) dos pixels opacos, para o flash de dano.
- onion_skin: sobreposição das poses de uma classe (só para revisão; tem semitransparência).
- animation_strip: prévia ampliada com rótulos (nome, classe) e a linha do chão/pivô marcados.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .palette import hex_to_rgb
from .raster import to_image, to_rgba
from .validators import PoseClasses, pose_class_groups

SHEET_FORMAT = "asset-forge-posesheet/1"


def _same_size(arrs: Sequence[np.ndarray]) -> tuple[int, int]:
    sizes = {(a.shape[1], a.shape[0]) for a in arrs}
    if len(sizes) != 1:
        raise ValueError("as poses precisam do mesmo canvas: " + ", ".join(f"{w}x{h}" for w, h in sorted(sizes)))
    return sizes.pop()


def class_labels(n: int, classes: PoseClasses | None) -> list[str | None]:
    """Rótulo de classe de cada pose (lista paralela), a partir de lista ou dict (ver pose_class_groups)."""
    if classes is None:
        return [None] * n
    out: list[str | None] = [None] * n
    for label, idx in pose_class_groups(n, classes).items():
        for i in idx:
            out[i] = None if label.startswith("#") and not isinstance(classes, (list, tuple)) else label
    return out


def pose_sheet(images: Sequence[Image.Image | np.ndarray], columns: int | None = None, *,
               names: Sequence[str] | None = None, labels: Sequence[str] | None = None,
               classes: PoseClasses | None = None, pivot: tuple[float, float] | None = None,
               padding: int = 0, ppu: int = 16) -> tuple[Image.Image, dict[str, Any]]:
    """Folha 1:1 com as poses em ordem (columns=None = uma linha só, o strip).

    Metadados (`format` asset-forge-posesheet/1): frame_size, columns, rows, width, height, padding, ppu,
    origin "top-left" (dos retângulos), pivot {px: [x, y] a partir do canto inferior esquerdo do quadro,
    norm: [x/l, y/a] como o spritePivot do .meta}, classes {rótulo: [índices]} e frames [{index, name,
    label, class, rect: [x, y, l, a], pivot_sheet: [x, y] (topo-esquerda, na folha)}]."""
    arrs = [to_rgba(i) for i in images]
    if not arrs:
        raise ValueError("folha de poses vazia")
    w, h = _same_size(arrs)
    n = len(arrs)
    cols = max(1, min(int(columns or n), n))
    rows = (n + cols - 1) // cols
    pad = max(0, int(padding))
    W, H = cols * (w + pad) - pad, rows * (h + pad) - pad
    out = np.zeros((H, W, 4), np.uint8)
    px, py = pivot if pivot is not None else (w / 2, 0.0)
    cls = class_labels(n, classes)
    frames = []
    for i, a in enumerate(arrs):
        x, y = (i % cols) * (w + pad), (i // cols) * (h + pad)
        out[y:y + h, x:x + w] = a
        frames.append({"index": i, "name": (names[i] if names else f"{i}"),
                       "label": (labels[i] if labels else None), "class": cls[i],
                       "rect": [x, y, w, h], "pivot_sheet": [x + px, y + h - py]})
    meta = {"format": SHEET_FORMAT, "frame_size": [w, h], "count": n, "columns": cols, "rows": rows,
            "width": W, "height": H, "padding": pad, "ppu": ppu, "origin": "top-left",
            "pivot": {"px": [px, py], "norm": [round(px / w, 6), round(py / h, 6)], "origin": "bottom-left"},
            "classes": pose_class_groups(n, classes) if classes is not None else {}, "frames": frames}
    return to_image(out), meta


def write_sheet_json(meta: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def hit_silhouette(img: Image.Image | np.ndarray, color: str = "#ffffff") -> Image.Image:
    """Silhueta chapada para o flash de dano: todo pixel opaco (contorno incluso) vira `color`, alfa binário."""
    a = to_rgba(img)
    op = a[..., 3] > 0
    out = np.zeros_like(a)
    out[op, :3] = hex_to_rgb(color)
    out[op, 3] = 255
    return to_image(out)


def onion_skin(images: Sequence[Image.Image | np.ndarray], scale: int = 6, bg: str = "#1a1830",
               min_alpha: float = 0.35) -> Image.Image:
    """Sobreposição das poses (a última opaca, as anteriores cada vez mais transparentes), ampliada.
    Só para revisão de coerência (contorno "tremendo", cabeça mudando de tamanho, chão estável)."""
    arrs = [to_rgba(i) for i in images]
    if not arrs:
        raise ValueError("onion skin vazio")
    w, h = _same_size(arrs)
    acc = np.empty((h, w, 3), np.float64)
    acc[:] = hex_to_rgb(bg)
    n = len(arrs)
    for k, a in enumerate(arrs):
        alpha = 1.0 if k == n - 1 else min_alpha + (1.0 - min_alpha) * (k / max(1, n - 1)) * 0.6
        m = (a[..., 3] > 0)[..., None] * alpha
        acc = acc * (1 - m) + a[..., :3] * m
    big = np.repeat(np.repeat(np.floor(acc + 0.5).astype(np.uint8), scale, 0), scale, 1)
    return Image.fromarray(big, "RGB")


def animation_strip(images: Sequence[Image.Image | np.ndarray], scale: int = 6, *,
                    labels: Sequence[str] | None = None, pivot: tuple[float, float] | None = None,
                    bg: str = "#1a1830", pad: int = 8, label_h: int = 14, ground: bool = True) -> Image.Image:
    """Poses ampliadas lado a lado com rótulo, a linha do chão (y do pivô) e o pivô marcados."""
    arrs = [to_rgba(i) for i in images]
    w, h = _same_size(arrs)
    cw, ch = w * scale, h * scale
    W = pad + len(arrs) * (cw + pad)
    H = pad + ch + label_h + pad
    sheet = Image.new("RGB", (W, H), hex_to_rgb(bg))
    d = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    bgc = np.array(hex_to_rgb(bg), np.float64)
    px, py = pivot if pivot is not None else (w / 2, 0.0)
    for i, a in enumerate(arrs):
        x0 = pad + i * (cw + pad)
        big = np.repeat(np.repeat(a, scale, 0), scale, 1)
        al = big[..., 3:4].astype(np.float64) / 255.0
        rgb = np.floor(big[..., :3] * al + bgc * (1 - al) + 0.5).astype(np.uint8)
        sheet.paste(Image.fromarray(rgb, "RGB"), (x0, pad))
        if ground:
            gy = pad + (h - int(py)) * scale
            d.line([(x0, gy), (x0 + cw - 1, gy)], fill=(0x3c, 0xc8, 0x6e))
            cx = x0 + int(px * scale)
            d.line([(cx, gy - scale), (cx, gy + scale)], fill=hex_to_rgb("#a8324a"), width=2)
        if labels:
            d.text((x0, pad + ch + 2), str(labels[i])[:40], fill=(0xcd, 0xc9, 0xdf), font=font)
    return sheet
