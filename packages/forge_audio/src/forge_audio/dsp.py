"""Processamento de áudio em numpy/scipy: leitura/gravação, análise, efeitos e loops.

Convenção: `Audio.data` é float32 com shape (amostras, canais), valores em [-1, 1].
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import soundfile as sf
from scipy import signal

FORMATS = {"wav": "WAV", "flac": "FLAC", "ogg": "OGG", "mp3": "MP3"}
AUDIO_EXTS = {".wav", ".flac", ".ogg", ".mp3", ".opus", ".aiff", ".aif"}


@dataclass
class Audio:
    data: np.ndarray
    sr: int

    @property
    def channels(self) -> int:
        return self.data.shape[1]

    @property
    def frames(self) -> int:
        return self.data.shape[0]

    @property
    def duration(self) -> float:
        return self.frames / self.sr

    def copy(self, data: np.ndarray | None = None, sr: int | None = None) -> "Audio":
        return Audio(np.ascontiguousarray(self.data if data is None else data, dtype=np.float32), sr or self.sr)


# ---------------------------------------------------------------- E/S

def load(path: str | Path) -> Audio:
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return Audio(np.ascontiguousarray(data), int(sr))


def save(audio: Audio, path: str | Path, *, subtype: str | None = None, quality: float | None = None) -> Path:
    path = Path(path)
    fmt = FORMATS.get(path.suffix.lower().lstrip("."))
    if fmt is None:
        raise ValueError(f"formato não suportado: {path.suffix}")
    if fmt == "MP3" and audio.sr not in (32000, 44100, 48000):
        audio = resample(audio, 44100)
    data = np.clip(audio.data, -1.0, 1.0)
    kw: dict[str, Any] = {}
    if fmt == "OGG":
        kw["subtype"] = "VORBIS"
    elif fmt == "MP3":
        kw["subtype"] = "MPEG_LAYER_III"
    elif subtype:
        kw["subtype"] = subtype
    if fmt in ("OGG", "MP3") and quality is not None:
        kw["compression_level"] = float(min(1.0, max(0.0, 1.0 - quality)))
    sf.write(str(path), data, audio.sr, format=fmt, **kw)
    return path


def silence(seconds: float, sr: int = 44100, channels: int = 1) -> Audio:
    return Audio(np.zeros((max(1, round(seconds * sr)), channels), np.float32), sr)


# ---------------------------------------------------------------- conversões

def to_mono(a: Audio) -> Audio:
    return a if a.channels == 1 else a.copy(a.data.mean(axis=1, keepdims=True))


def to_stereo(a: Audio) -> Audio:
    return a if a.channels == 2 else a.copy(np.repeat(a.data[:, :1], 2, axis=1))


def set_channels(a: Audio, mode: Literal["keep", "mono", "stereo"]) -> Audio:
    return {"keep": lambda x: x, "mono": to_mono, "stereo": to_stereo}[mode](a)


def resample(a: Audio, sr: int) -> Audio:
    if sr == a.sr:
        return a
    g = math.gcd(sr, a.sr)
    out = signal.resample_poly(a.data, sr // g, a.sr // g, axis=0)
    return Audio(out.astype(np.float32), sr)


def match(a: Audio, b: Audio) -> Audio:
    """Converte `b` para a taxa e o nº de canais de `a`."""
    b = resample(b, a.sr)
    if b.channels != a.channels:
        b = to_stereo(b) if a.channels == 2 else to_mono(b)
    return b


# ---------------------------------------------------------------- análise

def db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-10 else -200.0


def _k_weight(a: Audio) -> np.ndarray:
    """Filtro K (ITU-R BS.1770) com coeficientes calculados para qualquer taxa."""
    sr = a.sr
    G, Q, fc = 3.999843853973347, 0.7071752369554196, 1681.974450955533
    K = math.tan(math.pi * fc / sr)
    Vh, Vb = 10 ** (G / 20), (10 ** (G / 20)) ** 0.499666774155
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    Q, fc = 0.5003270373238773, 38.13547087602444
    K = math.tan(math.pi * fc / sr)
    a0 = 1 + K / Q + K * K
    a2 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    y = signal.lfilter(b1, a1, a.data, axis=0)
    return signal.lfilter([1, -2, 1], a2, y, axis=0)


def loudness(a: Audio) -> float:
    """Loudness integrado (LUFS, BS.1770 com gating). Áudios < 0,4 s usam o bloco inteiro."""
    if not a.frames:
        return -200.0
    y = _k_weight(a)
    block, step = int(0.4 * a.sr), int(0.1 * a.sr)
    if a.frames < block:
        ms = float(np.mean(y ** 2, axis=0).sum())
        return -0.691 + 10 * math.log10(ms) if ms > 0 else -200.0
    z = np.array([np.mean(y[s:s + block] ** 2, axis=0).sum() for s in range(0, a.frames - block + 1, step)])
    lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    g = z[lk > -70]
    if not len(g):
        return -200.0
    rel = -0.691 + 10 * math.log10(g.mean()) - 10
    g2 = z[(lk > -70) & (lk > rel)]
    return float(-0.691 + 10 * math.log10(g2.mean())) if len(g2) else -200.0


def analyze(a: Audio) -> dict[str, Any]:
    peak = float(np.max(np.abs(a.data))) if a.frames else 0.0
    rms = float(np.sqrt(np.mean(a.data ** 2))) if a.frames else 0.0
    return {
        "duration": round(a.duration, 3),
        "sample_rate": a.sr,
        "channels": a.channels,
        "peak_db": round(db(peak), 2),
        "rms_db": round(db(rms), 2),
        "lufs": round(loudness(a), 2),
        "clipped": int(np.sum(np.abs(a.data) >= 0.999)),
        "silent": peak < 1e-4,
    }


def find_onsets(a: Audio, threshold_db: float = -45.0) -> tuple[int, int]:
    """(início, fim) em amostras da região acima do limiar."""
    env = np.max(np.abs(a.data), axis=1)
    idx = np.nonzero(env > 10 ** (threshold_db / 20))[0]
    if not len(idx):
        return 0, a.frames
    return int(idx[0]), int(idx[-1]) + 1


# ---------------------------------------------------------------- edição básica

def trim(a: Audio, start: float = 0.0, end: float | None = None) -> Audio:
    s = max(0, round(start * a.sr))
    e = a.frames if end is None or end <= 0 else min(a.frames, round(end * a.sr))
    if e <= s:
        raise ValueError("trecho vazio (fim <= início)")
    return a.copy(a.data[s:e])


def cut(a: Audio, start: float, end: float, crossfade_ms: float = 5.0) -> Audio:
    """Remove o trecho [start, end) e emenda as pontas com um crossfade curto."""
    s, e = max(0, round(start * a.sr)), min(a.frames, round(end * a.sr))
    if e <= s:
        return a
    if s == 0:
        return a.copy(a.data[e:])
    if e >= a.frames:
        return a.copy(a.data[:s])
    return concat([a.copy(a.data[:s]), a.copy(a.data[e:])], crossfade_ms)


def trim_silence(a: Audio, threshold_db: float = -50.0, keep_ms: float = 15.0) -> Audio:
    s, e = find_onsets(a, threshold_db)
    p = round(keep_ms / 1000 * a.sr)
    return a.copy(a.data[max(0, s - p):min(a.frames, e + p)])


def _curve(n: int, shape: str) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)
    if shape == "exp":
        return t ** 2
    if shape == "log":
        return np.sqrt(t)
    if shape == "equal_power":
        return np.sin(t * np.pi / 2)
    return t


def fade(a: Audio, in_ms: float = 0.0, out_ms: float = 0.0, shape: str = "equal_power") -> Audio:
    d = a.data.copy()
    n_in = min(a.frames, round(in_ms / 1000 * a.sr))
    n_out = min(a.frames, round(out_ms / 1000 * a.sr))
    if n_in > 1:
        d[:n_in] *= _curve(n_in, shape)[:, None]
    if n_out > 1:
        d[-n_out:] *= _curve(n_out, shape)[::-1, None]
    return a.copy(d)


def gain(a: Audio, db_: float) -> Audio:
    return a.copy(a.data * (10 ** (db_ / 20)))


def normalize_peak(a: Audio, target_db: float = -1.0) -> Audio:
    peak = float(np.max(np.abs(a.data))) if a.frames else 0.0
    return a if peak < 1e-6 else a.copy(a.data * (10 ** (target_db / 20) / peak))


def soft_limit(a: Audio, ceiling_db: float = -1.0) -> Audio:
    c = 10 ** (ceiling_db / 20)
    return a.copy(np.tanh(a.data / c) * c)


def normalize_loudness(a: Audio, target_lufs: float = -16.0, peak_ceiling_db: float = -1.0) -> Audio:
    cur = loudness(a)
    if cur <= -100:
        return a
    out = gain(a, target_lufs - cur)
    if float(np.max(np.abs(out.data))) > 10 ** (peak_ceiling_db / 20):
        out = soft_limit(out, peak_ceiling_db)
    return out


def reverse(a: Audio) -> Audio:
    return a.copy(a.data[::-1])


def pad(a: Audio, before: float = 0.0, after: float = 0.0) -> Audio:
    b = np.zeros((round(before * a.sr), a.channels), np.float32)
    e = np.zeros((round(after * a.sr), a.channels), np.float32)
    return a.copy(np.concatenate([b, a.data, e]))


def concat(parts: list[Audio], crossfade_ms: float = 0.0) -> Audio:
    parts = [p for p in parts if p.frames]
    if not parts:
        raise ValueError("nada para concatenar")
    out = parts[0]
    for p in parts[1:]:
        p = match(out, p)
        n = min(round(crossfade_ms / 1000 * out.sr), out.frames, p.frames)
        if n > 1:
            c = _curve(n, "equal_power")
            mid = out.data[-n:] * c[::-1, None] + p.data[:n] * c[:, None]
            out = out.copy(np.concatenate([out.data[:-n], mid, p.data[n:]]))
        else:
            out = out.copy(np.concatenate([out.data, p.data]))
    return out


def mix(base: Audio, layer: Audio, offset: float = 0.0, gain_db: float = 0.0, pan: float = 0.0) -> Audio:
    """Soma `layer` em `base` a partir de `offset` segundos (estende se precisar). pan em [-1, 1]."""
    if pan and base.channels == 1:
        base = to_stereo(base)
    layer = gain(match(base, layer), gain_db)
    if pan and layer.channels == 2:
        ang = (pan + 1) * math.pi / 4
        layer = layer.copy(layer.data * np.array([math.cos(ang), math.sin(ang)], np.float32) * math.sqrt(2))
    start = max(0, round(offset * base.sr))
    end = start + layer.frames
    d = base.data
    d = np.concatenate([d, np.zeros((end - d.shape[0], base.channels), np.float32)]) if end > d.shape[0] else d.copy()
    d[start:end] += layer.data
    return base.copy(d)


# ---------------------------------------------------------------- tempo e altura

def speed(a: Audio, factor: float) -> Audio:
    """Muda velocidade e altura juntos (varispeed, como fita)."""
    if abs(factor - 1.0) < 1e-4:
        return a
    n = max(1, round(a.frames / factor))
    return a.copy(signal.resample(a.data, n, axis=0).astype(np.float32))


def time_stretch(a: Audio, factor: float, n_fft: int = 2048) -> Audio:
    """Muda a duração sem mudar a altura (phase vocoder). factor > 1 = mais longo."""
    if abs(factor - 1.0) < 1e-4:
        return a
    hop = n_fft // 4
    win = signal.windows.hann(n_fft, sym=False)
    omega = 2 * np.pi * hop * np.arange(n_fft // 2 + 1) / n_fft
    out_ch = []
    for ch in range(a.channels):
        x = np.concatenate([np.zeros(n_fft), a.data[:, ch], np.zeros(n_fft + hop)])
        frames = 1 + (len(x) - n_fft) // hop
        S = np.stack([np.fft.rfft(win * x[i * hop:i * hop + n_fft]) for i in range(frames)])
        steps = np.arange(0, frames - 1, 1 / factor)
        phase = np.angle(S[0])
        y = np.zeros(int(len(steps) * hop + n_fft))
        for k, t in enumerate(steps):
            i = int(t)
            frac = t - i
            mag = (1 - frac) * np.abs(S[i]) + frac * np.abs(S[i + 1])
            y[k * hop:k * hop + n_fft] += np.fft.irfft(mag * np.exp(1j * phase), n_fft) * win
            dphi = np.angle(S[i + 1]) - np.angle(S[i]) - omega
            dphi -= 2 * np.pi * np.round(dphi / (2 * np.pi))
            phase = phase + omega + dphi
        y = y / (np.sum(win ** 2) / hop)
        start = round(n_fft * factor)
        y = y[start:start + round(a.frames * factor)]
        out_ch.append(np.pad(y, (0, max(0, round(a.frames * factor) - len(y)))))
    return a.copy(np.stack(out_ch, axis=1).astype(np.float32))


def pitch_shift(a: Audio, semitones: float) -> Audio:
    """Muda a altura mantendo a duração."""
    if abs(semitones) < 1e-3:
        return a
    f = 2 ** (semitones / 12)
    out = speed(time_stretch(a, f), f)
    d = out.data[:a.frames]
    if len(d) < a.frames:
        d = np.concatenate([d, np.zeros((a.frames - len(d), a.channels), np.float32)])
    return a.copy(d)


# ---------------------------------------------------------------- filtros e efeitos

def _sos(kind: str, freq: float | tuple[float, float], sr: int, order: int = 4) -> np.ndarray:
    nyq = sr / 2
    if isinstance(freq, tuple):
        w: Any = (max(1.0, freq[0]) / nyq, min(nyq * 0.99, freq[1]) / nyq)
    else:
        w = min(max(1.0, freq), nyq * 0.99) / nyq
    return signal.butter(order, w, btype=kind, output="sos")


def lowpass(a: Audio, hz: float, order: int = 4) -> Audio:
    return a.copy(signal.sosfilt(_sos("lowpass", hz, a.sr, order), a.data, axis=0).astype(np.float32))


def highpass(a: Audio, hz: float, order: int = 4) -> Audio:
    return a.copy(signal.sosfilt(_sos("highpass", hz, a.sr, order), a.data, axis=0).astype(np.float32))


def bandpass(a: Audio, lo: float, hi: float, order: int = 2) -> Audio:
    return a.copy(signal.sosfilt(_sos("bandpass", (lo, hi), a.sr, order), a.data, axis=0).astype(np.float32))


def _biquad_peak(f0: float, gain_db: float, q: float, sr: int) -> tuple[list[float], list[float]]:
    A = 10 ** (gain_db / 40)
    w = 2 * math.pi * f0 / sr
    al = math.sin(w) / (2 * q)
    b = [1 + al * A, -2 * math.cos(w), 1 - al * A]
    aa = [1 + al / A, -2 * math.cos(w), 1 - al / A]
    return [x / aa[0] for x in b], [x / aa[0] for x in aa]


def _biquad_shelf(f0: float, gain_db: float, sr: int, high: bool) -> tuple[list[float], list[float]]:
    A = 10 ** (gain_db / 40)
    w = 2 * math.pi * f0 / sr
    al = math.sin(w) / 2 * math.sqrt(2)
    c = math.cos(w)
    sq = 2 * math.sqrt(A) * al
    if high:
        b = [A * ((A + 1) + (A - 1) * c + sq), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sq)]
        aa = [(A + 1) - (A - 1) * c + sq, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sq]
    else:
        b = [A * ((A + 1) - (A - 1) * c + sq), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - sq)]
        aa = [(A + 1) + (A - 1) * c + sq, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - sq]
    return [x / aa[0] for x in b], [x / aa[0] for x in aa]


def eq3(a: Audio, low_db: float = 0.0, mid_db: float = 0.0, high_db: float = 0.0,
        low_hz: float = 200.0, mid_hz: float = 1200.0, high_hz: float = 5000.0) -> Audio:
    d = a.data
    if low_db:
        d = signal.lfilter(*_biquad_shelf(low_hz, low_db, a.sr, high=False), d, axis=0)
    if mid_db:
        d = signal.lfilter(*_biquad_peak(mid_hz, mid_db, 0.9, a.sr), d, axis=0)
    if high_db:
        d = signal.lfilter(*_biquad_shelf(high_hz, high_db, a.sr, high=True), d, axis=0)
    return a.copy(np.asarray(d, np.float32))


def reverb(a: Audio, mix_: float = 0.3, room: float = 0.5, damping: float = 0.5, predelay_ms: float = 20.0,
           seed: int = 7) -> Audio:
    """Reverb por convolução com resposta sintética (ruído com decaimento exponencial, estéreo decorrelado).
    A cauda é preservada (o áudio fica mais longo)."""
    rt60 = 0.3 + 3.2 * room
    n = int(rt60 * a.sr)
    rng = np.random.default_rng(seed)
    env = np.exp(-6.9 * (np.arange(n) / a.sr) / rt60)
    pre = np.zeros(int(predelay_ms / 1000 * a.sr))
    st = to_stereo(a)
    wet_ch = []
    for c in range(2):
        ir = rng.standard_normal(n) * env
        if damping > 0:  # agudos decaem mais rápido
            ir = signal.sosfilt(_sos("lowpass", 16000 * (1 - 0.85 * damping) + 800, a.sr, 2), ir)
        ir = np.concatenate([pre, ir])
        ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
        wet_ch.append(signal.fftconvolve(st.data[:, c], ir))
    wet = np.stack(wet_ch, axis=1)
    dry_rms = float(np.sqrt(np.mean(st.data ** 2))) + 1e-9
    wet *= dry_rms / (float(np.sqrt(np.mean(wet[: st.frames] ** 2))) + 1e-9)
    dry = np.concatenate([st.data, np.zeros((wet.shape[0] - st.frames, 2), np.float32)])
    out = Audio((dry * (1 - mix_) + wet * mix_).astype(np.float32), a.sr)
    s, e = find_onsets(out, -70.0)
    out = out.copy(out.data[: max(e, a.frames)])
    return to_mono(out) if a.channels == 1 else out


def echo(a: Audio, delay_ms: float = 250.0, feedback: float = 0.4, mix_: float = 0.35, repeats: int = 6) -> Audio:
    d = max(1, round(delay_ms / 1000 * a.sr))
    out = np.zeros((a.frames + d * repeats, a.channels), np.float32)
    out[: a.frames] += a.data
    for k in range(1, repeats + 1):
        g = mix_ * feedback ** (k - 1)
        if g < 1e-3:
            break
        out[k * d:k * d + a.frames] += a.data * g
    res = a.copy(out)
    s, e = find_onsets(res, -70.0)
    return res.copy(res.data[: max(e, a.frames)])


def distortion(a: Audio, drive: float = 0.5) -> Audio:
    k = 1 + drive * 30
    peak = float(np.max(np.abs(a.data))) or 1.0
    return a.copy(np.tanh(a.data / peak * k) / math.tanh(k) * peak)


def bitcrush(a: Audio, bits: int = 8, target_sr: int | None = None) -> Audio:
    d = a.data
    if target_sr and target_sr < a.sr:  # sample-and-hold: aliasing proposital, som retrô
        step = a.sr / target_sr
        idx = (np.floor(np.arange(a.frames) / step) * step).astype(int)
        d = d[np.minimum(idx, a.frames - 1)]
    if bits < 24:
        q = 2 ** (bits - 1)
        d = np.round(d * q) / q
    return a.copy(d)


def tremolo(a: Audio, rate_hz: float = 6.0, depth: float = 0.5) -> Audio:
    t = np.arange(a.frames) / a.sr
    lfo = 1 - depth * (0.5 + 0.5 * np.sin(2 * np.pi * rate_hz * t))
    return a.copy(a.data * lfo[:, None].astype(np.float32))


def compress(a: Audio, threshold_db: float = -18.0, ratio: float = 4.0, attack_ms: float = 5.0,
             release_ms: float = 80.0, makeup_db: float | None = None) -> Audio:
    env = np.max(np.abs(a.data), axis=1)
    att = math.exp(-1 / max(1, attack_ms / 1000 * a.sr))
    rel = math.exp(-1 / max(1, release_ms / 1000 * a.sr))
    smooth = np.maximum(signal.lfilter([1 - rel], [1, -rel], env), signal.lfilter([1 - att], [1, -att], env))
    lvl = 20 * np.log10(np.maximum(smooth, 1e-6))
    gr = -np.maximum(0, lvl - threshold_db) * (1 - 1 / ratio)
    if makeup_db is None:
        makeup_db = -threshold_db * (1 - 1 / ratio) * 0.5
    g = 10 ** ((gr + makeup_db) / 20)
    return a.copy(a.data * g[:, None].astype(np.float32))


# ---------------------------------------------------------------- loops

def make_loop(a: Audio, crossfade_ms: float = 250.0) -> Audio:
    """Loop sem emenda: o final entra em crossfade com o início (a duração diminui `crossfade_ms`)."""
    n = min(round(crossfade_ms / 1000 * a.sr), a.frames // 3)
    if n < 2:
        return a
    c = _curve(n, "equal_power")
    joined = a.data[-n:] * c[::-1, None] + a.data[:n] * c[:, None]
    return a.copy(np.concatenate([joined, a.data[n:-n]]))


def bar_seconds(bpm: float, beats_per_bar: int = 4) -> float:
    return 60.0 / bpm * beats_per_bar


def loop_to_bars(a: Audio, bpm: float, beats_per_bar: int = 4, crossfade_ms: float = 30.0) -> Audio:
    """Corta no maior nº inteiro de compassos (potência de 2 quando possível) e fecha o loop no compasso."""
    bar = bar_seconds(bpm, beats_per_bar)
    bars = int(a.duration // bar)
    if bars < 1:
        return make_loop(a, crossfade_ms)
    p2 = 2 ** int(math.log2(bars))
    bars = p2 if p2 >= bars * 0.6 else bars
    length = bars * bar
    extra = min(crossfade_ms / 1000, a.duration - length)
    if extra > 0.002:  # usa o trecho após o último compasso como crossfade para o início
        n = round(extra * a.sr)
        body = a.data[: round(length * a.sr)].copy()
        c = _curve(n, "equal_power")
        body[:n] = body[:n] * c[:, None] + a.data[round(length * a.sr):round(length * a.sr) + n] * c[::-1, None]
        return a.copy(body)
    return trim(a, 0.0, length)


def loop_seam_score(a: Audio) -> float:
    """0..1: quão suave é a emenda fim→início (1 = perfeito)."""
    if a.frames < 16:
        return 0.0
    jump = float(np.mean(np.abs(a.data[-1] - a.data[0])))
    level = float(np.mean(np.abs(a.data))) + 1e-6
    return float(max(0.0, 1.0 - jump / (4 * level)))
