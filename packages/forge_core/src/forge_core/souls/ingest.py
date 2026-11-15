"""Ingestão de uma pasta de referências numa Soul: varredura, catálogo, miniaturas e índice."""
from __future__ import annotations

import fnmatch
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from .library import Soul

IMAGE_EXTS = {".png", ".webp", ".gif", ".jpg", ".jpeg", ".bmp"}
THUMB = 256
Progress = Callable[[float, str], None]


# ---------------------------------------------------------------- catálogo

def find_catalog(folder: Path) -> Path | None:
    for name in ("CATALOG.md", "catalog.md", "Catalog.md", "catalog.csv", "CATALOG.csv"):
        if (folder / name).exists():
            return folder / name
    return None


def parse_catalog(path: Path) -> dict[str, dict[str, Any]]:
    """Tabelas markdown (| Asset | Name | Size | Frames | Colour |) ou CSV com cabeçalho → {asset: info}."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    out: dict[str, dict[str, Any]] = {}
    if path.suffix.lower() == ".csv":
        import csv
        import io

        for row in csv.DictReader(io.StringIO(text)):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            asset = row.get("asset") or row.get("file") or row.get("filename")
            if asset:
                out[Path(asset).stem] = _info(row, None)
        return out
    section, header = None, None
    for line in text.splitlines():
        if line.startswith("## "):
            section = re.sub(r"\s*\(\d+\)\s*$", "", line[3:]).strip()
            header = None
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("`").strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        asset = row.get("asset") or row.get("file")
        if asset:
            out[Path(asset).stem] = _info(row, section)
    return out


def _info(row: dict[str, str], section: str | None) -> dict[str, Any]:
    name = row.get("name", "")
    if name.lower() in ("none", "-", "?"):
        name = ""
    w = h = 0
    m = re.match(r"(\d+)\s*[x×]\s*(\d+)", row.get("size", ""))
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    frames = int(row["frames"]) if row.get("frames", "").isdigit() else 1
    return {"name": name, "w": w, "h": h, "frames": max(1, frames),
            "colour": row.get("colour") or row.get("color") or "", "section": section}


def category_of(stem: str) -> str:
    """'Item_4' → 'Item'; 'Acc_Back_12' → 'Acc_Back'; 'NPC_Head_Boss_3' → 'NPC_Head_Boss'; 'slime' → ''."""
    m = re.match(r"^(.*?)[_\-]?\d+$", stem)
    return (m.group(1) if m else "").strip("_-")


# ---------------------------------------------------------------- imagens

def first_frame(img: Image.Image, frames: int) -> Image.Image:
    """Strips verticais (padrão Terraria) ou horizontais: devolve o 1º quadro."""
    if frames <= 1:
        return img
    if img.height >= img.width and img.height % frames == 0:
        return img.crop((0, 0, img.width, img.height // frames))
    if img.width % frames == 0:
        return img.crop((0, 0, img.width // frames, img.height))
    return img


def make_thumb(img: Image.Image, size: int = THUMB) -> Image.Image | None:
    """Recorta no conteúdo; pixel art pequena é ampliada em nearest. None se a imagem estiver vazia."""
    img = img.convert("RGBA")
    a = np.asarray(img)[..., 3]
    if a.max() < 8:
        return None
    bbox = Image.fromarray((a > 8).astype(np.uint8) * 255).getbbox()
    img = img.crop(bbox)
    m = max(img.size)
    if m <= size // 2:
        k = max(1, size // m)
        return img.resize((img.width * k, img.height * k), Image.Resampling.NEAREST)
    if m > size:
        img = img.copy()
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
    return img


def scan(folder: Path, include: list[str] | None = None, exclude: list[str] | None = None,
         recursive: bool = True) -> list[Path]:
    it = folder.rglob("*") if recursive else folder.glob("*")
    out = []
    for p in it:
        if p.suffix.lower() not in IMAGE_EXTS or not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        if include and not any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat) for pat in include):
            continue
        if exclude and any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat) for pat in exclude):
            continue
        out.append(p)
    return sorted(out)


# ---------------------------------------------------------------- build

def build(soul: Soul, folder: str | Path, *, include: list[str] | None = None, exclude: list[str] | None = None,
          catalog: str | Path | None = None, recursive: bool = True, describe: bool = True,
          on_progress: Progress | None = None, workers: int = 8) -> Soul:
    """(Re)constrói a Soul a partir da pasta. Substitui refs e índice; preserva pinned/estilo editado manualmente."""
    from . import analyze

    folder = Path(folder)
    prog = on_progress or (lambda v, m: None)
    if not folder.is_dir():
        raise FileNotFoundError(f"pasta não encontrada: {folder}")
    soul.manifest.status, soul.manifest.error = "building", None
    soul.manifest.source = {"folder": str(folder), "include": include or [], "exclude": exclude or [],
                            "catalog": str(catalog) if catalog else None, "recursive": recursive}
    soul.save()
    try:
        cat_path = Path(catalog) if catalog else find_catalog(folder)
        cat = parse_catalog(cat_path) if cat_path and cat_path.exists() else {}
        if cat_path:
            soul.manifest.source["catalog"] = str(cat_path)
        files = scan(folder, include, exclude, recursive)
        if not files:
            raise ValueError("nenhuma imagem encontrada com esses filtros")
        prog(0.02, f"{len(files)} imagens")
        refs = soul.refs_dir
        if refs.exists():
            for old in refs.glob("*.png"):
                old.unlink()
        refs.mkdir(parents=True, exist_ok=True)

        def one(item: tuple[int, Path]) -> dict[str, Any] | None:
            i, p = item
            info = cat.get(p.stem, {})
            try:
                with Image.open(p) as im:
                    im.load()
                    W, H = im.size
                    frames = int(info.get("frames") or 1)
                    fr = first_frame(im.convert("RGBA"), frames)
            except Exception:
                return None
            th = make_thumb(fr)
            if th is None:
                return None
            rid = f"r{i:05d}"
            th.save(refs / f"{rid}.png")
            return {"id": rid, "source": p.relative_to(folder).as_posix(), "stem": p.stem,
                    "name": info.get("name") or "", "category": info.get("section") or category_of(p.stem) or p.parent.name,
                    "w": W, "h": H, "fw": fr.width, "fh": fr.height, "frames": frames,
                    "colour": info.get("colour") or "", "tags": [], "caption": ""}

        entries: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for n, e in enumerate(ex.map(one, enumerate(files))):
                if e is not None:
                    entries.append(e)
                if n % 200 == 0:
                    prog(0.02 + 0.68 * n / len(files), f"miniaturas {n}/{len(files)}")
        if not entries:
            raise ValueError("todas as imagens estavam vazias")
        soul.write_entries(entries)
        prog(0.72, "analisando estilo")
        analyze.analyze(soul, folder, entries, describe=describe, on_progress=lambda v, m: prog(0.72 + 0.26 * v, m))
        soul.manifest.count = len(entries)
        soul.manifest.categories = dict(sorted(_count(e["category"] for e in entries).items(), key=lambda x: -x[1]))
        soul.manifest.status = "ready"
        soul.manifest.version += 1
        soul.save()
        prog(1.0, "pronta")
        return soul
    except Exception as e:
        soul.manifest.status, soul.manifest.error = "error", str(e)
        soul.save()
        raise


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out
