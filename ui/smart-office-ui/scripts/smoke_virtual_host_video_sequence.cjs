const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const avatar = fs.readFileSync(path.join(root, 'src/virtual-host/VirtualHostAvatar.tsx'), 'utf8')
const gate = fs.readFileSync(path.join(root, 'src/virtual-host/virtualHostIntroVoiceGate.ts'), 'utf8')
const css = fs.readFileSync(path.join(root, 'src/virtual-host/VirtualHostSeamlessVideo.css'), 'utf8')

function requireText(source, text, label) {
  if (!source.includes(text)) throw new Error(`Missing ${label}: ${text}`)
}

for (const asset of ['idle-1.mp4', 'idle-2.mp4', 'idle-3.mp4', 'intro.mp4', 'talk-1.mp4', 'talk-2.mp4', 'talk-3.mp4']) {
  requireText(avatar, asset, 'video asset')
}
requireText(avatar, 'VITE_VIRTUAL_HOST_IDLE_2_REPETITIONS', 'idle-2 repetition configuration')
requireText(avatar, "loop: true,\n          mode: 'talk-loop'", 'native talk-2 loop')
requireText(avatar, 'holdFirstFrame: true', 'visitor listening hold')
requireText(avatar, 'smartoffice:realtime-vad-speech-started', 'barge-in event')
requireText(avatar, 'smartoffice:host-intro-playback-started', 'intro playback acknowledgement')
requireText(avatar, 'transitionWorkerRunningRef', 'single transition worker')
requireText(avatar, 'queuedTransitionRef', 'latest queued transition')
requireText(avatar, 'currentTransitionRef', 'current transition owner')
requireText(avatar, 'initialStateEffectRef', 'duplicate mount transition guard')
requireText(avatar, 'if (code === 1) return', 'normal media abort suppression')
requireText(avatar, 'Virtual host video could not be loaded', 'truthful media failure copy')
requireText(gate, 'await gate.promise', 'intro voice synchronisation')
requireText(gate, "releaseGate('timeout'", 'intro fail-open timeout')
requireText(css, 'transition: none !important', 'flash-free layer switch')
requireText(css, '.virtual-host-stage::before', 'legacy stage backdrop override')
requireText(css, 'content: none !important', 'stage pseudo-element removal')
requireText(css, 'background: none !important', 'stage egg gradient removal')
requireText(css, 'box-shadow: none !important', 'stage egg shadow removal')

for (const removed of [
  'CROSSFADE_MS',
  'talk-a.mp4',
  'talk-b.mp4',
  'talk-c.mp4',
  'idle-primary.mp4',
  'idle-rare.mp4',
  'transitionSerialRef',
  'preloadersRef',
  "document.createElement('video')",
  'setAssetError(true)',
  'Virtual host video assets are not installed',
]) {
  if (avatar.includes(removed)) throw new Error(`Legacy or race-prone token remains: ${removed}`)
}

console.log('PASS: virtual-host seamless video sequence, transition ownership and stage-backdrop contract')
