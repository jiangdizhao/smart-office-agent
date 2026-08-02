param(
    [string]$CondaEnvName = "smartoffice",
    [string]$Model = "gpt-realtime-2.1",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

function Test-ProjectPython {
    param(
        [string]$Candidate,
        [string]$ExpectedEnvName
    )

    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
        return $false
    }

    try {
        $probe = & $Candidate -c "import json, sys; print(json.dumps({'prefix': sys.prefix, 'version': list(sys.version_info[:3]), 'exe': sys.executable}))" 2>$null
        if (-not $probe) {
            return $false
        }

        $info = $probe | Select-Object -Last 1 | ConvertFrom-Json
        $envName = Split-Path -Leaf ([string]$info.prefix)
        $isExpectedEnvironment = $envName -ieq $ExpectedEnvName
        $isSupportedPython = ([int]$info.version[0] -eq 3) -and ([int]$info.version[1] -ge 11)
        return $isExpectedEnvironment -and $isSupportedPython
    }
    catch {
        return $false
    }
}

function Add-Candidate {
    param(
        [System.Collections.Generic.List[string]]$Candidates,
        [string]$Candidate
    )

    if ($Candidate -and -not $Candidates.Contains($Candidate)) {
        $Candidates.Add($Candidate)
    }
}

function Resolve-CondaEnvironmentPython {
    param([string]$EnvironmentName)

    $candidates = New-Object System.Collections.Generic.List[string]

    try {
        $envListJson = & conda env list --json 2>$null
        if ($LASTEXITCODE -eq 0 -and $envListJson) {
            $envList = $envListJson | ConvertFrom-Json
            foreach ($environmentPath in $envList.envs) {
                if ((Split-Path -Leaf ([string]$environmentPath)) -ieq $EnvironmentName) {
                    Add-Candidate -Candidates $candidates -Candidate (Join-Path ([string]$environmentPath) "python.exe")
                }
            }
        }
    }
    catch {
    }

    try {
        $condaBase = (& conda info --base 2>$null | Select-Object -Last 1).Trim()
        if ($condaBase) {
            Add-Candidate -Candidates $candidates -Candidate (Join-Path $condaBase "envs\$EnvironmentName\python.exe")
        }
    }
    catch {
    }

    try {
        $activePython = (Get-Command python -ErrorAction Stop).Source
        Add-Candidate -Candidates $candidates -Candidate $activePython
    }
    catch {
    }

    if ($env:CONDA_PREFIX -and (Split-Path -Leaf $env:CONDA_PREFIX) -ieq $EnvironmentName) {
        Add-Candidate -Candidates $candidates -Candidate (Join-Path $env:CONDA_PREFIX "python.exe")
    }

    Add-Candidate -Candidates $candidates -Candidate "D:\anaconda3\envs\$EnvironmentName\python.exe"
    Add-Candidate -Candidates $candidates -Candidate "C:\Users\$env:USERNAME\anaconda3\envs\$EnvironmentName\python.exe"
    Add-Candidate -Candidates $candidates -Candidate "C:\Users\$env:USERNAME\miniconda3\envs\$EnvironmentName\python.exe"

    foreach ($candidate in $candidates) {
        if (Test-ProjectPython -Candidate $candidate -ExpectedEnvName $EnvironmentName) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $checked = if ($candidates.Count -gt 0) { $candidates -join "`n  - " } else { "(none discovered)" }
    throw "Could not automatically locate Python 3.11+ in the '$EnvironmentName' Conda environment.`nChecked:`n  - $checked`nCreate it with: conda create -n $EnvironmentName python=3.11 -y"
}

function Resolve-PortableProjectPath {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentVariable,
        [Parameter(Mandatory = $true)][string]$DefaultRelativePath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [switch]$CreateDirectory,
        [switch]$RequireFile
    )

    $configured = [Environment]::GetEnvironmentVariable($EnvironmentVariable, "Process")
    $usedDefault = [string]::IsNullOrWhiteSpace($configured)

    if ($usedDefault) {
        $candidate = Join-Path $RepositoryRoot $DefaultRelativePath
    }
    elseif ([System.IO.Path]::IsPathRooted($configured)) {
        $candidate = $configured
        $pathRoot = [System.IO.Path]::GetPathRoot($candidate)
        if ($pathRoot -and -not (Test-Path -LiteralPath $pathRoot)) {
            Write-Warning "$EnvironmentVariable points to unavailable root '$pathRoot'. Using this repository checkout instead."
            $candidate = Join-Path $RepositoryRoot $DefaultRelativePath
            $usedDefault = $true
        }
    }
    else {
        $candidate = Join-Path $RepositoryRoot $configured
    }

    $fullPath = [System.IO.Path]::GetFullPath($candidate)

    if ($CreateDirectory) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
    }
    if ($RequireFile -and -not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        if (-not $usedDefault) {
            $portableFallback = [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $DefaultRelativePath))
            if (Test-Path -LiteralPath $portableFallback -PathType Leaf) {
                Write-Warning "$EnvironmentVariable points to a missing file: $fullPath. Using repository file: $portableFallback"
                return $portableFallback
            }
        }
        throw "Required Smart Office file was not found: $fullPath"
    }

    return $fullPath
}

