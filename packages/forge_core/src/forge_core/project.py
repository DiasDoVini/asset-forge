"""Projeto = pasta com project.yaml (estilo global + blocos por modalidade), refs/ e assets/.

Layout:
    <projeto>/project.yaml
    <projeto>/refs/            imagens de referência de estilo
    <projeto>/assets/<slug>/vNNN/asset.json + arquivos
    <projeto>/.forge/          índice SQLite e dados internos (regeneráveis)
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

PROJECT_FILE = "project.yaml"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 48) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "item"


class LoraRef(BaseModel):
    name: str
    strength: float = 1.0


class SoulRef(BaseModel):
    """Creator Soul anexada ao projeto."""

    id: str
    strength: float = Field(0.8, ge=0.0, le=1.0, description="0 = só referências; 1 = estilo da soul domina")
    enabled: bool = True
    modalities: list[str] = Field(default_factory=list, description="vazio = todas")


class Style(BaseModel):
    """Estilo compartilhado por todas as modalidades."""

    model_config = ConfigDict(extra="allow")

    prompt: str = ""
    negative: str = ""
    system_prompt: str = ""
    refs: list[str] = Field(default_factory=list, description="caminhos relativos a refs/")
    loras: list[LoraRef] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list, description="cores hex, ex. #1a1c2c")


class Category(BaseModel):
    id: str
    name: str
    color: str = "#888888"


DEFAULT_CATEGORIES = [
    Category(id="character", name="Personagens", color="#60a5fa"),
    Category(id="enemy", name="Inimigos", color="#f87171"),
    Category(id="item", name="Itens", color="#fbbf24"),
    Category(id="weapon", name="Armas", color="#fb923c"),
    Category(id="environment", name="Cenário", color="#34d399"),
    Category(id="ui", name="UI", color="#c084fc"),
    Category(id="effect", name="Efeitos", color="#22d3ee"),
    Category(id="other", name="Outros", color="#a3a3a3"),
]


def normalize_folder(path: str | None) -> str:
    """'  Personagens//Cavaleiro/ ' -> 'Personagens/Cavaleiro'. Vazio = raiz. Sem '..'."""
    if not path:
        return ""
    parts = [p.strip() for p in str(path).replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts)


class ProjectConfig(BaseModel):
    """project.yaml. Chaves extras de topo ("2d", "3d", ...) são blocos de modalidade (dict livre)."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    style: Style = Field(default_factory=Style)
    # organização: pastas criadas (inclui vazias; as com assets aparecem sozinhas) e categorias
    folders: list[str] = Field(default_factory=list)
    categories: list[Category] = Field(default_factory=lambda: [c.model_copy() for c in DEFAULT_CATEGORIES])
    # modo +18: só o usuário liga (UI/CLI, com confirmação de maioridade). Ver forge_core.content.
    nsfw: bool = False
    nsfw_confirmed_at: datetime | None = None
    souls: list[SoulRef] = Field(default_factory=list)

    def modality(self, name: str) -> dict[str, Any]:
        block = (self.model_extra or {}).get(name)
        return dict(block) if isinstance(block, dict) else {}

    def set_modality(self, name: str, block: dict[str, Any]) -> None:
        setattr(self, name, dict(block))


