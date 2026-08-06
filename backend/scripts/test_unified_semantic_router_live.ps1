param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [switch]$SkipModelCases,
    [switch]$OfflineContract
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$pythonCommand = Get-Command python -ErrorAction Stop
$scriptPath = Join-Path $PSScriptRoot "test_unified_semantic_router_live.py"

$arguments = @($scriptPath, "--base-url", $BaseUrl)
if ($SkipModelCases) {
    $arguments += "--skip-model-cases"
}
if ($OfflineContract) {
    $arguments += "--offline-contract"
}

& $pythonCommand.Source @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Unified semantic router acceptance failed with exit code $LASTEXITCODE."
}
