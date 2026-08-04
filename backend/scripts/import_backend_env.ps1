param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$CreateFromTemplate,
    [string]$TemplatePath
)

$ErrorActionPreference = "Stop"

function Unquote-EnvValue {
    param([string]$Value)

    $trimmed = $Value.Trim()
    if ($trimmed.Length -ge 2) {
        $first = $trimmed[0]
        $last = $trimmed[$trimmed.Length - 1]
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $trimmed = $trimmed.Substring(1, $trimmed.Length - 2)
        }
    }
    return $trimmed
}

$resolvedPath = [System.IO.Path]::GetFullPath($Path)
if (-not (Test-Path -LiteralPath $resolvedPath -PathType Leaf)) {
    if (-not $CreateFromTemplate) {
        throw "Backend environment file was not found: $resolvedPath"
    }
    if ([string]::IsNullOrWhiteSpace($TemplatePath)) {
        throw "TemplatePath is required when CreateFromTemplate is enabled."
    }
    $resolvedTemplate = [System.IO.Path]::GetFullPath($TemplatePath)
    if (-not (Test-Path -LiteralPath $resolvedTemplate -PathType Leaf)) {
        throw "Backend environment template was not found: $resolvedTemplate"
    }
    $parent = Split-Path -Parent $resolvedPath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $resolvedTemplate -Destination $resolvedPath
    Write-Host "Created Backend local environment file: $resolvedPath" -ForegroundColor Yellow
}

$loaded = New-Object System.Collections.Generic.List[string]
$lineNumber = 0
foreach ($rawLine in Get-Content -LiteralPath $resolvedPath -Encoding UTF8) {
    $lineNumber += 1
    $line = [string]$rawLine
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) {
        continue
    }
    if ($trimmed.StartsWith('export ')) {
        $trimmed = $trimmed.Substring(7).TrimStart()
    }
    $separator = $trimmed.IndexOf('=')
    if ($separator -lt 1) {
        throw "Invalid Backend environment entry at ${resolvedPath}:$lineNumber. Expected KEY=VALUE."
    }
    $name = $trimmed.Substring(0, $separator).Trim()
    if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "Invalid environment variable name '$name' at ${resolvedPath}:$lineNumber."
    }
    $value = Unquote-EnvValue -Value $trimmed.Substring($separator + 1)
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    $loaded.Add($name)
}

[PSCustomObject]@{
    Path = $resolvedPath
    LoadedVariables = @($loaded)
}
