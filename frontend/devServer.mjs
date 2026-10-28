import {loadEnv} from 'vite'
import {fileURLToPath, pathToFileURL} from 'node:url'

const repository = fileURLToPath(new URL('../', import.meta.url))

export function developmentPorts(mode = 'development', directory = repository) {
  const environment = loadEnv(mode, directory, 'MANGAFLOW_')
  const port = (name, fallback) => {
    const value = environment[name] ?? String(fallback)
    if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
      throw new Error(`${name} must be a port between 1 and 65535`)
    }
    return Number(value)
  }
  const api = port('MANGAFLOW_API_PORT', 8000)
  const frontend = port('MANGAFLOW_DEV_PORT', 5173)
  if (api === frontend) throw new Error('MANGAFLOW_API_PORT and MANGAFLOW_DEV_PORT must be different')
  return {api, frontend}
}

export function developmentServer(mode, directory) {
  const ports = developmentPorts(mode, directory)
  // Explicit IPv4 avoids localhost's IPv6 fallback when Docker binds IPv4 only.
  const target = `http://127.0.0.1:${ports.api}`
  return {port: ports.frontend, strictPort: true, proxy: {
    '/api/ws': {target, ws: true, changeOrigin: false},
    ...Object.fromEntries(['/api', '/media', '/mcp', '/mangaflow'].map(path => [path, {target, changeOrigin: false}])),
  }}
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  try {
    const ports = developmentPorts()
    process.stdout.write(`${ports.api} ${ports.frontend}\n`)
  } catch (error) {
    process.stderr.write(`MangaFlow development setup failed: ${error.message}\n`)
    process.exitCode = 1
  }
}
