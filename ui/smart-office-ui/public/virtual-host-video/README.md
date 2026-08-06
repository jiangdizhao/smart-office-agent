# Virtual-host video assets

The Agent UI now uses a deterministic, boundary-matched video state machine. The seven muted assets are deliberately kept outside Git because they are binary exhibition media.

## Required browser assets

Copy these H.264 MP4 files into this directory:

- `idle-1.mp4` — source: `静止1.mov`
- `idle-2.mp4` — source: `静止2.mov`
- `idle-3.mp4` — source: `静止3.mov`
- `intro.mp4` — source: `开始介绍.mp4`
- `talk-1.mp4` — source: `说话1.mov`
- `talk-2.mp4` — source: `说话2.mov`
- `talk-3.mp4` — source: `说话3.mov`

Recommended delivery format: H.264, `yuv420p`, 828×1108, 30 fps, muted, with `faststart` enabled. The supplied HEVC `.mov` files should be converted before browser testing because HEVC support depends on the Windows codec installation.

## Playback contract

### No visitor

`idle-1 → idle-2 × 3 → idle-3 → repeat`

The number of `idle-2` repetitions is configurable.

### Visitor arrival

The active idle clip finishes at a slightly higher playback rate. The sequence then finishes `idle-3` and starts `intro`. If arrival occurs during `idle-3`, that current clip simply finishes before `intro`.

The welcome voice is gated until the first frame of `intro` is actually displayed. If the video asset cannot load, the gate fails open after a timeout so reception speech is never permanently blocked.

### User and Agent turns

- While the visitor is speaking or the system is processing the turn, Sara holds the first frame of `talk-1`.
- Agent speech: `talk-1 → talk-2 loop → talk-3`.
- `talk-2` remains in a native loop while audio is active. After audio completes, the current loop closes naturally and `talk-3` plays.
- A visitor barge-in immediately starts the short `talk-3` closing movement, then holds the first frame of `talk-1`.

There is no glow, flash, zoom or opacity crossfade. Two stacked video elements are still used, but the next element is decoded before an instantaneous boundary cut.

## Configuration

Add any required overrides to `ui/smart-office-ui/.env.local`:

```dotenv
VITE_VIRTUAL_HOST_IDLE_2_REPETITIONS=3
VITE_VIRTUAL_HOST_ARRIVAL_CURRENT_RATE=1.28
VITE_VIRTUAL_HOST_ARRIVAL_IDLE_3_RATE=1.18
VITE_VIRTUAL_HOST_TALK_LOOP_EXIT_RATE=1.20
VITE_VIRTUAL_HOST_INTERRUPTED_TALK_OUT_RATE=1.28
```

Recommended ranges are enforced in code to prevent visibly unnatural playback.

## Conversion script

Run the installer from the repository root. ASCII aliases such as `idle-1.mov` are detected automatically, or pass the original files explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\ui\smart-office-ui\scripts\install_virtual_host_video_assets.ps1 `
  -Idle1 "D:\Video\静止1.mov" `
  -Idle2 "D:\Video\静止2.mov" `
  -Idle3 "D:\Video\静止3.mov" `
  -Intro "D:\Video\开始介绍.mp4" `
  -Talk1 "D:\Video\说话1.mov" `
  -Talk2 "D:\Video\说话2.mov" `
  -Talk3 "D:\Video\说话3.mov"
```

Then restart Vite and use `Ctrl+Shift+R` in the browser.
