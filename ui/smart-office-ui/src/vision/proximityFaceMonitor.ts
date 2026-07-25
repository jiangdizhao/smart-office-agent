export type ProximityDetection = {
  face_area_ratio: number
  confidence: number
  frontal_score: number
  center_x: number
  center_y: number
  stable_frames: number
  detector: string
}

export type ProximityDetectorStatus =
  | 'starting'
  | 'watching'
  | 'blocked'
  | 'unsupported'
  | 'error'
  | 'stopped'

type FacePoint = { x: number; y: number }
type FaceObservation = {
  x: number
  y: number
  width: number
  height: number
  confidence: number
  keypoints: FacePoint[]
  detector: string
}

type DetectorAdapter = {
  detect: (video: HTMLVideoElement, timestamp: number) => Promise<FaceObservation[]>
  close: () => void
}

type NativeDetectedFace = {
  boundingBox: DOMRectReadOnly
  landmarks?: Array<{ locations?: DOMPointReadOnly[] }>
}

type NativeFaceDetector = {
  detect: (source: CanvasImageSource) => Promise<NativeDetectedFace[]>
}

type NativeFaceDetectorConstructor = new (options?: {
  fastMode?: boolean
  maxDetectedFaces?: number
}) => NativeFaceDetector

type MediaPipeDetection = {
  boundingBox?: {
    originX?: number
    originY?: number
    width?: number
    height?: number
  }
  categories?: Array<{ score?: number }>
  keypoints?: Array<{ x?: number; y?: number }>
}

type MediaPipeDetector = {
  detectForVideo: (
    video: HTMLVideoElement,
    timestamp: number,
  ) => { detections?: MediaPipeDetection[] }
  close?: () => void
}

type MediaPipeVisionModule = {
  FilesetResolver: {
    forVisionTasks: (root: string) => Promise<unknown>
  }
  FaceDetector: {
    createFromOptions: (
      fileset: unknown,
      options: Record<string, unknown>,
    ) => Promise<MediaPipeDetector>
  }
}

const DEFAULT_AREA_RATIO = 0.18
const DEFAULT_MIN_CONFIDENCE = 0.72
const DEFAULT_MIN_FRONTAL_SCORE = 0.62
const DETECTION_INTERVAL_MS = 220
const REQUIRED_STABLE_FRAMES = 4
const REARM_ABSENCE_MS = 5_000
const ATTEMPT_COOLDOWN_MS = 2_000

function numericEnv(name: string, fallback: number): number {
  const value = Number(import.meta.env[name])
  return Number.isFinite(value) ? value : fallback
}

function clamp(value: number): number {
  return Math.max(0, Math.min(1, value))
}

function frontalScore(
  observation: FaceObservation,
  frameWidth: number,
  frameHeight: number,
): number {
  const aspect = observation.width / Math.max(1, observation.height)
  const aspectScore = clamp(1 - Math.abs(aspect - 0.82) / 0.55)
  const centerX = (observation.x + observation.width / 2) / frameWidth
  const centerY = (observation.y + observation.height / 2) / frameHeight
  const centerScore =
    clamp(1 - Math.abs(centerX - 0.5) / 0.42) *
    clamp(1 - Math.abs(centerY - 0.44) / 0.5)

  if (observation.keypoints.length < 3) {
    return clamp(aspectScore * 0.58 + centerScore * 0.42)
  }

  const [eyeA, eyeB, nose] = observation.keypoints
  const eyeDistance = Math.hypot(eyeA.x - eyeB.x, eyeA.y - eyeB.y)
  const eyeLevelScore = clamp(
    1 - Math.abs(eyeA.y - eyeB.y) / Math.max(0.001, eyeDistance * 0.45),
  )
  const eyeMidX = (eyeA.x + eyeB.x) / 2
  const eyeMidY = (eyeA.y + eyeB.y) / 2
  const noseCenteredScore = clamp(
    1 - Math.abs(nose.x - eyeMidX) / Math.max(0.001, eyeDistance * 0.42),
  )
  const noseBelowEyesScore = nose.y > eyeMidY ? 1 : 0.3
  const keypointScore =
    eyeLevelScore * 0.38 + noseCenteredScore * 0.42 + noseBelowEyesScore * 0.2

  return clamp(aspectScore * 0.3 + centerScore * 0.25 + keypointScore * 0.45)
}

