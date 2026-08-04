import { strict as assert } from 'node:assert'
import {
  commandClarification,
  recoverCommandTranscript,
} from '../src/voice/commandTranscriptRepair'
import { deterministicPresentationSteps } from '../src/voice/presentationCommandPlan'

function names(steps: Array<Record<string, unknown>> | null): string[] | null {
  return steps?.map((step) => String(step.name ?? '')) ?? null
}

function assertPreserved(text: string): void {
  const repaired = recoverCommandTranscript(text, 'zh')
  assert.equal(repaired.normalized, text, `compound intent changed: ${text}`)
  assert.equal(repaired.target, null, `compound intent exposed a single target: ${text}`)
  assert.equal(repaired.action, null, `compound intent exposed a single action: ${text}`)
  assert.equal(repaired.ambiguous, false, `compound intent became ambiguous: ${text}`)
  assert.equal(commandClarification(repaired), null, `compound intent requested clarification: ${text}`)
}

for (const text of [
  '请打开并演示 PPT',
  '演示 PPT',
  '打开 PPT 然后开始放映',
  '打开 PPT 并跳到第五页',
  '打开 PowerPoint 然后关闭',
  '打开 Teams，然后打开 OneNote',
  '打开 Outlook，并给 Rico 创建会议总结草稿',
  '播放音乐，然后把音量调到 30%',
  '关闭音乐，并把系统音量恢复到 50%',
]) {
  assertPreserved(text)
}

const boundedOpen = recoverCommandTranscript('请打开 PPT', 'zh')
assert.equal(boundedOpen.normalized, '打开 PowerPoint')
assert.equal(boundedOpen.target, 'powerpoint')
assert.equal(boundedOpen.action, 'open')
assert.equal(boundedOpen.ambiguous, false)

const phoneticClose = recoverCommandTranscript('one B Teams', 'zh')
assert.equal(phoneticClose.normalized, '关闭 Teams')
assert.equal(phoneticClose.target, 'teams')
assert.equal(phoneticClose.action, 'close')

const bareApp = recoverCommandTranscript('PowerPoint', 'zh')
assert.equal(bareApp.ambiguous, true)
assert.match(commandClarification(bareApp) ?? '', /打开还是关闭/)

const capabilityQuestion = recoverCommandTranscript('PowerPoint 是什么', 'zh')
assert.equal(capabilityQuestion.normalized, 'PowerPoint 是什么')
assert.equal(capabilityQuestion.ambiguous, false)
assert.equal(commandClarification(capabilityQuestion), null)

assert.deepEqual(
  names(deterministicPresentationSteps('请打开并演示 PPT')),
  ['presentation_open_configured', 'presentation_start_slideshow'],
)
assert.deepEqual(
  names(deterministicPresentationSteps('演示 PPT')),
  ['presentation_open_configured', 'presentation_start_slideshow'],
)
assert.deepEqual(
  names(deterministicPresentationSteps('打开 PPT 然后开始放映')),
  ['presentation_open_configured', 'presentation_start_slideshow'],
)
assert.deepEqual(
  names(deterministicPresentationSteps('开始幻灯片放映')),
  ['presentation_start_slideshow'],
)
assert.deepEqual(
  names(deterministicPresentationSteps('下一页')),
  ['presentation_next_slide'],
)
assert.deepEqual(
  names(deterministicPresentationSteps('上一页')),
  ['presentation_previous_slide'],
)
assert.deepEqual(
  names(deterministicPresentationSteps('停止放映')),
  ['presentation_end_slideshow'],
)
assert.equal(deterministicPresentationSteps('PowerPoint 是什么'), null)
assert.equal(deterministicPresentationSteps('打开 PPT 并跳到第五页'), null)
assert.equal(deterministicPresentationSteps('打开 PPT 然后下一页'), null)
assert.equal(deterministicPresentationSteps('打开 PPT 并把音量调到 30%'), null)

console.log(
  'PASS: lossless transcript repair preserves compound intents; bounded ASR recovery remains active; complete PowerPoint demonstration commands resolve before generic clarification; rich multi-action and cross-domain requests defer intact to the unified planner.',
)
