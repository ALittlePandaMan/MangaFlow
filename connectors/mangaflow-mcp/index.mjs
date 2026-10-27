#!/usr/bin/env node
import {Client} from '@modelcontextprotocol/sdk/client/index.js'
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import {Server} from '@modelcontextprotocol/sdk/server/index.js'
import {StdioServerTransport} from '@modelcontextprotocol/sdk/server/stdio.js'
import {CallToolRequestSchema, ListToolsRequestSchema, ListPromptsRequestSchema, GetPromptRequestSchema, McpError, ErrorCode} from '@modelcontextprotocol/sdk/types.js'
import {createReadStream, createWriteStream} from 'node:fs'
import {realpath, stat, unlink} from 'node:fs/promises'
import {basename, resolve} from 'node:path'
import {Readable} from 'node:stream'
import {pipeline} from 'node:stream/promises'
import {pathToFileURL} from 'node:url'
import {randomUUID} from 'node:crypto'
import {DirectoryImports} from './directory-import.mjs'

export function endpoint(value) {
  const url = new URL(value)
  if (url.username || url.password || url.search || url.hash || !['/mangaflow','/mcp'].includes(url.pathname.replace(/\/$/, ''))) throw new Error('MANGAFLOW_MCP_URL must end in /mangaflow or /mcp without credentials/query/fragment')
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) throw new Error('Use HTTPS for remote MangaFlow')
  return url
}

export function transferUrl(base, path) {
  if (!/^\/(mangaflow|mcp)\/(?:(?:download|previews)\/[a-f0-9-]{36}|reports\/[a-f0-9-]{36}\/(?:json|markdown))$/.test(path)) throw new Error('Use a download_path returned by MangaFlow')
  return new URL(path, base)
}

export function connectionHeaders(url, token) {
  const local = ['localhost','127.0.0.1','[::1]'].includes(url.hostname)
  if (!token && (!local || url.pathname.replace(/\/$/,'') === '/mcp')) throw new Error('Set MANGAFLOW_MCP_TOKEN for remote or legacy /mcp connections; local /mangaflow requires no token')
  return token ? {Authorization:`Bearer ${token}`} : {}
}

export const localTools = [
  {name:'local_import',description:'Import an explicitly provided local directory, image, ZIP/CBZ/RAR/CBR or PDF. Streams uploads and auto-creates a project named after the input; project_id appends. Directories naturally sort images, recursively by default, ignoring links and metadata. Poll local_transfer_status then task_wait(kind=file) for result.job before workflow_start. Resume identical requests with the same operation_id; no scripts or manual project creation.',
    inputSchema:{type:'object',properties:{path:{type:'string',minLength:1},operation_id:{type:'string',minLength:1,maxLength:80},recursive:{type:'boolean',default:true},project_id:{type:'string'},project_name:{type:'string',minLength:1,maxLength:200},source_language:{type:'string'},target_language:{type:'string'}},required:['path','operation_id'],additionalProperties:false},
    annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:true,openWorldHint:true}},
  {name:'local_transfer_cancel',description:'Cancel a local import/download transfer. Stops streaming and cancels an accepted import job when possible. Cancellation never automatically resumes; completed pages remain.',
    inputSchema:{type:'object',properties:{transfer_id:{type:'string'}},required:['transfer_id'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:true}},
  {name: 'local_download', description: 'Stream an export, JSON/Markdown review report or preview to an explicitly selected local destination. Use download_path/report_downloads returned by MangaFlow. Never overwrites an existing file. Then view saved previews using your client image viewer if necessary.',
    inputSchema: {type: 'object', properties: {download_path: {type: 'string'}, destination: {type: 'string'}}, required: ['download_path', 'destination'], additionalProperties: false},
    annotations: {readOnlyHint: false, destructiveHint: false, openWorldHint: true}},
  {name:'local_transfer_status', description:'Check upload/download/directory import using transfer_id. Directory checkpoints survive connector restarts; INTERRUPTED needs local_import with the SAME operation_id/arguments to resume. COMPLETED directory transfer means result.job was accepted: wait for that file job before translating. Do not create another project or repeat a running transfer.',
    inputSchema:{type:'object',properties:{transfer_id:{type:'string'}},required:['transfer_id'],additionalProperties:false},
    annotations:{readOnlyHint:true,idempotentHint:true}},
]

