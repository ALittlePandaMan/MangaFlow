import {test} from 'node:test'
import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {createHash} from 'node:crypto'
import {gzipSync} from 'node:zlib'
import {setupUrl, unpackPackage, fetchBytes, serverConfiguration} from './install.mjs'
import {createServer} from 'node:http'

test('bootstrap accepts only loopback HTTP or remote HTTPS service endpoints', () => {
  assert.equal(setupUrl('http://localhost:8080/mangaflow/').href, 'http://localhost:8080/mangaflow')
  assert.equal(setupUrl('https://manga.example/mangaflow').protocol, 'https:')
  for (const value of ['http://manga.example/mangaflow', 'https://user:pass@manga.example/mangaflow',
    'https://manga.example/mangaflow?token=secret', 'file:///mangaflow', 'https://manga.example/api']) {
    assert.throws(() => setupUrl(value))
  }
})

test('installer requires an explicit address before starting installation', () => {
  for (const value of [undefined,'','   ']) assert.throws(() => setupUrl(value), /Provide --url or MANGAFLOW_MCP_URL/)
  const env={...process.env}
  delete env.MANGAFLOW_MCP_URL
  const result=spawnSync(process.execPath,[fileURLToPath(new URL('./install.mjs',import.meta.url))],{env,encoding:'utf8',timeout:10000})
  assert.equal(result.status,1)
  assert.match(result.stderr,/Provide --url or MANGAFLOW_MCP_URL/)
  assert.equal(result.stdout,'')
  assert.equal(setupUrl('http://127.0.0.1:18080/mangaflow').port,'18080')
})

test('installer rejects malformed raw URLs before downloading anything', () => {
  for (const value of ['https://[::1/mangaflow','https://manga example/mangaflow',
    'https://manga.example\\evil/mangaflow','\nhttps://manga.example/mangaflow',
    'https://@manga.example/mangaflow','https://manga.example:0/mangaflow',
    'https://manga.example:65536/mangaflow','https://manga.example:/mangaflow',
    'https://manga.example/mangaflow?','https://manga.example/mangaflow#',
    'https://manga.example/mangaflow//','https://%65xample.com/mangaflow']) assert.throws(() => setupUrl(value))
})

test('bootstrap refuses corrupt, unexpected and traversal tar entries before extraction', () => {
  const corrupt = gzipSync(Buffer.alloc(1024))
  assert.throws(() => unpackPackage(corrupt, '0'.repeat(64)), /checksum mismatch/)
  assert.throws(() => unpackPackage(corrupt, createHash('sha256').update(corrupt).digest('hex')), /incomplete/)
  for (const name of ['package/../index.mjs', '/etc/passwd', 'package/extra.mjs']) {
    const tar = Buffer.alloc(2048)
    tar.write(name, 0)
    tar.write('00000000001\0', 124)
    tar[156] = 48
    tar[512] = 65
    const bytes = gzipSync(tar)
    assert.throws(() => unpackPackage(bytes, createHash('sha256').update(bytes).digest('hex')), /Invalid connector package entry/)
  }
})

test('absolute stdio configuration contains no credential or shell dependency', () => {
  const config = serverConfiguration('/installed/漫画 folder', new URL('https://manga.example/mangaflow'))
  assert.equal(config.command, process.execPath)
  assert.equal(config.args.length, 1)
  assert.match(config.args[0], /index\.mjs$/)
  assert.deepEqual(config.env, {MANGAFLOW_MCP_URL: 'https://manga.example/mangaflow'})
  assert.equal(config.type, 'stdio')
})

test('bootstrap downloads reject redirects and bounded streaming overflow', async t => {
  const server = createServer((request, response) => {
    if (request.url === '/redirect') {response.writeHead(302, {location: '/other'}); response.end()}
    else if (request.url === '/large') {response.writeHead(200); response.write('123456789'); response.end('too large')}
    else {response.writeHead(200); response.end('ok')}
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => {server.closeAllConnections(); server.close(resolve)}))
  const base = `http://127.0.0.1:${server.address().port}`
  await assert.rejects(fetchBytes(new URL(base + '/redirect'), 100))
  await assert.rejects(fetchBytes(new URL(base + '/large'), 5), /size limit/)
  assert.equal((await fetchBytes(new URL(base + '/other'), 5)).toString(), 'ok')
})
