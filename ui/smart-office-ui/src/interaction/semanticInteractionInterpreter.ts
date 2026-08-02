import type { InteractionWindowKind } from '../display/multiScreenWindowManager'
import {
  realtimeAgent,
  type VoiceLanguage,
} from '../voice/realtimeAgentRuntime'

export type SemanticInteractionIntent = InteractionWindowKind | 'none'

export type SemanticInteractionDecision = {
  intent: SemanticInteractionIntent
  confidence: number
  source: 'local_semantic' | 'gpt_realtime'
}

const INTENTS = new Set<SemanticInteractionIntent>([
  'contact',
  'recording',
  'transcript',
  'results',
  'none',
])

function localSemanticDecision(text: string): SemanticInteractionDecision | null {
  const clean = text.trim().toLocaleLowerCase()
  if (!clean) return null

  if (
    /(我想|我要|希望|可以|能否|能不能|请|帮我|麻烦).{0,10}(登记|注册|留资料|留信息|留下.{0,5}联系方式|填写.{0,5}资料)|(?:把|将).{0,8}(我的|个人|联系).{0,8}(信息|资料|联系方式).{0,8}(给|留给|登记)|\b(?:i want to|i'd like to|let me|can i|help me)\b.{0,35}\b(?:register|leave my details|share my contact details|sign up)\b/i.test(clean)
  ) {
    return { intent: 'contact', confidence: 0.97, source: 'local_semantic' }
  }

  if (
    /(帮|请|可以|能否|能不能|我想|我要).{0,10}(录一下|录下来|开始录音|录制|记录.{0,5}对话)|(?:把|将).{0,8}(接下来的|我们的|这段|现场).{0,8}(谈话|对话).{0,8}(录下来|录制|记录)|\b(?:record this|record our conversation|start recording|capture this conversation)\b/i.test(clean)
  ) {
    return { intent: 'recording', confidence: 0.96, source: 'local_semantic' }
  }

  if (
    /(刚才|之前|前面|这次|当前).{0,10}(我们说了什么|谈话|对话|聊天|会话).{0,10}(看看|查看|显示|打开|内容|记录)?|(?:让我|我想|我要|请|帮我).{0,8}(看看|查看|显示).{0,8}(刚才|当前|这次).{0,8}(对话|聊天|会话|谈话)|\b(?:show|view|see)\b.{0,35}\b(?:what we said|current conversation|chat transcript|conversation transcript)\b/i.test(clean)
  ) {
    return { intent: 'transcript', confidence: 0.94, source: 'local_semantic' }
  }

  if (
    /(我想|我要|请|帮我|让我|可以|能否).{0,10}(查看|看看|打开|显示).{0,10}(保存的资料|登记结果|已登记|客户资料|联系人|录音文件|收集的结果|结果)|(?:录音|登记资料|客户资料).{0,8}(保存在哪|在哪里|列表|结果)|\b(?:show|view|open|see)\b.{0,35}\b(?:saved results|registered visitors|contact records|recording files|result center)\b/i.test(clean)
  ) {
    return { intent: 'results', confidence: 0.95, source: 'local_semantic' }
  }

  return null
}

function parseDecision(value: string): SemanticInteractionDecision | null {
  const clean = value
    .trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/i, '')
  try {
    const payload = JSON.parse(clean) as {
      intent?: unknown
      confidence?: unknown
    }
    const intent = String(payload.intent ?? '').trim() as SemanticInteractionIntent
    if (!INTENTS.has(intent)) return null
    const confidenceValue = Number(payload.confidence)
    const confidence = Number.isFinite(confidenceValue)
      ? Math.max(0, Math.min(1, confidenceValue))
      : 0
    return { intent, confidence, source: 'gpt_realtime' }
  } catch {
    return null
  }
}

function classifierInstructions(text: string, language: VoiceLanguage): string {
  return `
You are a bounded intent classifier for the Smart Office visitor-service panel.
Classify whether the user is asking the application to open one interface now.
Return exactly one JSON object with two keys:
{"intent":"contact|recording|transcript|results|none","confidence":0.0}

Definitions:
- contact: the user wants to register, sign up, leave personal/contact details, or fill in visitor information.
- recording: the user wants to start or open live recording of the people in the room.
- transcript: the user wants to view the current conversation/session transcript.
- results: the user wants to view saved contact records, saved recordings, collected visitor data, or the result center. This opens an administrator-password screen, never the data directly.
- none: ordinary conversation, questions about what a feature means, Office application control, or any request not clearly asking to open one of these interfaces.

Rules:
- Infer meaning rather than requiring exact keywords. “我想登记” is contact.
- Classify an action request, not a mere mention. “登记信息是什么” is none.
- Do not invent an action when the utterance is ambiguous.
- Product names and mixed Chinese-English speech do not change these definitions.
- The active visitor language is ${language === 'zh' ? 'Chinese' : 'English'}.
- Output JSON only. No Markdown, explanation, translation, or extra keys.

User utterance:
${JSON.stringify(text)}
`.trim()
}

export async function resolveSemanticInteractionIntent(
  text: string,
  language: VoiceLanguage,
  signal?: AbortSignal,
): Promise<SemanticInteractionDecision> {
  const local = localSemanticDecision(text)
  if (local) return local

  try {
    const generated = await realtimeAgent.generateText(
      classifierInstructions(text, language),
      language,
      'visitor_service_intent_classification',
      signal,
    )
    const parsed = parseDecision(generated)
    if (parsed) {
      console.info('[InteractionIntent] semantic-classification', {
        intent: parsed.intent,
        confidence: parsed.confidence,
        source: parsed.source,
      })
      return parsed
    }
  } catch (error) {
    if (signal?.aborted) throw error
    console.warn('[InteractionIntent] semantic-classification-failed', {
      error: error instanceof Error ? error.message : String(error),
    })
  }

  return { intent: 'none', confidence: 0, source: 'gpt_realtime' }
}
