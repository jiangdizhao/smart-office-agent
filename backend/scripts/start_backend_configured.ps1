param(
    [string]$CondaEnvName = "smartoffice",
    [string]$Model = "gpt-realtime-2.1",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$EnvFile
)

$ErrorActionPreference = "Stop"

$backendDirectory = Split-Path -Parent $PSScriptRoot
$repositoryRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $backendDirectory)).Path
$defaultEnvFile = Join-Path $backendDirectory ".env.local"
$templateFile = Join-Path $backendDirectory ".env.local.example"
$selectedEnvFile = if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $defaultEnvFile
}
elseif ([System.IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile
}
else {
    Join-Path $repositoryRoot $EnvFile
}

$loader = Join-Path $PSScriptRoot "import_backend_env.ps1"
$loaded = & $loader `
    -Path $selectedEnvFile `
    -CreateFromTemplate `
    -TemplatePath $templateFile

Write-Host "Loaded Backend configuration: $($loaded.Path)" -ForegroundColor Cyan
foreach ($name in $loaded.LoadedVariables) {
    $value = [Environment]::GetEnvironmentVariable([string]$name, "Process")
    if ([string]$name -match '(?i)(key|secret|token|password)') {
        Write-Host "  $name=<hidden>"
    }
    else {
        Write-Host "  $name=$value"
    }
}

$realtimeLauncher = Join-Path $PSScriptRoot "start_backend_realtime.ps1"
& $realtimeLauncher `
    -CondaEnvName $CondaEnvName `
    -Model $Model `
    -HostAddress $HostAddress `
    -Port $Port

exit $LASTEXITCODE
