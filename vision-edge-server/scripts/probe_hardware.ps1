param(
    [ValidateSet("all", "gpu", "camera")]
    [string]$Mode = "all",
    [string]$PythonExe = "",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot

function Resolve-PythonExecutable {
    param([string]$RequestedPython)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
        $explicit = Get-Command $RequestedPython -ErrorAction Stop
        return $explicit.Source
    }

    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path $condaPython) {
            return (Resolve-Path $condaPython).Path
        }
    }

    $pythonCommand = Get-Command python -ErrorAction Stop
    return $pythonCommand.Source
}

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $ServerRoot "config\vision.yaml"
}
$ResolvedConfig = (Resolve-Path $ConfigPath).Path
$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$ActualPython = (& $ResolvedPython -c "import sys; print(sys.executable)").Trim()

$Command = switch ($Mode) {
    "gpu" { "probe-gpu" }
    "camera" { "probe-camera" }
    default { "probe-all" }
}

Write-Host "Vision hardware probe"
Write-Host "Conda environment: $env:CONDA_DEFAULT_ENV"
Write-Host "Python: $ActualPython"
Write-Host "Config: $ResolvedConfig"

Push-Location $ServerRoot
try {
    & $ResolvedPython -m app.cli $Command --config $ResolvedConfig
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
