const fs = require('node:fs')
const path = require('node:path')

function read(relative) {
  return fs.readFileSync(path.join(process.cwd(), relative), 'utf8')
}

function requireText(source, text, label) {
  if (!source.includes(text)) throw new Error(`${label} is missing: ${text}`)
}

function forbidText(source, text, label) {
  if (source.includes(text)) throw new Error(`${label} must not contain: ${text}`)
}

const core = read('src/voice/fastConversationRouterCore.ts')
const sales = read('src/sales/salesConversationRouter.ts')
const renderer = read('src/sales/salesReplyRenderer.ts')
const proactive = read('src/sales/salesPhase2AProactiveScheduler.ts')
const client = read('src/routing/unifiedSemanticRouterClient.ts')

requireText(core, 'requestUnifiedSemanticRoute', 'Main conversation route')
requireText(core, "semantic.final_policy_decision", 'Policy-gated route')
requireText(core, 'canonicalSystemCommand', 'Canonical execution adapter')
requireText(core, "route.domain === 'interaction'", 'Structured interaction domain')
requireText(core, "route.domain === 'office'", 'Structured Office domain')
requireText(core, '__SMART_OFFICE_SEMANTIC_ANSWER__', 'Clarification answer context')
forbidText(core, 'matchInteractionWindowIntent', 'Main conversation route')
forbidText(core, 'matchSystemAction', 'Main conversation route')
forbidText(core, 'Smart Office 虚拟接待员', 'General direct persona')

requireText(sales, 'base.semantic_decision?.route', 'Sales semantic dispatch')
requireText(sales, "semantic?.primary_intent === 'self_introduction'", 'Semantic identity dispatch')
requireText(sales, 'flattenSemanticProfile', 'Validated sales profile bridge')
forbidText(sales, 'matchesSelfIntroduction', 'Sales route')
forbidText(sales, 'setSemanticPendingIntent', 'Sales preview route')
forbidText(sales, 'updatePendingIntent', 'Sales preview route')

requireText(renderer, 'questionFieldForPlan', 'Sales question lifecycle metadata')
requireText(renderer, "plan.recommended_action === 'offer_booking'", 'Booking question field')
requireText(renderer, "plan.recommended_action === 'offer_contact'", 'Contact question field')
requireText(renderer, "return 'booking'", 'Booking lifecycle field')
requireText(renderer, "return 'contact'", 'Contact lifecycle field')

requireText(proactive, 'syncPendingIntentAfterCompletedOutput', 'Completed-output pending sync')
requireText(proactive, "detail.questionField === 'booking'", 'Completed booking question')
requireText(proactive, "detail.questionField === 'contact'", 'Completed contact question')
requireText(proactive, 'clearSemanticPendingIntent', 'Superseded pending clear')
requireText(proactive, 'setSemanticPendingIntent', 'Completed pending set')
requireText(proactive, 'void this.syncPendingIntentAfterCompletedOutput(detail)', 'Assistant completion lifecycle hook')
forbidText(proactive, "response.reason === 'contextual_booking_offer'", 'Pre-speech pending write')
forbidText(proactive, 'pendingOfferVisitId', 'Pre-speech pending state')

requireText(client, '/api/semantic-route', 'Unified router endpoint')
requireText(client, '/api/semantic-route/pending', 'Pending intent endpoint')
requireText(client, "method: 'DELETE'", 'Pending intent cancellation endpoint')
requireText(client, '__SMART_OFFICE_SEMANTIC_ROUTE__', 'Operator diagnostics')
requireText(client, 'flattenSemanticProfile', 'Profile evidence bridge')
requireText(client, 'SUPPORTED_SALES_PROFILE_FIELDS', 'Deterministic profile field allowlist')
requireText(client, '.filter(([key]) => SUPPORTED_SALES_PROFILE_FIELDS.has(key))', 'Unsupported profile filtering')

console.log('PASS: frontend uses one semantic route, policy-gated structured actions, canonical execution commands, completion-bound booking/contact pending intent, supported sales profile fields and the canonical Digital Manager persona.')
