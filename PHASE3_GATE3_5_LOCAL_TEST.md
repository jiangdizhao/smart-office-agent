# Phase 3 Gate 3–5 Local Windows Acceptance

This acceptance batch covers bounded Windows volume and brightness control, local presentation-summary artifacts, Classic Outlook draft creation, a local editable recipient directory, and second-approval email sending.

## Preconditions

- Branch: `integration/virtual-host`
- Actor: `employee` or `operator`, except for the visitor-denial test
- `demo_files/Loss.pptx` exists
- Classic Microsoft Outlook for Windows is installed and logged in
- The configured Outlook sender account is available in the current Outlook profile
- Backend dependencies are updated
- The Smart Office UI remains on `DISPLAY1`; PowerPoint slide show remains on `DISPLAY2`

## Update and install

```powershell
cd F:\smart-office-agent
git fetch origin
git switch integration/virtual-host
git pull --ff-only origin integration/virtual-host
git rev-parse --short HEAD

conda activate smartoffice
pip install -r backend\requirements-smartoffice.txt
```

The local dependencies include `pycaw` for Windows Core Audio and `pywin32` for PowerPoint and Classic Outlook COM. Brightness uses Windows WMI and therefore only works when the display exposes `WmiMonitorBrightness`; many external monitors do not expose that interface.

## Recipient files

The repository contains a tracked template:

```text
F:\smart-office-agent\config\email_recipients.example.json
```

The Backend uses a separate local file:

```text
F:\smart-office-agent\config\email_recipients.json
```

`config/email_recipients.json` is ignored by Git so that real customer and colleague addresses are not accidentally committed. On the first normal Backend start, `start_backend_realtime.ps1` copies the tracked template to the local file when the local file does not yet exist.

The initial local file is:

```json
{
  "default_recipient_key": "rico",
  "recipients": {
    "rico": {
      "name": "Rico",
      "email": "jiangdizhao@gmail.com"
    }
  }
}
```

To add Tom, edit `config/email_recipients.json`:

```json
{
  "default_recipient_key": "rico",
  "recipients": {
    "rico": {
      "name": "Rico",
      "email": "jiangdizhao@gmail.com"
    },
    "tom": {
      "name": "Tom",
      "email": "tom@example.com"
    }
  }
}
```

Replace `tom@example.com` with Tom's actual valid address. To make Tom the default recipient, change:

```json
"default_recipient_key": "tom"
```

Recipient keys must contain only lowercase letters, digits, underscores, or hyphens. The Backend rejects malformed JSON, invalid addresses, duplicate addresses, unknown recipient keys, sender/recipient equality, raw model-supplied addresses, and draft/send steps that target different keys.

The file is hot-read. Saving a valid edit changes the next status, draft, or send action without restarting the Backend. The Backend does not retain a stale contact cache. If the file is temporarily invalid, status reports `recipient_config_error`, while draft and send actions fail explicitly.

An alternative local file can be selected before startup:

```powershell
$env:SMART_OFFICE_EMAIL_RECIPIENTS_FILE = "F:\somewhere\my_email_recipients.json"
```

This environment variable controls only the file path. Contact names and email addresses remain in the editable JSON file.

## Start

Backend:

```powershell
cd F:\smart-office-agent\backend
powershell -ExecutionPolicy Bypass -File .\scripts\start_backend_realtime.ps1
```

The startup output must show:

```text
Outlook sender: jiangdizhao1@outlook.com
Recipient file: F:\smart-office-agent\config\email_recipients.json
Default recipient key: rico
Configured Outlook recipients:
  - Rico [rico] <jiangdizhao@gmail.com>
Recipient file reload: enabled before status, draft, and send actions
```

A missing custom file or invalid file prevents startup. The default local file is created automatically from the template.

Frontend:

```powershell
cd F:\smart-office-agent\ui\smart-office-ui
npm run dev -- --host 127.0.0.1
```

Close old Smart Office browser tabs and open:

```text
http://127.0.0.1:5173/
```

