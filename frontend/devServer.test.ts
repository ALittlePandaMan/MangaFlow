// @vitest-environment node
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {createServer as httpServer} from 'node:http'
import {createConnection} from 'node:net'
import {createHash} from 'node:crypto'
import {createServer as viteServer} from 'vite'
import {developmentPorts, developmentServer} from './devServer.mjs'

let directory: string
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'mangaflow-dev-ports-'))
  for (const name of ['MANGAFLOW_API_PORT', 'MANGAFLOW_DEV_PORT']) vi.stubEnv(name, undefined)
})
afterEach(() => {vi.unstubAllEnvs(); rmSync(directory, {recursive: true, force: true})})

describe('development port configuration', () => {
  it('uses compatible defaults and shares dotenv precedence with Vite', () => {
    expect(developmentPorts('development', directory)).toEqual({api:8000, frontend:5173})
    writeFileSync(join(directory, '.env'), 'MANGAFLOW_API_PORT=18000\nMANGAFLOW_DEV_PORT=15173\nUNRELATED=$(touch should-not-execute)\n')
    writeFileSync(join(directory, '.env.development.local'), 'MANGAFLOW_DEV_PORT=25173\n')
    expect(developmentPorts('development', directory)).toEqual({api:18000, frontend:25173})
    vi.stubEnv('MANGAFLOW_API_PORT', '28000')
    expect(developmentPorts('development', directory)).toEqual({api:28000, frontend:25173})
    expect(developmentServer('development', directory).proxy['/mangaflow'].target).toBe('http://127.0.0.1:28000')
  })
  it.each(['', '0', '65536', '-1', '18.5', 'not-a-port', '18000; echo secret'])('rejects invalid ports without echoing their contents: %s', value => {
    vi.stubEnv('MANGAFLOW_API_PORT', value)
    expect(() => developmentPorts('development', directory)).toThrow(/^MANGAFLOW_API_PORT must be a port between 1 and 65535$/)
  })
  it('rejects a shared frontend/backend port', () => {
    vi.stubEnv('MANGAFLOW_API_PORT', '18000')
    vi.stubEnv('MANGAFLOW_DEV_PORT', '18000')
    expect(() => developmentPorts('development', directory)).toThrow(/must be different/)
  })
})

async function listen(server: ReturnType<typeof httpServer>) {
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  return (server.address() as {port:number}).port
}
async function close(server: ReturnType<typeof httpServer>) {
  server.closeAllConnections()
  await new Promise<void>(resolve => server.close(() => resolve()))
}

it('proxies API, media, MCP and WebSocket using custom ports and the original Host', async () => {
  const backend = httpServer((request, response) => {
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({path:request.url, host:request.headers.host}))
  })
  let websocketHost: string | undefined
  backend.on('upgrade', (request, socket) => {
    websocketHost = request.headers.host
    const accept = createHash('sha1').update(request.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
    socket.end(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`)
  })
  const backendPort = await listen(backend)
  const reservation = httpServer()
  const frontendPort = await listen(reservation)
  await close(reservation)
  vi.stubEnv('MANGAFLOW_API_PORT', String(backendPort))
  vi.stubEnv('MANGAFLOW_DEV_PORT', String(frontendPort))
  const vite = await viteServer({configFile:false, root:directory, server:{...developmentServer('development', directory), host:'127.0.0.1'}})
  try {
    await vite.listen()
    for (const path of ['/api/projects', '/media/sample.png', '/mcp', '/mangaflow/setup.json']) {
      const response = await fetch(`http://127.0.0.1:${frontendPort}${path}`)
      expect(await response.json()).toEqual({path, host:`127.0.0.1:${frontendPort}`})
    }
    const handshake = await new Promise<string>((resolve, reject) => {
      const socket = createConnection({host:'127.0.0.1', port:frontendPort}, () => {
        socket.write(`GET /api/ws/tasks/example HTTP/1.1\r\nHost: 127.0.0.1:${frontendPort}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n`)
      })
      socket.setTimeout(5000, () => {socket.destroy(); reject(new Error('WebSocket proxy timed out'))})
      socket.once('data', data => {socket.destroy(); resolve(data.toString())})
      socket.once('error', reject)
    })
    expect(handshake).toContain('101 Switching Protocols')
    expect(websocketHost).toBe(`127.0.0.1:${frontendPort}`)
  } finally {await vite.close(); await close(backend)}
})

it('fails on an occupied frontend port instead of choosing another one', async () => {
  const occupied = httpServer()
  const port = await listen(occupied)
  vi.stubEnv('MANGAFLOW_DEV_PORT', String(port))
  const vite = await viteServer({configFile:false, root:directory, server:{...developmentServer('development', directory), host:'127.0.0.1'}})
  try {
    const error = await vite.listen().then(() => '', error => error.message)
    expect(error).toMatch(/already in use/)
  }
  finally {await vite.close(); await close(occupied)}
})
