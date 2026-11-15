"""Download Tencent's Hunyuan3D 2.1 paint weights and convert them to the single-file layout the native
ComfyUI paint nodes load (diffusion_models/ + vae/ in the shared models folder).

Run with the dedicated instance's Python (it has torch + safetensors):

    comfy\\instance\\ComfyUI\\.venv\\Scripts\\python.exe comfy\\convert_hunyuan_paint.py

The .bin files are loaded with torch.load(weights_only=True) (tensors only, no code execution) and
deleted after conversion. Re-running skips what already exists.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import torch
from safetensors.torch import save_file

HERE = Path(__file__).resolve().parent
REPO = "https://huggingface.co/tencent/Hunyuan3D-2.1/resolve/main/hunyuan3d-paintpbr-v2-1"
SHARED = Path(os.environ.get("FORGE_MODELS_DIR", Path(os.environ["LOCALAPPDATA"]) / "Comfy-Desktop" / "ComfyUI-Shared" / "models"))
CACHE = HERE / "instance" / "cache"

JOBS = [
    # (url, target, keys that must exist)
    (f"{REPO}/unet/diffusion_pytorch_model.bin", SHARED / "diffusion_models" / "hunyuan3d-paintpbr-v2-1.safetensors",
     ["unet.conv_in.weight", "unet_dual.conv_in.weight", "unet.learned_text_clip_albedo"]),
    (f"{REPO}/vae/diffusion_pytorch_model.bin", SHARED / "vae" / "hunyuan3d_paintpbr_v2_1_vae.safetensors",
     ["encoder.conv_in.weight", "decoder.conv_out.weight"]),
]


def _download():
    spec = importlib.util.spec_from_file_location("download_models", HERE / "download_models.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.download


def main() -> int:
    download = _download()
    CACHE.mkdir(parents=True, exist_ok=True)
    for url, target, required in JOBS:
        if target.exists():
            print(f"{target.name}: already present")
            continue
        raw = CACHE / ("paint_" + url.split("/")[-2] + ".bin")
        if not raw.exists():
            print(f"downloading {url}")
            download(url, raw)
        print(f"converting {raw.name} -> {target}")
        sd = torch.load(raw, map_location="cpu", weights_only=True)
        missing = [k for k in required if k not in sd]
        if missing:
            print(f"ERROR: {raw.name} lacks expected keys {missing}; first keys: {sorted(sd)[:5]}")
            return 1
        sd = {k: v.contiguous() for k, v in sd.items() if isinstance(v, torch.Tensor)}
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        save_file(sd, str(tmp), metadata={"source": url})
        tmp.replace(target)
        raw.unlink()
        print(f"{target.name}: {len(sd)} tensors, {target.stat().st_size / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
