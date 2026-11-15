"""Execução de exportadores (sem criar versão)."""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .pipeline import ExportContext, ExportParams, get_registry
from .project import Project
from .runner import PipelineError


def run_export(project: Project, ref: str, exporter_id: str, params: dict[str, Any] | None = None,
               out_dir: str | Path | None = None) -> list[Path]:
    exporter = get_registry().exporter(exporter_id)
    asset = project.store.get(ref)
    if not exporter.accepts(asset):
        raise PipelineError(f"{exporter_id} não se aplica a assets do tipo '{asset.kind}' (aceita {sorted(exporter.kinds or [])})")
    try:
        p: ExportParams = exporter.Params.model_validate(params or {})
    except ValidationError as e:
        raise PipelineError(f"parâmetros inválidos para {exporter_id}:\n{e}") from None
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="forge-export-"))
    out.mkdir(parents=True, exist_ok=True)
    files = exporter.run(ExportContext(project=project, asset=asset, out_dir=out), p)
    if not files:
        raise PipelineError(f"{exporter_id} não gerou arquivos")
    return files


def zip_files(files: list[Path], dest: Path) -> Path:
    base = Path(*Path(files[0]).parts[:-1]) if len(files) == 1 else Path(_common(files))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, Path(f).relative_to(base).as_posix())
    return dest


def _common(files: list[Path]) -> str:
    import os

    return os.path.commonpath([str(Path(f).parent) for f in files])
