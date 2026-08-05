import { useEffect, useMemo, useState } from 'react'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VirtualHostVisualState } from './VirtualHostAvatar'

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

function cleanCaption(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

function currentTextTail(text: string): string {
  const clean = cleanCaption(text)
  if (!clean) return ''
  const hasCjk = /[\u3400-\u9fff]/.test(clean)
  const limit = hasCjk ? 46 : 110
  return clean.length <= limit ? clean : clean.slice(-limit)
}

export default function LiveCaption({
  state,
  language,
  userText,
  assistantText,
  welcomeText,
}: LiveCaptionProps) {
  const [activeAssistantChunk, setActiveAssistantChunk] = useState('')

  useEffect(() => {
    const onChunkStart = (event: Event) => {
      const detail = (event as CustomEvent<VoiceChunkDetail>).detail
      setActiveAssistantChunk(cleanCaption(detail?.text ?? ''))
    }
    const clearAssistantChunk = () => setActiveAssistantChunk('')
    window.addEventListener('smartoffice:voice-chunk-start', onChunkStart)
    window.addEventListener('smartoffice:assistant-output-completed', clearAssistantChunk)
    window.addEventListener('smartoffice:assistant-output-interrupted', clearAssistantChunk)
    window.addEventListener('smartoffice:assistant-output-failed', clearAssistantChunk)
    return () => {
      window.removeEventListener('smartoffice:voice-chunk-start', onChunkStart)
      window.removeEventListener('smartoffice:assistant-output-completed', clearAssistantChunk)
      window.removeEventListener('smartoffice:assistant-output-interrupted', clearAssistantChunk)
      window.removeEventListener('smartoffice:assistant-output-failed', clearAssistantChunk)
    }
  }, [])

  useEffect(() => {
    if (state === 'listening' || state === 'processing' || state === 'idle' || state === 'error') {
      setActiveAssistantChunk('')
    }
  }, [state])

  const isUserCaption = state === 'listening' || state === 'processing'
  const isAssistantCaption = state === 'speaking' || state === 'executing'
  const userCaption = useMemo(() => currentTextTail(userText), [userText])
  const assistantCaption = useMemo(
    () => currentTextTail(activeAssistantChunk || assistantText),
    [activeAssistantChunk, assistantText],
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
          <p className="caption-stream-text" aria-label={cleanCaption(userText) || text}>{text}</p>
        </div>
      </div>
    )
  }

  if (isAssistantCaption && assistantCaption) {
    return (
      <div className={`live-caption single-line-caption assistant-live-caption caption-${state}`} aria-live="polite">
        <span className="caption-role">{language === 'zh' ? '虚拟助手' : 'Virtual host'}</span>
        <div className="caption-stream-window">
          <p className="caption-stream-text" aria-label={activeAssistantChunk || assistantText}>
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
        <p className="caption-stream-text">{currentTextTail(welcomeText)}</p>
      </div>
    </div>
  )
}
