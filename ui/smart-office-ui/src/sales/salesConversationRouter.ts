import {
  openInteractionWindow,
  type InteractionWindowKind,
} from '../display/multiScreenWindowManager'
import {
  generateSimpleRealtimeAnswer as generateBaseRealtimeAnswer,
  previewConversationRoute as previewBaseConversationRoute,
  type FastConversationRoute,
} from '../voice/fastConversationRouterCore'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VisitLease } from '../vision/visitLeaseRegistry'
import {
  previewSalesTurn,
  reportSalesConversionEvent,
  type SalesTurnResponse,
} from './salesConversationClient'
import { renderSalesReply, type SalesUiResult } from './salesReplyRenderer'

const SALES_CONTEXT_PREFIX = '__SMART_OFFICE_SALES_CONTEXT__:'

export type SalesAwareConversationRoute = FastConversationRoute & {
  office_delegate_text?: string | null
}

type SalesRouteContext = {
  turn: SalesTurnResponse
  uiResult: SalesUiResult | null
}

type RouteRequest = {
  conversationId: string
  visitId: string | null
  text: string
  language: VoiceLanguage
  actor: 'visitor' | 'employee' | 'operator'
  lease: VisitLease | null
}

function salesContext(context: SalesRouteContext): string {
  return `${SALES_CONTEXT_PREFIX}${JSON.stringify(context)}`
}

function parseSalesContext(value: string): SalesRouteContext | null {
  if (!value.startsWith(SALES_CONTEXT_PREFIX)) return null
  try {
    const parsed = JSON.parse(value.slice(SALES_CONTEXT_PREFIX.length)) as SalesRouteContext
    if (!parsed?.turn?.session || !parsed.turn.reason) return null
    return parsed
  } catch {
    return null
  }
}

function conversionEvent(
  kind: InteractionWindowKind,
  ok: boolean,
): 'booking_opened' | 'booking_failed' | 'contact_opened' | 'contact_failed' | null {
  if (kind === 'meeting') return ok ? 'booking_opened' : 'booking_failed'
  if (kind === 'contact') return ok ? 'contact_opened' : 'contact_failed'
  return null
}

async function executeSalesUiAction(
  turn: SalesTurnResponse,
  request: RouteRequest,
): Promise<SalesUiResult | null> {
  if (!turn.ui_action || !request.visitId) return null
  const kind: InteractionWindowKind = turn.ui_action === 'open_booking' ? 'meeting' : 'contact'
  try {
    const result = await openInteractionWindow({
      kind,
      conversationId: request.conversationId,
      visitId: request.visitId,
      language: request.language,
    })
    const event = conversionEvent(kind, result.ok)
    if (event) {
      await reportSalesConversionEvent({
        conversationId: request.conversationId,
        visitId: request.visitId,
        event,
        lease: request.lease,
      }).catch((error) => {
        console.error('[SalesRuntime] conversion-event-report-failed', {
          event,
          message: error instanceof Error ? error.message : String(error),
          visitId: request.visitId,
        })
      })
    }
    return {
      attempted: true,
      kind,
      ok: result.ok,
      message: result.message,
    }
  } catch (error) {
    const event = conversionEvent(kind, false)
    if (event) {
      await reportSalesConversionEvent({
        conversationId: request.conversationId,
        visitId: request.visitId,
        event,
        lease: request.lease,
      }).catch(() => undefined)
    }
    return {
      attempted: true,
      kind,
      ok: false,
      message: error instanceof Error ? error.message : String(error),
    }
  }
}

function delegatedOfficeText(turn: SalesTurnResponse): string | null {
  const action = turn.reply_plan?.recommended_action?.trim() ?? ''
  if (!action.startsWith('delegate:')) return null
  const text = action.slice('delegate:'.length).trim()
  return text || null
}

function uiFailureFallback(
  context: SalesRouteContext,
  language: VoiceLanguage,
): string {
  if (!context.uiResult?.attempted || context.uiResult.ok) return context.turn.fallback_text
  const label = context.uiResult.kind === 'meeting'
    ? language === 'zh' ? '会议预约日历' : 'meeting-booking calendar'
    : language === 'zh' ? '登记信息表' : 'visitor-registration form'
  return language === 'zh'
    ? `${label}没有成功打开。请刷新主屏幕后再试一次。`
    : `The ${label} did not open. Please refresh the main display and try again.`
}

export async function previewConversationRoute(
  request: RouteRequest,
): Promise<SalesAwareConversationRoute> {
  const base = await previewBaseConversationRoute(request)

  // The exhibition UI uses operator for Office permissions. Operator therefore
  // remains a visitor conversation for sales, while employee explicitly bypasses it.
  if (
    request.actor === 'employee'
    || !request.visitId
    || base.route_reason === 'interaction_panel_command'
    || base.route_reason.startsWith('deterministic_system_action:')
  ) {
    return base
  }

  try {
    const turn = await previewSalesTurn({
      conversationId: request.conversationId,
      visitId: request.visitId,
      text: request.text,
      language: request.language,
      actor: 'visitor',
      recentContext: base.recent_context,
      lease: request.lease,
    })

    const delegateText = delegatedOfficeText(turn)
    if (!turn.handled && delegateText) {
      console.info('[SalesRuntime] allowlisted-demo-delegated', {
        reason: turn.reason,
        delegateText,
        visitId: request.visitId,
      })
      return {
        ...base,
        route: 'office_direct',
        scene: 'office',
        route_reason: turn.reason,
        conversation_complexity: 'not_applicable',
        answer_engine: 'office_interpreter',
        office_delegate_text: delegateText,
      }
    }

    if (!turn.handled) return base

    const uiResult = await executeSalesUiAction(turn, request)
    console.info('[SalesRuntime] sales-turn-routed', {
      reason: turn.reason,
      stage: turn.session.stage,
      uiAction: turn.ui_action,
      uiOk: uiResult?.ok ?? null,
      profilePersisted: turn.profile_persisted,
      visitId: request.visitId,
    })
    return {
      route: 'sales_realtime',
      scene: 'reception',
      route_reason: turn.reason,
      conversation_complexity: 'simple',
      answer_engine: 'realtime',
      recent_context: salesContext({ turn, uiResult }),
      visit_id: request.visitId,
      office_delegate_text: null,
    }
  } catch (error) {
    if (request.lease?.signal.aborted) throw error
    console.error('[SalesRuntime] sales-route-failed-open', {
      message: error instanceof Error ? error.message : String(error),
      visitId: request.visitId,
      baseRoute: base.route,
    })
    return base
  }
}

export async function generateSimpleRealtimeAnswer(
  text: string,
  language: VoiceLanguage,
  recentContext: string,
  lease: VisitLease | null,
): Promise<string> {
  const sales = parseSalesContext(recentContext)
  if (!sales) {
    return await generateBaseRealtimeAnswer(text, language, recentContext, lease)
  }
  return await renderSalesReply({
    userText: text,
    language,
    plan: sales.turn.reply_plan,
    fallbackText: uiFailureFallback(sales, language),
    lease,
    uiResult: sales.uiResult,
  })
}
