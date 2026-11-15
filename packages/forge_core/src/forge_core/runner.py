"""Execução síncrona de um pipeline: resolve entrada, reserva versão, roda, grava asset.json.

Usado diretamente pela CLI/MCP e, dentro de uma thread, pelo JobManager do servidor.
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from .assets import AssetVersion, parse_ref
from .pipeline import Context, Pipeline, PipelineParams, get_registry
from .project import Project, slugify


class PipelineError(Exception):
    pass


def validate_params(pipeline: Pipeline, params: dict[str, Any] | PipelineParams) -> PipelineParams:
    if isinstance(params, PipelineParams):
        return params
    try:
        return pipeline.Params.model_validate(params or {})
    except ValidationError as e:
        raise PipelineError(f"parâmetros inválidos para {pipeline.id}:\n{e}") from None


def resolve_input(project: Project, ref: str | None) -> tuple[AssetVersion | None, Path | None]:
    if not ref:
        return None, None
    if ref.startswith("soul:"):
        from .souls import get_library

        sid, _, rid = ref[5:].partition("/")
        try:
            f = get_library().get(sid).ref_path(rid)
        except KeyError:
            raise PipelineError(f"soul não encontrada: {sid}") from None
        if not f.exists():
            raise PipelineError(f"referência não encontrada: {ref}")
        return None, f
    path = Path(ref)
    if path.suffix and path.exists():
        return None, path.resolve()
    try:
        parse_ref(ref)
    except ValueError:
        raise PipelineError(f"input_asset não é arquivo existente nem referência válida: {ref}") from None
    try:
        return project.store.get(ref), None
    except FileNotFoundError as e:
        raise PipelineError(str(e)) from None


def run_pipeline(
    project: Project,
    pipeline_id: str,
    params: dict[str, Any] | PipelineParams | None = None,
    *,
    asset: str | None = None,
    name: str | None = None,
    tags: list[str] | None = None,
    comfy_url: str | None = None,
    on_log: Callable[[str], None] | None = None,
    on_progress: Callable[[float, str | None], None] | None = None,
    on_reserved: Callable[[str, int], None] | None = None,
    cancel: threading.Event | None = None,
    folder: str | None = None,
    category: str | None = None,
) -> AssetVersion:
    """folder/category: organização do asset de destino. Assets novos derivados de outro herdam
    pasta e categoria da entrada quando não informadas."""
    pipeline = get_registry().get(pipeline_id)
    p = validate_params(pipeline, params or {})
    from .content import check_params
    from .content import is_nsfw as check_nsfw

    check_params(project, p.model_dump())
    input_av, input_path = resolve_input(project, p.input_asset)
    if pipeline.requires_input and input_av is None and input_path is None:
        raise PipelineError(f"{pipeline.id} exige input_asset")

    # destino: slug explícito > mesmo asset da entrada (edições) > novo slug a partir do prompt
    store = project.store
    if asset:
        slug = slugify(asset)
    elif pipeline.same_asset and input_av is not None:
        slug = input_av.slug
    else:
        slug = store.unique_slug(pipeline.default_slug(p))

    version, vdir = store.reserve_version(slug)
    if on_reserved:
        on_reserved(slug, version)
    ctx = Context(project=project, pipeline=pipeline, version_dir=vdir, slug=slug, version=version,
                  input=input_av, input_path=input_path, comfy_url=comfy_url,
                  _on_log=on_log, _on_progress=on_progress, _cancel=cancel or threading.Event())
    try:
        outputs = pipeline.run(ctx, p)
        ctx.check_cancelled()
        if check_nsfw(project):  # verificação visual local antes de gravar qualquer coisa
            from .safety import verify_outputs

            if on_progress:
                on_progress(0.97, "verificação de segurança")
            verify_outputs([o.path for o in outputs], on_log)
        meta = dict(ctx.meta)
        extra = meta.pop("extra", {})
        if pipeline.same_asset and input_av is not None and slug != input_av.slug:
            extra.setdefault("base", input_av.ref)  # variação derivada gravada como asset separado
        av = store.finalize(
            slug, version, [(o.path, o.role, o.mime) for o in outputs],
            modality=pipeline.modality, kind=meta.pop("kind", pipeline.kind), pipeline=pipeline.id,
            prompt=meta.pop("prompt", getattr(p, "prompt", None)), seed=meta.pop("seed", getattr(p, "seed", None)),
            workflow=meta.pop("workflow", None), params=p.model_dump(mode="json"),
            parent=input_av.ref if input_av else None, name=name or meta.pop("name", slug),
            tags=tags or [], extra={**extra, **meta},
        )
        _organize(store, slug, version, input_av, folder, category)
        if on_progress:
            on_progress(1.0, "concluído")
        return av
    except BaseException:
        shutil.rmtree(vdir, ignore_errors=True)
        store.discard_reserved(slug, version)
        raise


def _organize(store, slug: str, version: int, input_av: AssetVersion | None, folder: str | None,
              category: str | None) -> None:
    if version == 1 and input_av is not None and input_av.slug != slug:
        src = store.get_meta(input_av.slug)
        folder = src.folder if folder is None else folder
        category = src.category if category is None else category
    if folder is not None or category is not None:
        store.set_meta(slug, folder=folder, category=category)