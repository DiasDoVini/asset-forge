"""Renderização de shaders no servidor (moderngl, contexto OpenGL sem janela).

Compila como GLSL ES 3.00 — mesmas regras estritas do WebGL2 da UI e próximas das do Godot — então um shader
que passa aqui roda na prévia do navegador e exporta sem surpresas.
"""
from __future__ import annotations

import re
import numpy as np
from PIL import Image

from .shaders import VERTEX_ES, ShaderDef, error_line_offset, glsl_es


class ShaderCompileError(ValueError):
    """Erro de compilação com linhas relativas ao código do efeito."""


_ctx = None


def _context():
    global _ctx
    if _ctx is None:
        import moderngl

        _ctx = moderngl.create_standalone_context(require=330)
    return _ctx


def available() -> tuple[bool, str]:
    try:
        _context()
        return True, ""
    except Exception as e:  # sem GPU/driver
        return False, f"OpenGL indisponível para renderizar shaders: {e}"


def _clean_error(msg: str, sd: ShaderDef) -> str:
    off = error_line_offset(sd)
    total = sd.code.count("\n") + 1

    def fix(m: re.Match) -> str:  # drivers que ignoram #line: converte para a linha do código do efeito
        n = int(m.group(2))
        if n > total:
            n -= off - 1
        return f"{m.group(1)}{n}{m.group(3)}"

    msg = re.sub(r"(0\()(\d+)(\))", fix, msg)
    msg = re.sub(r"(ERROR: 0:)(\d+)(:)", fix, msg)
    return "\n".join(ln for ln in msg.splitlines() if ln.strip() and "fragment_shader" not in ln)[:4000]


def compile_check(sd: ShaderDef) -> None:
    ctx = _context()
    try:
        prog = ctx.program(vertex_shader=VERTEX_ES, fragment_shader=glsl_es(sd))
        prog.release()
    except Exception as e:
        raise ShaderCompileError(_clean_error(str(e), sd)) from None


def _set(prog, name: str, value) -> None:
    if name in prog:
        prog[name].value = value


def render_frames(sd: ShaderDef, size: tuple[int, int] = (256, 256), frames: int = 16, *,
                  duration: float | None = None, texture: Image.Image | None = None,
                  start: float = 0.0) -> list[Image.Image]:
    """Renderiza `frames` quadros cobrindo `duration` segundos (padrão: sd.duration). RGBA com alpha reto."""
    ctx = _context()
    w, h = size
    duration = duration or sd.duration
    try:
        prog = ctx.program(vertex_shader=VERTEX_ES, fragment_shader=glsl_es(sd))
    except Exception as e:
        raise ShaderCompileError(_clean_error(str(e), sd)) from None
    quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4")
    vbo = ctx.buffer(quad.tobytes())
    vao = ctx.vertex_array(prog, [(vbo, "2f", "aPos")])
    fbo = ctx.simple_framebuffer((w, h), components=4, dtype="f1")
    tex = None
    if texture is not None:
        t = texture.convert("RGBA").transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        tex = ctx.texture(t.size, 4, t.tobytes())
        tex.build_mipmaps()
        tex.use(0)
        _set(prog, "uTexture", 0)
    _set(prog, "uHasTexture", 1.0 if tex is not None else 0.0)
    _set(prog, "uResolution", (float(w), float(h)))
    for u in sd.uniforms:
        v = u.values()
        _set(prog, u.name, v[0] if len(v) == 1 else tuple(v))
    out = []
    try:
        for i in range(frames):
            t = start + duration * i / frames
            _set(prog, "uTime", float(t))
            # one-shot: 0 → 1 inclusive no último quadro; loop: fase do ciclo (igual ao engine)
            _set(prog, "uProgress", float(i / max(1, frames - 1)) if sd.one_shot else float((t / duration) % 1.0))
            fbo.use()
            fbo.clear(0, 0, 0, 0)
            vao.render(mode=5)  # TRIANGLE_STRIP
            img = Image.frombytes("RGBA", (w, h), fbo.read(components=4, alignment=1))
            out.append(img.transpose(Image.Transpose.FLIP_TOP_BOTTOM))
    finally:
        for r in (vao, vbo, fbo, prog, tex):
            if r is not None:
                r.release()
    return out


def on_background(img: Image.Image, blend: str = "alpha", bg=(18, 20, 28)) -> Image.Image:
    """Compõe sobre fundo escuro como o engine faria (aditivo ou alpha)."""
    a = np.asarray(img.convert("RGBA"), np.float32) / 255
    base = np.ones(a.shape[:2] + (3,), np.float32) * (np.array(bg, np.float32) / 255)
    if blend == "add":
        rgb = base + a[..., :3] * a[..., 3:4]
    else:
        rgb = base * (1 - a[..., 3:4]) + a[..., :3] * a[..., 3:4]
    return Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8), "RGB")


def contact_sheet(frames: list[Image.Image], cols: int = 4, blend: str = "alpha") -> Image.Image:
    w, h = frames[0].size
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), (18, 20, 28))
    for i, f in enumerate(frames):
        sheet.paste(on_background(f, blend), ((i % cols) * w, (i // cols) * h))
    return sheet


def placeholder_sprite(size: int = 256) -> Image.Image:
    """Sprite genérico (escudo/gema) para prévias de efeitos que usam textura sem asset de entrada."""
    from PIL import ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    poly = [(s * 0.5, s * 0.12), (s * 0.8, s * 0.3), (s * 0.74, s * 0.68), (s * 0.5, s * 0.9), (s * 0.26, s * 0.68), (s * 0.2, s * 0.3)]
    d.polygon(poly, fill=(70, 120, 210, 255), outline=(20, 30, 60, 255), width=max(2, s // 64))
    d.polygon([(s * 0.5, s * 0.2), (s * 0.7, s * 0.33), (s * 0.5, s * 0.5), (s * 0.3, s * 0.33)], fill=(150, 200, 255, 255))
    d.ellipse([s * 0.42, s * 0.55, s * 0.58, s * 0.71], fill=(250, 210, 90, 255))
    return img
