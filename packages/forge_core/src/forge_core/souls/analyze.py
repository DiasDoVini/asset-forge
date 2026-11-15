"""Análise do "DNA" visual de uma Soul: paleta, escala de pixel, tamanhos por categoria, contorno, capa e
descrição de estilo (modelo de visão local via Ollama, opcional)."""
from __future__ import annotations

import base64
import io
import random
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from .ingest import first_frame
from .library import Soul


def _hex(c) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in c[:3])


def kmeans_palette(pixels: np.ndarray, k: int, iters: int = 12, seed: int = 0) -> list[str]:
    """Paleta por k-means em RGB (pixels Nx3 uint8), ordenada por luminância."""
    if len(pixels) == 0:
        return []
    rng = np.random.default_rng(seed)
    px = pixels.astype(np.float32)
    uniq = np.unique(pixels, axis=0)
    k = min(k, len(uniq))
    cent = uniq[rng.choice(len(uniq), k, replace=False)].astype(np.float32)
    for _ in range(iters):
        d = ((px[:, None, :] - cent[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(k):
            m = lab == j
            if m.any():
                cent[j] = px[m].mean(0)
    counts = np.bincount(lab, minlength=k)
    cent = cent[counts > 0]
    lum = cent @ np.array([0.299, 0.587, 0.114], np.float32)
    return [_hex(c) for c in cent[np.argsort(lum)]]


def _runs(arr: np.ndarray) -> list[int]:
    """Comprimentos das sequências de pixels iguais (linhas e colunas) dentro do conteúdo opaco."""
    out: list[int] = []
    flat = arr.reshape(arr.shape[0], arr.shape[1], -1).astype(np.int32)
    key = (flat[..., 0] << 24) ^ (flat[..., 1] << 16) ^ (flat[..., 2] << 8) ^ flat[..., -1]
    for m in (key, key.T):
        for row in m:
            change = np.flatnonzero(np.diff(row)) + 1
            bounds = np.concatenate([[0], change, [len(row)]])
            out += np.diff(bounds).tolist()  # recorte já está no limite do conteúdo: pontas são sequências completas
    return out


def pixel_scale(arr: np.ndarray) -> int:
    """Texels por pixel de arte: maior k (4, 3, 2) que divide quase todas as sequências de pixels iguais."""
    runs = _runs(arr)
    if len(runs) < 8:
        return 1
    r = np.array(runs)
    for k in (4, 3, 2):
        if (r % k == 0).mean() >= 0.9:
            return k
    return 1


def _load_frame(folder: Path, e: dict[str, Any]) -> np.ndarray | None:
    try:
        with Image.open(folder / e["source"]) as im:
            fr = first_frame(im.convert("RGBA"), int(e.get("frames") or 1))
            a = np.asarray(fr)
    except Exception:
        return None
    m = a[..., 3] > 8
    if not m.any():
        return None
    ys, xs = np.nonzero(m)
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _edge_mask(alpha: np.ndarray) -> np.ndarray:
    op = alpha > 8
    pad = np.pad(op, 1)
    inner = pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:]
    return op & ~inner


def cover_sheet(soul: Soul, entries: list[dict[str, Any]], n: int = 16, cell: int = 160) -> Image.Image:
    """Prancha com refs de categorias variadas (as mais comuns primeiro)."""
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)
    cats = sorted(by_cat, key=lambda c: -len(by_cat[c]))
    rnd = random.Random(7)
    picks: list[dict[str, Any]] = []
    pools = {c: rnd.sample(by_cat[c], min(len(by_cat[c]), 12)) for c in cats[:8]}
    while len(picks) < n and any(pools.values()):
        for c in cats[:8]:
            if pools[c] and len(picks) < n:
                picks.append(pools[c].pop())
    cols = 4
    rows = (len(picks) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cell, rows * cell), (24, 26, 34, 255))
    for i, e in enumerate(picks):
        im = Image.open(soul.ref_path(e["id"])).convert("RGBA")
        k = min((cell - 16) / im.width, (cell - 16) / im.height)
        if k > 1:
            k = int(k)
            im = im.resize((im.width * k, im.height * k), Image.Resampling.NEAREST)
        else:
            im.thumbnail((cell - 16, cell - 16))
        x, y = (i % cols) * cell + (cell - im.width) // 2, (i // cols) * cell + (cell - im.height) // 2
        sheet.alpha_composite(im, (x, y))
    return sheet


