# Creates "Asset Forge" shortcuts on the Desktop and in the Start Menu. ASCII only.
param([int]$Port = 8765)
$root = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $root "branding\icon.ico"
$launcher = Join-Path $root "scripts\launch.ps1"
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Asset Forge.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Asset Forge.lnk")
)
foreach ($lnk in $targets) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`" -Port $Port"
    $s.WorkingDirectory = $root
    $s.IconLocation = "$icon,0"
    $s.Description = "Asset Forge - local game asset generator"
    $s.WindowStyle = 7
    $s.Save()
    Write-Output "created $lnk"
}
