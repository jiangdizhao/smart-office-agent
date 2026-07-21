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

    # First ask Conda for every registered environment. This works even when the
    # caller is in base, in another environment, or in a child PowerShell.
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
        # Continue with deterministic filesystem candidates.
    }

    # Resolve the conventional named environment beneath the active Conda base.
    try {
        $condaBase = (& conda info --base 2>$null | Select-Object -Last 1).Trim()
        if ($condaBase) {
            Add-Candidate -Candidates $candidates -Candidate (Join-Path $condaBase "envs\$EnvironmentName\python.exe")
        }
    }
    catch {
        # Continue with the remaining candidates.
    }

    # The active interpreter is accepted only when it is actually the requested
    # named environment. A base Python can never pass Test-ProjectPython.
    try {
        $activePython = (Get-Command python -ErrorAction Stop).Source
        Add-Candidate -Candidates $candidates -Candidate $activePython
    }
    catch {
        # Python does not need to be on PATH when Conda discovery succeeds.
    }

    if ($env:CONDA_PREFIX -and (Split-Path -Leaf $env:CONDA_PREFIX) -ieq $EnvironmentName) {
        Add-Candidate -Candidates $candidates -Candidate (Join-Path $env:CONDA_PREFIX "python.exe")
    }

    # Known Windows Conda layouts are last-resort automatic candidates.
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

$resolvedPython = Resolve-CondaEnvironmentPython -EnvironmentName $CondaEnvName

$dependencyProbe = & $resolvedPython -c "import fastapi, sse_starlette, uvicorn; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "The automatically selected '$CondaEnvName' Python is missing backend dependencies.`nRun once:`n  conda run -n $CondaEnvName python -m pip install -r requirements-smartoffice.txt`nDetails: $dependencyProbe"
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
Write-Host "Conda environment: $CondaEnvName"
Write-Host "Python: $resolvedPython"
Write-Host "Model: $Model"
Write-Host "OPENAI_API_KEY: configured (value hidden)"
Write-Host "Backend: http://${HostAddress}:$Port"
Write-Host "Realtime status: http://${HostAddress}:$Port/api/realtime/status"

# Calling the selected interpreter directly guarantees that Uvicorn and its
# reload child process use the same Conda environment.
& $resolvedPython -m uvicorn app.main:app --reload --host $HostAddress --port $Port
