"""Pure mesh-processing library (no dependency on forge_core).

Pipelines in ``forge_3d.pipelines`` wrap these functions for the CLI/API/MCP/UI.
"""

from . import pbr
from .analyze import mesh_stats, remove_depth_tilt
from .io import MESH_EXTENSIONS, load_mesh, save_mesh
from .lowpoly import lowpoly
from .printing import PrinterProfile, fits_build_volume, orient_for_print, print_optimize, to_millimeters, wall_thickness
from .repair import clean, drop_fragments, is_printable_solid, make_printable, orient_outward, reconstruct_surface, solidify
from .simplify import simplify
from .split import Cut, SplitResult, auto_cuts, exploded_scene, split_for_print

__all__ = [
    "MESH_EXTENSIONS",
    "Cut",
    "PrinterProfile",
    "SplitResult",
    "auto_cuts",
    "clean",
    "drop_fragments",
    "exploded_scene",
    "fits_build_volume",
    "is_printable_solid",
    "load_mesh",
    "lowpoly",
    "make_printable",
    "mesh_stats",
    "remove_depth_tilt",
    "orient_for_print",
    "print_optimize",
    "reconstruct_surface",
    "save_mesh",
    "simplify",
    "solidify",
    "split_for_print",
    "to_millimeters",
    "wall_thickness",
]
