import { realtimeAgent } from './realtimeAgentRuntime'

const PATCH_FLAG = '__smartOfficeProximityTimelineDiagnosticsInstalled__'

type DiagnosticWindow = Window & {
  [PATCH_FLAG]?: boolean
}

function diagnosticsEnabled(): boolean {
  const configured = String(import.meta.env.VITE_PROXIMITY_DEBUG ?? '')
    .trim()
    .toLowerCase()
  return (
    configured === 'true' ||
    configured === '1' ||
    configured === 'on' ||
    (configured !== 'false' &&
      configured !== '0' &&
      configured !== 'off' &&
      import.meta.env.DEV)
  )
}

function errorText(error: unknown): string {
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error)
}

function runtimeSnapshot(): Record<string, unknown> {
  const status = realtimeAgent.status()
  return {
    connected: status.connected,
    connectionState: status.connectionState,
    dataChannelState: status.dataChannelState,
    microphoneAttached: status.microphoneAttached,
    responseActive: status.responseActive,
    outputActive: status.outputActive,
  }
}

function info(event: string, data: Record<string, unknown> = {}): void {
  console.info(`[ProximityDebug] ${event}`, {
    wallTime: new Date().toISOString(),
    monotonicMs: Math.round(performance.now()),
    ...data,
  })
}

function failure(event: string, error: unknown, data: Record<string, unknown> = {}): void {
  console.error(`[ProximityDebug] ${event}`, {
    wallTime: new Date().toISOString(),
    monotonicMs: Math.round(performance.now()),
    message: errorText(error),
    ...data,
  })
}

function installBrowserEventTracing(): void {
  const eventNames = [
    'smartoffice:realtime-speaking-start',
    'smartoffice:realtime-speaking-stop',
    'smartoffice:realtime-listening-start',
    'smartoffice:realtime-listening-stop',
    'smartoffice:realtime-connected',
    'smartoffice:browser-listening-start',
    'smartoffice:browser-listening-stop',
    'smartoffice:visit-activated',
    'smartoffice:visit-revoked',
    'smartoffice:host-intro-start',
    'smartoffice:host-intro-cancel',
  ] as const

  for (const eventName of eventNames) {
    window.addEventListener(eventName, (event) => {
      const detail = event instanceof CustomEvent ? event.detail : undefined
      info('timeline-browser-event', {
        eventName,
        detail: detail ?? null,
        runtime: runtimeSnapshot(),
      })
    })
  }

  window.addEventListener('smartoffice:realtime-connection-state', (event) => {
    info('timeline-browser-event', {
      eventName: 'smartoffice:realtime-connection-state',
      detail: event instanceof CustomEvent ? event.detail ?? null : null,
      runtime: runtimeSnapshot(),
    })
  })

  window.addEventListener('unhandledrejection', (event) => {
    failure('timeline-unhandled-rejection', event.reason, {
      runtime: runtimeSnapshot(),
    })
  })

  window.addEventListener('error', (event) => {
    failure('timeline-window-error', event.error ?? event.message, {
      filename: event.filename,
      line: event.lineno,
      column: event.colno,
      runtime: runtimeSnapshot(),
    })
  })
}

export function installProximityTimelineDiagnostics(): void {
  if (!diagnosticsEnabled()) return
  const diagnosticWindow = window as DiagnosticWindow
  if (diagnosticWindow[PATCH_FLAG]) return
  diagnosticWindow[PATCH_FLAG] = true
  installBrowserEventTracing()
  info('timeline-diagnostics-installed', { runtime: runtimeSnapshot() })
}

installProximityTimelineDiagnostics()
