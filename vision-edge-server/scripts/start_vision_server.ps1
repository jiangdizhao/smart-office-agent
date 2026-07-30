param(
    [string]$PythonExe = "python",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $ServerRoot "config\vision.yaml"
}
$ResolvedConfig = (Resolve-Path $ConfigPath).Path

& $PythonExe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required. Python executable: $PythonExe"
}

$env:VISION_CONFIG_PATH = $ResolvedConfig
Write-Host "Starting RTX Vision Edge Server"
Write-Host "Python: $PythonExe"
Write-Host "Config: $ResolvedConfig"
Write-Host "Health: http://127.0.0.1:8015/health"
Write-Host "Events: ws://127.0.0.1:8015/ws/v1/events"

Push-Location $ServerRoot
try {
    & $PythonExe -m app
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
