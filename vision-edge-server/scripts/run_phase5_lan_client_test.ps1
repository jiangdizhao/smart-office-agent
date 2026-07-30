param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [string]$PythonExe = "",

    [double]$TimeoutSeconds = 10
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $PythonExe) {
    if ($env:CONDA_PREFIX -and (Test-Path (Join-Path $env:CONDA_PREFIX "python.exe"))) {
        $PythonExe = Join-Path $env:CONDA_PREFIX "python.exe"
    }
    else {
        $PythonExe = "python"
    }
}

& $PythonExe ".\scripts\phase5_lan_client_test.py" `
    --server $Server `
    --timeout-seconds $TimeoutSeconds

exit $LASTEXITCODE
