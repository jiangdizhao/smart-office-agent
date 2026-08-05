import { createRoot } from 'react-dom/client'
import './index.css'
import './voice/visitRealtimeLeaseBridge'
import './voice/proximityTimelineDiagnostics'
import './voice/commandSpeechRecovery'
import './voice/meetingVoiceIntentPromptPatch'
import './voice/officeInterpreterCommandRecovery'
import './voice/preemptiveTurnCoordinator'
import './voice/continuousVoiceBootstrap'
import './voice/meetingVoiceCopyPatch'
import './voice/VoiceDebugPanelPhase2.css'
import './virtual-host/ProactiveReceptionStage1.css'
import './interaction/embeddedInteractionBridge'
import './interaction/interactionPanelCommandBridge'
import './interaction/sessionSummaryLifecycle'
import './interaction/contactConsentProfilePatch'
import './interaction/meetingWizardProgressiveFlow'
import { installSalesPhase2BEngagementOrchestrator } from './sales/salesPhase2BEngagementOrchestrator'
import DebugApp from './debug/DebugApp.tsx'
import InteractionActionRail from './interaction/InteractionActionRail.tsx'
import InteractionApp from './interaction/InteractionApp.tsx'
import InteractionPanelHost from './interaction/InteractionPanelHost.tsx'
import ProtectedResultCenterApp from './interaction/ProtectedResultCenterApp.tsx'
import VisitorExperienceApp from './interaction/VisitorExperienceApp.tsx'
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
installSalesPhase2BEngagementOrchestrator()

localStorage.setItem('smartoffice_actor_type', 'operator')

const appRoot = document.getElementById('root')
if (!appRoot) throw new Error('Application root element was not found.')

const normalizedPath = window.location.pathname.replace(/\/+$/, '') || '/'
const debugRoute = normalizedPath === '/debug' || normalizedPath.startsWith('/debug/')
const interactionRoute =
  normalizedPath === '/interaction' || normalizedPath.startsWith('/interaction/')
const resultCenterRoute =
  normalizedPath === '/interaction/results'
  || normalizedPath.startsWith('/interaction/results/')
const visitorExperienceRoute =
  normalizedPath === '/interaction/meeting'
  || normalizedPath.startsWith('/interaction/meeting/')
  || normalizedPath === '/interaction/transcript'
  || normalizedPath.startsWith('/interaction/transcript/')
const embeddedInteraction =
  interactionRoute && new URLSearchParams(window.location.search).get('embedded') === '1'
document.documentElement.dataset.smartOfficeRoute = debugRoute
  ? 'debug'
  : interactionRoute
    ? 'interaction'
    : 'virtual-host'
document.documentElement.dataset.smartOfficeEmbedded = embeddedInteraction ? 'true' : 'false'

createRoot(appRoot).render(
  debugRoute ? (
    <DebugApp />
  ) : resultCenterRoute ? (
    <ProtectedResultCenterApp />
  ) : visitorExperienceRoute ? (
    <VisitorExperienceApp />
  ) : interactionRoute ? (
    <InteractionApp />
  ) : (
    <>
      <VirtualHostApp />
      <InteractionPanelHost />
      <InteractionActionRail />
    </>
  ),
)
