"""Assets versionados: assets/<slug>/vNNN/asset.json + arquivos.

Uma versão é imutável depois de escrita (exceto metadados leves: name, tags, notes).
Referência textual: "slug" (última versão) ou "slug@v003" / "slug@3".
"""
from __future__ import annotations

import json
import mimetypes
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from pydantic import BaseModel, Field

from .project import slugify

if TYPE_CHECKING:
    from .project import Project

META_FILE = "asset.json"
ROLES = ("primary", "preview", "texture", "print", "part", "report", "mask", "source")
_REF_RE = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9-]*)(?:@v?(?P<ver>\d+))?$")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")
mimetypes.add_type("model/stl", ".stl")
mimetypes.add_type("model/obj", ".obj")
mimetypes.add_type("model/3mf", ".3mf")
mimetypes.add_type("model/ply", ".ply")
mimetypes.add_type("model/off", ".off")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/opus", ".opus")
mimetypes.add_type("text/x-glsl", ".glsl")
mimetypes.add_type("text/x-glsl", ".frag")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("video/quicktime", ".mov")


def guess_mime(path: str | Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


MESH_EXTS = {".glb", ".gltf", ".obj", ".stl", ".ply", ".3mf", ".off", ".fbx"}
AUDIO_EXTS = {".wav", ".flac", ".ogg", ".mp3", ".opus", ".aiff", ".aif"}
SHADER_EXTS = {".glsl", ".frag", ".gdshader"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}


_GENERIC_STEM = re.compile(
    r"^(image|img|picture|pic|photo|untitled|upload|download|file|output|out|render|result|screenshot|"
    r"screen ?shot.*|captura.*|imagem|sem t[ií]tulo|comfyui|model|mesh|scene|sprite|asset|frame|texture|"
    r"[0-9a-f]{8,}|\d+|img_\d+|dsc_?\d+|image[_ -]?\d+|comfyui_\d+_?)([_ -]?\(?\d+\)?)?$",
    re.IGNORECASE,
)


def import_slug_base(src: str | Path) -> str:
    """Nome base para um asset importado: o nome do arquivo, ou a pasta de origem quando o
    nome do arquivo é genérico ("image.png", "ComfyUI_00012_.png", "1696.png"...)."""
    p = Path(src)
    if not _GENERIC_STEM.match(p.stem.strip()):
        return p.stem
    for parent in (p.parent, p.parent.parent):
        name = parent.name
        if not name or _IGNORED_DIR.match(name):
            # versão de asset (…/knight/v003/image.png) aponta para o slug; outras pastas de sistema param
            if re.fullmatch(r"v\d+", name or "") and parent.parent.name:
                return parent.parent.name
            break
        if _GENERIC_STEM.match(name):  # id/uuid/número: sobe mais um nível
            continue
        return name
    return "import"


# pastas que não dizem nada sobre o conteúdo (temporárias, de sistema, de versão)
_IGNORED_DIR = re.compile(
    r"^(tmp.*|temp|te?mp_.*|uploads?|downloads?|desktop|documents?|documentos|pictures|imagens|images|"
    r"área de trabalho|area de trabalho|scratchpad|assets?|input|output|exports?|refs?|v\d+|\.forge|forge-.*)$",
    re.IGNORECASE,
)


def infer_modality_kind(path: str | Path) -> tuple[str, str]:
    """(modality, kind) padrão para um arquivo importado, pela extensão."""
    ext = Path(path).suffix.lower()
    if ext in MESH_EXTS:
        return "3d", "mesh"
    if ext in AUDIO_EXTS:
        return "audio", "audio"
    if ext in SHADER_EXTS:
        return "vfx", "shader"
    if ext in VIDEO_EXTS:
        return "video", "video"
    return "2d", "image"


ASSET_META_FILE = "_meta.json"


class AssetMeta(BaseModel):
    """Organização do asset (todas as versões): pasta e categoria."""

    folder: str = ""          # "Personagens/Cavaleiro"; "" = raiz
    category: str | None = None


class AssetFile(BaseModel):
    path: str  # relativo à pasta da versão
    role: str = "primary"
    mime: str = "application/octet-stream"


class AssetVersion(BaseModel):
    slug: str
    version: int
    modality: str
    kind: str = "image"
    name: str = ""
    pipeline: str | None = None
    prompt: str | None = None
    seed: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    parent: str | None = None
    workflow: str | None = None
    files: list[AssetFile] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ref(self) -> str:
        return f"{self.slug}@v{self.version:03d}"

    def primary(self) -> AssetFile | None:
        for f in self.files:
            if f.role == "primary":
                return f
        return self.files[0] if self.files else None

    def preview(self) -> AssetFile | None:
        for f in self.files:
            if f.role == "preview":
                return f
        p = self.primary()
        return p if p and p.mime.startswith("image/") else None


def parse_ref(ref: str) -> tuple[str, int | None]:
    m = _REF_RE.match(ref.strip())
    if not m:
        raise ValueError(f"referência de asset inválida: {ref!r} (use slug ou slug@v003)")
    ver = m.group("ver")
    return m.group("slug"), int(ver) if ver else None


class AssetStore:
    def __init__(self, project: "Project"):
        self.project = project
        self.root = project.assets_dir
        self._index = None

    @property
    def index(self):
        if self._index is None:
            from .index import AssetIndex

            self._index = AssetIndex(self.project.forge_dir / "index.db")
        return self._index

    # ---- leitura -------------------------------------------------------
    def slugs(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(d.name for d in self.root.iterdir() if d.is_dir() and not d.name.startswith("."))

    def version_numbers(self, slug: str) -> list[int]:
        d = self.root / slug
        if not d.is_dir():
            return []
        nums = []
        for v in d.iterdir():
            if v.is_dir() and v.name.startswith("v") and v.name[1:].isdigit() and (v / META_FILE).exists():
                nums.append(int(v.name[1:]))
        return sorted(nums)

    def version_dir(self, slug: str, version: int) -> Path:
        return self.root / slug / f"v{version:03d}"

    def get(self, ref: str) -> AssetVersion:
        slug, ver = parse_ref(ref)
        if ver is None:
            nums = self.version_numbers(slug)
            if not nums:
                raise FileNotFoundError(f"asset não encontrado: {slug}")
            ver = nums[-1]
        meta = self.version_dir(slug, ver) / META_FILE
        if not meta.exists():
            raise FileNotFoundError(f"versão não encontrada: {slug}@v{ver:03d}")
        return AssetVersion.model_validate_json(meta.read_text(encoding="utf-8"))

    def versions(self, slug: str) -> list[AssetVersion]:
        return [self.get(f"{slug}@v{n}") for n in self.version_numbers(slug)]

    def latest(self) -> list[AssetVersion]:
        out = []
        for slug in self.slugs():
            try:
                out.append(self.get(slug))
            except FileNotFoundError:
                continue
        return out

    def file_path(self, av: AssetVersion, f: AssetFile | str) -> Path:
        rel = f.path if isinstance(f, AssetFile) else f
        return self.version_dir(av.slug, av.version) / rel

    def files_of(self, av: AssetVersion, role: str | None = None) -> list[Path]:
        return [self.file_path(av, f) for f in av.files if role is None or f.role == role]

    def search(self, q: str = "", modality: str | None = None, kind: str | None = None, tag: str | None = None,
               base: str | None = None, folder: str | None = None, recursive: bool = False,
               category: str | None = None) -> list[dict]:
        """folder: "" = raiz (só assets sem pasta); None = qualquer pasta. recursive inclui subpastas."""
        return self.index.search(q=q, modality=modality, kind=kind, tag=tag, base=base, folder=folder,
                                 recursive=recursive, category=category)

    # ---- escrita -------------------------------------------------------
    def unique_slug(self, base: str) -> str:
        base = slugify(base)
        slug, i = base, 2
        while (self.root / slug).exists():
            slug = f"{base}-{i}"
            i += 1
        return slug

    def reserve_version(self, slug: str) -> tuple[int, Path]:
        """Cria a pasta da próxima versão de forma atômica (mkdir exclusivo)."""
        slug = slugify(slug)
        (self.root / slug).mkdir(parents=True, exist_ok=True)
        existing = [int(p.name[1:]) for p in (self.root / slug).iterdir() if p.is_dir() and p.name[1:].isdigit()]
        n = max(existing, default=0) + 1
        while True:
            d = self.version_dir(slug, n)
            try:
                d.mkdir()
                return n, d
            except FileExistsError:
                n += 1

    def discard_reserved(self, slug: str, version: int) -> None:
        d = self.version_dir(slug, version)
        if d.exists() and not (d / META_FILE).exists():
            shutil.rmtree(d, ignore_errors=True)
        parent = self.root / slug
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def write_meta(self, av: AssetVersion) -> AssetVersion:
        d = self.version_dir(av.slug, av.version)
        tmp = d / (META_FILE + ".tmp")
        tmp.write_text(av.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(d / META_FILE)
        self.index.upsert(av)
        return av

    def finalize(self, slug: str, version: int, files: Iterable[tuple[Path, str, str | None]], **meta: Any) -> AssetVersion:
        """Registra arquivos (caminho, role, mime) já presentes ou copiados para a pasta da versão."""
        d = self.version_dir(slug, version)
        entries: list[AssetFile] = []
        for path, role, mime in files:
            path = Path(path)
            if role not in ROLES:
                raise ValueError(f"role inválido: {role} (use {ROLES})")
            try:
                rel = path.resolve().relative_to(d.resolve())
            except ValueError:
                dest = d / path.name
                shutil.copy2(path, dest)
                rel = dest.relative_to(d)
            entries.append(AssetFile(path=rel.as_posix(), role=role, mime=mime or guess_mime(path)))
        if not entries:
            raise ValueError("pipeline não produziu arquivos")
        av = AssetVersion(slug=slug, version=version, files=entries, **meta)
        return self.write_meta(av)

    def import_file(self, src: Path, slug: str | None = None, modality: str | None = None, kind: str | None = None,
                    parent: str | None = None, name: str = "", tags: list[str] | None = None,
                    extra_files: list[tuple[Path, str]] | None = None, folder: str | None = None,
                    category: str | None = None, options: dict[str, Any] | None = None) -> AssetVersion:
        """Importa um arquivo externo como nova versão (nova ou do slug dado). Usado por upload/edição manual.
        Sem modality/kind, um Importer registrado para a extensão converte o arquivo (aseprite, gif
        animado, áudio com waveform, vídeo com poster...), com `options` do formato; senão, copia como veio.
        modality/kind vazios são inferidos pela extensão (malhas -> 3d/mesh, resto -> 2d/image)."""
        src = Path(src)
        if modality is None and kind is None:
            av = self._import_with_importer(src, slug, name, tags, folder, category, options or {}, parent)
            if av is not None:
                return av
        inf_mod, inf_kind = infer_modality_kind(src)
        modality = modality or inf_mod
        kind = kind or (inf_kind if modality == inf_mod else ("mesh" if modality == "3d" else "image"))
        slug = slugify(slug) if slug else self.unique_slug(import_slug_base(src))
        n, d = self.reserve_version(slug)
        try:
            ext = src.suffix.lower()
            stem = "model" if ext in MESH_EXTS else "audio" if ext in AUDIO_EXTS else "shader" if ext in SHADER_EXTS else "video" if ext in VIDEO_EXTS else "image"
            dest = d / f"{stem}{src.suffix.lower()}"
            shutil.copy2(src, dest)
            files = [(dest, "primary", None)] + [(p, r, None) for p, r in (extra_files or [])]
            av = self.finalize(slug, n, files, modality=modality, kind=kind, parent=parent,
                               name=name or slug, tags=tags or [], pipeline="core.import")
            if folder is not None or category is not None:
                self.set_meta(slug, folder=folder, category=category)
            return av
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise

    def _import_with_importer(self, src: Path, slug: str | None, name: str, tags: list[str] | None,
                              folder: str | None, category: str | None, options: dict[str, Any],
                              parent: str | None = None) -> AssetVersion | None:
        """Tenta os importers da extensão; None se nenhum serve (cai na cópia crua)."""
        from .pipeline import ImportContext, NotApplicable, get_registry

        for imp in get_registry().importers_for(src):
            slug_ = slugify(slug) if slug else self.unique_slug(import_slug_base(src))
            n, d = self.reserve_version(slug_)
            ctx = ImportContext(project=self.project, src=src, version_dir=d, slug=slug_, version=n, name=name,
                                options=options, meta={"modality": imp.modality, "kind": imp.kind})
            try:
                outs = imp.run(ctx)
            except NotApplicable:
                self.discard_reserved(slug_, n)
                continue
            except Exception:
                self.discard_reserved(slug_, n)
                shutil.rmtree(d, ignore_errors=True)
                raise
            try:
                meta = dict(ctx.meta)
                extra = {**meta.pop("extra", {}), "import": {"importer": imp.id, "source": src.name}}
                disp = name or meta.pop("name", None) or slug_
                meta.pop("name", None)
                av = self.finalize(slug_, n, [(o.path, o.role, o.mime) for o in outs], name=disp, tags=tags or [],
                                   pipeline="core.import", extra=extra, parent=parent, **meta)
                if folder is not None or category is not None:
                    self.set_meta(slug_, folder=folder, category=category)
                return av
            except Exception:
                shutil.rmtree(d, ignore_errors=True)
                raise
        return None

    def update_meta(self, ref: str, **changes: Any) -> AssetVersion:
        allowed = {"name", "tags", "notes"}
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f"campos não editáveis: {bad}")
        av = self.get(ref)
        av = av.model_copy(update={k: v for k, v in changes.items() if v is not None})
        return self.write_meta(av)

    def delete(self, ref: str) -> None:
        """Move a versão (ou o asset inteiro, se sem @vN) para .trash/ — nunca apaga de vez."""
        slug, ver = parse_ref(ref)
        trash = self.project.forge_dir / "trash"
        trash.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if ver is None:
            src = self.root / slug
            shutil.move(str(src), str(trash / f"{slug}-{stamp}"))
            self.index.remove(slug)
        else:
            src = self.version_dir(slug, ver)
            shutil.move(str(src), str(trash / f"{slug}-v{ver:03d}-{stamp}"))
            self.index.remove(slug, ver)
            if not self.version_numbers(slug) and (self.root / slug).exists():
                shutil.rmtree(self.root / slug, ignore_errors=True)

    # ---- organização (nível do asset, vale para todas as versões) -----
    def get_meta(self, slug: str) -> AssetMeta:
        f = self.root / slug / ASSET_META_FILE
        if f.exists():
            try:
                return AssetMeta.model_validate_json(f.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return AssetMeta()

    def set_meta(self, slug: str, folder: str | None = None, category: str | None = None,
                 clear_category: bool = False) -> AssetMeta:
        """Altera pasta e/ou categoria. folder="" move para a raiz; None mantém."""
        from .project import normalize_folder

        d = self.root / slug
        if not d.is_dir():
            raise FileNotFoundError(f"asset não encontrado: {slug}")
        meta = self.get_meta(slug)
        if folder is not None:
            meta.folder = normalize_folder(folder)
        if clear_category:
            meta.category = None
        elif category is not None:
            meta.category = category or None
        tmp = d / (ASSET_META_FILE + ".tmp")
        tmp.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(d / ASSET_META_FILE)
        self.index.set_meta(slug, meta.folder, meta.category)
        return meta

    def folder_counts(self) -> dict[str, int]:
        return self.index.folder_counts()

    def move_folder(self, old: str, new: str) -> int:
        """Renomeia/move uma pasta (e subpastas) — atualiza todos os assets dentro. Retorna nº movidos."""
        from .project import normalize_folder

        old, new = normalize_folder(old), normalize_folder(new)
        if not old:
            raise ValueError("a raiz não pode ser renomeada")
        if new == old or new.startswith(old + "/"):
            raise ValueError("não dá para mover uma pasta para dentro dela mesma")
        n = 0
        for slug in self.slugs():
            f = self.get_meta(slug).folder
            if f == old or f.startswith(old + "/"):
                self.set_meta(slug, folder=new + f[len(old):])
                n += 1
        return n

    def incomplete(self, min_age: float = 0.0) -> list[Path]:
        """Pastas de versão sem asset.json (job que morreu no meio). `min_age` em segundos
        evita pegar versões de jobs ainda em andamento em outro processo (CLI/MCP)."""
        now = datetime.now().timestamp()
        out = []
        for slug in self.slugs():
            for v in (self.root / slug).iterdir():
                if (v.is_dir() and v.name.startswith("v") and v.name[1:].isdigit()
                        and not (v / META_FILE).exists() and now - v.stat().st_mtime >= min_age):
                    out.append(v)
        return out

    def sweep_incomplete(self, min_age: float = 3600.0, only: tuple[str, int] | None = None) -> list[str]:
        """Move versões incompletas para .forge/trash/incomplete (recuperável). Retorna as movidas."""
        targets = [self.version_dir(*only)] if only else self.incomplete(min_age)
        trash = self.project.forge_dir / "trash" / "incomplete"
        moved = []
        for d in targets:
            if not d.exists() or (d / META_FILE).exists():
                continue
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / f"{d.parent.name}-{d.name}-{datetime.now():%Y%m%d-%H%M%S}"
            shutil.move(str(d), str(dest))
            moved.append(f"{d.parent.name}/{d.name}")
            parent = d.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        return moved

    def reindex(self) -> int:
        self.index.clear()
        count = 0
        for slug in self.slugs():
            for av in self.versions(slug):
                self.index.upsert(av)
                count += 1
            m = self.get_meta(slug)
            self.index.set_meta(slug, m.folder, m.category)
        return count
