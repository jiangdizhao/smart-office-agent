param(
  [Parameter(Mandatory = $true)]
  [string]$AssetFolder
)

$ErrorActionPreference = 'Stop'
$assetFolderPath = (Resolve-Path $AssetFolder).Path
$frontendRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$target = Join-Path $frontendRoot 'public\virtual-host-video'
$required = @(
  'intro.mp4',
  'idle-primary.mp4',
  'idle-rare.mp4',
  'talk-a.mp4',
  'talk-b.mp4',
  'talk-c.mp4'
)

New-Item -ItemType Directory -Force -Path $target | Out-Null
foreach ($name in $required) {
  $source = Join-Path $assetFolderPath $name
  if (-not (Test-Path $source)) {
    throw "Missing required virtual-host video asset: $source"
  }
  Copy-Item -Force $source (Join-Path $target $name)
}

Write-Host "Installed virtual-host video assets to: $target"
Get-ChildItem $target -Filter '*.mp4' | Select-Object Name, Length
