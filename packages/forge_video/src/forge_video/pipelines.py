"""Pipelines de vídeo.

Cada versão grava: video.mp4 (primary, H.264), poster.png (preview), video.json (source: metadados + marcações).
Marcações: [{"t": 1.25, "label": "golpe", "color": "#f59e0b"}] — usadas pelo editor e por video.regen_segment.

Bloco opcional no project.yaml:
    video: {engine: ti2v5b | wan14, aspect: "16:9", quality: 720p, models: {...}}
"""
from __future__ import annotations

import json
import random
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from forge_core import ComfyError, OutputFile, Pipeline, PipelineParams
from forge_core.pipeline import Context
from forge_core.runner import resolve_input

from . import edit, ff, graphs

Engine = Literal["auto", "ti2v5b", "wan14"]
Aspect = Literal["16:9", "9:16", "1:1", "4:3", "3:4"]
SIZES = {  # (largura, altura), múltiplos de 32
    "720p": {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (960, 960), "4:3": (1088, 832), "3:4": (832, 1088)},
    "480p": {"16:9": (832, 480), "9:16": (480, 832), "1:1": (640, 640), "4:3": (640, 480), "3:4": (480, 640)},
}


class Marker(BaseModel):
    model_config = ConfigDict(extra="ignore")
    t: float = Field(..., ge=0)
    label: str = ""
    color: str = "#f59e0b"


# ---------------------------------------------------------------- helpers

def _models(style: dict[str, Any]) -> dict[str, str]:
    m = dict(graphs.DEFAULT_MODELS)
    m.update(style.get("models") or {})
    return m


def _seed(seed: int | None) -> int:
    return seed if seed is not None and seed >= 0 else random.randint(0, 2**31 - 1)


def _ensure_comfy(ctx: Context) -> None:
    """ComfyUI configurado se ele enxergar os modelos; senão outra instância do Asset Forge no ar."""
    from forge_core.comfy_select import use_instance

    use_instance(ctx, what="o vídeo")


def _has(ctx: Context, folder: str, name: str) -> bool:
    return name in ctx.comfy.models(folder)


def pick_engine(ctx: Context, engine: Engine, models: dict[str, str], *, needs_end: bool = False) -> str:
    ti2v_ok = _has(ctx, "diffusion_models", models["ti2v_unet"]) and _has(ctx, "vae", models["ti2v_vae"])
    wan_ok = _has(ctx, "diffusion_models", models["wan_high"]) and _has(ctx, "diffusion_models", models["wan_low"])
    if engine == "auto":
        engine = "wan14" if needs_end or not ti2v_ok else "ti2v5b"
    if engine == "ti2v5b" and not ti2v_ok:
        raise ComfyError("Wan 2.2 TI2V 5B ausente: python scripts/download_models.py video")
    if engine == "wan14" and not wan_ok:
        raise ComfyError("Wan 2.2 14B i2v ausente no ComfyUI")
    if needs_end and engine != "wan14":
        raise ValueError("quadro final (first-last-frame) só funciona com engine wan14")
    return engine


