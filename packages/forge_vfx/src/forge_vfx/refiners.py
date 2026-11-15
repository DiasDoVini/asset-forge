"""Refinador de VFX para a geração avançada: o crítico vê os quadros do efeito sobre fundo escuro."""
from __future__ import annotations

import numpy as np
from PIL import Image

from forge_core.refine import Action, ActionSpec, Brief, Preview, PreviewContext, Refiner, preview_from_image

from . import particles, render
from .particles import ParticleSystem
from .shaders import ShaderDef


class EffectRefiner(Refiner):
    id = "vfx.effect"
    title = "Efeito visual (shader / partículas)"
    description = "Shader ou sistema de partículas refinado até ficar com o visual pedido (avaliação por quadros da animação)."
    default_criteria = [
        "Lê claramente como o efeito pedido em tamanho de jogo",
        "Cores e intensidade adequadas (sem estourar tudo em branco, sem ficar apagado)",
        "Movimento coerente entre os quadros (loop contínuo ou one-shot com começo, pico e fim)",
        "Cabe no quadro: nada cortado nas bordas",
    ]

    def initial_action(self, brief: Brief) -> Action:
        if brief.params.get("particles"):
            return Action(pipeline="vfx.particles", params={"prompt": brief.prompt}, note="primeiro sistema a partir do texto")
        params = {"prompt": brief.prompt}
        if brief.references:
            params["input_asset"] = "$reference"
        return Action(pipeline="vfx.shader", params=params, note="primeiro shader a partir do texto")

    def actions(self) -> list[ActionSpec]:
        return [
            ActionSpec("vfx.shader", "gerar o shader de novo (outro template, cores, ou código GLSL escrito por você em `code`)",
                       "template da biblioteca ou code (vec4 effect(vec2 uv)) + uniform_defs"),
            ActionSpec("vfx.shader_edit", "ajustar o shader atual: valores de uniforms, blend, duração, ou código corrigido",
                       'input_asset="$current"; uniforms={"uSpeed":0.5}; code para mudanças estruturais'),
            ActionSpec("vfx.particles", "gerar o sistema de partículas de novo (preset, cores, escala ou definition completa)",
                       "definition = particles.json completo para controle total"),
            ActionSpec("vfx.particles_edit", "ajustar partículas: escala, velocidade, quantidade, cores ou JSON",
                       'input_asset="$current"; scale/speed/amount/colors ou definition'),
        ]

    def previews(self, ctx: PreviewContext) -> list[Preview]:
        f = ctx.project.store.files_of(ctx.asset, "primary")[0]
        if f.name == "shader.json":
            sd = ShaderDef.model_validate_json(f.read_text(encoding="utf-8"))
            t = ctx.project.store.files_of(ctx.asset, "texture")
            tex = Image.open(t[0]).convert("RGBA") if t else (render.placeholder_sprite() if sd.uses_texture else None)
            frames = render.render_frames(sd, (256, 256), 8, texture=tex)
            blend = sd.blend
            what = f"shader '{sd.name}' ({'one-shot' if sd.one_shot else 'loop'} de {sd.duration}s, blend {sd.blend})"
        else:
            ps = ParticleSystem.model_validate_json(f.read_text(encoding="utf-8"))
            frames = particles.render(ps, 8, size=(256, 256))
            blend = "alpha"
            what = f"partículas '{ps.name}' ({len(ps.emitters)} emissores, {'loop' if ps.loop else 'one-shot'} de {ps.duration}s)"
        sheet = render.contact_sheet(frames, 4, blend)
        return [preview_from_image(sheet, ctx, "frames.png", f"8 quadros ao longo do tempo (esq.→dir., cima→baixo): {what}")]

    def critic_guidance(self) -> str:
        return ("Os quadros estão compostos sobre fundo escuro como no jogo. Efeitos aditivos devem brilhar sem virar uma "
                "mancha branca; fumaça deve ter volume e transparência. Para one-shots o 1º quadro é o início e o último o "
                "fim (deve sumir). Prefira ajustar valores (shader_edit uniforms / particles_edit scale/amount) antes de "
                "regenerar; reescreva o código só se a forma estiver errada.")


REFINERS = [EffectRefiner()]
