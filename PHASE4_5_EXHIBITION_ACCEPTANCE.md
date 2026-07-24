# Phase 4–5 Virtual Host Exhibition Acceptance

Branch: `integration/virtual-host`

This acceptance run verifies the exhibition-facing virtual host. The main screen must stay clean: no route, tool name, task UUID, raw verification result, WebRTC state string, sender address, recipient catalog, or other developer diagnostics may be visible on `/`.

Use `/debug` only when troubleshooting.

## Before the rehearsal

1. Start the Backend with the normal Realtime startup script.
2. Start the frontend and open `http://127.0.0.1:5173/` on the primary display.
3. Put PowerPoint and Outlook on the secondary display.
4. Confirm Classic Outlook is signed in and the local recipient JSON contains the intended demonstration recipient.
5. Open the settings drawer once and confirm:
   - language can switch between Chinese and English;
   - ASR can switch explicitly between GPT Realtime and Browser ASR when supported;
   - GPT Realtime remains the selected voice-output provider unless text-only mode is intentionally selected;
   - the drawer shows only user-facing service readiness, not raw diagnostics;
   - Stop speaking and Cancel current task are available only when applicable.
6. Close the drawer before the public demonstration.

## Scenario acceptance

### 1. Ordinary self-introduction

Say:

> 介绍一下你自己。

Expected:

- route is handled without creating an Office task;
- the virtual host gives the current Smart Office introduction, including reception and Office-assistant capabilities;
- no obsolete statement such as “Phase 2 does not execute real Office actions” is spoken;
- user subtitle remains visible during processing;
- Agent subtitle advances in lyric-style segments and fades after speaking.

### 2. Company / solution introduction

Say:

> 请介绍一下 Smart Office 方案。

Expected:

- the answer describes the primary-screen virtual host and secondary-screen Office workspace;
- the answer remains grounded in the approved local company profile;
- no Office task is created.

### 3. Open PowerPoint

Say:

> 打开 PowerPoint 演示文稿并开始播放。

Expected:

- the main screen changes from Processing to Executing;
- PowerPoint opens or starts on the configured secondary display;
- the virtual host remains on the primary display;
- completion speech occurs only after the Backend result is available.

### 4. Next slide

Say:

> 下一页。

Expected:

- the slide number increases by one;
- the main screen does not expose route, tool, task ID, or verification internals;
- the short completion reply is spoken once.

### 5. Adjust volume

Say:

> 把音量调到百分之四十。

Expected:

- Windows master volume changes to 40 percent;
- the virtual host reports the observed result once;
- no second voice or automatic provider fallback is used.

### 6. Summarize the presentation

Say:

> 总结当前演示并生成中文摘要。

Expected:

- the summary is written under the configured `demo_files/LOG` directory;
- the main screen remains in Executing while the task runs;
- the result is spoken once and the generated artifact can still be opened from the debug view when needed.

### 7. Create Outlook draft

Say:

> 根据最新演示摘要为 Rico 创建 Outlook 邮件草稿。

Expected:

- the simplified approval overlay appears;
- it shows the user-facing action and recipient only;
- it does not show EntryID, sender configuration, allowlist internals, task UUID, route, or tool name;
- Create draft, Not now, Stop speaking when applicable, and Cancel task are available;
- a rapid double-click cannot submit the same approval twice.

### 8. Approve and send

After checking the opened draft, continue the same task and use the second approval overlay.

Expected:

- the overlay clearly distinguishes Send now from Create draft;
- confirming sends the verified draft through Outlook;
- the overlay disappears when the task resumes;
- the final message is concise and does not recite internal COM or verification details.

### 9. Interrupt speech

During a long self-introduction or company introduction, press the main Stop speaking button, or open the settings drawer and press Stop speaking.

Expected:

- audio stops immediately;
- stopping speech is not shown as an error;
- the subtitle remains readable;
- the next push-to-talk turn starts normally;
- no stale audio resumes after the new turn begins.

### 10. Five-round continuous voice run

Use one browser session and the same persistent Realtime connection:

1. `介绍一下你自己。`
2. `请介绍一下 Smart Office 方案。`
3. `打开 PowerPoint 演示文稿并开始播放。`
4. `下一页。`
5. `把音量调低十个百分点。`

After each round confirm:

- push-to-talk enters Listening exactly once;
- starting input stops any current output;
- the physical microphone is released after recording;
- one normalized user transcript is displayed;
- one answer is spoken;
- no duplicate audio starts;
- the same browser conversation and persistent Realtime session remain usable;
- the fifth round behaves the same as the first.

## Recovery checks

- A Realtime connection timeout must return the UI to an error/retry state rather than remain Connecting indefinitely.
- A capture, commit, or transcription failure must release the physical microphone.
- Starting a new input turn must stop current output before attaching the microphone.
- Closing or refreshing the virtual-host page must not leave audible output running.
- If Browser ASR is selected, interim text should update while speaking and the final text should remain visible during Processing.

## Automated checks

Run:

```powershell
python backend/scripts/smoke_phase4_5_virtual_host_contract.py
```

Then run the normal frontend build:

```powershell
cd ui\smart-office-ui
npm run build
```

The automated contract checks source-level exhibition boundaries and non-Windows reception routes. The PowerPoint, Windows volume, Outlook, dual-display placement, microphone, and actual five-round audio run still require the local Windows rehearsal above.
