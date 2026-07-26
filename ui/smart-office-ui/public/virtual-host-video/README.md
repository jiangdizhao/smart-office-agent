# Virtual-host video assets

The frontend video state machine expects these muted MP4 assets in this directory:

- `intro.mp4` — full five-second introduction animation.
- `idle-primary.mp4` — source `stable.mp4`, 2.5–5.0 seconds; main idle loop.
- `idle-rare.mp4` — source `stable.mp4`, 0.0–2.5 seconds; occasional gesture.
- `talk-a.mp4` — source `talk.mp4`, 0.50–1.75 seconds.
- `talk-b.mp4` — source `talk.mp4`, 2.25–3.15 seconds.
- `talk-c.mp4` — source `talk.mp4`, 3.45–4.30 seconds.

The UI plays five primary idle loops for each rare idle gesture. During Agent speech it rotates through all three mouth-motion clips. Two stacked video elements provide a 260 ms crossfade.

The introduction video is always muted. Camera proximity detection triggers the animation and GPT Realtime separately reads exactly:

`Welcome to our office.`

Install the prepared asset pack before local browser testing. The MP4 files are intentionally not committed as UTF-8 text through the repository connector.
