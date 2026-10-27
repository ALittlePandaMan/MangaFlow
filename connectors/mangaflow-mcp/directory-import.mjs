// Local filesystem access stays in the official connector, never in an Agent script.
import {constants} from 'node:fs'
import {lstat, realpath, readdir, mkdir, readFile, writeFile, rename, unlink, open, stat} from 'node:fs/promises'
import {basename, dirname, extname, isAbsolute, join, relative, resolve, sep} from 'node:path'
import {homedir} from 'node:os'
import {createHash, randomUUID} from 'node:crypto'

const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const supported = new Set(['.jpg','.jpeg','.png','.webp'])
const terminal = new Set(['COMPLETED','FAILED','CANCELLED'])
const ignored = name => name.startsWith('.') || name === '__MACOSX' || ['thumbs.db','desktop.ini'].includes(name.toLowerCase())
const fail = (code, message) => Object.assign(new Error(message), {code})
const signature = info => [String(info.dev), String(info.ino), String(info.size), String(info.mtimeNs), String(info.ctimeNs)]
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b)

// Match the backend's digit-aware ordering without locale-dependent collation.
export function naturalCompare(a,b) {
  const aa=a.toLowerCase().split(/(\d+)/), bb=b.toLowerCase().split(/(\d+)/)
  for (let i=0;i<Math.min(aa.length,bb.length);i++) {
    const numeric=/^\d+$/.test(aa[i]) && /^\d+$/.test(bb[i])
    const x=numeric ? BigInt(aa[i]) : aa[i], y=numeric ? BigInt(bb[i]) : bb[i]
    if (x !== y) return x < y ? -1 : 1
  }
  return aa.length-bb.length || (a < b ? -1 : a > b ? 1 : 0)
}

export async function scanDirectory(path, recursive, limits, check=async () => {}) {
  await check()
  if (!isAbsolute(path)) throw fail('invalid_path','Provide an absolute directory path accessible to this connector')
  const root=resolve(path), info=await lstat(root)
  if (info.isSymbolicLink() || !info.isDirectory()) throw fail('invalid_path','Provide a real directory, not a symlink or junction')
  const canonical=await realpath(root), files=[], names=new Set()
  let bytes=0, visited=0
  const visit=async directory => {
    await check()
    // Re-check every directory, including between enumeration and traversal.
    const directoryInfo=await lstat(directory)
    if (directoryInfo.isSymbolicLink() || !directoryInfo.isDirectory() || await realpath(directory) !== directory) throw fail('unsafe_path','Directory changed or contains a symlink/junction')
    for (const entry of await readdir(directory,{withFileTypes:true})) {
      await check()
      if (++visited > limits.max_pages*10) throw fail('too_many_entries','Directory contains too many entries')
      if (ignored(entry.name)) continue
      const full=join(directory,entry.name), current=await lstat(full,{bigint:true})
      if (current.isSymbolicLink()) continue
      if (current.isDirectory()) {if (recursive) await visit(full);continue}
      if (!current.isFile() || !supported.has(extname(entry.name).toLowerCase())) continue
      const relative_path=relative(canonical,full).split(sep).join('/')
      if (relative_path.includes(':') || relative_path.split('/').some(part => part === '..') || Buffer.byteLength(entry.name,'utf8') > 255) throw fail('invalid_path','Unsupported image filename')
      if (names.has(relative_path.toLowerCase())) throw fail('duplicate_path','Image paths differ only by case')
      names.add(relative_path.toLowerCase())
      const size=Number(current.size)
      bytes+=size
      if (!size || size > limits.max_image_bytes || bytes > limits.max_total_bytes || files.length >= limits.max_pages) throw fail('import_limit','Directory exceeds configured page/byte limits or contains an empty image')
      files.push({relative_path, size, signature:signature(current)})
    }
  }
  await visit(canonical)
  if (!files.length) throw fail('empty_directory','No JPG, JPEG, PNG or WebP images found in the specified directory')
  files.sort((a,b) => naturalCompare(a.relative_path,b.relative_path))
  return {root:canonical, files, total_bytes:bytes}
}

