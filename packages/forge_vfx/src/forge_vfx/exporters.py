"""Exportadores de VFX: código de shader por engine, flipbook (sheet/GIF/frames) e partículas para Godot/Unity."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import Field

from forge_core import ExportContext, Exporter, ExportParams
from forge_2d.animation import aseprite_json, build_sheet, save_gif

from . import engines, particles, render, shaders
from .particles import ParticleSystem
from .shaders import ShaderDef


def _shader(ctx: ExportContext) -> ShaderDef:
    return ShaderDef.model_validate_json(ctx.files("primary")[0].read_text(encoding="utf-8"))


def _system(ctx: ExportContext) -> ParticleSystem:
    return ParticleSystem.model_validate_json(ctx.files("primary")[0].read_text(encoding="utf-8"))


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "effect"


class ShaderCodeParams(ExportParams):
    target: Literal["all", "godot", "unity", "xna", "glsl", "shadertoy", "hlsl"] = Field(
        "all", description="godot: .gdshader (canvas_item); unity: .shader (ShaderLab); xna: .fx (tModLoader/FNA); "
                           "glsl: GLSL ES 3.00 (WebGL2); shadertoy: cole no site; hlsl: funções puras (s&box, Unreal...)")


class ShaderCodeExporter(Exporter):
    id = "vfx.shader_code"
    title = "Shader para engine"
    description = "Gera o código do shader para Godot, Unity, XNA/tModLoader, GLSL/WebGL, Shadertoy ou HLSL puro."
    kinds = {"shader"}
    Params = ShaderCodeParams

    def run(self, ctx: ExportContext, p: ShaderCodeParams) -> list[Path]:
        sd = _shader(ctx)
        name = _safe(ctx.asset.slug)
        targets = ["godot", "unity", "xna", "glsl", "shadertoy", "hlsl"] if p.target == "all" else [p.target]
        out = []
        for t in targets:
            fn, code = {
                "godot": (f"{name}.gdshader", lambda: shaders.godot(sd)),
                "unity": (f"{name}.shader", lambda: shaders.unity(sd)),
                "xna": (f"{name}.fx", lambda: shaders.xna_fx(sd)),
                "glsl": (f"{name}.frag", lambda: shaders.glsl_es(sd)),
                "shadertoy": (f"{name}.shadertoy.glsl", lambda: shaders.shadertoy(sd)),
                "hlsl": (f"{name}.hlsl", lambda: shaders.hlsl_raw(sd)),
            }[t]
            path = ctx.out_dir / fn
            path.write_text(code(), encoding="utf-8")
            out.append(path)
        if sd.uses_texture:
            tex = ctx.files("texture")
            if tex:
                dst = ctx.out_dir / f"{name}_preview_sprite.png"
                dst.write_bytes(tex[0].read_bytes())
                out.append(dst)
        return out


class FlipbookParams(ExportParams):
    frames: int = Field(16, ge=2, le=128)
    size: int = Field(256, ge=16, le=1024, description="lado de cada quadro em px")
    fps: int = Field(12, ge=1, le=60, description="velocidade do GIF / JSON")
    format: Literal["sheet", "gif", "frames", "all"] = "all"
    columns: int | None = Field(None, ge=1, le=32, description="colunas da grade (vazio = quadrado)")
    loop_blend: bool = Field(True, description="loops: funde os últimos quadros com os primeiros para não 'pular'")
    background: Literal["transparent", "dark"] = Field("transparent", description="dark: compõe sobre fundo escuro (bom para aditivos em GIF)")
    premultiply_add: bool = Field(True, description="efeitos aditivos: grava cor×alpha (use blend aditivo no engine)")


def _loop_blend(frames: list[Image.Image], k: int) -> list[Image.Image]:
    """Crossfade dos k quadros extras do fim sobre o início (o resultado tem len - k quadros)."""
    n = len(frames) - k
    out = []
    for i in range(n):
        if i < k:
            w = (i + 1) / (k + 1)
            a = np.asarray(frames[i], np.float32)
            b = np.asarray(frames[n + i], np.float32)
            out.append(Image.fromarray((a * w + b * (1 - w)).astype(np.uint8), "RGBA"))
        else:
            out.append(frames[i])
    return out


class FlipbookExporter(Exporter):
    id = "vfx.flipbook"
    title = "Flipbook (sprite sheet / GIF / quadros)"
    description = ("Renderiza o shader ou as partículas em quadros: sprite sheet com JSON (Aseprite), GIF e PNGs. "
                   "Funciona em qualquer engine (Terraria, GameMaker, Phaser...).")
    kinds = {"shader", "particles"}
    Params = FlipbookParams

    def run(self, ctx: ExportContext, p: FlipbookParams) -> list[Path]:
        name = _safe(ctx.asset.slug)
        if ctx.asset.kind == "shader":
            sd = _shader(ctx)
            tex = ctx.files("texture")
            texture = Image.open(tex[0]).convert("RGBA") if tex else (render.placeholder_sprite(p.size) if sd.uses_texture else None)
            loop, blend = not sd.one_shot, sd.blend
            extra = max(2, p.frames // 4) if (loop and p.loop_blend) else 0
            dur = sd.duration * (p.frames + extra) / p.frames
            imgs = render.render_frames(sd, (p.size, p.size), p.frames + extra, duration=dur, texture=texture)
        else:
            ps = _system(ctx)
            from .pipelines import particle_textures

            class _C:  # adaptador mínimo para resolver texturas asset:
                project = ctx.project

            loop, blend = ps.loop, ("add" if all(e.blend == "add" for e in ps.emitters) else "alpha")
            extra = max(2, p.frames // 4) if (loop and p.loop_blend) else 0
            ps2 = ps.model_copy(update={"duration": ps.duration * (p.frames + extra) / p.frames}) if extra else ps
            imgs = particles.render(ps2, p.frames + extra, size=(p.size, p.size), textures=particle_textures(_C(), ps))  # type: ignore[arg-type]
        if extra:
            imgs = _loop_blend(imgs, extra)
        if p.background == "dark":
            imgs = [render.on_background(i, blend).convert("RGBA") for i in imgs]
        elif blend == "add" and p.premultiply_add:
            imgs = [Image.fromarray(np.dstack([np.asarray(i, np.float32)[..., :3] * np.asarray(i, np.float32)[..., 3:4] / 255,
                                               np.asarray(i)[..., 3]]).astype(np.uint8), "RGBA") for i in imgs]
        out: list[Path] = []
        durs = [round(1000 / p.fps)] * len(imgs)
        if p.format in ("sheet", "all"):
            sheet, rects = build_sheet(imgs, "grid", p.columns, 0, 1)
            png = ctx.out_dir / f"{name}_sheet.png"
            sheet.save(png)
            js = ctx.out_dir / f"{name}_sheet.json"
            js.write_text(json.dumps(aseprite_json(png.name, sheet.size, rects, durs,
                                                   [{"name": name, "from": 0, "to": len(imgs) - 1}], frame_prefix=name),
                                     indent=2), encoding="utf-8")
            out += [png, js]
        if p.format in ("gif", "all"):
            gif = ctx.out_dir / f"{name}.gif"
            gif_frames = imgs if p.background == "dark" else [render.on_background(i, blend).convert("RGBA") for i in imgs]
            save_gif(gif_frames, durs, gif, loop=loop)
            out.append(gif)
        if p.format in ("frames", "all"):
            d = ctx.out_dir / f"{name}_frames"
            d.mkdir(exist_ok=True)
            for i, im in enumerate(imgs):
                f = d / f"{name}_{i:03d}.png"
                im.save(f)
                out.append(f)
        return out


class ParticlesEngineParams(ExportParams):
    target: Literal["godot", "unity", "json"] = Field("godot", description="godot: .tscn com CPUParticles2D; unity: JSON + script de editor")
    godot_res_dir: str = Field("vfx", description="pasta (res://...) onde você vai colocar os arquivos no projeto Godot")


class ParticlesEngineExporter(Exporter):
    id = "vfx.particles_engine"
    title = "Partículas para engine"
    description = "Godot 4 (.tscn com CPUParticles2D + texturas) ou Unity (JSON + texturas + AssetForgeParticles.cs)."
    kinds = {"particles"}
    Params = ParticlesEngineParams

    def run(self, ctx: ExportContext, p: ParticlesEngineParams) -> list[Path]:
        ps = _system(ctx)
        name = _safe(ctx.asset.slug)
        out: list[Path] = []
        tex_files: dict[str, str] = {}
        for em in ps.emitters:
            if em.texture in tex_files:
                continue
            fn = f"{_safe(em.texture.replace('asset:', ''))}.png"
            if em.texture.startswith("asset:"):
                av = ctx.project.store.get(em.texture[6:])
                src = ctx.project.store.files_of(av, "primary")[0]
                Image.open(src).convert("RGBA").resize((engines.TEX_SIZE, engines.TEX_SIZE), Image.Resampling.LANCZOS).save(ctx.out_dir / fn)
            else:
                particles.texture_image(em.texture, engines.TEX_SIZE).save(ctx.out_dir / fn)
            tex_files[em.texture] = fn
            out.append(ctx.out_dir / fn)
        if p.target == "godot":
            f = ctx.out_dir / f"{name}.tscn"
            f.write_text(engines.godot_tscn(ps, tex_files, f"{p.godot_res_dir.strip('/')}/{name}"), encoding="utf-8")
            out.append(f)
            readme = ctx.out_dir / "README.txt"
            readme.write_text(f"Copie esta pasta para res://{p.godot_res_dir.strip('/')}/{name}/ no projeto Godot 4 e instancie "
                              f"{name}.tscn. Para one-shots chame restart() em cada CPUParticles2D.\n", encoding="utf-8")
            out.append(readme)
        elif p.target == "unity":
            f = ctx.out_dir / f"{name}.particles.json"
            f.write_text(json.dumps(engines.unity_json(ps), indent=2), encoding="utf-8")
            cs = ctx.out_dir / "Editor" / "AssetForgeParticles.cs"
            cs.parent.mkdir(exist_ok=True)
            cs.write_text(engines.UNITY_SCRIPT, encoding="utf-8")
            readme = ctx.out_dir / "README.txt"
            readme.write_text("Copie a pasta para Assets/ no Unity. Selecione o .particles.json e use o menu "
                              "Tools > Asset Forge > Build Particle System.\n", encoding="utf-8")
            out += [f, cs, readme]
        else:
            f = ctx.out_dir / f"{name}.particles.json"
            f.write_text(ps.model_dump_json(indent=2), encoding="utf-8")
            out.append(f)
        return out


EXPORTERS = [ShaderCodeExporter(), FlipbookExporter(), ParticlesEngineExporter()]
