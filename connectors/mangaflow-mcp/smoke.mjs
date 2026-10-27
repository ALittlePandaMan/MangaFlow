// Integration test only: credentials arrive over stdin, never command arguments.
import {Client} from '@modelcontextprotocol/sdk/client/index.js'
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js'
import {fileURLToPath} from 'node:url'
import {mkdtemp, mkdir, writeFile, readFile, rm, symlink} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {randomUUID} from 'node:crypto'

let input = ''
for await (const block of process.stdin) input += block
const {url, token = '', image_id, image_data, comic_data, page_data} = JSON.parse(input)
for (const mode of ['http', 'stdio']) {
  const linkRoot=mode === 'stdio' ? await mkdtemp(join(tmpdir(),'mangaflow-entry-test-')) : null
  if (linkRoot) await symlink(fileURLToPath(new URL('.',import.meta.url)),join(linkRoot,'connector'),process.platform === 'win32' ? 'junction' : 'dir')
  const client = new Client({name:'mangaflow-sdk-smoke',version:'1.0.0'})
  const transport = mode === 'http'
    ? new StreamableHTTPClientTransport(new URL(url),{requestInit:{headers:token ? {Authorization:`Bearer ${token}`} : {}}})
    : new StdioClientTransport({command:process.execPath,args:[join(linkRoot,'connector','index.mjs')],
      env:{...process.env,MANGAFLOW_MCP_URL:url,MANGAFLOW_MCP_TOKEN:token},stderr:'pipe'})
  try {
    await client.connect(transport)
    const tools = await client.listTools()
    if (tools.tools.length !== (mode === 'stdio' ? 24 : 20)) throw new Error('Incorrect comic tool allowlist')
    const connection=await client.callTool({name:'mcp_status',arguments:{}})
    const caps=connection.structuredContent.connection_capabilities
    if (caps.transport !== mode || caps.local_import !== (mode === 'stdio') || caps.client_image_viewing !== 'unknown') throw new Error('Incorrect connection capabilities')
    if (JSON.parse(connection.content[0].text).connection_capabilities.transport !== mode) throw new Error('Text capability drift')
    for (const invalid of [
      () => client.callTool({name:'__missing_tool__',arguments:{}}),
      () => client.listTools({cursor:'invalid'}),
      () => client.listPrompts({cursor:'invalid'}),
    ]) {
      let rejected=false
      try {await invalid()} catch (error) {
        if (error.code !== -32602) throw error
        rejected=true
      }
      if (!rejected) throw new Error('Expected a standard InvalidParams protocol error')
    }
    const response = await client.callTool({name:'workflow_get',arguments:{}})
    if (response.isError || !response.structuredContent?.instructions) throw new Error('Missing workflow response')
    const recipe = await client.callTool({name:'workflow_get',arguments:{workflow_id:'review_and_fix'}})
    const prompt = await client.getPrompt({name:'review_and_fix'})
    if (prompt.messages[0].content.text !== recipe.structuredContent.instructions) throw new Error('Prompt/recipe drift')
    if (mode === 'stdio' && !tools.tools.some(tool => tool.name === 'local_import')) throw new Error('Missing upload tool')
    const image = await client.callTool({name:'page_preview',arguments:{image_id,kind:'original'}})
    if (image.isError || !image.content.some(block => block.type === 'image' && block.mimeType === 'image/png')) throw new Error('Missing native image evidence')
    const current=await client.callTool({name:'page_state',arguments:{image_id}})
    const plan=await client.callTool({name:'page_review_plan',arguments:{image_id,phase:'preflight',expected_version:current.structuredContent.version}})
    if (plan.isError || plan.structuredContent.requests.length !== 1 || plan.structuredContent.requests[0].arguments.kind !== 'annotated' || plan.structuredContent.visual_review_performed) throw new Error('Missing compact read-only image plan')
    const evidence_ids=[]
    for (const request of plan.structuredContent.requests) {
      const evidence=await client.callTool({name:request.tool,arguments:request.arguments})
      if (evidence.isError || !evidence.content.some(block => block.type === 'image')) throw new Error('Planned native image failed')
      evidence_ids.push(evidence.structuredContent.preview_id)
    }
    const ready=await client.callTool({name:'page_review_plan',arguments:{image_id,phase:'preflight',expected_version:current.structuredContent.version,evidence_ids}})
    if (!ready.structuredContent.evidence_ready || ready.structuredContent.visual_review_performed) throw new Error('Evidence planning confused with visual judgment')
    const started=await client.callTool({name:'workflow_start',arguments:{project_id:current.structuredContent.page.project_id,
      image_ids:[image_id],workflow_id:'review_and_fix',operation_id:randomUUID()}})
    if (started.isError) throw new Error('Workflow start failed')
    const run_id=started.structuredContent.run_id
    const checks=Object.fromEntries(['geometry','layout','false_detection','missing_text','ocr','translation','inpainting'].map(key => [key,{status:'skipped',reason:'Protocol test has no visual reasoning model'}]))
    const review=await client.callTool({name:'workflow_review_page',arguments:{run_id,image_id,phase:'final',outcome:'unverified',
      checks,note:'Protocol test only; visual review not performed',evidence_ids:[],
      expected_versions:current.structuredContent.expected_versions,operation_id:randomUUID()}})
    if (review.isError) throw new Error('Workflow review receipt failed')
    const exported=await client.callTool({name:'workflow_export',arguments:{run_id,operation_id:randomUUID()}})
    if (exported.isError) throw new Error('Workflow export failed')
    const finished=await client.callTool({name:'task_wait',arguments:{task_id:exported.structuredContent.job.id,kind:'file',seconds:20}})
    if (finished.isError || finished.structuredContent.status !== 'COMPLETED') throw new Error('Workflow export did not complete')
    if (mode === 'stdio') {
      const directory=await mkdtemp(join(tmpdir(),'mangaflow-mcp-'))
      try {
        const source=join(directory,'漫画.png'), destination=join(directory,'preview.png')
        await writeFile(source,Buffer.from(image_data,'base64'))
        const uploaded=await client.callTool({name:'local_import',arguments:{path:source,operation_id:randomUUID()}})
        if (uploaded.isError || !uploaded.structuredContent?.result?.job) throw new Error('Local file upload failed')
        const downloaded=await client.callTool({name:'local_download',arguments:{download_path:image.structuredContent.download_path,destination}})
        if (downloaded.isError || (await readFile(destination)).subarray(1,4).toString() !== 'PNG') throw new Error('Preview download failed')
        const protectedFile=await client.callTool({name:'local_download',arguments:{download_path:image.structuredContent.download_path,destination}})
        if (!protectedFile.isError) throw new Error('Existing destination was overwritten')
        const reportPath=join(directory,'review.json')
        const report=await client.callTool({name:'local_download',arguments:{download_path:exported.structuredContent.report_downloads.json,destination:reportPath}})
        if (report.isError || JSON.parse(await readFile(reportPath,'utf8')).fully_verified !== false) throw new Error('Review report download or unverified marker failed')
        const call=async (name,args={}) => {
          const result=await client.callTool({name,arguments:args})
          if (result.isError) throw new Error(`${name}: ${JSON.stringify(result.structuredContent || result.content)}`)
          return result.structuredContent
        }
        const wait=async (task_id,kind) => {
          const result=await call('task_wait',{task_id,kind,seconds:20})
          if (result.status !== 'COMPLETED') throw new Error(`Task did not complete: ${JSON.stringify(result)}`)
        }
        const comicPath=join(directory,'中文漫画.cbz')
        await writeFile(comicPath,Buffer.from(comic_data,'base64'))
        const imported=await call('local_import',{path:comicPath,target_language:'en',operation_id:randomUUID()})
        await wait(imported.result.job.id,'file')
        const archivePages=(await call('project_list',{project_id:imported.result.project_id,limit:50})).pages
        if (archivePages.length !== 2 || !archivePages[0].filename.endsWith('2.png') || !archivePages[1].filename.endsWith('10.png')) throw new Error('Comic import lost natural page order')
        const folder=join(directory,'漫画 インティメイト')
        await mkdir(folder)
        for (let number=11;number>0;number--) await writeFile(join(folder,`${number}.png`),Buffer.from(page_data,'base64'))
        const directoryArgs={path:folder,target_language:'en',operation_id:randomUUID()}
        const directoryDefinition=tools.tools.find(tool => tool.name === 'local_import')
        if (!directoryDefinition || recipe.structuredContent.version !== '2.2.0') throw new Error('Missing directory tool/workflow version')
        const directoryImport=await call('local_import',directoryArgs)
        if (directoryImport.status !== 'COMPLETED' || !directoryImport.result?.job) throw new Error(`Directory import failed: ${JSON.stringify(directoryImport)}`)
        await wait(directoryImport.result.job.id,'file')
        const project={id:directoryImport.result.project_id}
        const repeated=await call('local_import',directoryArgs)
        if (repeated.result.project_id !== project.id) throw new Error('Directory retry created another project')
        const projectState=await call('project_list',{project_id:project.id,limit:50})
        if (projectState.name !== '漫画 インティメイト' || projectState.target_language !== 'en') throw new Error('Folder project name/language lost')
        const pages=(await call('project_list',{project_id:project.id,limit:50})).pages
        if (pages.length !== 11 || pages[1].filename !== '2.png' || pages[9].filename !== '10.png') throw new Error('Directory natural order/page count incorrect')
        const run=await call('workflow_start',{project_id:project.id,operation_id:randomUUID()})
        const collect=async (page,phase) => {
          const state=await call('page_state',{image_id:page.id})
          const evidence_ids=[]
          const args={image_id:page.id,phase,expected_version:state.version}
          for (let round=0;round<6;round++) {
            let plan=await call('page_review_plan',{...args,evidence_ids})
            if (plan.blockers.length) throw new Error('Unexpected image-plan blockers')
            if (plan.evidence_ready) return {state,evidence_ids}
            const batch=[...plan.requests]
            while (plan.next_offset !== null) {
              plan=await call('page_review_plan',{...args,evidence_ids,offset:plan.next_offset})
              batch.push(...plan.requests)
            }
            for (const request of batch) {
              const preview=await client.callTool({name:request.tool,arguments:request.arguments})
              if (preview.isError || !preview.content.some(block => block.type === 'image')) throw new Error('Planned native comic image missing')
              evidence_ids.push(preview.structuredContent.preview_id)
            }
          }
          throw new Error('Image plan did not converge')
        }
        const processBatch=async () => {
          const status=await call('workflow_status',{run_id:run.run_id})
          const next=status.next_steps[0]
          if (next.tool !== 'workflow_advance') throw new Error(`Incorrect processing advice: ${JSON.stringify(next)}`)
          if (next.arguments.image_ids.length !== 11) throw new Error('Expected one whole-comic batch')
          const response=await call(next.tool,{...next.arguments,operation_id:randomUUID()})
          for (const task of response.tasks) await wait(task.id,'processing')
        }
        await processBatch() // Entire comic: detection -> OCR -> translation; no erasure.
        for (const page of pages) {
          const before=await collect(page,'preflight')
          const preChecks=Object.fromEntries(['geometry','false_detection','missing_text','ocr','translation'].map(key => [key,
            {status:'pass',reason:'Simulated approval of deterministic fixture, not real visual reasoning'}]))
          await call('workflow_review_page',{run_id:run.run_id,image_id:page.id,phase:'preflight',outcome:'passed',
            checks:preChecks,note:'Deterministic test approval only; final report remains visually unverified',evidence_ids:before.evidence_ids,
            selection:{mode:'all_eligible'},expected_versions:before.state.expected_versions,operation_id:randomUUID()})
        }
        await processBatch() // Entire approved comic: mask -> inpainting -> rendering.
        for (const page of pages) {
          const after=await collect(page,'final')
          if (after.state.regions[0].translated_text !== 'Hello') throw new Error('Translation stages were not executed')
          await call('workflow_review_page',{run_id:run.run_id,image_id:page.id,phase:'final',outcome:'unverified',
            checks,note:'Protocol harness fetched images; model visual review not performed',evidence_ids:after.evidence_ids,
            expected_versions:after.state.expected_versions,operation_id:randomUUID()})
        }
        const delivery=(await call('workflow_status',{run_id:run.run_id,limit:50})).next_steps[0]
        if (delivery.tool !== 'workflow_export') throw new Error('Missing guarded delivery advice')
        const output=await call(delivery.tool,{...delivery.arguments,operation_id:randomUUID()})
        await wait(output.job.id,'file')
        const outputPath=join(directory,'译本.cbz')
        await call('local_download',{download_path:`/mangaflow/download/${output.job.id}`,destination:outputPath})
        if ((await readFile(outputPath)).subarray(0,2).toString() !== 'PK') throw new Error('Comic download was not an archive')
        const nativeReport=join(directory,'native-review.json')
        await call('local_download',{download_path:output.report_downloads.json,destination:nativeReport})
        const receipt=JSON.parse(await readFile(nativeReport,'utf8'))
        if (receipt.fully_verified || receipt.pages.length !== 11) throw new Error('Protocol-only run claimed visual quality')
        console.log('stdio: directory 11-page auto-create/import/translate/review/export passed')
        console.log('stdio: native comic import/detect/ocr/translate/mask/inpaint/render/review/export/download passed')
      } finally {await rm(directory,{recursive:true,force:true})}
    }
    // A second independent SDK/connector session competes for the SAME page.
    const peer = new Client({name:'mangaflow-sdk-worker',version:'1.0.0'})
    const peerTransport = mode === 'http'
      ? new StreamableHTTPClientTransport(new URL(url),{requestInit:{headers:token ? {Authorization:`Bearer ${token}`} : {}}})
      : new StdioClientTransport({command:process.execPath,args:[join(linkRoot,'connector','index.mjs')],
          env:{...process.env,MANGAFLOW_MCP_URL:url,MANGAFLOW_MCP_TOKEN:token},stderr:'pipe'})
    try {
      await peer.connect(peerTransport)
      const current = (await client.callTool({name:'page_state',arguments:{image_id}})).structuredContent
      const parallel = (await client.callTool({name:'workflow_start',arguments:{project_id:current.page.project_id,
        image_ids:[image_id],coordination_mode:'parallel',operation_id:randomUUID()}})).structuredContent
      if (!parallel.coordinator_token) throw new Error('Missing coordinator receipt')
      const attempts = await Promise.all([client,peer].map((session,index)=>session.callTool({name:'workflow_advance',arguments:{
        run_id:parallel.run_id,action:'claim',worker_id:`native-${index}`,operation_id:randomUUID()}})))
      if (attempts.some(result=>result.isError)) throw new Error('Native worker claim failed')
      const winners = attempts.map((result,index)=>({lease:result.structuredContent,index})).filter(item=>item.lease.assignment)
      if (winners.length !== 1) throw new Error('Parallel workers duplicated ownership')
      const winner = winners[0], session = [client,peer][winner.index]
      const lease = winner.lease, worker_id = lease.assignment.worker_id, assignment_token = lease.assignment.token
      const imageResult = await session.callTool({name:'page_preview',arguments:{image_id,kind:'original',expected_version:lease.version}})
      if (imageResult.isError || !imageResult.content.some(block=>block.type==='image')) throw new Error('Worker cannot receive native image')
      const state = (await session.callTool({name:'workflow_status',arguments:{run_id:parallel.run_id,worker_id,assignment_token}})).structuredContent
      if (state.pages.length !== 1 || state.pages[0].image_id !== image_id) throw new Error('Worker filtering failed')
      const accepted = await session.callTool({name:'workflow_review_page',arguments:{run_id:parallel.run_id,image_id,
        phase:'final',outcome:'unverified',checks,note:'Independent SDK workers received images; no multimodal model judgment in this harness',
        evidence_ids:[imageResult.structuredContent.preview_id],worker_id,assignment_token,
        expected_versions:{[image_id]:lease.version},operation_id:randomUUID()}})
      if (accepted.isError) throw new Error('Native parallel review failed')
      const blocked = await peer.callTool({name:'workflow_export',arguments:{run_id:parallel.run_id,operation_id:randomUUID()}})
      if (!blocked.isError) throw new Error('Worker export bypassed coordinator credential')
      console.log(`${mode}: independent workers, exclusive claim, native image, receipt and coordinator guard passed`)
      const projectUrl=new URL(`/api/projects/${current.page.project_id}`,url)
      const originalProject=await (await fetch(projectUrl)).json()
      const changed=await fetch(projectUrl,{method:'PATCH',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target_language:originalProject.target_language === 'fr' ? 'de' : 'fr'})})
      if (!changed.ok) throw new Error('Fixture configuration update failed')
      const stale=(await client.callTool({name:'workflow_status',arguments:{run_id:parallel.run_id}})).structuredContent
      const recovery=stale.next_steps[0]
      if (!stale.configuration_stale || recovery.arguments.action !== 'accept_configuration') throw new Error('Missing configuration recovery call')
      const resumed=await client.callTool({name:recovery.tool,arguments:{...recovery.arguments,
        coordinator_token:parallel.coordinator_token,operation_id:randomUUID()}})
      if (resumed.isError || !resumed.structuredContent.reviews_invalidated) throw new Error('Configuration recovery failed')
      const reclaimed=(await session.callTool({name:'workflow_advance',arguments:{run_id:parallel.run_id,
        action:'claim',worker_id,operation_id:randomUUID()}})).structuredContent
      const checked=await session.callTool({name:'workflow_review_page',arguments:{run_id:parallel.run_id,image_id,
        phase:'final',outcome:'unverified',checks,note:'Protocol-only configuration recovery; visual quality unverified',
        worker_id,assignment_token:reclaimed.assignment.token,expected_versions:reclaimed.expected_versions,operation_id:randomUUID()}})
      if (checked.isError) throw new Error('Review after configuration recovery failed')
      console.log(`${mode}: configuration change, same-run recovery and renewed review passed`)
    } finally {await peer.close()}
    console.log(`${mode}: initialize, tools/list (${tools.tools.length}), tools/call and structured results passed`)
  } finally {await client.close();if (linkRoot) await rm(linkRoot,{recursive:true,force:true})}
}
