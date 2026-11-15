"""The "3d" block of project.yaml, validated.

Example::

    3d:
      units: m                 # unit of generated/imported meshes
      poly_budget: 20000       # default target for 3d.simplify
      lowpoly_faces: 800       # default target for 3d.lowpoly
      export_format: glb
      default_height: 1.8      # generated meshes are scaled to this height (optional)
      comfy_url: http://127.0.0.1:8189
      printer:
        name: Bambu A1
        build_volume_mm: [256, 256, 256]
        nozzle_mm: 0.4
        min_wall_mm: 0.8
        tolerance_mm: 0.2
        max_overhang_deg: 45
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .geometry.printing import PrinterProfile

MeshFormat = Literal["glb", "obj", "stl", "ply", "3mf"]


class Settings3D(BaseModel):
    model_config = ConfigDict(extra="ignore")

    units: Literal["mm", "cm", "m", "in"] = "m"
    poly_budget: int = 20000
    lowpoly_faces: int = 800
    export_format: MeshFormat = "glb"
    default_height: float | None = Field(None, gt=0, description="height (project units) given to generated meshes")
    comfy_url: str | None = Field(None, description="ComfyUI for 3D generation (default: dedicated :8189, then FORGE_COMFY_URL)")
    models: dict[str, str] = Field(default_factory=dict, description="override generation model files")
    generator: str | None = Field(None, description="default shape model for image/text -> 3D: hunyuan3d-2.1 or trellis2")
    trellis_url: str | None = Field(None, description="TRELLIS.2 ComfyUI (default: :8190)")
    printer: PrinterProfile = Field(default_factory=PrinterProfile)
    palette: list[str] = Field(default_factory=list, description="merged from style.palette")

    @classmethod
    def from_style(cls, style: dict[str, Any]) -> "Settings3D":
        return cls.model_validate(style)
