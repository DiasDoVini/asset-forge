"""Pipelines de animação de sprites.

2d.animate      sprite base + "andando de perfil" + N frames  ->  asset kind=animation
2d.anim_frame   regenera/edita um frame de uma animação (nova versão)
2d.anim_edit    operações sem IA: apagar, duplicar, mover, duração, fps, inverter, ping-pong, espelhar, substituir
"""
from __future__ import annotations

import io
import math
import random
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import Field

from forge_core import AssetVersion, ComfyError, OutputFile, Pipeline, PipelineParams, slugify
from forge_core.pipeline import Context
from forge_core.runner import resolve_input

from . import graphs, imaging
from .animation import (ANIM_FILE, PRESETS, Anchor, Animation, align_frames, find_cycle, fit_canvas, phases_for,
                        sample_indices)
from .pixel import extract_palette, pixelize
from .pipelines import _ensure_comfy, _models, _open, _seed, _upload

PresetName = Literal[tuple(PRESETS)]  # type: ignore[valid-type]

# ---------------------------------------------------------------- helpers


def _run_batch(ctx: Context, graph: graphs.Graph, want: list[str | None], span: tuple[float, float], label: str
               ) -> dict[str, list[Image.Image]]:
    """Como pipelines._run, mas devolve todas as imagens de cada nó (lotes de frames), em ordem."""
    ctx.check_cancelled()
    a, b = span
    ctx.progress(a, label)
    outs = ctx.comfy.run_workflow(graph.to_dict(), on_progress=lambda v, m: ctx.progress(a + (b - a) * v, label))
    result: dict[str, list[Image.Image]] = {}
    for o in sorted(outs, key=lambda o: o.filename):
        if o.node_id in want:
            result.setdefault(o.node_id, []).append(_open(io.BytesIO(ctx.comfy.download(o.filename, o.subfolder, o.type))))
    missing = [w for w in want if w and w not in result]
    if missing:
        raise ComfyError(f"workflow não produziu saída nos nós {missing}")
    return result


def _comfy_has(ctx: Context, folder: str, name: str) -> bool:
    try:
        return name in ctx.comfy.models(folder)
    except Exception:
        return False


WAN_FILES = (("diffusion_models", "wan_high"), ("diffusion_models", "wan_low"), ("loras", "wan_lora_high"),
             ("loras", "wan_lora_low"), ("text_encoders", "wan_clip"), ("vae", "wan_vae"))


def _local_model_dirs() -> list[Path]:
    """Pastas de modelos locais conhecidas (Comfy Desktop + FORGE_COMFY_MODELS_DIR)."""
    import os

    dirs = []
    if os.environ.get("FORGE_COMFY_MODELS_DIR"):
        dirs.append(Path(os.environ["FORGE_COMFY_MODELS_DIR"]))
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Comfy-Desktop" / "ComfyUI-Shared" / "models")
    return [d for d in dirs if d.is_dir()]


def video_status(ctx: Context, models: dict[str, str]) -> tuple[bool, str]:
    """(pronto, motivo). Exige todos os arquivos do Wan no Comfy e nenhum download em andamento
    (marcadores <arquivo>.dl / .part / .partial nas pastas locais)."""
    missing = [models[key] for folder, key in WAN_FILES if not _comfy_has(ctx, folder, models[key])]
    if missing:
        return False, f"modelos Wan ausentes no ComfyUI: {missing}"
    for d in _local_model_dirs():
        for folder, key in WAN_FILES:
            f = d / folder / models[key]
            for suffix in (".dl", ".part", ".partial", ".download"):
                if Path(str(f) + suffix).exists():
                    return False, f"{models[key]} ainda está baixando"
    return True, ""


def _friendly(e: Exception) -> str:
    msg = str(e)
    if "UNETLoader" in msg and ("shape" in msg or "invalid" in msg or "header" in msg.lower()):
        return f"arquivo de modelo incompleto ou corrompido (download em andamento?): {msg}"
    return msg


