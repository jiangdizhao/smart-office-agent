param(
    [string]$PythonExe = "",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$WindowWidth = 960,
    [int]$WindowHeight = 540,
    [double]$PollHz = 10,
    [string]$EnrollName = "",
    [string]$ExternalId = ""
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
    ".\scripts\phase1234_live_view.py",
    "--base-url", $BaseUrl,
    "--window-width", $WindowWidth,
    "--window-height", $WindowHeight,
    "--poll-hz", $PollHz
)
if (-not [string]::IsNullOrWhiteSpace($EnrollName)) {
    $Arguments += @("--enroll-name", $EnrollName)
}
if (-not [string]::IsNullOrWhiteSpace($ExternalId)) {
    $Arguments += @("--external-id", $ExternalId)
}

Write-Host "Phase 1-4 live evaluation window"
Write-Host "Python: $ResolvedPython"
Write-Host "Server: $BaseUrl"
Write-Host "Window: ${WindowWidth}x${WindowHeight}"
if (-not [string]::IsNullOrWhiteSpace($EnrollName)) {
    Write-Host "Press E only after the named visitor has explicitly consented to enrollment."
    Write-Host "Enrollment name: $EnrollName"
}

Push-Location $ServerRoot
try {
    & $ResolvedPython @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
