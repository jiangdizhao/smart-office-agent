param(
    [string]$PythonExe = "",
    [string]$BaseUrl = "http://127.0.0.1:8015"
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

$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$ActualPython = (& $ResolvedPython -c "import sys; print(sys.executable)").Trim()
Write-Host "Vision smoke test"
Write-Host "Conda environment: $env:CONDA_DEFAULT_ENV"
Write-Host "Python: $ActualPython"
Write-Host "Base URL: $BaseUrl"

Push-Location $ServerRoot
try {
    & $ResolvedPython .\scripts\smoke_test_phase0.py --base-url $BaseUrl
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
