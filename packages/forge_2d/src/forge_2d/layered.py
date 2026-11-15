"""Arquivos de outros editores em camadas, no servidor (CLI/MCP): Pixelorama (.pxo) e Krita (.kra).

Pela galeria, o navegador já converte .pxo/.psd/.kra para .aseprite com as camadas (o editor de sprite abre). Aqui o
caminho é o achatado: .pxo vira animação (camadas compostas com opacidade e modo de mistura, quadros e tags) e .kra
vira imagem a partir do mergedimage.png que o Krita grava dentro do arquivo. O original fica guardado como fonte.
"""
from __future__ import annotations

import io
import json
import shutil
import zipfile
from typing import Any

import numpy as np
from PIL import Image

from forge_core import ImportContext, Importer, OutputFile

from .aseprite import BLEND_NAMES, _blend

# BaseLayer.BlendModes do Pixelorama (NORMAL = 0) -> nome do Aseprite (o mesmo mapa de web/.../formats/layered.ts)
_PXO_BLEND = ["normal", "darken", "multiply", "color_burn", "color_burn", "lighten", "screen", "color_dodge", "addition",
              "overlay", "overlay", "hard_light", "difference", "exclusion", "subtract", "divide"]


def _godot_color(s: str | None) -> str:
    try:
        vals = [float(v) for v in (s or "").split("(", 1)[1].split(")", 1)[0].split(",")[:3]]
        return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02x}" for v in vals)
    except (IndexError, ValueError):
        return "#f59e0b"


def read_pxo(data: bytes) -> tuple[list[Image.Image], list[int], list[dict[str, Any]], dict[str, Any]]:
    """Lê um .pxo (Pixelorama 1.x) e devolve (quadros compostos, durações em ms, tags, info)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(".pxo antigo (antes do Pixelorama 1.0) não é suportado; salve de novo numa versão recente") from e
    with zf:
        try:
            j = json.loads(zf.read("data.json").decode("utf-8"))
        except KeyError as e:
            raise ValueError(".pxo sem data.json") from e
        w, h = int(j["size_x"]), int(j["size_y"])
        fps = float(j.get("fps") or 6)
        layers: list[dict[str, Any]] = j.get("layers", [])
        names = set(zf.namelist())

        def visible(i: int) -> bool:
            seen = 0
            while i >= 0 and seen < 64:
                if not layers[i].get("visible", True):
                    return False
                i = int(layers[i].get("parent", -1))
                seen += 1
            return True

        def group_opacity(i: int) -> float:
            op, p = 1.0, int(layers[i].get("parent", -1))
            while p >= 0:
                op *= float(layers[p].get("opacity", 1))
                p = int(layers[p].get("parent", -1))
            return op

        frames, durations = [], []
        for f, fr in enumerate(j.get("frames", [])):
            canvas = np.zeros((h, w, 4), np.float32)
            clip_base: np.ndarray | None = None
            for c, ly in enumerate(layers):
                if ly.get("type", 0) not in (0, 3):   # grupos, 3D e áudio não têm pixels
                    continue
                name = f"image_data/frames/{f + 1}/layer_{c + 1}"
                raw = zf.read(name) if name in names else b""
                if len(raw) < w * h * 4:
                    if not ly.get("clipping_mask"):
                        clip_base = None
                    continue
                src = np.frombuffer(raw[: w * h * 4], np.uint8).reshape(h, w, 4).astype(np.float32) / 255.0
                cels = fr.get("cels", [])
                src[..., 3] *= float(cels[c].get("opacity", 1)) if c < len(cels) else 1.0
                if ly.get("clipping_mask"):
                    if clip_base is None:
                        continue
                    src[..., 3] *= clip_base
                else:
                    clip_base = src[..., 3].copy() if visible(c) else None
                if not visible(c):
                    continue
                src[..., 3] *= float(ly.get("opacity", 1)) * group_opacity(c)
                blend = _PXO_BLEND[int(ly.get("blend_mode", 0))] if int(ly.get("blend_mode", 0)) < len(_PXO_BLEND) else "normal"
                canvas = _blend(canvas, src, BLEND_NAMES.index(blend))
            frames.append(Image.fromarray(np.clip(canvas * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA"))
            durations.append(max(1, round(1000 / fps * float(fr.get("duration", 1)))))
        n = len(frames)
        tags = [{"name": t.get("name", "tag"), "from": max(0, int(t["from"]) - 1), "to": min(n - 1, max(0, int(t["to"]) - 1)),
                 "direction": "forward", "repeat": 0, "color": _godot_color(t.get("color"))} for t in j.get("tags", [])]
        info = {"pixelorama": {"size": [w, h], "fps": fps,
                               "layers": [{"name": ly.get("name"), "visible": ly.get("visible", True),
                                           "opacity": ly.get("opacity", 1), "group": ly.get("type") == 1} for ly in layers]}}
        return frames, durations, tags, info


class PixeloramaImporter(Importer):
    id = "2d.pixelorama"
    title = "Pixelorama (.pxo)"
    description = ("Lê o projeto do Pixelorama 1.x: camadas (opacidade, modo de mistura, grupos, máscara de recorte), "
                   "quadros com duração e tags. Vira animação (ou imagem, com 1 quadro); o .pxo fica como fonte. "
                   "Pela galeria do navegador as camadas são preservadas (vira .aseprite editável).")
    exts = {".pxo"}
    kind = "animation"

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        from .importers import _save_animation

        frames, durations, tags, info = read_pxo(ctx.src.read_bytes())
        if len(frames) == 1:
            ctx.meta["kind"] = "image"
            img = ctx.version_dir / "image.png"
            frames[0].save(img)
            keep = ctx.version_dir / "source.pxo"
            shutil.copyfile(ctx.src, keep)
            ctx.extra().update(info)
            return [OutputFile(img, "primary", "image/png"), OutputFile(keep, "source")]
        return _save_animation(ctx, frames, durations, tags=tags, source=ctx.src, source_role_name="source.pxo", extra=info)


class KritaImporter(Importer):
    id = "2d.krita"
    title = "Krita (.kra)"
    description = ("Usa a imagem composta que o Krita grava no arquivo (mergedimage.png) e guarda o .kra como fonte. "
                   "Pela galeria do navegador as camadas e a animação são preservadas (vira .aseprite editável).")
    exts = {".kra"}
    kind = "image"

    def run(self, ctx: ImportContext) -> list[OutputFile]:
        try:
            with zipfile.ZipFile(ctx.src) as zf:
                im = Image.open(io.BytesIO(zf.read("mergedimage.png")))
                im.load()
        except (zipfile.BadZipFile, KeyError) as e:
            raise ValueError("arquivo .kra inválido (sem mergedimage.png)") from e
        out = ctx.version_dir / "image.png"
        im.convert("RGBA").save(out)
        keep = ctx.version_dir / "source.kra"
        shutil.copyfile(ctx.src, keep)
        ctx.extra().update({"original_format": "KRA", "size": list(im.size)})
        return [OutputFile(out, "primary", "image/png"), OutputFile(keep, "source")]
