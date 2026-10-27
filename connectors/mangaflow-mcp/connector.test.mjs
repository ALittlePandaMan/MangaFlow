import {test} from 'node:test'
import assert from 'node:assert/strict'
import {endpoint, transferUrl, startTransfer, connectionHeaders, withLocalCapabilities, localTools} from './index.mjs'

test('only four local comic transfer tools are public',() => {
  assert.deepEqual(localTools.map(tool => tool.name).sort(),['local_download','local_import','local_transfer_cancel','local_transfer_status'])
})

test('stdio reports actual transfer capabilities in both text and structured results', () => {
  const original={content:[{type:'text',text:'{}'}],structuredContent:{enabled:true,
    connection_capabilities:{transport:'http',local_import:false,native_image_content:true}}}
  const updated=withLocalCapabilities(original)
  assert.equal(updated.structuredContent.connection_capabilities.transport,'stdio')
  assert.equal(updated.structuredContent.connection_capabilities.local_import,true)
  assert.equal(updated.structuredContent.connection_capabilities.local_download,true)
  assert.equal(updated.structuredContent.connection_capabilities.client_image_viewing,'unknown')
  assert.deepEqual(JSON.parse(updated.content[0].text),updated.structuredContent)
  assert.equal(original.structuredContent.connection_capabilities.transport,'http')
  const error={isError:true,content:[{type:'text',text:'failed'}]}
  assert.equal(withLocalCapabilities(error),error)
})

test('remote TLS and credential boundaries', () => {
  assert.equal(endpoint('http://127.0.0.1:8080/mcp').hostname, '127.0.0.1')
  assert.equal(endpoint('http://localhost:8080/mangaflow').pathname, '/mangaflow')
  assert.equal(endpoint('https://manga.example/mcp').protocol, 'https:')
  for (const value of ['http://manga.example/mcp', 'https://u:p@manga.example/mcp', 'https://manga.example/api', 'https://manga.example/mcp?key=secret']) {
    assert.throws(() => endpoint(value))
  }
})
test('the local canonical endpoint requires no key while remote and legacy access still do', () => {
  for (const host of ['localhost','127.0.0.1','[::1]']) {
    assert.deepEqual(connectionHeaders(endpoint(`http://${host}:8080/mangaflow`)), {})
  }
  for (const url of ['https://manga.example/mangaflow','http://localhost:8080/mcp']) {
    assert.throws(() => connectionHeaders(endpoint(url)))
    assert.deepEqual(connectionHeaders(endpoint(url),'test-key'), {Authorization:'Bearer test-key'})
  }
})
test('file transfer cannot send credentials to arbitrary origins or paths', () => {
  const base = endpoint('https://manga.example/mcp')
  const id = '01234567-0123-0123-0123-012345678901'
  assert.equal(transferUrl(base, `/mcp/download/${id}`).hostname, base.hostname)
  assert.equal(transferUrl(base, `/mangaflow/previews/${id}`).hostname, base.hostname)
  assert.equal(transferUrl(base, `/mangaflow/reports/${id}/json`).hostname, base.hostname)
  assert.equal(transferUrl(base, `/mcp/reports/${id}/markdown`).hostname, base.hostname)
  for (const path of [`/mangaflow/reports/${id}/../../secret`, `/mcp/reports/${id}/html`]) assert.throws(() => transferUrl(base, path))
  for (const path of ['https://evil.example/mcp/download/'+id, '/api/projects', '/mcp/previews/../../secret', '//evil.example/path']) assert.throws(() => transferUrl(base, path))
})

test('slow transfers remain queryable after the tool returns', async () => {
  const transfers=new Map()
  let finish
  const pending=new Promise(resolve => {finish=resolve})
  const running=await startTransfer(transfers,() => pending,1)
  assert.equal(running.status,'RUNNING')
  finish({asset_id:'uploaded'})
  await pending
  await Promise.resolve()
  assert.equal(transfers.get(running.transfer_id).status,'COMPLETED')
  assert.equal(transfers.get(running.transfer_id).result.asset_id,'uploaded')
})
