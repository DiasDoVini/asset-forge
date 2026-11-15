"""Sintetizador de efeitos retrô no estilo sfxr (DrPetter), em Python puro + numpy.

Roda em CPU, sem modelos: moeda, laser, explosão, power-up, dano, pulo, blip, etc.
Os parâmetros seguem o sfxr clássico (valores normalizados 0..1 ou -1..1).
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

import numpy as np

from .dsp import Audio

Wave = Literal["square", "saw", "sine", "noise", "triangle"]
PRESETS = ["coin", "laser", "explosion", "powerup", "hit", "jump", "blip", "random", "click", "magic", "step"]


@dataclass
class SfxrParams:
    wave: Wave = "square"
    attack: float = 0.0
    sustain: float = 0.3
    punch: float = 0.0
    decay: float = 0.4
    start_freq: float = 0.3
    min_freq: float = 0.0
    slide: float = 0.0
    delta_slide: float = 0.0
    vib_depth: float = 0.0
    vib_speed: float = 0.0
    arp_mod: float = 0.0
    arp_speed: float = 0.0
    duty: float = 0.0
    duty_sweep: float = 0.0
    repeat_speed: float = 0.0
    phaser_offset: float = 0.0
    phaser_sweep: float = 0.0
    lpf_freq: float = 1.0
    lpf_ramp: float = 0.0
    lpf_resonance: float = 0.0
    hpf_freq: float = 0.0
    hpf_ramp: float = 0.0
    volume: float = 0.5

    def update(self, **kw: Any) -> "SfxrParams":
        names = {f.name for f in fields(self)}
        for k, v in kw.items():
            if k not in names:
                raise ValueError(f"parâmetro sfxr desconhecido: {k}")
            setattr(self, k, v)
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _f(r: random.Random, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo + (hi - lo) * r.random()


def preset(name: str, seed: int) -> SfxrParams:
    r = random.Random(seed)
    p = SfxrParams()
    if name == "coin":
        p.start_freq = 0.4 + _f(r, 0, 0.5)
        p.sustain = _f(r, 0, 0.1)
        p.decay = 0.1 + _f(r, 0, 0.4)
        p.punch = 0.3 + _f(r, 0, 0.3)
        if r.random() < 0.5:
            p.arp_speed = 0.5 + _f(r, 0, 0.2)
            p.arp_mod = 0.2 + _f(r, 0, 0.4)
    elif name == "laser":
        p.wave = r.choice(["square", "square", "saw", "sine"])
        if r.random() < 0.33:
            p.start_freq, p.min_freq, p.slide = 0.3 + _f(r, 0, 0.6), _f(r, 0, 0.1), -0.35 - _f(r, 0, 0.3)
        else:
            p.start_freq = 0.5 + _f(r, 0, 0.5)
            p.min_freq = max(0.2, p.start_freq - 0.2 - _f(r, 0, 0.6))
            p.slide = -0.15 - _f(r, 0, 0.2)
        if p.wave == "saw":
            p.duty = 1
        if r.random() < 0.5:
            p.duty, p.duty_sweep = _f(r, 0, 0.5), _f(r, 0, 0.2)
        else:
            p.duty, p.duty_sweep = 0.4 + _f(r, 0, 0.5), -_f(r, 0, 0.7)
        p.sustain, p.decay = 0.1 + _f(r, 0, 0.2), _f(r, 0, 0.4)
        if r.random() < 0.5:
            p.punch = _f(r, 0, 0.3)
        if r.random() < 0.33:
            p.phaser_offset, p.phaser_sweep = _f(r, 0, 0.2), -_f(r, 0, 0.2)
        if r.random() < 0.5:
            p.hpf_freq = _f(r, 0, 0.3)
    elif name == "explosion":
        p.wave = "noise"
        if r.random() < 0.5:
            p.start_freq, p.slide = 0.1 + _f(r, 0, 0.4), -0.1 + _f(r, 0, 0.4)
        else:
            p.start_freq, p.slide = 0.2 + _f(r, 0, 0.7), -0.2 - _f(r, 0, 0.2)
        p.start_freq *= p.start_freq
        if r.random() < 0.2:
            p.slide = 0.0
        if r.random() < 0.33:
            p.repeat_speed = 0.3 + _f(r, 0, 0.5)
        p.sustain, p.decay = 0.15 + _f(r, 0, 0.3), 0.3 + _f(r, 0, 0.4)
        if r.random() < 0.5:
            p.phaser_offset, p.phaser_sweep = -0.3 + _f(r, 0, 0.9), -_f(r, 0, 0.3)
        p.punch = 0.2 + _f(r, 0, 0.6)
        if r.random() < 0.5:
            p.vib_depth, p.vib_speed = _f(r, 0, 0.7), _f(r, 0, 0.6)
        if r.random() < 0.33:
            p.arp_speed, p.arp_mod = 0.6 + _f(r, 0, 0.3), 0.8 - _f(r, 0, 1.6)
    elif name == "powerup":
        if r.random() < 0.5:
            p.wave, p.duty = "saw", 1
        else:
            p.duty = _f(r, 0, 0.6)
        if r.random() < 0.5:
            p.start_freq, p.slide, p.repeat_speed = 0.2 + _f(r, 0, 0.3), 0.1 + _f(r, 0, 0.4), 0.4 + _f(r, 0, 0.4)
        else:
            p.start_freq, p.slide = 0.2 + _f(r, 0, 0.3), 0.05 + _f(r, 0, 0.2)
            if r.random() < 0.5:
                p.vib_depth, p.vib_speed = _f(r, 0, 0.7), _f(r, 0, 0.6)
        p.sustain, p.decay = 0.1 + _f(r, 0, 0.3), 0.15 + _f(r, 0, 0.4)
    elif name == "hit":
        p.wave = r.choice(["square", "saw", "noise"])
        if p.wave == "square":
            p.duty = _f(r, 0, 0.6)
        if p.wave == "saw":
            p.duty = 1
        p.start_freq, p.slide = 0.2 + _f(r, 0, 0.6), -0.3 - _f(r, 0, 0.4)
        p.sustain, p.decay = _f(r, 0, 0.1), 0.1 + _f(r, 0, 0.2)
        if r.random() < 0.5:
            p.hpf_freq = _f(r, 0, 0.3)
    elif name == "jump":
        p.wave, p.duty = "square", _f(r, 0, 0.6)
        p.start_freq, p.slide = 0.3 + _f(r, 0, 0.3), 0.1 + _f(r, 0, 0.2)
        p.sustain, p.decay = 0.1 + _f(r, 0, 0.3), 0.1 + _f(r, 0, 0.2)
        if r.random() < 0.5:
            p.hpf_freq = _f(r, 0, 0.3)
        if r.random() < 0.5:
            p.lpf_freq = 1 - _f(r, 0, 0.6)
    elif name == "blip":
        p.wave = r.choice(["square", "saw"])
        if p.wave == "square":
            p.duty = _f(r, 0, 0.6)
        else:
            p.duty = 1
        p.start_freq = 0.2 + _f(r, 0, 0.4)
        p.sustain, p.decay = 0.1 + _f(r, 0, 0.1), _f(r, 0, 0.2)
        p.hpf_freq = 0.1
    elif name == "click":
        p.wave = "square"
        p.duty = 0.5
        p.start_freq = 0.55 + _f(r, 0, 0.3)
        p.sustain, p.decay = 0.0, 0.04 + _f(r, 0, 0.06)
        p.hpf_freq = 0.2
    elif name == "magic":
        p.wave = r.choice(["sine", "triangle", "square"])
        p.start_freq = 0.35 + _f(r, 0, 0.3)
        p.slide = 0.05 + _f(r, 0, 0.15)
        p.vib_depth, p.vib_speed = 0.2 + _f(r, 0, 0.3), 0.5 + _f(r, 0, 0.3)
        p.arp_speed, p.arp_mod = 0.4 + _f(r, 0, 0.3), -0.3 - _f(r, 0, 0.3)
        p.attack, p.sustain, p.decay = _f(r, 0, 0.1), 0.2 + _f(r, 0, 0.2), 0.3 + _f(r, 0, 0.3)
        p.phaser_offset, p.phaser_sweep = 0.1 + _f(r, 0, 0.2), 0.05
    elif name == "step":
        p.wave = "noise"
        p.start_freq = 0.05 + _f(r, 0, 0.15)
        p.sustain, p.decay = 0.0, 0.08 + _f(r, 0, 0.08)
        p.punch = 0.3
        p.lpf_freq = 0.3 + _f(r, 0, 0.3)
    else:  # random
        p.wave = r.choice(["square", "saw", "sine", "noise"])
        p.start_freq = (_f(r, -1, 1) ** 2) if r.random() < 0.5 else (_f(r, -1, 1) ** 3 + 0.5)
        p.start_freq = min(1.0, abs(p.start_freq))
        p.slide = _f(r, -1, 1) ** 5
        if p.start_freq > 0.7 and p.slide > 0.2:
            p.slide = -p.slide
        if p.start_freq < 0.2 and p.slide < -0.05:
            p.slide = -p.slide
        p.delta_slide = _f(r, -1, 1) ** 3
        p.duty, p.duty_sweep = _f(r, -1, 1), _f(r, -1, 1) ** 3
        p.vib_depth, p.vib_speed = _f(r, -1, 1) ** 3, _f(r, -1, 1)
        p.attack, p.sustain, p.decay = _f(r, -1, 1) ** 3, _f(r, -1, 1) ** 2, _f(r, -1, 1)
        p.punch = _f(r, -1, 1) ** 2
        if p.attack + p.sustain + p.decay < 0.2:
            p.sustain += 0.2 + _f(r, 0, 0.3)
            p.decay += 0.2 + _f(r, 0, 0.3)
        p.lpf_resonance, p.lpf_freq, p.lpf_ramp = _f(r, -1, 1), 1 - _f(r, 0, 1) ** 3, _f(r, -1, 1) ** 3
        if p.lpf_freq < 0.1 and p.lpf_ramp < -0.05:
            p.lpf_ramp = -p.lpf_ramp
        p.hpf_freq, p.hpf_ramp = _f(r, 0, 1) ** 5, _f(r, -1, 1) ** 5
        p.phaser_offset, p.phaser_sweep = _f(r, -1, 1) ** 3, _f(r, -1, 1) ** 3
        p.repeat_speed = _f(r, -1, 1)
        p.arp_speed, p.arp_mod = _f(r, -1, 1), _f(r, -1, 1)
        p.attack, p.sustain, p.decay = abs(p.attack), abs(p.sustain), abs(p.decay)
        p.punch, p.vib_depth, p.repeat_speed, p.arp_speed = abs(p.punch), abs(p.vib_depth), abs(p.repeat_speed), abs(p.arp_speed)
    return p


def mutate(p: SfxrParams, amount: float, seed: int) -> SfxrParams:
    """Variação: perturba levemente os parâmetros contínuos (amount 0..1)."""
    if amount <= 0:
        return p
    r = random.Random(seed)
    q = SfxrParams(**p.to_dict())
    for f in fields(q):
        if f.name in ("wave", "volume"):
            continue
        v = getattr(q, f.name)
        if r.random() < 0.6:
            lo = -1.0 if f.name in ("slide", "delta_slide", "duty_sweep", "arp_mod", "phaser_offset", "phaser_sweep",
                                    "lpf_ramp", "hpf_ramp") else 0.0
            setattr(q, f.name, float(min(1.0, max(lo, v + r.uniform(-1, 1) * amount * 0.1))))
    return q


def render(p: SfxrParams, sr: int = 44100, seed: int = 0, max_seconds: float = 6.0, length: float = 1.0) -> Audio:
    """Port do laço de síntese do sfxr (supersampling 8x a 44,1 kHz internos). `length` escala o envelope."""
    rng = random.Random(seed)
    INTERNAL = 44100
    fperiod = 100.0 / (p.start_freq ** 2 + 0.001)
    period = int(fperiod)
    fmaxperiod = 100.0 / (p.min_freq ** 2 + 0.001)
    fslide = 1.0 - (p.slide ** 3) * 0.01
    fdslide = -(p.delta_slide ** 3) * 0.000001
    square_duty = 0.5 - p.duty * 0.5
    square_slide = -p.duty_sweep * 0.00005
    arp_mod = (1.0 - (p.arp_mod ** 2) * 0.9) if p.arp_mod >= 0 else (1.0 + (p.arp_mod ** 2) * 10.0)
    arp_limit = int((1.0 - p.arp_speed) ** 2 * 20000 + 32)
    if p.arp_speed == 1.0:
        arp_limit = 0
    env_len = [int(x ** 2 * 100000.0 * length) for x in (p.attack, p.sustain, p.decay)]
    env_len = [max(1, n) for n in env_len]
    fphase = (p.phaser_offset ** 2) * 1020.0 * (1 if p.phaser_offset >= 0 else -1)
    fdphase = (p.phaser_sweep ** 2) * (1 if p.phaser_sweep >= 0 else -1)
    iphase = abs(int(fphase))
    ipp = 0
    phaser_buffer = [0.0] * 1024
    noise_buffer = [rng.uniform(-1, 1) for _ in range(32)]
    fltp = fltdp = fltphp = 0.0
    fltw = (p.lpf_freq ** 3) * 0.1
    fltw_d = 1.0 + p.lpf_ramp * 0.0001
    fltdmp = min(0.8, 5.0 / (1.0 + (p.lpf_resonance ** 2) * 20.0) * (0.01 + fltw))
    flthp = (p.hpf_freq ** 2) * 0.1
    flthp_d = 1.0 + p.hpf_ramp * 0.0003
    vib_phase = 0.0
    vib_speed = (p.vib_speed ** 2) * 0.01
    vib_amp = p.vib_depth * 0.5
    rep_limit = int((1.0 - p.repeat_speed) ** 2 * 20000 + 32)
    if p.repeat_speed == 0.0:
        rep_limit = 0
    env_stage, env_time, phase = 0, 0, 0
    rep_time = arp_time = 0
    base_fperiod, base_fslide, base_square, base_sslide = fperiod, fslide, square_duty, square_slide
    out: list[float] = []
    max_n = int(max_seconds * INTERNAL)
    while len(out) < max_n:
        rep_time += 1
        if rep_limit and rep_time >= rep_limit:  # reinicia (efeito de repetição)
            rep_time = 0
            fperiod, fslide, square_duty, square_slide = base_fperiod, base_fslide, base_square, base_sslide
            period = int(fperiod)
            arp_time = 0
        arp_time += 1
        if arp_limit and arp_time >= arp_limit:
            arp_limit = 0
            fperiod *= arp_mod
        fslide += fdslide
        fperiod *= fslide
        if fperiod > fmaxperiod:
            fperiod = fmaxperiod
            if p.min_freq > 0.0:
                break
        rfperiod = fperiod
        if vib_amp > 0.0:
            vib_phase += vib_speed
            rfperiod = fperiod * (1.0 + math.sin(vib_phase) * vib_amp)
        period = max(8, int(rfperiod))
        square_duty = min(0.5, max(0.0, square_duty + square_slide))
        env_time += 1
        if env_time > env_len[env_stage]:
            env_time = 0
            env_stage += 1
            if env_stage == 3:
                break
        if env_stage == 0:
            env_vol = env_time / env_len[0]
        elif env_stage == 1:
            env_vol = 1.0 + (1.0 - env_time / env_len[1]) ** 1.0 * 2.0 * p.punch
        else:
            env_vol = 1.0 - env_time / env_len[2]
        fphase += fdphase
        iphase = min(1023, abs(int(fphase)))
        if flthp_d != 1.0:
            flthp = min(0.1, max(0.00001, flthp * flthp_d))
        ssample = 0.0
        for _ in range(8):
            phase += 1
            if phase >= period:
                phase %= period
                if p.wave == "noise":
                    noise_buffer = [rng.uniform(-1, 1) for _ in range(32)]
            fp = phase / period
            if p.wave == "square":
                sample = 0.5 if fp < square_duty else -0.5
            elif p.wave == "saw":
                sample = 1.0 - fp * 2
            elif p.wave == "sine":
                sample = math.sin(fp * 2 * math.pi)
            elif p.wave == "triangle":
                sample = 4 * abs(fp - 0.5) - 1
            else:
                sample = noise_buffer[int(phase * 32 / period) % 32]
            pp = fltp
            fltw = min(0.1, max(0.0, fltw * fltw_d))
            if p.lpf_freq != 1.0:
                fltdp += (sample - fltp) * fltw
                fltdp -= fltdp * fltdmp
            else:
                fltp = sample
                fltdp = 0.0
            fltp += fltdp
            fltphp += fltp - pp
            fltphp -= fltphp * flthp
            sample = fltphp
            phaser_buffer[ipp & 1023] = sample
            sample += phaser_buffer[(ipp - iphase + 1024) & 1023]
            ipp = (ipp + 1) & 1023
            ssample += sample * env_vol
        out.append(ssample / 8 * p.volume * 0.2)
    y = np.clip(np.array(out or [0.0], np.float32), -1, 1)
    a = Audio(y[:, None], INTERNAL)
    if sr != INTERNAL:
        from .dsp import resample
        a = resample(a, sr)
    return a