export async function scanInput(path, recursive, limits, check=async () => {}) {
  await check()
  if (!isAbsolute(path)) throw fail('invalid_path','Provide an absolute local file or directory path')
  const info=await lstat(path,{bigint:true})
  if (info.isSymbolicLink()) throw fail('invalid_path','Provide a real file/directory, not a symlink or junction')
  if (info.isDirectory()) return {...await scanDirectory(path,recursive,limits,check),input_kind:'directory'}
  const suffix=extname(path).toLowerCase(), container=['.zip','.cbz','.rar','.cbr','.pdf'].includes(suffix)
  if (!info.isFile() || !supported.has(suffix) && !container) throw fail('unsupported_file','Use a directory, image, comic ZIP/CBZ/RAR/CBR or PDF')
  const size=Number(info.size), maximum=container ? limits.max_container_bytes : limits.max_image_bytes
  if (!size || !maximum || size>maximum) throw fail('import_limit','File exceeds configured byte limit or is empty')
  const canonical=await realpath(path), name=basename(canonical)
  if (Buffer.byteLength(name,'utf8')>255) throw fail('invalid_path','Unsupported filename length')
  return {root:dirname(canonical),files:[{relative_path:name,size,signature:signature(info)}],total_bytes:size,input_kind:'file'}
}

