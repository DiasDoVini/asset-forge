"""Composição de prompts: conteúdo + dica do tipo de asset + estilo global do projeto."""
from __future__ import annotations

from typing import Any, Literal

AssetType = Literal["object", "character", "creature", "tile", "icon", "ui", "background", "effect", "free"]

WHITE_BG = "isolated on a plain solid white background"

ASSET_HINTS: dict[str, str] = {
    "object": f"single 2D game item, centered, whole object visible, {WHITE_BG}",
    "character": f"single 2D game character, full body, front-facing three-quarter view, centered, {WHITE_BG}",
    "creature": f"single 2D game creature/enemy, full body, centered, {WHITE_BG}",
    "tile": "seamless tileable 2D game texture, flat top-down view, fills the entire frame, no borders",
    "icon": f"2D game UI icon, centered, bold readable silhouette, {WHITE_BG}",
    "ui": f"2D game UI element, clean edges, flat, {WHITE_BG}",
    "background": "2D game background scene, wide environment, no characters in the foreground",
    "effect": "2D game visual effect sprite, centered, on a plain solid black background",
    "free": "",
}

# tipos que por padrão não recebem remoção de fundo
OPAQUE_TYPES = {"tile", "background"}

PIXEL_HINT = "pixel art, crisp hard-edged pixels, limited color palette, no anti-aliasing, no blur"


def compose(content: str, style: dict[str, Any], asset_type: str = "object", pixel: bool = False,
            use_style: bool = True) -> str:
    parts = [content.strip().rstrip(".")]
    hint = ASSET_HINTS.get(asset_type, "")
    if hint:
        parts.append(hint)
    if use_style and style.get("prompt"):
        parts.append(style["prompt"].strip().rstrip("."))
    if pixel:
        parts.append(PIXEL_HINT)
    return ". ".join(p for p in parts if p) + "."


def to2d_instruction(extra: str, style: dict[str, Any], asset_type: str, has_refs: bool, pixel: bool) -> str:
    """Instrução para Image→2D (Qwen Edit): redesenhar a imagem 1 como asset de jogo."""
    s = "Redraw the main subject of image 1 as a 2D game asset"
    if style.get("prompt"):
        s += f" in this art style: {style['prompt'].strip().rstrip('.')}"
    if has_refs:
        s += ". Match the art style, line work, shading and color palette of the other reference images"
    if extra:
        s += f". {extra.strip().rstrip('.')}"
    hint = ASSET_HINTS.get(asset_type, "")
    if hint:
        s += f". {hint}"
    if pixel:
        s += f". {PIXEL_HINT}"
    return s + ". Keep the subject's shape, pose and identity."


def edit_instruction(instruction: str, style: dict[str, Any], keep_style: bool = True) -> str:
    s = instruction.strip().rstrip(".")
    if keep_style:
        s += ". Keep the same art style, framing and everything else unchanged"
    return s + "."
