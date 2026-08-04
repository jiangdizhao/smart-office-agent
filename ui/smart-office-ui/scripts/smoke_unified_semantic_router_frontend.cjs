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
requireText(sales, 'setSemanticPendingIntent', 'Structured pending conversion intent')
forbidText(sales, 'matchesSelfIntroduction', 'Sales route')

requireText(proactive, "response.reason === 'contextual_booking_offer'", 'Proactive booking metadata gate')
requireText(proactive, "intentType: 'booking_offer'", 'Proactive structured pending booking')
requireText(proactive, 'clearSemanticPendingIntent', 'Interrupted proactive pending cancellation')
requireText(proactive, 'proactive_offer_interrupted', 'Interrupted offer cleanup')
requireText(proactive, 'proactive_offer_failed', 'Failed offer cleanup')

requireText(client, '/api/semantic-route', 'Unified router endpoint')
requireText(client, '/api/semantic-route/pending', 'Pending intent endpoint')
requireText(client, "method: 'DELETE'", 'Pending intent cancellation endpoint')
requireText(client, '__SMART_OFFICE_SEMANTIC_ROUTE__', 'Operator diagnostics')
requireText(client, 'flattenSemanticProfile', 'Profile evidence bridge')
requireText(client, 'SUPPORTED_SALES_PROFILE_FIELDS', 'Deterministic profile field allowlist')
requireText(client, '.filter(([key]) => SUPPORTED_SALES_PROFILE_FIELDS.has(key))', 'Unsupported profile filtering')

console.log('PASS: frontend uses one semantic route, policy-gated structured actions, canonical execution commands, normal and proactive pending intent, supported sales profile fields and the canonical Digital Manager persona.')
