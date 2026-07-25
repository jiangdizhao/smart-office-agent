export type ProximityDetection = {
  body_area_ratio: number
  body_confidence: number
  face_area_ratio: number
  face_confidence: number
  face_inside_body: boolean
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

type BoundingBox = {
  x: number
  y: number
  width: number
  height: number
}

type Observation = BoundingBox & {
  confidence: number
  category: string
}

type MediaPipeDetection = {
  boundingBox?: {
    originX?: number
    originY?: number
    width?: number
    height?: number
  }
  categories?: Array<{
    score?: number
    categoryName?: string
    displayName?: string
  }>
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
  ObjectDetector: {
    createFromOptions: (
      fileset: unknown,
      options: Record<string, unknown>,
    ) => Promise<MediaPipeDetector>
  }
}

type DetectorAdapter = {
  detect: (
    video: HTMLVideoElement,
    timestamp: number,
  ) => Promise<{ people: Observation[]; faces: Observation[] }>
  close: () => void
}

const DEFAULT_BODY_AREA_RATIO = 0.16
const DEFAULT_BODY_CONFIDENCE = 0.55
const DEFAULT_FACE_CONFIDENCE = 0.6
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

function toObservation(detection: MediaPipeDetection, fallbackCategory: string): Observation {
  const box = detection.boundingBox ?? {}
  const category = detection.categories?.[0]
  return {
    x: box.originX ?? 0,
    y: box.originY ?? 0,
    width: box.width ?? 0,
    height: box.height ?? 0,
    confidence: clamp(category?.score ?? 0),
    category: category?.categoryName ?? category?.displayName ?? fallbackCategory,
  }
}

function isPersonCategory(value: string): boolean {
  const normalized = value.trim().toLocaleLowerCase()
  return normalized === 'person' || normalized === 'people'
}

function boxArea(box: BoundingBox): number {
  return Math.max(0, box.width) * Math.max(0, box.height)
}

function pointInsideBox(x: number, y: number, box: BoundingBox): boolean {
  return x >= box.x && x <= box.x + box.width && y >= box.y && y <= box.y + box.height
}

function faceInsidePerson(face: BoundingBox, person: BoundingBox): boolean {
  const faceCenterX = face.x + face.width / 2
  const faceCenterY = face.y + face.height / 2
  if (!pointInsideBox(faceCenterX, faceCenterY, person)) return false

  const intersectionLeft = Math.max(face.x, person.x)
  const intersectionTop = Math.max(face.y, person.y)
  const intersectionRight = Math.min(face.x + face.width, person.x + person.width)
  const intersectionBottom = Math.min(face.y + face.height, person.y + person.height)
  const intersectionArea =
    Math.max(0, intersectionRight - intersectionLeft) *
    Math.max(0, intersectionBottom - intersectionTop)
  return intersectionArea / Math.max(1, boxArea(face)) >= 0.7
}

function containedFace(person: Observation, faces: Observation[]): Observation | null {
  return (
    faces
      .filter((face) => face.width > 0 && face.height > 0 && faceInsidePerson(face, person))
      .sort((left, right) => right.confidence - left.confidence || boxArea(right) - boxArea(left))[0] ??
    null
  )
}

async function createMediaPipeAdapter(): Promise<DetectorAdapter> {
  const moduleUrl =
    import.meta.env.VITE_MEDIAPIPE_VISION_MODULE_URL ??
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22/+esm'
  const wasmRoot =
    import.meta.env.VITE_MEDIAPIPE_VISION_WASM_ROOT ??
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22/wasm'
  const faceModelUrl =
    import.meta.env.VITE_MEDIAPIPE_FACE_MODEL_URL ??
    'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite'
  const objectModelUrl =
    import.meta.env.VITE_MEDIAPIPE_OBJECT_MODEL_URL ??
    'https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite'

  const vision = (await import(/* @vite-ignore */ moduleUrl)) as MediaPipeVisionModule
  const fileset = await vision.FilesetResolver.forVisionTasks(wasmRoot)
  const [faceDetector, objectDetector] = await Promise.all([
    vision.FaceDetector.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: faceModelUrl },
      runningMode: 'VIDEO',
      minDetectionConfidence: numericEnv(
        'VITE_PROXIMITY_MIN_FACE_CONFIDENCE',
        DEFAULT_FACE_CONFIDENCE,
      ),
      minSuppressionThreshold: 0.3,
    }),
    vision.ObjectDetector.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: objectModelUrl },
      runningMode: 'VIDEO',
      scoreThreshold: numericEnv(
        'VITE_PROXIMITY_MIN_BODY_CONFIDENCE',
        DEFAULT_BODY_CONFIDENCE,
      ),
      categoryAllowlist: ['person'],
      maxResults: 4,
    }),
  ])

  return {
    async detect(video, timestamp) {
      const objectResult = objectDetector.detectForVideo(video, timestamp)
      const faceResult = faceDetector.detectForVideo(video, timestamp + 0.001)
      return {
        people: (objectResult.detections ?? [])
          .map((item) => toObservation(item, 'person'))
          .filter((item) => isPersonCategory(item.category)),
        faces: (faceResult.detections ?? []).map((item) => toObservation(item, 'face')),
      }
    },
    close() {
      faceDetector.close?.()
      objectDetector.close?.()
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
  private readonly eligible: () => boolean
  private readonly onQualifiedFace: (detection: ProximityDetection) => Promise<boolean>
  private readonly onStatus: (status: ProximityDetectorStatus, detail?: string) => void
  private readonly onObservation?: (detection: ProximityDetection | null) => void

  constructor(
    eligible: () => boolean,
    onQualifiedFace: (detection: ProximityDetection) => Promise<boolean>,
    onStatus: (status: ProximityDetectorStatus, detail?: string) => void,
    onObservation?: (detection: ProximityDetection | null) => void,
  ) {
    this.eligible = eligible
    this.onQualifiedFace = onQualifiedFace
    this.onStatus = onStatus
    this.onObservation = onObservation
  }

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

      this.adapter = await createMediaPipeAdapter()
      this.onStatus('watching', 'person-and-face')
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
      if (this.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return

      const frameWidth = Math.max(1, this.video.videoWidth)
      const frameHeight = Math.max(1, this.video.videoHeight)
      const { people, faces } = await this.adapter.detect(this.video, performance.now())
      const person = people
        .filter((item) => item.width > 0 && item.height > 0)
        .sort((left, right) => boxArea(right) - boxArea(left))[0]

      if (!person) {
        this.handleUnqualified()
        this.onObservation?.(null)
        return
      }

      const face = containedFace(person, faces)
      const bodyAreaRatio = clamp(boxArea(person) / (frameWidth * frameHeight))
      const faceAreaRatio = face
        ? clamp(boxArea(face) / (frameWidth * frameHeight))
        : 0
      const centerX = clamp((person.x + person.width / 2) / frameWidth)
      const centerY = clamp((person.y + person.height / 2) / frameHeight)
      const detection: ProximityDetection = {
        body_area_ratio: bodyAreaRatio,
        body_confidence: person.confidence,
        face_area_ratio: faceAreaRatio,
        face_confidence: face?.confidence ?? 0,
        face_inside_body: face !== null,
        center_x: centerX,
        center_y: centerY,
        stable_frames: this.stableFrames,
        detector: 'mediapipe-person+face',
      }
      this.onObservation?.(detection)

      const bodyAreaThreshold = numericEnv(
        'VITE_PROXIMITY_BODY_AREA_RATIO',
        DEFAULT_BODY_AREA_RATIO,
      )
      const bodyConfidenceThreshold = numericEnv(
        'VITE_PROXIMITY_MIN_BODY_CONFIDENCE',
        DEFAULT_BODY_CONFIDENCE,
      )
      const faceConfidenceThreshold = numericEnv(
        'VITE_PROXIMITY_MIN_FACE_CONFIDENCE',
        DEFAULT_FACE_CONFIDENCE,
      )
      const qualified =
        bodyAreaRatio >= bodyAreaThreshold &&
        person.confidence >= bodyConfidenceThreshold &&
        face !== null &&
        face.confidence >= faceConfidenceThreshold

      if (!qualified) {
        this.handleUnqualified()
        return
      }

      this.unqualifiedSince = performance.now()
      if (!this.eligible()) {
        this.stableFrames = 0
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
