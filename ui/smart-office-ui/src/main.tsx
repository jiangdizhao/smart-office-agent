import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './voice/safeRealtimeAgentRuntime'
import './voice/VoiceDebugPanelPhase2.css'
import { installRealtimeMetadataCompatibility } from './voice/realtimeMetadataCompatibility'
import DebugApp from './debug/DebugApp.tsx'
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

  const forward = (level: 'info' | 'error', args: unknown[]) => {
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
  const originalError = console.error.bind(console)
  console.info = (...args: unknown[]) => {
    originalInfo(...args)
    forward('info', args)
  }
  console.error = (...args: unknown[]) => {
    originalError(...args)
    forward('error', args)
  }
}

installProximityTerminalForwarding()
installRealtimeMetadataCompatibility()

const appRoot = document.getElementById('root')
if (!appRoot) throw new Error('Application root element was not found.')

const normalizedPath = window.location.pathname.replace(/\/+$/, '') || '/'
const debugRoute = normalizedPath === '/debug' || normalizedPath.startsWith('/debug/')
document.documentElement.dataset.smartOfficeRoute = debugRoute ? 'debug' : 'virtual-host'

createRoot(appRoot).render(
  <StrictMode>
    {debugRoute ? <DebugApp /> : <VirtualHostApp />}
  </StrictMode>,
)