class BaseSprite:
    """Sprite base em alta resolução para gerar + info de pixel art para voltar ao estilo."""

    def __init__(self, ctx: Context, av: AssetVersion | None, path: Path | None):
        self.av = av
        store = ctx.project.store
        self.is_sprite = bool(av and av.kind == "sprite")
        if av is not None:
            src = (store.files_of(av, "source") if self.is_sprite else []) or store.files_of(av, "primary")
            img = _open(src[0])
        else:
            img = _open(path)
        self.hi = imaging.trim(img.convert("RGBA"), 0) if imaging.has_alpha(img) else img.convert("RGBA")
        self.sprite = _open(store.files_of(av, "primary")[0]).convert("RGBA") if self.is_sprite else None
        self.pixel = dict(av.extra.get("pixel") or {}) if self.is_sprite and av else {}

    @property
    def ref(self) -> str | None:
        return self.av.ref if self.av else None

    def palette(self) -> np.ndarray | None:
        return extract_palette([self.sprite], 256) if self.sprite is not None else None

    def art_height(self) -> int | None:
        if self.sprite is None:
            return None
        bbox = self.sprite.split()[3].getbbox()
        return (bbox[3] - bbox[1]) if bbox else self.sprite.height


def _content_height(img: Image.Image) -> int:
    bbox = img.split()[3].point(lambda v: 255 if v > 16 else 0).getbbox()
    return (bbox[3] - bbox[1]) if bbox else img.height


def _pixelize_frames(frames: list[Image.Image], base: BaseSprite, style: dict[str, Any], art_height: int | None,
                     colors: int | None) -> tuple[list[Image.Image], dict[str, Any]]:
    """Pixeliza todos os frames com a mesma grade e paleta (consistência entre frames)."""
    h = max(_content_height(f) for f in frames)
    target = art_height or int((style.get("pixel") or {}).get("size", 64))
    cell = max(1.0, h / max(1, target))
    palette = base.palette()
    ncolors = colors or int(base.pixel.get("colors") or (style.get("pixel") or {}).get("colors", 16))
    if palette is None and style.get("palette"):
        from .pixel import hex_to_rgb
        palette = hex_to_rgb(style["palette"])
    if palette is None:  # paleta comum extraída de todos os frames
        palette = extract_palette(frames, ncolors)
    outline = base.pixel.get("outline") or (style.get("pixel") or {}).get("outline")
    out = [pixelize(f, mode="downscale", size=None, cell=cell, palette=palette, colors=ncolors, remove_bg=False,
                    crop=False, outline_color=outline, min_island=2).sprite for f in frames]
    return out, {"cell": cell, "art_height": target, "colors": ncolors, "outline": outline}


