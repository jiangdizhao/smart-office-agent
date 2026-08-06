export type ConversationRecordingResult = {
  blob: Blob
  mimeType: string
  startedAt: number
  stoppedAt: number
  durationSeconds: number
}

type AudioContextWindow = Window & {
  webkitAudioContext?: new () => AudioContext
}

function supportedMimeType(): string {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
  return candidates.find((value) => MediaRecorder.isTypeSupported(value)) ?? ''
}

export class ConversationAudioRecorder {
  private context: AudioContext | null = null
  private destination: MediaStreamAudioDestinationNode | null = null
  private microphoneStream: MediaStream | null = null
  private microphoneSource: MediaStreamAudioSourceNode | null = null
  private mediaRecorder: MediaRecorder | null = null
  private chunks: Blob[] = []
  private startedAt = 0
  private lastResult: ConversationRecordingResult | null = null

  active(): boolean {
    return this.mediaRecorder?.state === 'recording'
  }

  result(): ConversationRecordingResult | null {
    return this.lastResult
  }

  async start(): Promise<void> {
    if (this.active()) return
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This browser cannot access the microphone.')
    }
    if (typeof MediaRecorder === 'undefined') {
      throw new Error('This browser does not support MediaRecorder.')
    }

    await this.dispose(false)
    const audioWindow = window as AudioContextWindow
    const AudioContextClass = window.AudioContext ?? audioWindow.webkitAudioContext
    if (!AudioContextClass) throw new Error('Web Audio is unavailable.')

    try {
      const context = new AudioContextClass()
      const destination = context.createMediaStreamDestination()
      const microphoneStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
        video: false,
      })
      const microphoneSource = context.createMediaStreamSource(microphoneStream)
      microphoneSource.connect(destination)

      this.context = context
      this.destination = destination
      this.microphoneStream = microphoneStream
      this.microphoneSource = microphoneSource
      this.chunks = []
      this.startedAt = Date.now()
      this.lastResult = null

      const mimeType = supportedMimeType()
      const recorder = mimeType
        ? new MediaRecorder(destination.stream, { mimeType })
        : new MediaRecorder(destination.stream)
      recorder.addEventListener('dataavailable', (event) => {
        if (event.data.size > 0) this.chunks.push(event.data)
      })
      this.mediaRecorder = recorder
      await context.resume().catch(() => undefined)
      recorder.start(5_000)
    } catch (error) {
      await this.dispose(false)
      throw error
    }
  }

  async stop(): Promise<ConversationRecordingResult | null> {
    const recorder = this.mediaRecorder
    if (!recorder) return this.lastResult
    if (recorder.state === 'inactive') {
      await this.dispose(false)
      return this.lastResult
    }

    const mimeType = recorder.mimeType || supportedMimeType() || 'audio/webm'
    await new Promise<void>((resolve, reject) => {
      const cleanup = () => {
        recorder.removeEventListener('stop', onStop)
        recorder.removeEventListener('error', onError)
      }
      const onStop = () => {
        cleanup()
        resolve()
      }
      const onError = (event: Event) => {
        cleanup()
        const recorderError = event as Event & { error?: DOMException }
        reject(new Error(recorderError.error?.message || 'Conversation recording failed.'))
      }
      recorder.addEventListener('stop', onStop)
      recorder.addEventListener('error', onError)
      recorder.requestData()
      recorder.stop()
    })

    const stoppedAt = Date.now()
    const result: ConversationRecordingResult = {
      blob: new Blob(this.chunks, { type: mimeType }),
      mimeType,
      startedAt: this.startedAt,
      stoppedAt,
      durationSeconds: Math.max(0, Math.round((stoppedAt - this.startedAt) / 1_000)),
    }
    this.lastResult = result
    await this.dispose(true)
    return result
  }

  download(filename?: string): boolean {
    const result = this.lastResult
    if (!result?.blob.size) return false
    const extension = result.mimeType.includes('mp4') ? 'm4a' : 'webm'
    const url = URL.createObjectURL(result.blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download =
      filename ??
      `human-conversation-${new Date(result.startedAt).toISOString().replace(/[:.]/g, '-')}.${extension}`
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 5_000)
    return true
  }

  async dispose(preserveResult = true): Promise<void> {
    if (this.mediaRecorder?.state === 'recording') {
      this.mediaRecorder.stop()
    }
    this.mediaRecorder = null
    this.microphoneSource?.disconnect()
    this.microphoneSource = null
    for (const track of this.microphoneStream?.getTracks() ?? []) track.stop()
    this.microphoneStream = null
    for (const track of this.destination?.stream.getTracks() ?? []) track.stop()
    this.destination = null
    await this.context?.close().catch(() => undefined)
    this.context = null
    this.chunks = []
    this.startedAt = 0
    if (!preserveResult) this.lastResult = null
  }
}
