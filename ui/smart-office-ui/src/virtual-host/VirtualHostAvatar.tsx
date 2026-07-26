import { useCallback, useEffect, useRef, useState } from 'react'
import './VirtualHostVideoTheme.css'

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

type ClipKind = 'intro' | 'idle-primary' | 'idle-rare' | 'talk'

type VideoClip = {
  id: string
  kind: ClipKind
  src: string
}

const VIDEO_BASE =
  (import.meta.env.VITE_VIRTUAL_HOST_VIDEO_BASE as string | undefined)?.replace(/\/$/, '') ??
  '/virtual-host-video'
const CROSSFADE_MS = 260
const PRIMARY_IDLE_LOOPS_PER_RARE_GESTURE = 5

const INTRO_CLIP: VideoClip = {
  id: 'intro',
  kind: 'intro',
  src: `${VIDEO_BASE}/intro.mp4`,
}
const IDLE_PRIMARY: VideoClip = {
  id: 'idle-primary',
  kind: 'idle-primary',
  src: `${VIDEO_BASE}/idle-primary.mp4`,
}
const IDLE_RARE: VideoClip = {
  id: 'idle-rare',
  kind: 'idle-rare',
  src: `${VIDEO_BASE}/idle-rare.mp4`,
}
const TALK_CLIPS: VideoClip[] = [
  { id: 'talk-a', kind: 'talk', src: `${VIDEO_BASE}/talk-a.mp4` },
  { id: 'talk-b', kind: 'talk', src: `${VIDEO_BASE}/talk-b.mp4` },
  { id: 'talk-c', kind: 'talk', src: `${VIDEO_BASE}/talk-c.mp4` },
]

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

