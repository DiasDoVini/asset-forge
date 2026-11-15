"""Modalidade VFX do Asset Forge."""
from forge_core.pipeline import Modality

from .exporters import EXPORTERS
from .importers import IMPORTERS
from .pipelines import PIPELINES
from .refiners import REFINERS

modality = Modality(
    name="vfx",
    title="VFX",
    description="Shaders portáveis (Godot, Unity, XNA, GLSL) e sistemas de partículas com prévia, flipbook e exportação.",
    pipelines=PIPELINES,
    exporters=EXPORTERS,
    importers=IMPORTERS,
    refiners=REFINERS,
)
