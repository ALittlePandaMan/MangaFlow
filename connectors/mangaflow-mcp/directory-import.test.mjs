import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp, mkdir, writeFile, readFile, rm, symlink} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {createServer} from 'node:http'
import {randomUUID} from 'node:crypto'
import {DirectoryImports, scanDirectory} from './directory-import.mjs'

const limits={max_pages:2000,max_image_bytes:1024**2,max_container_bytes:10*1024**2,max_total_bytes:10*1024**2,max_page_pixels:50000000}
async function fixture(t) {
  const root=await mkdtemp(join(tmpdir(),'mangaflow-dir-test-'))
  const comic=join(root,'漫画 インティメイト'), cacheDir=join(root,'state')
  await mkdir(comic)
  const uploads=[], submissions=[], available=new Set(), jobs=new Map()
  let intercept=async () => {}, beforeSubmit=async () => {}
  const server=createServer(async (request,response) => {
    try {
      let bytes=0
      for await (const chunk of request) bytes+=chunk.length
      await intercept(request)
      const asset_id=randomUUID();available.add(asset_id);uploads.push({bytes,asset_id})
      response.writeHead(200,{'Content-Type':'application/json'});response.end(JSON.stringify({asset_id}))
    } catch {response.destroy()}
  })
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve))
  const url=new URL(`http://127.0.0.1:${server.address().port}/mangaflow`)
  const call=async (name,args) => {
    if (name === 'mcp_status') return {import_limits:limits,connection_capabilities:{directory_import_identity:true}}
    if (name === 'project_import_status') {
      const previous=jobs.get(args.operation_id)
      if (previous?.found && args.source_fingerprint && previous.source_fingerprint !== args.source_fingerprint) throw Object.assign(new Error('Import source differs'),{code:'conflict'})
      return {...(previous || {found:false}),available_asset_ids:(args.asset_ids || []).filter(id => available.has(id))}
    }
    if (name === 'project_import') {
      await beforeSubmit(args)
      submissions.push(args)
      const project_id=randomUUID(), job={id:randomUUID(),status:'QUEUED'}
      jobs.set(args.operation_id,{found:true,project_id,job,source_fingerprint:args.source_fingerprint})
      return {project_id,job,project_name:args.project_name,source_fingerprint:args.source_fingerprint}
    }
    if (name === 'task_cancel') {
      const found=[...jobs.values()].find(item => item.job.id === args.task_id)
      found.job.status='CANCELLED';return found.job
    }
    throw new Error(name)
  }
  const make=options => new DirectoryImports({url,call,cacheDir,uploadHeaders:() => ({}),...options})
  t.after(async () => {server.closeAllConnections();await new Promise(resolve => server.close(resolve));await rm(root,{recursive:true,force:true})})
  return {comic,root,cacheDir,make,uploads,submissions,jobs,available,
    intercept(fn) {intercept=fn},beforeSubmit(fn) {beforeSubmit=fn}}
}

test('Unicode directory creates one named project, orders 11 images, reconnects without duplicates',async t => {
  const f=await fixture(t)
  for (let i=11;i>0;i--) await writeFile(join(f.comic,`${i}.png`),'fake-pixels')
  await writeFile(join(f.comic,'readme.txt'),'ignored')
  await writeFile(join(f.comic,'nested.cbz'),'ignored')
  const args={path:f.comic,operation_id:'directory-unicode-1',target_language:'en'}
  const result=await f.make().start(args)
  assert.equal(result.status,'COMPLETED',JSON.stringify(result))
  assert.equal(result.result.project_name,'漫画 インティメイト')
  assert.equal(f.uploads.length,11)
  assert.deepEqual(f.submissions[0].images.map(file => file.relative_path),Array.from({length:11},(_,i) => `${i+1}.png`))
  const reconnected=await f.make().start(args)
  assert.equal(reconnected.result.project_id,result.result.project_id)
  assert.equal(f.submissions.length,1)
  await assert.rejects(f.make().start({...args,target_language:'ja'}),{code:'operation_conflict'})
  const checkpoint=await readFile(join(f.cacheDir,result.transfer_id+'.json'),'utf8')
  assert.ok(!checkpoint.includes('Authorization'))
})

test('one local_import handles a Chinese image and an archive/PDF without manual project creation',async t => {
  const f=await fixture(t)
  for (const [name,key] of [['单页.png','images'],['中文漫画.cbz','asset_id'],['漫画.pdf','asset_id']]) {
    const path=join(f.root,name);await writeFile(path,'streamed-file')
    const result=await f.make().start({path,target_language:'en',operation_id:name})
    assert.equal(result.status,'COMPLETED',JSON.stringify(result))
    assert.equal(result.result.project_name,name.replace(/\.[^.]+$/,''))
    const args=f.submissions.at(-1)
    assert.ok(args[key]);assert.equal(args[key === 'images' ? 'asset_id' : 'images'],undefined)
    const repeated=await f.make().start({path,target_language:'en',operation_id:name})
    assert.equal(repeated.result.project_id,result.result.project_id)
  }
  assert.equal(f.submissions.length,3);assert.equal(f.uploads.length,3)
})

