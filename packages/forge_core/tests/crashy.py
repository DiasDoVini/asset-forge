"""Modalidade de teste carregada no subprocesso via FORGE_MODALITIES=crashy:modality."""
import faulthandler
import time

from PIL import Image

from forge_core.pipeline import Modality, OutputFile, Pipeline, PipelineParams


class OkParams(PipelineParams):
    prompt: str = "ok"


class Ok(Pipeline):
    id = "crashy.ok"
    resources = {"cpu"}
    Params = OkParams

    def run(self, ctx, params):
        ctx.log("olá do worker")
        ctx.progress(0.5, "meio")
        out = ctx.version_dir / "image.png"
        Image.new("RGB", (4, 4), "red").save(out)
        return [OutputFile(out)]


class Segfault(Pipeline):
    id = "crashy.segfault"
    resources = {"cpu"}

    def run(self, ctx, params):
        (ctx.version_dir / "partial.bin").write_bytes(b"x")
        faulthandler._read_null()  # access violation nativo (sem SEH do ctypes)
        return []


class Hang(Pipeline):
    id = "crashy.hang"
    resources = {"cpu"}

    def run(self, ctx, params):
        time.sleep(600)  # ignora cancelamento: o pai precisa matar o processo
        return []


modality = Modality(name="crashy", pipelines=[Ok(), Segfault(), Hang()])
