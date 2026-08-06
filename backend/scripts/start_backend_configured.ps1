param(
    [string]$CondaEnvName = "sm",
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

[Environment]::SetEnvironmentVariable(
    "SMART_OFFICE_ENV_FILE_LOADED",
    [string]$loaded.Path,
    "Process"
)

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

$adminHash = [Environment]::GetEnvironmentVariable(
    "SMART_OFFICE_ADMIN_PASSWORD_HASH",
    "Process"
)
$adminPassword = [Environment]::GetEnvironmentVariable(
    "SMART_OFFICE_ADMIN_PASSWORD",
    "Process"
)
$adminSource = if (-not [string]::IsNullOrWhiteSpace($adminHash)) {
    "SMART_OFFICE_ADMIN_PASSWORD_HASH"
}
elseif (-not [string]::IsNullOrEmpty($adminPassword)) {
    "SMART_OFFICE_ADMIN_PASSWORD"
}
else {
    "none"
}

if ($adminSource -eq "none") {
    throw @"
Result Center administrator authentication is not configured in the Backend process.
Loaded file: $($loaded.Path)
Set exactly one non-empty value:
  SMART_OFFICE_ADMIN_PASSWORD=your-password
or
  SMART_OFFICE_ADMIN_PASSWORD_HASH=pbkdf2_sha256`$...
Then start the Backend again with this configured launcher.
"@
}
Write-Host "Result Center admin configured: True ($adminSource)" -ForegroundColor Green

$existingListeners = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
)
if ($existingListeners.Count -gt 0) {
    $pids = @($existingListeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $descriptions = @()
    foreach ($processId in $pids) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        $name = if ($null -ne $process) { $process.ProcessName } else { "unknown" }
        $descriptions += "PID $processId ($name)"
    }
    throw @"
Backend port $Port is already occupied by: $($descriptions -join ', ').
The browser would continue talking to that old process, which may not contain the newly loaded password.
Stop it first, for example:
  Stop-Process -Id $($pids -join ',') -Force
Then run start_backend_configured.ps1 again.
"@
}

$realtimeLauncher = Join-Path $PSScriptRoot "start_backend_realtime.ps1"
& $realtimeLauncher `
    -CondaEnvName $CondaEnvName `
    -Model $Model `
    -HostAddress $HostAddress `
    -Port $Port

exit $LASTEXITCODE
