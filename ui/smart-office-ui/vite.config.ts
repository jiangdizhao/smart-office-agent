import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

function proximityTerminalLogger(): Plugin {
  return {
    name: 'smart-office-proximity-terminal-logger',
    apply: 'serve',
    configureServer(server) {
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

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), proximityTerminalLogger()],
})
