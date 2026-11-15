"""Refinador 2D para a geração avançada (laço gerar → Claude avalia → ajustar)."""
from __future__ import annotations

from PIL import Image

from forge_core.refine import Action, ActionSpec, Brief, Preview, PreviewContext, Refiner, preview_from_image

from . import imaging


class AssetRefiner(Refiner):
    id = "2d.asset"
    title = "Asset 2D"
    description = "Sprite, ícone, personagem ou item 2D refinado até atender o brief (estilo do projeto aplicado)."
    default_criteria = [
        "Atende ao que o brief pede (conteúdo, pose, proporções)",
        "Segue o estilo visual do projeto e das referências",
        "Silhueta legível e limpa, sem artefatos, texto ou partes cortadas",
        "Fundo transparente limpo quando for um objeto/personagem",
    ]

    def initial_action(self, brief: Brief) -> Action:
        asset_type = brief.params.get("asset_type", "object")
        if brief.references:
            return Action(pipeline="2d.image2d",
                          params={"input_asset": "$reference", "prompt": brief.prompt, "asset_type": asset_type},
                          note="redesenhar a referência como asset no estilo do projeto")
        return Action(pipeline="2d.text2d", params={"prompt": brief.prompt, "asset_type": asset_type},
                      note="primeira geração a partir do texto")

    def actions(self) -> list[ActionSpec]:
        return [
            ActionSpec("2d.text2d", "gerar de novo do zero (prompt reescrito/mais específico, outra seed ou asset_type)",
                       "prompt em inglês descrevendo só o conteúdo; seed nova para variação"),
            ActionSpec("2d.image2d", "redesenhar a partir de uma imagem (referência ou resultado) no estilo do projeto",
                       'input_asset="$reference" ou "$current"; prompt com o que ajustar'),
            ActionSpec("2d.edit", "corrigir algo específico mantendo o resto (cor, detalhe, pose de uma parte)",
                       'input_asset="$current"; instruction curta e precisa em inglês; reference_assets="$references" ajuda na identidade'),
            ActionSpec("2d.soul", "o projeto tem Creator Soul e o resultado não está no estilo da coleção",
                       "prompt do conteúdo; strategy restyle (padrão) ou derive (mais fiel); references=soul:<id>/<ref> opcional"),
            ActionSpec("2d.remove_bg", "fundo sujo, halo ou recorte ruim", 'input_asset="$current"; hard_edges=true para sprites'),
            ActionSpec("2d.enhance", "resultado bom mas pequeno/borrado", 'input_asset="$current"; method esrgan ou seedvr2'),
            ActionSpec("2d.pixelize", "o brief pede pixel art e o resultado ainda não é pixel-perfect",
                       'input_asset="$current"; size em pixels de arte; outline opcional'),
        ]

    def previews(self, ctx: PreviewContext) -> list[Preview]:
        store = ctx.project.store
        files = store.files_of(ctx.asset, "primary")
        img = Image.open(files[0]).convert("RGBA")
        out = [preview_from_image(img, ctx, "result.png", "resultado")]
        if ctx.asset.kind == "sprite" or max(img.size) <= 256:
            out.append(preview_from_image(imaging.preview(img, 512), ctx, "result_zoom.png", "ampliado (nearest)"))
        return out

    def critic_guidance(self) -> str:
        return ("As prévias mostram o PNG final sobre fundo cinza (transparência = cinza). Verifique recorte, halo branco, "
                "partes cortadas, mãos/rosto deformados e coerência com as referências. Para pixel art, avalie a "
                "legibilidade na resolução nativa. Edições (2d.edit) preservam o resto da imagem; regenerar "
                "(2d.text2d/2d.image2d) muda tudo — use quando a composição estiver errada.")


REFINERS = [AssetRefiner()]
