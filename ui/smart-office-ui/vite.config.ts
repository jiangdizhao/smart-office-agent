import { appendFile, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const MEDIAPIPE_MODULE_URL =
  'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/+esm'
const MEDIAPIPE_WASM_ROOT =
  'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm'
const MEDIAPIPE_FACE_MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite'
const MEDIAPIPE_OBJECT_MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite'
const PROXIMITY_LOG_DIRECTORY = resolve(process.cwd(), 'logs')
const PROXIMITY_TIMELINE_PATH = resolve(
  PROXIMITY_LOG_DIRECTORY,
  'proximity_voice_timeline.jsonl',
)

async function proxyAsset(
  url: string,
  res: import('node:http').ServerResponse,
): Promise<void> {
  try {
    console.log(`[ProximityDebug] proxy-fetch-start ${url}`)
    const upstream = await fetch(url)
    if (!upstream.ok) throw new Error(`HTTP ${upstream.status} ${upstream.statusText}`)
    const body = Buffer.from(await upstream.arrayBuffer())
    res.statusCode = 200
    res.setHeader(
      'Content-Type',
      upstream.headers.get('content-type') || 'application/octet-stream',
    )
    res.setHeader('Cache-Control', 'public, max-age=3600')
    res.end(body)
    console.log(`[ProximityDebug] proxy-fetch-ok bytes=${body.length} ${url}`)
  } catch (error) {
    console.error(`[ProximityDebug] proxy-fetch-error ${url}`, error)
    res.statusCode = 502
    res.setHeader('Content-Type', 'text/plain; charset=utf-8')
    res.end(error instanceof Error ? error.message : String(error))
  }
}

function proximityTerminalLogger(): Plugin {
  return {
    name: 'smart-office-proximity-terminal-logger',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__mediapipe/tasks-vision.js', (_req, res) => {
        void proxyAsset(MEDIAPIPE_MODULE_URL, res)
      })
      server.middlewares.use('/__mediapipe/wasm', (req, res) => {
        const filename = (req.url || '/').replace(/^\/+/, '')
        void proxyAsset(`${MEDIAPIPE_WASM_ROOT}/${filename}`, res)
      })
      server.middlewares.use('/__mediapipe/models/face.tflite', (_req, res) => {
        void proxyAsset(MEDIAPIPE_FACE_MODEL_URL, res)
      })
      server.middlewares.use('/__mediapipe/models/object.tflite', (_req, res) => {
        void proxyAsset(MEDIAPIPE_OBJECT_MODEL_URL, res)
      })

      server.middlewares.use('/__proximity_debug', (req, res, next) => {
        if (req.method !== 'POST') {
          next()
          return
        }
        let body = ''
        req.setEncoding('utf8')
        req.on('data', (chunk: string) => {
          body += chunk
          if (body.length > 32_000) body = body.slice(0, 32_000)
        })
        req.on('end', () => {
          try {
            const payload = JSON.parse(body) as {
              event?: string
              data?: Record<string, unknown>
              time?: string
            }
            const event = payload.event || 'unknown'
            const time = payload.time || new Date().toISOString()
            const details = payload.data ? ` ${JSON.stringify(payload.data)}` : ''
            console.log(`[ProximityDebug][${time}] ${event}${details}`)

            const timelineRecord = {
              received_at: new Date().toISOString(),
              browser_time: time,
              event,
              data: payload.data ?? {},
            }
            void mkdir(PROXIMITY_LOG_DIRECTORY, { recursive: true })
              .then(() =>
                appendFile(
                  PROXIMITY_TIMELINE_PATH,
                  `${JSON.stringify(timelineRecord)}\n`,
                  'utf8',
                ),
              )
              .catch((error) => {
                console.error(
                  `[ProximityDebug] could not append timeline file ${PROXIMITY_TIMELINE_PATH}`,
                  error,
                )
              })
          } catch (error) {
            console.error('[ProximityDebug] malformed browser diagnostic', error, body)
          }
          res.statusCode = 204
          res.end()
        })
        req.on('error', (error) => {
          console.error('[ProximityDebug] terminal log request failed', error)
          if (!res.headersSent) res.statusCode = 500
          res.end()
        })
      })
    },
  }
}

export default defineConfig({
  envPrefix: ['VITE_', 'enable_'],
  define: {
    'import.meta.env.VITE_MEDIAPIPE_VISION_MODULE_URL': JSON.stringify(
      '/__mediapipe/tasks-vision.js',
    ),
    'import.meta.env.VITE_MEDIAPIPE_VISION_WASM_ROOT': JSON.stringify('/__mediapipe/wasm'),
    'import.meta.env.VITE_MEDIAPIPE_FACE_MODEL_URL': JSON.stringify(
      '/__mediapipe/models/face.tflite',
    ),
    'import.meta.env.VITE_MEDIAPIPE_OBJECT_MODEL_URL': JSON.stringify(
      '/__mediapipe/models/object.tflite',
    ),
  },
  plugins: [react(), proximityTerminalLogger()],
})
