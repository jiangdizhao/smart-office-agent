param(
    [Parameter(Mandatory = $false)]
    [string]$SourceDirectory = ".",

    [Parameter(Mandatory = $false)]
    [string]$Idle1 = "",

    [Parameter(Mandatory = $false)]
    [string]$Idle2 = "",

    [Parameter(Mandatory = $false)]
    [string]$Idle3 = "",

    [Parameter(Mandatory = $false)]
    [string]$Intro = "",

    [Parameter(Mandatory = $false)]
    [string]$Talk1 = "",

    [Parameter(Mandatory = $false)]
    [string]$Talk2 = "",

    [Parameter(Mandatory = $false)]
    [string]$Talk3 = "",

    [Parameter(Mandatory = $false)]
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

function Resolve-InputFile {
    param(
        [string]$ExplicitPath,
        [string]$SourceRoot,
        [string[]]$Aliases,
        [string]$Label
    )

    if ($ExplicitPath) {
        $resolved = Resolve-Path -LiteralPath $ExplicitPath -ErrorAction SilentlyContinue
        if (-not $resolved) { throw "$Label source file was not found: $ExplicitPath" }
        return $resolved.Path
    }

    foreach ($alias in $Aliases) {
        $candidate = Join-Path $SourceRoot $alias
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "$Label source file was not found. Pass its full path explicitly."
}

$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if (-not $ffmpeg) { $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue }
if (-not $ffmpeg) {
    throw "ffmpeg was not found in PATH. Install ffmpeg before running this script."
}

$sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $PSScriptRoot "..\public\virtual-host-video"
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path

$assets = @(
    @{ Label = "Idle 1"; Input = (Resolve-InputFile $Idle1 $sourceRoot @("idle-1.mov", "idle-1.mp4") "Idle 1"); Output = "idle-1.mp4" },
    @{ Label = "Idle 2"; Input = (Resolve-InputFile $Idle2 $sourceRoot @("idle-2.mov", "idle-2.mp4") "Idle 2"); Output = "idle-2.mp4" },
    @{ Label = "Idle 3"; Input = (Resolve-InputFile $Idle3 $sourceRoot @("idle-3.mov", "idle-3.mp4") "Idle 3"); Output = "idle-3.mp4" },
    @{ Label = "Introduction"; Input = (Resolve-InputFile $Intro $sourceRoot @("intro.mov", "intro.mp4") "Introduction"); Output = "intro.mp4" },
    @{ Label = "Talk 1"; Input = (Resolve-InputFile $Talk1 $sourceRoot @("talk-1.mov", "talk-1.mp4") "Talk 1"); Output = "talk-1.mp4" },
    @{ Label = "Talk 2"; Input = (Resolve-InputFile $Talk2 $sourceRoot @("talk-2.mov", "talk-2.mp4") "Talk 2"); Output = "talk-2.mp4" },
    @{ Label = "Talk 3"; Input = (Resolve-InputFile $Talk3 $sourceRoot @("talk-3.mov", "talk-3.mp4") "Talk 3"); Output = "talk-3.mp4" }
)

foreach ($asset in $assets) {
    $target = Join-Path $outputRoot $asset.Output
    Write-Host ("Converting {0}: {1}" -f $asset.Label, $asset.Input)
    & $ffmpeg.Source -y -hide_banner -loglevel error `
        -i $asset.Input `
        -an `
        -vf "scale=828:1108:flags=lanczos,fps=30" `
        -c:v libx264 `
        -preset medium `
        -crf 18 `
        -pix_fmt yuv420p `
        -movflags +faststart `
        -profile:v high `
        -level 4.1 `
        $target
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed while converting $($asset.Label)." }
}

Write-Host ""
Write-Host "Virtual-host video assets installed successfully:"
Get-ChildItem -LiteralPath $outputRoot -Filter "*.mp4" |
    Sort-Object Name |
    Select-Object Name, Length |
    Format-Table -AutoSize
