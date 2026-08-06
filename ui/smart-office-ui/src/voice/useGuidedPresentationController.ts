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

type StatusPayload = {
  ok?: boolean
  status?: {
    ok?: boolean
    data?: {
      current_slide?: number | null
      total_slides?: number | null
      slideshow_active?: boolean
    }
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

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${OFFICE_API_BASE}${path}`, init)
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`${path} failed: ${response.status}${detail ? ` ${detail}` : ''}`)
  }
  return await response.json() as T
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
  const slideRef = useRef(1)
  const scriptRef = useRef<ScriptPayload | null>(null)
  const operationRef = useRef(0)

  const setSessionState = useCallback((state: SessionState, slide = slideRef.current) => {
    stateRef.current = state
    slideRef.current = slide
    activeRef.current = state !== 'inactive'
    globalPresentationActive = activeRef.current
    sessionEvent(state, slide)
  }, [])

  const loadScript = useCallback(async (): Promise<ScriptPayload> => {
    if (scriptRef.current) return scriptRef.current
    const payload = await requestJson<ScriptPayload>('/api/presentation/session/script')
    scriptRef.current = payload
    return payload
  }, [])

  const speakExact = useCallback(async (
    text: string,
    language: VoiceLanguage,
    route: string,
    slideNumber: number,
    stateWhileSpeaking: SessionState,
  ): Promise<boolean> => {
    const clean = text.trim()
    if (!clean) return false
    const token = ++operationRef.current
    await controllerRef.current.stopSpeaking()
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
        signal: visitLeaseRegistry.current()?.signal,
        purpose: route,
        replyMode: 'exact_operational',
        expectUserResponse: true,
      })
      return token === operationRef.current
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') return false
      throw error
    }
  }, [setSessionState])

  const status = useCallback(async (): Promise<number> => {
    const payload = await requestJson<StatusPayload>('/api/presentation/status')
    const current = Number(payload.status?.data?.current_slide ?? 1)
    return Number.isFinite(current) && current >= 1 ? current : 1
  }, [])

  const action = useCallback(async (path: string, body?: unknown): Promise<number> => {
    await requestJson(path, {
      method: 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json; charset=utf-8' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return await status()
  }, [status])

  const narrateSlide = useCallback(async (slideNumber: number, language: VoiceLanguage): Promise<void> => {
    const script = await loadScript()
    const narration = script.slides[String(slideNumber)]?.narration?.trim()
    if (!narration) {
      setSessionState(slideNumber === 1 ? 'cover_waiting' : 'slide_waiting', slideNumber)
      return
    }
    const completed = await speakExact(
      narration,
      language,
      'presentation_slide_narration',
      slideNumber,
      'slide_narrating',
    )
    if (completed) setSessionState('slide_waiting', slideNumber)
  }, [loadScript, setSessionState, speakExact])

  const startSession = useCallback(async (language: VoiceLanguage): Promise<void> => {
    const script = await loadScript()
    await controllerRef.current.stopSpeaking()
    await action('/api/presentation/open')
    await action('/api/presentation/slideshow/start')
    await action('/api/presentation/slideshow/goto', { slide_number: 1 })
    const intro = script.cover_intro[language] || script.cover_intro.zh
    const completed = await speakExact(
      intro,
      language,
      'presentation_cover_intro',
      1,
      'cover_waiting',
    )
    if (completed) setSessionState('cover_waiting', 1)
  }, [action, loadScript, setSessionState, speakExact])

  const moveTo = useCallback(async (slideNumber: number, language: VoiceLanguage): Promise<void> => {
    await controllerRef.current.stopSpeaking()
    const current = await action('/api/presentation/slideshow/goto', { slide_number: slideNumber })
    slideRef.current = current
    if (current === 1) {
      const script = await loadScript()
      const intro = script.cover_intro[language] || script.cover_intro.zh
      const completed = await speakExact(intro, language, 'presentation_cover_intro', 1, 'cover_waiting')
      if (completed) setSessionState('cover_waiting', 1)
      return
    }
    await narrateSlide(current, language)
  }, [action, loadScript, narrateSlide, setSessionState, speakExact])

  const answerQuestion = useCallback(async (question: string, language: VoiceLanguage): Promise<void> => {
    const slideNumber = slideRef.current
    if (slideNumber < 2 || slideNumber > 4) {
      await controllerRef.current.submit(question, 'voice')
      return
    }
    await controllerRef.current.stopSpeaking()
    setSessionState('answering_question', slideNumber)
    const payload = await requestJson<QuestionPayload>('/api/presentation/session/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        slide_number: slideNumber,
        question,
        language,
      }),
    })
    const route = payload.open_registration
      ? 'presentation_knowledge_fallback'
      : 'presentation_slide_answer'
    const completed = await speakExact(
      payload.spoken_text,
      language,
      route,
      slideNumber,
      payload.open_registration ? 'opening_registration' : 'answering_question',
    )
    if (!completed) return
    if (payload.open_registration) {
      await openInteractionWindow({
        kind: 'contact',
        conversationId: controllerRef.current.conversationId,
        visitId: visitLeaseRegistry.current()?.visitId ?? null,
        language,
      })
    }
    setSessionState('slide_waiting', slideNumber)
  }, [setSessionState, speakExact])

  const finishAfterLastSlide = useCallback(async (language: VoiceLanguage): Promise<void> => {
    await controllerRef.current.stopSpeaking()
    await action('/api/presentation/slideshow/goto', { slide_number: 1 })
    await action('/api/presentation/slideshow/end')
    const ending = language === 'en' ? END_OF_DECK_EN : END_OF_DECK_ZH
    const completed = await speakExact(
      ending,
      language,
      'presentation_completed',
      1,
      'slide_waiting',
    )
    if (completed) setSessionState('inactive', 1)
  }, [action, setSessionState, speakExact])

  const submit = useCallback(async (
    text: string,
    source: 'text' | 'voice' = 'text',
  ): Promise<void> => {
    const clean = text.normalize('NFKC').replace(/\s+/g, ' ').trim()
    if (!clean) return
    const language = /[\u3400-\u9fff]/.test(clean) ? 'zh' : controllerRef.current.language

    if (!activeRef.current) {
      if (START_PRESENTATION.test(clean)) {
        await startSession(language)
        return
      }
      await controllerRef.current.submit(text, source)
      return
    }

    if (END_PRESENTATION.test(clean)) {
      await controllerRef.current.stopSpeaking()
      await action('/api/presentation/slideshow/goto', { slide_number: 1 })
      await action('/api/presentation/slideshow/end')
      setSessionState('inactive', 1)
      return
    }
    if (PAUSE_PRESENTATION.test(clean)) {
      await controllerRef.current.stopSpeaking()
      setSessionState(slideRef.current === 1 ? 'cover_waiting' : 'slide_waiting', slideRef.current)
      return
    }
    if (RESUME_PRESENTATION.test(clean)) {
      if (slideRef.current === 1) {
        const script = await loadScript()
        const intro = script.cover_intro[language] || script.cover_intro.zh
        const completed = await speakExact(intro, language, 'presentation_cover_intro', 1, 'cover_waiting')
        if (completed) setSessionState('cover_waiting', 1)
      } else {
        await narrateSlide(slideRef.current, language)
      }
      return
    }
    if (NEXT_SLIDE.test(clean)) {
      if (slideRef.current >= 4) {
        await finishAfterLastSlide(language)
        return
      }
      await moveTo(slideRef.current + 1, language)
      return
    }
    if (PREVIOUS_SLIDE.test(clean)) {
      await moveTo(Math.max(1, slideRef.current - 1), language)
      return
    }
    const target = requestedSlide(clean)
    if (target !== null) {
      await moveTo(Math.max(1, Math.min(4, target)), language)
      return
    }

    await answerQuestion(clean, language)
  }, [action, answerQuestion, finishAfterLastSlide, loadScript, moveTo, narrateSlide, setSessionState, speakExact, startSession])

  useEffect(() => {
    const reset = () => {
      operationRef.current += 1
      setSessionState('inactive', 1)
      scriptRef.current = null
    }
    window.addEventListener('smartoffice:visit-revoked', reset)
    return () => window.removeEventListener('smartoffice:visit-revoked', reset)
  }, [setSessionState])

  return {
    ...controller,
    submit,
  }
}