def _compose_canvas(img: Image.Image, size: tuple[int, int], height_frac: float, bottom_frac: float = 0.1) -> Image.Image:
    """Sprite sobre fundo branco num canvas WxH, centralizado, com folga para o movimento."""
    W, H = size
    k = min(height_frac * H / img.height, 0.8 * W / img.width)
    s = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (W, H), "white")
    y = round(H * (1 - bottom_frac)) - s.height if bottom_frac is not None else (H - s.height) // 2
    canvas.paste(imaging.flatten(s), ((W - s.width) // 2, max(0, y)), s.split()[3] if s.mode == "RGBA" else None)
    return canvas


def _anim_prompt(action: str, preset_motion: str, style: dict[str, Any], in_place: bool) -> str:
    parts = [p.strip().rstrip(".") for p in (action, preset_motion) if p and p.strip()]
    if in_place:
        parts.append("The character stays in place at the center of the frame (in-place animation, like on a treadmill)")
    parts.append("Static camera, the camera does not move or zoom. Plain solid white background")
    parts.append("2D game sprite animation, keep exactly the same character design, outfit, colors, proportions and art style")
    if style.get("prompt"):
        parts.append(style["prompt"].strip().rstrip("."))
    return ". ".join(parts) + "."


# ---------------------------------------------------------------- 2d.animate

class AnimateParams(PipelineParams):
    prompt: str = Field("", description='ação da animação, ex.: "andando para a direita, de perfil"')
    preset: PresetName | None = Field(None, description="modelo de movimento: " + ", ".join(f"{k} ({v.title})" for k, v in PRESETS.items()))
    frames: int | None = Field(None, ge=2, le=32, description="nº de sprites da animação (padrão do preset ou 8)")
    method: Literal["auto", "video", "edit"] = Field(
        "auto", description="video: Wan 2.2 gera o movimento (melhor); edit: Qwen gera pose a pose; auto: video se instalado")
    fps: int | None = Field(None, ge=1, le=60)
    loop: bool | None = Field(None, description="animação cíclica (vazio = padrão do preset)")
    anchor: Literal["auto", "bottom", "center", "union", "none"] = Field(
        "auto", description="bottom: pés fixos (andar/correr); center: itens; union: mantém deslocamento (pulo)")
    duration: float | None = Field(None, ge=0.5, le=5.0, description="segundos de vídeo gerados (método video)")
    resolution: int = Field(640, ge=320, le=960, description="lado maior do vídeo gerado")
    loop_strategy: Literal["cycle", "flf"] = Field(
        "cycle", description="loops no método vídeo: cycle = vídeo longo + detecção de ciclo (movimento contínuo); "
                              "flf = primeiro e último frame iguais (começa e termina na pose base)")
    pixelize: bool | None = Field(None, description="vazio = pixeliza se o sprite base for pixel art")
    pixel_colors: int | None = Field(None, ge=2, le=256)
    seed: int | None = None


class Animate(Pipeline):
    id = "2d.animate"
    title = "Animar sprite"
    description = ("Cria uma animação (N frames) a partir de um sprite base e de um prompt de ação, "
                   "mantendo o personagem/item. Gera GIF e sprite sheet; exporta grade+JSON, frames ou GIF.")
    resources = {"gpu"}
    kind = "animation"
    requires_input = True
    Params = AnimateParams

    def default_slug(self, p: AnimateParams) -> str:
        base = (p.input_asset or "sprite").split("@")[0]
        base = Path(base).stem if ("/" in base or "\\" in base) else base
        what = p.preset or " ".join(slugify(p.prompt).split("-")[:3]) or "anim"
        return f"{base}-{what}"

    def run(self, ctx: Context, p: AnimateParams) -> list[OutputFile]:
        style = ctx.style()
        preset = PRESETS.get(p.preset) if p.preset else None
        n = p.frames or (preset.frames if preset else 8)
        fps = p.fps or (preset.fps if preset else 10)
        loop = p.loop if p.loop is not None else (preset.loop if preset else True)
        anchor: Anchor = (preset.anchor if preset else "bottom") if p.anchor == "auto" else p.anchor  # type: ignore[assignment]
        base = BaseSprite(ctx, ctx.input, ctx.input_path)
        pixel_on = p.pixelize if p.pixelize is not None else (base.is_sprite or bool(style.get("pixel_art")))
        seed = _seed(p.seed)
        _ensure_comfy(ctx)
        models = _models(style)

        method = p.method
        ready, why = video_status(ctx, models)
        if method == "auto":
            method = "video" if ready else "edit"
            if not ready:
                ctx.log(f"usando pose a pose (vídeo indisponível: {why})")
        elif method == "video" and not ready:
            raise ComfyError(f"método vídeo indisponível: {why}. Use method=edit ou aguarde.")
        ctx.log(f"método: {method}, {n} frames, {fps} fps, loop={loop}, âncora={anchor}")

        if method == "video":
            try:
                raw = self._video(ctx, p, base, preset, n, fps, loop, anchor, seed, models, style)
            except ComfyError as e:
                if p.method != "auto":
                    raise ComfyError(_friendly(e)) from None
                ctx.log(f"vídeo falhou ({_friendly(e)}); tentando pose a pose")
                method = "edit"
                raw = self._edit(ctx, p, base, preset, n, seed, models, style)
        else:
            raw = self._edit(ctx, p, base, preset, n, seed, models, style)

        ctx.progress(0.92, "alinhando frames")
        frames = align_frames(raw, anchor, pad=4)
        pixel_meta = None
        if pixel_on:
            ctx.progress(0.95, "pixelizando")
            frames, pixel_meta = _pixelize_frames(frames, base, style, base.art_height(), p.pixel_colors)
        meta = {"base": base.ref, "action": p.prompt, "preset": p.preset, "method": method, "anchor": anchor,
                "seed": seed, "pixel": pixel_meta}
        anim = Animation(frames, fps=fps, loop=loop, meta=meta)
        ctx.meta.update(kind="animation", seed=seed, prompt=p.prompt or (preset.title if preset else None),
                        extra={**ctx.meta.get("extra", {}), "base": base.ref, "frames": len(frames), "fps": fps, "loop": loop, "method": method,
                               "preset": p.preset, "size": list(anim.size), "pixel": pixel_meta})
        return anim.save(ctx.version_dir)

    # -- métodos de geração ------------------------------------------------
    def _video(self, ctx, p, base: BaseSprite, preset, n, fps, loop, anchor, seed, models, style) -> list[Image.Image]:
        res = imaging.snap(p.resolution, 16)
        portrait = base.hi.height >= base.hi.width * 1.15
        W, H = (imaging.snap(round(res * 0.75), 16), res) if portrait else (res, res)
        frac = 0.55 if anchor == "union" else 0.7
        start = _compose_canvas(base.hi, (W, H), frac, 0.1 if anchor != "center" else None)
        flf = loop and p.loop_strategy == "flf"
        # loops: vídeo longo (vários ciclos) para achar um ciclo limpo depois do arranque
        dur = p.duration or (3.0 if loop and not flf else min(5.0, max(1.5, n / fps * 1.5)))
        length = max(17, int(round(dur * 16 / 4)) * 4 + 1)
        prompt = _anim_prompt(p.prompt, preset.motion if preset else "", style, in_place=anchor in ("bottom", "center"))
        ctx.log(f"vídeo {W}x{H}, {length} frames ({length / 16:.1f}s), {'FLF' if flf else 'i2v'}: {prompt}")
        name = _upload(ctx, start, "start.png")
        g, fid, mid = graphs.wan_i2v(prompt, name, W, H, length, seed, models=models,
                                     end_image=name if flf else None, remove_bg=True)
        out = _run_batch(ctx, g, [fid, mid], (0.05, 0.88), "gerando vídeo")
        rgba = [imaging.apply_mask(v, m) for v, m in zip(out[fid], out[mid])]
        if loop and not flf:
            cyc = find_cycle(rgba)
            if cyc:
                s, period, q = cyc
                idx = [s + round(i * period / n) for i in range(n)]
                ctx.log(f"ciclo detectado: frames {s}..{s + period} (período {period}, qualidade {q:.2f})")
                ctx.meta.setdefault("extra", {})["cycle"] = {"start": s, "period": period, "quality": round(q, 3)}
                return [rgba[i] for i in idx]
            ctx.log("nenhum ciclo claro no vídeo; amostrando o vídeo inteiro")
        idx = sample_indices(len(rgba), n, flf)
        ctx.log(f"{len(rgba)} frames de vídeo; usando {idx}")
        return [rgba[i] for i in idx]

    def _edit(self, ctx, p, base: BaseSprite, preset, n, seed, models, style) -> list[Image.Image]:
        src = imaging.flatten(base.hi)
        name = _upload(ctx, src, "base.png")
        motion = preset.motion if preset else ""
        frames = []
        for i, phase in enumerate(phases_for(preset, p.prompt, n)):
            ctx.check_cancelled()
            instr = (f"Redraw this exact same character (identical design, outfit, colors, proportions, art style and size). "
                     f"Animation: {'. '.join(x for x in (p.prompt, motion) if x)}. Frame {i + 1} of {n}: {phase}. "
                     f"Same framing and scale, plain white background.")
            g, img_id, mask_id = graphs.qwen_edit(instr, [name], seed + i, 4, models=models, remove_bg=True)
            out = _run_batch(ctx, g, [img_id, mask_id], (0.05 + 0.85 * i / n, 0.05 + 0.85 * (i + 1) / n), f"frame {i + 1}/{n}")
            frames.append(imaging.apply_mask(out[img_id][0], out[mask_id][0]))
        return frames


# ---------------------------------------------------------------- 2d.anim_frame

class AnimFrameParams(PipelineParams):
    frame: int = Field(..., ge=0, description="índice do frame (0 = primeiro)")
    instruction: str = Field("", description='o que mudar, ex.: "braço esquerdo mais alto". Vazio = refazer fiel ao personagem base')
    use_base: bool = Field(True, description="usar o sprite base como referência de identidade")
    seed: int | None = None


class AnimFrame(Pipeline):
    id = "2d.anim_frame"
    title = "Refazer frame"
    description = "Regenera ou edita um frame de uma animação com IA (Qwen Edit), mantendo tamanho, alinhamento e paleta."
    resources = {"gpu"}
    kind = "animation"
    requires_input = True
    same_asset = True
    Params = AnimFrameParams

    def run(self, ctx: Context, p: AnimFrameParams) -> list[OutputFile]:
        if ctx.input is None or ctx.input.kind != "animation" or ctx.input.modality != "2d":
            raise ValueError("entrada precisa ser uma animação")
        anim = Animation.load(ctx.project.store.version_dir(ctx.input.slug, ctx.input.version))
        if not 0 <= p.frame < len(anim.frames):
            raise ValueError(f"frame {p.frame} fora do intervalo (0..{len(anim.frames) - 1})")
        style = ctx.style()
        pixel = anim.meta.get("pixel")
        cur = anim.frames[p.frame]
        k = max(1, round(pixel["cell"])) if pixel else 1
        hi = cur.resize((cur.width * k, cur.height * k), Image.Resampling.NEAREST) if k > 1 else cur
        images = [imaging.flatten(hi)]
        base = None
        if p.use_base and anim.meta.get("base"):
            try:
                av, path = resolve_input(ctx.project, anim.meta["base"])
                base = BaseSprite(ctx, av, path)
                images.append(imaging.flatten(base.hi))
            except Exception:
                base = None
        instr = (p.instruction.strip().rstrip(".") + ". " if p.instruction.strip() else
                 "Redraw this animation frame cleanly, fixing any artifacts. ")
        instr += ("Keep the same pose and framing as image 1" +
                  (" and make the character exactly match the design, colors and proportions of image 2" if base else "") +
                  ". Plain white background.")
        seed = _seed(p.seed)
        _ensure_comfy(ctx)
        names = [_upload(ctx, im, f"in{i}.png") for i, im in enumerate(images)]
        g, img_id, mask_id = graphs.qwen_edit(instr, names, seed, 4, models=_models(style), remove_bg=True)
        out = _run_batch(ctx, g, [img_id, mask_id], (0.05, 0.85), "refazendo frame")
        new = imaging.apply_mask(out[img_id][0], out[mask_id][0])
        new = new.resize(hi.size, Image.Resampling.LANCZOS)
        new = _match_placement(new, hi)
        if pixel:
            pal = extract_palette([f for f in anim.frames], 256)
            new = pixelize(new, mode="downscale", size=None, cell=k, palette=pal, colors=pixel.get("colors", 16),
                           remove_bg=False, crop=False, outline_color=pixel.get("outline"), min_island=2).sprite
        new = fit_canvas([new], anim.size, "bottom")[0]
        anim.frames[p.frame] = new
        ctx.meta.update(kind="animation", seed=seed, prompt=p.instruction or None,
                        extra={**(ctx.input.extra or {}), "edited_frame": p.frame})
        return anim.save(ctx.version_dir)


def _match_placement(new: Image.Image, ref: Image.Image) -> Image.Image:
    """Desloca `new` para que base e centro de massa coincidam com `ref` (mesmo canvas)."""
    from .animation import _alpha, _bbox, _centroid_x

    a_new, a_ref = _alpha(new), _alpha(ref)
    b_new, b_ref = _bbox(a_new), _bbox(a_ref)
    if not b_new or not b_ref:
        return new
    dx = round(_centroid_x(a_ref) - _centroid_x(a_new))
    dy = b_ref[3] - b_new[3]
    out = Image.new("RGBA", new.size)
    out.paste(new, (dx, dy))
    return out


# ---------------------------------------------------------------- 2d.anim_edit

class AnimEditParams(PipelineParams):
    ops: list[dict[str, Any]] = Field(..., description=(
        'lista de operações aplicadas em ordem: {"op":"delete","frames":[2,3]}, {"op":"duplicate","frame":0}, '
        '{"op":"move","frame":4,"to":0}, {"op":"duration","frames":[1],"ms":200} (frames omitido = todos), '
        '{"op":"fps","value":12}, {"op":"loop","value":false}, {"op":"reverse"}, {"op":"pingpong"}, {"op":"flip_h"}, '
        '{"op":"replace","frame":2,"image":"<arquivo ou slug@v>"}, {"op":"insert","at":1,"image":"..."}'))


class AnimEdit(Pipeline):
    id = "2d.anim_edit"
    title = "Editar animação"
    description = "Edita frames sem IA (apagar, duplicar, reordenar, duração, fps, inverter, ping-pong, espelhar, substituir). Gera nova versão."
    resources = {"cpu"}
    kind = "animation"
    requires_input = True
    same_asset = True
    Params = AnimEditParams

    def run(self, ctx: Context, p: AnimEditParams) -> list[OutputFile]:
        if ctx.input is None or ctx.input.kind != "animation" or ctx.input.modality != "2d":
            raise ValueError("entrada precisa ser uma animação")
        anim = Animation.load(ctx.project.store.version_dir(ctx.input.slug, ctx.input.version))
        frames, durs = list(anim.frames), list(anim.durations or [])

        def load_img(ref: str) -> Image.Image:
            av, path = resolve_input(ctx.project, ref)
            if av is not None:
                path = ctx.project.store.files_of(av, "primary")[0]
            return fit_canvas([_open(path).convert("RGBA")], anim.size, anim.meta.get("anchor", "bottom"))[0]

        for op in p.ops:
            kind = op.get("op")
            if kind == "delete":
                drop = set(op["frames"])
                keep = [i for i in range(len(frames)) if i not in drop]
                if not keep:
                    raise ValueError("a animação precisa de pelo menos 1 frame")
                frames, durs = [frames[i] for i in keep], [durs[i] for i in keep]
            elif kind == "duplicate":
                i = op["frame"]
                frames.insert(i + 1, frames[i].copy())
                durs.insert(i + 1, durs[i])
            elif kind == "move":
                i, j = op["frame"], op["to"]
                f, d = frames.pop(i), durs.pop(i)
                frames.insert(j, f)
                durs.insert(j, d)
            elif kind == "duration":
                idx = op.get("frames") or range(len(frames))
                for i in idx:
                    durs[i] = int(op["ms"])
            elif kind == "fps":
                anim.fps = int(op["value"])
                durs = [round(1000 / anim.fps)] * len(frames)
            elif kind == "loop":
                anim.loop = bool(op["value"])
            elif kind == "reverse":
                frames.reverse()
                durs.reverse()
            elif kind == "pingpong":
                frames += frames[-2:0:-1]
                durs += durs[-2:0:-1]
            elif kind == "flip_h":
                frames = [f.transpose(Image.Transpose.FLIP_LEFT_RIGHT) for f in frames]
            elif kind == "replace":
                frames[op["frame"]] = load_img(op["image"])
            elif kind == "insert":
                at = int(op.get("at", len(frames)))
                frames.insert(at, load_img(op["image"]))
                durs.insert(at, durs[min(at, len(durs) - 1)] if durs else round(1000 / anim.fps))
            else:
                raise ValueError(f"operação desconhecida: {kind}")
        out = Animation(frames, fps=anim.fps, loop=anim.loop, durations=durs, meta=anim.meta)
        ctx.meta.update(kind="animation", extra={**(ctx.input.extra or {}), "frames": len(frames), "fps": anim.fps,
                                                 "loop": anim.loop, "ops": [o.get("op") for o in p.ops]})
        return out.save(ctx.version_dir)


# ---------------------------------------------------------------- 2d.view (direções)

VIEWS: dict[str, str] = {
    "front": "seen from the front, facing the viewer",
    "back": "seen from behind, back view, facing away from the viewer",
    "right": "side view, profile facing right",
    "left": "side view, profile facing left",
    "front_right": "three-quarter view facing front-right",
    "front_left": "three-quarter view facing front-left",
    "back_right": "three-quarter back view, facing away to the right",
    "back_left": "three-quarter back view, facing away to the left",
}


class ViewParams(PipelineParams):
    view: Literal[tuple(VIEWS)] = Field(..., description="direção: " + ", ".join(VIEWS))  # type: ignore[valid-type]
    prompt: str = Field("", description="ajustes extras")
    mirror_from: str | None = Field(None, description="em vez de gerar, espelha outra vista (ex.: left a partir de right)")
    seed: int | None = None


class View(Pipeline):
    id = "2d.view"
    title = "Vista/direção do sprite"
    description = ("Gera o mesmo personagem/item visto de outra direção (frente, costas, perfil, 3/4), "
                   "como asset ligado ao sprite base — para animações em 4/8 direções.")
    resources = {"gpu"}
    requires_input = True
    Params = ViewParams

    def default_slug(self, p: ViewParams) -> str:
        base = (p.input_asset or "sprite").split("@")[0]
        base = Path(base).stem if ("/" in base or "\\" in base) else base
        return f"{base}-{p.view.replace('_', '-')}"

    def run(self, ctx: Context, p: ViewParams) -> list[OutputFile]:
        style = ctx.style()
        base = BaseSprite(ctx, ctx.input, ctx.input_path)
        seed = _seed(p.seed)
        if p.mirror_from:
            av, path = resolve_input(ctx.project, p.mirror_from)
            src = ctx.project.store.files_of(av, "primary")[0] if av else path
            out = _open(src).convert("RGBA").transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            ctx.meta.update(kind=av.kind if av else "image", extra={"base": base.ref, "view": p.view, "mirrored": p.mirror_from})
            dest = ctx.version_dir / ("sprite.png" if av and av.kind == "sprite" else "image.png")
            out.save(dest)
            return [OutputFile(dest, "primary")]
        instr = (f"Redraw this exact same character (identical design, outfit, colors, proportions and art style), "
                 f"full body, {VIEWS[p.view]}. Same scale, neutral standing pose, plain white background.")
        if p.prompt:
            instr += f" {p.prompt.strip()}"
        _ensure_comfy(ctx)
        name = _upload(ctx, imaging.flatten(base.hi), "base.png")
        g, img_id, mask_id = graphs.qwen_edit(instr, [name], seed, 4, models=_models(style), remove_bg=True)
        out = _run_batch(ctx, g, [img_id, mask_id], (0.05, 0.85), f"vista {p.view}")
        img = imaging.trim(imaging.apply_mask(out[img_id][0], out[mask_id][0]), 8)
        ctx.meta.update(seed=seed, prompt=p.prompt or None, extra={"base": base.ref, "view": p.view, "instruction": instr})
        d = ctx.version_dir
        if base.is_sprite:
            frames, pixel = _pixelize_frames([img], base, style, base.art_height(), None)
            sprite = imaging.trim(frames[0], 0)
            sprite.save(d / "sprite.png")
            img.save(d / "source.png")
            imaging.preview(sprite, 512).save(d / "preview.png")
            ctx.meta["kind"] = "sprite"
            ctx.meta["extra"]["pixel"] = {**pixel, "art_size": list(sprite.size)}
            return [OutputFile(d / "sprite.png", "primary"), OutputFile(d / "preview.png", "preview"),
                    OutputFile(d / "source.png", "source")]
        img.save(d / "image.png")
        ctx.meta["kind"] = "image"
        return [OutputFile(d / "image.png", "primary")]


ANIM_PIPELINES: list[Pipeline] = [Animate(), AnimFrame(), AnimEdit(), View()]
