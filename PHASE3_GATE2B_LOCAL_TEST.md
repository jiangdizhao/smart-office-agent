# Phase 3 Gate 2B Local Windows Acceptance

Gate 2B adds bounded compound PowerPoint commands on top of the accepted Gate 2A path.

## Preconditions

- Branch: `integration/virtual-host`
- Backend and frontend are restarted after pulling the latest commit.
- `demo_files/Loss.pptx` exists.
- Microsoft 365 PowerPoint Desktop and pywin32 are available.
- Smart Office UI remains on `DISPLAY1`.
- PowerPoint slide show is placed on `DISPLAY2`.
- Actor is `employee` or `operator` unless testing permission denial.

## Start

```powershell
cd F:\smart-office-agent
git fetch origin
git switch integration/virtual-host
git pull --ff-only origin integration/virtual-host
git rev-parse --short HEAD
```

Backend:

```powershell
cd F:\smart-office-agent\backend
powershell -ExecutionPolicy Bypass -File .\scripts\start_backend_realtime.ps1
```

Frontend:

```powershell
cd F:\smart-office-agent\ui\smart-office-ui
npm run dev -- --host 127.0.0.1
```

Close all existing PowerPoint windows before the first test. This verifies that the Realtime/task path can bootstrap PowerPoint itself.

## Test A: Chinese compound command

Say one complete utterance:

```text
请打开演示文稿，开始播放，然后跳到第五页。
```

Expected:

- one `presentation_execute_sequence` task is created;
- three ordered steps appear: open, start, go to slide 5;
- every step reaches `succeeded` only after verification;
- slide show appears on `DISPLAY2`;
- final page is 5;
- the final spoken answer reports task completion and page 5.

Then say:

```text
再往后翻两页。
```

Expected:

- two ordered `presentation_next_slide` steps;
- final page advances by exactly two;
- both steps are verified.

Then say:

```text
回到上一页，然后告诉我现在是第几页。
```

Expected:

- previous-slide step followed by status step;
- final answer reports the observed page.

## Test B: English compound command

Say:

```text
Open the presentation, start the slide show, then go to slide five.
```

Expected:

- recognized text stays English;
- task executes three ordered verified steps;
- final text and speech contain no Chinese;
- final page is 5 on `DISPLAY2`.

Then say:

```text
Move forward two slides.
```

Expected:

- two next-slide steps;
- final page advances by exactly two.

Then say:

```text
Go back one slide, then tell me which slide we are on.
```

Expected:

- previous-slide followed by status;
- final answer is entirely English.

## Test C: failure stops later steps

End the slide show, then say:

```text
下一页，然后跳到第五页。
```

Expected:

- first step fails because no slide show is active;
- the second step is not executed;
- task status is `failed`;
- the agent does not claim success.

Start the slide show and say:

```text
跳到第999页，然后下一页。
```

Expected:

- go-to-slide step fails range validation or observed-state verification;
- next-slide step is not executed;
- current page does not incorrectly advance.

## Test D: ambiguity and permission

Say:

```text
打开那个，然后继续。
```

Expected: clarification or no execution; no invented file or action.

Switch actor to `visitor` and say:

```text
打开演示文稿，然后开始播放。
```

Expected:

- permission is denied;
- no compound task is created;
- PowerPoint state does not change.

## Test E: cancellation

Use a longer sequence, for example:

```text
下一页，然后下一页，再下一页，再回到上一页，然后告诉我现在是第几页。
```

While the task is active, test both cancellation paths separately:

```text
取消当前任务。
```

and the `取消当前任务` button.

English voice cancellation may be tested with:

```text
Cancel the current task.
```

Expected:

- the panel remains available for Push-to-Talk while the background task runs;
- the voice request is routed to the active TaskSession rather than treated as a new PowerPoint sequence;
- task reaches `cancelled`;
- the currently executing COM call may finish, but no later pending step starts;
- UI and spoken answer report cancellation without claiming full completion.

## Acceptance

Gate 2B passes local Windows acceptance when:

- Chinese and English compound commands preserve action order;
- each step stores a real ToolResult and VerificationResult;
- the sequence stops after the first failed step;
- voice and button cancellation prevent later pending steps from running;
- Visitor cannot create or execute a sequence;
- English transcript, final text, and speech stay English;
- Gate 2A single commands still work;
- PowerPoint can be bootstrapped from a fully closed state;
- slide show remains on `DISPLAY2` and Smart Office UI remains on `DISPLAY1`.
