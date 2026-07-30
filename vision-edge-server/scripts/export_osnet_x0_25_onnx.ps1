param(
    [string]$EnvironmentName = "visionedge-osnet-export",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
$OutputPath = Join-Path $ServerRoot "models\osnet_x0_25.onnx"

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found in PATH. Run this script from Anaconda PowerShell Prompt."
}

if ($Recreate) {
    conda env remove -n $EnvironmentName -y
}

$envList = conda env list --json | ConvertFrom-Json
$exists = $false
foreach ($path in $envList.envs) {
    if ((Split-Path $path -Leaf) -eq $EnvironmentName) {
        $exists = $true
        break
    }
}
if (-not $exists) {
    conda create -n $EnvironmentName python=3.11 -y
}

Write-Host "Installing exporter-only dependencies in Conda environment: $EnvironmentName"
conda run -n $EnvironmentName python -m pip install --upgrade pip
conda run -n $EnvironmentName python -m pip install `
    torch torchvision `
    --index-url https://download.pytorch.org/whl/cpu
conda run -n $EnvironmentName python -m pip install onnx

Push-Location $ServerRoot
try {
    conda run -n $EnvironmentName python .\scripts\export_osnet_x0_25_onnx.py `
        --output $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "OSNet ONNX export failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Get-Item $OutputPath | Format-List FullName, Length, LastWriteTime
Write-Host "The temporary exporter environment is separate from smartoffice."
