# Phase 1 Local Windows Validation

This checklist validates the code on `integration/virtual-host`. Automated GitHub checks cover Python contracts and the TypeScript/Vite build; microphone, browser permission, WebRTC audio, and speaker behavior require the Windows demo machine.

## 1. Update the local repository

```powershell
cd C:\smart-office-agent
git fetch origin
git switch integration/virtual-host
git pull --ff-only origin integration/virtual-host
git status -sb
git rev-parse --short HEAD
```

Expected branch:

```text
integration/virtual-host
```

Do not run Phase 1 from `main`.

## 2. Install dependencies when required

```powershell
conda activate smartoffice
cd C:\smart-office-agent
python -m pip install -r .\backend\requirements-smartoffice.txt

cd .\ui\smart-office-ui
npm install
```

Existing environments do not need reinstalling when their dependencies are already present.

## 3. Run local static and backend contract checks

```powershell
cd C:\smart-office-agent
conda activate smartoffice
python .\backend\scripts\smoke_phase1_voice_contract.py

cd .\ui\smart-office-ui
npm run build
```

Expected backend message:

```text
PASS: Phase 1 voice API and Milestone 2 task contracts are available.
```

## 4. Start the Backend with the API key kept server-side

```powershell
cd C:\smart-office-agent\backend
powershell -ExecutionPolicy Bypass -File .\scripts\start_backend_realtime.ps1
```

Paste the API key only into the secure prompt. The script does not print or store the key.

If Python auto-detection fails:

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\start_backend_realtime.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/
Invoke-RestMethod http://127.0.0.1:8000/api/realtime/status
Invoke-RestMethod http://127.0.0.1:8000/agent/turn/status
```

`/api/realtime/status` should show `configured = true`.

## 5. Start the Frontend

Open another PowerShell terminal:

```powershell
cd C:\smart-office-agent\ui\smart-office-ui
npm run dev -- --host 127.0.0.1
```

Open:

```text
http://127.0.0.1:5173/
```

A floating **M3A-Fusion · Phase 1** voice panel should appear beside the existing Milestone 2 task console.

## 6. Browser and microphone validation

Use current Microsoft Edge or Chrome.

1. Select `GPT Realtime` for speech recognition.
2. Select `GPT Realtime` for voice output.
3. Click **连接语音**.
4. Accept the microphone permission when prompted.
5. Click **点击说话**, say a full sentence, then click **结束说话**.
6. Confirm the recognized text appears.
7. Confirm `/agent/turn` returns a visible answer.
8. Confirm exactly one voice reads the answer.
9. After capture, confirm the panel shows `Mic: released`.

Provider failure must show an error. It must not start Browser TTS, Kokoro, OpenAI TTS, or another hidden speaker.

## 7. Five-turn stability test

Complete these five turns without refreshing the page:

1. `你好`
2. `你是谁`
3. `打开下午两点的 Teams 会议`
4. `下一页`
5. `停止`

Expected:

- the same WebRTC session remains connected;
- the microphone is released after every input turn;
- no duplicate answers or overlapping voices;
- Phase 1 returns text only and does not execute Teams or PowerPoint yet;
- the existing task console remains usable.

## 8. Correction-aware ASR test

Say:

```text
打开钱会色的演示，不对，是浅灰色，深浅的浅，灰色的灰。
```

Expected normalized transcript:

```text
打开浅灰色的演示。
```

## 9. Long Realtime output test

Use the text input in the Phase 1 panel and submit a Chinese paragraph of at least 150 Chinese characters. Confirm:

- audio starts within approximately 15 seconds;
- playback is not cancelled at 30 seconds;
- the final sentence is spoken;
- the UI returns to idle only after both response completion and audio-buffer completion;
- clicking **停止朗读** cancels immediately.

## 10. Browser ASR alternative

Select `Browser ASR` and repeat two turns. This path uses the browser recognizer for input but still sends normalized text to `/agent/turn`. Voice output remains whichever explicit provider is selected.

## 11. Milestone 2 regression

After voice testing, use the original task console:

1. Run a plan-only meeting task.
2. Confirm SSE events appear.
3. Exercise approval/skip when the plan reaches an approval step.
4. Confirm the task completes or cancels normally.

## Report these items after testing

- output of `git rev-parse --short HEAD`;
- output of `npm run build`;
- JSON from `/api/realtime/status`;
- whether the browser requested microphone permission;
- one successful recognized transcript;
- whether `Mic: released` appeared after each turn;
- whether any duplicate or fallback voice was heard;
- any browser console or Backend error.
