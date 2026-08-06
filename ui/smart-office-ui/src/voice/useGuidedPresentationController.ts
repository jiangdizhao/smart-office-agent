import { useCallback, useEffect, useRef } from 'react'
import { openInteractionWindow } from '../display/multiScreenWindowManager'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
import { registerVoiceOutputContext } from '../sales/voiceDelivery'
import type { VoiceLanguage } from './realtimeAgentRuntime'
import {
  OFFICE_API_BASE,
  type OfficeVoiceController,
} from './useOfficeVoiceController'
import { voiceOutputManager } from './voiceOutputManager'

type SessionState =
  | 'inactive'
  | 'cover_waiting'
  | 'slide_narrating'
  | 'slide_waiting'
  | 'answering_question'
  | 'opening_registration'
  | 'ending'

type ScriptPayload = {
  ok: boolean
  source_path: string
  cover_intro: Record<VoiceLanguage, string>
  slides: Record<string, { narration: string; knowledge: string }>
}

type QuestionPayload = {
  ok: boolean
  related: boolean
  spoken_text: string
  open_registration: boolean
  model?: string | null
}

type PresentationStatusData = {
  current_slide?: number | null
  total_slides?: number | null
  slideshow_active?: boolean
}

type PresentationActionPayload = {
  ok?: boolean
  operation_id?: string
  status?: {
    ok?: boolean
    data?: PresentationStatusData
  }
}

const START_PRESENTATION = /(?:(?:体验|演示|介绍|讲解|展示|播放|开始).{0,16}(?:ppt|power\s*point|powerpoint|幻灯片|演示文稿)|(?:ppt|power\s*point|powerpoint|幻灯片|演示文稿).{0,16}(?:体验|演示|介绍|讲解|展示|播放|开始)|\b(?:demo|demonstrate|present|show|introduce|start)\b.{0,24}\b(?:ppt|powerpoint|presentation|slide\s*deck)\b)/i
const NEXT_SLIDE = /^(?:请|麻烦|帮我)?\s*(?:下一页|下一张|翻页|往后翻|向后翻|继续下一页|继续往下|继续播放|继续演示|next(?:\s+slide)?|continue|advance)\s*[。.!！]?$/i
const PREVIOUS_SLIDE = /^(?:请|麻烦|帮我)?\s*(?:上一页|上一张|往前翻|向前翻|返回上一页|previous(?:\s+slide)?|go\s+back)\s*[。.!！]?$/i
const PAUSE_PRESENTATION = /^(?:停一下|暂停|先停|别讲了|停止讲解|pause|stop speaking)\s*[。.!！]?$/i
const RESUME_PRESENTATION = /^(?:继续讲|继续介绍|接着讲|重新讲这一页|resume|continue speaking)\s*[。.!！]?$/i
const END_PRESENTATION = /^(?:结束演示|停止演示|退出演示|回到第一页|结束ppt|end presentation|stop presentation|exit presentation)\s*[。.!！]?$/i
const GOTO_SLIDE = /(?:跳到|翻到|切到|转到|前往)\s*第?\s*([一二三四\d]+)\s*(?:页|张)|\b(?:go|jump|move)\s+to\s+(?:slide\s+)?([1-4])\b/i
const END_OF_DECK_ZH = '当前幻灯片已经结束，想要更多的体验欢迎线下来我们展馆参观。'
const END_OF_DECK_EN = 'This presentation has ended. For more experiences, you are welcome to visit our exhibition booth in person.'

const CHINESE_NUMBERS: Record<string, number> = {
  一: 1,
  二: 2,
  三: 3,
  四: 4,
}

const TRANSIENT_STATE_BUDGET_MS: Partial<Record<SessionState, number>> = {
  slide_narrating: 65_000,
  answering_question: 18_000,
  opening_registration: 15_000,
  ending: 20_000,
}

let globalPresentationActive = false

export function guidedPresentationActive(): boolean {
  return globalPresentationActive
}

