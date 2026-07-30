param(
    [ValidateSet("all", "gpu", "camera")]
    [string]$Mode = "all",
    [string]$PythonExe = "python",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $ServerRoot "config\vision.yaml"
}
$ResolvedConfig = (Resolve-Path $ConfigPath).Path
$Command = switch ($Mode) {
    "gpu" { "probe-gpu" }
    "camera" { "probe-camera" }
    default { "probe-all" }
}

Push-Location $ServerRoot
try {
    & $PythonExe -m app.cli $Command --config $ResolvedConfig
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
