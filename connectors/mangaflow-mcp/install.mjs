#!/usr/bin/env node
// Standalone bootstrap: only Node built-ins are loaded before npm ci.
import {createHash} from 'node:crypto'
import {spawn} from 'node:child_process'
import {mkdir, readFile, writeFile, stat, lstat, open, unlink, realpath} from 'node:fs/promises'
import {homedir} from 'node:os'
import {join, resolve} from 'node:path'
import {pathToFileURL} from 'node:url'
import {gunzipSync} from 'node:zlib'

const FILES = ['index.mjs', 'directory-import.mjs', 'package.json', 'package-lock.json', 'README.md']
const MAX_PACKAGE = 8 * 1024 * 1024

export function setupUrl(value) {
  const url = new URL(value)
  if (url.username || url.password || url.search || url.hash || !['/mangaflow', '/mcp'].includes(url.pathname.replace(/\/$/, ''))) {
    throw new Error('MangaFlow URL must end in /mangaflow or /mcp without credentials, query or fragment')
  }
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) {
    throw new Error('Use HTTPS for a remote MangaFlow service')
  }
  url.pathname = url.pathname.replace(/\/$/, '')
  return url
}

export async function fetchBytes(url, maximum) {
  const response = await fetch(url, {redirect: 'error', signal: AbortSignal.timeout(60000)})
  if (!response.ok) throw new Error(`Download failed (${response.status}): ${url.pathname}`)
  if (Number(response.headers.get('content-length')) > maximum) {
    await response.body?.cancel()
    throw new Error('Installation download exceeds size limit')
  }
  const blocks = []
  let size = 0
  for await (const block of response.body) {
    size += block.length
    if (size > maximum) {
      throw new Error('Installation download exceeds size limit')
    }
    blocks.push(block)
  }
  return Buffer.concat(blocks)
}

export function unpackPackage(compressed, digest) {
  if (!/^[a-f0-9]{64}$/.test(digest) || createHash('sha256').update(compressed).digest('hex') !== digest) {
    throw new Error('Connector package checksum mismatch')
  }
  const tar = gunzipSync(compressed, {maxOutputLength: MAX_PACKAGE})
  const files = new Map()
  for (let position = 0; position + 512 <= tar.length;) {
    const header = tar.subarray(position, position + 512)
    if (header.every(byte => byte === 0)) break
    const name = header.subarray(0, 100).toString().split('\0')[0]
    const sizeText = header.subarray(124, 136).toString().replaceAll('\0', '').trim()
    const filename = name.replace(/^package\//, '')
    const size = /^[0-7]+$/.test(sizeText) ? Number.parseInt(sizeText, 8) : NaN
    if (!name.startsWith('package/') || !FILES.includes(filename) || files.has(filename) ||
        ![0, 48].includes(header[156]) || header.subarray(345, 500).some(byte => byte !== 0) ||
        !Number.isSafeInteger(size) || size < 1 || size > MAX_PACKAGE || position + 512 + size > tar.length) {
      throw new Error('Invalid connector package entry')
    }
    files.set(filename, tar.subarray(position + 512, position + 512 + size))
    position += 512 + Math.ceil(size / 512) * 512
  }
  if (files.size !== FILES.length) throw new Error('Connector package is incomplete')
  const metadata = JSON.parse(files.get('package.json'))
  if (metadata.name !== 'mangaflow-mcp') throw new Error('Unexpected connector package')
  return files
}

async function npmCi(directory) {
  // The Windows command is fixed; user paths are passed through cwd, never shell text.
  const command = process.platform === 'win32' ? (process.env.ComSpec || 'cmd.exe') : 'npm'
  const args = process.platform === 'win32'
    ? ['/d', '/s', '/c', 'npm ci --ignore-scripts --no-audit --no-fund']
    : ['ci', '--ignore-scripts', '--no-audit', '--no-fund']
  await new Promise((accept, reject) => {
    const child = spawn(command, args, {cwd: directory, stdio: ['ignore', 2, 2], windowsHide: true})
    child.once('error', reject)
    child.once('exit', code => code === 0 ? accept() : reject(new Error(`npm ci failed (${code}); check Node/npm and registry access`)))
  })
}

export function serverConfiguration(directory, url) {
  return {type: 'stdio', command: process.execPath, args: [join(directory, 'index.mjs')], env: {MANGAFLOW_MCP_URL: url.href.replace(/\/$/, '')}}
}

async function verifyConnection(directory, server) {
  const library = join(directory, 'node_modules', '@modelcontextprotocol', 'sdk', 'dist', 'esm', 'client')
  const {Client} = await import(pathToFileURL(join(library, 'index.js')).href)
  const {StdioClientTransport} = await import(pathToFileURL(join(library, 'stdio.js')).href)
  const client = new Client({name: 'mangaflow-installer', version: '1.0.0'})
  const env = Object.fromEntries(Object.entries(process.env).filter(([, value]) => typeof value === 'string'))
  const transport = new StdioClientTransport({command: server.command, args: server.args, env: {...env, ...server.env}, stderr: 'pipe'})
  transport.stderr?.resume()
  try {
    await client.connect(transport, {timeout: 20000})
    const listed = await client.listTools(undefined, {timeout: 20000})
    if (!['local_import', 'local_download', 'local_transfer_status', 'local_transfer_cancel'].every(name => listed.tools.some(tool => tool.name === name))) {
      throw new Error('Connector probe did not discover the local file tools')
    }
    const result = await client.callTool({name: 'mcp_status', arguments: {}}, undefined, {timeout: 20000})
    const capabilities = result.structuredContent?.connection_capabilities
    if (result.isError || capabilities?.transport !== 'stdio' || !capabilities.local_import || !capabilities.local_download) {
      throw new Error('Connector probe did not confirm local import and download tools')
    }
    return {transport: 'stdio', local_import: true, local_download: true}
  } finally {
    await client.close()
    await transport.close()
  }
}

async function installationLock(path) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const file = await open(path, 'wx', 0o600)
      await file.writeFile(String(process.pid))
      await file.close()
      return async () => {await unlink(path)}
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
      const pid = Number(await readFile(path, 'utf8'))
      if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error('Installation lock needs inspection: ' + path)
      try {process.kill(pid, 0)} catch (cause) {
        if (cause.code === 'ESRCH') {await unlink(path); continue}
      }
      throw new Error('Another connector installation is running; wait before retrying')
    }
  }
  throw new Error('Unable to acquire connector installation lock')
}

