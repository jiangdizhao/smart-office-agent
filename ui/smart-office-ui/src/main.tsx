import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './voice/safeRealtimeAgentRuntime'
import './voice/VoiceDebugPanelPhase2.css'
import { installRealtimeMetadataCompatibility } from './voice/realtimeMetadataCompatibility'
import DebugApp from './debug/DebugApp.tsx'
import VirtualHostApp from './virtual-host/VirtualHostApp.tsx'

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
