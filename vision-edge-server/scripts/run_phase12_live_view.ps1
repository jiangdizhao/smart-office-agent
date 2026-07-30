param(
    [string]$PythonExe = "",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$WindowWidth = 960,
    [int]$WindowHeight = 540,
    [double]$PollHz = 10
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot

function Resolve-PythonExecutable {
    param([string]$RequestedPython)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
        return (Get-Command $RequestedPython -ErrorAction Stop).Source
    }
    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path $condaPython) {
            return (Resolve-Path $condaPython).Path
        }
    }
    return (Get-Command python -ErrorAction Stop).Source
}

$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$ActualPython = (& $ResolvedPython -c "import sys; print(sys.executable)").Trim()

Write-Host "Phase 1 + Phase 2 live visual test"
Write-Host "Conda environment: $env:CONDA_DEFAULT_ENV"
Write-Host "Python: $ActualPython"
Write-Host "Server: $BaseUrl"
Write-Host "Window: ${WindowWidth}x${WindowHeight}"

Push-Location $ServerRoot
try {
    & $ResolvedPython .\scripts\phase12_live_view.py `
        --base-url $BaseUrl `
        --window-width $WindowWidth `
        --window-height $WindowHeight `
        --poll-hz $PollHz
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