function Invoke-OptionalPythonProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Code
    )

    $previousPreference = $ErrorActionPreference
    try {
        # Python writes tracebacks to stderr. Under ErrorActionPreference=Stop,
        # Windows PowerShell converts that expected diagnostic stream into a
        # terminating NativeCommandError before this script can inspect it.
        $ErrorActionPreference = "Continue"
        $output = & $Python -c $Code 2>&1
        $exitCode = $LASTEXITCODE
    }
    catch {
        $output = @($_.Exception.Message)
        $exitCode = 1
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    return [PSCustomObject]@{
        ExitCode = $exitCode
        Output = @($output)
        Text = (@($output) | ForEach-Object { [string]$_ }) -join "`n"
    }
}

$backendDirectory = Split-Path -Parent $PSScriptRoot
$repoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $backendDirectory)).Path
$env:SMART_OFFICE_PROJECT_ROOT = $repoRoot

$resolvedPython = Resolve-CondaEnvironmentPython -EnvironmentName $CondaEnvName

$dependencyProbe = & $resolvedPython -c "import fastapi, sse_starlette, uvicorn, pythoncom, win32com.client; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "The automatically selected '$CondaEnvName' Python is missing backend or Office COM dependencies.`nRun once from the repository root:`n  conda run -n $CondaEnvName python -m pip install -r backend/requirements-smartoffice.txt`nDetails: $dependencyProbe"
}

$env:SMART_OFFICE_DEMO_PPT = Resolve-PortableProjectPath `
    -EnvironmentVariable "SMART_OFFICE_DEMO_PPT" `
    -DefaultRelativePath "demo_files\Loss.pptx" `
    -RepositoryRoot $repoRoot `
    -RequireFile

$env:SMART_OFFICE_OUTPUT_DIR = Resolve-PortableProjectPath `
    -EnvironmentVariable "SMART_OFFICE_OUTPUT_DIR" `
    -DefaultRelativePath "demo_files\LOG" `
    -RepositoryRoot $repoRoot `
    -CreateDirectory

if (-not $env:SMART_OFFICE_PRESENTATION_MONITOR_DEVICE) {
    $env:SMART_OFFICE_PRESENTATION_MONITOR_DEVICE = "\\.\DISPLAY2"
}
if (-not $env:SMART_OFFICE_PRESENTATION_MONITOR_NUMBER) {
    $env:SMART_OFFICE_PRESENTATION_MONITOR_NUMBER = "2"
}
if (-not $env:SMART_OFFICE_OUTLOOK_SENDER_EMAIL) {
    $env:SMART_OFFICE_OUTLOOK_SENDER_EMAIL = "jiangdizhao1@outlook.com"
}

$env:SMART_OFFICE_EMAIL_RECIPIENTS_FILE = Resolve-PortableProjectPath `
    -EnvironmentVariable "SMART_OFFICE_EMAIL_RECIPIENTS_FILE" `
    -DefaultRelativePath "config\email_recipients.json" `
    -RepositoryRoot $repoRoot

$recipientTemplate = Join-Path $repoRoot "config\email_recipients.example.json"
if (-not (Test-Path -LiteralPath $env:SMART_OFFICE_EMAIL_RECIPIENTS_FILE -PathType Leaf)) {
    if (Test-Path -LiteralPath $recipientTemplate -PathType Leaf) {
        $recipientParent = Split-Path -Parent $env:SMART_OFFICE_EMAIL_RECIPIENTS_FILE
        New-Item -ItemType Directory -Path $recipientParent -Force | Out-Null
        Copy-Item -LiteralPath $recipientTemplate -Destination $env:SMART_OFFICE_EMAIL_RECIPIENTS_FILE
        Write-Host "Created local recipient file from template: $env:SMART_OFFICE_EMAIL_RECIPIENTS_FILE" -ForegroundColor Yellow
    }
    else {
        Write-Warning "Outlook recipient template was not found: $recipientTemplate. Outlook recipient actions will remain unavailable, but Backend startup will continue."
    }
}

