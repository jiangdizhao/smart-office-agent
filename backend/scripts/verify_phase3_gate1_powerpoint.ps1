param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [switch]$CloseAtEnd
)

$ErrorActionPreference = "Stop"

function Invoke-Gate1Action {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [object]$Body = $null
    )

    $uri = "$BaseUrl$Path"
    if ($null -eq $Body) {
        $response = Invoke-RestMethod -Method Post -Uri $uri
    }
    else {
        $json = $Body | ConvertTo-Json -Depth 10
        $response = Invoke-RestMethod -Method Post -Uri $uri -ContentType "application/json; charset=utf-8" -Body $json
    }

    Write-Host "`n=== $Path ===" -ForegroundColor Cyan
    $response | ConvertTo-Json -Depth 12

    if (-not $response.tool_result.ok) {
        throw "Tool execution failed for $Path`: $($response.tool_result.message)"
    }
    if (-not $response.verification_result.ok) {
        throw "Verification failed for $Path`: $($response.verification_result.message)"
    }
    return $response
}

Write-Host "=== Gate 1 status ===" -ForegroundColor Cyan
$status = Invoke-RestMethod -Uri "$BaseUrl/api/presentation/status"
$status | ConvertTo-Json -Depth 10

if (-not $status.config.presentation_exists) {
    throw "Configured PPT was not found: $($status.config.presentation_path)"
}
if ($status.config.target_monitor_device -ne "\\.\DISPLAY2") {
    Write-Warning "Configured target monitor is $($status.config.target_monitor_device), expected \\.\DISPLAY2. Gate 1 records this target but does not enforce window placement yet."
}

$open = Invoke-Gate1Action -Path "/api/presentation/open"
$start = Invoke-Gate1Action -Path "/api/presentation/slideshow/start"

$currentStatus = Invoke-RestMethod -Uri "$BaseUrl/api/presentation/status"
$totalSlides = [int]$currentStatus.status.data.total_slides
$currentSlide = [int]$currentStatus.status.data.current_slide

if ($totalSlides -lt 1) {
    throw "PowerPoint reported no slides."
}

if ($totalSlides -ge 2 -and $currentSlide -lt $totalSlides) {
    $next = Invoke-Gate1Action -Path "/api/presentation/slideshow/next"
    $previous = Invoke-Gate1Action -Path "/api/presentation/slideshow/previous"
}
else {
    Write-Warning "Skipping Next/Previous because the presentation has only one usable position."
}

$targetSlide = [Math]::Min(3, $totalSlides)
$goto = Invoke-Gate1Action -Path "/api/presentation/slideshow/goto" -Body @{ slide_number = $targetSlide }
$end = Invoke-Gate1Action -Path "/api/presentation/slideshow/end"

if ($CloseAtEnd) {
    $close = Invoke-Gate1Action -Path "/api/presentation/close" -Body @{ confirmed = $true }
}

Write-Host "`nPASS: Gate 1 opened Loss.pptx, controlled the slide show through COM, and verified observed PowerPoint state." -ForegroundColor Green
