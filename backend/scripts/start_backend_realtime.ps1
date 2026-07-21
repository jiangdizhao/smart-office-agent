param(
    [string]$PythonExe = "",
    [string]$Model = "gpt-realtime-2.1",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

function Test-SmartOfficePython {
    param([string]$Candidate)

    if (-not $Candidate -or -not (Test-Path $Candidate)) {
        return $false
    }

    try {
        $probe = & $Candidate -c "import json, os, sys; print(json.dumps({'prefix': sys.prefix, 'version': list(sys.version_info[:3]), 'exe': sys.executable}))" 2>$null
        if (-not $probe) {
            return $false
        }

        $info = $probe | ConvertFrom-Json
        $envName = Split-Path -Leaf ([string]$info.prefix)
        $isSmartOffice = $envName -ieq "smartoffice"
        $isSupportedPython = ([int]$info.version[0] -eq 3) -and ([int]$info.version[1] -ge 11)
        return $isSmartOffice -and $isSupportedPython
    }
    catch {
        return $false
    }
}

function Resolve-SmartOfficePython {
    param([string]$ExplicitPython)

    $candidates = New-Object System.Collections.Generic.List[string]

    if ($ExplicitPython) {
        $candidates.Add($ExplicitPython)
    }

    try {
        $activePython = (Get-Command python -ErrorAction Stop).Source
        if ($activePython) {
            $candidates.Add($activePython)
        }
    }
    catch {
        # Continue to Conda-specific candidates.
    }

    if ($env:CONDA_PREFIX) {
        $candidates.Add((Join-Path $env:CONDA_PREFIX "python.exe"))
    }

    try {
        $condaBase = (& conda info --base 2>$null).Trim()
        if ($condaBase) {
            $candidates.Add((Join-Path $condaBase "envs\smartoffice\python.exe"))
        }
    }
    catch {
        # Continue to known local candidates.
    }

    $candidates.Add("D:\anaconda3\envs\smartoffice\python.exe")
    $candidates.Add("C:\Users\$env:USERNAME\anaconda3\envs\smartoffice\python.exe")
    $candidates.Add("C:\Users\$env:USERNAME\miniconda3\envs\smartoffice\python.exe")

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-SmartOfficePython -Candidate $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "Could not locate a Python 3.11+ interpreter in the smartoffice Conda environment. Activate smartoffice or pass -PythonExe explicitly."
}

$resolvedPython = Resolve-SmartOfficePython -ExplicitPython $PythonExe

$dependencyProbe = & $resolvedPython -c "import fastapi, sse_starlette, uvicorn; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "The selected smartoffice Python is missing backend dependencies. Run: `"$resolvedPython`" -m pip install -r requirements-smartoffice.txt`nDetails: $dependencyProbe"
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

Write-Host "Starting Smart Office Backend with Realtime voice..." -ForegroundColor Cyan
Write-Host "Python: $resolvedPython"
Write-Host "Model: $Model"
Write-Host "OPENAI_API_KEY: configured (value hidden)"
Write-Host "Backend: http://${HostAddress}:$Port"
Write-Host "Realtime status: http://${HostAddress}:$Port/api/realtime/status"

& $resolvedPython -m uvicorn app.main:app --reload --host $HostAddress --port $Port
