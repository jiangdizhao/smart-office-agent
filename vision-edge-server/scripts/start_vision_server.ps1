param(
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

& $ResolvedPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required. Python executable: $ActualPython"
}

$env:VISION_CONFIG_PATH = $ResolvedConfig
Write-Host "Starting RTX Vision Edge Server"
Write-Host "Conda environment: $env:CONDA_DEFAULT_ENV"
Write-Host "Python: $ActualPython"
Write-Host "Config: $ResolvedConfig"
Write-Host "Health: http://127.0.0.1:8015/health"
Write-Host "Events: ws://127.0.0.1:8015/ws/v1/events"

Push-Location $ServerRoot
try {
    & $ResolvedPython -m app
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
