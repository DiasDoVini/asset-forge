# Dedicated ComfyUI instance

A pinned ComfyUI (Python 3.12, PyTorch cu130 for RTX 50xx) on **127.0.0.1:8189**, separate from
Comfy Desktop (:8188). It shares Comfy Desktop's models folder through `extra_model_paths.yaml`, so nothing is
downloaded twice.

**Pinned source:** `jethac/ComfyUI@2d1c6623` - the branch of [Comfy-Org/ComfyUI#15020](https://github.com/Comfy-Org/ComfyUI/pull/15020),
which adds **native Hunyuan3D 2.1 PBR paint** (torch only, no compiled CUDA extensions, so it runs on Blackwell).
It is based on ComfyUI v0.34 and keeps every native node the other pipelines use. When the PR ships in an
official release, switch back: `install.ps1 -ComfyRemote origin -ComfyRef vX.Y.Z`.

```powershell
powershell -ExecutionPolicy Bypass -File comfy\install.ps1   # install / update (idempotent)
powershell -ExecutionPolicy Bypass -File comfy\start.ps1     # run on :8189 (add -LowVram if needed)
uv run python comfy\download_models.py                       # 3D shape model (Hunyuan3D 2.1, ~6.9 GB)
uv run python comfy\download_models.py --list
# Hunyuan3D 2.1 paint weights (~4 GB download, converted to safetensors; needs the instance's torch):
comfy\instance\ComfyUI\.venv\Scripts\python.exe comfy\convert_hunyuan_paint.py
```

- `custom_nodes/forge_nodes/`: our own small nodes, linked into the instance by `install.ps1`
  (`ForgeLoad3DFile`: load a mesh from the input folder by path, for API workflows).
- `custom_nodes.json`: third-party custom nodes pinned by commit (`[{"name", "repo", "commit"}]`). Empty.
- `models.json`: model manifest used by `download_models.py`. Files land in
  `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Shared\models` (override with `FORGE_MODELS_DIR`).
- `instance/` (git-ignored): the ComfyUI checkout, its venv, input/, output/ and the download cache.

## TRELLIS.2 instance (:8190)

Microsoft TRELLIS.2-4B (MIT): sharper edges and more detail than Hunyuan3D 2.1 from a single image, optional PBR
texture in the same run. It needs compiled CUDA extensions (cumesh, flex_gemm, o_voxel, nvdiffrast) whose
prebuilt Windows wheels (from [ComfyUI-Trellis2](https://github.com/visualbruno/ComfyUI-Trellis2), pinned) exist for
torch 2.10 + CUDA 13 only, so it runs in its own instance next to the main one.

```powershell
powershell -ExecutionPolicy Bypass -File comfy\install_trellis.ps1   # ComfyUI v0.38.1 + torch 2.10 cu130 + node + wheels
comfy\instance-trellis\ComfyUI\.venv\Scripts\hf.exe auth login       # once, after accepting the DINOv3 license (gated)
comfy\instance-trellis\ComfyUI\.venv\Scripts\python.exe comfy\download_trellis_models.py   # ~17.5 GB, official repos
powershell -ExecutionPolicy Bypass -File comfy\start_trellis.ps1     # :8190
```

Use it with `3d.image_to_3d` / `3d.text_to_3d` `generator: "trellis2"` (or project `3d.generator: trellis2`).
DINOv3 must come from the official gated repo: Asset Forge refuses to run TRELLIS.2 without it, because the node
would otherwise download an unofficial mirror by itself.
