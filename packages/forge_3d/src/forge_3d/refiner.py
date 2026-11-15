"""Advanced generation for 3D models (forge_core.refine): generate -> Claude reviews renders -> adjust."""

from __future__ import annotations

from forge_core.refine import Action, ActionSpec, Brief, Preview, PreviewContext, Refiner, preview_from_image

from . import geometry as g
from .geometry import render as R


class MeshRefiner(Refiner):
    id = "3d.model"
    title = "Modelo 3D"
    description = ("Gera um modelo 3D (de imagem de referência ou texto) e o ajusta em iterações: regenerar com outros "
                   "parâmetros, detalhar regiões, editar a forma, texturizar, limpar e otimizar a malha.")
    default_criteria = [
        "silhueta e proporções fiéis às referências (vistas de frente e perfil)",
        "sem buracos, faces faltando, membros fundidos ou pedaços soltos",
        "detalhes principais legíveis: rosto, mãos, acessórios, armas",
        "costas e laterais coerentes com a frente (nada achatado ou inventado de forma estranha)",
    ]

    def initial_action(self, brief: Brief) -> Action:
        opts = brief.params or {}
        common = {k: opts[k] for k in ("target_faces", "height", "octree_resolution") if k in opts}
        if brief.references:
            params = {"input_asset": "$reference", **common}
            if isinstance(opts.get("side_views"), dict):
                params["side_views"] = opts["side_views"]
            return Action(pipeline="3d.image_to_3d", params=params,
                          note="modelo a partir da primeira referência (Hunyuan3D)")
        return Action(pipeline="3d.text_to_3d", params={"prompt": brief.prompt, **common},
                      note="imagem conceito no estilo do projeto -> modelo (Hunyuan3D)")

    def actions(self) -> list[ActionSpec]:
        return [
            ActionSpec("3d.image_to_3d",
                       "recomeçar a partir da referência quando a forma geral está errada (silhueta, proporções, partes "
                       "fundidas). Mudar a seed costuma bastar. Métodos: trellis2 (padrão quando disponível: mais nítido, "
                       "textura opcional) ou hunyuan3d-2.1 (mais rápido, usado com vistas extras).",
                       '{"input_asset": "$reference", "seed": 1234, "generator": "trellis2"|"hunyuan3d-2.1", '
                       '"trellis": {"resolution": 1024|1536, "texture": true}, "hunyuan": {"octree_resolution": 384|512}, '
                       '"side_views": {"left"|"back"|"right": "<ref de imagem>"} (opcional; left = lado esquerdo do próprio objeto)}'),
            ActionSpec("3d.text_to_3d",
                       "recomeçar sem referência (só texto), com outra seed ou prompt mais preciso",
                       '{"prompt": "...", "asset_type": "object|character|creature|building|vehicle|weapon", "seed": 1234}'),
            ActionSpec("3d.refine_region",
                       "uma região tem pouco detalhe ou está deformada (rosto borrado, mãos sem dedos, arma disforme): "
                       "regenera só aquela região com muito mais detalhe a partir da imagem de origem e costura no modelo. "
                       "Leva ~5 min; uma região por vez.",
                       '{"input_asset": "$current", "region": {"image_box": [x0, y0, x1, y1]}} com frações 0..1 da imagem '
                       'de REFERÊNCIA (a frente), justa em volta da parte; ou {"region": {"prompt": "the face"}} '
                       '(localizado automaticamente). "octree_resolution": 384|512 (opcional).'),
            ActionSpec("3d.ai_edit",
                       "mudar forma ou acrescentar/remover algo (\"chapéu mais alto\", \"remova a capa\", \"braços "
                       "abertos\"): edita a imagem de origem e regenera o modelo",
                       '{"input_asset": "$current", "instruction": "<mudança em inglês, objetiva>"}'),
            ActionSpec("3d.repair",
                       "pedaços soltos ou flutuantes, buracos, superfície quebrada. reconstruct_surface=true só para "
                       "buracos grandes ou superfície muito quebrada: ele apaga peças finas (alças, hastes, armas finas, "
                       "cabelo); com peças finas use false. Malha aberta sem buracos visíveis não precisa de reparo.",
                       '{"input_asset": "$current", "reconstruct_surface": false, "min_component_ratio": 0.02}'),
            ActionSpec("3d.transform",
                       "orientação ou escala erradas (deitado, inclinado, tamanho fora do pedido)",
                       '{"input_asset": "$current", "rotate_deg": [x, y, z], "target_height": 1.8, "pivot": "bottom_center"}'),
            ActionSpec("3d.simplify",
                       "triângulos demais para o orçamento pedido (forma já boa)",
                       '{"input_asset": "$current", "target_faces": 20000}'),
            ActionSpec("3d.lowpoly",
                       "o pedido é estilo low poly facetado",
                       '{"input_asset": "$current", "target_faces": 1500}'),
            ActionSpec("3d.texture_ai",
                       "a forma está aprovada e falta cor/textura, ou a textura está borrada/errada. Use por último: "
                       "mudanças de forma depois disso perdem a textura.",
                       '{"input_asset": "$current", "method": "hunyuan3d-paint"} (melhor) ou "project" (usa vistas reais)'),
        ]

    def previews(self, ctx: PreviewContext) -> list[Preview]:
        store = ctx.project.store
        files = store.files_of(ctx.asset, "primary")
        mesh_file = next((f for f in files if f.suffix.lower() in g.MESH_EXTENSIONS), None)
        if mesh_file is None:
            return []
        mesh = g.load_mesh(mesh_file)
        if mesh_file.suffix.lower() in (".stl", ".3mf", ".ply"):  # Z-up formats: show them standing
            from .pipelines import Y_UP_TO_Z_UP
            import numpy as np

            mesh.apply_transform(np.linalg.inv(Y_UP_TO_Z_UP))
        stats = g.mesh_stats(mesh)
        info = (f"{stats['faces']:,} triângulos, {stats['components']} partes soltas, "
                f"{'fechada' if stats['watertight'] else 'aberta'}, {stats['extents'][1]:.2f} de altura").replace(",", ".")
        out = [
            preview_from_image(R.views_sheet(mesh, size=384), ctx, "views.png",
                               f"vistas (frente, 3/4, perfil esquerdo, costas, perfil direito, topo) — {info}"),
            preview_from_image(R.render_view(mesh, 0.0, 5.0, size=768), ctx, "front.png", "frente (ampliada, para detalhes)"),
            preview_from_image(R.render_view(mesh, 40.0, 12.0, size=768), ctx, "three_quarter.png", "3/4 frente-esquerda (ampliada)"),
            preview_from_image(R.render_view(mesh, 0.0, 5.0, size=640, wireframe=True), ctx, "wireframe.png",
                               "wireframe frente (topologia e densidade de triângulos)"),
        ]
        uv = R.uv_sheet(mesh)
        if uv is not None:
            out.append(preview_from_image(uv, ctx, "uv.png", "folha da textura com o layout UV"))
        return out

    def critic_guidance(self) -> str:
        return (
            "Você avalia modelos 3D gerados por IA (Hunyuan3D) a partir dos renders. Compare sempre com as referências.\n"
            "Armadilhas típicas do Hunyuan3D:\n"
            "- partes finas fundidas ou faltando (dedos, alças, cabos, armas finas, cabelo); armas finas às vezes saem "
            "inclinadas em profundidade (veja os perfis);\n"
            "- costas e laterais inventadas: achatadas, inchadas ou incoerentes com a frente (veja costas e perfis);\n"
            "- rosto e mãos borrados por falta de resolução: use 3d.refine_region na região, não regenere tudo;\n"
            "- pedaços soltos flutuando, buracos, superfície ondulada ou serrilhada (3d.repair com "
            "reconstruct_surface=false preserva peças finas; true só para buracos grandes);\n"
            "- escala/orientação: o modelo deve estar em pé, de frente para a câmera da vista 'frente';\n"
            "- textura: costas borradas ou repetindo a frente, cores lavadas, emendas visíveis.\n"
            "Prioridade: forma geral (regenerar com outra seed) > detalhes locais (refine_region) > limpeza (repair) "
            "> textura (texture_ai por último). Mudanças de forma depois da textura apagam a textura.\n"
            "A vista 'perfil esquerdo' mostra o lado esquerdo do próprio objeto. Os renders são cinza quando não há textura: "
            "isso é esperado, não penalize a falta de cor se a textura ainda não foi pedida."
        )


REFINERS = [MeshRefiner()]