function nativeFaceDetector(): NativeFaceDetectorConstructor | null {
  const host = window as unknown as { FaceDetector?: NativeFaceDetectorConstructor }
  return host.FaceDetector ?? null
}

async function createNativeAdapter(): Promise<DetectorAdapter | null> {
  const Constructor = nativeFaceDetector()
  if (!Constructor) return null
  const detector = new Constructor({ fastMode: true, maxDetectedFaces: 1 })
  return {
    async detect(video) {
      const faces = await detector.detect(video)
      return faces.map((face) => {
        const box = face.boundingBox
        const keypoints =
          face.landmarks
            ?.flatMap((landmark) => landmark.locations ?? [])
            .map((point) => ({
              x: point.x / Math.max(1, video.videoWidth),
              y: point.y / Math.max(1, video.videoHeight),
            })) ?? []
        return {
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
          confidence: 1,
          keypoints,
          detector: 'browser-face-detector',
        }
      })
    },
    close() {
      // Native FaceDetector has no explicit close method.
    },
  }
}

async function createMediaPipeAdapter(): Promise<DetectorAdapter> {
  const moduleUrl =
    import.meta.env.VITE_MEDIAPIPE_VISION_MODULE_URL ??
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22/+esm'
  const wasmRoot =
    import.meta.env.VITE_MEDIAPIPE_VISION_WASM_ROOT ??
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22/wasm'
  const modelUrl =
    import.meta.env.VITE_MEDIAPIPE_FACE_MODEL_URL ??
    'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite'

  const vision = (await import(/* @vite-ignore */ moduleUrl)) as MediaPipeVisionModule
  const fileset = await vision.FilesetResolver.forVisionTasks(wasmRoot)
  const detector = await vision.FaceDetector.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: modelUrl },
    runningMode: 'VIDEO',
    minDetectionConfidence: numericEnv(
      'VITE_PROXIMITY_MIN_CONFIDENCE',
      DEFAULT_MIN_CONFIDENCE,
    ),
    minSuppressionThreshold: 0.3,
  })

  return {
    async detect(video, timestamp) {
      const result = detector.detectForVideo(video, timestamp)
      return (result.detections ?? []).map((detection) => {
        const box = detection.boundingBox ?? {}
        return {
          x: box.originX ?? 0,
          y: box.originY ?? 0,
          width: box.width ?? 0,
          height: box.height ?? 0,
          confidence: detection.categories?.[0]?.score ?? 0,
          keypoints: (detection.keypoints ?? []).map((point) => ({
            x: point.x ?? 0,
            y: point.y ?? 0,
          })),
          detector: 'mediapipe-face-detector',
        }
      })
    },
    close() {
      detector.close?.()
    },
  }
}

export class ProximityFaceMonitor {
  private video: HTMLVideoElement | null = null
  private stream: MediaStream | null = null
  private adapter: DetectorAdapter | null = null
  private running = false
  private timer: number | null = null
  private stableFrames = 0
  private armed = true
  private unqualifiedSince = performance.now()
  private lastAttemptAt = 0

  constructor(
    private readonly eligible: () => boolean,
    private readonly onQualifiedFace: (detection: ProximityDetection) => Promise<boolean>,
    private readonly onStatus: (status: ProximityDetectorStatus, detail?: string) => void,
    private readonly onObservation?: (detection: ProximityDetection | null) => void,
  ) {}

  async start(): Promise<void> {
    if (this.running) return
    this.running = true
    this.onStatus('starting')

    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        this.onStatus('unsupported', 'getUserMedia is unavailable')
        this.running = false
        return
      }

