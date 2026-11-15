"""Legendas opcionais (modelo de visão local via Ollama) para referências sem nome útil no catálogo.

Retomável: grava o índice a cada lote; entradas já legendadas são puladas.
"""
from __future__ import annotations

import base64
import io
from typing import Any, Callable

import httpx
from PIL import Image

from .library import Soul

PROMPT = ("Name what this single game sprite depicts in 3 to 8 plain English words (object/creature type, material, "
          "color, element). No sentence, no style words.")


def needs_caption(e: dict[str, Any]) -> bool:
    return not e.get("caption") and not e.get("name")


def caption_entries(soul: Soul, *, limit: int | None = None, only_missing: bool = True,
                    on_progress: Callable[[float, str], None] | None = None, batch_save: int = 50) -> int:
    from ..safety import VISION_URL, _vision_model

    model = _vision_model()
    entries = soul.entries()
    todo = [e for e in entries if (needs_caption(e) if only_missing else not e.get("caption"))]
    if limit:
        todo = todo[:limit]
    done = 0
    for e in todo:
        im = Image.open(soul.ref_path(e["id"])).convert("RGBA")
        bg = Image.new("RGBA", im.size, (128, 128, 128, 255))
        bg.alpha_composite(im)
        buf = io.BytesIO()
        bg.convert("RGB").save(buf, format="PNG")
        try:
            r = httpx.post(f"{VISION_URL}/api/chat", timeout=120, json={
                "model": model, "stream": False, "think": False, "options": {"temperature": 0, "num_predict": 30},
                "messages": [{"role": "user", "content": PROMPT, "images": [base64.b64encode(buf.getvalue()).decode()]}]})
            r.raise_for_status()
            e["caption"] = r.json()["message"]["content"].strip().strip(".").lower()[:120]
        except Exception:
            continue
        done += 1
        if done % batch_save == 0:
            soul.write_entries(entries)
        if on_progress:
            on_progress(done / max(1, len(todo)), f"legendas {done}/{len(todo)}")
    soul.write_entries(entries)
    return done