export function withLocalCapabilities(response) {
  if (response.isError || !response.structuredContent) return response
  const data = {...response.structuredContent, connection_capabilities: {
    ...response.structuredContent.connection_capabilities, transport:'stdio',
    local_download:true, local_transfer_status:true,
    local_import:true, local_transfer_cancel:true, directory_import_recovery:true,
    native_image_content:true, client_image_viewing:'unknown',
    missing_local_files:'For a user-provided directory call local_import directly; it creates the project using the folder name and imports every supported image. local_import also accepts one image, archive or PDF. Paths must be accessible to this connector; no transfer scripts or extra libraries.',
  }}
  return {...response, structuredContent:data, content:[{type:'text',text:JSON.stringify(data)},
    ...(response.content || []).filter(block => block.type !== 'text')]}
}

export async function startTransfer(transfers, work, waitMs=20000, controller=new AbortController()) {
  const id=randomUUID()
  const state={transfer_id:id,status:'RUNNING'}
  Object.defineProperty(state,'controller',{value:controller})
  transfers.set(id,state)
  let timer
  const finished=work().then(result => Object.assign(state,{status:'COMPLETED',result}),
    error => Object.assign(state,{status:controller.signal.aborted ? 'CANCELLED' : 'FAILED',error:error.message}))
  try {
    await Promise.race([finished,new Promise(resolve => {timer=setTimeout(resolve,waitMs)})])
  } finally {clearTimeout(timer)}
  if (state.status === 'FAILED') throw new Error(state.error)
  return state.status === 'COMPLETED' ? state.result : {...state,next_tool:'local_transfer_status'}
}

export async function uploadFile(base, token, filename, signal) {
  const path = resolve(filename)
  const info = await stat(path)
  if (!info.isFile()) throw new Error('Provide one readable file, not a folder')
  const url = new URL(base.pathname.replace(/\/$/,'')+'/files', base)
  url.searchParams.set('filename', basename(path))
  const stream = createReadStream(path)
  try {
    const response = await fetch(url, {method: 'POST', headers: {...connectionHeaders(base, token), 'Content-Type': 'application/octet-stream', 'Content-Length': String(info.size)},
      body: stream, duplex: 'half', redirect: 'error', signal})
    if (!response.ok) throw new Error(`Upload failed (${response.status}): ${await response.text()}`)
    return response.json()
  } finally {stream.destroy()}
}

export async function downloadFile(base, token, path, destination, signal) {
  const response = await fetch(transferUrl(base, path), {headers: connectionHeaders(base, token), redirect: 'error',signal})
  if (!response.ok) throw new Error(`Download failed (${response.status}): ${await response.text()}`)
  const target = resolve(destination)
  const writer = createWriteStream(target, {flags: 'wx'})
  let created = false
  writer.once('open', () => {created = true})
  try {await pipeline(Readable.fromWeb(response.body), writer)}
  catch (error) {if (created) await unlink(target).catch(() => {}); throw error}
  return {path: target, bytes: writer.bytesWritten}
}

