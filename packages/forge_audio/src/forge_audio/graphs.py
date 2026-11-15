"""Grafos (formato API) do ComfyUI para áudio, baseados nos templates oficiais do Comfy 0.28:
audio_stable_audio_3_medium (SFX/ambiência) e audio_ace_step_1_5_split (música com ou sem letra).
Só nós nativos.
"""
from __future__ import annotations

from forge_2d.graphs import Graph, N

# sobrescreva em project.yaml -> audio.models.<chave>
DEFAULT_MODELS: dict[str, str] = {
    "sa3_sfx": "stable_audio_3_small_sfx.safetensors",
    "sa3_medium": "stable_audio_3_medium.safetensors",
    "sa3_clip": "t5gemma_b_b_ul2.safetensors",
    "ace_unet": "acestep_v1.5_turbo.safetensors",
    "ace_clip1": "qwen_0.6b_ace15.safetensors",
    "ace_clip2": "qwen_1.7b_ace15.safetensors",
    "ace_vae": "ace_1.5_vae.safetensors",
}

# arquivo -> (pasta do Comfy, URL) para checagem e download
MODEL_SOURCES: dict[str, tuple[str, str]] = {
    "stable_audio_3_small_sfx.safetensors": ("checkpoints", "https://huggingface.co/Comfy-Org/stable-audio-3/resolve/main/checkpoints/stable_audio_3_small_sfx.safetensors"),
    "stable_audio_3_medium.safetensors": ("checkpoints", "https://huggingface.co/Comfy-Org/stable-audio-3/resolve/main/checkpoints/stable_audio_3_medium.safetensors"),
    "t5gemma_b_b_ul2.safetensors": ("text_encoders", "https://huggingface.co/Comfy-Org/stable-audio-3/resolve/main/text_encoders/t5gemma_b_b_ul2.safetensors"),
    "acestep_v1.5_turbo.safetensors": ("diffusion_models", "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/diffusion_models/acestep_v1.5_turbo.safetensors"),
    "qwen_0.6b_ace15.safetensors": ("text_encoders", "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_0.6b_ace15.safetensors"),
    "qwen_1.7b_ace15.safetensors": ("text_encoders", "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_1.7b_ace15.safetensors"),
    "ace_1.5_vae.safetensors": ("vae", "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/vae/ace_1.5_vae.safetensors"),
}


def _save(g: Graph, audio: N, prefix: str) -> N:
    return g("SaveAudio", audio=audio, filename_prefix=prefix)


def stable_audio(prompt: str, negative: str, seconds: float, seed: int, *, models: dict[str, str],
                 model_key: str = "sa3_sfx", steps: int = 8, cfg: float = 1.0, batch: int = 1,
                 source_audio: str | None = None, denoise: float = 1.0) -> tuple[Graph, str]:
    """Stable Audio 3 (destilado: 8 passos, cfg 1, lcm). `source_audio` = nome já enviado (áudio → áudio)."""
    g = Graph()
    ckpt = g("CheckpointLoaderSimple", ckpt_name=models[model_key])
    clip = g("CLIPLoader", clip_name=models["sa3_clip"], type="stable_audio", device="default")
    pos = g("CLIPTextEncode", clip=clip, text=prompt)
    neg = g("CLIPTextEncode", clip=clip, text=negative)
    if source_audio:
        src = g("LoadAudio", audio=source_audio)
        latent = g("VAEEncodeAudio", audio=src, vae=ckpt[2])
        if batch > 1:
            latent = g("RepeatLatentBatch", samples=latent, amount=batch)
    else:
        latent = g("EmptyLatentAudio", seconds=float(seconds), batch_size=batch)
    samples = g("KSampler", model=ckpt[0], seed=seed, steps=steps, cfg=cfg, sampler_name="lcm", scheduler="simple",
                positive=pos, negative=neg, latent_image=latent, denoise=denoise)
    audio = g("VAEDecodeAudio", samples=samples, vae=ckpt[2])
    return g, _save(g, audio, "forge/audio/sa3").id


def ace_step15(tags: str, lyrics: str, seconds: float, seed: int, *, models: dict[str, str], bpm: int = 120,
               key: str = "C major", time_signature: str = "4", language: str = "en", steps: int = 8,
               cfg: float = 1.0, source_audio: str | None = None, denoise: float = 1.0) -> tuple[Graph, str]:
    """ACE-Step 1.5 turbo (música; letra opcional — "[Instrumental]" sem vocal)."""
    g = Graph()
    model: N = g("UNETLoader", unet_name=models["ace_unet"], weight_dtype="default")
    model = g("ModelSamplingAuraFlow", model=model, shift=3.0)
    clip = g("DualCLIPLoader", clip_name1=models["ace_clip1"], clip_name2=models["ace_clip2"], type="ace", device="default")
    vae = g("VAELoader", vae_name=models["ace_vae"])
    pos = g("TextEncodeAceStepAudio1.5", clip=clip, tags=tags, lyrics=lyrics or "[Instrumental]", seed=seed, bpm=int(bpm),
            duration=float(seconds), timesignature=str(time_signature), language=language, keyscale=key,
            generate_audio_codes=True, cfg_scale=2.0, temperature=0.85, top_p=0.9, top_k=0, min_p=0.0)
    neg = g("ConditioningZeroOut", conditioning=pos)
    if source_audio:
        src = g("LoadAudio", audio=source_audio)
        latent = g("VAEEncodeAudio", audio=src, vae=vae)
    else:
        latent = g("EmptyAceStep1.5LatentAudio", seconds=float(seconds), batch_size=1)
    samples = g("KSampler", model=model, seed=seed, steps=steps, cfg=cfg, sampler_name="euler", scheduler="simple",
                positive=pos, negative=neg, latent_image=latent, denoise=denoise)
    audio = g("VAEDecodeAudio", samples=samples, vae=vae)
    return g, _save(g, audio, "forge/audio/ace").id
