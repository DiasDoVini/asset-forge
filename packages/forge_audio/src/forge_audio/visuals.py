"""Imagens de prévia: forma de onda (thumbnail da galeria) e espectrograma (para o crítico e o inspetor)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from .dsp import Audio, to_mono

BG = (20, 22, 30, 255)
WAVE = (110, 190, 255)
WAVE_RMS = (180, 225, 255)

# mapa "inferno" simplificado (posição, rgb)
_STOPS = [(0.0, (0, 0, 4)), (0.15, (31, 12, 72)), (0.35, (102, 19, 110)), (0.55, (176, 50, 90)),
          (0.75, (237, 105, 37)), (0.9, (251, 180, 26)), (1.0, (252, 255, 164))]


def _colormap(v: np.ndarray) -> np.ndarray:
    xs = np.array([s[0] for s in _STOPS])
    out = np.stack([np.interp(v, xs, [s[1][c] for s in _STOPS]) for c in range(3)], axis=-1)
    return out.astype(np.uint8)


def waveform(a: Audio, width: int = 768, height: int = 256, color=WAVE, bg=BG) -> Image.Image:
    img = Image.new("RGBA", (width, height), bg)
    m = to_mono(a).data[:, 0] if a.frames else np.zeros(1, np.float32)
    peak = float(np.max(np.abs(m))) or 1.0
    m = m / peak * 0.95
    edges = np.linspace(0, len(m), width + 1).astype(int)
    d = ImageDraw.Draw(img)
    mid = height / 2
    d.line([(0, mid), (width, mid)], fill=(60, 64, 80), width=1)
    for x in range(width):
        seg = m[edges[x]:max(edges[x] + 1, edges[x + 1])]
        lo, hi = float(seg.min()), float(seg.max())
        rms = float(np.sqrt(np.mean(seg ** 2)))
        d.line([(x, mid - hi * mid), (x, mid - lo * mid)], fill=color, width=1)
        d.line([(x, mid - rms * mid), (x, mid + rms * mid)], fill=WAVE_RMS, width=1)
    return img


def spectrogram(a: Audio, width: int = 768, height: int = 256, n_fft: int = 2048, fmin: float = 30.0) -> Image.Image:
    """Espectrograma em escala log de frequência (dB, faixa de 80 dB)."""
    x = to_mono(a).data[:, 0]
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    hop = max(1, (len(x) - n_fft) // max(1, width - 1))
    win = np.hanning(n_fft)
    cols = []
    for i in range(width):
        s = min(i * hop, len(x) - n_fft)
        cols.append(np.abs(np.fft.rfft(win * x[s:s + n_fft])))
    S = np.stack(cols, axis=1)  # (bins, width)
    freqs = np.fft.rfftfreq(n_fft, 1 / a.sr)
    target = np.geomspace(fmin, a.sr / 2, height)
    rows = np.stack([np.interp(target, freqs, S[:, c]) for c in range(S.shape[1])], axis=1)
    dbs = 20 * np.log10(rows + 1e-9)
    dbs = np.clip((dbs - (dbs.max() - 80)) / 80, 0, 1)
    rgb = _colormap(dbs[::-1])  # agudos no topo
    return Image.fromarray(rgb, "RGB").convert("RGBA")


def overview(a: Audio, width: int = 768) -> Image.Image:
    """Forma de onda em cima, espectrograma embaixo (prévia para o crítico)."""
    w = waveform(a, width, 160)
    s = spectrogram(a, width, 256)
    out = Image.new("RGBA", (width, 160 + 256 + 4), BG)
    out.paste(w, (0, 0))
    out.paste(s, (0, 164))
    return out
