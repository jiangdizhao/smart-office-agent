type DataChannelPayload = string | Blob | ArrayBuffer | ArrayBufferView

type RealtimeResponseCreateEvent = {
  type?: unknown
  response?: {
    metadata?: unknown
    [key: string]: unknown
  }
  [key: string]: unknown
}

let installed = false

export function stringifyRealtimeResponseMetadata(data: string): string {
  let event: RealtimeResponseCreateEvent
  try {
    event = JSON.parse(data) as RealtimeResponseCreateEvent
  } catch {
    return data
  }

  if (event.type !== 'response.create') return data

  const response = event.response
  const metadata = response?.metadata
  if (!response || !metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return data
  }

  const normalizedMetadata = Object.fromEntries(
    Object.entries(metadata as Record<string, unknown>)
      .filter(([, value]) => value !== null && value !== undefined)
      .map(([key, value]) => [key, String(value)]),
  )

  return JSON.stringify({
    ...event,
    response: {
      ...response,
      metadata: normalizedMetadata,
    },
  })
}

/**
 * Realtime response metadata accepts string values only. Install one narrowly
 * scoped compatibility guard so every response.create event emitted by the
 * browser runtime satisfies that contract, including future diagnostics.
 */
export function installRealtimeMetadataCompatibility(): void {
  if (installed || typeof RTCDataChannel === 'undefined') return

  const prototype = RTCDataChannel.prototype
  const originalSend = prototype.send

  const patchedSend = function (this: RTCDataChannel, data: DataChannelPayload): void {
    const outgoing =
      typeof data === 'string' ? stringifyRealtimeResponseMetadata(data) : data
    originalSend.call(this, outgoing)
  }

  Object.defineProperty(prototype, 'send', {
    configurable: true,
    enumerable: false,
    writable: true,
    value: patchedSend,
  })

  installed = true
}
