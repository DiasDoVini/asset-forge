"""Downloads the TRELLIS.2 weights into the TRELLIS ComfyUI instance, from the official repositories only.

    comfy\\instance-trellis\\ComfyUI\\.venv\\Scripts\\python.exe comfy\\download_trellis_models.py

- microsoft/TRELLIS.2-4B (16 GB, MIT)
- facebook/dinov3-vitl16-pretrain-lvd1689m (1.2 GB): gated. Accept the license on its HuggingFace page first and
  log in once with: comfy\\instance-trellis\\ComfyUI\\.venv\\Scripts\\hf.exe auth login
- microsoft/TRELLIS-image-large: the one sparse-structure decoder file the node also loads

Placing DINOv3 here also stops ComfyUI-Trellis2 from fetching its unofficial mirror on first use.
"""

import sys
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

MODELS = Path(__file__).parent / "instance-trellis" / "ComfyUI" / "models"


def main() -> int:
    print("TRELLIS.2-4B ...")
    snapshot_download("microsoft/TRELLIS.2-4B", local_dir=MODELS / "microsoft" / "TRELLIS.2-4B")
    for name in ("ss_dec_conv3d_16l8_fp16.json", "ss_dec_conv3d_16l8_fp16.safetensors"):
        hf_hub_download("microsoft/TRELLIS-image-large", f"ckpts/{name}", local_dir=MODELS / "microsoft" / "TRELLIS-image-large")
    print("DINOv3 (gated) ...")
    try:
        for name in ("model.safetensors", "config.json", "preprocessor_config.json"):
            hf_hub_download("facebook/dinov3-vitl16-pretrain-lvd1689m", name,
                            local_dir=MODELS / "facebook" / "dinov3-vitl16-pretrain-lvd1689m")
    except (GatedRepoError, HfHubHTTPError) as exc:
        print(f"\nDINOv3 not downloaded: {exc}\nAccept the license at "
              "https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m and run `hf auth login`, then run this again.")
        return 1
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
