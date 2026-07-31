import type { OfficeVoiceController } from '../voice/useOfficeVoiceController'
import { realtimeAgent } from '../voice/realtimeAgentRuntime'

export type AutomaticVoiceTurnResult =
  | { kind: 'heard'; transcript: string }
  | { kind: 'silence' }
  | { kind: 'aborted' }
  | { kind: 'error'; message: string }

type VadResult = 'speech_complete' | 'silence' | 'aborted'

const CALIBRATION_MS = 500
const SPEECH_START_TIMEOUT_MS = 12_000
const MAX_UTTERANCE_MS = 25_000
const END_SILENCE_MS = 900
const SAMPLE_INTERVAL_MS = 50
const MIN_ABSOLUTE_RMS = 0.018
const SPEECH_CONFIRM_SAMPLES = 3

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

async function closeAudioResources(
  context: AudioContext | null,
  stream: MediaStream | null,
): Promise<void> {
  for (const track of stream?.getTracks() ?? []) track.stop()
  if (context && context.state !== 'closed') {
    await context.close().catch(() => undefined)
  }
}

async function waitForUtterance(signal: AbortSignal): Promise<VadResult> {
  let stream: MediaStream | null = null
  let context: AudioContext | null = null
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    })
    if (signal.aborted) return 'aborted'

    context = new AudioContext()
    const source = context.createMediaStreamSource(stream)
    const analyser = context.createAnalyser()
    analyser.fftSize = 1024
    analyser.smoothingTimeConstant = 0.2
    source.connect(analyser)

    const samples = new Float32Array(analyser.fftSize)
    const startedAt = performance.now()
    const calibrationEndsAt = startedAt + CALIBRATION_MS
    let noiseSum = 0
    let noiseSamples = 0
    let speechStartedAt = 0
    let lastSpeechAt = 0
    let consecutiveSpeechSamples = 0

    while (!signal.aborted) {
      const now = performance.now()
      analyser.getFloatTimeDomainData(samples)
      let energy = 0
      for (const sample of samples) energy += sample * sample
      const rms = Math.sqrt(energy / samples.length)

      if (now < calibrationEndsAt) {
        noiseSum += rms
        noiseSamples += 1
        await wait(SAMPLE_INTERVAL_MS)
        continue
      }

      const noiseFloor = noiseSamples > 0 ? noiseSum / noiseSamples : 0
      const speechThreshold = Math.max(MIN_ABSOLUTE_RMS, noiseFloor * 2.8)
      const speechNow = rms >= speechThreshold

      if (!speechStartedAt) {
        consecutiveSpeechSamples = speechNow ? consecutiveSpeechSamples + 1 : 0
        if (consecutiveSpeechSamples >= SPEECH_CONFIRM_SAMPLES) {
          speechStartedAt = now
          lastSpeechAt = now
        } else if (now - calibrationEndsAt >= SPEECH_START_TIMEOUT_MS) {
          return 'silence'
        }
      } else {
        if (speechNow) lastSpeechAt = now
        if (now - lastSpeechAt >= END_SILENCE_MS) return 'speech_complete'
        if (now - speechStartedAt >= MAX_UTTERANCE_MS) return 'speech_complete'
      }

      await wait(SAMPLE_INTERVAL_MS)
    }
    return 'aborted'
  } finally {
    await closeAudioResources(context, stream)
  }
}

async function waitForControllerTranscript(
  controller: () => OfficeVoiceController,
  previousTranscript: string,
): Promise<string> {
  const deadline = performance.now() + 1_500
  while (performance.now() < deadline) {
    const current = controller().transcript.trim()
    if (current && current !== previousTranscript.trim()) return current
    await wait(50)
  }
  return controller().transcript.trim()
}

export async function captureAutomaticRealtimeTurn(
  controller: () => OfficeVoiceController,
  signal: AbortSignal,
): Promise<AutomaticVoiceTurnResult> {
  if (signal.aborted) return { kind: 'aborted' }

  const previousTranscript = controller().transcript
  const vadController = new AbortController()
  const abortVad = () => vadController.abort()
  signal.addEventListener('abort', abortVad, { once: true })

  try {
    const vadPromise = waitForUtterance(vadController.signal)
    await controller().beginListening()
    await wait(200)

    if (signal.aborted) {
      vadController.abort()
      await realtimeAgent.abortCapture().catch(() => undefined)
      await controller().stopSpeaking().catch(() => undefined)
      return { kind: 'aborted' }
    }

    if (!realtimeAgent.status().microphoneAttached) {
      vadController.abort()
      return {
        kind: 'error',
        message: controller().error || 'GPT Realtime microphone capture did not start.',
      }
    }

    const vadResult = await vadPromise
    if (vadResult === 'aborted' || signal.aborted) {
      await realtimeAgent.abortCapture().catch(() => undefined)
      await controller().stopSpeaking().catch(() => undefined)
      return { kind: 'aborted' }
    }

    if (vadResult === 'silence') {
      await realtimeAgent.abortCapture().catch(() => undefined)
      await controller().stopSpeaking().catch(() => undefined)
      return { kind: 'silence' }
    }

    await controller().endListening()
    const transcript = await waitForControllerTranscript(controller, previousTranscript)
    return { kind: 'heard', transcript }
  } catch (error) {
    vadController.abort()
    await realtimeAgent.abortCapture().catch(() => undefined)
    await controller().stopSpeaking().catch(() => undefined)
    return { kind: 'error', message: errorText(error) }
  } finally {
    signal.removeEventListener('abort', abortVad)
    vadController.abort()
  }
}
