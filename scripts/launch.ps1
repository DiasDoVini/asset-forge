# Asset Forge launcher (desktop shortcut). ASCII only: Windows PowerShell 5.1 reads non-BOM files as ANSI.
# Starts "forge up" hidden (ComfyUI instances + supervised server) if it is not running, then opens the UI
# in an app window (Edge --app) or the default browser.
param([int]$Port = 8765)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$url = "http://127.0.0.1:$Port"
$forge = Join-Path $root ".venv\Scripts\forge.exe"

function Test-Up { try { (Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false } }

if (-not (Test-Up)) {
    if (-not (Test-Path $forge)) {
        [void][System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")
        [System.Windows.Forms.MessageBox]::Show("forge.exe not found in $root\.venv. Run 'uv sync' first.", "Asset Forge") | Out-Null
        exit 1
    }
    $logs = Join-Path $root ".logs"
    New-Item -ItemType Directory -Force $logs | Out-Null
    Start-Process -FilePath $forge -ArgumentList "up", "--port", "$Port" -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "forge-up.log") -RedirectStandardError (Join-Path $logs "forge-up.err.log")
    for ($i = 0; $i -lt 90 -and -not (Test-Up); $i++) { Start-Sleep -Seconds 1 }
}

$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) { Start-Process $edge -ArgumentList "--app=$url" } else { Start-Process $url }
