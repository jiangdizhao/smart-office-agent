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

function splitLongChunk(chunk: string): string[] {
  const clean = chunk.trim()
  if (!clean) return []

  const hasCjk = /[\u3400-\u9fff]/.test(clean)
  const limit = hasCjk ? 18 : 9
  if (hasCjk) {
    const parts: string[] = []
    for (let index = 0; index < clean.length; index += limit) {
      parts.push(clean.slice(index, index + limit))
    }
    return parts
  }

  const words = clean.split(/\s+/)
  const parts: string[] = []
  for (let index = 0; index < words.length; index += limit) {
    parts.push(words.slice(index, index + limit).join(' '))
  }
  return parts
}

function splitLyrics(text: string): string[] {
  const clean = text.replace(/\s+/g, ' ').trim()
  if (!clean) return []

  const sentenceChunks = clean
    .split(/(?<=[，。！？；：,.!?;:])/u)
    .map((item) => item.trim())
    .filter(Boolean)

  return sentenceChunks.flatMap(splitLongChunk)
}

function segmentDurationMs(segment: string): number {
  const cjk = (segment.match(/[\u3400-\u9fff]/g) ?? []).length
  const words = (segment.match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g) ?? []).length
  const punctuation = (segment.match(/[，。！？；：,.!?;:]/g) ?? []).length
  return Math.max(950, Math.min(4200, 520 + cjk * 180 + words * 310 + punctuation * 220))
}

export default function LiveCaption({
  state,
  language,
  userText,
  assistantText,
  welcomeText,
}: LiveCaptionProps) {
  const lyrics = useMemo(() => splitLyrics(assistantText), [assistantText])
  const [activeIndex, setActiveIndex] = useState(0)
  const [showAssistant, setShowAssistant] = useState(Boolean(assistantText))

  useEffect(() => {
    setActiveIndex(0)
    if (assistantText) setShowAssistant(true)

    if (state !== 'speaking' || lyrics.length < 2) return

    const timers: number[] = []
    let elapsed = 0
    lyrics.slice(0, -1).forEach((segment, index) => {
      elapsed += segmentDurationMs(segment)
      timers.push(window.setTimeout(() => setActiveIndex(index + 1), elapsed))
    })

    return () => timers.forEach((timer) => window.clearTimeout(timer))
  }, [assistantText, lyrics, state])

  useEffect(() => {
    if (state !== 'idle' || !assistantText) return
    const timer = window.setTimeout(() => setShowAssistant(false), 4200)
    return () => window.clearTimeout(timer)
  }, [assistantText, state])

  const isUserCaption = state === 'listening' || state === 'processing'
  const isAssistantCaption =
    state === 'speaking' || state === 'executing' || (state === 'idle' && showAssistant)

  if (isUserCaption) {
    const text =
      userText ||
      (language === 'zh'
        ? state === 'listening'
          ? '正在聆听，请继续说。'
          : '正在整理您刚才的请求。'
        : state === 'listening'
          ? 'Listening. Please continue.'
          : 'Preparing your request.')

    return (
      <div className={`live-caption user-live-caption caption-${state}`} aria-live="polite">
        <span className="caption-role">{language === 'zh' ? '您说' : 'You said'}</span>
        <p className="user-caption-text">“{text}”</p>
      </div>
    )
  }

  if (isAssistantCaption && assistantText) {
    const current = lyrics[activeIndex] ?? assistantText
    const previous = activeIndex > 0 ? lyrics[activeIndex - 1] : ''
    const next = activeIndex + 1 < lyrics.length ? lyrics[activeIndex + 1] : ''

    return (
      <div className={`live-caption lyric-caption caption-${state}`} aria-live="polite">
        <span className="caption-role">{language === 'zh' ? '虚拟助手' : 'Virtual host'}</span>
        <div className="lyric-stack">
          <p className="lyric-line lyric-previous">{previous}</p>
          <p className="lyric-line lyric-current">{current}</p>
          <p className="lyric-line lyric-next">{next}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="live-caption welcome-caption" aria-live="polite">
      <span className="caption-role">{language === 'zh' ? 'Smart Office' : 'Smart Office'}</span>
      <p>{welcomeText}</p>
    </div>
  )
}
