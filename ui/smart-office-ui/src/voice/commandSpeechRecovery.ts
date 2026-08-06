import {
  realtimeAgent,
  type VoiceLanguage,
} from './realtimeAgentRuntime'
import { recoverCommandTranscript } from './commandTranscriptRepair'

export {
  commandClarification,
  recoverCommandTranscript,
  type CommandAction,
  type CommandTarget,
  type RecoveredCommandTranscript,
} from './commandTranscriptRepair'

let installed = false
let activeLanguage: VoiceLanguage = 'zh'

function transcriptionPrompt(language: VoiceLanguage): string {
  const languageRule = language === 'zh'
    ? `The active visitor language is Chinese. Treat Chinese as a strong prior. Application names such as Teams, OneNote, PowerPoint, Outlook and PPT may be spoken in English, but nearby action words are normally Chinese. Do not switch the whole utterance to English merely because an application name is English. In particular, Chinese “关闭” (guan bi) must not be rendered as “one B”, “one bee”, “1B”, “wan bi” or similar English-looking text; normalize it to 关闭. Chinese “打开” (da kai) must not be rendered as unrelated English words; normalize it to 打开. A clear full English command such as “close Teams” may remain English.`
    : `The active visitor language is English. Preserve genuine Chinese words when they are clearly spoken. A clear Chinese command such as “关闭 Teams” may remain Chinese.`
  return `
You are a multilingual speech transcription and command-correction layer, not a conversational assistant.
Return only the user's final intended utterance as normalized plain text. Do not answer the user.
${languageRule}
Preserve genuine Chinese-English code-switching and keep Microsoft product names in their conventional form.
Preserve every requested action in its original order. Never simplify a compound request into one action.
Never omit application names, slide numbers, percentages, dates, times, people, recipients, approval wording, or the user's stated purpose.
For example, “打开并演示 PPT” must remain a compound open-and-demonstrate request; it must not become only “打开 PowerPoint”.
Treat “最后一页”, “末页”, “最后一张”, “final slide” and “last slide” as final-slide navigation. Never rewrite them as “下一页”, “后一页”, “next slide” or “continue”.
Keep “总结当前页” distinct from “总结整份 PPT”, “总结 PPT 内容” and “summarize the complete presentation”. The latter expressions refer to the whole deck, not only the current slide.
Use the active visitor language as the response-language prior; do not infer a language switch from a product name alone.
Later explicit corrections override earlier uncertain words.
Relevant commands include:
打开 Teams / 关闭 Teams / open Teams / close Teams
打开 OneNote / 关闭 OneNote / open OneNote / close OneNote
打开 PowerPoint / 关闭 PowerPoint / open PowerPoint / close PowerPoint
打开并演示 PowerPoint / 开始幻灯片放映 / 下一页 / 上一页 / 最后一页
总结当前页 / 解释当前页 / 总结整份 PPT
打开 Outlook / 关闭 Outlook / open Outlook / close Outlook
播放音乐 / 关闭音乐 / play music / stop music
Relevant terms also include meeting, presentation, mute, camera, next slide, previous slide, final slide, whole-deck summary, and screen sharing.
Never invent a request. If the complete utterance is genuinely unintelligible, output exactly __UNCLEAR__.
Output only normalized plain text without labels, JSON, Markdown, quotation marks, explanations, or translations.
`.trim()
}

function repairCapturedTranscript(transcript: string): string {
  const repaired = recoverCommandTranscript(transcript, activeLanguage)
  if (repaired.recovered) {
    console.info('[RealtimeDiagnostics] bounded-command-transcript-recovered', {
      raw: repaired.raw,
      normalized: repaired.normalized,
      target: repaired.target,
      action: repaired.action,
      visitLanguage: activeLanguage,
      commandLanguage: repaired.language,
    })
  }
  return repaired.normalized
}

export function installCommandSpeechRecovery(): void {
  if (installed) return
  installed = true

  const originalPrewarm = realtimeAgent.prewarm.bind(realtimeAgent)
  realtimeAgent.prewarm = async (language, signal) => {
    activeLanguage = language
    return await originalPrewarm(language, signal)
  }

  const originalBeginCapture = realtimeAgent.beginCapture.bind(realtimeAgent)
  realtimeAgent.beginCapture = async (language, signal) => {
    activeLanguage = language
    return await originalBeginCapture(language, signal)
  }

  const originalStartContinuousCapture = realtimeAgent.startContinuousCapture.bind(realtimeAgent)
  realtimeAgent.startContinuousCapture = async (language, signal) => {
    activeLanguage = language
    return await originalStartContinuousCapture(language, signal)
  }

  realtimeAgent.transcriptionInstructions = () => transcriptionPrompt(activeLanguage)

  const originalNextContinuousUtterance = realtimeAgent.nextContinuousUtterance.bind(realtimeAgent)
  realtimeAgent.nextContinuousUtterance = async (signal) => {
    const transcript = await originalNextContinuousUtterance(signal)
    return repairCapturedTranscript(transcript)
  }

  const originalEndCapture = realtimeAgent.endCapture.bind(realtimeAgent)
  realtimeAgent.endCapture = async (signal) => {
    const transcript = await originalEndCapture(signal)
    return repairCapturedTranscript(transcript)
  }
}

installCommandSpeechRecovery()