export async function main() {
  const transfers=new Map()
  const transferAbort=new AbortController()
  const url = endpoint(process.env.MANGAFLOW_MCP_URL || 'http://localhost:8080/mangaflow')
  const token = process.env.MANGAFLOW_MCP_TOKEN
  const remote = new Client({name: 'mangaflow-local-connector', version: '2.1.0'})
  const transport = new StreamableHTTPClientTransport(url, {requestInit: {headers: connectionHeaders(url, token)}})
  await remote.connect(transport)
  const directoryImports=new DirectoryImports({url,signal:transferAbort.signal,
    uploadHeaders:() => connectionHeaders(url,token),
    call:async (name,args) => {
      const result=await remote.callTool({name,arguments:args},undefined,{timeout:30000})
      if (result.isError) {
        const error=result.structuredContent?.error
        throw Object.assign(new Error(error?.message || JSON.stringify(result.content)),{code:error?.code || 'remote_tool_error'})
      }
      return result.structuredContent || JSON.parse(result.content.find(item => item.type === 'text').text)
    }})
  const advertised=await remote.listTools();
  if (!['project_import','workflow_advance','page_edit'].every(name => advertised.tools.some(tool => tool.name === name))) {await remote.close();throw new Error('Update MangaFlow server and the official connector to version 2.0.0 or newer')}
  const server = new Server({name: 'MangaFlow', version: '2.1.0'}, {capabilities: {tools: {}, prompts: {}},
    instructions: (remote.getInstructions() || '')+'\nOfficial local stdio connector: for a user-provided image directory call local_import directly. It names/creates the project and imports images automatically; do not ask the user to create/upload it. local_import/local_download/local_transfer_status/local_transfer_cancel are also available. No scripts or extra libraries are required.'})
  server.setRequestHandler(ListToolsRequestSchema, async request => {
    if (request.params?.cursor !== undefined) throw new McpError(ErrorCode.InvalidParams, 'Invalid cursor: this list has no further pages')
    const tools = []
    let cursor
    do {
      const page = await remote.listTools(cursor ? {cursor} : {})
      tools.push(...page.tools)
      cursor = page.nextCursor
    } while (cursor)
    return {tools: [...tools, ...localTools]}
  })
  server.setRequestHandler(ListPromptsRequestSchema, async request => remote.listPrompts(request.params))
  server.setRequestHandler(GetPromptRequestSchema, async request => remote.getPrompt(request.params))
  server.setRequestHandler(CallToolRequestSchema, async request => {
    try {
      const {name, arguments: args = {}} = request.params
      let data
      if (name === 'local_import') data=await directoryImports.start(args)
      else if (name === 'local_transfer_cancel') {
        if (args.transfer_id?.startsWith('dir-')) data=await directoryImports.cancel(args.transfer_id)
        else {const state=transfers.get(args.transfer_id);if (!state) throw new Error('Transfer not found');if (state.status === 'RUNNING') {state.controller.abort();state.status='CANCELLING'};data=state}
      }
      else if (name === 'local_download') {
        const controller=new AbortController();const abort=() => controller.abort();transferAbort.signal.addEventListener('abort',abort,{once:true});
        data=await startTransfer(transfers,() => downloadFile(url,token,args.download_path,args.destination,controller.signal).finally(() => transferAbort.signal.removeEventListener('abort',abort)),20000,controller)
      }
      else if (name === 'local_transfer_status') {
        if (args.transfer_id?.startsWith('dir-')) data=await directoryImports.status(args.transfer_id)
        else {
        const transfer=transfers.get(args.transfer_id)
        if (!transfer) throw new Error('Transfer not found; this connector session may have ended')
        data=transfer
        }
      }
      else {
        const response=await remote.callTool({name, arguments: args}, undefined, {timeout: 30000})
        return name === 'mcp_status' ? withLocalCapabilities(response) : response
      }
      return {content: [{type: 'text', text: JSON.stringify(data)}], structuredContent: data}
    } catch (error) {
      if (error instanceof McpError) throw error
      const data={error:{code:error.code || 'connector_error',message:error.message,retryable:false,...(error.transfer_id ? {transfer_id:error.transfer_id} : {})}}
      return {isError:true,structuredContent:data,content:[{type:'text',text:JSON.stringify(data)}]}
    }
  })
  let closing=false
  const close = async () => {
    if (closing) return
    closing=true;transferAbort.abort();await server.close();await remote.close()
  }
  process.once('SIGINT', () => {void close()})
  process.once('SIGTERM', () => {void close()})
  // Windows npm shims spawn node as a child: closing stdio must release its
  // remote HTTP connection too, otherwise the shim/Agent can hang on disconnect.
  process.stdin.once('end', () => {void close()})
  server.onclose = () => {void close()}
  await server.connect(new StdioServerTransport())
}

// npm local/global installs may launch through a symlink or Windows junction.
if (process.argv[1] && import.meta.url === pathToFileURL(await realpath(resolve(process.argv[1]))).href) {
  main().catch(error => {process.stderr.write(`${error.message}\n`); process.exitCode = 1})
}
