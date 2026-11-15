# Installs (or updates) the TRELLIS.2 ComfyUI instance (image -> 3D, Microsoft TRELLIS.2-4B).
#
#   powershell -ExecutionPolicy Bypass -File comfy\install_trellis.ps1
#
# Separate from comfy\instance (Hunyuan, :8189): TRELLIS.2 needs compiled CUDA extensions (cumesh, flex_gemm,
# o_voxel, nvdiffrast...) whose prebuilt Windows wheels exist for torch 2.10 + CUDA 13 only, while the main
# instance runs a newer torch. Layout: comfy\instance-trellis\ComfyUI + .venv (Python 3.12), port 8190.
# Model weights (~17 GB) are downloaded by the node on first use into instance-trellis\ComfyUI\models.
param(
    [string]$ComfyRef = "v0.38.1",
    [string]$NodeRepo = "https://github.com/visualbruno/ComfyUI-Trellis2.git",
    [string]$NodeRef = "b0e84c0f29857a444e8bcb5b9be2ea218e96efd0",
    [string]$TorchIndex = "https://download.pytorch.org/whl/cu130",
    [string]$TorchVersion = "2.10.0",
    [string]$XformersVersion = "0.0.34",
    # shared models folder (same as the 3D instance / Comfy Desktop): this ComfyUI 0.38 also runs Qwen-Image 2.1;
    # TRELLIS weights stay in instance-trellis\ComfyUI\models (still the default folder)
    [string]$SharedModels = "$env:LOCALAPPDATA\Comfy-Desktop\ComfyUI-Shared\models"
)

$ErrorActionPreference = "Stop"
$root = Join-Path $PSScriptRoot "instance-trellis"
$comfy = Join-Path $root "ComfyUI"
$node = Join-Path $comfy "custom_nodes\ComfyUI-Trellis2"

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Native { param([scriptblock]$cmd) & $cmd; if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE): $cmd" } }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv not found. Install with: winget install astral-sh.uv" }
# keep uv's Python inside the repo: its default (%APPDATA%\uv\python) is virtualized per app on Windows
# (e.g. when installed from the Claude desktop app), and the venv then fails to start from any other terminal
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot "python"
New-Item -ItemType Directory -Force $root | Out-Null

Step "ComfyUI $ComfyRef"
if (-not (Test-Path (Join-Path $comfy ".git"))) { Native { git clone --quiet https://github.com/comfyanonymous/ComfyUI.git $comfy } }
Native { git -C $comfy fetch --quiet --tags origin }
Native { git -C $comfy -c advice.detachedHead=false checkout --quiet $ComfyRef }

Step "ComfyUI-Trellis2 @ $NodeRef"
if (-not (Test-Path (Join-Path $node ".git"))) { Native { git clone --quiet $NodeRepo $node } }
Native { git -C $node fetch --quiet origin }
Native { git -C $node -c advice.detachedHead=false checkout --quiet $NodeRef }

# every later install must keep this exact torch (the compiled wheels are tied to it)
Set-Content -Path (Join-Path $root "constraints.txt") -Encoding ascii -Value @(
    "torch==$TorchVersion", "torchaudio==$TorchVersion", "xformers==$XformersVersion")

# uv gets relative paths from here on: Windows PowerShell mangles absolute ones containing spaces
Push-Location $root
try {
    $py = "ComfyUI\.venv\Scripts\python.exe"
    Step "Python 3.12 venv"
    if (-not (Test-Path $py)) { Native { uv venv --python 3.12 "ComfyUI\.venv" } }

    Step "PyTorch $TorchVersion + xformers ($TorchIndex)"
    Native { uv pip install --python $py "torch==$TorchVersion" torchvision "torchaudio==$TorchVersion" "xformers==$XformersVersion" --index-url $TorchIndex }

    $common = @("--constraint", "constraints.txt", "--extra-index-url", $TorchIndex, "--index-strategy", "unsafe-best-match")
    Step "ComfyUI requirements"
    Native { uv pip install --python $py -r "ComfyUI\requirements.txt" @common }

    Step "ComfyUI-Trellis2 requirements"
    Native { uv pip install --python $py -r "ComfyUI\custom_nodes\ComfyUI-Trellis2\requirements.txt" huggingface_hub hf_transfer scikit-image "triton-windows>=3.6,<3.7" trimesh easydict plyfile zstandard tqdm filelock omegaconf matplotlib accelerate "rembg[cpu]" @common }

    Step "Prebuilt CUDA extensions (torch 2.10 / CUDA 13.1, cp312)"
    $wheelDir = "ComfyUI\custom_nodes\ComfyUI-Trellis2\wheels\Windows\Torch2100\CUDA 13.1"
    $whl = Get-ChildItem $wheelDir -Filter "*-cp312-cp312-win_amd64.whl" | ForEach-Object { Join-Path $wheelDir $_.Name }
    if (-not $whl) { throw "no cp312 wheels in $wheelDir" }
    Native { uv pip install --python $py --no-deps @whl }

    Step "Check"
    Native { & $py -c "import torch, xformers, cumesh, flex_gemm, o_voxel, nvdiffrast; print('torch', torch.__version__, '| cuda', torch.version.cuda, '| gpu', torch.cuda.get_device_name(0), '| xformers', xformers.__version__)" }
}
finally { Pop-Location }

Step "extra_model_paths.yaml -> $SharedModels (not the default folder)"
if (-not (Test-Path $SharedModels)) {
    Write-Warning "shared models folder not found; the instance will only use its own models folder"
} else {
    $folders = "checkpoints","clip_vision","diffusion_models","loras","model_patches","text_encoders","upscale_models",
               "background_removal","vae","clip","unet"
    $lines = @("# Generated by comfy\install_trellis.ps1 - shares models with the other instances", "asset_forge_shared:",
               "  base_path: '$SharedModels'")
    foreach ($f in $folders) { $lines += "  ${f}: ${f}/" }
    Set-Content -Path (Join-Path $comfy "extra_model_paths.yaml") -Value $lines -Encoding utf8
}
Write-Host "Done. Start with: powershell -File comfy\start_trellis.ps1" -ForegroundColor Green
