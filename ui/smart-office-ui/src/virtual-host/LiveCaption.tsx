import { useEffect, useMemo, useRef, useState } from 'react'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VirtualHostVisualState } from './VirtualHostAvatar'
import './LiveCaption.css'

type LiveCaptionProps = {
  state: VirtualHostVisualState
  language: VoiceLanguage
  userText: string
  assistantText: string
  welcomeText: string
}

type VoiceChunkDetail = {
  text?: string
  outputId?: string
  index?: number
  count?: number
}

type AssistantOutputDetail = {
  outputId?: string
  text?: string
}

type TranscriptDetail = {
  transcript?: string
}

function cleanCaption(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

function currentUserTextTail(text: string): string {
  const clean = cleanCaption(text)
  if (!clean) return ''
  const hasCjk = /[\u3400-\u9fff]/.test(clean)
  const limit = hasCjk ? 84 : 210
  return clean.length <= limit ? clean : clean.slice(-limit)
}

function currentAssistantText(text: string): string {
  const clean = cleanCaption(text)
  if (!clean) return ''
  const hasCjk = /[\u3400-\u9fff]/.test(clean)
  const limit = hasCjk ? 96 : 240
  return clean.length <= limit ? clean : `${clean.slice(0, limit - 1)}…`
}

export default function LiveCaption({
  state,
  language,
  userText,
  assistantText,
  welcomeText,
}: LiveCaptionProps) {
  const [activeAssistantChunk, setActiveAssistantChunk] = useState('')
  const [eventAssistantText, setEventAssistantText] = useState('')
  const [eventUserText, setEventUserText] = useState('')
  const activeOutputId = useRef<string | null>(null)
  const [userTurnActive, setUserTurnActive] = useState(false)

  useEffect(() => {
    const resetForNewUserTurn = () => {
      setUserTurnActive(true)
      activeOutputId.current = null
      setActiveAssistantChunk('')
      setEventAssistantText('')
      setEventUserText('')
    }
    const onUserTranscript = (event: Event) => {
      const detail = (event as CustomEvent<TranscriptDetail>).detail
      const transcript = cleanCaption(detail?.transcript ?? '')
      if (transcript) setEventUserText(transcript)
    }
    const onDirectAssistant = (event: Event) => {
      const detail = (event as CustomEvent<AssistantOutputDetail>).detail
      const text = cleanCaption(detail?.text ?? '')
      if (!text) return
      setUserTurnActive(false)
      setEventAssistantText(text)
    }
    const onOutputStarted = (event: Event) => {
      const detail = (event as CustomEvent<AssistantOutputDetail>).detail
      const outputId = String(detail?.outputId ?? '').trim()
      activeOutputId.current = outputId || null
      setUserTurnActive(false)
      setActiveAssistantChunk('')
      const text = cleanCaption(detail?.text ?? '')
      if (text) setEventAssistantText(text)
    }
    const onChunkStart = (event: Event) => {
      const detail = (event as CustomEvent<VoiceChunkDetail>).detail
      const outputId = String(detail?.outputId ?? '').trim()
      if (activeOutputId.current && outputId && outputId !== activeOutputId.current) return
      if (outputId) activeOutputId.current = outputId
      setUserTurnActive(false)
      const text = cleanCaption(detail?.text ?? '')
      if (text) setActiveAssistantChunk(text)
    }
    const onOutputFinished = (event: Event) => {
      const detail = (event as CustomEvent<AssistantOutputDetail>).detail
      const outputId = String(detail?.outputId ?? '').trim()
      if (activeOutputId.current && outputId && outputId !== activeOutputId.current) return
      const text = cleanCaption(detail?.text ?? '')
      if (text) setEventAssistantText(text)
      activeOutputId.current = null
      setActiveAssistantChunk('')
    }
    const clearForVisitBoundary = () => {
      setUserTurnActive(false)
      activeOutputId.current = null
      setActiveAssistantChunk('')
      setEventAssistantText('')
      setEventUserText('')
    }

    window.addEventListener('smartoffice:realtime-vad-speech-started', resetForNewUserTurn)
    window.addEventListener('smartoffice:realtime-continuous-utterance', onUserTranscript)
    window.addEventListener('smartoffice:continuous-user-transcript', onUserTranscript)
    window.addEventListener('smartoffice:direct-assistant-caption', onDirectAssistant)
    window.addEventListener('smartoffice:assistant-output-started', onOutputStarted)
    window.addEventListener('smartoffice:voice-chunk-start', onChunkStart)
    window.addEventListener('smartoffice:assistant-output-completed', onOutputFinished)
    window.addEventListener('smartoffice:assistant-output-interrupted', onOutputFinished)
    window.addEventListener('smartoffice:assistant-output-failed', onOutputFinished)
    window.addEventListener('smartoffice:visit-activated', clearForVisitBoundary)
    window.addEventListener('smartoffice:visit-revoked', clearForVisitBoundary)
    return () => {
      window.removeEventListener('smartoffice:realtime-vad-speech-started', resetForNewUserTurn)
      window.removeEventListener('smartoffice:realtime-continuous-utterance', onUserTranscript)
      window.removeEventListener('smartoffice:continuous-user-transcript', onUserTranscript)
      window.removeEventListener('smartoffice:direct-assistant-caption', onDirectAssistant)
      window.removeEventListener('smartoffice:assistant-output-started', onOutputStarted)
      window.removeEventListener('smartoffice:voice-chunk-start', onChunkStart)
      window.removeEventListener('smartoffice:assistant-output-completed', onOutputFinished)
      window.removeEventListener('smartoffice:assistant-output-interrupted', onOutputFinished)
      window.removeEventListener('smartoffice:assistant-output-failed', onOutputFinished)
      window.removeEventListener('smartoffice:visit-activated', clearForVisitBoundary)
      window.removeEventListener('smartoffice:visit-revoked', clearForVisitBoundary)
    }
  }, [])

  useEffect(() => {
    const clean = cleanCaption(userText)
    if (clean) setEventUserText(clean)
  }, [userText])

  useEffect(() => {
    const clean = cleanCaption(assistantText)
    if (clean) setEventAssistantText(clean)
  }, [assistantText])

  useEffect(() => {
    if (state === 'listening' || state === 'processing') {
      activeOutputId.current = null
      setActiveAssistantChunk('')
      return
    }
    if (state === 'idle' || state === 'error') {
      activeOutputId.current = null
      setActiveAssistantChunk('')
    }
  }, [state])

  const isUserCaption = state === 'listening' || state === 'processing'
  const isAssistantCaption = state === 'speaking' || state === 'executing'
  const userCaption = useMemo(() => {
    const source = userTurnActive ? eventUserText : eventUserText || userText
    return currentUserTextTail(source)
  }, [eventUserText, userText, userTurnActive])
  const assistantSource = activeAssistantChunk || eventAssistantText || assistantText
  const assistantCaption = useMemo(
    () => currentAssistantText(assistantSource),
    [assistantSource],
  )

  if (isUserCaption) {
    const waitingForSpeech = state === 'listening' && !userCaption
    const role = waitingForSpeech
      ? language === 'zh'
        ? '麦克风已开启'
        : 'Microphone on'
      : language === 'zh'
        ? '您说'
        : 'You said'
    const text =
      userCaption ||
      (language === 'zh'
        ? state === 'listening'
          ? '正在聆听，请继续说。'
          : '正在整理您刚才的请求。'
        : state === 'listening'
          ? 'Listening. Please continue.'
          : 'Preparing your request.')

    return (
      <div className={`live-caption single-line-caption user-live-caption caption-${state}`} aria-live="polite">
        <span className="caption-role">{role}</span>
        <div className="caption-stream-window">
          <p className="caption-stream-text" aria-label={cleanCaption(eventUserText || userText) || text}>{text}</p>
        </div>
      </div>
    )
  }

  if (isAssistantCaption && assistantCaption) {
    return (
      <div className={`live-caption single-line-caption assistant-live-caption caption-${state}`} aria-live="polite">
        <span className="caption-role">{language === 'zh' ? '虚拟助手' : 'Virtual host'}</span>
        <div className="caption-stream-window">
          <p className="caption-stream-text" aria-label={cleanCaption(assistantSource)}>
            {assistantCaption}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="live-caption single-line-caption welcome-caption" aria-live="polite">
      <span className="caption-role">Smart Office</span>
      <div className="caption-stream-window">
        <p className="caption-stream-text">{currentAssistantText(welcomeText)}</p>
      </div>
    </div>
  )
}
