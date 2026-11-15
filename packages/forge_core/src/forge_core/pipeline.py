"""Contrato de pipelines e registro de modalidades.

Uma modalidade (pacote forge_2d, forge_3d, ...) expõe, no entry point group "forge.modalities",
um objeto `Modality` (ou callable que o retorna):

    # pyproject.toml do pacote
    [project.entry-points."forge.modalities"]
    3d = "forge_3d:modality"

    # forge_3d/__init__.py
    from forge_core.pipeline import Modality, Pipeline, PipelineParams, OutputFile

    class SimplifyParams(PipelineParams):
        target_faces: int = 5000

    class Simplify(Pipeline):
        id = "3d.simplify"
        title = "Simplificar malha"
        resources = {"cpu"}
        kind = "mesh"
        Params = SimplifyParams
        requires_input = True

        def run(self, ctx, params):
            src = ctx.input_files("primary")[0]
            out = ctx.version_dir / "model.glb"
            ...
            return [OutputFile(out, "primary")]

    modality = Modality(name="3d", title="3D", pipelines=[Simplify()])
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .assets import AssetVersion
    from .comfy import ComfyClient
    from .project import Project

log = logging.getLogger("forge")

Resource = Literal["gpu", "cpu"]
Role = Literal["primary", "preview", "texture", "print", "part", "report", "mask", "source"]


class Cancelled(Exception):
    """Levantada por ctx.check_cancelled() quando o job foi cancelado."""


class PipelineParams(BaseModel):
    """Base dos parâmetros. `input_asset` aceita "slug", "slug@v003" ou um caminho de arquivo."""

    model_config = ConfigDict(extra="forbid")

    input_asset: str | None = Field(default=None, description="asset de entrada: slug, slug@v003 ou caminho de arquivo")


@dataclass
class OutputFile:
    path: Path
    role: Role = "primary"
    mime: str | None = None


@dataclass
class Context:
    """Tudo que um pipeline precisa. Criado pelo runner; pipelines não criam pastas de versão."""

    project: "Project"
    pipeline: "Pipeline"
    version_dir: Path
    slug: str
    version: int
    input: "AssetVersion | None" = None
    input_path: Path | None = None  # quando input_asset era um arquivo solto
    comfy_url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)  # prompt/seed/workflow/extra -> asset.json
    _on_log: Callable[[str], None] | None = None
    _on_progress: Callable[[float, str | None], None] | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)
    _comfy: "ComfyClient | None" = None

    # ---- entrada -------------------------------------------------------
    def input_files(self, role: str | None = None) -> list[Path]:
        if self.input is not None:
            return self.project.store.files_of(self.input, role)
        if self.input_path is not None and role in (None, "primary"):
            return [self.input_path]
        return []

    def style(self, modality: str | None = None) -> dict[str, Any]:
        return self.project.style_for(modality or self.pipeline.modality)

    # ---- serviços ------------------------------------------------------
    @property
    def comfy(self) -> "ComfyClient":
        if self._comfy is None:
            from .comfy import ComfyClient
            from .config import get_settings

            self._comfy = ComfyClient(self.comfy_url or get_settings().comfy_url, cancel_event=self._cancel)
            self._comfy.meta = {"project": self.project.slug, "pipeline": self.pipeline.id, "asset": self.slug,
                                "version": self.version}
            from .content import is_nsfw

            if is_nsfw(self.project):
                from .safety import apply_to_graph

                self._comfy.graph_filter = apply_to_graph
        return self._comfy

    def log(self, msg: str) -> None:
        log.info("[%s] %s", self.pipeline.id, msg)
        if self._on_log:
            self._on_log(msg)

    def progress(self, value: float, message: str | None = None) -> None:
        if self._on_progress:
            self._on_progress(max(0.0, min(1.0, float(value))), message)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()


class Pipeline:
    id: ClassVar[str]                      # "2d.text2d", "3d.simplify"
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    resources: ClassVar[set[str]] = {"gpu"}
    kind: ClassVar[str] = "image"          # tipo do asset produzido: image, sprite, mesh, ...
    Params: ClassVar[type[PipelineParams]] = PipelineParams
    requires_input: ClassVar[bool] = False
    # usa input_asset quando dado (padrão: igual a requires_input). A UI só mostra o seletor se True.
    accepts_input: ClassVar[bool | None] = None
    # True: a saída vira nova versão do asset de entrada (edição). False: novo asset.
    same_asset: ClassVar[bool] = False

    @property
    def modality(self) -> str:
        return self.id.split(".", 1)[0]

    def run(self, ctx: Context, params: PipelineParams) -> list[OutputFile]:
        raise NotImplementedError

    def default_slug(self, params: PipelineParams) -> str:
        """Nome base do asset novo. Por padrão usa `prompt` se existir."""
        prompt = getattr(params, "prompt", None)
        if prompt:  # primeiras palavras significativas: "a frost sword with..." -> "frost-sword-with-glowing"
            words = [w for w in prompt.replace(",", " ").split() if w.lower() not in {"a", "an", "the", "of"}]
            return " ".join(words[:4])
        return self.id.split(".", 1)[-1]

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "modality": self.modality,
            "title": self.title or self.id,
            "description": self.description,
            "resources": sorted(self.resources),
            "kind": self.kind,
            "requires_input": self.requires_input,
            "accepts_input": self.requires_input if self.accepts_input is None else self.accepts_input,
            "same_asset": self.same_asset,
            "params_schema": self.Params.model_json_schema(),
        }


class ExportParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class ExportContext:
    """Exportação não cria versão: grava arquivos em out_dir (temporário ou escolhido pelo usuário)."""

    project: "Project"
    asset: "AssetVersion"
    out_dir: Path

    @property
    def version_dir(self) -> Path:
        return self.project.store.version_dir(self.asset.slug, self.asset.version)

    def files(self, role: str | None = None) -> list[Path]:
        return self.project.store.files_of(self.asset, role)

    def style(self, modality: str | None = None) -> dict[str, Any]:
        return self.project.style_for(modality or self.asset.modality)


class Exporter:
    """Converte uma versão de asset em arquivos para download (GIF, sprite sheet, STL...)."""

    id: ClassVar[str]                          # "2d.gif"
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    kinds: ClassVar[set[str] | None] = None    # kinds aceitos (None = qualquer)
    # por padrão só assets da própria modalidade (kinds podem coincidir entre modalidades, ex.: animation)
    any_modality: ClassVar[bool] = False
    # False = só usado por UIs específicas (ex.: precisa de dados gerados no navegador)
    show_in_dialog: ClassVar[bool] = True
    Params: ClassVar[type[ExportParams]] = ExportParams

    @property
    def modality(self) -> str:
        return self.id.split(".", 1)[0]

    def accepts(self, asset: "AssetVersion") -> bool:
        if not self.any_modality and asset.modality != self.modality:
            return False
        return self.kinds is None or asset.kind in self.kinds

    def run(self, ctx: ExportContext, params: ExportParams) -> list[Path]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title or self.id, "description": self.description,
                "kinds": sorted(self.kinds) if self.kinds else None, "show_in_dialog": self.show_in_dialog,
                "params_schema": self.Params.model_json_schema()}


class NotApplicable(Exception):
    """Levantada por um Importer quando o arquivo não é do formato dele (ex.: JSON qualquer): o import
    cai no próximo importer ou na cópia crua."""


@dataclass
class ImportContext:
    """Entrada de um Importer: o arquivo externo e a pasta da versão (já reservada) onde gravar a saída."""

    project: "Project"
    src: Path
    version_dir: Path
    slug: str
    version: int
    name: str = ""
    options: dict[str, Any] = field(default_factory=dict)  # opções do formato (ex.: grade de uma folha de sprites)
    meta: dict[str, Any] = field(default_factory=dict)     # kind / modality / extra / name -> asset.json

    def extra(self) -> dict[str, Any]:
        return self.meta.setdefault("extra", {})


class Importer:
    """Converte um arquivo externo (aseprite, gif animado, áudio, vídeo...) em um asset da modalidade, gravando
    os mesmos arquivos que os pipelines dela gravam (para o viewer/exportadores funcionarem igual)."""

    id: ClassVar[str]                      # "2d.aseprite"
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    exts: ClassVar[set[str]] = set()       # extensões (minúsculas, com ponto) que ele trata
    kind: ClassVar[str] = "image"          # kind padrão; ctx.meta["kind"] sobrescreve
    priority: ClassVar[int] = 0            # maior vence quando mais de um importer trata a extensão
    Options: ClassVar[type[BaseModel] | None] = None  # opções aceitas em ctx.options (schema para a UI)

    @property
    def modality(self) -> str:
        return self.id.split(".", 1)[0]

    def accepts(self, path: Path) -> bool:
        return path.suffix.lower() in self.exts

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "modality": self.modality, "title": self.title or self.id,
                "description": self.description, "exts": sorted(self.exts), "kind": self.kind,
                "options_schema": self.Options.model_json_schema() if self.Options else None}


@dataclass
class Modality:
    name: str
    title: str = ""
    pipelines: list[Pipeline] = field(default_factory=list)
    description: str = ""
    exporters: list[Exporter] = field(default_factory=list)
    importers: list[Importer] = field(default_factory=list)
    refiners: list[Any] = field(default_factory=list)   # forge_core.refine.Refiner (geração avançada)


class Registry:
    def __init__(self) -> None:
        self.modalities: dict[str, Modality] = {}
        self.pipelines: dict[str, Pipeline] = {}
        self.exporters: dict[str, Exporter] = {}
        self.importers: dict[str, Importer] = {}
        self.refiners: dict[str, Any] = {}
        self.errors: dict[str, str] = {}

    def add(self, modality: Modality) -> None:
        self.modalities[modality.name] = modality
        for p in [*modality.pipelines, *modality.exporters, *modality.importers, *modality.refiners]:
            if not p.id.startswith(modality.name + "."):
                raise ValueError(f"{p.id} deve começar com '{modality.name}.'")
        for p in modality.pipelines:
            self.pipelines[p.id] = p
        for e in modality.exporters:
            self.exporters[e.id] = e
        for i in modality.importers:
            self.importers[i.id] = i
        for r in modality.refiners:
            self.refiners[r.id] = r

    def importers_for(self, path: Path) -> list[Importer]:
        """Importers que tratam o arquivo, do de maior prioridade para o menor."""
        found = [i for i in self.importers.values() if i.accepts(Path(path))]
        return sorted(found, key=lambda i: -i.priority)

    def exporter(self, exporter_id: str) -> Exporter:
        try:
            return self.exporters[exporter_id]
        except KeyError:
            raise KeyError(f"exportador desconhecido: {exporter_id}. Disponíveis: {sorted(self.exporters)}") from None

    def exporters_for(self, asset: "AssetVersion") -> list[Exporter]:
        return [e for e in self.exporters.values() if e.accepts(asset)]

    def load_entry_points(self) -> None:
        """Entry points "forge.modalities" + FORGE_MODALITIES="pacote.modulo:attr,..." (plugins locais/testes)."""
        import importlib
        import os

        sources: list[tuple[str, Callable[[], Any]]] = [(ep.name, ep.load) for ep in entry_points(group="forge.modalities")]
        for spec in filter(None, (s.strip() for s in os.environ.get("FORGE_MODALITIES", "").split(","))):
            mod_name, _, attr = spec.partition(":")
            sources.append((spec, lambda m=mod_name, a=attr: getattr(importlib.import_module(m), a or "modality")))
        for name, load in sources:
            try:
                obj = load()
                mod = obj() if callable(obj) and not isinstance(obj, Modality) else obj
                self.add(mod)
            except Exception as e:  # uma modalidade quebrada não derruba as outras
                log.exception("falha ao carregar modalidade %s", name)
                self.errors[name] = f"{type(e).__name__}: {e}"

    def get(self, pipeline_id: str) -> Pipeline:
        try:
            return self.pipelines[pipeline_id]
        except KeyError:
            raise KeyError(f"pipeline desconhecido: {pipeline_id}. Disponíveis: {sorted(self.pipelines)}") from None

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": m.name, "title": m.title or m.name, "description": m.description,
             "pipelines": [p.describe() for p in m.pipelines],
             "exporters": [e.describe() for e in m.exporters],
             "importers": [i.describe() for i in m.importers],
             "refiners": [r.describe() for r in m.refiners]}
            for m in self.modalities.values()
        ]


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
        _registry.load_entry_points()
    return _registry


def set_registry(reg: Registry) -> None:
    """Para testes."""
    global _registry
    _registry = reg
