"""Configuração global via variáveis de ambiente (sem arquivo obrigatório)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    projects_dir: Path
    comfy_url: str
    host: str
    port: int
    web_dist: Path | None
    souls_root: Path | None = None

    @property
    def souls_dir(self) -> Path:
        """Biblioteca global de Creator Souls (padrão: irmã da pasta de projetos)."""
        return self.souls_root or self.projects_dir.parent / "souls"

    @property
    def server_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _default_web_dist() -> Path | None:
    # packages/forge_core/src/forge_core/config.py -> raiz do monorepo
    root = Path(__file__).resolve().parents[4]
    dist = root / "web" / "dist"
    return dist if dist.is_dir() else None


@lru_cache
def get_settings() -> Settings:
    web = os.environ.get("FORGE_WEB_DIST")
    return Settings(
        projects_dir=Path(os.environ.get("FORGE_PROJECTS_DIR", Path.home() / "AssetForge" / "projects")),
        comfy_url=os.environ.get("FORGE_COMFY_URL", "http://127.0.0.1:8188").rstrip("/"),
        host=os.environ.get("FORGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("FORGE_PORT", "8765")),
        web_dist=Path(web) if web else _default_web_dist(),
        souls_root=Path(os.environ["FORGE_SOULS_DIR"]) if os.environ.get("FORGE_SOULS_DIR") else None,
    )
