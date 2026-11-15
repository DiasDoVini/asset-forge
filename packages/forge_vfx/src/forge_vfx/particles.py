"""Sistemas de partículas: definição (particles.json), simulação determinística em numpy e renderização.

Unidades: pixels; origem no centro do canvas; y para cima; ângulos em graus anti-horário (90 = para cima).
A mesma semântica é implementada na prévia da UI (web/src/modalities/vfx/particles.ts) e traduzida para
Godot (CPUParticles2D) e Unity (ParticleSystem via script de editor).
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

BuiltinTexture = Literal["soft", "circle", "spark", "star", "smoke", "ring", "square", "flare"]
BUILTIN_TEXTURES = ["soft", "circle", "spark", "star", "smoke", "ring", "square", "flare"]


class Burst(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time: float = Field(0.0, ge=0)
    count: int = Field(10, ge=1, le=2000)


class Emitter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "emitter"
    texture: str = Field("soft", description="soft, circle, spark, star, smoke, ring, square, flare ou asset:<slug>")
    blend: Literal["add", "alpha"] = "add"
    rate: float = Field(20.0, ge=0, le=5000, description="partículas por segundo (0 = só bursts)")
    bursts: list[Burst] = Field(default_factory=list)
    max_particles: int = Field(400, ge=1, le=5000)
    start_delay: float = Field(0.0, ge=0)
    emit_duration: float | None = Field(None, ge=0, description="vazio = a duração do sistema")
    shape: Literal["point", "circle", "ring", "box", "line"] = "point"
    radius: float = Field(0.0, ge=0, description="circle/ring")
    width: float = Field(0.0, ge=0, description="box/line")
    height: float = Field(0.0, ge=0, description="box")
    position: list[float] = Field(default_factory=lambda: [0.0, 0.0])
    direction: float = Field(90.0, description="graus; 90 = para cima")
    spread: float = Field(20.0, ge=0, le=180, description="± graus em torno da direção")
    speed: list[float] = Field(default_factory=lambda: [40.0, 80.0])
    lifetime: list[float] = Field(default_factory=lambda: [0.8, 1.2])
    gravity: list[float] = Field(default_factory=lambda: [0.0, 0.0])
    drag: float = Field(0.0, ge=0, le=20, description="fração da velocidade perdida por segundo")
    radial_accel: float = Field(0.0, description="aceleração para fora (+) ou para dentro (-) da posição do emissor")
    tangential_accel: float = Field(0.0, description="aceleração girando em volta do emissor (+ = anti-horário)")
    turbulence: float = Field(0.0, ge=0, description="força do ruído de turbulência (px/s²)")
    size: list[float] = Field(default_factory=lambda: [10.0, 16.0], description="diâmetro inicial em px")
    size_curve: list[list[float]] = Field(default_factory=lambda: [[0.0, 1.0], [1.0, 0.0]], description="[t, multiplicador]")
    rotation: list[float] = Field(default_factory=lambda: [0.0, 0.0], description="graus iniciais")
    spin: list[float] = Field(default_factory=lambda: [0.0, 0.0], description="graus por segundo")
    align_to_velocity: bool = False
    stretch: float = Field(0.0, ge=0, le=10, description="alonga na direção da velocidade (faíscas)")
    color: list[list[Any]] = Field(default_factory=lambda: [[0.0, "#ffffffff"], [1.0, "#ffffff00"]],
                                   description="gradiente ao longo da vida: [t, '#rrggbbaa']")
    brightness_variation: float = Field(0.0, ge=0, le=1)
    random_color: bool = Field(False, description="cada partícula pega uma cor aleatória do gradiente (alpha segue a vida)")

    @field_validator("speed", "lifetime", "size", "rotation", "spin")
    @classmethod
    def _range(cls, v: list[float]) -> list[float]:
        if len(v) == 1:
            v = [v[0], v[0]]
        if len(v) != 2:
            raise ValueError("use [mín, máx]")
        return [float(min(v)), float(max(v))]

    @field_validator("position", "gravity")
    @classmethod
    def _vec(cls, v: list[float]) -> list[float]:
        if len(v) != 2:
            raise ValueError("use [x, y]")
        return [float(x) for x in v]

    @field_validator("size_curve")
    @classmethod
    def _curve(cls, v: list[list[float]]) -> list[list[float]]:
        if not v:
            return [[0.0, 1.0], [1.0, 1.0]]
        return sorted([[float(a), float(b)] for a, b in v])

    @field_validator("color")
    @classmethod
    def _grad(cls, v: list[list[Any]]) -> list[list[Any]]:
        if not v:
            raise ValueError("gradiente vazio")
        out = []
        for t, c in v:
            parse_color(c)
            out.append([float(t), c])
        return sorted(out, key=lambda x: x[0])


class ParticleSystem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    format: str = "asset-forge-particles/1"
    name: str = "particles"
    description: str = ""
    duration: float = Field(2.0, gt=0, le=60, description="ciclo do loop ou duração do one-shot (s)")
    loop: bool = True
    canvas: list[int] = Field(default_factory=lambda: [256, 256], description="área da prévia/flipbook em px")
    emitters: list[Emitter] = Field(..., min_length=1, max_length=8)
    preset: str | None = None


def parse_color(c: Any) -> tuple[float, float, float, float]:
    if isinstance(c, (list, tuple)):
        v = [float(x) for x in c] + [1.0] * 4
        return v[0], v[1], v[2], v[3]
    s = str(c).lstrip("#")
    if len(s) in (3, 4):
        s = "".join(ch * 2 for ch in s)
    if len(s) == 6:
        s += "ff"
    if len(s) != 8:
        raise ValueError(f"cor inválida: {c}")
    return tuple(int(s[i:i + 2], 16) / 255 for i in (0, 2, 4, 6))  # type: ignore[return-value]


def to_hex(rgba: tuple[float, ...]) -> str:
    return "#" + "".join(f"{round(max(0, min(1, x)) * 255):02x}" for x in rgba)


def sample_gradient(grad: list[list[Any]], t: np.ndarray) -> np.ndarray:
    ts = np.array([g[0] for g in grad], np.float32)
    cs = np.array([parse_color(g[1]) for g in grad], np.float32)
    return np.stack([np.interp(t, ts, cs[:, k]) for k in range(4)], axis=-1)


def sample_curve(curve: list[list[float]], t: np.ndarray) -> np.ndarray:
    return np.interp(t, [c[0] for c in curve], [c[1] for c in curve])


# ---------------------------------------------------------------- texturas

@lru_cache(maxsize=32)
def builtin_texture(name: str, size: int = 64) -> np.ndarray:
    """Máscara branca RGBA float (size×size×4), alpha com a forma."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2
    dx, dy = (x - c) / c, (y - c) / c
    r = np.sqrt(dx * dx + dy * dy)
    if name == "soft":
        a = np.exp(-(r ** 2) * 4.5) * (r < 1)
    elif name == "circle":
        a = np.clip((1 - r) * c / 1.5, 0, 1)
    elif name == "spark":
        a = np.exp(-(dx ** 2) * 2.5 - (dy ** 2) * 60)
    elif name == "star":
        a = np.clip(np.exp(-(dx ** 2) * 40) * np.exp(-(dy ** 2) * 2) + np.exp(-(dy ** 2) * 40) * np.exp(-(dx ** 2) * 2)
                    + np.exp(-(r ** 2) * 30), 0, 1)
    elif name == "smoke":
        rng = np.random.default_rng(3)
        n = np.zeros_like(r)
        for k, amp in ((4, 0.5), (8, 0.3), (16, 0.2)):
            g = rng.random((k + 1, k + 1)).astype(np.float32)
            gi = np.array(Image.fromarray((g * 255).astype(np.uint8)).resize((size, size), Image.Resampling.BICUBIC), np.float32) / 255
            n += gi * amp
        a = np.clip(np.exp(-(r ** 2) * 3.0) * (0.55 + n * 0.9) - 0.15, 0, 1) * (r < 1)
    elif name == "ring":
        a = np.exp(-((r - 0.75) ** 2) * 120)
    elif name == "square":
        a = np.ones_like(r)
    elif name == "flare":
        a = np.clip(np.exp(-(r ** 2) * 2.5) * 0.5 + np.exp(-(r ** 2) * 25), 0, 1) * (r < 1)
    else:
        raise ValueError(f"textura desconhecida: {name}")
    out = np.ones((size, size, 4), np.float32)
    out[..., 3] = a.astype(np.float32)
    return out


