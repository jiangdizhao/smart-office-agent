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

function Get-EnvEntryKey {
    param([string]$RawLine)

    $trimmed = ([string]$RawLine).Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) {
        return $null
    }
    if ($trimmed.StartsWith('export ')) {
        $trimmed = $trimmed.Substring(7).TrimStart()
    }
    $separator = $trimmed.IndexOf('=')
    if ($separator -lt 1) {
        return $null
    }
    $name = $trimmed.Substring(0, $separator).Trim()
    if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        return $null
    }
    return $name
}

function Merge-MissingDefaults {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$SourceTemplate
    )

    if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) {
        return
    }
    if (-not (Test-Path -LiteralPath $SourceTemplate -PathType Leaf)) {
        return
    }

    $existingKeys = @{}
    foreach ($line in Get-Content -LiteralPath $TargetPath -Encoding UTF8) {
        $key = Get-EnvEntryKey -RawLine $line
        if ($null -ne $key) {
            $existingKeys[$key] = $true
        }
    }

    $missingEntries = New-Object System.Collections.Generic.List[string]
    foreach ($line in Get-Content -LiteralPath $SourceTemplate -Encoding UTF8) {
        $key = Get-EnvEntryKey -RawLine $line
        if ($null -eq $key -or $existingKeys.ContainsKey($key)) {
            continue
        }
        $missingEntries.Add(([string]$line).Trim())
        $existingKeys[$key] = $true
    }

    if ($missingEntries.Count -eq 0) {
        return
    }

    $newline = [Environment]::NewLine
    $appendText = $newline + '# Automatically added missing defaults from .env.local.example' + $newline
    $appendText += ($missingEntries -join $newline) + $newline
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText($TargetPath, $appendText, $utf8NoBom)
    Write-Host "Added missing Backend defaults: $($missingEntries -join ', ')" -ForegroundColor Yellow
}

function Update-DeprecatedDefaults {
    param([Parameter(Mandatory = $true)][string]$TargetPath)

    if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) {
        return
    }

    $lines = @(Get-Content -LiteralPath $TargetPath -Encoding UTF8)
    $changed = $false
    for ($index = 0; $index -lt $lines.Count; $index += 1) {
        $line = [string]$lines[$index]
        $key = Get-EnvEntryKey -RawLine $line
        if ($key -ne 'OPENAI_SEMANTIC_ROUTER_MODEL') {
            continue
        }
        $separator = $line.IndexOf('=')
        if ($separator -lt 1) {
            continue
        }
        $value = Unquote-EnvValue -Value $line.Substring($separator + 1)
        if ($value.Trim().ToLowerInvariant() -eq 'gpt-5.6-luna') {
            $lines[$index] = 'OPENAI_SEMANTIC_ROUTER_MODEL=gpt-5.6-terra'
            $changed = $true
        }
    }

    if (-not $changed) {
        return
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($TargetPath, $lines, $utf8NoBom)
    Write-Host 'Migrated OPENAI_SEMANTIC_ROUTER_MODEL from gpt-5.6-luna to gpt-5.6-terra.' -ForegroundColor Yellow
}

$resolvedPath = [System.IO.Path]::GetFullPath($Path)
$resolvedTemplate = $null
if (-not [string]::IsNullOrWhiteSpace($TemplatePath)) {
    $resolvedTemplate = [System.IO.Path]::GetFullPath($TemplatePath)
}

if (-not (Test-Path -LiteralPath $resolvedPath -PathType Leaf)) {
    if (-not $CreateFromTemplate) {
        throw "Backend environment file was not found: $resolvedPath"
    }
    if ([string]::IsNullOrWhiteSpace($resolvedTemplate)) {
        throw "TemplatePath is required when CreateFromTemplate is enabled."
    }
    if (-not (Test-Path -LiteralPath $resolvedTemplate -PathType Leaf)) {
        throw "Backend environment template was not found: $resolvedTemplate"
    }
    $parent = Split-Path -Parent $resolvedPath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $resolvedTemplate -Destination $resolvedPath
    Write-Host "Created Backend local environment file: $resolvedPath" -ForegroundColor Yellow
}
elseif ($CreateFromTemplate -and -not [string]::IsNullOrWhiteSpace($resolvedTemplate)) {
    Merge-MissingDefaults -TargetPath $resolvedPath -SourceTemplate $resolvedTemplate
}

Update-DeprecatedDefaults -TargetPath $resolvedPath

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
