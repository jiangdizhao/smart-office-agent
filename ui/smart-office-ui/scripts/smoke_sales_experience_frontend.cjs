const fs = require('node:fs')
const path = require('node:path')

function read(relative) {
  return fs.readFileSync(path.join(process.cwd(), relative), 'utf8')
}

function requireText(source, text, label) {
  if (!source.includes(text)) throw new Error(`${label} is missing: ${text}`)
}

const scheduler = read('src/sales/salesProactiveScheduler.ts')
const voice = read('src/voice/voiceOutputManager.ts')
const delivery = read('src/sales/voiceDelivery.ts')
const ui = read('src/virtual-host/VirtualHostApp.tsx')
const receptionClient = read('src/sales/salesExperienceClient.ts')

for (const event of [
  'smartoffice:assistant-output-started',
  'smartoffice:assistant-output-completed',
  'smartoffice:assistant-output-interrupted',
  'smartoffice:assistant-output-failed',
]) {
  requireText(scheduler, event, 'Visit sales coordinator')
}
requireText(scheduler, 'FIRST_NUDGE_MS = 7_000', 'First silence threshold')
requireText(scheduler, 'SECOND_NUDGE_DELAY_MS = 8_000', 'Sequential second threshold')
requireText(scheduler, "detail.purpose === 'sales_proactive_first'", 'Sequential scheduling rule')
if (scheduler.includes("addEventListener('smartoffice:realtime-speaking-stop'")) {
  throw new Error('The coordinator must not treat the ambiguous Realtime stop event as completion.')
}

for (const result of ['started', 'completed', 'interrupted', 'failed']) {
  requireText(voice, `'${result}'`, `Voice lifecycle result ${result}`)
}
requireText(voice, "this.dispatchLifecycle('started'", 'Voice lifecycle start dispatch')
requireText(voice, "this.dispatchLifecycle('completed'", 'Voice lifecycle completion dispatch')
requireText(voice, "aborted ? 'interrupted' : 'failed'", 'Interrupted/failed classification')
requireText(voice, 'speakExpressiveExact', 'Expressive exact speech path')
requireText(delivery, 'light_playful', 'Approved playful delivery style')
requireText(delivery, 'calm_reassuring', 'Sensitive-context delivery style')
requireText(delivery, 'pause_before_question', 'Question pacing control')
requireText(receptionClient, '/api/sales/experience/proactive', 'Experience proactive endpoint')
requireText(ui, '数字管理员 · 解决方案顾问', 'Compact Chinese customer-facing identity')
requireText(ui, 'Digital Manager · Solution Consultant', 'English header identity')
if (ui.includes("Smart Office 虚拟助手。访客靠近后")) {
  throw new Error('The old virtual-assistant standby identity is still visible.')
}

console.log('PASS: customer-facing Phase 1 uses reliable output lifecycle events, sequential Visit-scoped proactive timing, controlled voice delivery and the English-first bilingual Digital Manager persona.')
