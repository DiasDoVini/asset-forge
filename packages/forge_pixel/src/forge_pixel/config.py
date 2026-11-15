"""Leitura do bloco `pixel` do project.yaml: paleta travada e specs (presets + sobrescritas).

    pixel:
      palette: {core: [...], groups: {personagem/chest: {cores: [...]}, ...}}
      # ou: palette: team/art/paletas.json   (caminho relativo à raiz do projeto, ou absoluto)
      specs: {torso: {safe_area: [2, 0, 12, 16]}, sword: {...}}

Sem paleta declarada: PaletteSet só com a cor do contorno, marcada `fallback` (o cheque de paleta
é ignorado e a quantização usa o limite de cores da spec).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .spec import OUTLINE_COLOR, AssetSpec, PaletteSet, make_specs

if TYPE_CHECKING:
    from forge_core.project import Project


def pixel_block(project: "Project | None") -> dict[str, Any]:
    if project is None:
        return {}
    try:
        return project.config.modality("pixel")
    except AttributeError:
        return {}


def palette_from_block(block: dict[str, Any], root: Path | None = None) -> PaletteSet:
    pal = block.get("palette") or block.get("palette_file")
    if isinstance(pal, PaletteSet):
        return pal
    if isinstance(pal, str):
        p = Path(pal)
        if not p.is_absolute() and root is not None:
            p = root / p
        if not p.exists():
            raise FileNotFoundError(f"paleta não encontrada: {p}")
        pal = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(pal, dict) and (pal.get("core") or pal.get("groups")):
        return PaletteSet.from_dict(pal)
    return PaletteSet.only_outline(OUTLINE_COLOR)


def load_palette(project: "Project | None") -> PaletteSet:
    return palette_from_block(pixel_block(project), getattr(project, "root", None))


def load_specs(project: "Project | None") -> dict[str, AssetSpec]:
    return make_specs(pixel_block(project).get("specs") or {})