export class DirectoryImports {
  constructor({url, call, uploadHeaders, cacheDir, signal}) {
    this.url=url; this.call=call; this.uploadHeaders=uploadHeaders
    this.cacheDir=cacheDir || process.env.MANGAFLOW_MCP_STATE_DIR || join(homedir(),'.mangaflow','mcp-transfers')
    this.signal=signal; this.active=new Map();this.saves=new Map()
  }
  id(operation_id) {return 'dir-'+hash([this.url.href,operation_id])}
  path(id) {
    if (!/^dir-[a-f0-9]{64}$/.test(id)) throw fail('invalid_transfer','Invalid directory transfer ID')
    return join(this.cacheDir,id+'.json')
  }
  async load(id) {
    try {return JSON.parse(await readFile(this.path(id),'utf8'))}
    catch (error) {if (error.code === 'ENOENT') throw fail('transfer_not_found','Directory transfer not found on this connector');throw error}
  }
  async save(state) {
    const id=state.transfer_id
    const previous=this.saves.get(id) || Promise.resolve()
    const next=previous.catch(() => {}).then(async () => {
      await mkdir(this.cacheDir,{recursive:true,mode:0o700})
      const path=this.path(id), temporary=path+'.'+randomUUID()+'.tmp'
      try {await writeFile(temporary,JSON.stringify(state),{mode:0o600,flag:'wx'});await rename(temporary,path)}
      finally {await unlink(temporary).catch(() => {})}
    })
    this.saves.set(id,next)
    try {await next} finally {if (this.saves.get(id) === next) this.saves.delete(id)}
  }
  async checkCancelled(state, signal) {
    signal.throwIfAborted()
    try {await stat(this.path(state.transfer_id)+'.cancel')}
    catch (error) {if (error.code === 'ENOENT') return;throw error}
    state.cancel_requested=true;state.status='CANCELLED'
    throw fail('transfer_cancelled','Directory import was cancelled; it will not resume automatically')
  }
  async lock(id) {
    await mkdir(this.cacheDir,{recursive:true,mode:0o700})
    const path=this.path(id)+'.lock'
    for (let attempt=0;attempt<2;attempt++) {
      try {
        const handle=await open(path,'wx',0o600)
        await handle.writeFile(String(process.pid));await handle.close()
        let released=false
        return async () => {if (!released) {released=true;await unlink(path).catch(() => {})}}
      } catch (error) {
        if (error.code !== 'EEXIST') throw error
        const pid=Number(await readFile(path,'utf8'))
        if (!pid) {
          if (Date.now()-(await stat(path)).mtimeMs < 60000) break
        } else {
          try {process.kill(pid,0);break} catch (probe) {if (probe.code !== 'ESRCH') break}
        }
        await unlink(path).catch(() => {})
      }
    }
    throw Object.assign(fail('transfer_busy','Another connector is handling this operation; query local_transfer_status and reuse the same operation_id'),{transfer_id:id})
  }
  public(state) {
    return {transfer_id:state.transfer_id,operation_id:state.operation_id,status:state.status,stage:state.stage,
      completed_files:Object.keys(state.assets).length,total_files:state.files.length,
      uploaded_bytes:state.uploaded_bytes,total_bytes:state.total_bytes,
      ...(state.result ? {result:state.result} : {}),...(state.error ? {error:state.error} : {}),
      next_tool:state.status === 'RUNNING' ? (this.active.has(state.transfer_id) ? 'local_transfer_status' : 'local_import') : undefined}
  }
  async recovered(state, supplied) {
    const remote=supplied || await this.call('project_import_status',{operation_id:state.operation_id,source_fingerprint:state.fingerprint})
    if (remote.found) {
      state.submission_started=false
      state.result={project_id:remote.project_id,job:remote.job}
      state.status=remote.job.status === 'FAILED' ? 'FAILED' : remote.job.status === 'CANCELLED' ? 'CANCELLED' : 'COMPLETED'
      state.stage=remote.job.status;await this.save(state);return true
    }
    if (remote.operation) {
      if (remote.operation.status === 'COMPLETED' && remote.operation.result?.structuredContent?.job) {
        state.submission_started=false
        state.result={...remote.operation.result.structuredContent,job_expired:true}
        state.status='COMPLETED';state.stage='EXPIRED';await this.save(state);return true
      }
      if (remote.operation.status === 'COMPLETED' && remote.operation.result?.isError) {
        state.submission_started=false
        state.status='FAILED';state.stage='REJECTED'
        state.error=remote.operation.result.structuredContent?.error || {code:'import_rejected',message:'Import rejected; inspect operation_status'}
        await this.save(state);return true
      }
      throw fail('operation_requires_inspection',`Import submission has status ${remote.operation.status}; inspect operation_status before resubmitting`)
    }
    return false
  }
  async start(args, waitMs=20000) {
    const deadline=Date.now()+Math.max(0,Math.min(20000,waitMs))
    if (typeof args.path !== 'string' || !isAbsolute(args.path) || typeof args.operation_id !== 'string' || !args.operation_id.length || args.operation_id.length > 80) throw fail('invalid_arguments','path must be absolute; operation_id must contain 1–80 characters')
    if (Object.keys(args).some(key => !['path','operation_id','recursive','project_id','project_name','source_language','target_language'].includes(key)) || ['project_id','project_name','source_language','target_language'].some(key => args[key] !== undefined && (typeof args[key] !== 'string' || !args[key].trim())) || args.project_name?.length > 200) throw fail('invalid_arguments','Invalid directory import options')
    const normalized={path:resolve(args.path),recursive:args.recursive ?? true,
      ...Object.fromEntries(['project_id','project_name','source_language','target_language'].filter(key => args[key] !== undefined).map(key => [key,args[key]]))}
    if (typeof normalized.recursive !== 'boolean' || args.project_id && ['project_name','source_language','target_language'].some(key => args[key] !== undefined)) throw fail('invalid_arguments','Existing projects retain their name/languages; recursive must be boolean')
    const id=this.id(args.operation_id), fingerprint=hash(normalized)
    const running=this.active.get(id)
    if (running) {
      if (running.state.fingerprint !== fingerprint) throw fail('operation_conflict','operation_id was used for different arguments')
      return this.public(running.state)
    }
    const release=await this.lock(id)
    let state
    try {
      try {state=await this.load(id)} catch (error) {if (error.code !== 'transfer_not_found') throw error}
      if (state && state.fingerprint !== fingerprint) throw fail('operation_conflict','operation_id was used for different arguments')
      if (state?.status === 'CANCELLED') return this.public(state)
      if (!state) state={transfer_id:id,operation_id:args.operation_id,fingerprint,args:normalized,
        root:normalized.path,files:[],total_bytes:0,assets:{},uploaded_bytes:0,manifest_ready:false}
      state.status='RUNNING';state.stage='INITIALIZING';delete state.error
      await this.save(state)
      const controller=new AbortController()
      const abort=() => controller.abort()
      this.signal?.addEventListener('abort',abort,{once:true})
      if (this.signal?.aborted) controller.abort()
      let initializationError, initialized=false
      const finished=(async () => {
        if (state.cancel_requested) {await this.cancel(id);return}
        await this.checkCancelled(state,controller.signal)
        const config=await this.call('mcp_status',{})
        if (!config.import_limits || !config.connection_capabilities?.directory_import_identity) throw fail('server_upgrade_required','Update MangaFlow to use verified directory import recovery')
        await this.checkCancelled(state,controller.signal)
        if (await this.recovered(state)) return
        state.stage='SCANNING';await this.save(state)
        const scanned=await scanInput(normalized.path,normalized.recursive,config.import_limits,
          () => this.checkCancelled(state,controller.signal))
        const hasManifest=state.manifest_ready ?? state.files.length > 0
        if (hasManifest && (!same(state.files,scanned.files) || state.root !== scanned.root)) throw fail('source_changed','Directory changed since this operation began; inspect it before using a new operation_id')
        Object.assign(state,scanned,{manifest_ready:true,stage:'UPLOADING'})
        await this.save(state);initialized=true
        await this.run(state,controller.signal)
      })().catch(async error => {
        if (!initialized && !state.cancel_requested && error.name !== 'AbortError') initializationError=error
        state.status=state.cancel_requested ? (state.submission_started ? 'CANCELLING' : 'CANCELLED') : error.name === 'AbortError' ? 'INTERRUPTED' : 'FAILED'
        state.error={code:error.code || 'directory_import_failed',message:error.message,retryable:state.status === 'INTERRUPTED'}
        await this.save(state)
      }).finally(async () => {this.signal?.removeEventListener('abort',abort);this.active.delete(id);await release()})
      this.active.set(id,{state,controller,finished})
      let timer
      try {await Promise.race([finished,new Promise(resolve => {timer=setTimeout(resolve,Math.max(0,deadline-Date.now()))})])}
      finally {clearTimeout(timer)}
      if (initializationError) throw initializationError
      return this.public(state)
    } finally {
      if (!this.active.has(id)) await release()
    }
  }
  async upload(state, item, signal, inFlight) {
    await this.checkCancelled(state,signal)
    const path=join(state.root,...item.relative_path.split('/'))
    if (await realpath(path) !== path || !same(signature(await lstat(path,{bigint:true})),item.signature)) throw fail('source_changed',`Image changed: ${item.relative_path}`)
    const handle=await open(path,constants.O_RDONLY | (constants.O_NOFOLLOW || 0))
    let stream
    try {
      if (!same(signature(await handle.stat({bigint:true})),item.signature)) throw fail('source_changed',`Image changed: ${item.relative_path}`)
      stream=handle.createReadStream({autoClose:false})
      const checkCancelled=() => this.checkCancelled(state,signal)
      const body=(async function* () {
        let lastCheck=0
        for await (const chunk of stream) {
          if (Date.now()-lastCheck >= 500) {await checkCancelled();lastCheck=Date.now()}
          inFlight.set(item.relative_path,(inFlight.get(item.relative_path)||0)+chunk.length)
          state.uploaded_bytes=state.files.filter(file => state.assets[file.relative_path]).reduce((sum,file) => sum+file.size,0)+[...inFlight.values()].reduce((a,b) => a+b,0)
          yield chunk
        }
      })()
      const url=new URL(this.url.pathname.replace(/\/$/,'')+'/files',this.url)
      url.searchParams.set('filename',basename(path))
      const response=await fetch(url,{method:'POST',headers:{...this.uploadHeaders(),'Content-Type':'application/octet-stream','Content-Length':String(item.size)},body,duplex:'half',redirect:'error',signal})
      if (!response.ok) throw fail('upload_failed',`Upload failed (${response.status}): ${await response.text()}`)
      const asset=await response.json()
      if (!same(signature(await handle.stat({bigint:true})),item.signature) || !same(signature(await lstat(path,{bigint:true})),item.signature) || await realpath(path) !== path) throw fail('source_changed',`Image changed during upload: ${item.relative_path}`)
      state.assets[item.relative_path]=asset.asset_id
      inFlight.delete(item.relative_path)
      await this.save(state)
    } finally {stream?.destroy();await handle.close()}
  }
  async run(state, signal) {
    const ids=Object.values(state.assets)
    const status=await this.call('project_import_status',{operation_id:state.operation_id,asset_ids:ids,source_fingerprint:state.fingerprint})
    if (status.found || status.operation) {await this.recovered(state,status);return}
    const available=new Set(status.available_asset_ids)
    for (const [name,id] of Object.entries(state.assets)) if (!available.has(id)) delete state.assets[name]
    state.uploaded_bytes=state.files.filter(file => state.assets[file.relative_path]).reduce((sum,file) => sum+file.size,0)
    const pending=state.files.filter(file => !state.assets[file.relative_path]), inFlight=new Map()
    let cursor=0, uploadError
    // Wait for both workers to settle before releasing the checkpoint lock.
    await Promise.all([0,1].map(async () => {
      while (cursor<pending.length && !uploadError) {
        try {await this.upload(state,pending[cursor++],signal,inFlight)} catch (error) {uploadError=error}
      }
    }))
    if (uploadError) throw uploadError
    await this.checkCancelled(state,signal)
    // Freeze all directory entries again before publishing; files may have changed
    // after their individual uploads completed, including already-uploaded entries.
    const config=await this.call('mcp_status',{})
    await this.checkCancelled(state,signal)
    const current=await scanInput(state.args.path,state.args.recursive,config.import_limits,() => this.checkCancelled(state,signal))
    if (!same(state.files,current.files)) throw fail('source_changed','Directory changed during upload; no project was submitted')
    state.stage='SUBMITTING';await this.save(state)
    await this.checkCancelled(state,signal)
    const args={operation_id:state.operation_id,source_fingerprint:state.fingerprint,
      images:state.files.map(file => ({relative_path:file.relative_path,asset_id:state.assets[file.relative_path]}))}
    const first=state.files[0]
    if (state.input_kind === 'file' && !supported.has(extname(first.relative_path).toLowerCase())) {
      args.asset_id=state.assets[first.relative_path];delete args.images
    }
    if (state.args.project_id) args.project_id=state.args.project_id
    else args.project_name=state.args.project_name || Array.from(state.input_kind === 'file' ? basename(first.relative_path,extname(first.relative_path)) : basename(state.root)).slice(0,200).join('') || 'Comic'
    for (const key of ['source_language','target_language']) if (state.args[key] !== undefined) args[key]=state.args[key]
    try {
      // From this point, cancellation is pending until the server acknowledges
      // the submission outcome. Never report CANCELLED while a write is in flight.
      state.submission_started=true
      const result=await this.call('project_import',args)
      if (!result.job) {state.stage='RECOVERING';await this.save(state);if (!await this.recovered(state)) throw fail('operation_requires_inspection','Submission result is not yet known; inspect project_import_status')}
      else {state.submission_started=false;state.result=result;state.status='COMPLETED';state.stage='SUBMITTED';await this.save(state)}
    } catch (error) {
      if (['conflict','invalid_request','expired','too_large','not_found','rate_limited','unauthorized'].includes(error.code)) {state.submission_started=false;throw error}
      if (!await this.recovered(state)) throw error
    }
    // Cancellation racing with submission must cancel the accepted server job too.
    let cancelled=state.cancel_requested
    try {await stat(this.path(state.transfer_id)+'.cancel');cancelled=true} catch (error) {if (error.code !== 'ENOENT') throw error}
    if (cancelled && state.result?.job) await this.cancel(state.transfer_id)
  }
  async status(id) {
    const active=this.active.get(id)
    const state=active?.state || await this.load(id)
    if (!active && state.cancel_requested) return this.cancel(id)
    if (!active && state.status !== 'CANCELLED') await this.recovered(state)
    if (!active && ['RUNNING','INTERRUPTED'].includes(state.status)) {
      let runningElsewhere=false
      try {
        const pid=Number(await readFile(this.path(id)+'.lock','utf8'))
        if (pid) {process.kill(pid,0);runningElsewhere=true}
      } catch (error) {if (!['ENOENT','ESRCH'].includes(error.code)) runningElsewhere=true}
      return {...this.public(state),running_elsewhere:runningElsewhere,
        next_tool:runningElsewhere ? 'local_transfer_status' : 'local_import'}
    }
    return this.public(state)
  }
  async cancel(id) {
    const active=this.active.get(id), state=active?.state || await this.load(id)
    state.cancel_requested=true
    const submitting=state.submission_started || ['SUBMITTING','RECOVERING'].includes(state.stage)
    state.status=submitting ? 'CANCELLING' : 'CANCELLED';active?.controller.abort()
    await writeFile(this.path(id)+'.cancel','cancelled',{mode:0o600})
    const remote=await this.call('project_import_status',{operation_id:state.operation_id,source_fingerprint:state.fingerprint})
    if (remote.found) {
      const job=terminal.has(remote.job.status) ? remote.job : await this.call('task_cancel',{task_id:remote.job.id,kind:'file',operation_id:('cancel-'+hash([id,remote.job.id])).slice(0,80)})
      state.result={project_id:remote.project_id,job};state.status=terminal.has(job.status) ? job.status : 'CANCELLING'
    } else state.status=submitting || remote.operation && ['RUNNING','UNKNOWN'].includes(remote.operation.status) ? 'CANCELLING' : 'CANCELLED'
    state.stage=state.status;await this.save(state)
    return this.public(state)
  }
}