$recipientInfo = $null
$recipientProbeError = $null
Push-Location $backendDirectory
try {
    $probeCode = "import json; from app.presentation_config import presentation_config as c; d=c.recipient_directory(); print(json.dumps({'config_path': str(d.config_path), 'default_key': d.default_recipient_key, 'recipients': d.public_catalog()}, ensure_ascii=False))"
    $probe = Invoke-OptionalPythonProbe -Python $resolvedPython -Code $probeCode
    if ($probe.ExitCode -eq 0 -and $probe.Output.Count -gt 0) {
        try {
            $recipientInfo = ($probe.Output | Select-Object -Last 1) | ConvertFrom-Json
        }
        catch {
            $recipientProbeError = "Recipient probe returned invalid JSON.`n$($probe.Text)"
        }
    }
    else {
        $recipientProbeError = $probe.Text
    }
}
finally {
    Pop-Location
}

if (-not $recipientInfo) {
    Write-Warning @"
Outlook recipient configuration is unavailable. Backend startup will continue.
Only Outlook draft/send actions are affected.
File: $env:SMART_OFFICE_EMAIL_RECIPIENTS_FILE
Details:
$recipientProbeError
"@
}
else {
    foreach ($recipient in $recipientInfo.recipients) {
        if ($env:SMART_OFFICE_OUTLOOK_SENDER_EMAIL -ieq [string]$recipient.email) {
            Write-Warning "Outlook sender and configured recipient '$($recipient.key)' use the same address. Outlook actions for this recipient will fail validation, but Backend startup will continue."
        }
    }
}

$secureKey = Read-Host "OpenAI API key" -AsSecureString
if ($secureKey.Length -eq 0) {
    throw "OPENAI_API_KEY cannot be empty."
}

$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw "OPENAI_API_KEY cannot be empty."
    }
    $env:OPENAI_API_KEY = $plainKey
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    Remove-Variable plainKey -ErrorAction SilentlyContinue
}

$env:OPENAI_REALTIME_ENABLED = "true"
$env:OPENAI_REALTIME_MODEL = $Model
$env:OPENAI_REALTIME_CONNECT_TIMEOUT_SECONDS = "30"

Write-Host "Starting Smart Office Backend with Realtime voice and Office COM..." -ForegroundColor Cyan
Write-Host "Repository root: $repoRoot"
Write-Host "Conda environment: $CondaEnvName"
Write-Host "Python: $resolvedPython"
Write-Host "Model: $Model"
Write-Host "OPENAI_API_KEY: configured (value hidden)"
Write-Host "Configured PPT: $env:SMART_OFFICE_DEMO_PPT"
Write-Host "Output directory: $env:SMART_OFFICE_OUTPUT_DIR"
Write-Host "Presentation monitor: $env:SMART_OFFICE_PRESENTATION_MONITOR_DEVICE"
Write-Host "Outlook sender: $env:SMART_OFFICE_OUTLOOK_SENDER_EMAIL"
Write-Host "Recipient file: $env:SMART_OFFICE_EMAIL_RECIPIENTS_FILE"
if ($recipientInfo) {
    Write-Host "Outlook recipient directory: available" -ForegroundColor Green
    Write-Host "Default recipient key: $($recipientInfo.default_key)"
    Write-Host "Configured Outlook recipients:"
    foreach ($recipient in $recipientInfo.recipients) {
        Write-Host "  - $($recipient.name) [$($recipient.key)] <$($recipient.email)>"
    }
}
else {
    Write-Host "Outlook recipient directory: unavailable (non-blocking)" -ForegroundColor Yellow
}
Write-Host "Recipient file reload: enabled before status, draft, and send actions"
Write-Host "Backend: http://${HostAddress}:$Port"
Write-Host "Realtime status: http://${HostAddress}:$Port/api/realtime/status"
Write-Host "Presentation status: http://${HostAddress}:$Port/api/presentation/status"
Write-Host "Uvicorn reload: disabled for stable Office COM activation"

Push-Location $backendDirectory
try {
    & $resolvedPython -m uvicorn app.main:app --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
