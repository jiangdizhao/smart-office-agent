import { realtimeAgent, type VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VisitLease } from '../vision/visitLeaseRegistry'
import type { SalesReplyPlan } from './salesConversationClient'

export type SalesUiResult = {
  attempted: boolean
  kind: 'meeting' | 'contact' | null
  ok: boolean
  message: string
}

function cleanFallback(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function salesInstructions(input: {
  userText: string
  language: VoiceLanguage
  plan: SalesReplyPlan
  fallbackText: string
  uiResult?: SalesUiResult | null
}): string {
  const planJson = JSON.stringify(input.plan)
  const uiJson = JSON.stringify(input.uiResult ?? null)
  if (input.language === 'en') {
    return `
You are Sara, the Smart Office Digital Manager and Enterprise Solution Consultant.
Render the approved Sales Reply Plan below as natural spoken English.

HARD RULES:
1. Use only facts in visitor_context, verified_facts and approved_claims. Never infer the visitor's age, nationality, income, authority, budget, personality, emotion or purchasing power.
2. Never claim an Office action, booking, registration, email, file, presentation, device change or integration succeeded unless verified_facts or UI_RESULT explicitly confirms it.
3. Follow prohibited_claims literally.
4. Ask at most one question, and only use suggested_question. If it is null, do not ask a new question.
5. Do not exceed maximum_sentences.
6. If humour.allowed is true, either include humour.text exactly once or omit it. Never paraphrase it and never invent another joke. If humour.allowed is false, use no humour.
7. Keep appointment-first wording. Do not offer an on-site product demonstration unless recommended_action explicitly delegates an allowlisted action.
8. If UI_RESULT says attempted=true and ok=false, clearly state that the panel did not open. Do not claim success.
9. If reply_mode is closing, do not add another question, booking offer or contact request.
10. Return plain text only, without labels, JSON, Markdown or quotation marks.

SALES_REPLY_PLAN:
${planJson}

UI_RESULT:
${uiJson}

CURRENT_VISITOR_MESSAGE:
${input.userText || '(proactive turn)'}

DETERMINISTIC_FALLBACK_FOR_MEANING:
${input.fallbackText}
`.trim()
  }
  return `
你是 Sara，Smart Office 数字管理员与企业解决方案顾问。
请把下面已经批准的 Sales Reply Plan 表达成自然、成熟、适合朗读的中文。

硬性规则：
1. 只能使用 visitor_context、verified_facts 和 approved_claims 中的事实。不得推断访客的年龄、国籍、收入、决策权、预算、性格、情绪或购买力。
2. 除非 verified_facts 或 UI_RESULT 明确确认成功，否则不得声称已经完成 Office 操作、预约、登记、邮件、文件、PowerPoint、设备修改或系统集成。
3. 必须严格遵守 prohibited_claims。
4. 每次最多问一个问题，而且只能使用 suggested_question；该字段为空时，不得新增问题。
5. 不得超过 maximum_sentences。
6. humour.allowed=true 时，只能原样使用 humour.text 一次，或者完全不用；不得改写，也不得自己编笑话。humour.allowed=false 时不得加入幽默。
7. 必须遵守预约优先策略。除非 recommended_action 明确委托白名单现场动作，否则不得承诺现场演示产品功能。
8. UI_RESULT 显示 attempted=true 且 ok=false 时，必须明确说明面板没有打开，不能宣称成功。
9. reply_mode=closing 时，不得新增问题、预约邀请或联系方式邀请。
10. 只输出最终口语文本，不要输出标签、JSON、Markdown 或引号。

SALES_REPLY_PLAN：
${planJson}

UI_RESULT：
${uiJson}

当前访客的话：
${input.userText || '（主动销售回合）'}

确定性后备文本所表达的含义：
${input.fallbackText}
`.trim()
}

export async function renderSalesReply(input: {
  userText: string
  language: VoiceLanguage
  plan: SalesReplyPlan | null
  fallbackText: string
  lease: VisitLease | null
  uiResult?: SalesUiResult | null
}): Promise<string> {
  const fallback = cleanFallback(input.fallbackText)
  if (!input.plan) {
    if (!fallback) throw new Error('The sales runtime returned neither a Reply Plan nor fallback text.')
    return fallback
  }
  try {
    const answer = (
      await realtimeAgent.generateText(
        salesInstructions({
          userText: input.userText,
          language: input.language,
          plan: input.plan,
          fallbackText: fallback,
          uiResult: input.uiResult,
        }),
        input.language,
        input.plan.reply_mode === 'proactive_sales'
          ? 'proactive_sales_reply'
          : 'sales_reply_render',
        input.lease?.signal,
      )
    ).replace(/\s+/g, ' ').trim()
    if (answer) return answer
  } catch (error) {
    if (input.lease?.signal.aborted) throw error
    console.error('[SalesRuntime] realtime-sales-render-failed', {
      message: error instanceof Error ? error.message : String(error),
      visitId: input.lease?.visitId ?? null,
      stage: input.plan.sales_stage,
      goal: input.plan.goal,
    })
  }
  if (!fallback) throw new Error('GPT Realtime returned no sales answer and no fallback text is available.')
  return fallback
}
