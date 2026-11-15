"""Modalidade Áudio do Asset Forge."""
from forge_core.pipeline import Modality

from .exporters import EXPORTERS
from .importers import IMPORTERS
from .pipelines import PIPELINES
from .refiners import REFINERS

modality = Modality(
    name="audio",
    title="Áudio",
    description="Efeitos sonoros, ambiências, música e sons retrô: geração, variações, edição, mixagem e exportação.",
    pipelines=PIPELINES,
    exporters=EXPORTERS,
    importers=IMPORTERS,
    refiners=REFINERS,
)