def texture_image(name: str, size: int = 64) -> Image.Image:
    return Image.fromarray((builtin_texture(name, size) * 255).astype(np.uint8), "RGBA")


# ---------------------------------------------------------------- simulação

def _rand(rng: np.random.Generator, lo_hi: list[float], n: int) -> np.ndarray:
    return rng.uniform(lo_hi[0], lo_hi[1], n).astype(np.float32) if lo_hi[1] > lo_hi[0] else np.full(n, lo_hi[0], np.float32)


class _EmitterState:
    def __init__(self, em: Emitter, seed: int):
        self.em = em
        self.rng = np.random.default_rng(seed)
        self.pos = np.zeros((0, 2), np.float32)
        self.vel = np.zeros((0, 2), np.float32)
        self.age = np.zeros(0, np.float32)
        self.life = np.zeros(0, np.float32)
        self.size = np.zeros(0, np.float32)
        self.rot = np.zeros(0, np.float32)
        self.spin = np.zeros(0, np.float32)
        self.bright = np.zeros(0, np.float32)
        self.hue = np.zeros(0, np.float32)
        self.acc = 0.0

    def spawn(self, n: int) -> None:
        em, rng = self.em, self.rng
        n = min(n, em.max_particles - len(self.age))
        if n <= 0:
            return
        ox, oy = em.position
        if em.shape == "circle":
            ang, rad = rng.uniform(0, 2 * math.pi, n), em.radius * np.sqrt(rng.uniform(0, 1, n))
            off = np.stack([np.cos(ang) * rad, np.sin(ang) * rad], 1)
        elif em.shape == "ring":
            ang = rng.uniform(0, 2 * math.pi, n)
            off = np.stack([np.cos(ang) * em.radius, np.sin(ang) * em.radius], 1)
        elif em.shape == "box":
            off = np.stack([rng.uniform(-em.width / 2, em.width / 2, n), rng.uniform(-em.height / 2, em.height / 2, n)], 1)
        elif em.shape == "line":
            off = np.stack([rng.uniform(-em.width / 2, em.width / 2, n), np.zeros(n)], 1)
        else:
            off = np.zeros((n, 2))
        pos = (off + [ox, oy]).astype(np.float32)
        d = np.radians(em.direction + rng.uniform(-em.spread, em.spread, n))
        sp = _rand(rng, em.speed, n)
        vel = np.stack([np.cos(d) * sp, np.sin(d) * sp], 1).astype(np.float32)
        self.pos = np.concatenate([self.pos, pos])
        self.vel = np.concatenate([self.vel, vel])
        self.age = np.concatenate([self.age, np.zeros(n, np.float32)])
        self.life = np.concatenate([self.life, np.maximum(0.01, _rand(rng, em.lifetime, n))])
        self.size = np.concatenate([self.size, _rand(rng, em.size, n)])
        self.rot = np.concatenate([self.rot, _rand(rng, em.rotation, n)])
        self.spin = np.concatenate([self.spin, _rand(rng, em.spin, n)])
        self.bright = np.concatenate([self.bright, 1 - rng.uniform(0, em.brightness_variation, n).astype(np.float32)])
        self.hue = np.concatenate([self.hue, rng.uniform(0, 1, n).astype(np.float32)])

    def step(self, t: float, dt: float, sys_duration: float, loop: bool) -> None:
        em = self.em
        # emissão contínua
        local = t - em.start_delay
        span = em.emit_duration if em.emit_duration is not None else sys_duration
        active = local >= 0 and (loop or local < span)
        if active and em.rate > 0:
            if not loop or (local % sys_duration) < span:
                self.acc += em.rate * dt
                k = int(self.acc)
                self.acc -= k
                self.spawn(k)
        # bursts (repetem a cada ciclo no loop)
        for b in em.bursts:
            bt = em.start_delay + b.time
            if loop:
                phase_prev, phase = (t - dt - bt) % sys_duration, (t - bt) % sys_duration
                if t - bt >= 0 and phase < phase_prev:
                    self.spawn(b.count)
            elif t - dt < bt <= t:
                self.spawn(b.count)
        if not len(self.age):
            return
        acc = np.zeros_like(self.vel)
        acc += np.array(em.gravity, np.float32)
        if em.radial_accel or em.tangential_accel:
            rel = self.pos - np.array(em.position, np.float32)
            dist = np.linalg.norm(rel, axis=1, keepdims=True) + 1e-4
            nrm = rel / dist
            acc += nrm * em.radial_accel + np.stack([-nrm[:, 1], nrm[:, 0]], 1) * em.tangential_accel
        if em.turbulence:
            p = self.pos * 0.02
            acc += np.stack([np.sin(p[:, 1] * 1.7 + t * 1.3) + np.sin(p[:, 0] * 0.9 - t), np.cos(p[:, 0] * 1.3 - t * 1.1) + np.sin(p[:, 1] * 0.7 + t)], 1) * em.turbulence
        self.vel += acc * dt
        if em.drag:
            self.vel *= max(0.0, 1 - em.drag * dt)
        self.pos += self.vel * dt
        self.rot += self.spin * dt
        self.age += dt
        keep = self.age < self.life
        if not keep.all():
            for name in ("pos", "vel", "age", "life", "size", "rot", "spin", "bright", "hue"):
                setattr(self, name, getattr(self, name)[keep])


