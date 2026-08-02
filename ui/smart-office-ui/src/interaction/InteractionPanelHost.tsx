import { useEffect, useRef, useState } from 'react'
import {
  INTERACTION_PANEL_CLOSE_EVENT,
  INTERACTION_PANEL_CLOSE_MESSAGE,
  INTERACTION_PANEL_COMMAND_EVENT,
  INTERACTION_PANEL_COMMAND_MESSAGE,
  INTERACTION_PANEL_OPEN_EVENT,
  INTERACTION_PANEL_READY_MESSAGE,
  INTERACTION_PANEL_RESULT_EVENT,
  INTERACTION_PANEL_RESULT_MESSAGE,
  interactionPanelUrl,
  markInteractionPanelClosed,
  type InteractionPanelCommand,
  type InteractionPanelCommandResult,
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
  meeting: { eyebrow: 'Meeting Scheduler', title: '预约会议' },
  recording: { eyebrow: 'Conversation Recording', title: '实时录音' },
  transcript: { eyebrow: 'Current Session Summary', title: '对话总结' },
  results: { eyebrow: 'Visitor Profiles', title: '结果中心' },
}

export default function InteractionPanelHost() {
  const [active, setActive] = useState<ActivePanel | null>(null)
  const [closing, setClosing] = useState(false)
  const activeRef = useRef<ActivePanel | null>(null)
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const closeTimer = useRef<number | null>(null)
  const frameReady = useRef(false)
  const pendingCommands = useRef<InteractionPanelCommand[]>([])

  function updateActive(value: ActivePanel | null): void {
    activeRef.current = value
    setActive(value)
  }

  function clearCloseTimer(): void {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = null
  }

  function finishClose(): void {
    const panelId = activeRef.current?.request.panelInstanceId
    markInteractionPanelClosed(panelId)
    pendingCommands.current = []
    frameReady.current = false
    updateActive(null)
    setClosing(false)
    closeTimer.current = null
  }

  function closePanel(immediate = false): void {
    if (!activeRef.current) return
    if (immediate) {
      clearCloseTimer()
      finishClose()
      return
    }
    if (closing) return
    setClosing(true)
    clearCloseTimer()
    closeTimer.current = window.setTimeout(finishClose, 220)
  }

  function postCommand(command: InteractionPanelCommand): void {
    iframeRef.current?.contentWindow?.postMessage(
      { type: INTERACTION_PANEL_COMMAND_MESSAGE, command },
      window.location.origin,
    )
  }

  function flushCommands(): void {
    const current = activeRef.current
    if (!current || !frameReady.current) return
    const panelId = current.request.panelInstanceId
    const remaining: InteractionPanelCommand[] = []
    for (const command of pendingCommands.current) {
      if (command.panelInstanceId === panelId) postCommand(command)
      else remaining.push(command)
    }
    pendingCommands.current = remaining
  }

  useEffect(() => {
    const onOpen = (event: Event) => {
      const request = event instanceof CustomEvent
        ? event.detail as InteractionWindowRequest
        : null
      if (!request?.kind || !request.conversationId || !request.panelInstanceId) return
      clearCloseTimer()
      setClosing(false)
      const current = activeRef.current
      if (current?.request.panelInstanceId === request.panelInstanceId) {
        updateActive({ ...current, request })
        return
      }
      frameReady.current = false
      pendingCommands.current = []
      updateActive({ request, instanceId: Date.now() })
    }

    const onCommand = (event: Event) => {
      const command = event instanceof CustomEvent
        ? event.detail as InteractionPanelCommand
        : null
      const current = activeRef.current
      if (!command || !current) return
      if (
        command.panelInstanceId !== current.request.panelInstanceId
        || command.target !== current.request.kind
        || String(command.visitId ?? '') !== String(current.request.visitId ?? '')
      ) return
      if (frameReady.current) postCommand(command)
      else pendingCommands.current.push(command)
    }

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return
      if (event.source !== iframeRef.current?.contentWindow) return
      const current = activeRef.current
      if (!current) return
      if (event.data?.type === INTERACTION_PANEL_CLOSE_MESSAGE) {
        closePanel()
        return
      }
      if (
        event.data?.type === INTERACTION_PANEL_READY_MESSAGE
        && event.data?.panelInstanceId === current.request.panelInstanceId
      ) {
        frameReady.current = true
        flushCommands()
        return
      }
      if (event.data?.type === INTERACTION_PANEL_RESULT_MESSAGE) {
        const result = event.data?.result as InteractionPanelCommandResult | undefined
        if (!result || result.panelInstanceId !== current.request.panelInstanceId) return
        window.dispatchEvent(
          new CustomEvent<InteractionPanelCommandResult>(
            INTERACTION_PANEL_RESULT_EVENT,
            { detail: result },
          ),
        )
      }
    }

    const onCloseRequest = () => closePanel()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closePanel()
    }
    const onVisitActivated = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null
      const nextVisitId = String(detail?.visitId ?? '')
      const panelVisitId = String(activeRef.current?.request.visitId ?? '')
      if (activeRef.current && (!nextVisitId || panelVisitId !== nextVisitId)) {
        closePanel(true)
      }
    }
    const onVisitRevoked = () => closePanel(true)

    window.addEventListener(INTERACTION_PANEL_OPEN_EVENT, onOpen)
    window.addEventListener(INTERACTION_PANEL_COMMAND_EVENT, onCommand)
    window.addEventListener(INTERACTION_PANEL_CLOSE_EVENT, onCloseRequest)
    window.addEventListener('message', onMessage)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('smartoffice:visit-activated', onVisitActivated)
    window.addEventListener('smartoffice:visit-revoked', onVisitRevoked)
    return () => {
      clearCloseTimer()
      window.removeEventListener(INTERACTION_PANEL_OPEN_EVENT, onOpen)
      window.removeEventListener(INTERACTION_PANEL_COMMAND_EVENT, onCommand)
      window.removeEventListener(INTERACTION_PANEL_CLOSE_EVENT, onCloseRequest)
      window.removeEventListener('message', onMessage)
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('smartoffice:visit-activated', onVisitActivated)
      window.removeEventListener('smartoffice:visit-revoked', onVisitRevoked)
    }
  }, [])

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
          <button type="button" onClick={() => closePanel()} aria-label="关闭交互面板">×</button>
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
