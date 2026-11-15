# Sobe o ComfyUI do Comfy Desktop em modo API (sem abrir o app), com os modelos compartilhados.
# Uso: scripts\comfy-desktop.ps1 [-Port 8188] [args extras do ComfyUI]
param([int]$Port = 8188, [Parameter(ValueFromRemainingArguments = $true)]$Rest)
$ErrorActionPreference = "Stop"
$install = "$env:LOCALAPPDATA\Comfy-Desktop\ComfyUI-Installs\ComfyUI"
$shared = "$env:APPDATA\Comfy Desktop\shared_model_paths.yaml"
if (-not (Test-Path "$install\standalone-env\python.exe")) { throw "Comfy Desktop não encontrado em $install" }
Set-Location "$install\ComfyUI"
$ErrorActionPreference = "Continue"  # ComfyUI loga em stderr; no PS 5.1 isso viraria erro fatal
&"$install\standalone-env\python.exe" main.py --listen 127.0.0.1 --port $Port `
    --extra-model-paths-config $shared --disable-auto-launch @Rest
