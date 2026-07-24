import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './voice/safeRealtimeAgentRuntime'
import './voice/VoiceDebugPanelPhase2.css'
import { installRealtimeMetadataCompatibility } from './voice/realtimeMetadataCompatibility'
import DebugApp from './debug/DebugApp.tsx'

installRealtimeMetadataCompatibility()

const appRoot = document.getElementById('root')
if (!appRoot) throw new Error('Application root element was not found.')

const normalizedPath = window.location.pathname.replace(/\/+$/, '') || '/'
document.documentElement.dataset.smartOfficeRoute =
  normalizedPath === '/debug' || normalizedPath.startsWith('/debug/') ? 'debug' : 'legacy'

createRoot(appRoot).render(
  <StrictMode>
    <DebugApp />
  </StrictMode>,
)
