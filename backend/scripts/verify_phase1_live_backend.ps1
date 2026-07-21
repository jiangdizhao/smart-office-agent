param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw "FAIL: $Message"
    }
}

Write-Host "Checking Smart Office Phase 1 backend at $BaseUrl ..." -ForegroundColor Cyan

$root = Invoke-RestMethod -Method Get -Uri "$BaseUrl/"
Assert-True ($root.status -eq "ok") "Backend root health check did not return status=ok."

$realtime = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/realtime/status"
Assert-True ([bool]$realtime.ok) "Realtime status did not return ok=true."
Assert-True ([bool]$realtime.configured) "Realtime is not configured."
Assert-True ([bool]$realtime.enabled) "Realtime is not enabled."
Assert-True ([bool]$realtime.api_key_present) "The backend process does not have OPENAI_API_KEY."
Assert-True ($realtime.transport -eq "webrtc") "Realtime transport is not WebRTC."
Assert-True ($realtime.turn_mode -eq "push_to_talk") "Realtime turn mode is not push_to_talk."
Assert-True ($realtime.phase -eq "m3a_fusion_phase_1") "Unexpected Realtime phase marker."

$turnStatus = Invoke-RestMethod -Method Get -Uri "$BaseUrl/agent/turn/status"
Assert-True ([bool]$turnStatus.ok) "Agent turn status did not return ok=true."
Assert-True ($turnStatus.phase -eq "m3a_fusion_phase_1") "Unexpected agent-turn phase marker."
Assert-True (-not [bool]$turnStatus.task_creation_enabled) "Phase 1 /agent/turn must not create executable tasks."

$turnBody = @{
    conversation_id = "phase1-live-check"
    text = "你好"
    language = "zh"
    input_source = "text"
    actor_context = @{
        type = "employee"
        source = "verify_phase1_live_backend.ps1"
    }
} | ConvertTo-Json -Depth 10

$turn = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/agent/turn" `
    -ContentType "application/json; charset=utf-8" `
    -Body $turnBody

Assert-True ($turn.route -eq "realtime_direct") "Greeting did not use realtime_direct."
Assert-True (-not [string]::IsNullOrWhiteSpace([string]$turn.spoken_text)) "Agent turn returned empty spoken_text."
Assert-True ($null -eq $turn.task_id) "Phase 1 agent turn unexpectedly created a task."
Assert-True (-not [bool]$turn.approval_required) "Phase 1 agent turn unexpectedly requested approval."

Write-Host "Realtime status:" -ForegroundColor DarkCyan
$realtime | ConvertTo-Json -Depth 10
Write-Host "Agent turn sample:" -ForegroundColor DarkCyan
$turn | ConvertTo-Json -Depth 10
Write-Host "PASS: Phase 1 live backend status and text-turn contract are healthy." -ForegroundColor Green
