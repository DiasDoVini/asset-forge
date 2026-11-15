"""LoRAs de uma Soul: ficam em <soul>/loras/ (vão junto no .soul) e são expostas ao ComfyUI em
<models>/loras/souls/<soul>/ (hardlink no mesmo disco, cópia se não der).

Cada LoRA cobre categorias da Soul e guarda o formato de prompt/canvas com que foi treinada:
    {"name": "item", "file": "trr_item_v2.safetensors", "categories": ["Item", "Armor", "Acc", "Buff"],
     "base": "sd_xl_base_1.0.safetensors", "template": "trrsprite, item, {prompt}", "negative": "...",
     "cell_px": 32, "guide": false, "strength": 1.0, "sampler": "dpmpp_2m", "scheduler": "karras",
     "steps": 30, "cfg": 7.0, "chroma": "#ff00ff"}
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .library import Soul

DEFAULT_NEGATIVE = "blurry, smooth, gradient, photo, realistic, 3d, text, watermark, multiple objects"


def comfy_models_dir() -> Path:
    env = os.environ.get("FORGE_MODELS_DIR")
    p = Path(env) if env else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Comfy-Desktop" / "ComfyUI-Shared" / "models"
    return p.resolve()  # caminho real: mkdir através de junction falha no Windows


def comfy_name(soul: Soul, file: str) -> str:
    """Nome da LoRA como o ComfyUI a lista (subpasta com separador do SO)."""
    return os.path.join("souls", soul.id, file)


def _link_or_copy(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if dst.stat().st_size == src.stat().st_size:
            return "existente"
        dst.unlink()
    try:
        os.link(src.resolve(), dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "cópia"


def expose(soul: Soul, models_dir: Path | None = None) -> list[str]:
    """Garante que todas as LoRAs da Soul estejam visíveis ao ComfyUI."""
    root = (models_dir or comfy_models_dir()) / "loras"
    out = []
    for lr in soul.manifest.loras:
        src = soul.root.resolve() / "loras" / lr["file"]
        if src.exists():
            out.append(f"{lr['file']}: {_link_or_copy(src, root / 'souls' / soul.id / lr['file'])}")
    return out


def add_lora(soul: Soul, src: Path, *, name: str, categories: list[str], template: str, base: str,
             cell_px: int = 32, guide: bool = False, negative: str = DEFAULT_NEGATIVE, strength: float = 1.0,
             sampler: str = "dpmpp_2m", scheduler: str = "karras", steps: int = 30, cfg: float = 7.0,
             chroma: str = "#ff00ff", models_dir: Path | None = None, notes: str = "") -> dict[str, Any]:
    """Copia a LoRA para a Soul, registra no manifesto e expõe ao ComfyUI."""
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(src)
    dst = soul.root.resolve() / "loras" / src.name
    how = _link_or_copy(src, dst)
    entry = {"name": name, "file": src.name, "categories": categories, "base": base, "template": template,
             "negative": negative, "cell_px": cell_px, "guide": guide, "strength": strength, "sampler": sampler,
             "scheduler": scheduler, "steps": steps, "cfg": cfg, "chroma": chroma, "source": str(src), "notes": notes}
    soul.manifest.loras = [lr for lr in soul.manifest.loras if lr["name"] != name] + [entry]
    soul.save()
    expose(soul, models_dir)
    entry["installed"] = how
    return entry


def lora_for(soul: Soul, category: str | None) -> dict[str, Any] | None:
    """LoRA cuja lista de categorias cobre a categoria (prefixo, sem caixa)."""
    if not category:
        return None
    c = category.lower()
    for lr in soul.manifest.loras:
        if any(c.startswith(x.lower()) for x in lr.get("categories", [])):
            return lr
    return None


def import_captions(soul: Soul, captions: dict[str, str], categories: list[str] | None = None,
                    overwrite: bool = False) -> int:
    """Mescla legendas {nome do catálogo (ou stem do arquivo): descrição} no índice."""
    entries = soul.entries()
    by_key: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        for k in {e.get("name") or "", e.get("stem") or ""}:
            if k:
                by_key.setdefault(k.lower(), []).append(e)
    cats = [c.lower() for c in categories] if categories else None
    n = 0
    for key, text in captions.items():
        for e in by_key.get(key.lower(), []):
            if cats and not any(e["category"].lower().startswith(c) for c in cats):
                continue
            if e.get("caption") and not overwrite:
                continue
            e["caption"] = str(text).strip()[:240]
            n += 1
    soul.write_entries(entries)
    return n
