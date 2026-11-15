"""Exportadores de vídeo: MP4/WebM/GIF, quadros PNG e sprite sheet (com JSON Aseprite)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field

from forge_core import ExportContext, Exporter, ExportParams

from . import ff


class VideoFileParams(ExportParams):
    format: Literal["mp4", "webm", "gif", "frames", "sheet"] = Field(
        "mp4", description="sheet/frames: para engines 2D (sprite sheet com JSON Aseprite)")
    start: float = Field(0.0, ge=0, description="início (s)")
    end: float | None = Field(None, ge=0, description="fim (s); vazio = até o final")
    width: int | None = Field(None, ge=16, le=4096, description="largura (altura proporcional)")
    fps: float | None = Field(None, ge=1, le=60, description="vazio = original (GIF/sheet: 12)")
    columns: int | None = Field(None, ge=1, le=64, description="sheet: colunas (vazio = quadrado)")
    quality: int = Field(23, ge=10, le=40, description="mp4/webm: CRF (menor = melhor)")


class VideoFileExporter(Exporter):
    id = "video.file"
    title = "Arquivo de vídeo"
    description = "MP4 (H.264), WebM (VP9), GIF, quadros PNG ou sprite sheet, com recorte de tempo, tamanho e fps."
    kinds = {"video"}
    Params = VideoFileParams

    def run(self, ctx: ExportContext, p: VideoFileParams) -> list[Path]:
        src = ctx.files("primary")[0]
        name = ctx.asset.slug
        cut = ["-ss", f"{p.start:.3f}"] + (["-to", f"{p.end:.3f}"] if p.end else [])
        vf = []
        fps = p.fps or (12 if p.format in ("gif", "sheet") else None)
        if fps:
            vf.append(f"fps={fps}")
        if p.width:
            vf.append(f"scale={p.width}:-2:flags=lanczos")
        vfa = ["-vf", ",".join(vf)] if vf else []
        out = ctx.out_dir
        if p.format == "mp4":
            f = out / f"{name}.mp4"
            ff.run([*cut, "-i", str(src), *vfa, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(p.quality),
                    "-c:a", "aac", "-movflags", "+faststart", str(f)])
            return [f]
        if p.format == "webm":
            f = out / f"{name}.webm"
            ff.run([*cut, "-i", str(src), *vfa, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(p.quality + 8),
                    "-c:a", "libopus", str(f)])
            return [f]
        if p.format == "gif":
            f = out / f"{name}.gif"
            chain = ",".join(vf + ["split[a][b]", "[a]palettegen=stats_mode=diff[p]", "[b][p]paletteuse=dither=bayer:bayer_scale=4"])
            ff.run([*cut, "-i", str(src), "-filter_complex", chain, "-loop", "0", str(f)])
            return [f]
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            ff.run([*cut, "-i", str(src), *vfa, str(td / "f_%05d.png")])
            frames = sorted(td.glob("f_*.png"))
            if not frames:
                raise ValueError("nenhum quadro no intervalo")
            if p.format == "frames":
                d = out / f"{name}_frames"
                d.mkdir(exist_ok=True)
                res = []
                for i, fr in enumerate(frames):
                    dst = d / f"{name}_{i:04d}.png"
                    dst.write_bytes(fr.read_bytes())
                    res.append(dst)
                return res
            from forge_2d.animation import aseprite_json, build_sheet

            imgs = [Image.open(f).convert("RGBA") for f in frames]
            sheet, rects = build_sheet(imgs, "grid", p.columns, 0, 1)
            png = out / f"{name}_sheet.png"
            sheet.save(png)
            js = out / f"{name}_sheet.json"
            dur = round(1000 / (fps or 12))
            js.write_text(json.dumps(aseprite_json(png.name, sheet.size, rects, [dur] * len(imgs),
                                                   [{"name": name, "from": 0, "to": len(imgs) - 1}], frame_prefix=name),
                                     indent=2), encoding="utf-8")
            return [png, js]


EXPORTERS = [VideoFileExporter()]
