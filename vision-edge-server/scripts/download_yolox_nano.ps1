param(
    [string]$OutputPath = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $ServerRoot "models\yolox_nano.onnx"
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$OutputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

if ((Test-Path $OutputPath) -and -not $Force) {
    Write-Host "Model already exists: $OutputPath"
    Write-Host "Use -Force to download it again."
    exit 0
}

$Url = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx"
$TemporaryPath = "$OutputPath.download"
Remove-Item $TemporaryPath -Force -ErrorAction SilentlyContinue

Write-Host "Downloading official YOLOX-Nano ONNX model"
Write-Host "Source: $Url"
Write-Host "Destination: $OutputPath"
Invoke-WebRequest -Uri $Url -OutFile $TemporaryPath -UseBasicParsing

$Size = (Get-Item $TemporaryPath).Length
if ($Size -lt 1000000) {
    Remove-Item $TemporaryPath -Force -ErrorAction SilentlyContinue
    throw "Downloaded file is unexpectedly small ($Size bytes)."
}
Move-Item $TemporaryPath $OutputPath -Force
$Hash = (Get-FileHash $OutputPath -Algorithm SHA256).Hash
Write-Host "Downloaded $Size bytes"
Write-Host "SHA256: $Hash"
Write-Host "Model ready: $OutputPath"
