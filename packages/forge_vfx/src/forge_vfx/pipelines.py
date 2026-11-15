"""Pipelines de VFX: shaders portáveis, sistemas de partículas e texturas de efeito.

Arquivos por versão:
  shader    → shader.json (primary), effect.glsl (source), preview.png, sheet.png (report), texture.png (texture, opcional)
  particles → particles.json (primary), preview.png, sheet.png (report)
  texture   → texture.png (primary)
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import Field, ValidationError

from forge_core import ComfyError, OutputFile, Pipeline, PipelineParams
from forge_core.pipeline import Context
from forge_core.runner import resolve_input

from . import library, particle_presets, particles, render
from .particles import ParticleSystem
from .shaders import ShaderDef

Generator = Literal["auto", "template", "claude"]


# ---------------------------------------------------------------- helpers

def _image_of(ctx: Context, ref: str | None = None) -> Image.Image | None:
    if ref is None:
        files = ctx.input_files("texture") or ctx.input_files("primary")
        files = [f for f in files if f.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg")]
        if not files:
            return None
        return Image.open(files[0]).convert("RGBA")
    av, path = resolve_input(ctx.project, ref)
    if av is not None:
        fs = ctx.project.store.files_of(av, "primary")
        path = fs[0] if fs else None
    return Image.open(path).convert("RGBA") if path else None


def _fit_square(img: Image.Image, size: int = 256) -> Image.Image:
    """Centraliza a imagem num quadrado transparente com margem (efeitos como contorno precisam de espaço)."""
    bb = img.getbbox() or (0, 0, img.width, img.height)
    img = img.crop(bb)
    s = int(max(img.size) * 1.25)
    sq = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sq.alpha_composite(img, ((s - img.width) // 2, (s - img.height) // 2))
    return sq.resize((size, size), Image.Resampling.LANCZOS)


def _use_claude(gen: Generator, has_prompt: bool) -> bool:
    if gen == "template" or not has_prompt:
        return False
    from forge_core.critic import api_available

    ok, why = api_available()
    if gen == "claude" and not ok:
        raise ValueError(f"generator=claude indisponível: {why}")
    return ok


def write_shader(ctx: Context, sd: ShaderDef, texture: Image.Image | None, *, frames: int = 16,
                 size: int = 256) -> list[OutputFile]:
    d = ctx.version_dir
    tex = texture if sd.uses_texture else None
    if sd.uses_texture and tex is None:
        tex = render.placeholder_sprite(size)
    imgs = render.render_frames(sd, (size, size), frames, texture=tex)
    (d / "shader.json").write_text(sd.model_dump_json(indent=2), encoding="utf-8")
    (d / "effect.glsl").write_text(sd.code, encoding="utf-8")
    hero = imgs[len(imgs) // 2] if sd.one_shot else imgs[0]
    (render.on_background(hero, sd.blend) if sd.blend == "add" else hero).save(d / "preview.png")
    render.contact_sheet(imgs[:: max(1, len(imgs) // 8)][:8], 4, sd.blend).save(d / "sheet.png")
    files = [OutputFile(d / "shader.json", "primary", "application/json"), OutputFile(d / "effect.glsl", "source", "text/x-glsl"),
             OutputFile(d / "preview.png", "preview"), OutputFile(d / "sheet.png", "report")]
    if tex is not None:
        tex.save(d / "texture.png")
        files.append(OutputFile(d / "texture.png", "texture"))
    ctx.meta["kind"] = "shader"
    ctx.meta.setdefault("extra", {}).update({"shader": {"name": sd.name, "blend": sd.blend, "mode": sd.mode,
                                                         "one_shot": sd.one_shot, "duration": sd.duration,
                                                         "uses_texture": sd.uses_texture, "template": sd.template,
                                                         "uniforms": [u.name for u in sd.uniforms]}})
    return files


def _shader_with_retries(ctx: Context, prompt: str, *, base: ShaderDef | None, sprite: bool) -> ShaderDef:
    from . import llm

    err = None
    for attempt in range(3):
        ctx.progress(0.1 + attempt * 0.2, "Claude escrevendo o shader" if not err else "corrigindo erro de compilação")
        data = llm.generate_shader(prompt, base=base.model_dump() if base else None, error=err, sprite=sprite)
        try:
            sd = ShaderDef(**data, template=base.template if base else None)
            render.compile_check(sd)
            return sd
        except (ValidationError, render.ShaderCompileError) as e:
            err = str(e)
            ctx.log(f"tentativa {attempt + 1} falhou: {err[:300]}")
    raise ValueError(f"o shader gerado não compilou após 3 tentativas: {err}")


# ---------------------------------------------------------------- Shader

class ShaderParams(PipelineParams):
    prompt: str = Field("", description='o efeito: "blue magic portal swirling", "sprite dissolving into embers"')
    template: Literal["auto", *library.TEMPLATES.keys()] = Field(  # type: ignore[valid-type]
        "auto", description="ponto de partida da biblioteca (auto = escolhe pelo prompt)")
    generator: Generator = Field("auto", description="auto: Claude se houver chave da API, senão template; template: só biblioteca")
    code: str | None = Field(None, description="código GLSL pronto (vec4 effect(vec2 uv)); agentes podem escrever direto")
    uniforms: dict[str, Any] = Field(default_factory=dict, description='valores, ex.: {"uColor": [1,0.2,0.1,1], "uSpeed": 2}')
    uniform_defs: list[dict[str, Any]] | None = Field(None, description="com `code`: declarações [{name,type,default,min,max,label}]")
    blend: Literal["auto", "alpha", "add"] = "auto"
    one_shot: bool | None = None
    duration: float | None = Field(None, gt=0, le=60)
    colors: list[str] = Field(default_factory=list, description="cores hex para os slots de cor do template")
    frames: int = Field(16, ge=4, le=64, description="quadros da prévia")


def _hex_rgb(h: str) -> list[float]:
    r, g, b, _ = particles.parse_color(h)
    return [r, g, b]


class TextToShader(Pipeline):
    id = "vfx.shader"
    title = "Text → Shader"
    description = ("Shader de efeito 2D (fogo, portal, escudo, raio, dissolve, contorno...) com prévia renderizada e "
                   "exportação para Godot, Unity, XNA/tModLoader, GLSL e Shadertoy. Entrada opcional: sprite para "
                   "efeitos aplicados sobre ele.")
    resources = {"gpu"}
    kind = "shader"
    accepts_input = True
    Params = ShaderParams

    def default_slug(self, params: ShaderParams) -> str:
        if params.prompt:
            return super().default_slug(params)
        return params.template if params.template != "auto" else "shader"

    def run(self, ctx: Context, p: ShaderParams) -> list[OutputFile]:
        ok, why = render.available()
        if not ok:
            raise ComfyError(why)
        tex = _image_of(ctx)
        tex = _fit_square(tex) if tex is not None else None
        colors = [_hex_rgb(c) for c in p.colors] or None
        if p.code:
            sd = ShaderDef(name=(p.prompt or "custom")[:40], description=p.prompt, code=p.code,
                           uniforms=p.uniform_defs or [], uses_texture="sampleTex" in p.code,
                           mode="sprite" if "sampleTex" in p.code else "overlay")
            gen = "code"
        elif _use_claude(p.generator, bool(p.prompt)):
            base = library.from_template(p.template) if p.template != "auto" else None
            sd = _shader_with_retries(ctx, p.prompt, base=base, sprite=tex is not None)
            gen = "claude"
        else:
            # com sprite de entrada, escolhe entre os efeitos aplicados sobre sprite
            name = p.template if p.template != "auto" else library.pick_template(p.prompt, sprite=tex is not None)
            sd = library.from_template(name, p.prompt, colors)
            gen = "template"
            ctx.log(f"template: {name}")
        upd: dict[str, Any] = {}
        if p.blend != "auto":
            upd["blend"] = p.blend
        if p.one_shot is not None:
            upd["one_shot"] = p.one_shot
        if p.duration:
            upd["duration"] = p.duration
        if p.prompt and not sd.description:
            upd["description"] = p.prompt
        sd = sd.model_copy(update=upd).with_values(p.uniforms)
        ctx.progress(0.75, "renderizando prévia")
        ctx.meta.update(prompt=p.prompt or None)
        files = write_shader(ctx, sd, tex, frames=p.frames)
        ctx.meta["extra"]["generator"] = gen
        return files


class ShaderEditParams(PipelineParams):
    instruction: str = Field("", description="o que mudar (usa o Claude): 'make it slower and more purple'")
    code: str | None = Field(None, description="novo código GLSL completo (substitui)")
    uniforms: dict[str, Any] = Field(default_factory=dict, description="novos valores de uniforms")
    uniform_defs: list[dict[str, Any]] | None = Field(None, description="redefine a lista de uniforms (com code)")
    blend: Literal["keep", "alpha", "add"] = "keep"
    one_shot: bool | None = None
    duration: float | None = Field(None, gt=0, le=60)
    texture: str | None = Field(None, description="troca o sprite de prévia (asset ou arquivo)")
    frames: int = Field(16, ge=4, le=64)


class ShaderEdit(Pipeline):
    id = "vfx.shader_edit"
    title = "Editar shader"
    description = "Edita um shader: código, valores, blend, duração ou instrução em linguagem natural (Claude). Nova versão."
    resources = {"gpu"}
    requires_input = True
    same_asset = True
    Params = ShaderEditParams

    def run(self, ctx: Context, p: ShaderEditParams) -> list[OutputFile]:
        src = ctx.input_files("primary")
        if not src or src[0].name != "shader.json":
            raise ValueError("a entrada não é um asset de shader")
        sd = ShaderDef.model_validate_json(src[0].read_text(encoding="utf-8"))
        tex = _image_of(ctx, p.texture) if p.texture else None
        if tex is not None:
            tex = _fit_square(tex)
        elif sd.uses_texture:
            t = ctx.input_files("texture")
            tex = Image.open(t[0]).convert("RGBA") if t else None
        if p.instruction:
            sd = _shader_with_retries(ctx, p.instruction, base=sd, sprite=sd.uses_texture or tex is not None)
        upd: dict[str, Any] = {}
        if p.code:
            upd["code"] = p.code
            upd["uses_texture"] = "sampleTex" in p.code
        if p.uniform_defs is not None:
            upd["uniforms"] = p.uniform_defs
        if p.blend != "keep":
            upd["blend"] = p.blend
        if p.one_shot is not None:
            upd["one_shot"] = p.one_shot
        if p.duration:
            upd["duration"] = p.duration
        if upd:
            sd = ShaderDef(**{**sd.model_dump(), **upd})
        sd = sd.with_values(p.uniforms)
        if not (p.instruction or upd or p.uniforms or p.texture):
            raise ValueError("nenhuma alteração pedida")
        render.compile_check(sd)
        ctx.meta.update(prompt=p.instruction or (ctx.input.prompt if ctx.input else None))
        return write_shader(ctx, sd, tex, frames=p.frames)


# ---------------------------------------------------------------- Partículas

def particle_textures(ctx: Context, ps: ParticleSystem) -> dict[str, np.ndarray]:
    out = {}
    for em in ps.emitters:
        if em.texture.startswith("asset:") and em.texture not in out:
            img = _image_of(ctx, em.texture[6:])
            if img is None:
                raise ValueError(f"textura não encontrada: {em.texture}")
            out[em.texture] = np.asarray(img.resize((64, 64), Image.Resampling.LANCZOS), np.float32) / 255
    return out


def write_particles(ctx: Context, ps: ParticleSystem, *, frames: int = 16, size: int = 256) -> list[OutputFile]:
    d = ctx.version_dir
    texs = particle_textures(ctx, ps)
    imgs = particles.render(ps, frames, size=(size, size), textures=texs)
    (d / "particles.json").write_text(ps.model_dump_json(indent=2), encoding="utf-8")
    hero = imgs[min(len(imgs) - 1, max(1, len(imgs) // 4))] if not ps.loop else imgs[len(imgs) // 2]
    render.on_background(hero, "alpha").save(d / "preview.png")
    render.contact_sheet(imgs[:: max(1, len(imgs) // 8)][:8], 4, "alpha").save(d / "sheet.png")
    ctx.meta["kind"] = "particles"
    ctx.meta.setdefault("extra", {}).update({"particles": {"name": ps.name, "emitters": [e.name for e in ps.emitters],
                                                           "loop": ps.loop, "duration": ps.duration, "preset": ps.preset}})
    return [OutputFile(d / "particles.json", "primary", "application/json"), OutputFile(d / "preview.png", "preview"),
            OutputFile(d / "sheet.png", "report")]


def _particles_from_llm(ctx: Context, prompt: str, base: ParticleSystem | None) -> ParticleSystem:
    from . import llm

    err = None
    schema = ParticleSystem.model_json_schema()
    for attempt in range(3):
        ctx.progress(0.1 + attempt * 0.2, "Claude desenhando o sistema" if not err else "corrigindo o JSON")
        data = llm.generate_particles(prompt, schema, base=base.model_dump() if base else None, error=err)
        try:
            return ParticleSystem(**data)
        except ValidationError as e:
            err = str(e)
            ctx.log(f"tentativa {attempt + 1} inválida: {err[:300]}")
    raise ValueError(f"o sistema gerado é inválido após 3 tentativas: {err}")


class ParticlesParams(PipelineParams):
    prompt: str = Field("", description='o efeito: "campfire with embers", "green poison bubbles", "coin burst"')
    preset: Literal["auto", *particle_presets.PRESETS.keys()] = "auto"  # type: ignore[valid-type]
    generator: Generator = "auto"
    definition: dict[str, Any] | None = Field(None, description="particles.json completo (agentes podem mandar pronto)")
    colors: list[str] = Field(default_factory=list, description="cores hex aplicadas aos emissores aditivos")
    scale: float = Field(1.0, ge=0.2, le=4.0, description="tamanho geral")
    speed: float = Field(1.0, ge=0.2, le=4.0)
    amount: float = Field(1.0, ge=0.1, le=5.0, description="multiplica a quantidade de partículas")
    loop: bool | None = None
    duration: float | None = Field(None, gt=0, le=60)
    frames: int = Field(16, ge=4, le=64)


class TextToParticles(Pipeline):
    id = "vfx.particles"
    title = "Text → Partículas"
    description = ("Sistema de partículas 2D (fogo, fumaça, faíscas, explosão, magia, chuva, neve, moedas...) com "
                   "prévia, flipbook e exportação para Godot (CPUParticles2D) e Unity (ParticleSystem).")
    resources = {"cpu"}
    kind = "particles"
    Params = ParticlesParams

    def default_slug(self, params: ParticlesParams) -> str:
        if params.prompt:
            return super().default_slug(params)
        return params.preset if params.preset != "auto" else "particles"

    def run(self, ctx: Context, p: ParticlesParams) -> list[OutputFile]:
        colors = [_hex_rgb(c) for c in p.colors] or None
        if p.definition:
            ps = ParticleSystem(**p.definition)
            gen = "definition"
        elif _use_claude(p.generator, bool(p.prompt)):
            base = particle_presets.from_preset(p.preset) if p.preset != "auto" else None
            ps = _particles_from_llm(ctx, p.prompt, base)
            gen = "claude"
        else:
            name = p.preset if p.preset != "auto" else particle_presets.pick_preset(p.prompt)
            ps = particle_presets.from_preset(name, p.prompt, colors=colors, scale=p.scale, speed=p.speed, amount=p.amount)
            gen = "preset"
            ctx.log(f"preset: {name}")
        upd: dict[str, Any] = {}
        if p.loop is not None:
            upd["loop"] = p.loop
        if p.duration:
            upd["duration"] = p.duration
        if p.prompt:
            upd["description"] = p.prompt
        ps = ps.model_copy(update=upd)
        ctx.progress(0.6, "simulando")
        ctx.meta.update(prompt=p.prompt or None)
        files = write_particles(ctx, ps, frames=p.frames)
        ctx.meta["extra"]["generator"] = gen
        return files


class ParticlesEditParams(PipelineParams):
    instruction: str = Field("", description="o que mudar (usa o Claude): 'more sparks, slower smoke'")
    definition: dict[str, Any] | None = Field(None, description="particles.json completo (substitui)")
    colors: list[str] = Field(default_factory=list)
    scale: float = Field(1.0, ge=0.2, le=4.0)
    speed: float = Field(1.0, ge=0.2, le=4.0)
    amount: float = Field(1.0, ge=0.1, le=5.0)
    loop: bool | None = None
    duration: float | None = Field(None, gt=0, le=60)
    frames: int = Field(16, ge=4, le=64)


def _scale_system(ps: ParticleSystem, scale: float, speed: float, amount: float, colors: list[list[float]] | None) -> ParticleSystem:
    d = ps.model_dump()
    for i, em in enumerate(d["emitters"]):
        em["size"] = [x * scale for x in em["size"]]
        em["speed"] = [x * scale * speed for x in em["speed"]]
        em["position"] = [x * scale for x in em["position"]]
        em["gravity"] = [x * scale for x in em["gravity"]]
        for k in ("radius", "width", "height"):
            em[k] *= scale
        em["rate"] *= amount
        for b in em["bursts"]:
            b["count"] = max(1, round(b["count"] * amount))
        if colors and em["blend"] == "add":
            em["color"] = particles.tint_gradient(em["color"], colors[i % len(colors)])
    return ParticleSystem(**d)


class ParticlesEdit(Pipeline):
    id = "vfx.particles_edit"
    title = "Editar partículas"
    description = "Edita um sistema de partículas: JSON, escala, velocidade, quantidade, cores ou instrução (Claude). Nova versão."
    resources = {"cpu"}
    requires_input = True
    same_asset = True
    Params = ParticlesEditParams

    def run(self, ctx: Context, p: ParticlesEditParams) -> list[OutputFile]:
        src = ctx.input_files("primary")
        if not src or src[0].name != "particles.json":
            raise ValueError("a entrada não é um asset de partículas")
        ps = ParticleSystem.model_validate_json(src[0].read_text(encoding="utf-8"))
        if p.definition:
            ps = ParticleSystem(**p.definition)
        if p.instruction:
            ps = _particles_from_llm(ctx, p.instruction, ps)
        colors = [_hex_rgb(c) for c in p.colors] or None
        if p.scale != 1 or p.speed != 1 or p.amount != 1 or colors:
            ps = _scale_system(ps, p.scale, p.speed, p.amount, colors)
        upd: dict[str, Any] = {}
        if p.loop is not None:
            upd["loop"] = p.loop
        if p.duration:
            upd["duration"] = p.duration
        ps = ps.model_copy(update=upd)
        ctx.meta.update(prompt=p.instruction or (ctx.input.prompt if ctx.input else None))
        return write_particles(ctx, ps, frames=p.frames)


# ---------------------------------------------------------------- Textura de efeito (Z-Image)

class TextureParams(PipelineParams):
    prompt: str = Field(..., description='"wispy smoke puff", "bright magic spark", "fire flame tongue", "slash arc"')
    alpha_from: Literal["luminance", "background_removal"] = Field(
        "luminance", description="luminance: fundo preto vira transparente (efeitos aditivos); background_removal: objetos sólidos")
    size: int = Field(256, ge=32, le=1024, description="lado do PNG final")
    soft_edges: bool = Field(True, description="esmaece as bordas do quadrado (evita cortes em partículas)")
    seed: int | None = None


class EffectTexture(Pipeline):
    id = "vfx.texture"
    title = "Textura de efeito"
    description = ("Gera textura para partículas/flipbooks (fumaça, faísca, chama, brilho, corte) com Z-Image em fundo "
                   "preto e alpha pela luminância. Use em partículas com texture='asset:<slug>'.")
    resources = {"gpu"}
    kind = "texture"
    Params = TextureParams

    def run(self, ctx: Context, p: TextureParams) -> list[OutputFile]:
        from forge_2d import graphs as g2
        from forge_2d.pipelines import _models, _run

        style = ctx.style("2d")
        seed = p.seed if p.seed is not None and p.seed >= 0 else random.randint(0, 2**31 - 1)
        from forge_core.comfy_select import use_instance

        use_instance(ctx, what="a textura de VFX")
        if p.alpha_from == "luminance":
            full = (f"{p.prompt}, single isolated game VFX element, centered, glowing, on pure black background, "
                    "high contrast, no text, no frame, fully contained in the image")
        else:
            full = f"{p.prompt}, single isolated game VFX element, centered, plain background, no text"
        g, img_id, mask_id = g2.text2img_zimage(full, 1024, 1024, seed, 8, models=_models(style),
                                                remove_bg=p.alpha_from == "background_removal")
        out = _run(ctx, g, [img_id, mask_id], label="gerando textura")
        img = out[img_id].convert("RGB")
        a = np.asarray(img, np.float32) / 255
        if p.alpha_from == "luminance":
            alpha = np.clip((a.max(axis=2) - 0.04) / 0.96, 0, 1)
            rgb = np.where(alpha[..., None] > 1e-3, a / np.maximum(a.max(axis=2, keepdims=True), 1e-3), 0)
        else:
            alpha = np.asarray(out[mask_id].convert("L"), np.float32) / 255
            rgb = a
        if p.soft_edges:
            h, w = alpha.shape
            y, x = np.mgrid[0:h, 0:w]
            r = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2)
            alpha *= np.clip((1.0 - r) / 0.15, 0, 1)
        res = Image.fromarray((np.dstack([np.clip(rgb, 0, 1), alpha]) * 255).astype(np.uint8), "RGBA")
        res = res.resize((p.size, p.size), Image.Resampling.LANCZOS)
        path = ctx.version_dir / "texture.png"
        res.save(path)
        ctx.meta.update(seed=seed, prompt=p.prompt, extra={"full_prompt": full, "alpha_from": p.alpha_from})
        return [OutputFile(path, "primary")]


PIPELINES: list[Pipeline] = [TextToShader(), ShaderEdit(), TextToParticles(), ParticlesEdit(), EffectTexture()]
