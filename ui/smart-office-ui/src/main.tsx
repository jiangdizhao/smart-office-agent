import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './voice/safeRealtimeAgentRuntime'
import App from './App.tsx'
import VoiceDebugPanel from './voice/VoiceDebugPanel.tsx'

const appRoot = document.getElementById('root')
if (!appRoot) throw new Error('Application root element was not found.')

createRoot(appRoot).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

const voiceDebugRoot = document.createElement('div')
voiceDebugRoot.id = 'voice-debug-root'
document.body.appendChild(voiceDebugRoot)

createRoot(voiceDebugRoot).render(
  <StrictMode>
    <VoiceDebugPanel />
  </StrictMode>,
)
