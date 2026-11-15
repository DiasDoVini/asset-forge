# Starts the TRELLIS.2 ComfyUI instance on 127.0.0.1:8190 (Hunyuan instance: 8189, Comfy Desktop: 8188).
#
#   powershell -ExecutionPolicy Bypass -File comfy\start_trellis.ps1 [-Port 8190]

param([int]$Port = 8190)

$comfy = Join-Path $PSScriptRoot "instance-trellis\ComfyUI"
$py = Join-Path $comfy ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "instance not installed. Run: powershell -File comfy\install_trellis.ps1" }

$env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"
$env:HF_HUB_ENABLE_HF_TRANSFER = "1"
# --disable-xformers: ComfyUI core attention uses PyTorch SDPA. The xformers build here has no kernels for
# Blackwell (sm_120), which broke other models run on this instance (e.g. Qwen-Image 2.1). TRELLIS.2 picks its own
# attention backend (node option / ATTN_BACKEND), so it is not affected.
$argsList = @("main.py", "--listen", "127.0.0.1", "--port", $Port, "--disable-auto-launch", "--disable-xformers",
              "--output-directory", (Join-Path $PSScriptRoot "instance-trellis\output"),
              "--input-directory", (Join-Path $PSScriptRoot "instance-trellis\input"))
Push-Location $comfy
try { & $py @argsList } finally { Pop-Location }
