"""Utilitários de imagem (Pillow/numpy) usados pelos pipelines 2D."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def has_alpha(img: Image.Image) -> bool:
    if img.mode not in ("RGBA", "LA", "PA") and "transparency" not in img.info:
        return False
    a = np.asarray(img.convert("RGBA"))[..., 3]
    return bool((a < 250).mean() > 0.005)


def flatten(img: Image.Image, bg: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """RGBA -> RGB sobre fundo sólido (modelos de difusão ignoram alpha)."""
    if img.mode == "RGB":
        return img
    rgba = img.convert("RGBA")
    base = Image.new("RGB", rgba.size, bg)
    base.paste(rgba, mask=rgba.split()[3])
    return base


def apply_mask(img: Image.Image, mask: Image.Image, hard: bool = False, threshold: int = 128) -> Image.Image:
    """Usa `mask` (branco = objeto) como canal alpha."""
    m = mask.convert("L")
    if m.size != img.size:
        m = m.resize(img.size, Image.Resampling.BILINEAR)
    if hard:
        m = m.point(lambda v: 255 if v >= threshold else 0)
    out = img.convert("RGBA")
    out.putalpha(m)
    return out


def trim(img: Image.Image, pad: int = 0) -> Image.Image:
    """Recorta no bounding box do alpha (com margem)."""
    if img.mode != "RGBA":
        return img
    bbox = img.split()[3].point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bbox:
        return img
    l, t, r, b = bbox
    return img.crop((max(0, l - pad), max(0, t - pad), min(img.width, r + pad), min(img.height, b + pad)))


def composite_region(original: Image.Image, edited: Image.Image, mask: Image.Image, feather: int = 4) -> Image.Image:
    """Mantém o original fora da máscara (branco = área editada)."""
    orig = original.convert("RGBA")
    ed = edited.convert("RGBA").resize(orig.size, Image.Resampling.LANCZOS)
    m = mask.convert("L").resize(orig.size, Image.Resampling.BILINEAR)
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    return Image.composite(ed, orig, m)


def resize_alpha(alpha: Image.Image, size: tuple[int, int]) -> Image.Image:
    return alpha.convert("L").resize(size, Image.Resampling.LANCZOS)


def preview(img: Image.Image, min_side: int = 256) -> Image.Image:
    """Versão ampliada (nearest) para sprites pequenos ficarem visíveis na UI."""
    if max(img.size) >= min_side:
        return img
    k = max(1, min_side // max(img.size))
    return img.resize((img.width * k, img.height * k), Image.Resampling.NEAREST)


def snap(v: int, step: int = 16) -> int:
    return max(step, int(round(v / step)) * step)
