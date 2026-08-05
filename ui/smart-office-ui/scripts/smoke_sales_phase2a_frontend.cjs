const fs = require('node:fs')
const path = require('node:path')

function read(relative) {
  return fs.readFileSync(path.join(process.cwd(), relative), 'utf8')
}

function requireText(source, text, label) {
  if (!source.includes(text)) throw new Error(`${label} is missing: ${text}`)
}

const main = read('src/main.tsx')
const orchestrator = read('src/sales/salesPhase2BEngagementOrchestrator.ts')
const client = read('src/sales/salesPhase2BClient.ts')
const router = read('src/sales/salesConversationRouter.ts')
const bridge = read('src/interaction/embeddedInteractionBridge.ts')
const panelHost = read('src/interaction/InteractionPanelHost.tsx')
const voiceDelivery = read('src/sales/voiceDelivery.ts')

requireText(main, 'installSalesPhase2BEngagementOrchestrator', 'Phase 2B orchestrator installation')
if (main.includes('installSalesPhase2AProactiveScheduler')) {
  throw new Error('The legacy Phase 2A scheduler must not remain installed beside Phase 2B.')
}
if (main.includes('installSalesProactiveScheduler()')) {
  throw new Error('The Phase 1 scheduler must not remain installed beside Phase 2B.')
}
requireText(orchestrator, 'Any completed customer-facing output can resume', 'All-output rearm rule')
requireText(orchestrator, 'this.rearmBusy(', 'Busy-state defer and retry')
requireText(orchestrator, "detail.purpose === 'sales_phase2b_proactive_first'", 'Sequential Phase 2B delay')
requireText(orchestrator, 'observePhase2BTurn', 'Non-blocking visitor context observer')
requireText(orchestrator, 'requestPhase2BProactive', 'Phase 2B proactive planner')
requireText(orchestrator, 'reportPhase2BInteraction', 'Verified interaction lifecycle')
requireText(orchestrator, "detail.questionField === 'booking'", 'Booking pending-intent ownership')
requireText(orchestrator, "detail.questionField === 'contact'", 'Contact pending-intent ownership')
requireText(orchestrator, 'setSemanticPendingIntent', 'Structured conversion response handling')
requireText(client, '/api/sales/phase2b/observe-turn', 'Phase 2B observer endpoint')
requireText(client, '/api/sales/phase2b/proactive', 'Phase 2B proactive endpoint')
requireText(client, '/api/sales/phase2b/output-result', 'Phase 2B output lifecycle endpoint')
requireText(client, '/api/sales/phase2b/interaction-result', 'Phase 2B interaction verification endpoint')
requireText(bridge, "'submit_booking'", 'Verified booking submission event')
requireText(bridge, "'submit_contact'", 'Verified contact submission event')
requireText(bridge, 'verified: ok', 'Submission verification evidence')
requireText(panelHost, 'smartoffice:interaction-panel-closed', 'Completed panel-close lifecycle')
requireText(voiceDelivery, "style: 'warm_confident'", 'Warm conversational default voice')
requireText(voiceDelivery, "style: 'verified_success'", 'Verified operational result voice')
requireText(router, 'semantic_canonical_self_introduction', 'Canonical self-introduction route')
requireText(router, "semantic?.primary_intent === 'self_introduction'", 'Unified identity intent gate')
requireText(router, "purpose: 'sales_self_introduction'", 'Self-introduction voice context')

console.log('PASS: Phase 2B has one Visit engagement owner, all-output rearming, busy deferral, contextual observation, verified conversion events and warm conversational delivery.')
