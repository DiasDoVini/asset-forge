"""Refinador de áudio para a geração avançada: o crítico vê forma de onda + espectrograma e as estatísticas."""
from __future__ import annotations

from forge_core.refine import Action, ActionSpec, Brief, Preview, PreviewContext, Refiner, preview_from_image

from . import dsp, visuals


class SoundRefiner(Refiner):
    id = "audio.sound"
    title = "Som / música"
    description = "SFX, ambiência ou música refinados até atender o brief (avaliação por forma de onda, espectrograma e loudness)."
    default_criteria = [
        "Tipo e caráter do som condizem com o brief (ex.: impacto curto e seco, ambiência contínua)",
        "Sem silêncio sobrando no início, sem cortes abruptos ou cliques no fim",
        "Sem clipping; loudness adequado ao uso em jogo",
        "Loops: emenda fim → início sem degrau visível na forma de onda",
    ]

    def initial_action(self, brief: Brief) -> Action:
        if brief.params.get("music"):
            return Action(pipeline="audio.music", params={"prompt": brief.prompt, **{k: v for k, v in brief.params.items() if k != "music"}},
                          note="primeira composição a partir do texto")
        return Action(pipeline="audio.sfx", params={"prompt": brief.prompt, "variations": 2, **brief.params},
                      note="primeira geração a partir do texto")

    def actions(self) -> list[ActionSpec]:
        return [
            ActionSpec("audio.sfx", "gerar o efeito de novo (prompt mais específico, outro sound_type/duração/seed)",
                       "prompt em inglês, concreto (material, ação, distância); sound_type adequado"),
            ActionSpec("audio.music", "compor a música de novo (tags, bpm, tonalidade, duração)", "prompt = gênero, instrumentos, clima"),
            ActionSpec("audio.retro", "o brief pede som 8/16-bit de jogo retrô", "preset e seed; params sfxr para ajuste fino"),
            ActionSpec("audio.variation", "o som está perto, mas o timbre/estilo precisa mudar", 'input_asset="$current"; strength 0.3–0.6'),
            ActionSpec("audio.edit", "ajustes de pós: cortar, fade, normalizar, pitch, filtros, reverb, loop",
                       'input_asset="$current"; use os campos simples ou ops[]'),
        ]

    def previews(self, ctx: PreviewContext) -> list[Preview]:
        files = ctx.project.store.files_of(ctx.asset, "primary")
        a = dsp.load(files[0])
        st = dsp.analyze(a)
        label = (f"forma de onda (cima) e espectrograma (baixo) — {st['duration']}s, {st['lufs']} LUFS, "
                 f"pico {st['peak_db']} dBFS, {st['channels']} canal(is)")
        out = [preview_from_image(visuals.overview(a), ctx, "overview.png", label)]
        if (ctx.asset.extra or {}).get("loop"):
            seam = dsp.concat([dsp.trim(a, max(0, a.duration - 0.5)), dsp.trim(a, 0, min(0.5, a.duration))])
            out.append(preview_from_image(visuals.waveform(seam, 768, 200), ctx, "loop_seam.png",
                                          "emenda do loop: 0,5 s finais + 0,5 s iniciais (deve ser contínua)"))
        return out

    def critic_guidance(self) -> str:
        return ("Você não ouve o áudio: avalie pela forma de onda, espectrograma e números. Impactos: ataque forte no "
                "início e cauda curta. Ambiências: energia contínua sem buracos. Agudos ausentes = som abafado; energia "
                "só no grave = embolado. Clipping aparece como topo achatado. Música: estrutura com seções e sem "
                "silêncios longos. Se a forma estiver boa e só faltar pós (fade, loop, loudness), use audio.edit.")


REFINERS = [SoundRefiner()]