def simulate(ps: ParticleSystem, frames: int = 24, *, fps: float | None = None, seed: int = 1, prewarm: bool | None = None,
             substeps: int = 4) -> list[list[dict[str, np.ndarray]]]:
    """Estados por quadro: lista (quadros) de listas (emissores) com arrays de partículas vivas."""
    states = [_EmitterState(em, seed * 1000 + i) for i, em in enumerate(ps.emitters)]
    fps = fps or frames / ps.duration
    dt = 1.0 / (fps * substeps)
    t = 0.0
    if prewarm if prewarm is not None else ps.loop:  # sistemas em loop começam "cheios"
        max_life = max(em.lifetime[1] + em.start_delay for em in ps.emitters)
        n = int(max(ps.duration, max_life) / dt)
        for _ in range(n):
            for s in states:
                s.step(t, dt, ps.duration, ps.loop)
            t += dt
    out = []
    for _ in range(frames):
        # avança antes de fotografar: no quadro 0 os bursts de t=0 já aparecem
        for _ in range(substeps):
            for s in states:
                s.step(t, dt, ps.duration, ps.loop)
            t += dt
        snap = []
        for s in states:
            tl = np.clip(s.age / s.life, 0, 1)
            snap.append({"pos": s.pos.copy(), "vel": s.vel.copy(), "t": tl, "size": s.size.copy(), "rot": s.rot.copy(),
                         "bright": s.bright.copy(), "hue": s.hue.copy()})
        out.append(snap)
    return out


