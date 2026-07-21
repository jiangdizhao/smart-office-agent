param(
    [string]$PythonExe = "",
    [string]$Model = "gpt-realtime-2.1",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

function Resolve-SmartOfficePython {
    param([string]$ExplicitPython)

    $candidates = New-Object System.Collections.Generic.List[string]
    if ($ExplicitPython) {
        $candidates.Add($ExplicitPython)
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

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "Could not locate smartoffice\python.exe. Activate the smartoffice conda environment or pass -PythonExe explicitly."
}

$resolvedPython = Resolve-SmartOfficePython -ExplicitPython $PythonExe
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
