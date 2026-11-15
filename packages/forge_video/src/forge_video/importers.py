"""Importador de vídeo: MP4, WebM, MOV, MKV e M4V.

Re-encoda para H.264 (o formato base dos assets de vídeo, que o navegador toca e o editor corta) e grava poster.png e
video.json, como os pipelines de vídeo. Precisa do ffmpeg.
"""
from __future__ import annotations

import json
import shutil

from pydantic import BaseModel, ConfigDict, Field

from forge_core import ImportContext, Importer, OutputFile

from . import ff


class VideoOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fps: float | None = Field(None, gt=0, le=120, description="converte para este fps")
    max_width: int | None = Field(None, ge=16, le=7680, description="reduz se for mais largo que isto")
    keep_source: bool = Field(False, description="guardar o arquivo original (pode ser grande)")


class VideoImporter(Importer):
    id = "video.import"
    title = "Vídeo (MP4, WebM, MOV, MKV, M4V)"
    description = "Converte para MP4 H.264, gera o poster e os metadados (resolução, fps, duração, áudio)."
    exts = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
    kind = "video"
    Options = VideoOptions

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        o = VideoOptions.model_validate(ctx.options or {})
        try:
            src_info = ff.probe(ctx.src)
        except Exception as e:
            raise ValueError(f"não consegui ler o vídeo (o ffmpeg/ffprobe está instalado? winget install Gyan.FFmpeg): {e}") from e
        d = ctx.version_dir
        out = d / "video.mp4"
        width = height = None
        if o.max_width and src_info.width > o.max_width:
            width = o.max_width - o.max_width % 2
            height = round(src_info.height * width / src_info.width)
            height -= height % 2
        ff.normalize(ctx.src, out, fps=o.fps, width=width, height=height)
        info = ff.probe(out)
        ff.poster(out, d / "poster.png")
        (d / "video.json").write_text(json.dumps({"info": info.to_dict(), "markers": []}, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
        files = [OutputFile(out, "primary", "video/mp4"), OutputFile(d / "poster.png", "preview"),
                 OutputFile(d / "video.json", "source", "application/json")]
        if o.keep_source:
            keep = d / ("source" + ctx.src.suffix.lower())
            shutil.copyfile(ctx.src, keep)
            files.append(OutputFile(keep, "source"))
        ctx.extra().update({"video": info.to_dict(), "markers": [], "original": src_info.to_dict()})
        return files


IMPORTERS = [VideoImporter()]
