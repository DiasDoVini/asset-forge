"""Frames -> GIF / MP4 / WebM."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


def frames_from_sheet(sheet: Path, count: int, columns: int) -> list[Image.Image]:
    """Cut a sprite sheet (row-major, equal cells) into frames."""
    img = Image.open(sheet).convert("RGBA")
    rows = -(-count // columns)
    w, h = img.width // columns, img.height // rows
    if w < 2 or h < 2:
        raise ValueError("sprite sheet too small for the given frame count/columns")
    return [img.crop(((i % columns) * w, (i // columns) * h, (i % columns + 1) * w, (i // columns + 1) * h)) for i in range(count)]


def write_media(frames: list[Image.Image], path: Path, fps: float = 24.0, loop: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = path.suffix.lower()
    if fmt == ".gif":
        # GIF keeps binary transparency (adaptive palette per frame); disposal=2 clears between frames
        pal = [f.convert("RGBA") for f in frames]
        pal[0].save(path, save_all=True, append_images=pal[1:], duration=round(1000 / fps), loop=0 if loop else 1,
                    disposal=2, optimize=False)
        return path
    if fmt not in (".mp4", ".webm"):
        raise ValueError(f"unsupported media format: {fmt}")
    import imageio_ffmpeg

    w, h = frames[0].size
    w, h = w - w % 2, h - h % 2  # yuv420 needs even sizes
    codec = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"] if fmt == ".mp4" else \
            ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30"]
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *codec, str(path)]
    data = b"".join(np.asarray(f.convert("RGBA").crop((0, 0, w, h)), dtype=np.uint8).tobytes() for f in frames)
    if fmt == ".mp4":  # no alpha in H.264: composite on white
        data = b"".join(_on_white(f.crop((0, 0, w, h))).tobytes() for f in frames)
    proc = subprocess.run(cmd, input=data, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode(errors='replace')[:500]}")
    return path


def _on_white(frame: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", frame.size, (255, 255, 255, 255))
    bg.alpha_composite(frame.convert("RGBA"))
    return bg
