"""Biblioteca global de Creator Souls (pacotes de referência reaproveitáveis entre projetos).

    <souls_dir>/<id>/soul.yaml     manifesto (estilo derivado, paleta, estatísticas de pixel, origem)
    <souls_dir>/<id>/index.json    entradas indexadas (nome, palavras, categoria, tamanho, quadros, cor, legenda)
    <souls_dir>/<id>/refs/<n>.png  miniaturas normalizadas
    <souls_dir>/<id>/cover.png     prancha de capa
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..project import slugify

if TYPE_CHECKING:
    from .index import SearchIndex

SOUL_FILE = "soul.yaml"
INDEX_FILE = "index.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _replace(tmp: Path, dst: Path, tries: int = 40) -> None:
    """os.replace com novas tentativas: no Windows falha se outro leitor está com o arquivo aberto naquele instante."""
    import time

    for i in range(tries):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.025)


class SoulStyle(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt: str = ""
    negative: str = ""
    system_prompt: str = ""


class SoulPixel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    is_pixel_art: bool = False
    scale: int = Field(1, description="texels por pixel de arte (2 = sprites desenhados em 2x)")
    art_size: int = Field(0, description="maior lado típico em pixels de arte")
    colors: int = Field(0, description="cores típicas por sprite")
    outline: str | None = Field(None, description="cor de contorno dominante (hex) se a coleção usa contorno")
    sizes: dict[str, dict[str, int]] = Field(default_factory=dict, description="por categoria: p10/median/p90 do maior lado")


class SoulManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    format: str = "asset-forge-soul/1"
    id: str
    name: str
    description: str = ""
    version: int = 1
    created: datetime = Field(default_factory=_now)
    updated: datetime = Field(default_factory=_now)
    status: Literal["empty", "building", "ready", "error"] = "empty"
    error: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    license: str = ""
    nsfw: bool = False
    tags: list[str] = Field(default_factory=list)
    style: SoulStyle = Field(default_factory=SoulStyle)
    pixel: SoulPixel = Field(default_factory=SoulPixel)
    palette: list[str] = Field(default_factory=list)
    palettes: dict[str, list[str]] = Field(default_factory=dict)
    categories: dict[str, int] = Field(default_factory=dict)
    pinned: list[str] = Field(default_factory=list)
    count: int = 0
    loras: list[dict[str, Any]] = Field(default_factory=list)


class Soul:
    def __init__(self, root: Path, manifest: SoulManifest):
        self.root = Path(root)
        self.manifest = manifest
        self._index: "SearchIndex | None" = None
        self._lock = threading.RLock()

    # ---- arquivos ------------------------------------------------------
    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def refs_dir(self) -> Path:
        return self.root / "refs"

    @property
    def cover(self) -> Path:
        return self.root / "cover.png"

    def ref_path(self, ref_id: str) -> Path:
        return self.refs_dir / f"{ref_id}.png"

    @classmethod
    def load(cls, root: Path) -> "Soul":
        data = yaml.safe_load((Path(root) / SOUL_FILE).read_text(encoding="utf-8")) or {}
        return cls(root, SoulManifest.model_validate(data))

    def save(self) -> None:
        self.manifest.updated = _now()
        self.root.mkdir(parents=True, exist_ok=True)
        data = self.manifest.model_dump(mode="json")
        tmp = self.root / (SOUL_FILE + ".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        _replace(tmp, self.root / SOUL_FILE)

    # ---- índice --------------------------------------------------------
    def entries(self) -> list[dict[str, Any]]:
        f = self.root / INDEX_FILE
        return json.loads(f.read_text(encoding="utf-8")).get("entries", []) if f.exists() else []

    def write_entries(self, entries: list[dict[str, Any]]) -> None:
        tmp = self.root / (INDEX_FILE + ".tmp")
        tmp.write_text(json.dumps({"format": 1, "entries": entries}, ensure_ascii=False), encoding="utf-8")
        _replace(tmp, self.root / INDEX_FILE)
        with self._lock:
            self._index = None

    @property
    def index(self) -> "SearchIndex":
        from .index import SearchIndex

        with self._lock:
            if self._index is None:
                self._index = SearchIndex(self.entries())
            return self._index

    def retrieve(self, query: str, k: int = 2, *, categories: list[str] | None = None,
                 exclude: set[str] | None = None) -> list[dict[str, Any]]:
        """Referências mais relevantes para o texto (fixadas primeiro quando casam com a busca)."""
        return self.index.search(query, k=k, categories=categories, exclude=exclude, pinned=set(self.manifest.pinned))

    def summary(self) -> dict[str, Any]:
        return {**self.manifest.model_dump(mode="json"), "path": str(self.root), "has_cover": self.cover.exists()}


class SoulLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)

    def list(self) -> list[Soul]:
        if not self.root.is_dir():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and (d / SOUL_FILE).exists():
                try:
                    out.append(Soul.load(d))
                except Exception:  # manifesto corrompido: ignora na listagem
                    continue
        return out

    def get(self, soul_id: str) -> Soul:
        d = self.root / soul_id
        if not (d / SOUL_FILE).exists():
            raise KeyError(f"soul não encontrada: {soul_id}")
        return Soul.load(d)

    def unique_id(self, name: str) -> str:
        base = slugify(name)
        sid, i = base, 2
        while (self.root / sid).exists():
            sid = f"{base}-{i}"
            i += 1
        return sid

    def create(self, name: str, *, description: str = "", source: dict[str, Any] | None = None,
               license: str = "", nsfw: bool = False, tags: list[str] | None = None) -> Soul:
        sid = self.unique_id(name)
        soul = Soul(self.root / sid, SoulManifest(id=sid, name=name, description=description, source=source or {},
                                                  license=license, nsfw=nsfw, tags=tags or []))
        soul.refs_dir.mkdir(parents=True, exist_ok=True)
        soul.save()
        return soul

    def delete(self, soul_id: str) -> Path:
        """Move para <souls>/.trash (recuperável)."""
        src = self.get(soul_id).root
        trash = self.root / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        dst = trash / f"{soul_id}-{_now().strftime('%Y%m%d%H%M%S')}"
        shutil.move(str(src), str(dst))
        return dst


def get_library() -> SoulLibrary:
    from ..config import get_settings

    return SoulLibrary(get_settings().souls_dir)