# ---------------------------------------------------------------- renderização

def _sprite(tex: np.ndarray, w: int, h: int, angle: float) -> np.ndarray:
    img = Image.fromarray((tex * 255).astype(np.uint8), "RGBA").resize((max(1, w), max(1, h)), Image.Resampling.BILINEAR)
    if abs(angle) > 0.5:
        img = img.rotate(angle, resample=Image.Resampling.BILINEAR, expand=True)
    return np.asarray(img, np.float32) / 255


def render(ps: ParticleSystem, frames: int = 24, *, size: tuple[int, int] | None = None, seed: int = 1,
           textures: dict[str, np.ndarray] | None = None) -> list[Image.Image]:
    """Quadros RGBA (alpha reto) do sistema. `textures`: texturas extras (asset:<slug>) já carregadas."""
    W, H = size or tuple(ps.canvas)
    sx, sy = W / ps.canvas[0], H / ps.canvas[1]
    states = simulate(ps, frames, seed=seed)
    cache: dict[tuple, np.ndarray] = {}
    out = []
    for snap in states:
        prem = np.zeros((H, W, 3), np.float32)  # cor pré-multiplicada
        alpha = np.zeros((H, W), np.float32)
        for em, st in zip(ps.emitters, snap):
            if not len(st["t"]):
                continue
            tex = (textures or {}).get(em.texture) if em.texture.startswith("asset:") else builtin_texture(em.texture)
            if tex is None:
                tex = builtin_texture("soft")
            cols = sample_gradient(em.color, st["t"])
            if em.random_color:  # rgb de um ponto aleatório do gradiente; alpha pela vida
                cols[:, :3] = sample_gradient(em.color, st["hue"] * 0.95)[:, :3]
            mult = sample_curve(em.size_curve, st["t"])
            speed = np.linalg.norm(st["vel"], axis=1)
            for i in range(len(st["t"])):
                d = float(st["size"][i] * mult[i] * (sx + sy) / 2)
                if d < 0.5 or cols[i, 3] <= 0.003:
                    continue
                w = h = d
                ang = float(st["rot"][i])
                if em.align_to_velocity or em.stretch:
                    ang = math.degrees(math.atan2(st["vel"][i, 1], st["vel"][i, 0]))
                    if em.stretch:
                        w = d * (1 + em.stretch * float(speed[i]) / 100)
                key = (em.texture, int(round(w)), int(round(h)), int(round(ang / 10)) * 10)
                spr = cache.get(key)
                if spr is None:
                    spr = cache[key] = _sprite(tex, key[1], key[2], key[3])
                cx = W / 2 + float(st["pos"][i, 0]) * sx
                cy = H / 2 - float(st["pos"][i, 1]) * sy
                x0, y0 = int(round(cx - spr.shape[1] / 2)), int(round(cy - spr.shape[0] / 2))
                xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + spr.shape[1]), min(H, y0 + spr.shape[0])
                if xa >= xb or ya >= yb:
                    continue
                s = spr[ya - y0:yb - y0, xa - x0:xb - x0]
                c = cols[i]
                a = s[..., 3] * c[3]
                rgb = s[..., :3] * c[:3] * float(st["bright"][i])
                if em.blend == "add":
                    prem[ya:yb, xa:xb] += rgb * a[..., None]
                    alpha[ya:yb, xa:xb] = np.maximum(alpha[ya:yb, xa:xb], a)
                else:
                    prem[ya:yb, xa:xb] = rgb * a[..., None] + prem[ya:yb, xa:xb] * (1 - a[..., None])
                    alpha[ya:yb, xa:xb] = a + alpha[ya:yb, xa:xb] * (1 - a)
        # aditivo pode passar de 1: leva o excesso para o alpha (fica brilhante sobre fundo escuro)
        lum = prem.max(axis=2)
        alpha = np.clip(np.maximum(alpha, lum), 0, 1)
        rgb = np.where(alpha[..., None] > 1e-4, prem / np.maximum(alpha[..., None], 1e-4), 0)
        img = np.concatenate([np.clip(rgb, 0, 1), alpha[..., None]], axis=2)
        out.append(Image.fromarray((img * 255).astype(np.uint8), "RGBA"))
    return out


def tint_gradient(grad: list[list[Any]], rgb: list[float], keep_white: bool = True) -> list[list[Any]]:
    """Recolore um gradiente mantendo alpha e brilho relativo (branco quente continua claro se keep_white)."""
    out = []
    for t, c in grad:
        r, g, b, a = parse_color(c)
        lum = max(r, g, b)
        sat = lum - min(r, g, b)
        if keep_white and sat < 0.15 and lum > 0.85:
            out.append([t, c])
            continue
        out.append([t, to_hex((rgb[0] * lum, rgb[1] * lum, rgb[2] * lum, a))])
    return out