export default function VirtualHostAvatar({ state }: VirtualHostAvatarProps) {
  const firstVideoRef = useRef<HTMLVideoElement>(null)
  const secondVideoRef = useRef<HTMLVideoElement>(null)
  const activeLayerRef = useRef<0 | 1>(0)
  const activeClipRef = useRef<VideoClip | null>(null)
  const stateRef = useRef(state)
  const introActiveRef = useRef(false)
  const transitionSerialRef = useRef(0)
  const idlePrimaryLoopCountRef = useRef(0)
  const talkIndexRef = useRef(0)
  const pauseTimerRef = useRef<number | null>(null)
  const [activeLayer, setActiveLayer] = useState<0 | 1>(0)
  const [layerClips, setLayerClips] = useState<[VideoClip | null, VideoClip | null]>([
    null,
    null,
  ])
  const [introActive, setIntroActive] = useState(false)
  const [assetError, setAssetError] = useState(false)

  const videoAt = useCallback(
    (layer: 0 | 1): HTMLVideoElement | null =>
      layer === 0 ? firstVideoRef.current : secondVideoRef.current,
    [],
  )

  useEffect(() => {
    stateRef.current = state
  }, [state])

  const transitionTo = useCallback(
    async (clip: VideoClip, restart = false): Promise<void> => {
      const currentClip = activeClipRef.current
      if (!restart && currentClip?.id === clip.id) return

      const serial = transitionSerialRef.current + 1
      transitionSerialRef.current = serial
      const previousLayer = activeLayerRef.current
      const nextLayer: 0 | 1 = previousLayer === 0 ? 1 : 0
      const nextVideo = videoAt(nextLayer)
      if (!nextVideo) return

      if (pauseTimerRef.current !== null) {
        window.clearTimeout(pauseTimerRef.current)
        pauseTimerRef.current = null
      }

      setLayerClips((current) => {
        const next: [VideoClip | null, VideoClip | null] = [...current]
        next[nextLayer] = clip
        return next
      })

      nextVideo.pause()
      nextVideo.src = clip.src
      nextVideo.currentTime = 0
      nextVideo.muted = true
      nextVideo.playsInline = true
      nextVideo.preload = 'auto'

      try {
        await nextVideo.play()
        if (transitionSerialRef.current !== serial) return
        setAssetError(false)
        activeLayerRef.current = nextLayer
        activeClipRef.current = clip
        setActiveLayer(nextLayer)
        pauseTimerRef.current = window.setTimeout(() => {
          videoAt(previousLayer)?.pause()
          pauseTimerRef.current = null
        }, CROSSFADE_MS + 90)
      } catch (error) {
        if (transitionSerialRef.current !== serial) return
        console.error('[VirtualHostVideo] could not play clip', clip.src, error)
        setAssetError(true)
      }
    },
    [videoAt],
  )

  const startIdleSequence = useCallback(() => {
    idlePrimaryLoopCountRef.current = 0
    void transitionTo(IDLE_PRIMARY, true)
  }, [transitionTo])

  const startTalkingSequence = useCallback(() => {
    talkIndexRef.current = 0
    void transitionTo(TALK_CLIPS[0], true)
  }, [transitionTo])

  useEffect(() => {
    startIdleSequence()
    return () => {
      transitionSerialRef.current += 1
      if (pauseTimerRef.current !== null) window.clearTimeout(pauseTimerRef.current)
      firstVideoRef.current?.pause()
      secondVideoRef.current?.pause()
    }
  }, [startIdleSequence])

  useEffect(() => {
    const handleIntroStart = () => {
      introActiveRef.current = true
      setIntroActive(true)
      idlePrimaryLoopCountRef.current = 0
      void transitionTo(INTRO_CLIP, true)
    }
    const handleIntroCancel = () => {
      introActiveRef.current = false
      setIntroActive(false)
      if (stateRef.current === 'speaking') startTalkingSequence()
      else startIdleSequence()
    }
    window.addEventListener('smartoffice:host-intro-start', handleIntroStart)
    window.addEventListener('smartoffice:host-intro-cancel', handleIntroCancel)
    return () => {
      window.removeEventListener('smartoffice:host-intro-start', handleIntroStart)
      window.removeEventListener('smartoffice:host-intro-cancel', handleIntroCancel)
    }
  }, [startIdleSequence, startTalkingSequence, transitionTo])

  useEffect(() => {
    if (introActiveRef.current) return
    if (state === 'speaking') startTalkingSequence()
    else startIdleSequence()
  }, [state, startIdleSequence, startTalkingSequence])

  function handleEnded(layer: 0 | 1): void {
    if (layer !== activeLayerRef.current) return
    const clip = activeClipRef.current
    if (!clip) return

    if (clip.kind === 'intro') {
      introActiveRef.current = false
      setIntroActive(false)
      window.dispatchEvent(new CustomEvent('smartoffice:host-intro-ended'))
      if (stateRef.current === 'speaking') startTalkingSequence()
      else startIdleSequence()
      return
    }

    if (clip.kind === 'talk') {
      if (stateRef.current !== 'speaking') {
        startIdleSequence()
        return
      }
      talkIndexRef.current = (talkIndexRef.current + 1) % TALK_CLIPS.length
      void transitionTo(TALK_CLIPS[talkIndexRef.current], true)
      return
    }

    if (stateRef.current === 'speaking') {
      startTalkingSequence()
      return
    }

    if (clip.kind === 'idle-rare') {
      idlePrimaryLoopCountRef.current = 0
      void transitionTo(IDLE_PRIMARY, true)
      return
    }

    idlePrimaryLoopCountRef.current += 1
    if (idlePrimaryLoopCountRef.current >= PRIMARY_IDLE_LOOPS_PER_RARE_GESTURE) {
      idlePrimaryLoopCountRef.current = 0
      void transitionTo(IDLE_RARE, true)
    } else {
      void transitionTo(IDLE_PRIMARY, true)
    }
  }

  return (
    <div
      className={`virtual-host-avatar video-host-avatar avatar-${state} ${introActive ? 'avatar-introducing' : ''}`}
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
            onError={() => setAssetError(true)}
          >
            {layerClips[layer] ? <source src={layerClips[layer]?.src} type="video/mp4" /> : null}
          </video>
        ))}

        <div className="video-avatar-vignette" aria-hidden="true" />
        <div className="video-avatar-topline">
          <span className="video-avatar-live-dot" />
          <span>{introActive ? 'Welcoming visitor' : stateLabel(state)}</span>
        </div>

        {assetError ? (
          <div className="video-avatar-fallback" role="status">
            <strong>Virtual host video assets are not installed</strong>
            <span>Copy the prepared MP4 files to public/virtual-host-video.</span>
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
