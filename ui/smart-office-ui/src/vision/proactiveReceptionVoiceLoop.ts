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

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'))
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup()
      resolve()
    }, milliseconds)
    const onAbort = () => {
      window.clearTimeout(timer)
      cleanup()
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const cleanup = () => signal?.removeEventListener('abort', onAbort)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

async function waitForUtterance(
  stream: MediaStream,
  signal: AbortSignal,
): Promise<VadResult> {
  let context: AudioContext | null = null
  try {
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
        await wait(SAMPLE_INTERVAL_MS, signal)
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

      await wait(SAMPLE_INTERVAL_MS, signal)
    }
    return 'aborted'
  } catch (error) {
    if (signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
      return 'aborted'
    }
    throw error
  } finally {
    if (context && context.state !== 'closed') await context.close().catch(() => undefined)
  }
}

async function waitForControllerTranscript(
  controller: () => OfficeVoiceController,
  previousTranscript: string,
  signal: AbortSignal,
): Promise<string> {
  const deadline = performance.now() + 1_500
  while (performance.now() < deadline && !signal.aborted) {
    const current = controller().transcript.trim()
    if (current && current !== previousTranscript.trim()) return current
    await wait(50, signal)
  }
  return controller().transcript.trim()
}

async function settleAbortedController(
  controller: () => OfficeVoiceController,
  releaseMicrophone: boolean,
): Promise<void> {
  await realtimeAgent.abortCapture(releaseMicrophone).catch(() => undefined)
  await controller().stopSpeaking().catch(() => undefined)
}

export async function captureAutomaticRealtimeTurn(
  controller: () => OfficeVoiceController,
  signal: AbortSignal,
): Promise<AutomaticVoiceTurnResult> {
  if (signal.aborted) return { kind: 'aborted' }
  const previousTranscript = controller().transcript

  try {
    // beginListening obtains the single physical microphone stream and attaches
    // it to WebRTC. The same stream is then observed locally by VAD; no second
    // getUserMedia() request is made.
    await controller().beginListening()
    if (signal.aborted) {
      await settleAbortedController(controller, true)
      return { kind: 'aborted' }
    }

    const stream = realtimeAgent.currentMicrophoneStream()
    if (!stream || !realtimeAgent.status().microphoneAttached) {
      await settleAbortedController(controller, true)
      return {
        kind: 'error',
        message: controller().error || 'GPT Realtime microphone capture did not start.',
      }
    }

    const vadResult = await waitForUtterance(stream, signal)
    if (vadResult === 'aborted' || signal.aborted) {
      await settleAbortedController(controller, true)
      return { kind: 'aborted' }
    }
    if (vadResult === 'silence') {
      // Keep the single stream only until the controller has returned to idle;
      // the next turn may reuse it if the browser keeps it live.
      await settleAbortedController(controller, false)
      return { kind: 'silence' }
    }

    await controller().endListening()
    if (signal.aborted) return { kind: 'aborted' }
    const transcript = await waitForControllerTranscript(controller, previousTranscript, signal)
    return { kind: 'heard', transcript }
  } catch (error) {
    await settleAbortedController(controller, true)
    if (signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
      return { kind: 'aborted' }
    }
    return { kind: 'error', message: errorText(error) }
  }
}
