"""Grafos de vídeo do ComfyUI. Wan 2.2 TI2V 5B (text/image → vídeo 720p, 24 fps; template video_wan2_2_5B_ti2v)
e Wan 2.2 14B i2v/first-last-frame (16 fps, Lightning 4 passos) reaproveitado de forge_2d.graphs.
Os quadros saem como PNGs (SaveImage) e o vídeo é montado com ffmpeg.
"""
from __future__ import annotations

from forge_2d.graphs import DEFAULT_MODELS as MODELS_2D
from forge_2d.graphs import Graph, N, wan_i2v

DEFAULT_MODELS: dict[str, str] = {
    **MODELS_2D,
    "ti2v_unet": "wan2.2_ti2v_5B_fp16.safetensors",
    "ti2v_vae": "wan2.2_vae.safetensors",
}

# negativo padrão do template Wan (chinês, como o modelo foi treinado)
WAN_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，"
                "丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
                "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")

TI2V_FPS = 24
I2V_FPS = 16


def ti2v(prompt: str, width: int, height: int, length: int, seed: int, *, models: dict[str, str],
         start_image: str | None = None, negative: str = WAN_NEGATIVE, steps: int = 20, cfg: float = 5.0,
         shift: float = 8.0) -> tuple[Graph, str]:
    """Wan 2.2 TI2V 5B. length = 4k+1 quadros a 24 fps. Com start_image vira imagem → vídeo."""
    g = Graph()
    model: N = g("UNETLoader", unet_name=models["ti2v_unet"], weight_dtype="default")
    model = g("ModelSamplingSD3", model=model, shift=shift)
    clip = g("CLIPLoader", clip_name=models["wan_clip"], type="wan", device="default")
    vae = g("VAELoader", vae_name=models["ti2v_vae"])
    pos = g("CLIPTextEncode", clip=clip, text=prompt)
    neg = g("CLIPTextEncode", clip=clip, text=negative)
    kw = {"start_image": g("LoadImage", image=start_image)} if start_image else {}
    latent = g("Wan22ImageToVideoLatent", vae=vae, width=width, height=height, length=length, batch_size=1, **kw)
    samples = g("KSampler", model=model, seed=seed, steps=steps, cfg=cfg, sampler_name="uni_pc", scheduler="simple",
                positive=pos, negative=neg, latent_image=latent, denoise=1.0)
    frames = g("VAEDecode", samples=samples, vae=vae)
    return g, g("SaveImage", images=frames, filename_prefix="forge/video/ti2v").id


def i2v14(prompt: str, start_image: str, width: int, height: int, length: int, seed: int, *, models: dict[str, str],
          end_image: str | None = None, negative: str = WAN_NEGATIVE, lightning: bool = True,
          steps: int | None = None) -> tuple[Graph, str]:
    """Wan 2.2 14B (alta qualidade de movimento). Com end_image = first-last-frame (interpolação/emenda)."""
    g, frames, _ = wan_i2v(prompt, start_image, width, height, length, seed, models=models, end_image=end_image,
                           negative=negative, lightning=lightning, steps=steps, remove_bg=False)
    return g, frames


def frames_for(seconds: float, fps: int) -> int:
    """Quadros válidos para o Wan (4k+1)."""
    n = max(5, round(seconds * fps))
    return (n - 1) // 4 * 4 + 1
