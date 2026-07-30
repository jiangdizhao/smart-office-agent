param(
    [string]$PythonExe = "python",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ServerRoot
try {
    & $PythonExe .\scripts\smoke_test_phase0.py --base-url $BaseUrl
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
