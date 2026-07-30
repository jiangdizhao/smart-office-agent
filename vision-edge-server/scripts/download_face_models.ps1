param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
$ModelsDir = Join-Path $ServerRoot "models"
New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null

$Models = @(
    @{
        Name = "YuNet face detector"
        Url = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
        Path = Join-Path $ModelsDir "face_detection_yunet_2023mar.onnx"
        MinimumBytes = 150000
    },
    @{
        Name = "SFace face recognizer"
        Url = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
        Path = Join-Path $ModelsDir "face_recognition_sface_2021dec.onnx"
        MinimumBytes = 10000000
    }
)

foreach ($Model in $Models) {
    if ((Test-Path $Model.Path) -and -not $Force) {
        $Existing = Get-Item $Model.Path
        if ($Existing.Length -ge $Model.MinimumBytes) {
            Write-Host "$($Model.Name) already exists: $($Existing.FullName)"
            continue
        }
        Write-Warning "$($Model.Name) is smaller than expected and will be downloaded again."
    }

    $Temporary = "$($Model.Path).download"
    Remove-Item $Temporary -Force -ErrorAction SilentlyContinue
    Write-Host "Downloading $($Model.Name)..."
    Invoke-WebRequest -Uri $Model.Url -OutFile $Temporary -UseBasicParsing
    $Downloaded = Get-Item $Temporary
    if ($Downloaded.Length -lt $Model.MinimumBytes) {
        Remove-Item $Temporary -Force -ErrorAction SilentlyContinue
        throw "$($Model.Name) download is unexpectedly small: $($Downloaded.Length) bytes"
    }
    Move-Item $Temporary $Model.Path -Force
}

Write-Host "Face models ready"
foreach ($Model in $Models) {
    $Item = Get-Item $Model.Path
    $Hash = Get-FileHash $Model.Path -Algorithm SHA256
    Write-Host "$($Model.Name): $($Item.FullName)"
    Write-Host "  Bytes: $($Item.Length)"
    Write-Host "  SHA256: $($Hash.Hash)"
}
