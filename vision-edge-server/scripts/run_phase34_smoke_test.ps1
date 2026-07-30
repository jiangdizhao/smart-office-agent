param(
    [string]$PythonExe = "",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$TimeoutSeconds = 90,
    [switch]$RequireFace
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot

function Resolve-PythonExecutable {
    param([string]$RequestedPython)
    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
        return (Get-Command $RequestedPython -ErrorAction Stop).Source
    }
    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $Candidate = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path $Candidate) { return (Resolve-Path $Candidate).Path }
    }
    return (Get-Command python -ErrorAction Stop).Source
}

$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$Arguments = @(
    ".\scripts\phase34_smoke_test.py",
    "--base-url", $BaseUrl,
    "--timeout-seconds", $TimeoutSeconds
)
if ($RequireFace) { $Arguments += "--require-face" }

Write-Host "Phase 3 + Phase 4 smoke test"
Write-Host "Python: $ResolvedPython"
Write-Host "Server: $BaseUrl"
if ($RequireFace) { Write-Host "A stable high-quality face is required during the test." }

Push-Location $ServerRoot
try {
    & $ResolvedPython @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
