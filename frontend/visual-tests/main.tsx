import {useEffect, useMemo, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import App from '../src/App'
import {GlobalDialogProvider, useGlobalDialog} from '../src/components/GlobalDialog'
import {ActivitySpinner, BlockingLoader, ButtonLoading, CircularProgress, PageLoader, ProgressBar} from '../src/components/LoadingUI'
import {CanvasTaskList} from '../src/features/editor/components/CanvasTaskList'
import {useGlobalAlert} from '../src/components/GlobalAlert'
import {RegionProperties} from '../src/features/editor/components/RegionProperties'
import {MaskControls} from '../src/features/editor/components/MaskControls'
import {ImmersiveReader} from '../src/features/editor/components/ImmersiveReader'
import {TranslationContextDialog} from '../src/features/editor/components/TranslationContextDialog'
import {TaskActivity} from '../src/features/editor/lib/taskActivity'
import {useEditorStore} from '../src/features/editor/store'
import {changeLocale, normalizeLocale, t} from '../src/i18n'
import {buttonClass} from '../src/ui'
import {applyTheme} from '../src/theme'
import {fonts, installAuditApi, pages, regions, tasks} from './fixtures'
import '../src/tailwind.css'
import './gallery.css'

const params=new URLSearchParams(location.search)
const screen=params.get('screen')||'projects'
const variant=params.get('state')||'normal'
changeLocale(normalizeLocale(params.get('locale'))||'zh-CN')
applyTheme(params.get('theme')==='dark'?'dark':'light',false)
installAuditApi(screen)
useEditorStore.getState().setView('original')
const noop=()=>{}
function ContextPreview() {
  const [text,setText]=useState('{"characters":{"name":"Sample"},"glossary":{}}')
  const [mode,setMode]=useState<'snapshot'|'previous_page'>(variant==='previous'?'previous_page':'snapshot')
  const [error,setError]=useState(variant==='error'?'LongUnbrokenValidationDiagnostic'.repeat(4):'')
  return <TranslationContextDialog text={text} mode={mode} error={error} saving={variant==='busy'} onTextChange={setText} onModeChange={setMode} onError={setError} onClose={noop} onSave={noop}/>
}
function PromptPreview() {
  const {confirm,openDialog}=useGlobalDialog()
  useEffect(()=>{
    if(variant==='content') openDialog({title:t('编辑项目'),description:t('选择应用界面使用的语言。'),content:<div style={{padding:24}}><button className={buttonClass}>{t('保存修改')}</button></div>})
    else void confirm({title:t('删除项目？'),message:t('“{{v0}}”及其全部页面、区域和工程文件将被永久删除。此操作无法撤销。',{v0:'LongUnbrokenProjectTitle'.repeat(variant==='long'?40:8)}),confirmLabel:t('删除项目')})
  },[confirm,openDialog])
  return null
}
function ActivityPreview() {
  const {alert} = useGlobalAlert()
  useEffect(() => {alert({message:t('无法读取设置：')+'LongUnbrokenDiagnostic'.repeat(5),tone:'danger'})}, [alert])
  const activity=useMemo(()=>{const value=new TaskActivity('audit');value.begin(variant==='failed'?tasks.map(task=>({...task,status:'FAILED',error_message:'LongUnbrokenProviderDiagnostic'.repeat(5)})):tasks);value.setConnectionIssue(true);return value},[])
  return <div style={{height:'100%',padding:12,overflow:'auto'}}><CanvasTaskList activity={activity} pages={pages} onCancelTask={async()=>{}}/></div>
}
function ToastPreview() {
  const {alert} = useGlobalAlert()
  return <div className="flex flex-wrap gap-2 p-4">
    <button className={buttonClass} onClick={() => alert({message:t('配置已安装'),tone:'success'})}>Success toast</button>
    <button className={buttonClass} onClick={() => alert({message:'First line\nSecond line\nThird line',tone:'warning'})}>Multiline toast</button>
  </div>
}
function InspectorPreview() {
  const [items,setItems]=useState(regions)
  const selected=variant==='empty'?[]:variant==='multiple'?items:items.slice(0,1)
  const width=Number(params.get('panel')||320)
  const updateMany=(changes:Array<{id:string;patch:Partial<typeof regions[number]>}>)=>setItems(previous=>previous.map(item=>({...item,...changes.find(change=>change.id===item.id)?.patch})))
  return <div className="audit-inspector editor-properties" style={{width:`min(100%,${width}px)`,height:'100%',container:'editor-properties / size'}}><RegionProperties page={pages[0]} regions={items} region={selected[0]} selectedRegions={selected} selectedCount={selected.length} fontOptions={fonts.map(font=>font.name)} busyAction={variant==='busy'?'translation':null} onUpdate={(id,patch)=>updateMany([{id,patch}])} onUpdateMany={updateMany} onAction={noop}/></div>
}
function MaskPreview() {
  const [tool,setTool]=useState<'brush'|'eraser'|'select'|'pan'|'rectangle'|'polygon'|'lasso'>('brush')
  const [size,setSize]=useState(24),[opacity,setOpacity]=useState(30),[reference,setReference]=useState(true)
  return <div style={{width:'min(100%,320px)',height:'100%'}}><MaskControls region={regions[0]} regions={regions} tool={tool} operation="brush" diameter={size} referenceOpacity={opacity} showOriginal={reference} disabled={variant==='busy'} onRegionChange={noop} onToolChange={setTool} onDiameterChange={setSize} onReferenceOpacityChange={setOpacity} onReferenceChange={setReference}/></div>
}
function Gallery() {
  if(screen==='inspector')return <InspectorPreview/>
  if(screen==='mask')return <MaskPreview/>
  if(screen==='context')return <ContextPreview/>
  if(screen==='activity')return <ActivityPreview/>
  if(screen==='toast')return <ToastPreview/>
  if(screen==='prompt')return <PromptPreview/>
  if(screen==='reader')return <ImmersiveReader page={pages[0]} regions={regions} busy={variant==='busy'} error={variant==='error'?'LongUnbrokenReaderDiagnostic'.repeat(4):''} onPreviousPage={noop} onNextPage={noop} onClose={noop}/>
  if(screen==='blocking')return <BlockingLoader label={t('正在清除文字并修复背景…')} progress={variant==='unknown'?null:45} scope={{kind:'regions',filename:pages[2].filename,regionKeys:regions.map(region=>region.region_key)}}/>
  if(screen==='loading')return <div className="h-full"><PageLoader/><div className="fixed bottom-8 left-4 right-4"><ProgressBar value={45} label={t('正在生成译文排版…')}/><ButtonLoading label={t('正在安装模型…')}/><ActivitySpinner/><CircularProgress value={40}/></div></div>
  return <MemoryRouter initialEntries={[screen==='editor-empty'?'/projects/audit/editor':screen.startsWith('editor')?'/projects/audit/editor/page-0':screen.startsWith('settings')?'/settings':'/projects']}><App/></MemoryRouter>
}
document.documentElement.dataset.auditScreen=screen
document.documentElement.dataset.auditState=variant
createRoot(document.getElementById('root')!).render(<GlobalDialogProvider><Gallery/></GlobalDialogProvider>)
