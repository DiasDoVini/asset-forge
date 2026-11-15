"""Animações de sprite: formato em disco, alinhamento, sprite sheet, GIF e JSON (Aseprite).

Uma animação é um asset `kind="animation"` (modalidade 2d). Pasta da versão:

    animation.json      primary  — metadados + lista de frames (formato abaixo)
    frames/000.png      part     — um PNG RGBA por frame, todos do mesmo tamanho
    sheet.png           texture  — tira horizontal com todos os frames
    preview.gif         preview  — GIF animado (galeria/UI)

animation.json:
    {"base": "knight@v001", "action": "...", "preset": "walk", "method": "video",
     "fps": 10, "loop": true, "anchor": "bottom", "size": [w, h], "pixel": {...} | null,
     "frames": [{"file": "frames/000.png", "duration": 100}, ...]}

Inspirado no Aseprite (frames com duração própria, tags, export sheet+JSON) e em sheets de
RPG (uma linha por animação de um personagem).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image

from forge_core import OutputFile

Anchor = Literal["bottom", "center", "union", "none"]

ANIM_FILE = "animation.json"


# ---------------------------------------------------------------- presets

@dataclass(frozen=True)
class Preset:
    title: str
    motion: str           # descrição do movimento (inglês: entendida por todos os modelos)
    fps: int
    loop: bool
    anchor: Anchor
    frames: int
    phases: tuple[str, ...] = ()  # poses-chave (método "edit"), interpoladas para N frames


PRESETS: dict[str, Preset] = {
    "idle": Preset("Parado (idle)", "idle stance, subtle breathing, chest gently rising and falling, slight sway, blinking",
                   6, True, "bottom", 4,
                   ("neutral standing pose", "chest slightly raised, inhaling", "neutral standing pose", "body slightly lowered, exhaling")),
    "walk": Preset("Andar", "walking in place, natural walk cycle, arms swinging opposite to the legs", 10, True, "bottom", 8,
                   ("contact pose: front leg forward heel down, back leg extended, arms opposite",
                    "down pose: body lowest, front knee bent taking the weight",
                    "passing pose: back leg lifted passing under the body, supporting leg straight",
                    "up pose: body highest, pushing off with the back foot",
                    "contact pose with the other leg in front, arms swapped",
                    "down pose on the other leg",
                    "passing pose with the other leg lifted",
                    "up pose pushing off with the other foot")),
    "run": Preset("Correr", "running in place, fast run cycle, body leaning forward, arms pumping", 12, True, "bottom", 8,
                  ("contact: front foot striking the ground, leaning forward",
                   "recoil: knee bent absorbing impact",
                   "passing: back leg swinging forward high",
                   "flight: both feet off the ground, legs spread",
                   "contact with the other foot",
                   "recoil on the other leg",
                   "passing with the other leg",
                   "flight with legs swapped")),
    "attack": Preset("Ataque", "performing a melee weapon attack: anticipation wind-up, fast strike, follow-through, recovery",
                     12, False, "bottom", 6,
                     ("ready stance", "anticipation: weapon pulled back, body coiled", "strike: weapon swinging fast forward",
                      "impact: arm fully extended", "follow-through: weapon past the target", "recovery back to ready stance")),
    "jump": Preset("Pulo", "jumping: crouch, take off, rise, apex in the air, fall, land", 10, False, "union", 6,
                   ("crouch preparing to jump", "take off, legs extending", "rising in the air, knees tucked",
                    "apex of the jump", "falling, legs reaching down", "landing, knees bent absorbing")),
    "hurt": Preset("Dano", "getting hit and recoiling backwards in pain, then recovering", 10, False, "bottom", 4,
                   ("neutral", "hit: head and torso jerked back, pained face", "recoiling further back", "recovering to neutral")),
    "death": Preset("Morte", "dying: staggering, collapsing and falling to the ground, lying still", 8, False, "union", 6,
                    ("hit and staggering", "knees buckling", "falling down", "hitting the ground", "lying on the ground", "lying still")),
    "cast": Preset("Magia", "casting a spell: raising hands, magical energy gathering, releasing the spell forward", 10, False, "bottom", 6,
                   ("neutral", "hands raising", "energy gathering in the hands, glowing", "hands thrust forward releasing",
                    "spell released, glow fading", "back to neutral")),
    "spin": Preset("Girar (item)", "rotating 360 degrees around its vertical axis, like a spinning collectible item", 12, True, "center", 8,
                   ("front view", "rotated 45 degrees", "side view", "rotated 135 degrees", "back view", "rotated 225 degrees",
                    "other side view", "rotated 315 degrees")),
    "float": Preset("Flutuar (item)", "floating gently up and down with a soft glow, collectible item bobbing", 8, True, "union", 6,
                    ("at rest", "slightly up", "highest", "slightly up", "at rest", "slightly down")),
}


def phases_for(preset: Preset | None, action: str, n: int) -> list[str]:
    """Descrição da pose de cada frame (método por frame)."""
    if preset and preset.phases:
        k = len(preset.phases)
        return [preset.phases[min(k - 1, int(i * k / n))] for i in range(n)]
    return [f"pose {i + 1} of {n}, at {round(100 * i / n)}% of the motion" for i in range(n)]


# ---------------------------------------------------------------- formato

@dataclass
class Animation:
    frames: list[Image.Image]
    fps: int = 10
    loop: bool = True
    durations: list[int] | None = None   # ms por frame (None = 1000/fps)
    meta: dict[str, Any] = field(default_factory=dict)  # base, action, preset, method, anchor, pixel...

    def __post_init__(self) -> None:
        self.frames = [f.convert("RGBA") for f in self.frames]
        if self.durations is None or len(self.durations) != len(self.frames):
            self.durations = [round(1000 / max(1, self.fps))] * len(self.frames)

    @property
    def size(self) -> tuple[int, int]:
        return self.frames[0].size if self.frames else (0, 0)

    def save(self, d: Path) -> list[OutputFile]:
        d = Path(d)
        (d / "frames").mkdir(parents=True, exist_ok=True)
        entries = []
        for i, (f, dur) in enumerate(zip(self.frames, self.durations)):
            rel = f"frames/{i:03d}.png"
            f.save(d / rel)
            entries.append({"file": rel, "duration": int(dur)})
        data = {**self.meta, "fps": self.fps, "loop": self.loop, "size": list(self.size), "frames": entries}
        (d / ANIM_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        sheet, _ = build_sheet(self.frames, layout="row")
        sheet.save(d / "sheet.png")
        save_gif(self.frames, self.durations, d / "preview.gif", scale=_preview_scale(self.size), loop=self.loop)
        out = [OutputFile(d / ANIM_FILE, "primary", "application/json"), OutputFile(d / "sheet.png", "texture"),
               OutputFile(d / "preview.gif", "preview")]
        out += [OutputFile(d / e["file"], "part") for e in entries]
        return out

    @classmethod
    def load(cls, d: Path) -> "Animation":
        d = Path(d)
        data = json.loads((d / ANIM_FILE).read_text(encoding="utf-8"))
        frames = [Image.open(d / e["file"]).convert("RGBA") for e in data["frames"]]
        meta = {k: v for k, v in data.items() if k not in ("fps", "loop", "size", "frames")}
        return cls(frames, int(data.get("fps", 10)), bool(data.get("loop", True)),
                   [int(e.get("duration", 100)) for e in data["frames"]], meta)


def _preview_scale(size: tuple[int, int]) -> int:
    return max(1, 192 // max(1, max(size)))


# ---------------------------------------------------------------- alinhamento

def _alpha(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"))[..., 3]


def _bbox(a: np.ndarray, thr: int = 16) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(a > thr)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _centroid_x(a: np.ndarray) -> float:
    w = (a > 16).astype(np.float64)
    s = w.sum()
    return float((w.sum(axis=0) * np.arange(a.shape[1])).sum() / s) if s else a.shape[1] / 2


def align_frames(frames: list[Image.Image], anchor: Anchor = "bottom", pad: int = 4) -> list[Image.Image]:
    """Põe todos os frames no mesmo canvas.

    bottom: pés na mesma linha de base e centro de massa horizontal fixo ("in place").
    center: centro de massa fixo nos dois eixos (itens girando).
    union:  recorte comum a todos (preserva deslocamento real: pulo, queda) — câmera fixa.
    none:   só padroniza o tamanho do canvas.
    """
    frames = [f.convert("RGBA") for f in frames]
    if anchor == "none" or not frames:
        w = max(f.width for f in frames)
        h = max(f.height for f in frames)
        out = []
        for f in frames:
            c = Image.new("RGBA", (w, h))
            c.paste(f, ((w - f.width) // 2, h - f.height))
            out.append(c)
        return out
    alphas = [_alpha(f) for f in frames]
    boxes = [_bbox(a) for a in alphas]
    if anchor == "union":
        valid = [b for b in boxes if b]
        if not valid:
            return frames
        l, t = min(b[0] for b in valid), min(b[1] for b in valid)
        r, b_ = max(b[2] for b in valid), max(b[3] for b in valid)
        return [_pad(f.crop((l, t, r, b_)), pad) for f in frames]

    # bottom / center: recorta cada frame e alinha pelo âncora
    crops, anchors = [], []
    for f, a, box in zip(frames, alphas, boxes):
        if not box:
            crops.append(Image.new("RGBA", (1, 1)))
            anchors.append((0.5, 1.0 if anchor == "bottom" else 0.5))
            continue
        cx = _centroid_x(a) - box[0]
        c = f.crop(box)
        crops.append(c)
        if anchor == "bottom":
            anchors.append((cx, float(c.height)))
        else:
            ys = np.nonzero(a[box[1]:box[3], box[0]:box[2]] > 16)[0]
            anchors.append((cx, float(ys.mean()) if len(ys) else c.height / 2))
    left = max(ax for ax, _ in anchors)
    right = max(c.width - ax for c, (ax, _) in zip(crops, anchors))
    if anchor == "bottom":
        up = max(ay for _, ay in anchors)
        w, h = math.ceil(2 * max(left, right)), math.ceil(up)
        out = []
        for c, (ax, ay) in zip(crops, anchors):
            canvas = Image.new("RGBA", (w, h))
            canvas.paste(c, (round(w / 2 - ax), round(h - ay)))
            out.append(_pad(canvas, pad))
        return out
    top = max(ay for _, ay in anchors)
    bottom = max(c.height - ay for c, (_, ay) in zip(crops, anchors))
    w, h = math.ceil(2 * max(left, right)), math.ceil(2 * max(top, bottom))
    out = []
    for c, (ax, ay) in zip(crops, anchors):
        canvas = Image.new("RGBA", (w, h))
        canvas.paste(c, (round(w / 2 - ax), round(h / 2 - ay)))
        out.append(_pad(canvas, pad))
    return out


def _pad(img: Image.Image, pad: int) -> Image.Image:
    if pad <= 0:
        return img
    c = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad))
    c.paste(img, (pad, pad))
    return c


def fit_canvas(frames: list[Image.Image], size: tuple[int, int], anchor: Anchor = "bottom") -> list[Image.Image]:
    """Encaixa frames (já alinhados entre si) num canvas fixo, ex.: o tamanho de uma animação existente."""
    w, h = size
    out = []
    for f in frames:
        k = min(1.0, w / f.width, h / f.height)
        g = f.resize((max(1, round(f.width * k)), max(1, round(f.height * k))), Image.Resampling.LANCZOS) if k < 1 else f
        c = Image.new("RGBA", (w, h))
        y = h - g.height if anchor == "bottom" else (h - g.height) // 2
        c.paste(g, ((w - g.width) // 2, y))
        out.append(c)
    return out


def match_height(frames: list[Image.Image], target_h: int) -> list[Image.Image]:
    """Escala uniforme para que o maior frame tenha `target_h` de altura de conteúdo."""
    hs = [b[3] - b[1] for b in (_bbox(_alpha(f)) for f in frames) if b]
    if not hs:
        return frames
    k = target_h / max(hs)
    if abs(k - 1) < 0.02:
        return frames
    return [f.resize((max(1, round(f.width * k)), max(1, round(f.height * k))), Image.Resampling.LANCZOS) for f in frames]


# ---------------------------------------------------------------- sheet / json / gif

def build_sheet(frames: list[Image.Image], layout: str = "row", columns: int | None = None, padding: int = 0,
                scale: int = 1) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """Grade de frames (todos do mesmo tamanho). Retorna (imagem, [x, y, w, h] por frame)."""
    n = len(frames)
    fw, fh = frames[0].width * scale, frames[0].height * scale
    if layout == "row":
        cols = n
    elif layout == "column":
        cols = 1
    else:
        cols = columns or math.ceil(math.sqrt(n))
    cols = max(1, min(cols, n))
    rows = math.ceil(n / cols)
    sheet = Image.new("RGBA", (cols * fw + (cols - 1) * padding, rows * fh + (rows - 1) * padding))
    rects = []
    for i, f in enumerate(frames):
        x, y = (i % cols) * (fw + padding), (i // cols) * (fh + padding)
        g = f if scale == 1 else f.resize((fw, fh), Image.Resampling.NEAREST)
        sheet.paste(g, (x, y))
        rects.append((x, y, fw, fh))
    return sheet, rects


def aseprite_json(image_name: str, sheet_size: tuple[int, int], rects: list[tuple[int, int, int, int]],
                  durations: list[int], tags: list[dict[str, Any]], frame_prefix: str = "frame",
                  slices: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """JSON no formato "array" do Aseprite (lido por Phaser `load.aseprite`, Godot/Unity importers)."""
    frames = []
    for i, ((x, y, w, h), dur) in enumerate(zip(rects, durations)):
        frames.append({
            "filename": f"{frame_prefix} {i}.png",
            "frame": {"x": x, "y": y, "w": w, "h": h},
            "rotated": False, "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h},
            "sourceSize": {"w": w, "h": h},
            "duration": int(dur),
        })
    return {
        "frames": frames,
        "meta": {
            "app": "Asset Forge", "version": "1.0", "image": image_name, "format": "RGBA8888",
            "size": {"w": sheet_size[0], "h": sheet_size[1]}, "scale": "1",
            "frameTags": [{"name": t["name"], "from": t["from"], "to": t["to"],
                           "direction": t.get("direction", "forward")} for t in tags],
            "layers": [{"name": "Layer", "opacity": 255, "blendMode": "normal"}],
            "slices": [_slice_json(s) for s in slices or []],
        },
    }


def _slice_json(s: dict[str, Any]) -> dict[str, Any]:
    """Fatia no formato do JSON do Aseprite (uma chave, no quadro 0)."""
    key: dict[str, Any] = {"frame": int(s.get("frame", 0)),
                           "bounds": {"x": s["x"], "y": s["y"], "w": s["w"], "h": s["h"]}}
    if s.get("center"):
        key["center"] = dict(s["center"])
    if s.get("pivot"):
        key["pivot"] = dict(s["pivot"])
    return {"name": s["name"], "color": s.get("color", "#0000ffff"), "keys": [key]}


def texturepacker_json(image_name: str, sheet_size: tuple[int, int], rects: list[tuple[int, int, int, int]],
                       names: list[str]) -> dict[str, Any]:
    """TexturePacker "JSON (Hash)" — Phaser atlas, PixiJS, Cocos."""
    return {
        "frames": {n: {"frame": {"x": x, "y": y, "w": w, "h": h}, "rotated": False, "trimmed": False,
                       "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h}, "sourceSize": {"w": w, "h": h}}
                   for n, (x, y, w, h) in zip(names, rects)},
        "meta": {"app": "Asset Forge", "image": image_name, "format": "RGBA8888",
                 "size": {"w": sheet_size[0], "h": sheet_size[1]}, "scale": "1"},
    }


def _hex_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def save_gif(frames: list[Image.Image], durations: list[int], path: Path, scale: int = 1, loop: bool = True,
             background: str | None = None) -> Path:
    """GIF animado. Sem `background`: transparência binária (alpha >= 128) com disposal=2."""
    out = []
    for f in frames:
        f = f.convert("RGBA")
        if scale > 1:
            f = f.resize((f.width * scale, f.height * scale), Image.Resampling.NEAREST)
        if background:
            bg = Image.new("RGBA", f.size, _hex_rgb(background) + (255,))
            bg.alpha_composite(f)
            out.append(bg.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT))
            continue
        a = np.asarray(f)[..., 3]
        q = f.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        idx = np.asarray(q).copy()
        idx[a < 128] = 255
        p = Image.fromarray(idx, "P")
        pal = (q.getpalette() or [])[: 255 * 3]
        pal += [0] * (255 * 3 - len(pal)) + [0, 0, 0]
        p.putpalette(pal)
        p.info["transparency"] = 255
        out.append(p)
    kwargs: dict[str, Any] = {"save_all": True, "append_images": out[1:], "duration": [max(20, int(d)) for d in durations],
                              "disposal": 2, "optimize": False}
    if loop:
        kwargs["loop"] = 0
    if not background:
        kwargs["transparency"] = 255
    out[0].save(path, format="GIF", **kwargs)
    return Path(path)


def find_cycle(frames: list[Image.Image], min_period: int = 6, warmup_frac: float = 0.2,
               size: tuple[int, int] = (64, 64)) -> tuple[int, int, float] | None:
    """Acha o melhor trecho cíclico [s, s+P) num vídeo: o frame s+P volta a ser quase igual a s,
    com bastante movimento no meio. Pula o arranque (o vídeo começa parado na pose base).
    Retorna (s, P, qualidade) ou None se não houver movimento suficiente."""
    n = len(frames)
    if n < min_period * 2:
        return None
    small = np.stack([np.asarray(f.convert("RGBA").resize(size, Image.Resampling.BILINEAR), np.float32) for f in frames])
    # considera cor * alpha (fundo transparente não conta)
    a = small[..., 3:4] / 255.0
    feat = (small[..., :3] * a).reshape(n, -1)
    D = np.abs(feat[:, None, :] - feat[None, :, :]).mean(-1)
    motion_scale = float(np.median(D[np.triu_indices(n, 1)])) + 1e-6
    warm = int(n * warmup_frac)
    best: tuple[float, int, int] | None = None
    for P in range(min_period, n - warm):
        for s in range(warm, n - P):
            seg = D[s, s + 1:s + P]
            motion = float(seg.max())
            if motion < 0.5 * motion_scale:  # trecho parado
                continue
            closure = D[s, s + P] / motion          # 0 = fecha perfeito
            score = closure * (1 + 0.015 * P)       # prefere um ciclo (não dois)
            if best is None or score < best[0]:
                best = (score, s, P)
    if best is None:
        return None
    score, s, P = best
    return s, P, float(1 - min(1.0, score))


def sample_indices(total: int, n: int, loop: bool) -> list[int]:
    """Índices de n frames espaçados em `total` (num loop, o último frame == primeiro é ignorado)."""
    span = total - 1 if loop else total
    if n >= span:
        return list(range(span if loop else total))
    if loop:
        return [round(i * span / n) for i in range(n)]
    return [round(i * (total - 1) / max(1, n - 1)) for i in range(n)]
