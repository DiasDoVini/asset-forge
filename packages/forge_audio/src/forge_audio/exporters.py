"""Exportadores de áudio: arquivo no formato/taxa do engine, com variações e metadados de loop."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from forge_core import ExportContext, Exporter, ExportParams

from . import dsp


class FileParams(ExportParams):
    format: Literal["ogg", "wav", "mp3", "flac"] = Field("ogg", description="ogg: Godot/Unity/web; wav: Unity/Terraria/FMOD; mp3: web")
    sample_rate: Literal["keep", "22050", "44100", "48000"] = "keep"
    channels: Literal["keep", "mono", "stereo"] = "keep"
    bit_depth: Literal["16", "24", "float"] = Field("16", description="só WAV/FLAC")
    quality: float = Field(0.7, ge=0.0, le=1.0, description="OGG/MP3: 1 = melhor")
    include_variations: bool = Field(True, description="incluir var_02, var_03... (nomeados _01, _02...)")
    godot_import: bool = Field(False, description="gera .import com loop ligado (Godot 4) para loops")
    gain_db: float = Field(0.0, ge=-30, le=12)


class AudioFileExporter(Exporter):
    id = "audio.file"
    title = "Arquivo de áudio"
    description = "WAV / OGG / MP3 / FLAC com taxa, canais e variações; metadados de loop opcionais."
    Params = FileParams

    def run(self, ctx: ExportContext, p: FileParams) -> list[Path]:
        primary = ctx.files("primary")
        if not primary:
            raise ValueError("asset sem arquivo de áudio")
        sources = primary + (sorted(ctx.files("part")) if p.include_variations else [])
        name = ctx.asset.slug
        many = len(sources) > 1
        subtype = {"16": "PCM_16", "24": "PCM_24", "float": "FLOAT"}[p.bit_depth]
        loop = bool((ctx.asset.extra or {}).get("loop"))
        out: list[Path] = []
        for i, src in enumerate(sources):
            a = dsp.load(src)
            if p.sample_rate != "keep":
                a = dsp.resample(a, int(p.sample_rate))
            a = dsp.set_channels(a, p.channels)
            if p.gain_db:
                a = dsp.gain(a, p.gain_db)
            fn = ctx.out_dir / (f"{name}_{i + 1:02d}.{p.format}" if many else f"{name}.{p.format}")
            dsp.save(a, fn, subtype=subtype if p.format in ("wav", "flac") else None, quality=p.quality)
            out.append(fn)
            if p.godot_import and loop and p.format in ("ogg", "mp3", "wav"):
                imp = Path(str(fn) + ".import")
                importer = {"ogg": "oggvorbisstr", "mp3": "mp3", "wav": "wav"}[p.format]
                params = "loop=true\nloop_offset=0.0\n" if p.format != "wav" else f"edit/loop_mode=2\nedit/loop_begin=0\nedit/loop_end={a.frames}\n"
                imp.write_text(f'[remap]\n\nimporter="{importer}"\n\n[params]\n\n{params}', encoding="utf-8")
                out.append(imp)
        if many or loop:
            meta = {"name": name, "files": [f.name for f in out if f.suffix != ".import"], "loop": loop,
                    "duration": (ctx.asset.extra or {}).get("audio", {}).get("duration"),
                    "prompt": ctx.asset.prompt, "bpm": (ctx.asset.extra or {}).get("bpm")}
            js = ctx.out_dir / f"{name}.sound.json"
            js.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            out.append(js)
        return out


EXPORTERS = [AudioFileExporter()]