class Project:
    def __init__(self, root: Path, config: ProjectConfig):
        self.root = Path(root)
        self.config = config
        self._store = None

    # ---- ciclo de vida -------------------------------------------------
    @classmethod
    def create(cls, root: Path, name: str | None = None, **fields: Any) -> "Project":
        root = Path(root)
        if (root / PROJECT_FILE).exists():
            raise FileExistsError(f"projeto já existe: {root}")
        root.mkdir(parents=True, exist_ok=True)
        config = ProjectConfig(name=name or root.name, **fields)
        project = cls(root, config)
        project.refs_dir.mkdir(exist_ok=True)
        project.assets_dir.mkdir(exist_ok=True)
        project.save()
        return project

    @classmethod
    def load(cls, root: Path) -> "Project":
        root = Path(root)
        path = root / PROJECT_FILE
        if not path.exists():
            raise FileNotFoundError(f"project.yaml não encontrado em {root}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(root, ProjectConfig.model_validate(data))

    def save(self) -> None:
        data = self.config.model_dump(mode="json", exclude_none=True)
        tmp = self.root / (PROJECT_FILE + ".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(self.root / PROJECT_FILE)

    # ---- caminhos ------------------------------------------------------
    @property
    def slug(self) -> str:
        return self.root.name

    @property
    def refs_dir(self) -> Path:
        return self.root / "refs"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def forge_dir(self) -> Path:
        d = self.root / ".forge"
        d.mkdir(exist_ok=True)
        return d

    @property
    def store(self):
        if self._store is None:
            from .assets import AssetStore

            self._store = AssetStore(self)
        return self._store

    # ---- estilo --------------------------------------------------------
    def style_for(self, modality: str) -> dict[str, Any]:
        """Estilo global mesclado com o bloco da modalidade (bloco vence). refs viram caminhos absolutos."""
        merged: dict[str, Any] = self.config.style.model_dump(mode="json")
        merged.update(self.config.modality(modality))
        merged["refs"] = [str(self.refs_dir / r) for r in merged.get("refs", []) if (self.refs_dir / r).exists()]
        if self.config.souls:
            _merge_souls(self, merged, modality)
        return merged

    # ---- creator souls -------------------------------------------------
    def attach_soul(self, soul_id: str, strength: float = 0.8, modalities: list[str] | None = None) -> None:
        from .souls import get_library

        soul = get_library().get(soul_id)
        if soul.manifest.nsfw and not self.config.nsfw:
            raise PermissionError("esta Soul é +18: habilite o modo +18 no projeto para anexá-la")
        refs = [s for s in self.config.souls if s.id != soul_id]
        refs.append(SoulRef(id=soul_id, strength=strength, modalities=modalities or []))
        self.config.souls = refs
        self.save()

    def update_soul(self, soul_id: str, **fields: Any) -> None:
        for i, s in enumerate(self.config.souls):
            if s.id == soul_id:
                self.config.souls[i] = s.model_copy(update={k: v for k, v in fields.items() if v is not None})
                self.save()
                return
        raise KeyError(f"soul não anexada: {soul_id}")

    def detach_soul(self, soul_id: str) -> None:
        self.config.souls = [s for s in self.config.souls if s.id != soul_id]
        self.save()

    def add_ref(self, src: Path, name: str | None = None) -> str:
        src = Path(src)
        self.refs_dir.mkdir(exist_ok=True)
        dest_name = name or src.name
        dest = self.refs_dir / dest_name
        i = 1
        while dest.exists():
            dest = self.refs_dir / f"{Path(dest_name).stem}_{i}{Path(dest_name).suffix}"
            i += 1
        shutil.copy2(src, dest)
        if dest.name not in self.config.style.refs:
            self.config.style.refs.append(dest.name)
            self.save()
        return dest.name

    def remove_ref(self, name: str) -> None:
        if name in self.config.style.refs:
            self.config.style.refs.remove(name)
            self.save()
        (self.refs_dir / name).unlink(missing_ok=True)

    # ---- edição genérica por caminho pontuado ------------------------
    def set_value(self, dotted: str, value: Any) -> None:
        """Ex.: set_value("style.prompt", "...") ou set_value("2d.pixel_size", 2)."""
        if dotted.split(".")[0] in ("nsfw", "nsfw_confirmed_at"):
            raise PermissionError("o modo +18 só pode ser alterado pelo usuário (UI ou `forge project nsfw`)")
        data = self.config.model_dump(mode="json")
        keys = dotted.split(".")
        cur = data
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
            if not isinstance(cur, dict):
                raise ValueError(f"'{k}' não é um bloco em {dotted}")
        cur[keys[-1]] = value
        self.config = ProjectConfig.model_validate(data)
        self.save()

    def set_nsfw(self, enabled: bool, *, confirm_adult: bool = False) -> None:
        """Liga/desliga o modo +18. Ligar exige a confirmação explícita de maioridade do usuário."""
        if enabled and not confirm_adult:
            raise PermissionError("habilitar o modo +18 exige confirmar que você tem 18 anos ou mais")
        self.config.nsfw = bool(enabled)
        self.config.nsfw_confirmed_at = datetime.now(timezone.utc) if enabled else None
        self.save()

    def summary(self) -> dict[str, Any]:
        return {"slug": self.slug, "path": str(self.root), **self.config.model_dump(mode="json")}


class ProjectRegistry:
    """Projetos sob um diretório raiz (FORGE_PROJECTS_DIR)."""

    def __init__(self, base: Path):
        self.base = Path(base)

    def list(self) -> list[Project]:
        if not self.base.is_dir():
            return []
        out = []
        for d in sorted(self.base.iterdir()):
            if (d / PROJECT_FILE).exists():
                try:
                    out.append(Project.load(d))
                except Exception:  # projeto corrompido não derruba a listagem
                    continue
        return out

    def resolve(self, ref: str | Path) -> Project:
        """Aceita slug (sob base) ou caminho de pasta."""
        p = Path(ref)
        if (p / PROJECT_FILE).exists():
            return Project.load(p)
        candidate = self.base / str(ref)
        if (candidate / PROJECT_FILE).exists():
            return Project.load(candidate)
        raise FileNotFoundError(f"projeto não encontrado: {ref}")

    def create(self, slug: str, name: str | None = None, **fields: Any) -> Project:
        return Project.create(self.base / slugify(slug), name=name or slug, **fields)


def _merge_souls(project: "Project", merged: dict[str, Any], modality: str) -> None:
    """Mescla o estilo das Souls anexadas no estilo efetivo (o projeto sempre vence no que definiu)."""
    from .souls import soul_context

    ctx = soul_context(project, modality)
    if not ctx:
        return
    prompts = [merged.get("prompt") or ""]
    souls_info = []
    for soul, strength in ctx:
        m = soul.manifest
        if strength >= 0.3 and m.style.prompt:
            prompts.append(m.style.prompt)
        if m.style.negative:
            merged["negative"] = ", ".join(x for x in [merged.get("negative"), m.style.negative] if x)
        if not merged.get("palette") and m.palette and strength >= 0.5:
            merged["palette"] = list(m.palette)
        if modality == "2d" and m.pixel.is_pixel_art and strength >= 0.5:
            px = dict(merged.get("pixel") or {})
            px.setdefault("size", max(16, min(256, m.pixel.art_size or 64)))
            px.setdefault("colors", max(4, min(64, m.pixel.colors or 16)))
            if m.pixel.outline:
                px.setdefault("outline", m.pixel.outline)
            merged["pixel"] = px
            merged.setdefault("pixel_art", True)
        souls_info.append({"id": soul.id, "strength": strength})
    merged["prompt"] = ", ".join(p for p in prompts if p)
    merged["souls"] = souls_info
