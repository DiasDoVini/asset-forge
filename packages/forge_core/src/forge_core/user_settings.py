"""Preferências da instalação editáveis pela tela de Opções (o que não é variável de ambiente): ficam em
`<pasta dos projetos>/../settings.json`, ao lado de `projects/` e `souls/`. Variáveis de ambiente continuam ganhando.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .config import get_settings

_lock = threading.Lock()


def path() -> Path:
    return get_settings().projects_dir.parent / "settings.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get(section: str, key: str, default: Any = None) -> Any:
    return (load().get(section) or {}).get(key, default)


def update(section: str, **values: Any) -> dict[str, Any]:
    """Atualiza chaves de uma seção (None apaga a chave) e grava de forma atômica."""
    with _lock:
        data = load()
        sec = {**(data.get(section) or {})}
        for k, v in values.items():
            if v is None:
                sec.pop(k, None)
            else:
                sec[k] = v
        data[section] = sec
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        return sec
