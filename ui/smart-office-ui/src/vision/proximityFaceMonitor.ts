export type ProximityDetection = {
  body_area_ratio: number
  body_confidence: number
  face_area_ratio: number
  face_confidence: number
  face_inside_body: boolean
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

type Box = { x: number; y: number; width: number; height: number }
type Observation = Box & { confidence: number; category: string }
type MediaPipeDetection = {
  boundingBox?: { originX?: number; originY?: number; width?: number; height?: number }
  categories?: Array<{ score?: number; categoryName?: string; displayName?: string }>
}
type MediaPipeDetector = {
  detectForVideo: (
    video: HTMLVideoElement,
    timestamp: number,
  ) => { detections?: MediaPipeDetection[] }
  close?: () => void
}
type MediaPipeVisionModule = {
  FilesetResolver: { forVisionTasks: (root: string) => Promise<unknown> }
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
const DEFAULT_REARM_UNQUALIFIED_SECONDS = 5
const DETECTION_INTERVAL_MS = 220
const REQUIRED_STABLE_FRAMES = 4
const ATTEMPT_COOLDOWN_MS = 2_000
const DEBUG_LOG_INTERVAL_MS = 1_000

function numericEnv(name: string, fallback: number): number {
  let raw: unknown
  switch (name) {
    case 'VITE_PROXIMITY_BODY_AREA_RATIO':
      raw = import.meta.env.VITE_PROXIMITY_BODY_AREA_RATIO
      break
    case 'VITE_PROXIMITY_MIN_BODY_CONFIDENCE':
      raw = import.meta.env.VITE_PROXIMITY_MIN_BODY_CONFIDENCE
      break
    case 'VITE_PROXIMITY_MIN_FACE_CONFIDENCE':
      raw = import.meta.env.VITE_PROXIMITY_MIN_FACE_CONFIDENCE
      break
    case 'VITE_PROXIMITY_REARM_ABSENCE_SECONDS':
      raw = import.meta.env.VITE_PROXIMITY_REARM_ABSENCE_SECONDS
      break
    default:
      raw = undefined
  }
  const value = Number(raw)
  return Number.isFinite(value) ? value : fallback
}

function rearmUnqualifiedMs(): number {
  return (
    Math.max(
      0,
      numericEnv(
        'VITE_PROXIMITY_REARM_ABSENCE_SECONDS',
        DEFAULT_REARM_UNQUALIFIED_SECONDS,
      ),
    ) * 1_000
  )
}

function debugEnabled(): boolean {
  const configured = String(import.meta.env.VITE_PROXIMITY_DEBUG ?? '').trim().toLowerCase()
  if (configured === 'false' || configured === '0' || configured === 'off') return false
  if (configured === 'true' || configured === '1' || configured === 'on') return true
  return import.meta.env.DEV
}

function clamp(value: number): number {
  return Math.max(0, Math.min(1, value))
}

function area(box: Box): number {
  return Math.max(0, box.width) * Math.max(0, box.height)
}

function toObservation(detection: MediaPipeDetection, fallback: string): Observation {
  const box = detection.boundingBox ?? {}
  const category = detection.categories?.[0]
  return {
    x: box.originX ?? 0,
    y: box.originY ?? 0,
    width: box.width ?? 0,
    height: box.height ?? 0,
    confidence: clamp(category?.score ?? 0),
    category: category?.categoryName ?? category?.displayName ?? fallback,
  }
}

function isPerson(value: string): boolean {
  const normalized = value.trim().toLocaleLowerCase()
  return normalized === 'person' || normalized === 'people'
}

function faceInsidePerson(face: Box, person: Box): boolean {
  const centerX = face.x + face.width / 2
  const centerY = face.y + face.height / 2
  if (
    centerX < person.x ||
    centerX > person.x + person.width ||
    centerY < person.y ||
    centerY > person.y + person.height
  ) {
    return false
  }
  const left = Math.max(face.x, person.x)
  const top = Math.max(face.y, person.y)
  const right = Math.min(face.x + face.width, person.x + person.width)
  const bottom = Math.min(face.y + face.height, person.y + person.height)
  const overlap = Math.max(0, right - left) * Math.max(0, bottom - top)
  return overlap / Math.max(1, area(face)) >= 0.7
}

function bestContainedFace(person: Observation, faces: Observation[]): Observation | null {
  return (
    faces
      .filter((face) => face.width > 0 && face.height > 0 && faceInsidePerson(face, person))
      .sort((left, right) => right.confidence - left.confidence || area(right) - area(left))[0] ??
    null
  )
}

async function createMediaPipeAdapter(
  debug: (event: string, data?: Record<string, unknown>) => void,
): Promise<DetectorAdapter> {
  const moduleUrl =
    import.meta.env.VITE_MEDIAPIPE_VISION_MODULE_URL ?? '/__mediapipe/tasks-vision.js'
  const wasmRoot = import.meta.env.VITE_MEDIAPIPE_VISION_WASM_ROOT ?? '/__mediapipe/wasm'
  const faceModelUrl =
    import.meta.env.VITE_MEDIAPIPE_FACE_MODEL_URL ?? '/__mediapipe/models/face.tflite'
  const objectModelUrl =
    import.meta.env.VITE_MEDIAPIPE_OBJECT_MODEL_URL ?? '/__mediapipe/models/object.tflite'

  debug('model-load-start', { moduleUrl, wasmRoot, faceModelUrl, objectModelUrl })
  const vision = (await import(/* @vite-ignore */ moduleUrl)) as MediaPipeVisionModule
  debug('vision-module-loaded')
  const fileset = await vision.FilesetResolver.forVisionTasks(wasmRoot)
  debug('wasm-fileset-loaded')

  const faceThreshold = numericEnv(
    'VITE_PROXIMITY_MIN_FACE_CONFIDENCE',
    DEFAULT_FACE_CONFIDENCE,
  )
  const bodyThreshold = numericEnv(
    'VITE_PROXIMITY_MIN_BODY_CONFIDENCE',
    DEFAULT_BODY_CONFIDENCE,
  )
  const [faceDetector, objectDetector] = await Promise.all([
    vision.FaceDetector.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: faceModelUrl },
      runningMode: 'VIDEO',
      minDetectionConfidence: faceThreshold,
      minSuppressionThreshold: 0.3,
    }),
    vision.ObjectDetector.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: objectModelUrl },
      runningMode: 'VIDEO',
      scoreThreshold: bodyThreshold,
      categoryAllowlist: ['person'],
      maxResults: 4,
    }),
  ])
  debug('detectors-ready', {
    bodyThreshold,
    faceThreshold,
    rearmUnqualifiedMs: rearmUnqualifiedMs(),
  })

  return {
    async detect(video, timestamp) {
      const objects = objectDetector.detectForVideo(video, timestamp)
      const faces = faceDetector.detectForVideo(video, timestamp + 0.001)
      return {
        people: (objects.detections ?? [])
          .map((item) => toObservation(item, 'person'))
          .filter((item) => isPerson(item.category)),
        faces: (faces.detections ?? []).map((item) => toObservation(item, 'face')),
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
  private unqualifiedSince: number | null = null
  private lastAttemptAt = 0
  private lastDebugAt = 0
  private readonly debugMode = debugEnabled()
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

  private debug(event: string, data?: Record<string, unknown>, force = false): void {
    if (!this.debugMode) return
    const now = performance.now()
    if (!force && now - this.lastDebugAt < DEBUG_LOG_INTERVAL_MS) return
    this.lastDebugAt = now
    if (data) console.info(`[ProximityDebug] ${event}`, data)
    else console.info(`[ProximityDebug] ${event}`)
  }

  async start(): Promise<void> {
    if (this.running) return
    this.running = true
    this.onStatus('starting', 'camera-request')
    this.debug('monitor-start', { debugMode: this.debugMode }, true)
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        this.onStatus('unsupported', 'getUserMedia is unavailable')
        this.debug('unsupported-getUserMedia', undefined, true)
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
      const track = this.stream.getVideoTracks()[0]
      this.debug(
        'camera-opened',
        {
          label: track?.label ?? '',
          settings: track?.getSettings?.() ?? {},
          readyState: track?.readyState ?? 'unknown',
        },
        true,
      )
      this.onStatus('starting', 'camera-opened; loading MediaPipe models')

      this.video = document.createElement('video')
      this.video.muted = true
      this.video.playsInline = true
      this.video.autoplay = true
      this.video.srcObject = this.stream
      await this.video.play()
      this.debug(
        'video-playing',
        { width: this.video.videoWidth, height: this.video.videoHeight, readyState: this.video.readyState },
        true,
      )

      this.adapter = await createMediaPipeAdapter((event, data) => this.debug(event, data, true))
      this.onStatus('watching', 'detectors-ready; waiting for frames')
      this.debug('monitor-ready', undefined, true)
      this.schedule(0)
    } catch (error) {
      const name = error instanceof DOMException ? error.name : ''
      const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error)
      console.error('[ProximityDebug] startup-error', error)
      this.onStatus(
        name === 'NotAllowedError' || name === 'SecurityError' ? 'blocked' : 'error',
        message,
      )
      this.stop(false)
    }
  }

  stop(emitStatus = true): void {
    this.debug('monitor-stop', { emitStatus }, true)
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
    this.unqualifiedSince = null
    this.debug(
      'suppressed-until-unqualified',
      { requiredUnqualifiedMs: rearmUnqualifiedMs() },
      true,
    )
  }

  private schedule(delay = DETECTION_INTERVAL_MS): void {
    if (this.running) this.timer = window.setTimeout(() => void this.tick(), delay)
  }

  private async tick(): Promise<void> {
    if (!this.running || !this.video || !this.adapter) return
    try {
      if (this.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
        this.debug('waiting-for-video-frame', { readyState: this.video.readyState })
        this.onStatus('watching', `waiting-frame readyState=${this.video.readyState}`)
        return
      }
      const frameWidth = Math.max(1, this.video.videoWidth)
      const frameHeight = Math.max(1, this.video.videoHeight)
      const observations = await this.adapter.detect(this.video, performance.now())
      const person = observations.people
        .filter((item) => item.width > 0 && item.height > 0)
        .sort((left, right) => area(right) - area(left))[0]

      if (!person) {
        this.handleUnqualified('no-person')
        this.onObservation?.(null)
        this.onStatus(
          'watching',
          `body=0 face=${observations.faces.length} eligible=${this.eligible()} armed=${this.armed}`,
        )
        this.debug('frame-no-person', {
          peopleCount: observations.people.length,
          faceCount: observations.faces.length,
          eligible: this.eligible(),
          armed: this.armed,
        })
        return
      }

      const face = bestContainedFace(person, observations.faces)
      const bodyAreaRatio = clamp(area(person) / (frameWidth * frameHeight))
      const faceAreaRatio = face ? clamp(area(face) / (frameWidth * frameHeight)) : 0
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
      const bodyAreaOk = bodyAreaRatio >= bodyAreaThreshold
      const bodyConfidenceOk = person.confidence >= bodyConfidenceThreshold
      const faceInside = face !== null
      const faceConfidenceOk = face !== null && face.confidence >= faceConfidenceThreshold
      const eligible = this.eligible()
      const qualified = bodyAreaOk && bodyConfidenceOk && faceInside && faceConfidenceOk

      const detection: ProximityDetection = {
        body_area_ratio: bodyAreaRatio,
        body_confidence: person.confidence,
        face_area_ratio: faceAreaRatio,
        face_confidence: face?.confidence ?? 0,
        face_inside_body: faceInside,
        confidence: person.confidence,
        frontal_score: faceInside ? 1 : 0,
        center_x: clamp((person.x + person.width / 2) / frameWidth),
        center_y: clamp((person.y + person.height / 2) / frameHeight),
        stable_frames: this.stableFrames,
        detector: 'mediapipe-person+face',
      }
      this.onObservation?.(detection)

      const reason = !bodyAreaOk
        ? 'body-area-low'
        : !bodyConfidenceOk
          ? 'body-confidence-low'
          : !faceInside
            ? 'face-not-inside-body'
            : !faceConfidenceOk
              ? 'face-confidence-low'
              : !eligible
                ? 'frontend-not-eligible'
                : !this.armed
                  ? 'detector-not-armed'
                  : 'qualified'
      const detail =
        `body=${(bodyAreaRatio * 100).toFixed(1)}%/${(bodyAreaThreshold * 100).toFixed(0)}% ` +
        `bodyConf=${person.confidence.toFixed(2)}/${bodyConfidenceThreshold.toFixed(2)} ` +
        `faces=${observations.faces.length} inside=${faceInside} ` +
        `faceConf=${(face?.confidence ?? 0).toFixed(2)}/${faceConfidenceThreshold.toFixed(2)} ` +
        `stable=${this.stableFrames}/${REQUIRED_STABLE_FRAMES} eligible=${eligible} armed=${this.armed} reason=${reason}`
      this.onStatus('watching', detail)
      this.debug('frame-diagnostic', {
        frame: `${frameWidth}x${frameHeight}`,
        peopleCount: observations.people.length,
        faceCount: observations.faces.length,
        bodyAreaRatio,
        bodyAreaThreshold,
        bodyConfidence: person.confidence,
        bodyConfidenceThreshold,
        faceInside,
        faceConfidence: face?.confidence ?? 0,
        faceConfidenceThreshold,
        stableFrames: this.stableFrames,
        requiredStableFrames: REQUIRED_STABLE_FRAMES,
        eligible,
        armed: this.armed,
        qualified,
        reason,
      })

      if (!qualified) {
        this.handleUnqualified(reason)
        return
      }

      this.unqualifiedSince = null
      if (!eligible) {
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
        this.debug('greeting-request-start', { detection }, true)
        const triggered = await this.onQualifiedFace(detection)
        this.debug('greeting-request-result', { triggered }, true)
        this.stableFrames = 0
        if (triggered) {
          this.armed = false
          this.unqualifiedSince = null
          this.onStatus(
            'watching',
            'greeting-triggered; waiting for continuously unqualified frames before rearming',
          )
        } else {
          this.onStatus('watching', 'visual gate passed, but greeting callback/backend returned false')
        }
      }
    } catch (error) {
      const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error)
      console.error('[ProximityDebug] frame-error', error)
      this.onStatus('error', message)
    } finally {
      this.schedule()
    }
  }

  private handleUnqualified(reason: string): void {
    this.stableFrames = 0
    if (this.armed) {
      this.unqualifiedSince = null
      return
    }

    const now = performance.now()
    if (this.unqualifiedSince === null) this.unqualifiedSince = now

    const requiredUnqualifiedMs = rearmUnqualifiedMs()
    const unqualifiedForMs = now - this.unqualifiedSince
    const remainingMs = Math.max(0, requiredUnqualifiedMs - unqualifiedForMs)
    this.debug('rearm-waiting-unqualified', {
      reason,
      unqualifiedForMs: Math.round(unqualifiedForMs),
      requiredUnqualifiedMs: Math.round(requiredUnqualifiedMs),
      remainingMs: Math.round(remainingMs),
    })

    if (unqualifiedForMs >= requiredUnqualifiedMs) {
      this.armed = true
      this.unqualifiedSince = null
      this.debug(
        'detector-rearmed',
        {
          reason,
          unqualifiedForMs: Math.round(unqualifiedForMs),
          requiredUnqualifiedMs: Math.round(requiredUnqualifiedMs),
        },
        true,
      )
    }
  }
}