export async function install({url: value, directory = join(homedir(), '.mangaflow', 'mcp-connector')}, dependencies = {}) {
  const url = setupUrl(value)
  const download = dependencies.download || fetchBytes
  const manifest = JSON.parse(await download(new URL(url.href + '/setup.json'), 32768))
  if (manifest.name !== 'mangaflow-mcp' || !/^\d+\.\d+\.\d+$/.test(manifest.version) ||
      !/^[a-f0-9]{64}$/.test(manifest.sha256) || manifest.package_path !== '/mangaflow/connector.tgz' ||
      !Number.isSafeInteger(manifest.size) || manifest.size < 1 || manifest.size > MAX_PACKAGE) {
    throw new Error('Invalid MangaFlow installation manifest')
  }
  const compressed = await download(new URL(manifest.package_path, url), MAX_PACKAGE)
  if (compressed.length !== manifest.size) throw new Error('Incomplete connector download')
  const files = unpackPackage(compressed, manifest.sha256)
  if (JSON.parse(files.get('package.json')).version !== manifest.version) throw new Error('Package version differs from manifest')
  const root = resolve(directory)
  await mkdir(root, {recursive: true, mode: 0o700})
  const unlock = await installationLock(join(root, 'install.lock'))
  try {
    const release = join(root, manifest.version + '-' + manifest.sha256.slice(0, 16))
    try {
      if (!(await lstat(release)).isDirectory()) throw new Error('Installation path is not a regular directory')
    } catch (error) {if (error.code !== 'ENOENT') throw error}
    await mkdir(release, {recursive: true, mode: 0o700})
    let reused = true
    const receipt = join(release, 'installed.json')
    try {if (JSON.parse(await readFile(receipt, 'utf8')).sha256 !== manifest.sha256) reused = false}
    catch {reused = false}
    for (const [name, contents] of files) {
      try {
        if (!(await lstat(join(release, name))).isFile() || !(await readFile(join(release, name))).equals(contents)) reused = false
      } catch (error) {if (error.code !== 'ENOENT') throw error; reused = false}
    }
    try {await stat(join(release, 'node_modules', '@modelcontextprotocol', 'sdk', 'dist', 'esm', 'client', 'index.js'))}
    catch {reused = false}
    if (!reused) {
      await unlink(receipt).catch(error => {if (error.code !== 'ENOENT') throw error})
      for (const [name, contents] of files) {
        // No extraction of arbitrary paths, links, scripts or extra archive entries.
        const target = join(release, name)
        try {
          if (!(await lstat(target)).isFile()) throw new Error('Unexpected installation file: ' + name)
        } catch (error) {if (error.code !== 'ENOENT') throw error}
        await writeFile(target, contents, {mode: 0o600})
      }
      await (dependencies.npmCi || npmCi)(release)
      await writeFile(receipt, JSON.stringify({sha256: manifest.sha256}), {mode: 0o600})
    }
    const server = serverConfiguration(release, url)
    const capabilities = await (dependencies.verify || verifyConnection)(release, server)
    return {installed: true, reused, version: manifest.version, directory: release,
      server, generic_configuration: {mcpServers: {mangaflow: server}},
      probe: {verified: true, connection_capabilities: capabilities},
      required_environment: url.protocol === 'https:' || url.pathname === '/mcp' ? ['MANGAFLOW_MCP_TOKEN'] : [],
      next_steps: ['Merge server.command/args/env into the CURRENT Agent client stdio MCP configuration; preserve other servers.',
        'Reload/reconnect this Agent, then call mcp_status to confirm local_import=true.',
        'The isolated installer probe succeeded; this does not mean the current Agent session has reloaded.']}
  } finally {await unlock()}
}

export async function main(args = process.argv.slice(2)) {
  const [major, minor] = process.versions.node.split('.').map(Number)
  if (major < 20 || (major === 20 && minor < 11)) throw new Error('Node.js >=20.11 and npm are required')
  const options = {url: process.env.MANGAFLOW_MCP_URL || 'http://localhost:8080/mangaflow'}
  for (let index = 0; index < args.length; index += 2) {
    if (!['--url', '--directory'].includes(args[index]) || !args[index + 1]) throw new Error('Usage: node install.mjs [--url URL] [--directory ABSOLUTE_DIRECTORY]')
    options[args[index] === '--url' ? 'url' : 'directory'] = args[index + 1]
  }
  const result = await install(options)
  process.stdout.write(JSON.stringify(result, null, 2) + '\n')
}

if (process.argv[1] && pathToFileURL(await realpath(process.argv[1])).href === import.meta.url) {
  main().catch(error => {process.stderr.write('MangaFlow MCP setup failed: ' + error.message + '\n'); process.exitCode = 1})
}
