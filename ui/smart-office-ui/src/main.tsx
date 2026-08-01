import { createRoot } from 'react-dom/client'
import './index.css'
import './voice/visitRealtimeLeaseBridge'
import './voice/proximityTimelineDiagnostics'
import './voice/VoiceDebugPanelPhase2.css'
import './virtual-host/ProactiveReceptionStage1.css'
import DebugApp from './debug/DebugApp.tsx'
import InteractionActionRail from './interaction/InteractionActionRail.tsx'
import InteractionApp from './interaction/InteractionApp.tsx'
import VirtualHostApp from './virtual-host/VirtualHostApp.tsx'

function serialiseDiagnostic(value: unknown): unknown {
  if (value instanceof Error) {
    return { name: value.name, message: value.message, stack: value.stack }
  }
  if (value === undefined) return null
  try {
    JSON.stringify(value)
    return value
  } catch {
    return String(value)
  }
}

function installProximityTerminalForwarding(): void {
  const configured = String(import.meta.env.VITE_PROXIMITY_DEBUG ?? '').trim().toLowerCase()
  const enabled =
    configured === 'true' ||
    configured === '1' ||
    configured === 'on' ||
    (configured !== 'false' && configured !== '0' && configured !== 'off' && import.meta.env.DEV)
  if (!enabled) return

  const forward = (level: 'info' | 'warn' | 'error', args: unknown[]) => {
    const first = typeof args[0] === 'string' ? args[0] : ''
    if (!first.startsWith('[ProximityDebug]')) return

    const event = first.replace(/^\[ProximityDebug\]\s*/, '') || 'browser-log'
    const data = {
      level,
      arguments: args.slice(1).map(serialiseDiagnostic),
      page: window.location.pathname,
    }

    void fetch('/__proximity_debug', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, data, time: new Date().toISOString() }),
      keepalive: true,
    }).catch(() => {
      // Diagnostic forwarding must never affect the Agent UI.
    })
  }

  const originalInfo = console.info.bind(console)
  const originalWarn = console.warn.bind(console)
  const originalError = console.error.bind(console)
  console.info = (...args: unknown[]) => {
    originalInfo(...args)
    forward('info', args)
  }
  console.warn = (...args: unknown[]) => {
    originalWarn(...args)
    forward('warn', args)
  }
  console.error = (...args: unknown[]) => {
    originalError(...args)
    forward('error', args)
  }
}

installProximityTerminalForwarding()

// Exhibition mode is function-first: every visitor is treated as an operator for
// PowerPoint, volume and other demonstration capabilities. Destructive actions
// such as real email sending retain their separate confirmation workflow.
localStorage.setItem('smartoffice_actor_type', 'operator')

const appRoot = document.getElementById('root')
if (!appRoot) throw new Error('Application root element was not found.')

const normalizedPath = window.location.pathname.replace(/\/+$/, '') || '/'
const debugRoute = normalizedPath === '/debug' || normalizedPath.startsWith('/debug/')
const interactionRoute =
  normalizedPath === '/interaction' || normalizedPath.startsWith('/interaction/')
document.documentElement.dataset.smartOfficeRoute = debugRoute
  ? 'debug'
  : interactionRoute
    ? 'interaction'
    : 'virtual-host'

createRoot(appRoot).render(
  debugRoute ? (
    <DebugApp />
  ) : interactionRoute ? (
    <InteractionApp />
  ) : (
    <>
      <VirtualHostApp />
      <InteractionActionRail />
    </>
  ),
)
