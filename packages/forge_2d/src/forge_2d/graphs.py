"""Construção dos grafos (formato API) do ComfyUI para os pipelines 2D.

Baseados nos templates oficiais do Comfy 0.28 (image_z_image_turbo, image_qwen_image_edit_2511,
utility_seedvr2_3b_int8_upscale_image, utility_birefnet_remove_background) e, para o Qwen-Image 2.1, nos templates
image_qwen_image_2_1_t2i / image_qwen_image_2_1_turbo_t2i (ComfyUI >= 0.38). Usa só nós nativos.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Arquivos de modelo padrão; sobrescreva em project.yaml -> 2d.models.<chave>
DEFAULT_MODELS: dict[str, str] = {
    "zimage_unet": "z_image_turbo_bf16.safetensors",
    "zimage_clip": "qwen_3_4b.safetensors",
    "zimage_vae": "ae.safetensors",
    "qwen_edit_unet": "qwen_image_edit_2511_fp8mixed.safetensors",
    "qwen_edit_lora": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
    "qwen_clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "qwen_vae": "qwen_image_vae.safetensors",
    "bg_removal": "birefnet.safetensors",
    "esrgan": "4x-UltraSharp.safetensors",
    "seedvr2_unet": "seedvr2_3b_fp8_e4m3fn.safetensors",
    "seedvr2_vae": "seedvr2_ema_vae_fp16.safetensors",
    "wan_high": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    "wan_low": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "wan_lora_high": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "wan_lora_low": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
    "wan_clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "wan_vae": "wan_2.1_vae.safetensors",
    # Qwen-Image 2.1 (Alibaba, set/out 2026): Turbo int8 (8 passos, ~7 GB) cabe com folga em 16 GB de VRAM
    "qi21_unet": "qwen_image_2.1_turbo_int8_convrot.safetensors",
    "qi21_full_unet": "qwen_image_2.1_int8_convrot.safetensors",
    "qi21_clip": "qwen3vl_8b_int8_convrot.safetensors",
    "qi21_vae": "qwen_image_2.1_vae_bf16.safetensors",
}

# nós nativos que o Qwen-Image 2.1 exige (servem para achar uma instância do ComfyUI que o rode)
QI21_NODES = ("TextEncodeQwenImage21", "QwenImage21Cache")
# agenda de 8 passos gravada no checkpoint Turbo (template oficial image_qwen_image_2_1_turbo_t2i)
QI21_TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"


@dataclass(frozen=True)
class N:
    """Referência a um nó; N[i] é a saída i."""

    id: str

    def __getitem__(self, slot: int) -> list:
        return [self.id, slot]

    @property
    def out(self) -> list:
        return [self.id, 0]


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}

    def __call__(self, class_type: str, **inputs: Any) -> N:
        nid = str(len(self.nodes) + 1)
        self.nodes[nid] = {
            "class_type": class_type,
            "inputs": {k: (v.out if isinstance(v, N) else v) for k, v in inputs.items()},
        }
        return N(nid)

    def to_dict(self) -> dict[str, Any]:
        return self.nodes


def _bg_removal(g: Graph, image: N, models: dict[str, str]) -> N:
    """Devolve MASK de primeiro plano (1 = objeto)."""
    bg = g("LoadBackgroundRemovalModel", bg_removal_name=models["bg_removal"])
    return g("RemoveBackground", bg_removal_model=bg, image=image)


def _save(g: Graph, images: N, prefix: str) -> N:
    return g("SaveImage", images=images, filename_prefix=prefix)


def text2img_zimage(prompt: str, width: int, height: int, seed: int, steps: int = 8, *,
                    models: dict[str, str], remove_bg: bool = False, loras: list[dict] | None = None,
                    shift: float = 3.0) -> tuple[Graph, str, str | None]:
    """Z-Image Turbo (cfg 1, negativo zerado). Retorna (grafo, nó da imagem, nó da máscara|None)."""
    g = Graph()
    model: N = g("UNETLoader", unet_name=models["zimage_unet"], weight_dtype="default")
    for lr in loras or []:
        model = g("LoraLoaderModelOnly", model=model, lora_name=lr["name"], strength_model=float(lr.get("strength", 1.0)))
    clip = g("CLIPLoader", clip_name=models["zimage_clip"], type="lumina2", device="default")
    vae = g("VAELoader", vae_name=models["zimage_vae"])
    model = g("ModelSamplingAuraFlow", model=model, shift=shift)
    pos = g("CLIPTextEncode", clip=clip, text=prompt)
    neg = g("ConditioningZeroOut", conditioning=pos)
    latent = g("EmptySD3LatentImage", width=width, height=height, batch_size=1)
    samples = g("KSampler", model=model, seed=seed, steps=steps, cfg=1.0, sampler_name="res_multistep",
                scheduler="simple", positive=pos, negative=neg, latent_image=latent, denoise=1.0)
    image = g("VAEDecode", samples=samples, vae=vae)
    img_node = _save(g, image, "forge/2d/t2i")
    mask_node = None
    if remove_bg:
        mask = _bg_removal(g, image, models)
        mask_node = _save(g, g("MaskToImage", mask=mask), "forge/2d/mask")
    return g, img_node.id, mask_node.id if mask_node else None


def text2img_qwen21(prompt: str, width: int, height: int, seed: int, *, models: dict[str, str], turbo: bool = True,
                    steps: int = 25, negative: str = "", remove_bg: bool = False,
                    loras: list[dict] | None = None) -> tuple[Graph, str, str | None]:
    """Qwen-Image 2.1 (texto → imagem, cfg 1). Turbo: 8 passos com a agenda do checkpoint; completo: `steps` passos.
    Retorna (grafo, nó da imagem, nó da máscara|None)."""
    g = Graph()
    model: N = g("UNETLoader", unet_name=models["qi21_unet" if turbo else "qi21_full_unet"], weight_dtype="default")
    for lr in loras or []:
        model = g("LoraLoaderModelOnly", model=model, lora_name=lr["name"], strength_model=float(lr.get("strength", 1.0)))
    model = g("QwenImage21Cache", model=model, device="auto", dtype="default")
    clip = g("CLIPLoader", clip_name=models["qi21_clip"], type="qwen_image", device="default")
    vae = g("VAELoader", vae_name=models["qi21_vae"])
    cond = g("TextEncodeQwenImage21", clip=clip, prompt=prompt, negative_prompt=negative, resolution=1024)
    latent = g("EmptyLatentImage", width=width, height=height, batch_size=1)
    if turbo:
        guider = g("CFGGuider", model=model, positive=cond[0], negative=cond[1], cfg=1.0)
        samples = g("SamplerCustomAdvanced", noise=g("RandomNoise", noise_seed=seed), guider=guider,
                    sampler=g("KSamplerSelect", sampler_name="euler"), sigmas=g("ManualSigmas", sigmas=QI21_TURBO_SIGMAS),
                    latent_image=latent)
    else:
        samples = g("KSampler", model=model, seed=seed, steps=steps, cfg=1.0, sampler_name="euler", scheduler="simple",
                    positive=cond[0], negative=cond[1], latent_image=latent, denoise=1.0)
    image = g("VAEDecode", samples=samples, vae=vae)
    img_node = _save(g, image, "forge/2d/qi21")
    mask_node = None
    if remove_bg:
        mask = _bg_removal(g, image, models)
        mask_node = _save(g, g("MaskToImage", mask=mask), "forge/2d/mask")
    return g, img_node.id, mask_node.id if mask_node else None


def qwen_edit(prompt: str, images: list[str], seed: int, steps: int = 4, *, models: dict[str, str],
              lightning: bool = True, cfg: float | None = None, megapixels: float = 1.0,
              remove_bg: bool = False, loras: list[dict] | None = None) -> tuple[Graph, str, str | None]:
    """Qwen-Image-Edit 2511. `images` = nomes já enviados ao Comfy (1 a 3); o 1º é o editado."""
    if not 1 <= len(images) <= 3:
        raise ValueError("Qwen Edit aceita de 1 a 3 imagens")
    g = Graph()
    model: N = g("UNETLoader", unet_name=models["qwen_edit_unet"], weight_dtype="default")
    model = g("ModelSamplingAuraFlow", model=model, shift=3.1)
    model = g("CFGNorm", model=model, strength=1.0)
    if lightning:
        model = g("LoraLoaderModelOnly", model=model, lora_name=models["qwen_edit_lora"], strength_model=1.0)
    for lr in loras or []:
        model = g("LoraLoaderModelOnly", model=model, lora_name=lr["name"], strength_model=float(lr.get("strength", 1.0)))
    clip = g("CLIPLoader", clip_name=models["qwen_clip"], type="qwen_image", device="default")
    vae = g("VAELoader", vae_name=models["qwen_vae"])
    loaded = [g("LoadImage", image=name) for name in images]
    main = g("ImageScaleToTotalPixels", image=loaded[0], upscale_method="lanczos", megapixels=megapixels, resolution_steps=16)
    img_inputs: dict[str, Any] = {"image1": main}
    for i, ld in enumerate(loaded[1:], start=2):
        img_inputs[f"image{i}"] = ld
    pos = g("TextEncodeQwenImageEditPlus", clip=clip, prompt=prompt, vae=vae, **img_inputs)
    neg = g("TextEncodeQwenImageEditPlus", clip=clip, prompt="", vae=vae, **img_inputs)
    pos = g("FluxKontextMultiReferenceLatentMethod", conditioning=pos, reference_latents_method="index_timestep_zero")
    neg = g("FluxKontextMultiReferenceLatentMethod", conditioning=neg, reference_latents_method="index_timestep_zero")
    latent = g("VAEEncode", pixels=main, vae=vae)
    samples = g("KSampler", model=model, seed=seed, steps=steps, cfg=cfg if cfg is not None else (1.0 if lightning else 4.0),
                sampler_name="euler", scheduler="simple", positive=pos, negative=neg, latent_image=latent, denoise=1.0)
    image = g("VAEDecode", samples=samples, vae=vae)
    img_node = _save(g, image, "forge/2d/edit")
    mask_node = None
    if remove_bg:
        mask = _bg_removal(g, image, models)
        mask_node = _save(g, g("MaskToImage", mask=mask), "forge/2d/mask")
    return g, img_node.id, mask_node.id if mask_node else None


def remove_background(image_name: str, *, models: dict[str, str]) -> tuple[Graph, str]:
    g = Graph()
    img = g("LoadImage", image=image_name)
    mask = _bg_removal(g, img, models)
    return g, _save(g, g("MaskToImage", mask=mask), "forge/2d/mask").id


def upscale_esrgan(image_name: str, scale: float, *, models: dict[str, str], model_scale: int = 4) -> tuple[Graph, str]:
    g = Graph()
    img = g("LoadImage", image=image_name)
    up = g("UpscaleModelLoader", model_name=models["esrgan"])
    out = g("ImageUpscaleWithModel", upscale_model=up, image=img)
    if abs(scale - model_scale) > 1e-3:
        out = g("ImageScaleBy", image=out, upscale_method="lanczos", scale_by=scale / model_scale)
    return g, _save(g, out, "forge/2d/up").id


def upscale_seedvr2(image_name: str, scale: float, seed: int, *, models: dict[str, str],
                    color_correction: str = "lab", tile: int = 512) -> tuple[Graph, str]:
    g = Graph()
    img = g("LoadImage", image=image_name)
    resized = g("ImageScaleBy", image=img, upscale_method="lanczos", scale_by=scale)
    model = g("UNETLoader", unet_name=models["seedvr2_unet"], weight_dtype="default")
    vae = g("VAELoader", vae_name=models["seedvr2_vae"])
    pre = g("SeedVR2Preprocess", resized_images=resized)
    latent = g("VAEEncodeTiled", pixels=pre, vae=vae, tile_size=tile, overlap=128, temporal_size=4096, temporal_overlap=8)
    cond = g("SeedVR2Conditioning", model=model, vae_conditioning=latent)
    samples = g("KSampler", model=model, seed=seed, steps=1, cfg=1.0, sampler_name="euler", scheduler="simple",
                positive=cond[0], negative=cond[1], latent_image=latent, denoise=1.0)
    dec = g("VAEDecodeTiled", samples=samples, vae=vae, tile_size=tile, overlap=128, temporal_size=4096, temporal_overlap=8)
    post = g("SeedVR2PostProcessing", images=dec, original_resized_images=resized, color_correction_method=color_correction)
    return g, _save(g, post, "forge/2d/up").id


WAN_NEGATIVE = (
    "camera movement, zoom, pan, camera shake, background change, scene change, text, watermark, subtitles, "
    "blurry, low quality, deformed, extra limbs, extra fingers, morphing, flickering, static image, "
    "色调艳丽，过曝，静态，细节模糊不清，字幕，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，畸形的，形态畸形的肢体"
)


def wan_i2v(prompt: str, start_image: str, width: int, height: int, length: int, seed: int, *,
            models: dict[str, str], end_image: str | None = None, negative: str = WAN_NEGATIVE,
            lightning: bool = True, steps: int | None = None, remove_bg: bool = True) -> tuple[Graph, str, str | None]:
    """Wan 2.2 14B image→video (2 estágios high/low noise). Com `end_image` usa first-last-frame
    (loop perfeito quando start == end). `length` = 4k+1 frames a 16 fps. Retorna (grafo, nó dos
    frames, nó das máscaras|None) — salva o lote de frames como PNGs, sem montar vídeo."""
    g = Graph()
    hi: N = g("UNETLoader", unet_name=models["wan_high"], weight_dtype="default")
    lo: N = g("UNETLoader", unet_name=models["wan_low"], weight_dtype="default")
    if lightning:
        hi = g("LoraLoaderModelOnly", model=hi, lora_name=models["wan_lora_high"], strength_model=1.0)
        lo = g("LoraLoaderModelOnly", model=lo, lora_name=models["wan_lora_low"], strength_model=1.0)
    hi = g("ModelSamplingSD3", model=hi, shift=5.0 if lightning else 8.0)
    lo = g("ModelSamplingSD3", model=lo, shift=5.0 if lightning else 8.0)
    clip = g("CLIPLoader", clip_name=models["wan_clip"], type="wan", device="default")
    vae = g("VAELoader", vae_name=models["wan_vae"])
    pos = g("CLIPTextEncode", clip=clip, text=prompt)
    neg = g("CLIPTextEncode", clip=clip, text=negative)
    start = g("LoadImage", image=start_image)
    if end_image:
        end = g("LoadImage", image=end_image)
        cond = g("WanFirstLastFrameToVideo", positive=pos, negative=neg, vae=vae, width=width, height=height,
                 length=length, batch_size=1, start_image=start, end_image=end)
    else:
        cond = g("WanImageToVideo", positive=pos, negative=neg, vae=vae, width=width, height=height,
                 length=length, batch_size=1, start_image=start)
    total = steps or (4 if lightning else 20)
    mid = total // 2
    cfg = 1.0 if lightning else 3.5
    lat = g("KSamplerAdvanced", model=hi, add_noise="enable", noise_seed=seed, steps=total, cfg=cfg,
            sampler_name="euler", scheduler="simple", positive=cond[0], negative=cond[1], latent_image=cond[2],
            start_at_step=0, end_at_step=mid, return_with_leftover_noise="enable")
    lat = g("KSamplerAdvanced", model=lo, add_noise="disable", noise_seed=0, steps=total, cfg=cfg,
            sampler_name="euler", scheduler="simple", positive=cond[0], negative=cond[1], latent_image=lat,
            start_at_step=mid, end_at_step=10000, return_with_leftover_noise="disable")
    frames = g("VAEDecode", samples=lat, vae=vae)
    frames_node = _save(g, frames, "forge/2d/anim")
    mask_node = None
    if remove_bg:
        mask = _bg_removal(g, frames, models)
        mask_node = _save(g, g("MaskToImage", mask=mask), "forge/2d/anim_mask")
    return g, frames_node.id, mask_node.id if mask_node else None


def text2img_checkpoint(prompt: str, negative: str, width: int, height: int, seed: int, *, checkpoint: str,
                        steps: int = 28, cfg: float = 6.0, sampler: str = "euler_ancestral", scheduler: str = "normal",
                        clip_skip: int = 0, loras: list[dict] | None = None, models: dict[str, str],
                        remove_bg: bool = False, batch: int = 1, init_image: str | None = None,
                        denoise: float = 1.0) -> tuple[Graph, str, str | None]:
    """Checkpoint "tudo em um" (SD 1.5 / SDXL / Pony / Illustrious...) com LoRAs. clip_skip 2 = Pony/anime.
    Com `init_image` (nome já enviado) vira img2img com `denoise`."""
    g = Graph()
    ck = g("CheckpointLoaderSimple", ckpt_name=checkpoint)
    model: N = ck[0]
    clip: N = ck[1]
    for lr in loras or []:
        s = float(lr.get("strength", 1.0))
        lo = g("LoraLoader", model=model, clip=clip, lora_name=lr["name"], strength_model=s,
               strength_clip=float(lr.get("strength_clip", s)))
        model, clip = lo[0], lo[1]
    if clip_skip:
        clip = g("CLIPSetLastLayer", clip=clip, stop_at_clip_layer=-abs(int(clip_skip)))
    pos = g("CLIPTextEncode", clip=clip, text=prompt)
    neg = g("CLIPTextEncode", clip=clip, text=negative)
    if init_image:
        latent = g("VAEEncode", pixels=g("LoadImage", image=init_image), vae=ck[2])
    else:
        latent = g("EmptyLatentImage", width=width, height=height, batch_size=batch)
        denoise = 1.0
    samples = g("KSampler", model=model, seed=seed, steps=steps, cfg=cfg, sampler_name=sampler, scheduler=scheduler,
                positive=pos, negative=neg, latent_image=latent, denoise=denoise)
    image = g("VAEDecode", samples=samples, vae=ck[2])
    img_node = _save(g, image, "forge/2d/ckpt")
    mask_node = None
    if remove_bg:
        mask_node = _save(g, g("MaskToImage", mask=_bg_removal(g, image, models)), "forge/2d/mask")
    return g, img_node.id, mask_node.id if mask_node else None
