"""Modalidade Vídeo do Asset Forge."""
from forge_core.pipeline import Modality

from .exporters import EXPORTERS
from .importers import IMPORTERS
from .pipelines import PIPELINES
from .refiners import REFINERS

modality = Modality(
    name="video",
    title="Vídeo",
    description="Text/imagem → vídeo (Wan 2.2), estender, regenerar trechos entre marcações e edição manual.",
    pipelines=PIPELINES,
    exporters=EXPORTERS,
    importers=IMPORTERS,
    refiners=REFINERS,
)