      this.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: 'user',
          width: { ideal: 960 },
          height: { ideal: 540 },
          frameRate: { ideal: 15, max: 24 },
        },
        audio: false,
      })
      this.video = document.createElement('video')
      this.video.muted = true
      this.video.playsInline = true
      this.video.autoplay = true
      this.video.srcObject = this.stream
      await this.video.play()

      this.adapter = (await createNativeAdapter()) ?? (await createMediaPipeAdapter())
      this.onStatus('watching')
      this.schedule(0)
    } catch (error) {
      const name = error instanceof DOMException ? error.name : ''
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        this.onStatus('blocked', error instanceof Error ? error.message : String(error))
      } else {
        this.onStatus('error', error instanceof Error ? error.message : String(error))
      }
      this.stop(false)
    }
  }

  stop(emitStatus = true): void {
    this.running = false
    if (this.timer !== null) window.clearTimeout(this.timer)
    this.timer = null
    this.adapter?.close()
    this.adapter = null
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    if (this.video) this.video.srcObject = null
    this.video = null
    if (emitStatus) this.onStatus('stopped')
  }

  suppressUntilAbsent(): void {
    this.armed = false
    this.stableFrames = 0
    this.unqualifiedSince = performance.now()
  }

  private schedule(delay = DETECTION_INTERVAL_MS): void {
    if (!this.running) return
    this.timer = window.setTimeout(() => void this.tick(), delay)
  }

  private async tick(): Promise<void> {
    if (!this.running || !this.video || !this.adapter) return
    try {
      if (this.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
        this.schedule()
        return
      }
      const observations = await this.adapter.detect(this.video, performance.now())
      const observation = observations
        .filter((item) => item.width > 0 && item.height > 0)
        .sort((left, right) => right.width * right.height - left.width * left.height)[0]

      if (!observation) {
        this.handleUnqualified()
        this.onObservation?.(null)
        this.schedule()
        return
      }

      const frameWidth = Math.max(1, this.video.videoWidth)
      const frameHeight = Math.max(1, this.video.videoHeight)
      const areaRatio = clamp(
        (observation.width * observation.height) / (frameWidth * frameHeight),
      )
      const centerX = clamp((observation.x + observation.width / 2) / frameWidth)
      const centerY = clamp((observation.y + observation.height / 2) / frameHeight)
      const score = frontalScore(observation, frameWidth, frameHeight)
      const detection: ProximityDetection = {
        face_area_ratio: areaRatio,
        confidence: clamp(observation.confidence),
        frontal_score: score,
        center_x: centerX,
        center_y: centerY,
        stable_frames: this.stableFrames,
        detector: observation.detector,
      }
      this.onObservation?.(detection)

      const areaThreshold = numericEnv(
        'VITE_PROXIMITY_FACE_AREA_RATIO',
        DEFAULT_AREA_RATIO,
      )
      const confidenceThreshold = numericEnv(
        'VITE_PROXIMITY_MIN_CONFIDENCE',
        DEFAULT_MIN_CONFIDENCE,
      )
      const frontalThreshold = numericEnv(
        'VITE_PROXIMITY_MIN_FRONTAL_SCORE',
        DEFAULT_MIN_FRONTAL_SCORE,
      )
      const centered = centerX >= 0.2 && centerX <= 0.8 && centerY >= 0.12 && centerY <= 0.78
      const qualified =
        areaRatio >= areaThreshold &&
        observation.confidence >= confidenceThreshold &&
        score >= frontalThreshold &&
        centered

      if (!qualified) {
        this.handleUnqualified()
        this.schedule()
        return
      }

      this.unqualifiedSince = performance.now()
      if (!this.eligible()) {
        this.stableFrames = 0
        this.schedule()
        return
      }

      this.stableFrames += 1
      detection.stable_frames = this.stableFrames
      const now = performance.now()
      if (
        this.armed &&
        this.stableFrames >= REQUIRED_STABLE_FRAMES &&
        now - this.lastAttemptAt >= ATTEMPT_COOLDOWN_MS
      ) {
        this.lastAttemptAt = now
        const triggered = await this.onQualifiedFace(detection)
        this.stableFrames = 0
        if (triggered) {
          this.armed = false
          this.unqualifiedSince = now
        }
      }
    } catch (error) {
      this.onStatus('error', error instanceof Error ? error.message : String(error))
    } finally {
      this.schedule()
    }
  }

  private handleUnqualified(): void {
    this.stableFrames = 0
    const now = performance.now()
    if (now - this.unqualifiedSince >= REARM_ABSENCE_MS) {
      this.armed = true
    }
  }
}
