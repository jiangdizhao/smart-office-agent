param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [string]$PythonExe = "",

    [double]$TimeoutSeconds = 10
)

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $PSScriptRoot
Set-Location $BackendRoot

if (-not $PythonExe) {
    if ($env:CONDA_PREFIX -and (Test-Path (Join-Path $env:CONDA_PREFIX "python.exe"))) {
        $PythonExe = Join-Path $env:CONDA_PREFIX "python.exe"
    }
    else {
        $PythonExe = "python"
    }
}

& $PythonExe ".\scripts\test_phase5_remote_vision_lan.py" `
    --server $Server `
    --timeout-seconds $TimeoutSeconds

exit $LASTEXITCODE
