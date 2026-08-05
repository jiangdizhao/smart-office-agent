const fs = require('node:fs')
const path = require('node:path')

function read(relative) {
  return fs.readFileSync(path.resolve(__dirname, '..', relative), 'utf8')
}

function requireText(source, token, message) {
  if (!source.includes(token)) throw new Error(message || `Missing required token: ${token}`)
}

function forbidText(source, token, message) {
  if (source.includes(token)) throw new Error(message || `Forbidden token present: ${token}`)
}

const preemptive = read('src/voice/preemptiveTurnCoordinator.ts')
requireText(preemptive, "smartoffice:realtime-vad-speech-started", 'Barge-in VAD listener must remain installed.')
requireText(preemptive, "voiceOutputManager.stop(reason)", 'Barge-in must retain highest-priority speech cancellation.')
requireText(preemptive, "realtimeAgent.stopOutput()", 'Realtime output must still stop immediately on barge-in.')
requireText(preemptive, "backgroundTaskPreserved: true", 'Generic barge-in must explicitly preserve accepted tasks.')
requireText(preemptive, "smartoffice:background-task-preserved-during-barge-in", 'Task-preservation diagnostics must remain observable.')
forbidText(preemptive, "/agent/tasks/${encodeURIComponent(taskId)}/cancel", 'VAD barge-in must never call the task cancellation endpoint.')

const fastRouter = read('src/voice/fastConversationRouter.ts')
requireText(fastRouter, 'startManagedBackgroundAction', 'Teams, OneNote and music must use nonblocking background actions.')
requireText(fastRouter, 'direct_scoped_volume_action', 'Scoped volume must remain independent of long Office tasks.')
requireText(fastRouter, 'local_conservative_non_action_fast_path', 'Conservative local conversation bypass must remain available.')
requireText(fastRouter, '比较|对比', 'Comparison language must be excluded from action execution fast paths.')
requireText(fastRouter, '百分之', 'Chinese percentage phrasing must remain supported.')

const semanticBridge = read('src/voice/semanticOfficeInterpreterBridge.ts')
requireText(semanticBridge, 'semantic-office-plan-reused', 'Unified semantic actions must bypass duplicate Office interpretation when safe.')
requireText(semanticBridge, "steps: [first, { name: 'presentation_get_status' }]", 'Slow PowerPoint actions must be promoted to verified background plans.')
requireText(semanticBridge, 'installConversationWriteBehind()', 'Conversation metadata writes must remain off the first-response path.')
requireText(semanticBridge, 'installOfficeTaskEventObserver()', 'Office task observation must remain SSE-backed.')

const taskObserver = read('src/voice/officeTaskEventObserver.ts')
requireText(taskObserver, 'new EventSource', 'Office task monitoring must use the Backend SSE event stream.')
requireText(taskObserver, 'X-Smart-Office-Task-Cache', 'Legacy polling must be served from the SSE-updated cache when fresh.')
requireText(taskObserver, "'verification_result'", 'Verification events must refresh the shared task snapshot.')

const writeBehind = read('src/voice/conversationWriteBehind.ts')
requireText(writeBehind, 'const queues = new Map', 'Conversation writes must retain per-conversation ordering.')
requireText(writeBehind, 'X-Smart-Office-Write-Behind', 'Write-behind acceptance must be traceable.')
requireText(writeBehind, 'turn-start|turn-complete|task-state', 'Only noncritical conversation state endpoints may be write-behind.')

const background = read('src/voice/backgroundTaskRuntime.ts')
requireText(background, 'conversationBusy', 'Background completion announcements must not interrupt active conversation.')
requireText(background, 'background_task_verified', 'Verified background completion must retain output lifecycle reporting.')
requireText(background, 'smartoffice:assistant-output-completed', 'Background notification scheduling must respect assistant output lifecycle.')

const phase2b = read('src/sales/salesPhase2BEngagementOrchestrator.ts')
requireText(phase2b, 'DEFAULT_FIRST_NUDGE_MS = 8_000', 'First silent progression timing must remain intact.')
requireText(phase2b, 'DEFAULT_SECOND_NUDGE_MS = 10_000', 'Second silent progression timing must remain intact.')
requireText(phase2b, "smartoffice:assistant-output-completed", 'Phase 2B must continue rearming from completed output.')
requireText(phase2b, "smartoffice:assistant-output-interrupted", 'Phase 2B must continue handling interrupted output.')
requireText(phase2b, 'rearmBusy', 'Busy conditions must defer rather than destroy proactive progression.')

console.log('Latency optimization frontend contract passed.')
