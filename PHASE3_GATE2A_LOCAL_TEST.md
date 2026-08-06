# Phase 3 Gate 2A Local Windows Acceptance

Gate 2A connects natural Chinese/English utterances to controlled GPT Realtime presentation function calls, Backend permission checks, PowerPoint COM execution, observed-state verification, and secondary-display verification.

## 1. Update the existing branch

```powershell
cd F:\smart-office-agent
git status --short
git fetch origin
git switch integration/virtual-host
git pull --ff-only origin integration/virtual-host
git rev-parse --short HEAD
```

Do not reset a dirty working tree. Preserve local changes before updating.

## 2. Start the Backend

Stop any previous Backend process first. Then:

```powershell
cd F:\smart-office-agent\backend
powershell -ExecutionPolicy Bypass -File .\scripts\start_backend_realtime.ps1
```

Enter the OpenAI API key when prompted. This script starts Uvicorn without `--reload`, which is required for stable PowerPoint COM automation.

Verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/ | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8000/api/realtime/status | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8000/agent/turn/status | ConvertTo-Json -Depth 10
```

Expected indicators:

- phase: `m3a_fusion_phase_3_gate_2a`
- realtime presentation function calling: enabled
- presentation execution through `/agent/turn`: enabled
- general Office execution through `/agent/turn`: disabled

## 3. Start the Frontend

In another PowerShell window:

```powershell
cd F:\smart-office-agent\ui\smart-office-ui
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173/` on DISPLAY1.

## 4. Chinese employee test

In the Gate 2A console select:

- Language: 中文
- Actor: Employee
- Speech recognition: GPT Realtime
- Voice output: GPT Realtime

Connect voice, then issue one request per turn:

1. `请打开演示文稿。`
2. `开始演示。`
3. `下一页。`
4. `请翻到下一张。`
5. `回到上一页。`
6. `跳到第五页。`
7. `现在是第几页？`
8. `结束演示。`

For every real action, check:

- Tool shows the expected registered presentation capability.
- Verification shows PASS.
- Presentation state and slide number match PowerPoint.
- The slide show is physically visible on DISPLAY2.
- Display shows `\\.\DISPLAY2` and `verified` while the slide show is active.
- The Smart Office UI remains usable on DISPLAY1.
- Spoken feedback describes the observed result rather than claiming unverified success.

## 5. English employee test

Switch Language to English and issue one request per turn:

1. `Open the presentation.`
2. `Start the slide show.`
3. `Move to the next slide.`
4. `Go back one slide.`
5. `Take me to slide five.`
6. `What slide are we on?`
7. `End the presentation.`

The same tool, verification, slide-state, and DISPLAY2 checks must pass.

## 6. Visitor permission test

Switch Actor to Visitor and say:

```text
Open the presentation.
```

Expected:

- permission: denied
- no PowerPoint tool execution
- no change to the PowerPoint state
- a spoken explanation that visitors cannot control PowerPoint

## 7. Boundary tests

Test the following separately:

```text
跳到第 999 页。
打开那个。
打开演示文稿并开始播放。
请介绍一下你们的解决方案。
```

Expected:

- invalid slide number: rejected by Backend or PowerPoint state validation; no false success
- ambiguous presentation instruction: clarification or no execution
- compound request: no real multi-step execution in Gate 2A; it remains plan-only for Gate 2B
- reception question: continues through the Phase 2 reception knowledge path

## Acceptance result

Gate 2A passes only when:

- Chinese natural-language commands pass
- English natural-language commands pass
- Visitor denial passes
- PowerPoint actions and observed state match
- slide show placement on DISPLAY2 is verified
- Smart Office UI stays on DISPLAY1
- no unsupported or compound request is falsely reported as completed
