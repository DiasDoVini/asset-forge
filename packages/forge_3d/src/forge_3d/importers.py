"""Importador 3D: GLB, glTF, OBJ, STL, PLY, OFF e 3MF.

Valida que há geometria, converte para `model.glb` (o formato base de todos os pipelines 3D e do viewer) e guarda o
arquivo original como fonte. Registra a análise de topologia (watertight, componentes, bounds) em `extra.mesh`.
"""
from __future__ import annotations

import shutil

from pydantic import BaseModel, ConfigDict, Field

from forge_core import ImportContext, Importer, OutputFile

from .geometry.analyze import mesh_stats
from .geometry.io import MESH_EXTENSIONS, load_mesh, save_mesh


class MeshOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep_source: bool = Field(True, description="guardar o arquivo original como fonte quando for convertido")


class MeshImporter(Importer):
    id = "3d.mesh"
    title = "Malha 3D (GLB, glTF, OBJ, STL, PLY, OFF, 3MF)"
    description = ("Carrega a malha, confere que há triângulos, converte para GLB (normais suaves nas quinas "
                   "suaves) e registra a topologia. GLB entra sem alterações (materiais e texturas preservados).")
    exts = set(MESH_EXTENSIONS)
    kind = "mesh"
    Options = MeshOptions

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        o = MeshOptions.model_validate(ctx.options or {})
        mesh = load_mesh(ctx.src)          # ValueError com mensagem clara se não houver geometria
        out = ctx.version_dir / "model.glb"
        files: list[OutputFile] = []
        if ctx.src.suffix.lower() == ".glb":
            shutil.copyfile(ctx.src, out)
        else:
            save_mesh(mesh, out)
            if o.keep_source:
                keep = ctx.version_dir / ("source" + ctx.src.suffix.lower())
                shutil.copyfile(ctx.src, keep)
                files.append(OutputFile(keep, "source"))
        ctx.extra().update({"mesh": mesh_stats(mesh), "original_format": ctx.src.suffix.lstrip(".").upper()})
        return [OutputFile(out, "primary", "model/gltf-binary"), *files]


IMPORTERS = [MeshImporter()]
