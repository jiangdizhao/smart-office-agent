param(
    [ValidateSet("list", "enroll", "delete")]
    [string]$Action,
    [string]$PythonExe = "",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$TrackId = 0,
    [string]$DisplayName = "",
    [string]$ExternalId = "",
    [string]$IdentityId = "",
    [switch]$Consent
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
function Resolve-PythonExecutable {
    param([string]$RequestedPython)
    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) { return (Get-Command $RequestedPython -ErrorAction Stop).Source }
    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $Candidate = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path $Candidate) { return (Resolve-Path $Candidate).Path }
    }
    return (Get-Command python -ErrorAction Stop).Source
}

$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$Arguments = @(".\scripts\manage_identity.py", $Action, "--base-url", $BaseUrl)
if ($TrackId -gt 0) { $Arguments += @("--track-id", $TrackId) }
if (-not [string]::IsNullOrWhiteSpace($DisplayName)) { $Arguments += @("--display-name", $DisplayName) }
if (-not [string]::IsNullOrWhiteSpace($ExternalId)) { $Arguments += @("--external-id", $ExternalId) }
if (-not [string]::IsNullOrWhiteSpace($IdentityId)) { $Arguments += @("--identity-id", $IdentityId) }
if ($Consent) { $Arguments += "--consent" }

Push-Location $ServerRoot
try {
    & $ResolvedPython @Arguments
    exit $LASTEXITCODE
}
finally { Pop-Location }