function requestedSlide(text: string): number | null {
  const match = text.match(GOTO_SLIDE)
  const token = match?.[1] ?? match?.[2]
  if (!token) return null
  if (/^\d+$/.test(token)) return Number(token)
  return CHINESE_NUMBERS[token] ?? null
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  options: { timeoutMs: number; signal?: AbortSignal },
): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs)
  const abortFromParent = () => controller.abort()
  options.signal?.addEventListener('abort', abortFromParent, { once: true })
  try {
    const response = await fetch(`${OFFICE_API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        ...(init.body === undefined ? {} : { 'Content-Type': 'application/json; charset=utf-8' }),
        ...(init.headers ?? {}),
      },
    })
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw new Error(`${path} failed: ${response.status}${detail ? ` ${detail}` : ''}`)
    }
    return await response.json() as T
  } catch (error) {
    if (controller.signal.aborted) throw abortError(`${path} was cancelled or exceeded ${options.timeoutMs} ms.`)
    throw error
  } finally {
    window.clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abortFromParent)
  }
}

function observedSlide(payload: PresentationActionPayload, fallback: number): number {
  const current = Number(payload.status?.data?.current_slide)
  return Number.isInteger(current) && current >= 1 && current <= 4 ? current : fallback
}

function sessionEvent(state: SessionState, slideNumber: number): void {
  window.dispatchEvent(new CustomEvent('smartoffice:presentation-session-state', {
    detail: {
      active: state !== 'inactive',
      state,
      slideNumber,
      suppressProactiveSpeech: state !== 'inactive',
    },
  }))
}

function directCaption(text: string, route: string, slideNumber: number): void {
  window.dispatchEvent(new CustomEvent('smartoffice:direct-assistant-caption', {
    detail: { text, route, slideNumber },
  }))
}

export function useGuidedPresentationController(
  controller: OfficeVoiceController,
): OfficeVoiceController {
  const controllerRef = useRef(controller)
  controllerRef.current = controller
  const activeRef = useRef(false)
  const stateRef = useRef<SessionState>('inactive')
  const stateStartedAtRef = useRef(performance.now())
  const slideRef = useRef(1)
  const scriptRef = useRef<ScriptPayload | null>(null)
  const operationRef = useRef(0)
  const operationAbortRef = useRef<AbortController | null>(null)

  const setSessionState = useCallback((state: SessionState, slide = slideRef.current) => {
    stateRef.current = state
    stateStartedAtRef.current = performance.now()
    slideRef.current = slide
    activeRef.current = state !== 'inactive'
    globalPresentationActive = activeRef.current
    sessionEvent(state, slide)
  }, [])

  const beginOperation = useCallback((): { id: number; signal: AbortSignal } => {
    operationAbortRef.current?.abort()
    const controller = new AbortController()
    operationAbortRef.current = controller
    const id = ++operationRef.current
    return { id, signal: controller.signal }
  }, [])

  const operationCurrent = useCallback((id: number, signal: AbortSignal): boolean => (
    id === operationRef.current && !signal.aborted
  ), [])

  const loadScript = useCallback(async (signal: AbortSignal): Promise<ScriptPayload> => {
    if (scriptRef.current) return scriptRef.current
    const payload = await requestJson<ScriptPayload>(
      '/api/presentation/session/script',
      {},
      { timeoutMs: 6_000, signal },
    )
    scriptRef.current = payload
    return payload
  }, [])

  const speakExact = useCallback(async (
    text: string,
    language: VoiceLanguage,
    route: string,
    slideNumber: number,
    stateWhileSpeaking: SessionState,
    operationId: number,
    signal: AbortSignal,
  ): Promise<boolean> => {
    const clean = text.trim()
    if (!clean || !operationCurrent(operationId, signal)) return false
    await controllerRef.current.stopSpeaking()
    if (!operationCurrent(operationId, signal)) return false
    setSessionState(stateWhileSpeaking, slideNumber)
    directCaption(clean, route, slideNumber)
    registerVoiceOutputContext(clean, {
      delivery: {
        schema_version: 'voice-delivery-v1',
        style: route === 'presentation_knowledge_fallback' ? 'calm_reassuring' : 'warm_confident',
        pace: 'natural',
        energy: 'medium',
        question_tone: 'none',
        emphasis_terms: [],
        humour_delivery: 'light_smile',
        pause_before_question: false,
      },
      purpose: route,
      replyMode: 'exact_operational',
      expectUserResponse: true,
      questionField: null,
    })
    try {
      await voiceOutputManager.speak(clean, language, {
        lease: visitLeaseRegistry.current(),
        signal,
        purpose: route,
        replyMode: 'exact_operational',
        expectUserResponse: true,
      })
      return operationCurrent(operationId, signal)
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') return false
      throw error
    }
  }, [operationCurrent, setSessionState])

  const action = useCallback(async (
    path: string,
    body: unknown | undefined,
    timeoutMs: number,
    signal: AbortSignal,
    fallbackSlide: number,
  ): Promise<number> => {
    const payload = await requestJson<PresentationActionPayload>(
      path,
      {
        method: 'POST',
        body: body === undefined ? undefined : JSON.stringify(body),
      },
      { timeoutMs, signal },
    )
    if (payload.ok === false) throw new Error(`${path} returned an unsuccessful PowerPoint result.`)
    return observedSlide(payload, fallbackSlide)
  }, [])

  const narrateSlide = useCallback(async (
    slideNumber: number,
    language: VoiceLanguage,
    operationId: number,
    signal: AbortSignal,
  ): Promise<void> => {
    const script = await loadScript(signal)
    const narration = script.slides[String(slideNumber)]?.narration?.trim()
    if (!narration) {
      if (operationCurrent(operationId, signal)) {
        setSessionState(slideNumber === 1 ? 'cover_waiting' : 'slide_waiting', slideNumber)
      }
      return
    }
    try {
      await speakExact(
        narration,
        language,
        'presentation_slide_narration',
        slideNumber,
        'slide_narrating',
        operationId,
        signal,
      )
    } finally {
      if (operationCurrent(operationId, signal)) setSessionState('slide_waiting', slideNumber)
    }
  }, [loadScript, operationCurrent, setSessionState, speakExact])

  const startSession = useCallback(async (
    language: VoiceLanguage,
    operationId: number,
    signal: AbortSignal,
  ): Promise<void> => {
    setSessionState('cover_waiting', 1)
    const script = await loadScript(signal)
    await controllerRef.current.stopSpeaking()
    const current = await action(
      '/api/presentation/guided/start',
      undefined,
      28_000,
      signal,
      1,
    )
    if (!operationCurrent(operationId, signal)) return
    slideRef.current = current
    const intro = script.cover_intro[language] || script.cover_intro.zh
    try {
      await speakExact(
        intro,
        language,
        'presentation_cover_intro',
        1,
        'cover_waiting',
        operationId,
        signal,
      )
    } finally {
      if (operationCurrent(operationId, signal)) setSessionState('cover_waiting', 1)
    }
  }, [action, loadScript, operationCurrent, setSessionState, speakExact])

  const moveTo = useCallback(async (
    slideNumber: number,
    language: VoiceLanguage,
    operationId: number,
    signal: AbortSignal,
  ): Promise<void> => {
    await controllerRef.current.stopSpeaking()
    const current = await action(
      '/api/presentation/slideshow/goto',
      { slide_number: slideNumber },
      6_000,
      signal,
      slideNumber,
    )
    if (!operationCurrent(operationId, signal)) return
    slideRef.current = current
    if (current === 1) {
      const script = await loadScript(signal)
      const intro = script.cover_intro[language] || script.cover_intro.zh
      try {
        await speakExact(
          intro,
          language,
          'presentation_cover_intro',
          1,
          'cover_waiting',
          operationId,
          signal,
        )
      } finally {
        if (operationCurrent(operationId, signal)) setSessionState('cover_waiting', 1)
      }
      return
    }
    await narrateSlide(current, language, operationId, signal)
  }, [action, loadScript, narrateSlide, operationCurrent, setSessionState, speakExact])

  const answerQuestion = useCallback(async (
    question: string,
    language: VoiceLanguage,
    operationId: number,
    signal: AbortSignal,
  ): Promise<void> => {
    const slideNumber = slideRef.current
    if (slideNumber < 2 || slideNumber > 4) {
      await controllerRef.current.submit(question, 'voice')
      return
    }
    await controllerRef.current.stopSpeaking()
    if (!operationCurrent(operationId, signal)) return
    setSessionState('answering_question', slideNumber)
    let openingRegistration = false
    try {
      const payload = await requestJson<QuestionPayload>(
        '/api/presentation/session/answer',
        {
          method: 'POST',
          body: JSON.stringify({
            slide_number: slideNumber,
            question,
            language,
          }),
        },
        { timeoutMs: 14_000, signal },
      )
      if (!operationCurrent(operationId, signal)) return
      const route = payload.open_registration
        ? 'presentation_knowledge_fallback'
        : 'presentation_slide_answer'
      openingRegistration = payload.open_registration
      const completed = await speakExact(
        payload.spoken_text,
        language,
        route,
        slideNumber,
        payload.open_registration ? 'opening_registration' : 'answering_question',
        operationId,
        signal,
      )
      if (!completed || !operationCurrent(operationId, signal)) return
      if (payload.open_registration) {
        await openInteractionWindow({
          kind: 'contact',
          conversationId: controllerRef.current.conversationId,
          visitId: visitLeaseRegistry.current()?.visitId ?? null,
          language,
        })
      }
    } finally {
      if (operationCurrent(operationId, signal)) {
        setSessionState('slide_waiting', slideNumber)
      } else if (openingRegistration && activeRef.current && stateRef.current === 'opening_registration') {
        setSessionState('slide_waiting', slideNumber)
      }
    }
  }, [operationCurrent, setSessionState, speakExact])

  const finishAfterLastSlide = useCallback(async (
    language: VoiceLanguage,
    operationId: number,
    signal: AbortSignal,
  ): Promise<void> => {
    setSessionState('ending', 4)
    try {
      await controllerRef.current.stopSpeaking()
      try {
        await action(
          '/api/presentation/guided/finish',
          undefined,
          16_000,
          signal,
          1,
        )
      } catch (error) {
        if (!(error instanceof Error && error.name === 'AbortError')) {
          console.error('[GuidedPresentation] finish-action-failed', error)
        }
      }
      if (!operationCurrent(operationId, signal)) return
      const ending = language === 'en' ? END_OF_DECK_EN : END_OF_DECK_ZH
      await speakExact(
        ending,
        language,
        'presentation_completed',
        1,
        'ending',
        operationId,
        signal,
      )
    } finally {
      setSessionState('inactive', 1)
    }
  }, [action, operationCurrent, setSessionState, speakExact])

  const endSessionSilently = useCallback(async (
    signal: AbortSignal,
  ): Promise<void> => {
    setSessionState('ending', slideRef.current)
    try {
      await controllerRef.current.stopSpeaking()
      await action(
        '/api/presentation/guided/finish',
        undefined,
        16_000,
        signal,
        1,
      ).catch(() => undefined)
    } finally {
      setSessionState('inactive', 1)
    }
  }, [action, setSessionState])

  const submit = useCallback(async (
    text: string,
    source: 'text' | 'voice' = 'text',
  ): Promise<void> => {
    const clean = text.normalize('NFKC').replace(/\s+/g, ' ').trim()
    if (!clean) return
    const language = /[\u3400-\u9fff]/.test(clean) ? 'zh' : controllerRef.current.language

    if (!activeRef.current && !START_PRESENTATION.test(clean)) {
      await controllerRef.current.submit(text, source)
      return
    }

    const operation = beginOperation()
    try {
      if (!activeRef.current) {
        await startSession(language, operation.id, operation.signal)
        return
      }
      if (END_PRESENTATION.test(clean)) {
        await endSessionSilently(operation.signal)
        return
      }
      if (PAUSE_PRESENTATION.test(clean)) {
        await controllerRef.current.stopSpeaking()
        if (operationCurrent(operation.id, operation.signal)) {
          setSessionState(slideRef.current === 1 ? 'cover_waiting' : 'slide_waiting', slideRef.current)
        }
        return
      }
      if (RESUME_PRESENTATION.test(clean)) {
        if (slideRef.current === 1) {
          const script = await loadScript(operation.signal)
          const intro = script.cover_intro[language] || script.cover_intro.zh
          try {
            await speakExact(
              intro,
              language,
              'presentation_cover_intro',
              1,
              'cover_waiting',
              operation.id,
              operation.signal,
            )
          } finally {
            if (operationCurrent(operation.id, operation.signal)) setSessionState('cover_waiting', 1)
          }
        } else {
          await narrateSlide(slideRef.current, language, operation.id, operation.signal)
        }
        return
      }
      if (NEXT_SLIDE.test(clean)) {
        if (slideRef.current >= 4) {
          await finishAfterLastSlide(language, operation.id, operation.signal)
          return
        }
        await moveTo(slideRef.current + 1, language, operation.id, operation.signal)
        return
      }
      if (PREVIOUS_SLIDE.test(clean)) {
        await moveTo(Math.max(1, slideRef.current - 1), language, operation.id, operation.signal)
        return
      }
      const target = requestedSlide(clean)
      if (target !== null) {
        await moveTo(
          Math.max(1, Math.min(4, target)),
          language,
          operation.id,
          operation.signal,
        )
        return
      }
      await answerQuestion(clean, language, operation.id, operation.signal)
    } catch (error) {
      const aborted = error instanceof Error && error.name === 'AbortError'
      if (!aborted) console.error('[GuidedPresentation] operation-failed', error)
      if (activeRef.current && stateRef.current !== 'ending') {
        setSessionState(slideRef.current === 1 ? 'cover_waiting' : 'slide_waiting', slideRef.current)
      }
    }
  }, [
    answerQuestion,
    beginOperation,
    endSessionSilently,
    finishAfterLastSlide,
    loadScript,
    moveTo,
    narrateSlide,
    operationCurrent,
    setSessionState,
    speakExact,
    startSession,
  ])

  useEffect(() => {
    const reset = () => {
      operationAbortRef.current?.abort()
      operationAbortRef.current = null
      operationRef.current += 1
      setSessionState('inactive', 1)
      scriptRef.current = null
    }
    const onBargeIn = () => {
      if (!activeRef.current) return
      const state = stateRef.current
      if (!['slide_narrating', 'answering_question', 'opening_registration'].includes(state)) return
      operationAbortRef.current?.abort()
      operationRef.current += 1
      void voiceOutputManager.stop('presentation-visitor-barge-in')
      setSessionState(slideRef.current === 1 ? 'cover_waiting' : 'slide_waiting', slideRef.current)
    }
    window.addEventListener('smartoffice:visit-revoked', reset)
    window.addEventListener('smartoffice:realtime-vad-speech-started', onBargeIn)
    return () => {
      window.removeEventListener('smartoffice:visit-revoked', reset)
      window.removeEventListener('smartoffice:realtime-vad-speech-started', onBargeIn)
    }
  }, [setSessionState])

  useEffect(() => {
    const timer = window.setInterval(() => {
      const state = stateRef.current
      const budget = TRANSIENT_STATE_BUDGET_MS[state]
      if (!budget || performance.now() - stateStartedAtRef.current <= budget) return
      console.error('[GuidedPresentation] state-watchdog-recovery', {
        state,
        slide: slideRef.current,
        elapsedMs: Math.round(performance.now() - stateStartedAtRef.current),
        budgetMs: budget,
      })
      operationAbortRef.current?.abort()
      operationRef.current += 1
      void voiceOutputManager.stop('presentation-state-watchdog')
      setSessionState(
        state === 'ending' ? 'inactive' : slideRef.current === 1 ? 'cover_waiting' : 'slide_waiting',
        state === 'ending' ? 1 : slideRef.current,
      )
    }, 1_000)
    return () => window.clearInterval(timer)
  }, [setSessionState])

  return {
    ...controller,
    submit,
  }
}
