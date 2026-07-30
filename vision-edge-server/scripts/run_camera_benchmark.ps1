param(
    [ValidateSet("MSMF", "DSHOW", "ANY")]
    [string]$Backend = "MSMF",
    [string]$Fourcc = "AUTO",
    [double]$Fps = 30,
    [int]$DurationSeconds = 300,
    [int]$WarmupSeconds = 3,
    [int]$ReportIntervalSeconds = 10,
    [string]$PythonExe = "",
    [string]$ConfigPath = "",
    [string]$OutputPath = ""
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

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $ServerRoot "config\vision.yaml"
}
$ResolvedConfig = (Resolve-Path $ConfigPath).Path
$ResolvedPython = Resolve-PythonExecutable -RequestedPython $PythonExe
$ActualPython = (& $ResolvedPython -c "import sys; print(sys.executable)").Trim()

$Arguments = @(
    ".\scripts\camera_benchmark.py",
    "--config", $ResolvedConfig,
    "--backend", $Backend,
    "--fourcc", $Fourcc,
    "--fps", $Fps,
    "--duration-seconds", $DurationSeconds,
    "--warmup-seconds", $WarmupSeconds,
    "--report-interval-seconds", $ReportIntervalSeconds
)

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $Arguments += @("--output", $OutputPath)
}

Write-Host "Sustained camera benchmark"
Write-Host "Conda environment: $env:CONDA_DEFAULT_ENV"
Write-Host "Python: $ActualPython"
Write-Host "Config: $ResolvedConfig"
Write-Host "Mode: backend=$Backend, fourcc=$Fourcc, fps=$Fps"
Write-Host "Duration: $DurationSeconds seconds"

Push-Location $ServerRoot
try {
    & $ResolvedPython @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
