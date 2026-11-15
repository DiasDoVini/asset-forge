# Starts the dedicated Asset Forge ComfyUI on 127.0.0.1:8189 (Comfy Desktop keeps 8188).
#
#   powershell -ExecutionPolicy Bypass -File comfy\start.ps1 [-Port 8189] [-LowVram]

param(
    [int]$Port = 8189,
    [switch]$LowVram
)

$comfy = Join-Path $PSScriptRoot "instance\ComfyUI"
$py = Join-Path $comfy ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "instance not installed. Run: powershell -File comfy\install.ps1" }

$argsList = @("main.py", "--listen", "127.0.0.1", "--port", $Port, "--disable-auto-launch",
              "--output-directory", (Join-Path $PSScriptRoot "instance\output"),
              "--input-directory", (Join-Path $PSScriptRoot "instance\input"))
if ($LowVram) { $argsList += "--lowvram" }

Push-Location $comfy
try { & $py @argsList } finally { Pop-Location }
