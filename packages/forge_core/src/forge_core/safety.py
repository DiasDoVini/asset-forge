"""Proteções ocultas de geração (não aparecem na UI nem nos metadados dos assets).

1. Prompts de segurança injetados no grafo do ComfyUI em projetos +18:
   - sufixo POSITIVO (funciona mesmo com cfg=1, caso do Z-Image/Qwen/Wan Lightning);
   - NEGATIVO (só tem efeito em samplers com cfg > 1: checkpoints SD/SDXL, Wan TI2V, Wan sem Lightning).
2. Verificação visual local (Ollama, modelo com visão) de tudo que um projeto +18 gera: se a imagem parecer
   mostrar menor de idade ou automutilação, a versão é descartada antes de ser gravada. Falha fechada: sem o
   verificador disponível, a geração em projetos +18 é recusada.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from .content import ContentPolicyError

log = logging.getLogger("forge.safety")

HIDDEN_POSITIVE = ("all characters are adults over 21 years old with mature adult faces and fully developed adult "
                   "bodies, adult proportions")
HIDDEN_NEGATIVE = ("child, children, kid, minor, underage, teen, teenager, adolescent, young-looking, childlike, "
                   "loli, shota, baby face, child body, prepubescent, petite child body, flat child chest, school "
                   "uniform, schoolgirl, toddler, infant, self-harm, self-mutilation, cutting, wrist cuts, scars from "
                   "cutting, suicide, hanging, noose, blood from wrists, bestiality, rape, non-consensual")

# nós de texto → nome do campo de texto
_TEXT_FIELDS = {"CLIPTextEncode": "text", "TextEncodeQwenImageEditPlus": "prompt", "CLIPTextEncodeSDXL": "text_g",
                "TextEncodeZImageOmni": "prompt"}
_SAMPLERS = {"KSampler", "KSamplerAdvanced", "CFGGuider", "BasicGuider", "DualCFGGuider"}
_AUDIO_NODES = {"VAEDecodeAudio", "EmptyLatentAudio", "EmptyAceStep1.5LatentAudio", "TextEncodeAceStepAudio1.5"}


def _find_encoders(graph: dict[str, Any], ref: Any, side: str, seen: set[str]) -> list[str]:
    """Segue uma referência de condicionamento até os nós de texto que a originam."""
    if not (isinstance(ref, list) and len(ref) == 2 and isinstance(ref[0], str)):
        return []
    nid, slot = ref
    if nid in seen or nid not in graph:
        return []
    seen.add(nid)
    node = graph[nid]
    ct, inp = node.get("class_type", ""), node.get("inputs", {})
    if ct in _TEXT_FIELDS:
        return [nid]
    if ct == "ConditioningZeroOut":
        return []
    # nós que repassam (positive, negative) nas saídas 0/1 (WanImageToVideo, ControlNet, ...)
    if "positive" in inp and "negative" in inp:
        key = "positive" if slot == 0 else "negative" if slot == 1 else side
        return _find_encoders(graph, inp[key], side, seen)
    out: list[str] = []
    for k in ("conditioning", "conditioning_1", "conditioning_2", "conditioning_to", "conditioning_from", side):
        if k in inp:
            out += _find_encoders(graph, inp[k], side, seen)
    return out


def apply_to_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Anexa os prompts de segurança aos encoders positivos/negativos de cada sampler (cópia do grafo)."""
    if any(n.get("class_type") in _AUDIO_NODES for n in graph.values()):
        return graph  # áudio: não se aplica
    g = json.loads(json.dumps(graph))
    pos, neg = set(), set()
    for node in g.values():
        if node.get("class_type") in _SAMPLERS:
            inp = node.get("inputs", {})
            pos.update(_find_encoders(g, inp.get("positive"), "positive", set()))
            neg.update(_find_encoders(g, inp.get("negative"), "negative", set()))
    for nid in pos - neg:
        f = _TEXT_FIELDS[g[nid]["class_type"]]
        g[nid]["inputs"][f] = f"{g[nid]['inputs'].get(f, '')}, {HIDDEN_POSITIVE}".strip(", ")
    for nid in neg - pos:
        f = _TEXT_FIELDS[g[nid]["class_type"]]
        g[nid]["inputs"][f] = f"{g[nid]['inputs'].get(f, '')}, {HIDDEN_NEGATIVE}".strip(", ")
    return g


# ---------------------------------------------------------------- verificação visual

VISION_URL = os.environ.get("FORGE_OLLAMA_URL", "http://127.0.0.1:11434")
VISION_MODEL = os.environ.get("FORGE_VISION_MODEL", "")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "minor_present": {"type": "boolean"},
        "minor_confidence": {"type": "number"},
        "youngest_apparent_age": {"type": "number"},
        "self_harm": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["minor_present", "minor_confidence", "youngest_apparent_age", "self_harm", "reason"],
}

