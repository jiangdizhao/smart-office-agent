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
        if (Test-Path $Candidate) {
            return (Resolve-Path $Candidate).Path
        }
    }
    return (Get-Command python -ErrorAction Stop).Source
}

$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$Arguments = @(
    ".\scripts\phase1234_smoke_test.py",
    "--base-url", $BaseUrl,
    "--timeout-seconds", $TimeoutSeconds
)
if ($RequireFace) {
    $Arguments += "--require-face"
}

Write-Host "Complete Phase 1-4 smoke test"
Write-Host "Python: $ResolvedPython"
Write-Host "Server: $BaseUrl"
Write-Host "OSNet is required by this test."
if ($RequireFace) {
    Write-Host "A stable quality-approved face is also required."
}

Push-Location $ServerRoot
try {
    & $ResolvedPython @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
