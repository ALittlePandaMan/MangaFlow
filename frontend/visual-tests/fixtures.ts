import {api} from '../src/services/api'
import type {ExecutionStatus, ImagePage, ModelConfiguration, ModelDescriptor, ProcessingTask, Project, TextRegion} from '../src/types'

// Synthetic data only. The gallery must never read or mutate a user's projects.
const date='2026-10-21T00:00:00Z'
const artwork='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="840"><rect width="600" height="840" fill="#e7ecf2"/><rect x="40" y="40" width="520" height="760" fill="#fff" stroke="#94a3b8" stroke-width="4"/><path d="M40 480H560M300 40V480" stroke="#94a3b8" stroke-width="4"/><ellipse cx="150" cy="170" rx="85" ry="100" fill="#f1f5f9"/><ellipse cx="420" cy="350" rx="95" ry="60" fill="#f1f5f9"/></svg>')
export const history={project_id:'audit',undo_count:2,redo_count:1,undo_id:2,redo_id:3,undo_label:'编辑文本',redo_label:'移动文本框'}
export const project:Project={id:'audit',name:'多语言排版检查 / Localization layout review / 日本語',description:'',source_language:'ja',target_language:'zh-CN',translation_context:{characters:{name:'Sample character'},glossary:{MangaFlow:'MangaFlow'}},settings:{},cover_url:artwork,page_count:3,created_at:date,updated_at:date}
export const pages:ImagePage[]=Array.from({length:5},(_,index)=>({id:`page-${index}`,project_id:project.id,filename:index===2?'very-long-original-page-file-name-for-localization-layout-check-日本語-繁體中文-简体中文.webp':`page-${index+1}.webp`,width:600,height:840,order_index:index,status:['OCR_DONE','TRANSLATED','FAILED','COMPLETED','UPLOADED'][index],current_stage:null,error_message:index===2?'Sample diagnostic: '+('LongUnbrokenErrorIdentifier'.repeat(5)):null,ocr_exempt:false,region_count:3,original_url:artwork,thumbnail_url:artwork,clean_url:artwork,rendered_url:artwork,text_layer_url:null,created_at:date,updated_at:date,editor_history:history}))
export const regions:TextRegion[]=Array.from({length:3},(_,index)=>({id:`region-${index}`,image_id:pages[0].id,region_key:`P0001-R${String(index+1).padStart(2,'0')}`,polygon:[[60,70+index*170],[240,70+index*170],[240,210+index*170],[60,210+index*170]],bbox:[60,70+index*170,180,140],translated_polygon:[[60,70+index*170],[240,70+index*170],[240,210+index*170],[60,210+index*170]],translated_bbox:[60,70+index*170,180,140],mask_url:null,source_text:'これはテスト用の原文です。',translated_text:index===2?'LongUnbrokenTranslation'.repeat(6):'这是一段用于检查布局的译文。',confidence:.98,orientation:index===1?'horizontal':'vertical',reading_order:index,panel_id:null,bubble_id:null,region_type:'bubble_simple',font_size:24+index,font_family:'Noto Sans SC',font_weight:400,text_color:'#111111',stroke_color:'#ffffff',stroke_width:1,alignment:'center',line_spacing:1.2,character_spacing:0,rotation:index===1?15:0,perspective_warp:false,opacity:1,locked:index===2,visible:true,needs_review:false,review_reasons:[],layout_warning:false,layout_data:index===0?{ocr_geometry_stale:true}:{},updated_at:date}))
const choices=[['detection','rfdetr'],['detection','opencv-fallback'],['ocr','paddleocr-vl-1.6'],['ocr','manga-ocr'],['ocr','baberu-ocr'],['ocr','hayai-ocr'],['ocr','tesseract'],['ocr','cloud-multimodal'],['inpainting','lama'],['inpainting','opencv'],['rendering','pillow'],['translation','openai-compatible'],['translation','google-web']]
export const available:ModelDescriptor[]=choices.map(([kind,name])=>({kind,name,description:name,devices:['cpu','cuda'],orientations:['vertical','horizontal'],supports_batch:true,capabilities:{},is_fallback:false,installed:true}))
export const configured:ModelConfiguration[]=available.filter(item=>['rfdetr','paddleocr-vl-1.6','lama','pillow','openai-compatible'].includes(item.name)).map(item=>({id:item.kind,kind:item.kind,name:item.name,provider:item.name,enabled:true,is_default:true,config:{device:'auto',base_url:'https://example.invalid/v1',model:'very-long-custom-model-name-for-localization-review',api_protocol:'auto',max_tokens:2048,text_box_padding:{top:3,bottom:3,left:3,right:3}},has_api_key:true,capabilities:{...item}}))
export const fonts=[{name:'Noto Sans SC',filename:'NotoSansSC-Regular.otf',path:'',url:''},{name:'CustomFontFamilyWithAVeryLongUnbrokenName',filename:'CustomFontWithAVeryLongUnbrokenFileName-繁體中文-日本語.ttf',path:'',url:''}]
export const tasks:ProcessingTask[]=Array.from({length:6},(_,index)=>({id:`task-${index}`,project_id:project.id,image_id:pages[index%pages.length].id,region_id:null,task_type:'pipeline',status:['RUNNING','QUEUED','PAUSED','CANCELLING','RUNNING','QUEUED'][index],progress:.35,current_stage:'translation',message:'',error_message:null,payload:{provider:'very-long-custom-model-name-for-localization-review'},pause_requested:index===2,created_at:date,updated_at:date,active_stages:index===4?['translation','inpainting']:['translation'],waiting_reason:index===1?'previous_page_translation':null}))
export const execution:ExecutionStatus={policy:{mode:'auto',max_pages:8,cpu_workers:8,memory_reserve_mb:1024,gpu_max_concurrency:1,gpu_memory_reserve_mb:2048},pages:{active:2,limit:4,maximum:8,peak:4,reason:'内存余量不足，降低并发'},cpu:{active:2,limit:4,maximum:8,peak:4,reason:'CPU 繁忙，降低并发'},models:{active:1,limit:1,reason:'单实例安全限制；独立双实例须通过吞吐、P95 和显存验收后才能启用',calls:100,wait_seconds:42.21,run_seconds:123.41},inference:[{kind:'ocr',name:'paddleocr-vl-1.6',backend:'torch-worker',device:'cpu',state:'ready',fallback_reason:'cuda_memory_reserve'}],resources:{known:true,available_bytes:10*1024**3,total_bytes:32*1024**3,cpu_percent:75,cpu_cores:16,rss_bytes:3*1024**3},native_threads:2,recent_stages:[],stage_timings:[{stage:'translation',samples:20,mean_seconds:12,p95_seconds:15}],gpu:{known:true,devices:[{index:0,name:'Sample GPU with a long hardware name',total_bytes:16*1024**3,used_bytes:10*1024**3,free_bytes:6*1024**3,utilization_percent:80}]}}
const pending=()=>new Promise<never>(()=>{})
export function installAuditApi(scenario:string) {
  const clone=<T,>(value:T):Promise<T>=>Promise.resolve(structuredClone(value))
  const reject=()=>Promise.reject(new Error('A long synthetic diagnostic: '+('LongUnbrokenDiagnosticCode'.repeat(4))))
  api.models.setupStatus=scenario==='setup-loading'?pending:scenario==='setup-error'?reject:()=>clone({ready:scenario!=='setup',first_run:scenario==='setup',validated:true,stages:['detection','ocr','translation','inpainting','rendering','font'].map(kind=>({kind,provider:null,status:'missing' as const,message:'等待配置'}))})
  api.preferences.get=()=>clone({shortcuts:{}})
  api.preferences.update=async shortcuts=>({shortcuts})
  api.preferences.updateTextStyle=async text_style=>({shortcuts:{},text_style})
  api.models.list=scenario==='settings-loading'?pending:scenario==='settings-error'?reject:()=>clone({available,configured})
  api.models.deviceProfile=()=>clone({platform:{system:'Sample OS',architecture:'x64'},cpu:{logical_cores:16,memory_gb:32},gpu:{available:true,devices:[{index:0,name:'Sample GPU',memory_gb:16,compute_capability:'8.0'}]},runtimes:{torch:{installed:true,cuda_available:true},paddle:{installed:true,cuda_available:true}},recommendation:{profile:'gpu',provider_devices:{detection:'cuda:0',ocr:'cuda:0',inpainting:'cuda:0'},summary:'Sample hardware'}})
  api.models.discover=()=>clone({models:['short-model','very-long-custom-model-name-for-localization-review'],endpoint:'https://example.invalid/v1/models',base_url:'https://example.invalid/v1',protocol:'openai'})
  api.models.bootstrap=reject
  api.models.update=async(id,payload)=>({...configured.find(item=>item.id===id)!,...payload})
  api.models.create=reject
  api.fonts.list=()=>clone(fonts)
  api.fonts.remove=async()=>undefined
  api.fonts.upload=reject
  api.projects.list=scenario==='projects-loading'?pending:scenario==='projects-error'?reject:()=>clone(scenario==='projects-empty'?[]:[project,{...project,id:'audit2',name:'LongUnbrokenProjectTitle'.repeat(10)},{...project,id:'audit3',name:'短いタイトル'}])
  api.projects.get=scenario==='editor-error'?reject:scenario==='editor-loading'?pending:()=>clone(project)
  api.projects.images=scenario==='editor-error'?reject:()=>clone(scenario==='editor-empty'?[]:pages)
  api.projects.fonts=()=>clone(fonts)
  api.projects.create=async payload=>({...project,...payload})
  api.projects.update=async(_id,payload)=>({...project,...payload})
  api.projects.remove=async()=>undefined
  api.projects.batch=()=>clone(tasks)
  api.projects.export=reject
  api.images.get=scenario==='editor-error'?reject:async id=>structuredClone(pages.find(page=>page.id===id)||pages[0])
  api.images.regions=scenario==='editor-error'?reject:()=>clone(regions)
  api.images.process=()=>clone(tasks[0])
  api.images.setOcrExempt=async(id,exempt)=>({...pages.find(page=>page.id===id)!,ocr_exempt:exempt})
  api.images.remove=async()=>undefined
  api.tasks.list=()=>clone(scenario==='editor-busy'?tasks:[])
  api.tasks.statuses=()=>clone(tasks)
  api.tasks.cancel=async id=>({...tasks.find(task=>task.id===id)!,status:'CANCELLED'})
  api.execution.get=()=>clone(execution)
  api.execution.update=async policy=>({...execution,policy})
  api.history.state=()=>clone(history)
  api.regions.update=async(id,patch)=>({...regions.find(region=>region.id===id)!,...patch})
  api.regions.updateMany=async changes=>changes.map(({id,patch})=>({...regions.find(region=>region.id===id)!,...patch}))
  api.regions.maskHistory=()=>clone({region_updated_at:date,history,undo:[],redo:[]})
  // A quiet, in-memory socket: no connections to a real task service.
  class AuditSocket extends EventTarget {
    static OPEN=1; static CLOSED=3; readyState=1; OPEN=1; CLOSED=3
    onopen:((event:Event)=>void)|null=null
    onmessage:((event:MessageEvent)=>void)|null=null
    onerror:((event:Event)=>void)|null=null
    onclose:((event:Event)=>void)|null=null
    constructor() {super();queueMicrotask(()=>this.onopen?.(new Event('open')))}
    send() {} close() {this.readyState=3}
  }
  window.WebSocket=AuditSocket as unknown as typeof WebSocket
}
