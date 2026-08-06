import { useCallback, useEffect, useRef, useState } from 'react'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
import './virtualHostIntroVoiceGate'
import './VirtualHostVideoTheme.css'
import './VirtualHostSeamlessVideo.css'

export type VirtualHostVisualState =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'executing'
  | 'waiting-approval'
  | 'error'

type VirtualHostAvatarProps = {
  state: VirtualHostVisualState
}

type ClipKind =
  | 'idle-1'
  | 'idle-2'
  | 'idle-3'
  | 'intro'
  | 'talk-1'
  | 'talk-2'
  | 'talk-3'

type SequenceMode =
  | 'idle-cycle'
  | 'arrival-winddown'
  | 'arrival-ready'
  | 'introducing'
  | 'visitor-hold'
  | 'talk-in'
  | 'talk-loop'
  | 'talk-out'

type VideoClip = {
  id: string
  kind: ClipKind
  src: string
}

type TransitionOptions = {
  holdFirstFrame?: boolean
  loop?: boolean
  playbackRate?: number
  mode: SequenceMode
}

type TransitionRequest = {
  key: string
  clip: VideoClip
  options: TransitionOptions
  generation: number
  promise: Promise<boolean>
  resolve: (result: boolean) => void
}

type IntroRequest = {
  requestId: string
  visitId: string | null
  epoch: number | null
}

type IntroEventDetail = {
  requestId?: string
  visitId?: string | null
  epoch?: number | null
}

type AssetFailure = {
  clipId: string
  src: string
  message: string
  mediaCode: number | null
}

const VIDEO_BASE =
  (import.meta.env.VITE_VIRTUAL_HOST_VIDEO_BASE as string | undefined)?.replace(/\/$/, '') ??
  '/virtual-host-video'

function configuredInteger(value: unknown, fallback: number, minimum: number, maximum: number): number {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(minimum, Math.min(maximum, parsed))
}

function configuredRate(value: unknown, fallback: number, minimum: number, maximum: number): number {
  const parsed = Number.parseFloat(String(value ?? ''))
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(minimum, Math.min(maximum, parsed))
}

const IDLE_2_REPETITIONS = configuredInteger(
  import.meta.env.VITE_VIRTUAL_HOST_IDLE_2_REPETITIONS,
  3,
  1,
  8,
)
const ARRIVAL_CURRENT_RATE = configuredRate(
  import.meta.env.VITE_VIRTUAL_HOST_ARRIVAL_CURRENT_RATE,
  1.28,
  1,
  1.6,
)
const ARRIVAL_IDLE_3_RATE = configuredRate(
  import.meta.env.VITE_VIRTUAL_HOST_ARRIVAL_IDLE_3_RATE,
  1.18,
  1,
  1.45,
)
const TALK_LOOP_EXIT_RATE = configuredRate(
  import.meta.env.VITE_VIRTUAL_HOST_TALK_LOOP_EXIT_RATE,
  1.2,
  1,
  1.45,
)
const INTERRUPTED_TALK_OUT_RATE = configuredRate(
  import.meta.env.VITE_VIRTUAL_HOST_INTERRUPTED_TALK_OUT_RATE,
  1.28,
  1,
  1.6,
)
const MEDIA_READY_TIMEOUT_MS = 4_000

const CLIPS: Record<ClipKind, VideoClip> = {
  'idle-1': { id: 'idle-1', kind: 'idle-1', src: `${VIDEO_BASE}/idle-1.mp4` },
  'idle-2': { id: 'idle-2', kind: 'idle-2', src: `${VIDEO_BASE}/idle-2.mp4` },
  'idle-3': { id: 'idle-3', kind: 'idle-3', src: `${VIDEO_BASE}/idle-3.mp4` },
  intro: { id: 'intro', kind: 'intro', src: `${VIDEO_BASE}/intro.mp4` },
  'talk-1': { id: 'talk-1', kind: 'talk-1', src: `${VIDEO_BASE}/talk-1.mp4` },
  'talk-2': { id: 'talk-2', kind: 'talk-2', src: `${VIDEO_BASE}/talk-2.mp4` },
  'talk-3': { id: 'talk-3', kind: 'talk-3', src: `${VIDEO_BASE}/talk-3.mp4` },
}

