"""Importadores de VFX: shader GLSL (`vec4 effect(vec2 uv)`), shader.json e particles.json.

Gravam o mesmo que os pipelines de VFX (definição + prévia renderizada), então viewer, edição e exportação para
Godot/Unity/XNA funcionam. O shader é compilado na GPU na importação: erro de compilação recusa o arquivo.
"""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from forge_core import ImportContext, Importer, NotApplicable, OutputFile

from . import render
from .particles import ParticleSystem
from .pipelines import write_particles, write_shader
from .shaders import RESERVED, ShaderDef, Uniform

def _read_text(path) -> str:
    """UTF-8 (com ou sem BOM); arquivos salvos no Windows em cp1252 também são aceitos."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


_UNIFORM_RE = re.compile(
    r"^[ \t]*uniform[ \t]+(?:(?:lowp|mediump|highp)[ \t]+)?(float|vec2|vec3|vec4|bool)[ \t]+(\w+)[ \t]*;[^\n]*\n?", re.M)


def _shader_from_glsl(code: str, name: str) -> ShaderDef:
    if not re.search(r"vec4\s+effect\s*\(\s*vec2\s+\w+\s*\)", code):
        raise NotApplicable()
    uniforms: list[Uniform] = []

    def take(m: re.Match[str]) -> str:
        typ, uname = m.group(1), m.group(2)
        if uname in RESERVED:
            return ""
        if typ in ("vec3", "vec4"):
            uniforms.append(Uniform(name=uname, type="color", default=[1.0, 1.0, 1.0, 1.0], label=uname))
        elif typ == "vec2":
            uniforms.append(Uniform(name=uname, type="vec2", default=[0.5, 0.5], label=uname))
        else:
            uniforms.append(Uniform(name=uname, type=typ, default=[1.0], label=uname))
        return ""

    body = _UNIFORM_RE.sub(take, code).strip() + "\n"
    uses_tex = "sampleTex" in body
    return ShaderDef(name=name, description=f"importado de {name}", code=body, uniforms=uniforms, uses_texture=uses_tex,
                     mode="sprite" if uses_tex else "overlay", one_shot="uProgress" in body)


class ShaderImporter(Importer):
    id = "vfx.shader_import"
    title = "Shader GLSL (effect) ou shader.json"
    description = ("Arquivo .glsl/.frag com `vec4 effect(vec2 uv)` (uniforms declarados viram controles) ou um shader.json "
                   "do Asset Forge. Compila na GPU e renderiza a prévia.")
    exts = {".glsl", ".frag", ".json"}
    kind = "shader"

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        text = _read_text(ctx.src)
        if ctx.src.suffix.lower() == ".json":
            try:
                data = json.loads(text)
            except ValueError as e:
                raise NotApplicable() from e
            if not isinstance(data, dict) or "code" not in data:
                raise NotApplicable()
            try:
                sd = ShaderDef(**data)
            except ValidationError as e:
                raise ValueError(f"shader.json inválido: {e}") from e
        else:
            sd = _shader_from_glsl(text, ctx.src.stem)
        ok, why = render.available()
        if not ok:
            raise ValueError(f"não dá para compilar o shader aqui: {why}")
        try:
            return write_shader(ctx, sd, None)
        except Exception as e:
            raise ValueError(f"o shader não compilou: {e}") from e


class ParticlesImporter(Importer):
    id = "vfx.particles_import"
    title = "Partículas (particles.json)"
    description = "Definição JSON de sistema de partículas do Asset Forge (emissores, gradientes, texturas embutidas)."
    exts = {".json"}
    kind = "particles"

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        try:
            data = json.loads(_read_text(ctx.src))
        except ValueError as e:
            raise NotApplicable() from e
        if not isinstance(data, dict) or "emitters" not in data:
            raise NotApplicable()
        try:
            ps = ParticleSystem(**data)
        except ValidationError as e:
            raise ValueError(f"particles.json inválido: {e}") from e
        return write_particles(ctx, ps)


IMPORTERS = [ShaderImporter(), ParticlesImporter()]
