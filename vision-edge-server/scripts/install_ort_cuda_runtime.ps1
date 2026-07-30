param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot

function Resolve-PythonExecutable {
    param([string]$RequestedPython)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
        $explicit = Get-Command $RequestedPython -ErrorAction Stop
        return $explicit.Source
    }

    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path $condaPython) {
            return (Resolve-Path $condaPython).Path
        }
    }

    $pythonCommand = Get-Command python -ErrorAction Stop
    return $pythonCommand.Source
}

$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$ActualPython = (& $ResolvedPython -c "import sys; print(sys.executable)").Trim()

Write-Host "Install ONNX Runtime CUDA 13 runtime dependencies"
Write-Host "Conda environment: $env:CONDA_DEFAULT_ENV"
Write-Host "Python: $ActualPython"
Write-Host "This installs NVIDIA CUDA/cuDNN runtime wheels into the selected Python environment."

Push-Location $ServerRoot
try {
    & $ResolvedPython -m pip install --upgrade `
        "numpy==1.26.4" `
        "protobuf==4.25.9" `
        "onnxruntime-gpu[cuda,cudnn]==1.28.0"
    if ($LASTEXITCODE -ne 0) {
        throw "CUDA runtime dependency installation failed with exit code $LASTEXITCODE"
    }

    & $ResolvedPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "pip check reported dependency conflicts"
    }

    & $ResolvedPython .\scripts\verify_ort_cuda.py --model .\models\yolox_nano.onnx
    if ($LASTEXITCODE -ne 0) {
        throw "ONNX Runtime CUDA session verification failed"
    }
}
finally {
    Pop-Location
}
