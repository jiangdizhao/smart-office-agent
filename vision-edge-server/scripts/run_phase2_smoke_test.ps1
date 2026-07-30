param(
    [string]$PythonExe = "",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot

function Resolve-PythonExecutable {
    param([string]$RequestedPython)
    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
        return (Get-Command $RequestedPython -ErrorAction Stop).Source
    }
    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $candidate = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }
    return (Get-Command python -ErrorAction Stop).Source
}

$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$ActualPython = (& $ResolvedPython -c "import sys; print(sys.executable)").Trim()
Write-Host "Phase 1 + Phase 2 automated smoke test"
Write-Host "Conda environment: $env:CONDA_DEFAULT_ENV"
Write-Host "Python: $ActualPython"
Write-Host "Server: $BaseUrl"

Push-Location $ServerRoot
try {
    & $ResolvedPython .\scripts\phase2_smoke_test.py `
        --base-url $BaseUrl `
        --timeout-seconds $TimeoutSeconds
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