def _upload(ctx: Context, img: Image.Image, name: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        img.convert("RGB").save(p)
        return ctx.comfy.upload_image(p)


def _fit(img: Image.Image, w: int, h: int) -> Image.Image:
    """Recorta/escala para w×h preenchendo (cover)."""
    img = img.convert("RGB")
    k = max(w / img.width, h / img.height)
    r = img.resize((round(img.width * k), round(img.height * k)), Image.Resampling.LANCZOS)
    x, y = (r.width - w) // 2, (r.height - h) // 2
    return r.crop((x, y, x + w, y + h))


def _image_of(ctx: Context, ref: str) -> Image.Image:
    av, path = resolve_input(ctx.project, ref)
    if av is not None:
        files = ctx.project.store.files_of(av, "primary")
        path = files[0]
        if path.suffix.lower() in (".mp4", ".webm", ".mov"):
            return ff.frame_at(path, -0.001)
    im = Image.open(path)
    im.load()
    return im


def _input_video(ctx: Context) -> Path:
    files = ctx.input_files("primary") or ctx.input_files()
    vids = [f for f in files if f.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv")]
    if not vids:
        raise ValueError("a entrada não é um vídeo")
    return vids[0]


def _input_markers(ctx: Context) -> list[dict[str, Any]]:
    return list(((ctx.input.extra if ctx.input else None) or {}).get("markers") or [])


def _gen_frames(ctx: Context, graph: graphs.Graph, node: str, label: str, span=(0.05, 0.85)) -> list[Image.Image]:
    ctx.check_cancelled()
    a, b = span
    ctx.progress(a, label)
    outs = ctx.comfy.run_workflow(graph.to_dict(), on_progress=lambda v, m: ctx.progress(a + (b - a) * v, label))
    frames = []
    for o in sorted((o for o in outs if o.node_id == node), key=lambda o: o.filename):
        import io

        im = Image.open(io.BytesIO(ctx.comfy.download(o.filename, o.subfolder, o.type)))
        im.load()
        frames.append(im.convert("RGB"))
    if not frames:
        raise ComfyError("o workflow não produziu quadros")
    ctx.progress(b, "montando vídeo")
    return frames


def write_video(ctx: Context, mp4: Path, *, markers: list[dict[str, Any]] | None = None,
                extra: dict[str, Any] | None = None) -> list[OutputFile]:
    d = ctx.version_dir
    out = d / "video.mp4"
    if mp4 != out:
        shutil.copyfile(mp4, out)
    info = ff.probe(out)
    ff.poster(out, d / "poster.png")
    ms = sorted([Marker(**m).model_dump() for m in (markers or []) if 0 <= float(m.get("t", -1)) <= info.duration],
                key=lambda m: m["t"])
    (d / "video.json").write_text(json.dumps({"info": info.to_dict(), "markers": ms}, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
    ctx.meta["kind"] = "video"
    ctx.meta.setdefault("extra", {}).update({**(extra or {}), "video": info.to_dict(), "markers": ms})
    return [OutputFile(out, "primary", "video/mp4"), OutputFile(d / "poster.png", "preview"),
            OutputFile(d / "video.json", "source", "application/json")]


def _style_prompt(ctx: Context, prompt: str, use_style: bool) -> str:
    style = ctx.style()
    extra = style.get("video_prompt") or (ctx.project.config.style.prompt if use_style else "")
    return f"{prompt}, {extra}" if use_style and extra else prompt


def _generate_clip(ctx: Context, *, prompt: str, engine: str, w: int, h: int, seconds: float, seed: int,
                   models: dict[str, str], start: Image.Image | None = None, end: Image.Image | None = None,
                   negative: str = graphs.WAN_NEGATIVE, steps: int | None = None, span=(0.05, 0.85)) -> tuple[list[Image.Image], int]:
    """Gera os quadros de um clipe; devolve (quadros, fps)."""
    if engine == "ti2v5b":
        n = graphs.frames_for(seconds, graphs.TI2V_FPS)
        name = _upload(ctx, _fit(start, w, h), "start.png") if start is not None else None
        g, node = graphs.ti2v(prompt, w, h, n, seed, models=models, start_image=name, negative=negative, steps=steps or 20)
        return _gen_frames(ctx, g, node, "gerando vídeo (TI2V 5B)", span), graphs.TI2V_FPS
    if start is None:
        raise ValueError("wan14 precisa de um quadro inicial")
    n = graphs.frames_for(seconds, graphs.I2V_FPS)
    s_name = _upload(ctx, _fit(start, w, h), "start.png")
    e_name = _upload(ctx, _fit(end, w, h), "end.png") if end is not None else None
    g, node = graphs.i2v14(prompt, s_name, w, h, n, seed, models=models, end_image=e_name, negative=negative, steps=steps)
    return _gen_frames(ctx, g, node, "gerando vídeo (Wan 14B)", span), graphs.I2V_FPS


# ---------------------------------------------------------------- Text → Vídeo

class GenerateParams(PipelineParams):
    prompt: str = Field(..., description="cena + ação + câmera: 'a knight swings a flaming sword, slow motion, camera orbits'")
    engine: Engine = Field("auto", description="ti2v5b: rápido, 24 fps, text→vídeo nativo; wan14: melhor movimento (gera o 1º quadro com Z-Image)")
    duration: float = Field(5.0, ge=1.0, le=10.0, description="segundos")
    aspect: Aspect = "16:9"
    quality: Literal["480p", "720p"] = "720p"
    first_frame: str | None = Field(None, description="imagem (asset/arquivo) para começar — vira imagem → vídeo")
    negative: str = Field("", description="vazio = negativo padrão do Wan")
    use_style: bool = Field(True, description="anexar o prompt de estilo do projeto")
    seed: int | None = None
    steps: int | None = Field(None, ge=1, le=60)


class TextToVideo(Pipeline):
    id = "video.generate"
    title = "Text → Vídeo"
    description = ("Vídeo a partir de texto com Wan 2.2 (TI2V 5B a 24 fps, ou 14B com 1º quadro gerado pelo Z-Image). "
                   "Cutscenes, trailers, fundos animados, referências de animação.")
    resources = {"gpu"}
    kind = "video"
    Params = GenerateParams

    def run(self, ctx: Context, p: GenerateParams) -> list[OutputFile]:
        style = ctx.style()
        models = _models(style)
        _ensure_comfy(ctx)
        engine = pick_engine(ctx, p.engine if p.engine != "auto" else style.get("engine", "auto"), models)
        w, h = SIZES[p.quality][p.aspect]
        seed = _seed(p.seed)
        prompt = _style_prompt(ctx, p.prompt, p.use_style)
        start = _image_of(ctx, p.first_frame) if p.first_frame else None
        if start is None and engine == "wan14":  # 1º quadro com Z-Image
            from forge_2d import graphs as g2
            from forge_2d.pipelines import _run

            ctx.log("gerando o 1º quadro com Z-Image")
            g, img_id, _ = g2.text2img_zimage(prompt, w, h, seed, 8, models=models)
            start = _run(ctx, g, [img_id], span=(0.02, 0.15), label="1º quadro")[img_id]
        ctx.log(f"{engine} {w}x{h} {p.duration}s | {prompt}")
        frames, fps = _generate_clip(ctx, prompt=prompt, engine=engine, w=w, h=h, seconds=p.duration, seed=seed,
                                     models=models, start=start, negative=p.negative or graphs.WAN_NEGATIVE,
                                     steps=p.steps, span=(0.15 if start is not None else 0.05, 0.9))
        with tempfile.TemporaryDirectory() as td:
            mp4 = ff.encode_frames(frames, fps, Path(td) / "v.mp4")
            ctx.meta.update(seed=seed, prompt=p.prompt)
            return write_video(ctx, mp4, extra={"engine": engine, "full_prompt": prompt, "aspect": p.aspect})


# ---------------------------------------------------------------- Imagem → Vídeo

class AnimateParams(PipelineParams):
    prompt: str = Field("", description="o movimento: 'the dragon spreads its wings and roars, camera pushes in'")
    engine: Engine = Field("auto", description="auto: wan14 se tiver quadro final, senão TI2V 5B")
    duration: float = Field(5.0, ge=1.0, le=10.0)
    end_image: str | None = Field(None, description="quadro final (asset/arquivo) — interpola entre os dois (wan14)")
    loop: bool = Field(False, description="termina no mesmo quadro em que começa (loop perfeito; wan14)")
    quality: Literal["480p", "720p"] = "720p"
    aspect: Literal["auto", "16:9", "9:16", "1:1", "4:3", "3:4"] = Field("auto", description="auto = proporção da imagem")
    negative: str = ""
    seed: int | None = None
    steps: int | None = Field(None, ge=1, le=60)


def _aspect_of(img: Image.Image) -> str:
    """Proporção suportada mais próxima da imagem."""
    r = img.width / img.height
    ratios = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4}
    return min(ratios, key=lambda k: abs(r - ratios[k]))


class ImageToVideo(Pipeline):
    id = "video.animate"
    title = "Imagem → Vídeo"
    description = "Anima uma imagem (arte conceitual, personagem, cenário) com Wan 2.2; opcional quadro final ou loop."
    resources = {"gpu"}
    kind = "video"
    requires_input = True
    Params = AnimateParams

    def default_slug(self, params: AnimateParams) -> str:
        return Path((params.input_asset or "video").split("@")[0]).stem + "-video"

    def run(self, ctx: Context, p: AnimateParams) -> list[OutputFile]:
        style = ctx.style()
        models = _models(style)
        _ensure_comfy(ctx)
        files = ctx.input_files("primary") or ctx.input_files()
        start = ff.frame_at(files[0], -0.001) if files[0].suffix.lower() in (".mp4", ".webm", ".mov") else Image.open(files[0]).convert("RGB")
        end = start if p.loop else (_image_of(ctx, p.end_image) if p.end_image else None)
        engine = pick_engine(ctx, p.engine, models, needs_end=end is not None)
        aspect = _aspect_of(start) if p.aspect == "auto" else p.aspect
        w, h = SIZES[p.quality][aspect]
        seed = _seed(p.seed)
        prompt = p.prompt or (ctx.input.prompt if ctx.input and ctx.input.prompt else "subtle natural motion")
        frames, fps = _generate_clip(ctx, prompt=prompt, engine=engine, w=w, h=h, seconds=p.duration, seed=seed,
                                     models=models, start=start, end=end, negative=p.negative or graphs.WAN_NEGATIVE,
                                     steps=p.steps)
        if p.loop and len(frames) > 2:
            frames = frames[:-1]  # o último repete o primeiro
        with tempfile.TemporaryDirectory() as td:
            mp4 = ff.encode_frames(frames, fps, Path(td) / "v.mp4")
            ctx.meta.update(seed=seed, prompt=p.prompt or None)
            return write_video(ctx, mp4, extra={"engine": engine, "loop": p.loop})


# ---------------------------------------------------------------- Estender

class ExtendParams(PipelineParams):
    prompt: str = Field("", description="o que acontece a seguir (vazio = continua o movimento)")
    seconds: float = Field(4.0, ge=1.0, le=10.0)
    engine: Engine = "auto"
    seed: int | None = None


class ExtendVideo(Pipeline):
    id = "video.extend"
    title = "Estender vídeo"
    description = "Continua o vídeo a partir do último quadro (IA) e emenda no final. Nova versão do mesmo asset."
    resources = {"gpu"}
    requires_input = True
    same_asset = True
    Params = ExtendParams

    def run(self, ctx: Context, p: ExtendParams) -> list[OutputFile]:
        style = ctx.style()
        models = _models(style)
        _ensure_comfy(ctx)
        src = _input_video(ctx)
        info = ff.probe(src)
        last = ff.frame_at(src, -0.001)
        engine = pick_engine(ctx, p.engine, models)
        seed = _seed(p.seed)
        prompt = p.prompt or (ctx.input.prompt if ctx.input and ctx.input.prompt else "continue the motion naturally")
        frames, fps = _generate_clip(ctx, prompt=prompt, engine=engine, w=info.width // 32 * 32, h=info.height // 32 * 32,
                                     seconds=p.seconds, seed=seed, models=models, start=last)
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            clip = ff.encode_frames(frames[1:], fps, td / "clip.mp4")  # o 1º quadro repete o último
            out = edit.apply(src, {"op": "concat", "path": str(clip), "transition": "none"}, td / "out.mp4")
            ctx.meta.update(seed=seed, prompt=ctx.input.prompt if ctx.input else None)
            return write_video(ctx, out, markers=_input_markers(ctx) + [{"t": info.duration, "label": "extensão", "color": "#22c55e"}],
                               extra={"extended_with": prompt, "engine": engine})


# ---------------------------------------------------------------- Regenerar trecho (IA entre marcações)

class RegenParams(PipelineParams):
    prompt: str = Field(..., description="o que deve acontecer no trecho: 'the hero dodges to the left'")
    start: float | None = Field(None, ge=0, description="início do trecho (s) — ou use start_marker")
    end: float | None = Field(None, ge=0, description="fim do trecho (s) — ou use end_marker")
    start_marker: str | None = Field(None, description="rótulo da marcação de início")
    end_marker: str | None = Field(None, description="rótulo da marcação de fim")
    seed: int | None = None
    steps: int | None = Field(None, ge=1, le=60)


class RegenSegment(Pipeline):
    id = "video.regen_segment"
    title = "Regenerar trecho (IA)"
    description = ("Refaz com IA o trecho entre duas marcações/tempos, mantendo o quadro de entrada e o de saída "
                   "(Wan 14B first-last-frame) — corrige ou muda uma ação sem perder o resto do vídeo.")
    resources = {"gpu"}
    requires_input = True
    same_asset = True
    Params = RegenParams

    def run(self, ctx: Context, p: RegenParams) -> list[OutputFile]:
        style = ctx.style()
        models = _models(style)
        _ensure_comfy(ctx)
        src = _input_video(ctx)
        info = ff.probe(src)
        markers = _input_markers(ctx)

        def at(t: float | None, label: str | None, what: str) -> float:
            if label:
                m = next((m for m in markers if m.get("label") == label), None)
                if m is None:
                    raise ValueError(f"marcação '{label}' não existe (tem: {[m.get('label') for m in markers]})")
                return float(m["t"])
            if t is None:
                raise ValueError(f"informe {what} ou {what}_marker")
            return t

        s = at(p.start, p.start_marker, "start")
        e = at(p.end, p.end_marker, "end")
        if not 0 <= s < e <= info.duration + 1e-3 or e - s < 0.3:
            raise ValueError(f"trecho inválido: {s:.2f}–{e:.2f}s (vídeo de {info.duration:.2f}s, mínimo 0,3 s)")
        engine = pick_engine(ctx, "wan14", models, needs_end=True)
        a, b = ff.frame_at(src, s), ff.frame_at(src, min(e, info.duration - 1e-3))
        w, h = info.width // 32 * 32, info.height // 32 * 32
        if max(w, h) > 1280:
            k = 1280 / max(w, h)
            w, h = int(w * k) // 32 * 32, int(h * k) // 32 * 32
        seed = _seed(p.seed)
        frames, fps = _generate_clip(ctx, prompt=p.prompt, engine=engine, w=w, h=h, seconds=e - s, seed=seed,
                                     models=models, start=a, end=b, steps=p.steps)
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            clip = ff.encode_frames(frames, fps, td / "clip.mp4")
            # ajusta a duração do clipe ao trecho (o Wan arredonda para 4k+1 quadros)
            ci = ff.probe(clip)
            if abs(ci.duration - (e - s)) > 0.05:
                clip = edit.apply(clip, {"op": "speed", "factor": ci.duration / (e - s)}, td / "clip2.mp4")
            out = edit.apply(src, {"op": "splice", "start": s, "end": e, "path": str(clip)}, td / "out.mp4")
            ctx.meta.update(seed=seed, prompt=ctx.input.prompt if ctx.input else None)
            return write_video(ctx, out, markers=markers, extra={"regenerated": {"start": s, "end": e, "prompt": p.prompt}})


# ---------------------------------------------------------------- Edição manual

class Op(BaseModel):
    model_config = ConfigDict(extra="allow")
    op: Literal["trim", "cut", "speed", "reverse", "loop", "fade", "crop", "resize", "fps", "color", "concat", "audio",
                "mute", "splice"]


class EditParams(PipelineParams):
    ops: list[Op] = Field(default_factory=list, description="operações em ordem: " + edit.OPS_HELP)
    trim_start: float | None = Field(None, ge=0)
    trim_end: float | None = Field(None, ge=0)
    speed: float = Field(1.0, ge=0.1, le=8.0)
    reverse: bool = False
    loop: int = Field(1, ge=1, le=20, description="repetições")
    pingpong: bool = Field(False, description="ida e volta (boomerang)")
    fade_in: float = Field(0.0, ge=0, le=10)
    fade_out: float = Field(0.0, ge=0, le=10)
    width: int | None = Field(None, ge=64, le=4096, description="redimensionar (altura proporcional)")
    fps: float | None = Field(None, ge=1, le=120)
    smooth_fps: bool = Field(False, description="interpolar quadros ao mudar o fps (minterpolate)")
    append: list[str] = Field(default_factory=list, description="vídeos (assets) para emendar no final")
    transition: Literal["none", "fade", "dissolve", "wipeleft", "slideleft"] = "fade"
    transition_s: float = Field(0.5, ge=0.1, le=3)
    audio_asset: str | None = Field(None, description="áudio (asset) para a trilha")
    audio_mode: Literal["replace", "mix"] = "replace"
    audio_offset: float = Field(0.0, ge=0)
    audio_volume: float = Field(1.0, ge=0, le=4)
    mute: bool = False
    markers: list[Marker] | None = Field(None, description="substitui as marcações (vazio = mantém/ajusta as atuais)")


def _asset_file(ctx: Context, ref: str) -> Path:
    av, path = resolve_input(ctx.project, ref)
    if av is not None:
        path = ctx.project.store.files_of(av, "primary")[0]
    if path is None:
        raise ValueError(f"não encontrado: {ref}")
    return Path(path)


def edit_ops(ctx: Context, p: EditParams) -> list[dict[str, Any]]:
    ops = [o.model_dump() for o in p.ops]
    for o in ops:  # refs de assets em ops → caminhos
        for k in ("path", "asset"):
            if o.get(k) and not Path(str(o[k])).is_file():
                o["path"] = str(_asset_file(ctx, str(o.pop(k))))
    if p.trim_start is not None or p.trim_end is not None:
        ops.append({"op": "trim", "start": p.trim_start or 0, "end": p.trim_end})
    if p.reverse:
        ops.append({"op": "reverse"})
    if p.speed != 1.0:
        ops.append({"op": "speed", "factor": p.speed})
    if p.pingpong or p.loop > 1:
        ops.append({"op": "loop", "count": p.loop, "mode": "pingpong" if p.pingpong else "repeat"})
    for ref in p.append:
        ops.append({"op": "concat", "path": str(_asset_file(ctx, ref)), "transition": p.transition, "duration": p.transition_s})
    if p.width:
        ops.append({"op": "resize", "width": p.width, "height": -2})
    if p.fps:
        ops.append({"op": "fps", "fps": p.fps, "smooth": p.smooth_fps})
    if p.fade_in or p.fade_out:
        ops.append({"op": "fade", "in_s": p.fade_in, "out_s": p.fade_out})
    if p.mute:
        ops.append({"op": "mute"})
    if p.audio_asset:
        ops.append({"op": "audio", "path": str(_asset_file(ctx, p.audio_asset)), "mode": p.audio_mode,
                    "offset": p.audio_offset, "volume": p.audio_volume})
    return ops


def shift_markers(markers: list[dict[str, Any]], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ajusta marcações para trims/cortes/velocidade (as outras ops mantêm os tempos)."""
    ms = [dict(m) for m in markers]
    for o in ops:
        if o["op"] == "trim":
            s, e = float(o.get("start") or 0), o.get("end")
            ms = [{**m, "t": m["t"] - s} for m in ms if m["t"] >= s and (e is None or m["t"] <= float(e))]
        elif o["op"] == "cut":
            s, e = float(o["start"]), float(o["end"])
            ms = [{**m, "t": m["t"] - (e - s) if m["t"] >= e else m["t"]} for m in ms if not s < m["t"] < e]
        elif o["op"] == "speed":
            f = float(o.get("factor", 1))
            ms = [{**m, "t": m["t"] / f} for m in ms]
        elif o["op"] == "reverse":
            ms = []  # tempos invertidos não fazem sentido para rótulos
    return ms


class VideoEdit(Pipeline):
    id = "video.edit"
    title = "Editar vídeo"
    description = ("Corta, remove trechos, velocidade, inverte, loop/boomerang, fades, emenda com transições, "
                   "trilha de áudio (assets de áudio), redimensiona, fps e marcações. Nova versão (CPU/ffmpeg).")
    resources = {"cpu"}
    requires_input = True
    same_asset = True
    Params = EditParams

    def run(self, ctx: Context, p: EditParams) -> list[OutputFile]:
        src = _input_video(ctx)
        ops = edit_ops(ctx, p)
        if not ops and p.markers is None:
            raise ValueError("nenhuma edição pedida")
        with tempfile.TemporaryDirectory() as tds:
            out = Path(tds) / "out.mp4"
            if ops:
                edit.apply_all(src, ops, out, on_step=lambda i, op: ctx.progress(i / len(ops), op["op"]))
            else:
                shutil.copyfile(src, out)
            markers = [m.model_dump() for m in p.markers] if p.markers is not None else shift_markers(_input_markers(ctx), ops)
            keep = {k: v for k, v in ((ctx.input.extra if ctx.input else None) or {}).items() if k not in ("video", "markers", "ops")}
            ctx.meta.update(prompt=ctx.input.prompt if ctx.input else None)
            return write_video(ctx, out, markers=markers, extra={**keep, "ops": ops})


# ---------------------------------------------------------------- Imagens/animação → vídeo

class FromFramesParams(PipelineParams):
    fps: float | None = Field(None, ge=1, le=60, description="vazio = fps da animação (ou 12)")
    scale: int = Field(1, ge=1, le=16, description="ampliação nearest (pixel art)")
    loops: int = Field(1, ge=1, le=50)
    background: str = Field("#000000", description="cor de fundo (vídeo não tem transparência)")


class FromAnimation(Pipeline):
    id = "video.from_animation"
    title = "Animação 2D → Vídeo"
    description = "Converte uma animação de sprites (2D) em MP4 com fundo sólido, escala e repetições."
    resources = {"cpu"}
    kind = "video"
    requires_input = True
    Params = FromFramesParams

    def default_slug(self, params: FromFramesParams) -> str:
        return Path((params.input_asset or "anim").split("@")[0]).stem + "-video"

    def run(self, ctx: Context, p: FromFramesParams) -> list[OutputFile]:
        from forge_2d.animation import Animation

        if ctx.input is None or ctx.input.kind != "animation":
            raise ValueError("a entrada deve ser uma animação 2D")
        anim = Animation.load(ctx.project.store.version_dir(ctx.input.slug, ctx.input.version))
        fps = p.fps or (1000 / (sum(anim.durations) / len(anim.durations)) if anim.durations else 12)
        bg = Image.new("RGB", anim.frames[0].size, p.background)
        frames = []
        for _ in range(p.loops):
            for f in anim.frames:
                im = bg.copy()
                im.paste(f.convert("RGBA"), (0, 0), f.convert("RGBA"))
                if p.scale > 1:
                    im = im.resize((im.width * p.scale, im.height * p.scale), Image.Resampling.NEAREST)
                frames.append(im)
        with tempfile.TemporaryDirectory() as td:
            mp4 = ff.encode_frames(frames, fps, Path(td) / "v.mp4")
            return write_video(ctx, mp4, extra={"from_animation": f"{ctx.input.slug}@v{ctx.input.version:03d}"})


PIPELINES: list[Pipeline] = [TextToVideo(), ImageToVideo(), ExtendVideo(), RegenSegment(), VideoEdit(), FromAnimation()]