Check Backend state:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/ | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8000/api/office/status | ConvertTo-Json -Depth 12
```

Expected capability indicators include:

```text
classic_outlook_draft_creation = true
outlook_send_second_approval_gate = true
approved_email_recipient_allowlist = true
fixed_email_recipient = false
arbitrary_email_recipient = false
email_send_enabled = false
approval_gated_email_send_enabled = true
unrestricted_email_send_enabled = false
```

`status.recipient_catalog` must match the current local file.

## Test A: volume

Say separately:

```text
把系统音量调到35%。
把音量降低10%。
现在的音量是多少？
```

Expected: real system volume changes, the observed value is returned, and every mutation receives ToolResult and VerificationResult.

## Test B: brightness

Say separately:

```text
把屏幕亮度调到60%。
把亮度降低一点。
现在的亮度是多少？
```

On a WMI-capable display, the real brightness changes and verifies. Without WMI brightness support, the action must fail explicitly without claiming success.

## Test C: summary artifact

Open or start `Loss.pptx`, then say:

```text
请生成当前演示文稿的中文摘要。
```

Expected: one Markdown summary and one JSON record are created in `demo_files/LOG`, the UI displays `打开摘要`, and the Backend verifies the non-empty artifact.

## Test D: draft for Rico

Say:

```text
生成当前演示的摘要，并准备一封发给 Rico 的 Outlook 草稿。
```

Expected:

1. the summary is generated;
2. the task pauses for the first approval;
3. the approval prompt identifies `Rico <jiangdizhao@gmail.com>`;
4. after approval, Outlook creates and displays a draft from `jiangdizhao1@outlook.com` to `jiangdizhao@gmail.com`;
5. the body contains `该邮件目前仅保存为 Outlook 草稿，尚未发送。`;
6. result data contains `recipient_key=rico`, `outlook_draft_verified=true`, and `sent=false`.

## Test E: runtime file reload and Tom

Keep the Backend running. Add a valid `tom` entry to `config/email_recipients.json`, save the file, and check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/office/status |
  ConvertTo-Json -Depth 12
```

The returned recipient catalog must now contain Tom without restarting Uvicorn.

Then say:

```text
生成当前演示的摘要，并准备一封发给 Tom 的 Outlook 草稿。
```

Expected: Realtime uses `recipient_key="tom"`; Backend resolves the address from the current local file; the approval prompt and displayed draft identify Tom. No raw email address appears in the tool arguments.

Then test an unknown person:

```text
准备一封发给 Alice 的邮件。
```

Expected: the assistant requests clarification or asks the user to add/select a configured contact; no task is executed for an unknown alias.

## Test F: invalid file rejection

While the Backend is running, temporarily introduce malformed JSON or an invalid address into the local recipient file, then request status:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/office/status |
  ConvertTo-Json -Depth 12
```

Expected:

- the status response reports a non-empty `recipient_config_error`;
- `recipient_catalog` is empty;
- the Backend does not use the previous valid contacts;
- a draft or send request fails explicitly.

Restore valid JSON before continuing.

## Test G: second-approved sending

With a verified unsent draft open, say:

```text
把刚才给 Rico 的草稿发送出去。
```

Expected:

1. a new task reaches `waiting_approval` for `outlook_send_approved_draft`;
2. no send occurs before the second approval;
3. after approval, Backend reopens the latest verified unsent draft for `rico`;
4. sender remains `jiangdizhao1@outlook.com`;
5. the recipient list contains exactly `jiangdizhao@gmail.com`, with no added To, CC, or BCC recipient;
6. Backend removes `该邮件目前仅保存为 Outlook 草稿，尚未发送。` and its English equivalent;
7. Backend saves and re-verifies the edited draft before calling Outlook `Send()`;
8. result data reports `draft_notice_removed=true`, `send_invoked=true`, and `sent=true`;
9. `delivery_confirmed=false` remains accurate because local COM acceptance is not proof of remote mailbox delivery.

Repeat with `跳过` or `取消任务`. The draft must remain unsent.

## Test H: compound two-approval workflow

Say:

```text
生成当前演示的摘要，准备一封发给 Rico 的 Outlook 草稿，审核后发送。
```

Expected ordered behavior:

1. generate summary;
2. pause for first approval;
3. create and display the Rico draft;
4. pause again for second approval;
5. remove the draft-only notice and send;
6. both draft and send steps use the same `recipient_key`.

## Test I: recipient tampering

Create a verified draft, then manually add another To, CC, or BCC recipient before the second approval. Approve sending.

Expected: Backend refuses to send because the message no longer contains exactly one selected configured recipient.

## Test J: permission

Switch Actor to `visitor` and test device control, summary generation, Outlook draft creation, and Outlook sending. Expected: permission denied and no Office task scheduled.

## Acceptance

Gate 3–5 passes local Windows acceptance when:

- volume and supported brightness controls mutate and verify real state;
- summary artifacts remain bounded to the configured LOG directory;
- contacts are loaded from the local editable recipient file rather than environment-variable JSON or model-generated addresses;
- the local file is ignored by Git and is initialized from a tracked template;
- valid file edits become effective without Backend restart;
- unknown, malformed, or duplicate contacts are rejected without stale fallback;
- the fixed sender can create drafts for Rico and other configured contacts;
- draft creation and sending require separate approvals;
- sending removes the draft-only notice before Outlook `Send()`;
- altered or additional recipients prevent sending;
- visitor denial, cancellation, and failure-stop behavior remain intact;
- existing Gate 2B PowerPoint commands and DISPLAY2 verification still pass.