function stateLabel(state: VirtualHostVisualState): string {
  const labels: Record<VirtualHostVisualState, string> = {
    idle: 'Ready',
    connecting: 'Connecting',
    listening: 'Listening',
    processing: 'Thinking',
    speaking: 'Speaking',
    executing: 'Working',
    'waiting-approval': 'Confirmation needed',
    error: 'Please retry',
  }
  return labels[state]
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function waitForLoadedData(video: HTMLVideoElement, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(abortError('Video transition was cancelled.'))
  if (video.readyState >= 2) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(
      () => finish(new Error('Video asset did not become ready.')),
      MEDIA_READY_TIMEOUT_MS,
    )
    const onReady = () => finish()
    const onError = () => {
      const code = video.error?.code ?? null
      finish(new Error(`Video asset failed to load${code ? ` (media code ${code})` : ''}.`))
    }
    const onAbort = () => finish(abortError('Video transition was cancelled.'))
    const finish = (error?: Error) => {
      window.clearTimeout(timer)
      video.removeEventListener('loadeddata', onReady)
      video.removeEventListener('error', onError)
      signal.removeEventListener('abort', onAbort)
      if (error) reject(error)
      else resolve()
    }
    video.addEventListener('loadeddata', onReady, { once: true })
    video.addEventListener('error', onError, { once: true })
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function seekToStart(video: HTMLVideoElement, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(abortError('Video transition was cancelled.'))
  if (!video.seeking && video.currentTime <= 0.001) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(
      () => finish(new Error('Video asset did not seek to its first frame.')),
      1_000,
    )
    const onSeeked = () => finish()
    const onError = () => {
      const code = video.error?.code ?? null
      finish(new Error(`Video asset failed while seeking${code ? ` (media code ${code})` : ''}.`))
    }
    const onAbort = () => finish(abortError('Video transition was cancelled.'))
    const finish = (error?: Error) => {
      window.clearTimeout(timer)
      video.removeEventListener('seeked', onSeeked)
      video.removeEventListener('error', onError)
      signal.removeEventListener('abort', onAbort)
      if (error) reject(error)
      else resolve()
    }
    video.addEventListener('seeked', onSeeked, { once: true })
    video.addEventListener('error', onError, { once: true })
    signal.addEventListener('abort', onAbort, { once: true })
    video.currentTime = 0
  })
}

function waitForPresentedFrame(video: HTMLVideoElement, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(abortError('Video transition was cancelled.'))
  const frameVideo = video as unknown as {
    requestVideoFrameCallback?: (callback: () => void) => number
  }
  if (frameVideo.requestVideoFrameCallback) {
    return new Promise((resolve, reject) => {
      let settled = false
      const finish = (error?: Error) => {
        if (settled) return
        settled = true
        window.clearTimeout(timer)
        signal.removeEventListener('abort', onAbort)
        if (error) reject(error)
        else resolve()
      }
      const onAbort = () => finish(abortError('Video transition was cancelled.'))
      const timer = window.setTimeout(() => finish(), 350)
      signal.addEventListener('abort', onAbort, { once: true })
      frameVideo.requestVideoFrameCallback?.(() => finish())
    })
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(abortError('Video transition was cancelled.'))
    signal.addEventListener('abort', onAbort, { once: true })
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        signal.removeEventListener('abort', onAbort)
        if (signal.aborted) reject(abortError('Video transition was cancelled.'))
        else resolve()
      })
    })
  })
}

function isIdleClip(clip: VideoClip | null): boolean {
  return Boolean(clip && clip.kind.startsWith('idle-'))
}

function isTalkClip(clip: VideoClip | null): boolean {
  return Boolean(clip && clip.kind.startsWith('talk-'))
}

function transitionKey(clip: VideoClip, options: TransitionOptions): string {
  return [
    clip.id,
    options.mode,
    options.holdFirstFrame ? 'hold' : 'play',
    options.loop ? 'loop' : 'once',
    String(options.playbackRate ?? 1),
  ].join('|')
}

function mediaFailure(clip: VideoClip, video: HTMLVideoElement, error: unknown): AssetFailure {
  return {
    clipId: clip.id,
    src: clip.src,
    message: error instanceof Error ? error.message : String(error),
    mediaCode: video.error?.code ?? null,
  }
}

