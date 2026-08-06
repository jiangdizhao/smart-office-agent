import { useCallback, useRef } from 'react'
import {
  openInteractionWindow,
  type InteractionWindowKind,
} from '../display/multiScreenWindowManager'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
import type { OfficeVoiceController } from './useOfficeVoiceController'

const CONTACT_INTENT = /(?:登记信息|登记资料|访客登记|注册信息|填写(?:联系|联络)方式|留下(?:联系|联络)方式|留(?:下)?(?:电话|邮箱|联系方式)|registration|visitor\s+registration|register(?:\s+me|\s+visitor)?|leave\s+(?:my\s+)?contact\s+details?|contact\s+details?)/i
const MEETING_INTENT = /(?:预约会议|会议预约|预约见面|预约会面|安排会议|预定会议|约个会议|book\s+(?:a\s+)?meeting|schedule\s+(?:a\s+)?meeting|meeting\s+booking|book\s+(?:an\s+)?appointment|schedule\s+(?:an\s+)?appointment)/i
const RECORDING_INTENT = /(?:实时录音|现场录音|开始录音|打开录音|录制(?:现场|对话|谈话|会议)|live\s+recording|start\s+(?:the\s+)?recording|record\s+(?:this|the|our)?\s*(?:conversation|meeting|session)|open\s+(?:the\s+)?recording)/i
const SUMMARY_INTENT = /(?:对话总结|会话总结|本次总结|总结(?:本次|这次)?(?:对话|会话|交流)|查看(?:对话|会话|本次)总结|session\s+summary|conversation\s+summary|summari[sz]e\s+(?:this|the|our)?\s*(?:conversation|session))/i
const RESULTS_INTENT = /(?:结果中心|查看结果|打开结果|访客档案|访客记录|录音文件|result\s+center|open\s+(?:the\s+)?results?|visitor\s+(?:profiles?|records?)|recording\s+files?)/i

type ForcedInteractionIntent = {
  kind: InteractionWindowKind
  route: string
  confirmationZh: string
  confirmationEn: string
}

function resolveForcedInteraction(text: string): ForcedInteractionIntent | null {
  if (CONTACT_INTENT.test(text)) {
    return {
      kind: 'contact',
      route: 'forced_interaction_registration',
      confirmationZh: '已打开登记信息。',
      confirmationEn: 'Registration is open.',
    }
  }
  if (MEETING_INTENT.test(text)) {
    return {
      kind: 'meeting',
      route: 'forced_interaction_meeting',
      confirmationZh: '已打开预约会议。',
      confirmationEn: 'Meeting booking is open.',
    }
  }
  if (RECORDING_INTENT.test(text)) {
    return {
      kind: 'recording',
      route: 'forced_interaction_recording',
      confirmationZh: '已打开实时录音。',
      confirmationEn: 'Live recording is open.',
    }
  }
  if (SUMMARY_INTENT.test(text)) {
    return {
      kind: 'transcript',
      route: 'forced_interaction_session_summary',
      confirmationZh: '已打开对话总结。',
      confirmationEn: 'The session summary is open.',
    }
  }
  if (RESULTS_INTENT.test(text)) {
    return {
      kind: 'results',
      route: 'forced_interaction_result_center',
      confirmationZh: '已打开结果中心。',
      confirmationEn: 'The result center is open.',
    }
  }
  return null
}

function directCaption(text: string, route: string): void {
  window.dispatchEvent(new CustomEvent('smartoffice:direct-assistant-caption', {
    detail: { text, route },
  }))
}

/**
 * Highest-priority deterministic gate for the five Touch Services shown beside
 * Sara. Matching commands never reach presentation Q&A, the semantic router,
 * Office Interpreter or general chat.
 */
export function useForcedInteractionServiceController(
  controller: OfficeVoiceController,
): OfficeVoiceController {
  const controllerRef = useRef(controller)
  controllerRef.current = controller

  const submit = useCallback(async (
    text: string,
    source: 'text' | 'voice' = 'text',
  ): Promise<void> => {
    const clean = text.normalize('NFKC').replace(/\s+/g, ' ').trim()
    const intent = resolveForcedInteraction(clean)
    if (!intent) {
      await controllerRef.current.submit(text, source)
      return
    }

    // This path is intentionally model-free and permission-free. Stop any old
    // narration first, then open the requested service immediately.
    await controllerRef.current.stopSpeaking().catch(() => undefined)
    const language = controllerRef.current.language
    const result = await openInteractionWindow({
      kind: intent.kind,
      conversationId: controllerRef.current.conversationId,
      visitId: visitLeaseRegistry.current()?.visitId ?? null,
      language,
    })
    const confirmation = result.ok
      ? language === 'zh' ? intent.confirmationZh : intent.confirmationEn
      : language === 'zh' ? '访客服务页面暂时未能打开。' : 'The visitor service panel could not be opened.'
    directCaption(confirmation, intent.route)
    window.dispatchEvent(new CustomEvent('smartoffice:forced-interaction-service', {
      detail: {
        kind: intent.kind,
        route: intent.route,
        source,
        transcript: clean,
        ok: result.ok,
      },
    }))
  }, [])

  return {
    ...controller,
    submit,
  }
}