_VISION_PROMPT = (
    "You are a strict content-safety classifier for an adult-content image generator. Do not describe the image "
    "in detail; only assess it. 1) Estimate the apparent age of EVERY human or humanoid character (including "
    "anime, cartoon, 3D and stylized characters) from face, body proportions, height, clothing and context. "
    "Set minor_present=true if ANY character could reasonably be perceived as under 18 (childlike face or body, "
    "school context, child-like proportions), with minor_confidence from 0 to 1, and youngest_apparent_age as your "
    "best estimate (use 99 if there are no people). 2) Set self_harm=true if the image depicts self-harm, "
    "self-mutilation, suicide or its aftermath. Answer only with the JSON object.")


class VisionGuardError(ContentPolicyError):
    pass


def _vision_model() -> str:
    if VISION_MODEL:
        return VISION_MODEL
    try:
        r = httpx.get(f"{VISION_URL}/api/tags", timeout=3)
        r.raise_for_status()
    except httpx.HTTPError:
        raise VisionGuardError("verificação de segurança indisponível: abra o Ollama (projetos +18 exigem a "
                               "verificação visual local)") from None
    for m in r.json().get("models", []):
        if "vision" in (m.get("capabilities") or []):
            return m["name"]
    raise VisionGuardError("nenhum modelo de visão no Ollama para a verificação de segurança (ex.: ollama pull gemma4:12b)")


def _b64(img) -> str:
    img = img.convert("RGB")
    img.thumbnail((768, 768))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import glob

    hits = sorted(glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
                                         "Gyan.FFmpeg*", "*", "bin", "ffmpeg.exe")))
    return hits[-1] if hits else None


def _video_frames(path: Path, n: int = 4) -> list:
    from PIL import Image

    ff = _ffmpeg()
    if not ff:
        raise VisionGuardError("ffmpeg ausente: não é possível verificar o vídeo")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([ff, "-loglevel", "error", "-i", str(path), "-vf", f"thumbnail=12,fps=1,scale=640:-2",
                        "-frames:v", str(n), str(Path(td) / "f_%02d.jpg")], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        frames = []
        for f in sorted(Path(td).glob("f_*.jpg")):
            im = Image.open(f)
            im.load()
            frames.append(im)
    if not frames:
        raise VisionGuardError("não foi possível extrair quadros do vídeo para a verificação")
    return frames


def collect_images(paths: list[Path], limit: int = 6) -> list:
    from PIL import Image, ImageSequence

    imgs: list = []
    for p in paths:
        ext = p.suffix.lower()
        try:
            if ext in VIDEO_EXTS:
                imgs += _video_frames(p)
            elif ext in IMAGE_EXTS:
                im = Image.open(p)
                frames = [f.copy() for f in ImageSequence.Iterator(im)] if getattr(im, "n_frames", 1) > 1 else [im]
                pick = frames if len(frames) <= 2 else [frames[0], frames[len(frames) // 2], frames[-1]]
                for f in pick:
                    f.load()
                    imgs.append(f)
        except VisionGuardError:
            raise
        except Exception as e:  # arquivo não-imagem: ignora
            log.debug("ignorando %s: %s", p, e)
    return imgs[:limit]


def check_image(img) -> dict[str, Any]:
    model = _vision_model()
    try:
        r = httpx.post(f"{VISION_URL}/api/chat", timeout=300, json={
            "model": model, "stream": False, "think": False, "format": _VERDICT_SCHEMA, "keep_alive": "2m",
            "options": {"temperature": 0},
            "messages": [{"role": "user", "content": _VISION_PROMPT, "images": [_b64(img)]}],
        })
        r.raise_for_status()
        data = json.loads(r.json()["message"]["content"])
    except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
        raise VisionGuardError(f"verificação de segurança falhou ({e.__class__.__name__}); a geração foi descartada") from None
    return data


def blocked(v: dict[str, Any]) -> str | None:
    age = float(v.get("youngest_apparent_age", 99) or 99)
    if (v.get("minor_present") and float(v.get("minor_confidence", 1)) >= 0.4) or age < 18:
        return f"possível menor de idade (idade aparente {age:.0f})"
    if v.get("self_harm"):
        return "automutilação"
    return None


def verify_outputs(paths: list[Path], on_log=None) -> None:
    """Levanta ContentPolicyError se qualquer imagem/quadro for bloqueado. Sem imagens: nada a verificar."""
    imgs = collect_images(paths)
    for i, img in enumerate(imgs):
        v = check_image(img)
        why = blocked(v)
        if why:
            log.warning("verificação visual bloqueou uma geração: %s", why)
            raise ContentPolicyError(f"resultado descartado pela verificação de segurança: {why}")
        if on_log:
            on_log(f"verificação de segurança {i + 1}/{len(imgs)}: ok")
