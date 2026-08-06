import { preemptiveTurnCoordinator } from './preemptiveTurnCoordinator'

const OFFICE_TURN_TIMEOUT_MS = 28_000
const OFFICE_PATTERN = /ppt|power\s*point|powerpoint|幻灯片|演示文稿|presentation|\bslides?\b|slideshow|teams|one\s*note|onenote|outlook|word|excel|邮件|邮箱|草稿|会议|音量|亮度|音乐|录音|文档|office/i

let timer: number | null = null
let generation = 0
let pendingText = ''

function clearWatchdog(reason: string): void {
  generation += 1
  pendingText = ''
  if (timer !== null) {
    window.clearTimeout(timer)
    timer = null
  }
  console.info('[OfficeTurnWatchdog] cleared', { reason, generation })
}

function textFromEvent(event: Event): string {
  const detail = event instanceof CustomEvent ? event.detail : null
  return String(
    detail?.transcript
    ?? detail?.text
    ?? detail?.utterance
    ?? '',
  ).trim()
}

function arm(text: string): void {
  if (!OFFICE_PATTERN.test(text)) return
  clearWatchdog('new_office_utterance')
  const current = generation
  pendingText = text
  timer = window.setTimeout(() => {
    if (current !== generation || !pendingText) return
    const expiredText = pendingText
    timer = null
    pendingText = ''
    console.error('[OfficeTurnWatchdog] timeout-recovery', {
      generation: current,
      text: expiredText,
      timeoutMs: OFFICE_TURN_TIMEOUT_MS,
    })
    window.dispatchEvent(new CustomEvent('smartoffice:office-turn-watchdog-timeout', {
      detail: { text: expiredText, timeoutMs: OFFICE_TURN_TIMEOUT_MS },
    }))
    void preemptiveTurnCoordinator
      .preempt('office_turn_watchdog_timeout')
      .then(() => preemptiveTurnCoordinator.recoverToReady('office_turn_watchdog_timeout'))
      .catch((error) => {
        console.error('[OfficeTurnWatchdog] recovery-failed', {
          message: error instanceof Error ? error.message : String(error),
        })
      })
  }, OFFICE_TURN_TIMEOUT_MS)
}

for (const name of [
  'smartoffice:realtime-continuous-utterance',
  'smartoffice:continuous-user-transcript',
  'smartoffice:current-user-utterance',
]) {
  window.addEventListener(name, (event) => arm(textFromEvent(event)))
}

for (const name of [
  'smartoffice:assistant-output-started',
  'smartoffice:assistant-output-completed',
  'smartoffice:assistant-output-interrupted',
  'smartoffice:assistant-output-failed',
  'smartoffice:assistant-caption-direct',
  'smartoffice:turn-ready',
  'smartoffice:visit-activated',
  'smartoffice:visit-revoked',
]) {
  window.addEventListener(name, () => clearWatchdog(name))
}