test('unsupported local files are rejected without uploads or project creation',async t => {
  const f=await fixture(t),path=join(f.root,'not-a-comic.txt')
  await writeFile(path,'data')
  await assert.rejects(f.make().start({path,operation_id:'unsupported'}),{code:'unsupported_file'})
  assert.equal(f.uploads.length,0);assert.equal(f.submissions.length,0)
})

test('recursive scan excludes links/metadata/archives, respects nonrecursive and byte limits',async t => {
  const f=await fixture(t)
  await mkdir(join(f.comic,'第2章'))
  await mkdir(join(f.comic,'__MACOSX'))
  await writeFile(join(f.comic,'2.PNG'),'abc')
  await writeFile(join(f.comic,'第2章','10.webp'),'abc')
  await writeFile(join(f.comic,'__MACOSX','1.png'),'abc')
  try {await symlink(join(f.comic,'第2章'),join(f.comic,'link'),process.platform === 'win32' ? 'junction' : 'dir')}
  catch (error) {if (!['EPERM','EACCES'].includes(error.code)) throw error}
  assert.equal((await scanDirectory(f.comic,true,limits)).files.length,2)
  assert.equal((await scanDirectory(f.comic,false,limits)).files.length,1)
  await assert.rejects(scanDirectory(f.comic,true,{...limits,max_pages:1}),{code:'import_limit'})
  await assert.rejects(scanDirectory(f.comic,true,{...limits,max_total_bytes:3}),{code:'import_limit'})
  await assert.rejects(scanDirectory('relative',true,limits),{code:'invalid_path'})
})

test('interrupted upload resumes completed assets using durable checkpoint',async t => {
  const f=await fixture(t)
  for (let i=0;i<3;i++) await writeFile(join(f.comic,`${i}.png`),'abcdef')
  let count=0
  const controller=new AbortController()
  f.intercept(async () => {if (++count > 1) {controller.abort();throw new Error('disconnected')}})
  const args={path:f.comic,operation_id:'interrupted'}
  const result=await f.make({signal:controller.signal}).start(args)
  assert.equal(result.status,'INTERRUPTED')
  assert.equal(f.submissions.length,0)
  f.intercept(async () => {})
  const resumed=await f.make().start(args)
  assert.equal(resumed.status,'COMPLETED',JSON.stringify(resumed))
  assert.equal(f.submissions.length,1)
  assert.equal(new Set(f.submissions[0].images.map(item => item.asset_id)).size,3)
})

test('source mutation during transfer never creates project',async t => {
  const f=await fixture(t)
  const path=join(f.comic,'1.png');await writeFile(path,'abcdef')
  f.intercept(async () => {await writeFile(path,'changed')})
  const result=await f.make().start({path:f.comic,operation_id:'mutated'})
  assert.equal(result.status,'FAILED')
  assert.equal(result.error.code,'source_changed')
  assert.equal(f.submissions.length,0)
})

test('lost submit response recovers original project, and cancellation reaches accepted job',async t => {
  const f=await fixture(t);await writeFile(join(f.comic,'1.png'),'abcdef')
  const manager=f.make(), original=manager.call
  manager.call=async (name,args) => {
    const result=await original(name,args)
    if (name === 'project_import') throw new Error('response lost')
    return result
  }
  const result=await manager.start({path:f.comic,operation_id:'lost-response'})
  assert.equal(result.status,'COMPLETED')
  assert.equal(f.submissions.length,1)
  const cancelled=await f.make().cancel(result.transfer_id)
  assert.equal(cancelled.status,'CANCELLED')
  assert.equal(cancelled.result.job.status,'CANCELLED')
})

test('running transfer is queryable and explicit cancellation prevents submission',async t => {
  const f=await fixture(t);await writeFile(join(f.comic,'1.png'),'abcdef')
  let release, entered
  const paused=new Promise(resolve => {release=resolve})
  const uploading=new Promise(resolve => {entered=resolve})
  f.intercept(async () => {entered();await paused})
  const manager=f.make(), args={path:f.comic,operation_id:'cancel-upload'}
  const result=await manager.start(args,1)
  assert.equal(result.status,'RUNNING')
  await uploading
  assert.equal((await manager.status(result.transfer_id)).total_files,1)
  const cancelled=await manager.cancel(result.transfer_id)
  release()
  await manager.active.get(result.transfer_id)?.finished
  assert.equal(cancelled.status,'CANCELLED')
  assert.equal(f.submissions.length,0)
  assert.equal((await f.make().start(args)).status,'CANCELLED')
})