def describe_style(sheet: Image.Image) -> str | None:
    """Um parágrafo de estilo escrito por um modelo de visão local (Ollama). None se indisponível."""
    import httpx

    from ..safety import VISION_URL, VisionGuardError, _vision_model

    try:
        model = _vision_model()
    except VisionGuardError:
        return None
    buf = io.BytesIO()
    sheet.convert("RGB").save(buf, format="PNG")
    prompt = ("These are sample sprites from one game's art collection. Describe their shared VISUAL STYLE as one "
              "compact comma-separated paragraph usable as a style prompt for an image generator: medium (pixel art "
              "or not), pixel density, outlines, shading technique, palette and saturation, perspective/view angle, "
              "level of detail, mood. Do not name the game or specific objects. Max 60 words.")
    try:
        r = httpx.post(f"{VISION_URL}/api/chat", timeout=300, json={
            "model": model, "stream": False, "think": False, "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(buf.getvalue()).decode()]}]})
        r.raise_for_status()
        text = r.json()["message"]["content"].strip().strip('"')
        return text or None
    except Exception:
        return None


def analyze(soul: Soul, folder: Path, entries: list[dict[str, Any]], *, describe: bool = True,
            on_progress: Callable[[float, str], None] | None = None, sample: int = 600) -> None:
    prog = on_progress or (lambda v, m: None)
    rnd = random.Random(1)
    smp = rnd.sample(entries, min(sample, len(entries)))
    scales, colors, sizes, hard_alpha, dark_edges = [], [], {}, [], []
    edge_cols: Counter = Counter()
    px_all, px_cat = [], {}
    for n, e in enumerate(smp):
        a = _load_frame(folder, e)
        if a is None:
            continue
        sc = pixel_scale(a)
        scales.append(sc)
        op = a[..., 3] > 8
        rgb = a[..., :3][op]
        colors.append(len(np.unique(rgb, axis=0)))
        hard_alpha.append(float(((a[..., 3] == 0) | (a[..., 3] == 255)).mean()))
        sizes.setdefault(e["category"], []).append(max(a.shape[:2]) / sc)
        edge = _edge_mask(a[..., 3])
        er = a[..., :3][edge]
        if len(er):
            lum = er @ np.array([0.299, 0.587, 0.114]) / 255
            dark_edges.append(float((lum < 0.25).mean()))
            edge_cols.update(_hex(c) for c in er[lum < 0.25][::4])
        take = rgb[:: max(1, len(rgb) // 300)]
        px_all.append(take)
        px_cat.setdefault(e["category"], []).append(take)
        if n % 50 == 0:
            prog(0.5 * n / len(smp), f"amostra {n}/{len(smp)}")
    pal = kmeans_palette(np.concatenate(px_all) if px_all else np.zeros((0, 3), np.uint8), 48)
    prog(0.6, "paletas")
    top = sorted(px_cat, key=lambda c: -len(px_cat[c]))[:8]
    palettes = {c: kmeans_palette(np.concatenate(px_cat[c]), 16) for c in top}
    scale = Counter(scales).most_common(1)[0][0] if scales else 1
    pixel_art = bool(scales) and bool(np.median(colors) <= 96 and np.median(hard_alpha) > 0.97)
    pct = lambda v, q: int(round(float(np.percentile(v, q))))  # noqa: E731
    all_sizes = [s for v in sizes.values() for s in v]
    px = soul.manifest.pixel
    px.is_pixel_art = pixel_art
    px.scale = int(scale)
    px.art_size = pct(all_sizes, 50) if all_sizes else 0
    px.colors = int(np.median(colors)) if colors else 0
    px.outline = edge_cols.most_common(1)[0][0] if dark_edges and np.median(dark_edges) > 0.5 and edge_cols else None
    px.sizes = {c: {"p10": pct(v, 10), "median": pct(v, 50), "p90": pct(v, 90), "n": len(v)}
                for c, v in sorted(sizes.items(), key=lambda x: -len(x[1])) if len(v) >= 3}
    soul.manifest.palette = pal
    soul.manifest.palettes = palettes
    prog(0.75, "capa")
    sheet = cover_sheet(soul, entries)
    sheet.save(soul.cover)
    if not soul.manifest.style.prompt or soul.manifest.source.get("style_auto", True):
        text = None
        if describe:
            prog(0.85, "descrevendo o estilo (modelo de visão local)")
            text = describe_style(sheet)
        base = []
        if pixel_art:
            base.append(f"pixel art, crisp {scale}x pixel scale, small sprites around {px.art_size}px, "
                        f"limited palette of about {px.colors} colors per sprite")
            if px.outline:
                base.append("dark 1-pixel outlines")
        soul.manifest.style.prompt = ", ".join(x for x in [*base, text or ""] if x)
        soul.manifest.source["style_auto"] = True
    soul.save()
    prog(1.0, "análise concluída")