export default function VirtualHostAvatar({ state }: VirtualHostAvatarProps) {
  const firstVideoRef = useRef<HTMLVideoElement>(null)
  const secondVideoRef = useRef<HTMLVideoElement>(null)
  const activeLayerRef = useRef<0 | 1>(0)
  const activeClipRef = useRef<VideoClip | null>(null)
  const stateRef = useRef(state)
  const visitorPresentRef = useRef(Boolean(visitLeaseRegistry.current()))
  const userSpeakingRef = useRef(false)
  const speechActiveRef = useRef(state === 'speaking')
  const arrivalPendingRef = useRef(false)
  const introRequestRef = useRef<IntroRequest | null>(null)
  const introActiveRef = useRef(false)
  const introStartingRef = useRef(false)
  const transitionGenerationRef = useRef(0)
  const transitionWorkerRunningRef = useRef(false)
  const currentTransitionRef = useRef<TransitionRequest | null>(null)
  const queuedTransitionRef = useRef<TransitionRequest | null>(null)
  const lifecycleAbortRef = useRef(new AbortController())
  const mountedRef = useRef(false)
  const initialStateEffectRef = useRef(true)
  const sequenceModeRef = useRef<SequenceMode>('idle-cycle')
  const idle2PlayCountRef = useRef(0)
  const [activeLayer, setActiveLayer] = useState<0 | 1>(0)
  const [introActive, setIntroActive] = useState(false)
  const [sequenceMode, setSequenceModeState] = useState<SequenceMode>('idle-cycle')
  const [assetFailure, setAssetFailure] = useState<AssetFailure | null>(null)

  const setSequenceMode = useCallback((mode: SequenceMode) => {
    sequenceModeRef.current = mode
    setSequenceModeState(mode)
  }, [])

  const videoAt = useCallback(
    (layer: 0 | 1): HTMLVideoElement | null =>
      layer === 0 ? firstVideoRef.current : secondVideoRef.current,
    [],
  )

  const activeVideo = useCallback(
    (): HTMLVideoElement | null => videoAt(activeLayerRef.current),
    [videoAt],
  )

  const performTransition = useCallback(
    async (request: TransitionRequest): Promise<boolean> => {
      const previousLayer = activeLayerRef.current
      const nextLayer: 0 | 1 = previousLayer === 0 ? 1 : 0
      const previousVideo = videoAt(previousLayer)
      const nextVideo = videoAt(nextLayer)
      const signal = lifecycleAbortRef.current.signal
      if (!nextVideo || signal.aborted || request.generation !== transitionGenerationRef.current) {
        return false
      }

      nextVideo.pause()
      nextVideo.loop = Boolean(request.options.loop)
      nextVideo.playbackRate = request.options.playbackRate ?? 1
      nextVideo.defaultPlaybackRate = request.options.playbackRate ?? 1
      nextVideo.muted = true
      nextVideo.playsInline = true
      nextVideo.preload = 'auto'
      nextVideo.dataset.clipId = request.clip.id
      nextVideo.dataset.transitionKey = request.key

      const expectedSrc = new URL(request.clip.src, window.location.href).href
      if (nextVideo.src !== expectedSrc) {
        nextVideo.src = request.clip.src
        nextVideo.load()
      }

      try {
        await waitForLoadedData(nextVideo, signal)
        if (request.generation !== transitionGenerationRef.current) return false
        await seekToStart(nextVideo, signal)
        if (request.generation !== transitionGenerationRef.current) return false

        if (request.options.holdFirstFrame) {
          nextVideo.pause()
        } else {
          await nextVideo.play()
          await waitForPresentedFrame(nextVideo, signal)
        }
        if (signal.aborted || request.generation !== transitionGenerationRef.current) return false

        activeLayerRef.current = nextLayer
        activeClipRef.current = request.clip
        setActiveLayer(nextLayer)
        setSequenceMode(request.options.mode)
        setAssetFailure(null)

        window.requestAnimationFrame(() => {
          if (!mountedRef.current || activeLayerRef.current !== nextLayer) return
          previousVideo?.pause()
          if (previousVideo) {
            previousVideo.loop = false
            previousVideo.playbackRate = 1
            previousVideo.defaultPlaybackRate = 1
          }
        })
        return true
      } catch (error) {
        if (
          isAbortError(error)
          || signal.aborted
          || request.generation !== transitionGenerationRef.current
        ) {
          return false
        }
        const failure = mediaFailure(request.clip, nextVideo, error)
        console.error('[VirtualHostVideo] could not prepare clip', failure)
        setAssetFailure(failure)
        return false
      }
    },
    [setSequenceMode, videoAt],
  )

  const drainTransitions = useCallback(async (): Promise<void> => {
    if (transitionWorkerRunningRef.current) return
    transitionWorkerRunningRef.current = true
    try {
      while (mountedRef.current && queuedTransitionRef.current) {
        const request = queuedTransitionRef.current
        queuedTransitionRef.current = null
        currentTransitionRef.current = request
        const result = await performTransition(request)
        request.resolve(result)
        if (currentTransitionRef.current === request) currentTransitionRef.current = null
      }
    } finally {
      transitionWorkerRunningRef.current = false
      if (mountedRef.current && queuedTransitionRef.current) void drainTransitions()
    }
  }, [performTransition])

  const transitionTo = useCallback(
    (clip: VideoClip, options: TransitionOptions): Promise<boolean> => {
      const key = transitionKey(clip, options)
      const current = currentTransitionRef.current
      if (current?.key === key && current.generation === transitionGenerationRef.current) {
        return current.promise
      }
      const queued = queuedTransitionRef.current
      if (queued?.key === key && queued.generation === transitionGenerationRef.current) {
        return queued.promise
      }

      let resolveRequest: (result: boolean) => void = () => undefined
      const promise = new Promise<boolean>((resolve) => {
        resolveRequest = resolve
      })
      const request: TransitionRequest = {
        key,
        clip,
        options,
        generation: transitionGenerationRef.current,
        promise,
        resolve: resolveRequest,
      }

      if (queuedTransitionRef.current) queuedTransitionRef.current.resolve(false)
      queuedTransitionRef.current = request
      void drainTransitions()
      return promise
    },
    [drainTransitions],
  )

  const invalidateTransitions = useCallback(() => {
    transitionGenerationRef.current += 1
    if (queuedTransitionRef.current) {
      queuedTransitionRef.current.resolve(false)
      queuedTransitionRef.current = null
    }
  }, [])

  const startIdleCycle = useCallback(() => {
    if (visitorPresentRef.current || introActiveRef.current || arrivalPendingRef.current) return
    idle2PlayCountRef.current = 0
    void transitionTo(CLIPS['idle-1'], { mode: 'idle-cycle' })
  }, [transitionTo])

  const holdVisitorPose = useCallback(() => {
    if (!visitorPresentRef.current || introActiveRef.current || arrivalPendingRef.current) return
    const currentVideo = activeVideo()
    if (
      activeClipRef.current?.kind === 'talk-1'
      && sequenceModeRef.current === 'visitor-hold'
      && currentVideo?.paused
    ) return
    void transitionTo(CLIPS['talk-1'], {
      holdFirstFrame: true,
      mode: 'visitor-hold',
    })
  }, [activeVideo, transitionTo])

  const startTalkIn = useCallback(() => {
    speechActiveRef.current = true
    if (introActiveRef.current || arrivalPendingRef.current || introStartingRef.current) return
    const current = activeClipRef.current
    if (current?.kind === 'talk-2' && sequenceModeRef.current === 'talk-loop') return
    if (current?.kind === 'talk-1' && sequenceModeRef.current === 'talk-in' && !activeVideo()?.paused) return
    void transitionTo(CLIPS['talk-1'], { mode: 'talk-in' })
  }, [activeVideo, transitionTo])

  const startTalkOut = useCallback((interrupted: boolean) => {
    speechActiveRef.current = false
    if (introActiveRef.current || arrivalPendingRef.current || introStartingRef.current) return
    const current = activeClipRef.current
    if (!isTalkClip(current)) {
      if (visitorPresentRef.current) holdVisitorPose()
      else startIdleCycle()
      return
    }
    if (current?.kind === 'talk-3') return

    if (current?.kind === 'talk-1' && !interrupted) {
      setSequenceMode('talk-out')
      return
    }

    if (current?.kind === 'talk-2' && !interrupted) {
      const video = activeVideo()
      if (video) {
        video.loop = false
        video.playbackRate = TALK_LOOP_EXIT_RATE
        video.defaultPlaybackRate = TALK_LOOP_EXIT_RATE
        setSequenceMode('talk-out')
        return
      }
    }

    if (interrupted) invalidateTransitions()
    void transitionTo(CLIPS['talk-3'], {
      playbackRate: interrupted ? INTERRUPTED_TALK_OUT_RATE : 1,
      mode: 'talk-out',
    })
  }, [
    activeVideo,
    holdVisitorPose,
    invalidateTransitions,
    setSequenceMode,
    startIdleCycle,
    transitionTo,
  ])

  const startIntro = useCallback(async (): Promise<void> => {
    const request = introRequestRef.current
    if (!request || introStartingRef.current || !visitorPresentRef.current) return
    introStartingRef.current = true
    arrivalPendingRef.current = false
    introActiveRef.current = true
    setIntroActive(true)
    const started = await transitionTo(CLIPS.intro, { mode: 'introducing' })
    introStartingRef.current = false

    if (!started || !introActiveRef.current || introRequestRef.current?.requestId !== request.requestId) {
      if (!started && introRequestRef.current?.requestId === request.requestId) {
        introActiveRef.current = false
        setIntroActive(false)
        window.dispatchEvent(new CustomEvent('smartoffice:host-intro-playback-failed', {
          detail: request,
        }))
      }
      return
    }

    window.dispatchEvent(new CustomEvent('smartoffice:host-intro-playback-started', {
      detail: request,
    }))
  }, [transitionTo])

  const accelerateArrival = useCallback(() => {
    const clip = activeClipRef.current
    const video = activeVideo()
    if (!clip || !video || !isIdleClip(clip)) {
      void transitionTo(CLIPS['idle-3'], {
        playbackRate: ARRIVAL_IDLE_3_RATE,
        mode: 'arrival-winddown',
      })
      return
    }
    video.playbackRate = clip.kind === 'idle-3' ? ARRIVAL_IDLE_3_RATE : ARRIVAL_CURRENT_RATE
    video.defaultPlaybackRate = video.playbackRate
    setSequenceMode('arrival-winddown')
    if (
      clip.kind === 'idle-3'
      && (video.ended || (video.duration > 0 && video.currentTime >= video.duration - 0.05))
    ) {
      void startIntro()
    }
  }, [activeVideo, setSequenceMode, startIntro, transitionTo])

  useEffect(() => {
    mountedRef.current = true
    lifecycleAbortRef.current = new AbortController()
    if (visitorPresentRef.current) holdVisitorPose()
    else startIdleCycle()

    return () => {
      mountedRef.current = false
      lifecycleAbortRef.current.abort()
      invalidateTransitions()
      currentTransitionRef.current?.resolve(false)
      currentTransitionRef.current = null
      firstVideoRef.current?.pause()
      secondVideoRef.current?.pause()
    }
  }, [holdVisitorPose, invalidateTransitions, startIdleCycle])

  useEffect(() => {
    if (initialStateEffectRef.current) {
      initialStateEffectRef.current = false
      stateRef.current = state
      return
    }

    const previous = stateRef.current
    stateRef.current = state

    if (state === 'speaking') {
      startTalkIn()
      return
    }
    if (previous === 'speaking') {
      startTalkOut(false)
      return
    }
    if (state === 'listening' || state === 'processing') {
      if (!speechActiveRef.current) holdVisitorPose()
      return
    }
    if (!speechActiveRef.current && !introActiveRef.current && !arrivalPendingRef.current) {
      if (visitorPresentRef.current) holdVisitorPose()
      else if (!isIdleClip(activeClipRef.current)) startIdleCycle()
    }
  }, [holdVisitorPose, startIdleCycle, startTalkIn, startTalkOut, state])

  useEffect(() => {
    const onVisitActivated = () => {
      visitorPresentRef.current = true
      arrivalPendingRef.current = true
      introRequestRef.current = null
      idle2PlayCountRef.current = 0
      accelerateArrival()
    }
    const onVisitRevoked = () => {
      visitorPresentRef.current = false
      userSpeakingRef.current = false
      speechActiveRef.current = false
      arrivalPendingRef.current = false
      introRequestRef.current = null
      introActiveRef.current = false
      introStartingRef.current = false
      setIntroActive(false)
      invalidateTransitions()
      startIdleCycle()
    }
    const onIntroStart = (event: Event) => {
      const rawDetail = event instanceof CustomEvent ? event.detail : null
      const detail = rawDetail && typeof rawDetail === 'object'
        ? rawDetail as IntroEventDetail
        : {}
      const requestId = String(detail.requestId ?? crypto.randomUUID())
      introRequestRef.current = {
        requestId,
        visitId: detail.visitId == null ? null : String(detail.visitId),
        epoch: detail.epoch == null ? null : Number(detail.epoch),
      }
      visitorPresentRef.current = true
      arrivalPendingRef.current = true
      accelerateArrival()
      const clip = activeClipRef.current
      const video = activeVideo()
      if (clip?.kind === 'idle-3' && video && (video.ended || video.paused)) void startIntro()
    }
    const onIntroCancel = () => {
      arrivalPendingRef.current = false
      introRequestRef.current = null
      introActiveRef.current = false
      introStartingRef.current = false
      setIntroActive(false)
      invalidateTransitions()
      if (visitorPresentRef.current) holdVisitorPose()
      else startIdleCycle()
    }
    const onSpeechStarted = () => startTalkIn()
    const onSpeechCompleted = () => startTalkOut(false)
    const onSpeechInterrupted = () => startTalkOut(true)
    const onUserSpeechStarted = () => {
      const wasSpeaking = speechActiveRef.current || stateRef.current === 'speaking'
      userSpeakingRef.current = true
      if (wasSpeaking) startTalkOut(true)
      else holdVisitorPose()
    }
    const onUserSpeechStopped = () => {
      userSpeakingRef.current = false
      if (!speechActiveRef.current && !introActiveRef.current && !arrivalPendingRef.current) {
        holdVisitorPose()
      }
    }

    window.addEventListener('smartoffice:visit-activated', onVisitActivated)
    window.addEventListener('smartoffice:visit-revoked', onVisitRevoked)
    window.addEventListener('smartoffice:host-intro-start', onIntroStart)
    window.addEventListener('smartoffice:host-intro-cancel', onIntroCancel)
    window.addEventListener('smartoffice:assistant-output-started', onSpeechStarted)
    window.addEventListener('smartoffice:assistant-output-completed', onSpeechCompleted)
    window.addEventListener('smartoffice:assistant-output-interrupted', onSpeechInterrupted)
    window.addEventListener('smartoffice:assistant-output-failed', onSpeechInterrupted)
    window.addEventListener('smartoffice:realtime-vad-speech-started', onUserSpeechStarted)
    window.addEventListener('smartoffice:realtime-vad-speech-stopped', onUserSpeechStopped)
    return () => {
      window.removeEventListener('smartoffice:visit-activated', onVisitActivated)
      window.removeEventListener('smartoffice:visit-revoked', onVisitRevoked)
      window.removeEventListener('smartoffice:host-intro-start', onIntroStart)
      window.removeEventListener('smartoffice:host-intro-cancel', onIntroCancel)
      window.removeEventListener('smartoffice:assistant-output-started', onSpeechStarted)
      window.removeEventListener('smartoffice:assistant-output-completed', onSpeechCompleted)
      window.removeEventListener('smartoffice:assistant-output-interrupted', onSpeechInterrupted)
      window.removeEventListener('smartoffice:assistant-output-failed', onSpeechInterrupted)
      window.removeEventListener('smartoffice:realtime-vad-speech-started', onUserSpeechStarted)
      window.removeEventListener('smartoffice:realtime-vad-speech-stopped', onUserSpeechStopped)
    }
  }, [
    accelerateArrival,
    activeVideo,
    holdVisitorPose,
    invalidateTransitions,
    startIdleCycle,
    startIntro,
    startTalkIn,
    startTalkOut,
  ])

  function handleEnded(layer: 0 | 1): void {
    if (layer !== activeLayerRef.current) return
    const clip = activeClipRef.current
    if (!clip) return

    if (clip.kind === 'intro') {
      const request = introRequestRef.current
      introActiveRef.current = false
      introRequestRef.current = null
      setIntroActive(false)
      window.dispatchEvent(new CustomEvent('smartoffice:host-intro-ended', {
        detail: request,
      }))
      if (speechActiveRef.current) startTalkIn()
      else if (visitorPresentRef.current) holdVisitorPose()
      else startIdleCycle()
      return
    }

    if (clip.kind === 'idle-1') {
      if (arrivalPendingRef.current) {
        void transitionTo(CLIPS['idle-3'], {
          playbackRate: ARRIVAL_IDLE_3_RATE,
          mode: 'arrival-winddown',
        })
      } else {
        idle2PlayCountRef.current = 1
        void transitionTo(CLIPS['idle-2'], { mode: 'idle-cycle' })
      }
      return
    }

    if (clip.kind === 'idle-2') {
      if (arrivalPendingRef.current) {
        void transitionTo(CLIPS['idle-3'], {
          playbackRate: ARRIVAL_IDLE_3_RATE,
          mode: 'arrival-winddown',
        })
      } else if (idle2PlayCountRef.current < IDLE_2_REPETITIONS) {
        idle2PlayCountRef.current += 1
        void transitionTo(CLIPS['idle-2'], { mode: 'idle-cycle' })
      } else {
        void transitionTo(CLIPS['idle-3'], { mode: 'idle-cycle' })
      }
      return
    }

    if (clip.kind === 'idle-3') {
      if (arrivalPendingRef.current) {
        if (introRequestRef.current) void startIntro()
        else setSequenceMode('arrival-ready')
      } else {
        startIdleCycle()
      }
      return
    }

    if (clip.kind === 'talk-1') {
      if (speechActiveRef.current) {
        void transitionTo(CLIPS['talk-2'], {
          loop: true,
          mode: 'talk-loop',
        })
      } else {
        void transitionTo(CLIPS['talk-3'], { mode: 'talk-out' })
      }
      return
    }

    if (clip.kind === 'talk-2') {
      void transitionTo(CLIPS['talk-3'], { mode: 'talk-out' })
      return
    }

    if (speechActiveRef.current) startTalkIn()
    else if (visitorPresentRef.current) holdVisitorPose()
    else startIdleCycle()
  }

  function handleVideoError(layer: 0 | 1, video: HTMLVideoElement): void {
    const code = video.error?.code ?? null
    if (code === 1) return

    const clipId = String(video.dataset.clipId ?? '').trim()
    const relevant =
      layer === activeLayerRef.current
      || currentTransitionRef.current?.clip.id === clipId
    if (!clipId || !relevant) return

    const clip = CLIPS[clipId as ClipKind]
    if (!clip) return
    const failure: AssetFailure = {
      clipId,
      src: clip.src,
      message: code
        ? `Browser media error ${code}.`
        : 'The browser could not load this video.',
      mediaCode: code,
    }
    console.error('[VirtualHostVideo] media element error', failure)
    setAssetFailure(failure)
  }

  return (
    <div
      className={`virtual-host-avatar video-host-avatar avatar-${state} avatar-sequence-${sequenceMode} ${introActive ? 'avatar-introducing' : ''}`}
      role="img"
      aria-label="Smart Office virtual receptionist"
    >
      <div className="video-avatar-halo" aria-hidden="true" />
      <div className="video-avatar-frame">
        {([0, 1] as const).map((layer) => (
          <video
            key={layer}
            ref={layer === 0 ? firstVideoRef : secondVideoRef}
            className={`video-avatar-layer ${activeLayer === layer ? 'active' : ''}`}
            muted
            playsInline
            preload="auto"
            aria-hidden="true"
            onEnded={() => handleEnded(layer)}
            onError={(event) => handleVideoError(layer, event.currentTarget)}
          />
        ))}

        <div className="video-avatar-vignette" aria-hidden="true" />
        <div className="video-avatar-topline">
          <span className="video-avatar-live-dot" />
          <span>{introActive ? 'Welcoming visitor' : stateLabel(state)}</span>
        </div>

        {assetFailure ? (
          <div className="video-avatar-fallback" role="status">
            <strong>Virtual host video could not be loaded</strong>
            <span>
              {assetFailure.clipId}.mp4 · {assetFailure.message}
              {assetFailure.mediaCode ? ` Media code: ${assetFailure.mediaCode}.` : ''}
            </span>
          </div>
        ) : null}
      </div>

      <div className="video-avatar-audio-bars" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
        <span />
      </div>
      <div className="video-avatar-shadow" aria-hidden="true" />
    </div>
  )
}
