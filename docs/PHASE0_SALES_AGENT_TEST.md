# Phase 0 Sales Agent Foundation — Local Test Guide

## Purpose

Phase 0 installs the approved sales persona, capability catalog, playbooks, claims,
Visit-scoped state contracts, hard invitation limits, humour/proactivity budgets,
telemetry structure and read-only inspection APIs.

It does **not** activate sales dialogue, proactive speech, humour generation, profile
extraction or appointment-first routing. Existing exhibition behaviour must remain
unchanged while the feature flags are disabled.

## Default feature flags

Do not enable the sales runtime during Phase 0 acceptance.

```powershell
$env:SMART_OFFICE_SALES_AGENT_ENABLED = "false"
$env:SMART_OFFICE_SALES_PROACTIVE_ENABLED = "false"
$env:SMART_OFFICE_SALES_HUMOUR_ENABLED = "false"
$env:SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED = "false"
$env:SMART_OFFICE_SALES_TELEMETRY_ENABLED = "true"
$env:SMART_OFFICE_REALTIME_MODE = "quality"
```

The quality baseline remains `gpt-realtime-2.1`. A Mini model is not selected or
assumed available during Phase 0.

## 1. Update and confirm the branch

```powershell
cd D:\smart-office-agent
git switch integration/virtual-host
git pull --ff-only origin integration/virtual-host
git status --short
git rev-parse HEAD
```

`git status --short` should be empty. Preserve local configuration files before
resolving any unrelated local changes.

## 2. Review the approved configuration

```powershell
Get-Content .\config\sales_persona.json -Encoding UTF8
Get-Content .\config\capability_catalog.json -Encoding UTF8
Get-Content .\config\sales_playbooks.json -Encoding UTF8
Get-Content .\config\sales_claims.json -Encoding UTF8
```

Check the following decisions:

- Sara is identified as the Smart Office Digital Manager and Enterprise Solution
  Consultant.
- Booking invitations are limited to two per Visit.
- Contact invitations are limited to one per Visit.
- Industry, role and office pain points are approved discovery fields.
- Explicitly declined fields cannot be asked again.
- Teams is `appointment_demo`.
- PowerPoint, Outlook, meeting summaries, activity management, office automation and
  product planning are `appointment_demo`.
- Meeting booking and visitor registration remain immediate conversion actions.
- The three-coffee analogy is allowed only after a customer asks about cost, and its
  required scope excludes hardware, deployment, integration and custom services.
- Humour is limited to one line, normally separated by at least four effective turns,
  and forbidden in privacy, failure, approval and high-risk contexts.

## 3. Run the dedicated Phase 0 contract

Use the Python interpreter from the Smart Office environment:

```powershell
cd D:\smart-office-agent
D:\anaconda3\envs\sm\python.exe `
  .\backend\scripts\smoke_sales_phase0_contract.py
```

Expected final output begins with:

```text
PASS: Phase 0 sales configuration, Visit isolation, invitation limits,
```

The test verifies:

- all four configuration files load and validate;
- default feature flags keep the sales runtime disabled;
- the same `conversation_id` with different `visit_id` values does not share profile
  facts;
- a booking cannot be offered more than twice;
- contact details cannot be requested more than once;
- a declined discovery field is not reintroduced;
- the same humour theme cannot be used twice in one Visit;
- proactive nudges cannot exceed two;
- the first Teams demonstration request recommends booking;
- the second explicit Teams request permits only the allowlisted `teams_open_only`
  fallback;
- exact operational replies require a verified Backend fact;
- telemetry removes transcript and contact fields;
- Phase 0 APIs are available while the current conversation runtime remains unchanged.

## 4. Start the existing Backend

```powershell
cd D:\smart-office-agent
powershell -ExecutionPolicy Bypass `
  -File .\backend\scripts\start_backend_realtime.ps1 `
  -CondaEnvName sm `
  -Model gpt-realtime-2.1 `
  -HostAddress 0.0.0.0 `
  -Port 8000
```

## 5. Inspect Phase 0 status

Open a second PowerShell window:

```powershell
$status = Invoke-RestMethod http://127.0.0.1:8000/api/sales/status
$status | ConvertTo-Json -Depth 20
```

Expected values:

```text
phase                                      phase0_sales_foundation
runtime_active                             False
current_conversation_behaviour_changed     False
configuration.ok                           True
policy.effective_flags.agent_enabled       False
policy.effective_flags.proactive_enabled   False
policy.effective_flags.humour_enabled      False
```

The `phase1_not_yet_active` list must still include sales reply generation, proactive
sales scheduling, humour rendering and profile persistence.

## 6. Inspect the capability catalog

```powershell
$catalog = Invoke-RestMethod `
  "http://127.0.0.1:8000/api/sales/capabilities?language=zh"

$catalog.capabilities |
  Select-Object capability_id, title, status, recommended_next_action |
  Format-Table -AutoSize
```

Expected examples:

```text
teams_collaboration        appointment_demo
presentation_automation    appointment_demo
outlook_workflow           appointment_demo
meeting_summary            appointment_demo
meeting_booking            live_demo
visitor_registration       live_demo
```

`live_demo` for booking and registration means that their UI may open immediately as
conversion actions. It does not override the appointment-first policy for product
capability demonstrations.

## 7. Inspect first and repeated Teams requests

First explicit request:

```powershell
Invoke-RestMethod `
  "http://127.0.0.1:8000/api/sales/demonstration-policy/teams_collaboration?language=zh&explicit_request_count=1" |
  ConvertTo-Json -Depth 10
```

Expected:

```text
action          offer_booking
onsite_allowed  False
```

Second explicit request:

```powershell
Invoke-RestMethod `
  "http://127.0.0.1:8000/api/sales/demonstration-policy/teams_collaboration?language=zh&explicit_request_count=2" |
  ConvertTo-Json -Depth 10
```

Expected:

```text
action          teams_open_only
onsite_allowed  True
```

This is only a Phase 0 policy inspection. The current voice router does not apply the
policy until Phase 1.

## 8. Confirm the current runtime is unchanged

Start the frontend normally and perform a short regression:

```powershell
cd D:\smart-office-agent\ui\smart-office-ui
npm run build
npm run dev -- --host 0.0.0.0
```

Test the currently working functions:

1. Say “预约会议” and confirm the booking panel still opens.
2. Say “打开登记信息表” and confirm the registration form opens, not the result center.
3. Say “打开 PowerPoint” and confirm current behaviour is unchanged.
4. Test interruption while Sara is speaking.
5. Let a Visit end and confirm the next visitor receives a separate Session.
6. Confirm Sara does not yet use the new sales identity, proactive questions or humour.

The sixth item is required in Phase 0: the sales runtime is intentionally inactive.

## 9. Optional flag-suppression check

Set a child feature without enabling the main agent:

```powershell
$env:SMART_OFFICE_SALES_AGENT_ENABLED = "false"
$env:SMART_OFFICE_SALES_HUMOUR_ENABLED = "true"
```

Restart the Backend and inspect `/api/sales/status`.

Expected:

```text
requested_flags.humour_enabled            True
effective_flags.humour_enabled            False
suppressed_without_agent                  humour_enabled
```

This prevents partial sales behaviour from being activated accidentally.

## Feedback requested after testing

Report:

1. Whether the dedicated contract prints PASS.
2. Whether `/api/sales/status` reports `configuration.ok=true`.
3. Whether the capability statuses match the approved strategy.
4. Any wording in the four configuration files that should be revised before Phase 1.
5. Whether every existing voice, booking, registration and Office function behaves the
   same as before Phase 0.
6. The Backend terminal output if startup or configuration validation fails.
