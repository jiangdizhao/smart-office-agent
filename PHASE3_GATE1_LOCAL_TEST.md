# Phase 3 Gate 1 — PowerPoint COM Local Acceptance

Gate 1 validates deterministic control of the configured Microsoft 365 PowerPoint presentation. It does not yet connect GPT Realtime function calls or the unified `/agent/turn` router to real Office execution; that belongs to Gate 2.

## Frozen local configuration

- Presentation: `demo_files/Loss.pptx`
- Output directory: `demo_files/LOG`
- PowerPoint: Microsoft 365 desktop, COM ProgID `PowerPoint.Application`
- Target display record: `\\.\DISPLAY2`
- Default recipient: `Rico <jiangdizhao@gmail.com>`
- Email sending: disabled

All values may be overridden with environment variables:

```text
SMART_OFFICE_DEMO_PPT
SMART_OFFICE_OUTPUT_DIR
SMART_OFFICE_PRESENTATION_MONITOR_DEVICE
SMART_OFFICE_PRESENTATION_MONITOR_NUMBER
SMART_OFFICE_DEMO_RECIPIENT_NAME
SMART_OFFICE_DEMO_RECIPIENT_EMAIL
```

## Gate 1 API

```text
GET  /api/presentation/status
POST /api/presentation/open
POST /api/presentation/slideshow/start
POST /api/presentation/slideshow/next
POST /api/presentation/slideshow/previous
POST /api/presentation/slideshow/goto
POST /api/presentation/slideshow/end
POST /api/presentation/close
```

`POST /api/presentation/close` requires:

```json
{"confirmed": true}
```

The configured presentation is opened read-only. The controller refuses to close a presentation with unsaved changes.

## Start the backend

From the repository root:

```powershell
cd F:\smart-office-agent
powershell -ExecutionPolicy Bypass -File .\backend\scripts\start_backend_realtime.ps1
```

Check the status endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/presentation/status |
  ConvertTo-Json -Depth 10
```

Expected configuration:

```text
presentation_exists = true
target_monitor_device = \\.\DISPLAY2
recipient_name = Rico
recipient_email = jiangdizhao@gmail.com
email_send_enabled = false
```

## Run the complete Gate 1 live test

Open a second PowerShell window:

```powershell
cd F:\smart-office-agent
powershell -ExecutionPolicy Bypass `
  -File .\backend\scripts\verify_phase3_gate1_powerpoint.ps1
```

The script performs:

1. Inspect configured presentation state.
2. Open `Loss.pptx` through PowerPoint COM.
3. Start slide-show mode.
4. Move to the next slide when possible.
5. Move back to the previous slide.
6. Go to slide 3, or the last available slide when fewer than three exist.
7. End slide-show mode.
8. Verify the observed PowerPoint state after every command.

The presentation remains open after the default test. To close it at the end:

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\backend\scripts\verify_phase3_gate1_powerpoint.ps1 `
  -CloseAtEnd
```

Expected terminal result:

```text
PASS: Gate 1 opened Loss.pptx, controlled the slide show through COM, and verified observed PowerPoint state.
```

## Acceptance criteria

- `Loss.pptx` opens in Microsoft 365 PowerPoint.
- Slide-show mode starts.
- Next and Previous change the observed slide number.
- GoTo reaches the requested valid slide.
- End exits slide-show mode without closing the presentation.
- Every action returns both `ToolResult` and `VerificationResult`.
- No unrestricted shell, arbitrary file path, arbitrary COM method, or email-send capability is exposed.
- Existing Phase 1 voice and Phase 2 router contracts remain green.

## Known Gate 1 boundary

Gate 1 records `\\.\DISPLAY2` as the target display but does not yet force the slide-show window onto that monitor. Display workspace placement and the GPT Realtime-to-Task Runtime execution path are Gate 2 work.
