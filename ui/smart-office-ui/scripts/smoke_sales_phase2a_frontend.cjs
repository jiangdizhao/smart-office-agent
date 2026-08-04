const fs = require('node:fs')
const path = require('node:path')

function read(relative) {
  return fs.readFileSync(path.join(process.cwd(), relative), 'utf8')
}

function requireText(source, text, label) {
  if (!source.includes(text)) throw new Error(`${label} is missing: ${text}`)
}

const main = read('src/main.tsx')
const scheduler = read('src/sales/salesPhase2AProactiveScheduler.ts')
const client = read('src/sales/salesPhase2AClient.ts')
const router = read('src/sales/salesConversationRouter.ts')

requireText(main, 'installSalesPhase2AProactiveScheduler', 'Phase 2A scheduler installation')
if (main.includes('installSalesProactiveScheduler()')) {
  throw new Error('The Phase 1 scheduler must not remain installed beside Phase 2A.')
}
requireText(scheduler, 'FIRST_NUDGE_MS = 8_000', 'Phase 2A first delay')
requireText(scheduler, 'SECOND_NUDGE_DELAY_MS = 10_000', 'Phase 2A second delay')
requireText(scheduler, "detail.purpose.startsWith('sales_')", 'Sales-output continuation gate')
requireText(scheduler, "detail.purpose === 'sales_phase2a_proactive_second'", 'Second-nudge terminal rule')
if (scheduler.includes('if (!detail.expectUserResponse)')) {
  throw new Error('Phase 2A must not stop merely because the previous sales reply had no question.')
}
requireText(client, '/api/sales/phase2a/proactive', 'Phase 2A proactive endpoint')
requireText(client, '/api/sales/phase2a/output-result', 'Phase 2A output lifecycle endpoint')
requireText(client, '/api/sales/phase2a/self-introduction', 'Canonical self-introduction endpoint')
requireText(router, 'semantic_canonical_self_introduction', 'Semantic deterministic self-introduction route')
requireText(router, "semantic?.primary_intent === 'self_introduction'", 'Unified identity intent gate')
requireText(router, "purpose: 'sales_self_introduction'", 'Self-introduction voice context')
requireText(router, "replyMode: 'pure_sales'", 'Self-introduction sales continuation mode')

console.log('PASS: Phase 2A retains a single continuity scheduler and canonical persona while identity recognition is supplied by the unified semantic router.')
