"""Refinador de vídeo: o crítico vê 8 quadros ao longo do clipe."""
from __future__ import annotations

from PIL import Image

from forge_core.refine import Action, ActionSpec, Brief, Preview, PreviewContext, Refiner, preview_from_image

from . import ff


class ClipRefiner(Refiner):
    id = "video.clip"
    title = "Clipe de vídeo"
    description = "Vídeo curto refinado até a ação e o visual atenderem o brief (avaliação por quadros ao longo do tempo)."
    default_criteria = [
        "A ação pedida acontece de forma clara e na ordem certa",
        "Personagens e objetos mantêm a identidade (sem deformar ou trocar de aparência)",
        "Movimento natural, sem tremores, membros extras ou objetos surgindo do nada",
        "Enquadramento e estilo coerentes com o brief",
    ]

    def initial_action(self, brief: Brief) -> Action:
        if brief.references:
            return Action(pipeline="video.animate", params={"input_asset": "$reference", "prompt": brief.prompt},
                          note="animar a referência")
        return Action(pipeline="video.generate", params={"prompt": brief.prompt}, note="primeiro clipe a partir do texto")

    def actions(self) -> list[ActionSpec]:
        return [
            ActionSpec("video.generate", "gerar de novo (prompt mais específico sobre ação e câmera, outra seed/engine)",
                       "prompt em inglês: sujeito + ação + câmera; engine wan14 para movimento mais fiel"),
            ActionSpec("video.animate", "animar a partir de uma imagem (referência ou um quadro bom)", 'input_asset="$reference"'),
            ActionSpec("video.regen_segment", "só um trecho está ruim: refazer entre dois tempos mantendo o resto",
                       'input_asset="$current"; start/end em segundos; prompt do que deve acontecer'),
            ActionSpec("video.extend", "o clipe está bom mas termina cedo", 'input_asset="$current"; prompt do que vem depois'),
            ActionSpec("video.edit", "ajustes de montagem: cortar, velocidade, loop, fades", 'input_asset="$current"'),
        ]

    def previews(self, ctx: PreviewContext) -> list[Preview]:
        src = ctx.project.store.files_of(ctx.asset, "primary")[0]
        info = ff.probe(src)
        n = 8
        frames = [ff.frame_at(src, info.duration * (i + 0.5) / n) for i in range(n)]
        w = 320
        h = round(frames[0].height * w / frames[0].width)
        sheet = Image.new("RGB", (w * 4, h * 2))
        for i, f in enumerate(frames):
            sheet.paste(f.resize((w, h)), ((i % 4) * w, (i // 4) * h))
        label = f"8 quadros ao longo de {info.duration:.1f}s (esq.→dir., cima→baixo), {info.width}x{info.height}"
        return [preview_from_image(sheet, ctx, "frames.png", label)]

    def critic_guidance(self) -> str:
        return ("Avalie continuidade entre os quadros: identidade do personagem, anatomia, objetos que somem ou surgem. "
                "Se só uma parte do tempo falhar, prefira video.regen_segment naquele intervalo.")


REFINERS = [ClipRefiner()]