test('two connector instances cannot upload/submit the same operation concurrently',async t => {
  const f=await fixture(t);await writeFile(join(f.comic,'1.png'),'abcdef')
  let release
  const paused=new Promise(resolve => {release=resolve})
  f.intercept(async () => {await paused})
  const first=f.make(), second=f.make(), args={path:f.comic,operation_id:'multi-agent'}
  const running=await first.start(args,1)
  await assert.rejects(second.start(args),error => error.code === 'transfer_busy' && error.transfer_id === running.transfer_id)
  const observed=await second.status(running.transfer_id)
  assert.equal(observed.running_elsewhere,true)
  assert.equal(observed.next_tool,'local_transfer_status')
  release();await first.active.get(running.transfer_id)?.finished
  assert.equal((await second.start(args)).status,'COMPLETED')
  assert.equal(f.submissions.length,1)
})

test('empty folders fail before upload or project creation',async t => {
  const f=await fixture(t)
  await assert.rejects(f.make().start({path:f.comic,operation_id:'empty'}),{code:'empty_directory'})
  assert.equal(f.uploads.length,0);assert.equal(f.submissions.length,0)
})

test('unknown server submission is inspected instead of uploading again',async t => {
  const f=await fixture(t);await writeFile(join(f.comic,'1.png'),'abcdef')
  const manager=f.make(), original=manager.call
  manager.call=async (name,args) => name === 'project_import'
    ? Promise.reject(new Error('response lost')) : original(name,args)
  const args={path:f.comic,operation_id:'unknown'}
  const failed=await manager.start(args)
  assert.equal(failed.status,'FAILED')
  f.jobs.set(args.operation_id,{found:false,operation:{status:'UNKNOWN'}})
  await assert.rejects(f.make().start(args),{code:'operation_requires_inspection'})
  assert.equal(f.uploads.length,1);assert.equal(f.submissions.length,0)
})

test('cancel during final verification never submits, even if the backend could finish immediately',async t => {
  const f=await fixture(t);await writeFile(join(f.comic,'1.png'),'abcdef')
  let entered, release, configs=0
  const waiting=new Promise(resolve => {entered=resolve}), gate=new Promise(resolve => {release=resolve})
  const manager=f.make(), original=manager.call
  manager.call=async (name,args) => {
    if (name === 'mcp_status' && ++configs === 2) {entered();await gate}
    return original(name,args)
  }
  const started=await manager.start({path:f.comic,operation_id:'cancel-verify'},1)
  await waiting
  const finished=manager.active.get(started.transfer_id).finished
  assert.equal((await manager.cancel(started.transfer_id)).status,'CANCELLED')
  release();await finished
  assert.equal((await manager.status(started.transfer_id)).status,'CANCELLED')
  assert.equal(f.submissions.length,0)
})

test('cancel an in-flight submission stays pending until the backend receipt is known',async t => {
  const f=await fixture(t);await writeFile(join(f.comic,'1.png'),'abcdef')
  let entered,release
  const waiting=new Promise(resolve => {entered=resolve}), gate=new Promise(resolve => {release=resolve})
  f.beforeSubmit(async () => {entered();await gate})
  const manager=f.make(), started=await manager.start({path:f.comic,operation_id:'cancel-submitting'},1)
  await waiting
  const finished=manager.active.get(started.transfer_id).finished
  assert.equal((await manager.cancel(started.transfer_id)).status,'CANCELLING')
  release();await finished
  assert.equal((await manager.status(started.transfer_id)).status,'CANCELLED')
  assert.equal(f.submissions.length,1)
})

test('fresh clients verify the original source before recovery and never return another comic',async t => {
  const f=await fixture(t);await writeFile(join(f.comic,'1.png'),'abcdef')
  const args={path:f.comic,target_language:'en',operation_id:'cross-client'}
  const original=await f.make().start(args)
  const recovered=await f.make({cacheDir:join(f.root,'second-state')}).start(args)
  assert.equal(recovered.result.project_id,original.result.project_id)
  await assert.rejects(f.make({cacheDir:join(f.root,'third-state')}).start({...args,target_language:'ja'}),{code:'conflict'})
  assert.equal(f.uploads.length,1);assert.equal(f.submissions.length,1)
})

test('initial network/configuration/scan time counts against the first response budget',async t => {
  const f=await fixture(t);await writeFile(join(f.comic,'1.png'),'abcdef')
  const manager=f.make(), original=manager.call
  let release
  const gate=new Promise(resolve => {release=resolve})
  manager.call=async (name,args) => {if (name === 'mcp_status') await gate;return original(name,args)}
  const result=await Promise.race([
    manager.start({path:f.comic,operation_id:'slow-init'},10),
    new Promise((_,reject) => {const timer=setTimeout(() => reject(new Error('Initialization blocked the response budget')),500);timer.unref()})
  ])
  assert.equal(result.status,'RUNNING');assert.ok(result.transfer_id)
  assert.equal((await manager.status(result.transfer_id)).stage,'INITIALIZING')
  release();await manager.active.get(result.transfer_id).finished
  assert.equal((await manager.status(result.transfer_id)).status,'COMPLETED')
})
