"""Asset Forge 3D modality.

``forge_3d.geometry`` is a standalone mesh library; ``modality`` registers the pipelines with forge_core.
"""

from forge_core import Modality

from .anim_pipelines import ANIM_PIPELINES
from .character import CHARACTER_PIPELINES
from .edit_pipelines import EDIT_PIPELINES
from .exporters import EXPORTERS
from .importers import IMPORTERS
from .gen_pipelines import GEN_PIPELINES
from .pipelines import PIPELINES
from .refine_pipelines import PIPELINES as REFINE_PIPELINES
from .refiner import REFINERS
from .tex_pipelines import TEX_PIPELINES

modality = Modality(
    name="3d",
    title="3D",
    description="3D meshes for games and 3D printing: text/image to 3D, manual and AI editing, region refinement, texturing/painting, part animation, character rigging with library animations, simplify, low poly, repair, print prep, part splitting.",
    pipelines=GEN_PIPELINES + EDIT_PIPELINES + REFINE_PIPELINES + TEX_PIPELINES + ANIM_PIPELINES + CHARACTER_PIPELINES + PIPELINES,
    exporters=EXPORTERS,
    importers=IMPORTERS,
    refiners=REFINERS,
)
