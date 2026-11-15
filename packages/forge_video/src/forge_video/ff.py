"""ffmpeg/ffprobe: metadados, extração de quadros, codificação e operações de edição.

Procura o ffmpeg em FORGE_FFMPEG, no PATH e na instalação do winget (Gyan.FFmpeg).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image


class FFmpegError(RuntimeError):
    pass


@cache
def binary(name: str = "ffmpeg") -> str:
    env = os.environ.get("FORGE_FFMPEG")
    if env:
        cand = Path(env)
        cand = cand.with_name(f"{name}.exe" if os.name == "nt" else name) if cand.is_file() else cand / f"{name}.exe"
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    hits = sorted(glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", f"{name}.exe")))
    if hits:
        return hits[-1]
    raise FFmpegError(f"{name} não encontrado. Instale com `winget install Gyan.FFmpeg` ou defina FORGE_FFMPEG.")


def run(args: list[str], *, timeout: float = 1800) -> str:
    cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise FFmpegError(f"ffmpeg falhou: {p.stderr.strip()[-1500:]}")
    return p.stdout


@dataclass
class Info:
    width: int
    height: int
    fps: float
    duration: float
    frames: int
    has_audio: bool

    def to_dict(self) -> dict:
        return {"width": self.width, "height": self.height, "fps": round(self.fps, 3), "duration": round(self.duration, 3),
                "frames": self.frames, "has_audio": self.has_audio}


def probe(path: str | Path) -> Info:
    p = subprocess.run([binary("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise FFmpegError(f"ffprobe falhou: {p.stderr.strip()[-500:]}")
    d = json.loads(p.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise FFmpegError("arquivo sem trilha de vídeo")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "24/1").split("/")
    fps = float(num) / float(den) if float(den) else 24.0
    dur = float(v.get("duration") or d.get("format", {}).get("duration") or 0)
    frames = int(v.get("nb_frames") or round(dur * fps))
    return Info(int(v["width"]), int(v["height"]), fps, dur, frames, any(s["codec_type"] == "audio" for s in d["streams"]))


# ---------------------------------------------------------------- quadros

def frame_at(path: str | Path, t: float) -> Image.Image:
    """Quadro no tempo t (s). t < 0 conta do fim (-0.001 = último quadro)."""
    info = probe(path)
    if t < 0:
        t = max(0.0, info.duration + t - 1.0 / max(info.fps, 1))
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "f.png"
        run(["-ss", f"{max(0.0, t):.4f}", "-i", str(path), "-frames:v", "1", str(out)])
        if not out.exists():  # depois do fim: pega o último
            run(["-sseof", "-0.5", "-i", str(path), "-update", "1", str(out)])
        im = Image.open(out)
        im.load()
        return im.convert("RGB")


def extract_frames(path: str | Path, out_dir: Path, fps: float | None = None, size: int | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = []
    if fps:
        vf.append(f"fps={fps}")
    if size:
        vf.append(f"scale='if(gt(iw,ih),{size},-2)':'if(gt(iw,ih),-2,{size})'")
    args = ["-i", str(path)] + (["-vf", ",".join(vf)] if vf else []) + [str(out_dir / "f_%05d.png")]
    run(args)
    return sorted(out_dir.glob("f_*.png"))


H264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium", "-movflags", "+faststart"]


def encode_frames(frames: list[Image.Image] | list[Path], fps: float, out: Path, *, audio: Path | None = None) -> Path:
    """PNGs/imagens → MP4 H.264 (lados pares)."""
    with tempfile.TemporaryDirectory() as td:
        for i, f in enumerate(frames):
            dst = Path(td) / f"f_{i:05d}.png"
            if isinstance(f, Image.Image):
                f.convert("RGB").save(dst)
            else:
                shutil.copyfile(f, dst)
        args = ["-framerate", f"{fps:g}", "-i", str(Path(td) / "f_%05d.png")]
        if audio:
            args += ["-i", str(audio), "-c:a", "aac", "-b:a", "192k", "-shortest"]
        args += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", *H264, str(out)]
        run(args)
    return out


def poster(path: str | Path, out: Path, t: float | None = None) -> Path:
    info = probe(path)
    frame_at(path, info.duration * 0.4 if t is None else t).save(out)
    return out


def normalize(src: Path, out: Path, *, fps: float | None = None, width: int | None = None, height: int | None = None) -> Path:
    """Re-encoda para H.264 + AAC (ou sem áudio) — formato base de todos os assets de vídeo."""
    vf = []
    if width and height:
        vf.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
    if fps:
        vf.append(f"fps={fps}")
    vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    info = probe(src)
    args = ["-i", str(src), "-vf", ",".join(vf), *H264]
    args += ["-c:a", "aac", "-b:a", "192k"] if info.has_audio else ["-an"]
    run(args + [str(out)])
    return out
