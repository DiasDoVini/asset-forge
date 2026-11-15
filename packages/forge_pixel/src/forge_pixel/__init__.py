"""Modalidade Pixel art do Asset Forge: estúdio de pixel art determinístico, sem ComfyUI nem IA.

Módulos:
    spec        AssetSpec / PaletteSet / presets de referência (default_specs)
    pixelmap    formato "asset-forge-pixelmap/1" (mapa ASCII + legenda), render, contorno, lado _L
    palette     OKLab, quantização exata sem pontilhado, rampas, darken
    fit         fit_to_spec: qualquer imagem -> canvas/pivô/paleta/contorno da spec
    validators  validadores do aceite (Report com to_markdown), inclusive os de tile (autotile, parede, minério)
    autotile    máscara cardinal de 4 bits, as 16 variantes de borda, lista mínima, mapa de teste e atlas
    preview     zoom_preview, contact_sheet, side_by_side
    poses       pose_sheet (folha + JSON com retângulos, pivô e classes), hit_silhouette, onion_skin
    api         validate_asset(project, ref, spec, references) para servidor/MCP
    rig         montagem do personagem por ossos (dados do RigLayout/ClipFactory) e teste de junção das peças
"""
from forge_core.pipeline import Modality

from .pipelines import PIPELINES

modality = Modality(
    name="pixel",
    title="Pixel art",
    description=("Pixel art determinística: sprites com canvas, pivô e área segura exatos, paleta travada, "
                 "contorno de 1 px automático e validadores de aceite. Desenhe por pixel map ou encaixe uma imagem."),
    pipelines=PIPELINES,
)
