"""Modalidade 2D do Asset Forge."""
from forge_core.pipeline import Modality

from .anim_pipelines import ANIM_PIPELINES
from .atlas import ATLAS_PIPELINES
from .exporters import EXPORTERS
from .importers import IMPORTERS
from .pipelines import PIPELINES
from .refiners import REFINERS
from .slice_pipeline import SLICE_PIPELINES
from .soul_pipelines import SOUL_PIPELINES
from .surface import SURFACE_PIPELINES

modality = Modality(
    name="2d",
    title="2D",
    description="Sprites, ícones, tiles e ilustrações 2D: texto→2D, imagem→2D, edição, enhancement, pixelizer e animação.",
    pipelines=PIPELINES + SOUL_PIPELINES + ANIM_PIPELINES + SLICE_PIPELINES + SURFACE_PIPELINES + ATLAS_PIPELINES,
    exporters=EXPORTERS,
    importers=IMPORTERS,
    refiners=REFINERS,
)
