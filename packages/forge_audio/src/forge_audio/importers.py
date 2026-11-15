"""Importador de áudio: WAV, FLAC, OGG, MP3, Opus e AIFF.

Grava o mesmo que os pipelines de áudio: audio.wav (24 bits, primary), waveform.png, spectrogram.png e as
estatísticas (duração, pico, LUFS) em extra.audio. O arquivo original fica como fonte.
"""
from __future__ import annotations

import shutil

from pydantic import BaseModel, ConfigDict, Field

from forge_core import ImportContext, Importer, OutputFile

from . import dsp, visuals


class AudioOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str | None = Field(None, description="sfx | music (padrão: música se tiver 20 s ou mais)")
    normalize_lufs: float | None = Field(None, ge=-40, le=-5, description="normaliza a loudness (ex.: -16)")
    trim_silence: bool = Field(False, description="corta o silêncio do começo e do fim")
    mono: bool = Field(False, description="mistura para mono")
    sample_rate: int | None = Field(None, ge=8000, le=192000, description="reamostra")


class AudioImporter(Importer):
    id = "audio.import"
    title = "Áudio (WAV, FLAC, OGG, MP3, Opus, AIFF)"
    description = "Decodifica, gera forma de onda e espectrograma e guarda como WAV 24 bits; o original fica como fonte."
    exts = {".wav", ".flac", ".ogg", ".mp3", ".opus", ".aiff", ".aif"}
    kind = "sfx"
    Options = AudioOptions

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        o = AudioOptions.model_validate(ctx.options or {})
        try:
            a = dsp.load(ctx.src)
        except Exception as e:
            raise ValueError(f"não consegui decodificar o áudio ({ctx.src.suffix}): {e}") from e
        if a.frames == 0:
            raise ValueError("o áudio está vazio")
        if o.trim_silence:
            a = dsp.trim_silence(a)
        if o.mono:
            a = dsp.to_mono(a)
        if o.sample_rate and a.sr != o.sample_rate:
            a = dsp.resample(a, o.sample_rate)
        if o.normalize_lufs is not None:
            a = dsp.normalize_loudness(a, o.normalize_lufs)
        d = ctx.version_dir
        dsp.save(a, d / "audio.wav", subtype="PCM_24")
        visuals.waveform(a).save(d / "waveform.png")
        visuals.spectrogram(a).save(d / "spectrogram.png")
        keep = d / ("source" + ctx.src.suffix.lower())
        shutil.copyfile(ctx.src, keep)
        ctx.meta["kind"] = o.kind or ("music" if a.duration >= 20 else "sfx")
        ctx.extra().update({"audio": dsp.analyze(a), "variations": 1, "original_format": ctx.src.suffix.lstrip(".").upper()})
        return [OutputFile(d / "audio.wav", "primary", "audio/wav"), OutputFile(d / "waveform.png", "preview"),
                OutputFile(d / "spectrogram.png", "report"), OutputFile(keep, "source")]


IMPORTERS = [AudioImporter()]
