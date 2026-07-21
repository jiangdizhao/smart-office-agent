# Phase 1 Local Acceptance — Smart Office Virtual Host

This checklist validates the browser, microphone, WebRTC, and real-audio paths that cannot be proven by GitHub Actions.

## Preconditions

- Branch: `integration/virtual-host`
- Backend running through `backend/scripts/start_backend_realtime.ps1`
- Frontend running at `http://127.0.0.1:5173`
- Edge or Chrome is used
- The browser has microphone permission for `127.0.0.1:5173`
- The Phase 1 voice panel is visible

## 1. Live backend check

From a second PowerShell window:

```powershell
cd F:\smart-office-agent\backend
powershell -ExecutionPolicy Bypass -File .\scripts\verify_phase1_live_backend.ps1
```

Expected final line:

```text
PASS: Phase 1 live backend status and text-turn contract are healthy.
```

## 2. WebRTC connection

1. Set `语音识别` to `GPT Realtime`.
2. Set `语音输出` to `GPT Realtime`.
3. Click `连接语音`.
4. Confirm the panel shows a connected peer connection and an open data channel.
5. Before recording, confirm `Mic: released`.

Pass condition:

```text
WebRTC: connected / open
Mic: released
```

## 3. Five consecutive push-to-talk turns

Run these five turns without refreshing the page or restarting either service:

1. `你好。`
2. `请介绍一下你自己。`
3. `打开 PowerPoint。`
4. `我说错了，不是打开 Word，是打开 Teams。`
5. `请重复我刚才的最终要求。`

For every turn:

1. Click `点击说话`.
2. Wait until the panel shows `正在聆听`.
3. Speak the whole sentence.
4. Click `结束说话`.
5. Confirm recognized text appears.
6. Confirm exactly one selected provider reads the answer.
7. After recognition finishes, confirm the panel returns to `Mic: released`.
8. Wait for the whole answer to finish before starting the next turn.

Pass conditions:

- All five turns complete without page refresh or reconnect.
- Turn 4 resolves the correction to Teams rather than Word.
- The microphone is released after every turn.
- There is no overlapping second voice.
- There is no silent hidden switch to another provider.

Phase 1 does not execute Office commands through `/agent/turn`; command-like input is used only to validate speech understanding and routing.

## 4. Browser ASR selection

1. Change `语音识别` to `Browser ASR`.
2. Keep `语音输出` explicitly set to either `GPT Realtime` or `仅显示文字`.
3. Speak: `这是浏览器语音识别测试。`
4. Confirm the transcript appears and the turn reaches `/agent/turn`.

Pass conditions:

- Browser ASR is chosen only because the user selected it.
- Changing ASR does not change the selected voice-output provider.
- A provider error is displayed rather than starting another voice.

## 5. Long Chinese audio completion

Select `GPT Realtime` voice output. Paste the following text into the text test box and click `发送`:

```text
欢迎来到我们的智能办公演示空间。我是这里的虚拟接待与办公助手，可以通过自然语言帮助来访者了解企业信息，也可以在获得明确授权后协助员工处理会议和办公任务。当前阶段重点验证语音输入、文本路由、长语音完整播放以及单一语音输出机制。后续阶段将逐步接入 Microsoft Teams、PowerPoint 和多屏窗口管理，并继续保留任务审批、执行验证、取消操作和审计记录。请注意，系统不会在没有依据的情况下编造公司信息，也不会在工具尚未验证成功时声称任务已经完成。
```

Pass conditions:

- Audio starts within 15 seconds.
- The entire passage is read without being cut off.
- The state returns from `正在朗读` to `空闲` only after audio completion.
- Clicking `停止朗读` interrupts the selected voice immediately and does not start another voice.

## 6. Failure cleanup check

During a fresh voice turn, temporarily deny microphone permission or disconnect the selected microphone, then attempt capture.

Pass conditions:

- The UI reports an error.
- The panel returns to `Mic: released`.
- No microphone track remains active in the browser tab after the failed turn.
- A later successful turn can start without refreshing the page.

## Acceptance record

Record the following after testing:

```text
Commit:
Browser and version:
Realtime connection: PASS / FAIL
Five consecutive turns: PASS / FAIL
Mic released after each turn: PASS / FAIL
Correction utterance: PASS / FAIL
Browser ASR selection: PASS / FAIL
Long Chinese playback: PASS / FAIL
Stop output: PASS / FAIL
Failure cleanup and recovery: PASS / FAIL
Overlapping or hidden fallback voice observed: YES / NO
Notes:
```

Phase 1 is complete only when automated checks pass and every local item above is recorded as PASS, with no overlapping or hidden fallback voice.
