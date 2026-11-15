"""Small ComfyUI nodes used by Asset Forge pipelines (linked into the dedicated instance by install.ps1)."""

import os

import folder_paths
from comfy_api.latest import IO, ComfyExtension, Types
from typing_extensions import override


class ForgeLoad3DFile(IO.ComfyNode):
    """Load a mesh file from ComfyUI's input folder by path - the API-friendly counterpart of Load3D
    (which needs the editor's viewport state). Feed the result to Get3DComponents to get a MESH."""

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ForgeLoad3DFile",
            display_name="Load 3D File (Asset Forge)",
            category="3d",
            inputs=[
                IO.String.Input("path", default="", tooltip="file path relative to the input folder (e.g. forge/abc_model.glb)"),
            ],
            outputs=[IO.File3DAny.Output(display_name="model_3d")],
        )

    @classmethod
    def execute(cls, path: str) -> IO.NodeOutput:
        base = os.path.realpath(folder_paths.get_input_directory())
        full = os.path.realpath(os.path.join(base, path))
        if not full.startswith(base + os.sep):
            raise ValueError("ForgeLoad3DFile: path must stay inside the input folder")
        if not os.path.isfile(full):
            raise FileNotFoundError(f"ForgeLoad3DFile: {path} not found in the input folder")
        return IO.NodeOutput(Types.File3D(full))


class ForgeNodes(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [ForgeLoad3DFile]


async def comfy_entrypoint() -> ForgeNodes:
    return ForgeNodes()
