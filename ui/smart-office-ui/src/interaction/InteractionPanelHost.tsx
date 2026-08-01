import { useEffect, useRef, useState } from 'react'
import {
  INTERACTION_PANEL_CLOSE_MESSAGE,
  INTERACTION_PANEL_OPEN_EVENT,
  interactionPanelUrl,
  type InteractionWindowKind,
  type InteractionWindowRequest,
} from '../display/multiScreenWindowManager'
import './InteractionPanelHost.css'

type ActivePanel = {
  request: InteractionWindowRequest
  instanceId: number
}

const PANEL_LABELS: Record<InteractionWindowKind, { eyebrow: string; title: string }> = {
  contact: { eyebrow: 'Visitor Registration', title: '登记信息' },
  recording: { eyebrow: 'Conversation Recording', title: '实时录音' },
  transcript: { eyebrow: 'Current Session', title: '对话记录' },
  results: { eyebrow: 'Saved Results', title: '结果中心' },
}

export default function InteractionPanelHost() {
  const [active, setActive] = useState<ActivePanel | null>(null)
  const [closing, setClosing] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const closeTimer = useRef<number | null>(null)

  function clearCloseTimer(): void {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = null
  }

  function closePanel(): void {
    if (!active || closing) return
    setClosing(true)
    clearCloseTimer()
    closeTimer.current = window.setTimeout(() => {
      setActive(null)
      setClosing(false)
      closeTimer.current = null
    }, 220)
  }

  useEffect(() => {
    const onOpen = (event: Event) => {
      const request = event instanceof CustomEvent ? event.detail as InteractionWindowRequest : null
      if (!request?.kind || !request.conversationId) return
      clearCloseTimer()
      setClosing(false)
      setActive({ request, instanceId: Date.now() })
    }
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return
      if (event.source !== iframeRef.current?.contentWindow) return
      if (event.data?.type === INTERACTION_PANEL_CLOSE_MESSAGE) closePanel()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closePanel()
    }
    window.addEventListener(INTERACTION_PANEL_OPEN_EVENT, onOpen)
    window.addEventListener('message', onMessage)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      clearCloseTimer()
      window.removeEventListener(INTERACTION_PANEL_OPEN_EVENT, onOpen)
      window.removeEventListener('message', onMessage)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [active, closing])

  if (!active) return null
  const label = PANEL_LABELS[active.request.kind]

  return (
    <aside
      className={`interaction-panel-host ${closing ? 'is-closing' : 'is-open'}`}
      aria-label={label.title}
    >
      <section className="interaction-panel-surface" role="dialog" aria-modal="false">
        <header className="interaction-panel-chrome">
          <div>
            <span>{label.eyebrow}</span>
            <strong>{label.title}</strong>
          </div>
          <button type="button" onClick={closePanel} aria-label="关闭交互面板">×</button>
        </header>
        <div className="interaction-panel-frame-wrap">
          <iframe
            key={active.instanceId}
            ref={iframeRef}
            title={label.title}
            src={interactionPanelUrl(active.request)}
            allow="microphone"
          />
        </div>
      </section>
    </aside>
  )
}
