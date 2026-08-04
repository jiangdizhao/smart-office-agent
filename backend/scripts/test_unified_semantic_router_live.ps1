param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [switch]$SkipModelCases
)

$ErrorActionPreference = "Stop"
$base = $BaseUrl.TrimEnd("/")

function Invoke-JsonPost {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$Body
    )
    $json = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod `
        -Method Post `
        -Uri "$base$Path" `
        -ContentType "application/json; charset=utf-8" `
        -Body $json
}

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)]$Actual,
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if ($Actual -ne $Expected) {
        throw "$Message Expected='$Expected' Actual='$Actual'"
    }
}

function Assert-NotExecute {
    param(
        [Parameter(Mandatory = $true)]$Response,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if ($Response.final_policy_decision -eq "execute") {
        throw "$Message The router incorrectly allowed execution."
    }
}

Write-Host "=== Unified Semantic Router status ===" -ForegroundColor Cyan
$status = Invoke-RestMethod -Uri "$base/api/semantic-route/status"
$status | ConvertTo-Json -Depth 20
Assert-Equal $status.ok $true "Semantic router status is not healthy."
Assert-Equal $status.mode "unified" "Semantic router is not in unified mode."

Write-Host "`n=== Contracts ===" -ForegroundColor Cyan
$contracts = Invoke-RestMethod -Uri "$base/api/semantic-route/contracts"
$contracts | ConvertTo-Json -Depth 20
Assert-Equal $contracts.route_schema "semantic-route-v1" "Unexpected semantic route schema."

Write-Host "`n=== Deterministic self-test ===" -ForegroundColor Cyan
$selfTest = Invoke-JsonPost -Path "/api/semantic-route/self-test" -Body @{}
$selfTest | ConvertTo-Json -Depth 20
Assert-Equal $selfTest.ok $true "Deterministic semantic route self-test failed."

$exactCases = @(
    @{
        Name = "Canonical identity"
        Text = "你是谁"
        Intent = "self_introduction"
        Decision = "answer_only"
    },
    @{
        Name = "Exact Teams command"
        Text = "打开 Teams"
        Intent = "application_action"
        Decision = "execute"
    },
    @{
        Name = "Exact stop music"
        Text = "停止音乐"
        Intent = "system_action"
        Decision = "execute"
    },
    @{
        Name = "Bounded volume"
        Text = "音量设置为30%"
        Intent = "system_action"
        Decision = "execute"
    }
)

Write-Host "`n=== Exact fast-path cases (route preview only; no tool executes) ===" -ForegroundColor Cyan
foreach ($case in $exactCases) {
    $response = Invoke-JsonPost -Path "/api/semantic-route" -Body @{
        conversation_id = "semantic-live-fast"
        visit_id = $null
        language = "zh"
        actor_type = "visitor"
        text = $case.Text
        recent_turns = @()
        runtime_context = @{
            interaction_panel = $null
            active_tool = $null
            assistant_speaking = $false
            visitor_present = $false
        }
    }
    Write-Host "PASS candidate: $($case.Name)" -ForegroundColor DarkCyan
    $response | Select-Object decision_id, final_policy_decision, model, elapsed_ms | Format-List
    $response.route | Select-Object source, primary_intent, domain, confidence, risk, actions, negated_actions, reason_codes | Format-List
    Assert-Equal $response.route.primary_intent $case.Intent "$($case.Name): wrong intent."
    Assert-Equal $response.final_policy_decision $case.Decision "$($case.Name): wrong final decision."
}

if (-not $SkipModelCases) {
    $modelCases = @(
        @{
            Name = "Identity paraphrase"
            Text = "你在这个展台主要负责什么？"
            MustNotExecute = $true
        },
        @{
            Name = "Negated Teams explanation"
            Text = "先不要打开 Teams，介绍一下它能做什么。"
            MustNotExecute = $true
        },
        @{
            Name = "Teams troubleshooting question"
            Text = "Teams 为什么总是打不开？"
            MustNotExecute = $true
        },
        @{
            Name = "Hypothetical Teams action"
            Text = "如果打开 Teams，会发生什么？"
            MustNotExecute = $true
        },
        @{
            Name = "Booking discussion without action"
            Text = "我不是要预约，只是想了解会议预约功能。"
            MustNotExecute = $true
        },
        @{
            Name = "Open-domain sales profile"
            Text = "我在建筑行业做项目经理，最麻烦的是会后行动项整理。"
            MustNotExecute = $true
        }
    )

    Write-Host "`n=== Model semantic cases (route preview only; no tool executes) ===" -ForegroundColor Cyan
    foreach ($case in $modelCases) {
        $response = Invoke-JsonPost -Path "/api/semantic-route" -Body @{
            conversation_id = "semantic-live-model"
            visit_id = $null
            language = "zh"
            actor_type = "visitor"
            text = $case.Text
            recent_turns = @()
            runtime_context = @{
                interaction_panel = $null
                active_tool = $null
                assistant_speaking = $false
                visitor_present = $false
            }
        }
        Write-Host "Result: $($case.Name)" -ForegroundColor DarkCyan
        $response | Select-Object decision_id, final_policy_decision, model, elapsed_ms | Format-List
        $response.route | Select-Object source, primary_intent, domain, confidence, risk, actions, negated_actions, reason_codes, profile_extraction | Format-List
        if ($case.MustNotExecute) {
            Assert-NotExecute $response "$($case.Name):"
        }
    }
}

Write-Host "`n=== Recent decisions (raw text is not stored by default) ===" -ForegroundColor Cyan
$recent = Invoke-RestMethod -Uri "$base/api/semantic-route/recent-decisions?limit=20"
$recent | ConvertTo-Json -Depth 20

Write-Host "`nPASS: Unified semantic router live acceptance completed." -ForegroundColor Green
Write-Host "Note: This script previews routes only. Test actual Office and interaction execution through the Sara UI after this check passes." -ForegroundColor Yellow
