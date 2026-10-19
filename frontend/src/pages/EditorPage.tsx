import {EditorToolbarFeedback} from '../features/editor/components/EditorToolbarFeedback'
import {PageListItem} from '../features/editor/components/PageListItem'
import {VirtualPageList, VIRTUAL_PAGE_THRESHOLD} from '../features/editor/components/VirtualPageList'
import {blurActiveControl, pageTaskBusyLabel, pageNeedsOcr} from '../features/editor/lib/pagePresentation'
import {preserveLocalRegionGeometry, reconcileRegionSnapshot} from '../features/editor/lib/regionSnapshots'
import {type TaskUpdateHandler} from '../features/editor/hooks/useTaskMonitor'
import {useEditorTasks} from '../features/editor/hooks/useEditorTasks'
import {CanvasTaskList} from '../features/editor/components/CanvasTaskList'
import {EditorWorkspace} from '../features/editor/components/EditorWorkspace'
import {ClassificationStatus} from '../features/editor/components/ClassificationStatus'
import {EditorHeaderActions} from '../features/editor/components/EditorHeaderActions'
import {ImmersiveReader} from '../features/editor/components/ImmersiveReader'
import { Archive, BookOpenText, CheckCircle2, ChevronDown, Download, Eraser, Image as ImageIcon, ImagePlus, Languages, Layers3, Maximize, RotateCcw, ScanText, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, SetStateAction } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAppHeaderSlots } from '../components/AppShell'
import { useGlobalDialog } from '../components/GlobalDialog'
import { BlockingLoader, ButtonLoading, EditorSkeleton, useMinimumLoadingTime, type ProcessingScope } from '../components/LoadingUI'
import { DEFAULT_FONT_FAMILIES } from '../constants/fonts'
import {notifyFontsChanged} from '../features/editor/lib/fontRevision'
import { CanvasZoomControls, EditorToolbar } from '../features/editor/components/EditorToolbar'
import { MangaCanvas } from '../features/editor/components/MangaCanvas'
import { RegionProperties } from '../features/editor/components/RegionProperties'
import {MaskEditor, type MaskEditorHandle, type MaskEditorPreferences, type MaskEditorState} from '../features/editor/components/MaskEditor'
import {prepareMaskScene, type PreparedMaskScene} from '../features/editor/lib/prepareMaskScene'
import {draftRegion, moveRegions, constrainGroupMove} from '../features/editor/lib/regionEditing'
import {invalidateHistoryPreview} from '../features/editor/lib/regionHistory'
import {RegionCommandQueue,type RegionCommand} from '../features/editor/lib/regionCommandQueue'
import {preloadImage} from '../features/editor/hooks/useImage'
import {useSelectedPageScroll} from '../features/editor/hooks/useSelectedPageScroll'
import {useEditorHistory} from '../features/editor/hooks/useEditorHistory'
import {canRunPageWorkflowStage, type PageWorkflowStage} from '../features/editor/pageWorkflow'
import {createPageWorkflowCommands} from '../features/editor/lib/pageWorkflowCommands'
import { useEditorStore } from '../features/editor/store'
import {formatShortcut, shortcutForEvent, shortcutToAria, type ShortcutId, useShortcutStore} from '../features/shortcuts/store'
import { api } from '../services/api'
import {ImageUploadError, type ImageUploadProgress} from '../services/imageUpload'
import type { EditorHistoryState, FontResource, ImagePage, ProcessingTask, Project, RegionStageAction, TextRegion, ViewMode } from '../types'
import {buttonClass, cn, eyebrowClass, iconButtonClass, primaryButtonClass, scrollbarClass, textareaClass} from '../ui'

type PendingChange = {pageId: string, before: TextRegion, patch: Partial<TextRegion>, timer: number, version: number, preview:RegionCommand}
type PageContextMenu = {x: number, y: number, pageId: string, source?: 'canvas' | 'thumbnail'}
type RegionProcessing = {ids: string[], keys: string[], filename: string}
type RegionCommandAction = RegionStageAction | 'merge' | 'delete'
type PagePress = {pageId: string, fromIndex: number, overIndex: number, pointerId: number, startX: number, startY: number, active: boolean}
type PageDrag = {pageId: string, fromIndex: number, overIndex: number}
type ExportKind = 'project' | 'translated' | 'clean'

const EXPORT_OPTIONS: Array<{kind: ExportKind, label: string, detail: string, formats: string[]}> = [
  {kind: 'project', label: '导出源项目', detail: '完整工程包，可再次导入 MangaFlow 继续编辑', formats: ['project']},
  {kind: 'translated', label: '导出翻译后图片', detail: '仅打包所有页面的最终译图', formats: ['translated']},
  {kind: 'clean', label: '导出净图', detail: '未修复的页面会自动使用原图', formats: ['clean']},
]

const PAGE_DRAG_START_THRESHOLD = 3

function viewNeedsCleanArtifact(view: ViewMode) {
  return view === 'clean' || view === 'translated'
}

function viewSourceLayers(view: ViewMode) {
  return {
    original: view === 'original',
    clean: viewNeedsCleanArtifact(view),
  }
}

function safePageView(page: ImagePage, currentView: ViewMode, restoreView?: ViewMode | null) {
  if (!page.clean_url && viewNeedsCleanArtifact(currentView)) return 'original'
  if (page.clean_url && currentView === 'original' && restoreView && viewNeedsCleanArtifact(restoreView)) return restoreView
  return currentView
}

function visiblePageSources(page: ImagePage, view: ViewMode, layers: {original: boolean, clean: boolean}) {
  const cleanSource = page.clean_url || page.original_url
  if (view === 'comparison') return [...new Set([page.original_url, cleanSource])]
  const sources: string[] = []
  if (layers.original) sources.push(page.original_url)
  if (layers.clean) sources.push(cleanSource)
  return [...new Set(sources)]
}

export function EditorPage() {
  const {projectId = '', imageId} = useParams()
  const navigate = useNavigate()
  const navigateRef = useRef(navigate)
  navigateRef.current = navigate
  const [searchParams] = useSearchParams()
  const [project, setProject] = useState<Project | null>(null)
  const [pages, setPages] = useState<ImagePage[]>([])
  const [page, setPage] = useState<ImagePage | null>(null)
  const pageRef=useRef<ImagePage | null>(null)
  const [regions, setRegions] = useState<TextRegion[]>([])
  const [fontResources, setFontResources] = useState<FontResource[]>([])
  const pagesRef = useRef<ImagePage[]>([])
  const regionsRef = useRef<TextRegion[]>([])
  const regionStateRevision = useRef(0)
  const pageLoadGeneration = useRef(0)
  const currentPageId = useRef<string | null>(null)
  const loadedProjectId = useRef<string | null>(null)
  const pending = useRef<Map<string, PendingChange>>(new Map())
  const regionEditVersions = useRef<Map<string, number>>(new Map())
  const editSequence = useRef(0)
  const [,setCommandRevision]=useState(0)
  const commandQueue=useRef<RegionCommandQueue | null>(null)
  if (!commandQueue.current) commandQueue.current=new RegionCommandQueue(()=>setCommandRevision(value=>value+1))
  const editQueue=commandQueue.current
  const [recoveringEdits,setRecoveringEdits]=useState(false)
  const recoveringEditsRef=useRef(false)
  const cachedScenes=useRef(new Map<string,{page:ImagePage,regions:TextRegion[]}>())
  const persistedPages=useRef(new Map<string,ImagePage>())
  const [waitingForEdits,setWaitingForEdits]=useState(false)
  const [hasPendingEdits,setHasPendingEdits] = useState(false)
  const [uploadingPages, setUploadingPages] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<ImageUploadProgress | null>(null)
  const uploadBusy = useRef(false)
  const uploadAbort = useRef<AbortController | null>(null)
  useEffect(() => {
    setUploadingPages(false)
    setUploadProgress(null)
    return () => {
      uploadAbort.current?.abort()
      uploadAbort.current = null
      uploadBusy.current = false
    }
  }, [projectId])
  const flushEditsRef = useRef<() => Promise<void>>(async () => undefined)
  useEffect(() => {
    if (!hasPendingEdits && !editQueue.pending && !uploadingPages) return
    const warn = (event: BeforeUnloadEvent) => {event.preventDefault();event.returnValue = ''}
    window.addEventListener('beforeunload',warn)
    return () => window.removeEventListener('beforeunload',warn)
  },[hasPendingEdits,editQueue.pending,uploadingPages])
  const [historyDirection,setHistoryDirection] = useState<'undo' | 'redo' | null>(null)
  const editorHistory = useEditorHistory(projectId)
  const historyRefreshRef = useRef(editorHistory.refresh)
  historyRefreshRef.current = editorHistory.refresh
  const [loading, setLoading] = useState(true)
  const [switchingPage, setSwitchingPage] = useState(false)
  const showingInitialLoading = useMinimumLoadingTime(loading, 420)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [runningRegionAction, setRunningRegionAction] = useState<RegionCommandAction | 'create' | 'maskRepair' | null>(null)
  const [regionProcessing, setRegionProcessing] = useState<RegionProcessing | null>(null)
  const [showContext, setShowContext] = useState(false)
  const [immersiveReading, setImmersiveReading] = useState(false)
  const closeImmersiveReader = useCallback(() => setImmersiveReading(false), [])
  const contextDialogRef = useRef<HTMLDivElement>(null)
  const [contextText, setContextText] = useState('{}')
  const [contextMode, setContextMode] = useState<'snapshot' | 'previous_page'>('snapshot')
  const [contextError, setContextError] = useState('')
  const [schedulingProcess, setSchedulingProcess] = useState(false)
  const [runningWorkflowStage, setRunningWorkflowStage] = useState<PageWorkflowStage | null>(null)
  const [runningPageTask, setRunningPageTaskState] = useState<ProcessingTask | null>(null)
  const [syncingTaskResult, setSyncingTaskResult] = useState(false)
  const pendingRecoveredRefresh = useRef(new Set<string>())
  const [schedulingBatch, setSchedulingBatch] = useState<'ocr' | 'repairTranslate' | null>(null)
  const batchBusy = useRef(false)
  const [batchProgress, setBatchProgress] = useState<number | null>(null)
  const [resettingPage, setResettingPage] = useState(false)
  const [deletingPageId, setDeletingPageId] = useState<string | null>(null)
  const [updatingPageRecognitionId, setUpdatingPageRecognitionId] = useState<string | null>(null)
  const [reorderingPages, setReorderingPages] = useState(false)
  const [reorderProgress, setReorderProgress] = useState<number | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportKind, setExportKind] = useState<ExportKind | null>(null)
  const [exportMenuOpen, setExportMenuOpen] = useState(false)
  const exportMenuRef = useRef<HTMLDivElement>(null)
  const [savingContext, setSavingContext] = useState(false)
  type MaskSession = {page: ImagePage, region: TextRegion | undefined, regions: TextRegion[], prepared?: PreparedMaskScene, version?: number}
  const [maskSession, setMaskSessionState] = useState<MaskSession | null>(null)
  const maskSessionRef = useRef<MaskSession | null>(null)
  const maskRepairBusy = useRef(false)
  const requestMaskLeaveRef = useRef<(leave: () => void, nextView?: ViewMode) => void>(() => undefined)
  const setMaskSession = useCallback((next: SetStateAction<MaskSession | null>) => {
    const resolved = typeof next === 'function' ? next(maskSessionRef.current) : next
    maskSessionRef.current = resolved
    setMaskSessionState(resolved)
  }, [])
  const maskHistoryVersion = useRef(0)
  const maskEditorRef = useRef<MaskEditorHandle>(null)
  const maskPreferences = useRef<MaskEditorPreferences>({diameter:24, referenceOpacity:30, showOriginal:true})
  const [maskControlsTarget, setMaskControlsTarget] = useState<HTMLDivElement | null>(null)
  const [maskState, setMaskState] = useState<MaskEditorState>({undo: false, redo: false, dirty: false, busy: false})
  const [openingMask, setOpeningMask] = useState(false)
  const [pageContextMenu, setPageContextMenu] = useState<PageContextMenu | null>(null)
  const [pageDrag, setPageDrag] = useState<PageDrag | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const pageListRef = useRef<HTMLDivElement>(null)
  const pageContextMenuRef = useRef<HTMLDivElement>(null)
  const pagePressRef = useRef<PagePress | null>(null)
  const suppressPageClick = useRef(false)
  const historyBusy = useRef(false)
  const deleteBusy = useRef(false)
  const regionActionBusy = useRef(false)
  const recoveredTaskUpdate = useRef<TaskUpdateHandler>(() => undefined)
  const recoveredTasksSettled = useRef<() => Promise<unknown>>(async () => undefined)
  const {generation:taskMonitorGeneration, waitForTask, waitForTasks, activity:taskActivity, recordTask, cancelTask}=useEditorTasks(projectId, task => recoveredTaskUpdate.current(task), () => recoveredTasksSettled.current())
  const setRunningPageTask = useCallback((task: ProcessingTask | null) => {
    recordTask(task)
    setRunningPageTaskState(task)
  }, [recordTask])
  const observedPageTask = useSyncExternalStore(taskActivity.subscribe, () => taskActivity.getPageTask(page?.id))
  const projectHasTasks = useSyncExternalStore(taskActivity.subscribe, () => {
    const state=taskActivity.getSnapshot()
    return state.active > 0 || state.recovering && state.total > 0
  })
  const taskSyncVersion = useRef('')
  const shortcutHandlerRef = useRef<(event: KeyboardEvent) => void>(() => undefined)
  const leaveGuardRef=useRef<(event:MouseEvent)=>void>(()=>undefined)
  useEffect(()=>{
    const guard=(event:MouseEvent)=>leaveGuardRef.current(event)
    document.addEventListener('click',guard,true)
    return ()=>document.removeEventListener('click',guard,true)
  },[])
  const routeImageIdRef = useRef(imageId)
  const routeRegionIdRef = useRef(searchParams.get('region'))
  routeImageIdRef.current = imageId
  routeRegionIdRef.current = searchParams.get('region')
  useEffect(() => {
    if (!showContext) return
    const previous = document.activeElement as HTMLElement | null
    const dialog = contextDialogRef.current
    dialog?.querySelector<HTMLButtonElement>('button')?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && dialog?.dataset.saving !== 'true') {
        event.preventDefault(); event.stopPropagation(); setShowContext(false)
      }
      if (event.key !== 'Tab') return
      const items = Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), textarea:not(:disabled)') || [])
      const first = items[0], last = items.at(-1)
      if (!first || !last) {event.preventDefault(); return}
      if (event.shiftKey && (document.activeElement === first || !dialog?.contains(document.activeElement))) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !dialog?.contains(document.activeElement))) {
        event.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown', keydown, true)
    return () => {document.removeEventListener('keydown', keydown, true); previous?.focus()}
  }, [showContext])

  const selectedIds = useEditorStore(state => state.selectedIds)
  const selectedPageId = imageId || page?.id
  useSelectedPageScroll(pageListRef, selectedPageId, !showingInitialLoading && pages.length <= VIRTUAL_PAGE_THRESHOLD)
  const select = useEditorStore(state => state.select)
  const setTool = useEditorStore(state => state.setTool)
  const setView = useEditorStore(state => state.setView)
  const tool = useEditorStore(state => state.tool)
  const maskOperation = useEditorStore(state => state.maskOperation)
  const view = useEditorStore(state => state.view)
  const layers = useEditorStore(state => state.layers)
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const {confirm: confirmDialog, isOpen: dialogOpen} = useGlobalDialog()
  const {editorTarget} = useAppHeaderSlots()
  // Attribute/geometry saves are serialized, but do not lock the editor.
  // Background inference is shown in the canvas task list, never a modal.
  // Keep structural writes blocking and protect edits against published results.
  const backgroundRegionAction = !!runningRegionAction && (['ocr','translate','inpaint','maskRepair'].includes(runningRegionAction) || !!runningPageTask)
  const foregroundBusy = recoveringEdits || waitingForEdits || resettingPage || uploadingPages || !!deletingPageId || !!updatingPageRecognitionId || reorderingPages || exporting || (!!runningRegionAction && !backgroundRegionAction) || !!historyDirection
  const blockingBusy = foregroundBusy || schedulingProcess || !!schedulingBatch || backgroundRegionAction || !!observedPageTask || projectHasTasks || syncingTaskResult
  const editorBusy = blockingBusy || editQueue.failed || switchingPage || openingMask || !!maskSession
  const editorBusyRef = useRef(editorBusy)
  editorBusyRef.current = editorBusy
  // Mask mode locks destructive workflows, not page navigation. Only an active
  // save, confirmation or page transition should prevent switching images.
  const pageNavigationBusy = foregroundBusy || maskRepairBusy.current || switchingPage || openingMask || maskState.busy
  const pageNavigationBusyRef = useRef(pageNavigationBusy)
  pageNavigationBusyRef.current = pageNavigationBusy

  useEffect(() => () => {
    if (useEditorStore.getState().view === 'mask') useEditorStore.getState().setView('original')
  }, [])
  useEffect(() => {
    if (!maskSession) return
    // Header navigation must use the same unsaved-mask guard as view tabs.
    const guardLink = (event: MouseEvent) => {
      const anchor = (event.target as Element)?.closest<HTMLAnchorElement>('a[href]')
      if (!anchor || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || anchor.target === '_blank') return
      const target = new URL(anchor.href, window.location.href)
      if (target.origin !== window.location.origin) return
      event.preventDefault(); event.stopPropagation()
      const leave = () => {setMaskSession(null); setView('original'); navigate(`${target.pathname}${target.search}${target.hash}`)}
      requestMaskLeaveRef.current(leave,'original')
    }
    document.addEventListener('click', guardLink, true)
    return () => document.removeEventListener('click', guardLink, true)
  }, [maskSession, navigate, setView])

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => shortcutHandlerRef.current(event)
    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  }, [])

  const setRegionState = useCallback((next: TextRegion[]) => {
    regionStateRevision.current += 1
    regionsRef.current = next
    setRegions(next)
    const id=currentPageId.current,cached=id?cachedScenes.current.get(id):undefined
    if (id && cached) cachedScenes.current.set(id,{...cached,regions:next})
    if (id && pagesRef.current.some(item => item.id === id && item.region_count !== next.length)) {
      const nextPages = pagesRef.current.map(item => item.id === id ? {...item, region_count: next.length} : item)
      pagesRef.current = nextPages
      setPages(nextPages)
    }
  }, [])
  useEffect(() => { pagesRef.current = pages }, [pages])
  const syncPageState = useCallback((nextPage: ImagePage) => {
    pageRef.current=nextPage
    const nextPages = pagesRef.current.map(item => item.id === nextPage.id ? nextPage : item)
    pagesRef.current = nextPages
    setPages(nextPages)
    setPage(nextPage)
    const cached=cachedScenes.current.get(nextPage.id)
    if (cached) cachedScenes.current.set(nextPage.id,{...cached,page:nextPage})
  }, [])
  const cacheScene = () => {
    const current=pageRef.current
    if (current && current.id===currentPageId.current) cachedScenes.current.set(current.id,{page:current,regions:regionsRef.current})
  }
  const rememberPageEdit = (step:RegionCommand, clean=false, authoritative?:ImagePage) => {
    const base=persistedPages.current.get(step.pageId) || step.beforePage
    const next=authoritative || (base?invalidateHistoryPreview(base):undefined)
    if (!next) return
    if (!authoritative && clean) next.clean_url=null
    persistedPages.current.set(step.pageId,next)
    if (!authoritative) {step.beforePage=base;step.afterPage=next}
    return next
  }
  const adoptDecodedPage = async (next:ImagePage,sequence:number) => {
    const isCurrent=()=>currentPageId.current===next.id && editSequence.current===sequence
    let prepared:string[] | null=null
    while (isCurrent()) {
      const store=useEditorStore.getState()
      const sources=visiblePageSources(next,store.view,store.layers)
      if (prepared && prepared.length===sources.length && prepared.every((source,index)=>source===sources[index])) {
        // Keep the decoded old background visible until its replacement is
        // ready. A newer edit cancels this paint; view changes are rechecked.
        syncPageState(next);cacheScene();return
      }
      try {await Promise.all(sources.map(source=>preloadImage(source)))}
      catch {if (isCurrent()) setError('编辑已保存，但背景图片暂时无法加载。请稍后切换视图重试。');return}
      prepared=sources
    }
  }
  const nextEditVersions = (ids:string[]) => new Map(ids.map(id=>{
    const version=(regionEditVersions.current.get(id)||0)+1
    regionEditVersions.current.set(id,version)
    return [id,version]
  }))
  const serverHistory=useRef(editorHistory.state)
  useEffect(()=>{
    if (!editQueue.pending && editorHistory.state) {serverHistory.current=editorHistory.state;editQueue.observe(editorHistory.state)}
  },[editorHistory.state,editQueue])
  const acceptCommandHistory = async (step:RegionCommand, supplied?:EditorHistoryState | null) => {
    const state=supplied || await api.history.state(projectId)
    step.entryId=state.undo_id
    if (state.undo_id===serverHistory.current?.undo_id) {step.noOp=true;editQueue.remove(step)}
    serverHistory.current=state;editorHistory.accept(state)
  }
  const recoverEditingQueue = async (reason:unknown,step?:RegionCommand) => {
    if (recoveringEditsRef.current) return
    recoveringEditsRef.current=true;setRecoveringEdits(true)
    for (const change of pending.current.values()) window.clearTimeout(change.timer)
    pending.current.clear();setHasPendingEdits(false)
    await editQueue.settled()
    try {
      const id=currentPageId.current
      const [state,nextPage,nextRegions]=await Promise.all([api.history.state(projectId),id?api.images.get(id):null,id?api.images.regions(id):[]])
      if (nextPage && currentPageId.current===id) {
        syncPageState(nextPage);setRegionState(nextRegions)
        const selection=useEditorStore.getState().selectedIds
        useEditorStore.getState().selectMany((selection.length?selection:step?.ids||[]).filter(value=>nextRegions.some(item=>item.id===value)))
      }
      cachedScenes.current.clear();persistedPages.current.clear();editQueue.reset();serverHistory.current=state;editorHistory.accept(state)
      setError(reason instanceof Error?reason.message:String(reason))
    } catch {
      setError('编辑未能同步，后台队列已停止。请重试同步后继续；不会将后续操作应用到错误的历史。')
    } finally {recoveringEditsRef.current=false;setRecoveringEdits(false)}
  }
  const saveRegionCommand = <T,>(step:RegionCommand,run:()=>Promise<T>) => {
    const save=editQueue.enqueue(run)
    void save.catch(reason=>{void recoverEditingQueue(reason,step)})
    return save
  }
  const enqueuePendingRegions = () => {
    for (const [id,change] of pending.current) void commitPendingRegion(id,change).catch(()=>undefined)
  }
  const refreshPages = useCallback(async () => {
    const next = await api.projects.images(projectId)
    pagesRef.current = next
    setPages(next)
    setPage(current => current ? next.find(item => item.id === current.id) || current : current)
    return next
  }, [projectId])
  const loadPage = useCallback(async (id: string, preservedSelection?: string | null, preserveGeometry = false, snapshot?: ImagePage,
    initial?: {page:Promise<ImagePage>, regions:Promise<TextRegion[]>, authority:Promise<ImagePage | null>}) => {
    const generation = ++pageLoadGeneration.current
    const revisionBeforeRequest = regionStateRevision.current
    const editBeforeRequest=editSequence.current
    const samePageBeforeRequest = currentPageId.current === id
    // Keep the project-list snapshot on the critical path while starting the
    // authoritative request immediately. A failed background check is merely
    // a cache miss: the already-visible snapshot remains usable.
    const cached=(editQueue.pending || pending.current.size) ? cachedScenes.current.get(id) : undefined
    const authoritativePage = initial?.authority || (snapshot && !cached ? api.images.get(id).catch(() => null) : null)
    const [nextPage, nextRegions] = await Promise.all([
      cached ? Promise.resolve(cached.page) : initial ? initial.page : snapshot ? Promise.resolve(snapshot) : api.images.get(id),
      cached ? Promise.resolve(cached.regions) : initial ? initial.regions : api.images.regions(id),
    ])
    if (nextPage.project_id !== projectId) throw new Error('该页面不属于当前项目')
    // A rapid A → B → C switch should not spend time decoding B after its
    // network response arrives late, nor let it overwrite C.
    if (generation !== pageLoadGeneration.current) return nextRegions
    let editorStore = useEditorStore.getState()
    const requestedView = editorStore.view
    const initialView = safePageView(nextPage, requestedView)
    const restoreView = initialView === requestedView ? null : requestedView
    // Before the first repair, users are reviewing detector/OCR geometry.
    // Open that workflow on the source frame so dragging a box cannot be
    // mistaken for editing the independent translated layout frame.
    if (initialView !== requestedView) {
      editorStore.setView(initialView)
      editorStore = useEditorStore.getState()
    }
    const initialSources = visiblePageSources(nextPage, initialView, editorStore.layers)
    await Promise.all(initialSources.map(source => preloadImage(source).catch(() => undefined)))
    if (generation !== pageLoadGeneration.current) return nextRegions
    const localStateChanged = samePageBeforeRequest && currentPageId.current === id && revisionBeforeRequest !== regionStateRevision.current
    const resolvedRegions = localStateChanged
      ? reconcileRegionSnapshot(nextRegions, regionsRef.current)
      : preserveGeometry ? preserveLocalRegionGeometry(nextRegions, regionsRef.current) : nextRegions
    const target = preservedSelection === undefined ? routeRegionIdRef.current : preservedSelection
    const maskRegion = resolvedRegions.find(item => item.id === target) || resolvedRegions.find(item => !item.locked) || resolvedRegions[0]
    const maskView = useEditorStore.getState().view === 'mask'
    const prepared = maskView && maskRegion ? await prepareMaskScene(nextPage,maskRegion,resolvedRegions) : undefined
    if (generation !== pageLoadGeneration.current) return nextRegions
    currentPageId.current = id
    syncPageState(nextPage); setRegionState(resolvedRegions)
    if (!editQueue.pending && !pending.current.size) persistedPages.current.set(id,nextPage)
    void historyRefreshRef.current()
    select(target && resolvedRegions.some(region => region.id === target) ? target : null)
    if (maskView) {
      setMaskState({undo:false, redo:false, dirty:false, busy:false})
      setMaskSession({page:nextPage, regions:resolvedRegions, region:maskRegion, prepared})
    }

    if (authoritativePage) {
      void authoritativePage.then(async freshPage => {
        const isCurrent = () => generation === pageLoadGeneration.current && currentPageId.current === id && editBeforeRequest===editSequence.current && !editQueue.pending && !pending.current.size
        if (!freshPage || freshPage.id !== id || !isCurrent()) return

        // The user may change views while the authority request or image decode
        // is in flight. Repeat until the resources prepared for the fresh page
        // are still the ones visible at the instant it is committed.
        let preparedSources: string[] | null = null
        while (isCurrent()) {
          const latestStore = useEditorStore.getState()
          const freshView = safePageView(freshPage, latestStore.view, restoreView)
          const sourceLayers = freshView === latestStore.view ? latestStore.layers : viewSourceLayers(freshView)
          const freshSources = visiblePageSources(freshPage, freshView, sourceLayers)
          const sourcesArePrepared = preparedSources !== null &&
            preparedSources.length === freshSources.length &&
            preparedSources.every((source, index) => source === freshSources[index])
          if (sourcesArePrepared) {
            if (freshView !== latestStore.view) latestStore.setView(freshView)
            syncPageState(freshPage)
            persistedPages.current.set(id,freshPage)
            return
          }
          await Promise.all(freshSources.map(source => preloadImage(source).catch(() => undefined)))
          preparedSources = freshSources
        }
      })
    }
    return resolvedRegions
  }, [select, setRegionState, syncPageState,editQueue,projectId])

  // Project metadata, the page index and fonts are stable while navigating
  // between pages. Load them once per project instead of putting that entire
  // request group on every image switch's critical path.
  useEffect(() => {
    let disposed = false
    loadedProjectId.current = null
    setLoading(true)
    setSwitchingPage(false)
    const pagesRequest = api.projects.images(projectId)
    const firstId = routeImageIdRef.current
    // Start the page independently of the complete book index and font list.
    // Prefer whichever snapshot arrives first while validating with authority.
    const directPage = firstId ? api.images.get(firstId) : null
    const firstSnapshot = firstId ? pagesRequest.then(items => items.find(item => item.id === firstId) || directPage!) : null
    const firstLoad = firstId && directPage && firstSnapshot ? loadPage(firstId, undefined, false, undefined, {
      page: Promise.race([directPage.catch(() => firstSnapshot), firstSnapshot]),
      regions: api.images.regions(firstId),
      authority: directPage.catch(() => null),
    }).then(() => {
      if (!disposed && routeImageIdRef.current === firstId) setLoading(false)
    }) : null
    // Metadata may fail or take longer than the page; attach a rejection handler
    // immediately, and report the authoritative error through loadProject below.
    void firstLoad?.catch(() => undefined)
    void Promise.all([api.fonts.list().catch(() => []), api.projects.fonts(projectId).catch(() => [])])
      .then(([globalFonts, projectFonts]) => {
        if (!disposed) setFontResources([...new Map([...projectFonts, ...globalFonts].map(font => [font.name, font])).values()])
      })
    const loadProject = async () => {
      try {
        const [nextProject, nextPages] = await Promise.all([
          api.projects.get(projectId),
          pagesRequest,
        ])
        if (disposed) return
        pagesRef.current = nextPages
        setProject(nextProject)
        setPages(nextPages)
        setContextText(JSON.stringify(nextProject.translation_context || {}, null, 2))
        setContextMode(nextProject.settings.translation_context_mode === 'previous_page' ? 'previous_page' : 'snapshot')
        let target = routeImageIdRef.current || nextPages[0]?.id
        if (target) {
          if (!routeImageIdRef.current) navigateRef.current(`/projects/${projectId}/editor/${target}`, {replace: true})
          while (target && !disposed) {
            if (target === firstId && firstLoad) await firstLoad
            else await loadPage(target, undefined, false, nextPages.find(item => item.id === target))
            const latestTarget = routeImageIdRef.current || nextPages[0]?.id
            if (!latestTarget || latestTarget === target) break
            target = latestTarget
          }
        } else {
          currentPageId.current = null
          setPage(null)
          setRegionState([])
        }
        if (!disposed) loadedProjectId.current = projectId
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (!disposed) setLoading(false)
      }
    }
    void loadProject()
    return () => {
      disposed = true
      pageLoadGeneration.current += 1
    }
  }, [loadPage, projectId, setRegionState])

  // A route-only image change reuses the page snapshot already held by the
  // sidebar, so the switch normally needs just the regions request and image
  // decode (which adjacent preloading usually satisfies).
  useEffect(() => {
    if (!imageId || loadedProjectId.current !== projectId) return
    if (currentPageId.current === imageId) {
      setSwitchingPage(false)
      return
    }
    let disposed = false
    setSwitchingPage(true)
    select(null)
    void loadPage(imageId, undefined, false, pagesRef.current.find(item => item.id === imageId))
      .catch(reason => {
        if (disposed) return
        setError(reason instanceof Error ? reason.message : String(reason))
        setPageContextMenu(null)
        // Keep the previous usable page and URL together if preparation fails.
        if (currentPageId.current && useEditorStore.getState().view === 'mask') navigateRef.current(`/projects/${projectId}/editor/${currentPageId.current}`, {replace:true})
      })
      .finally(() => { if (!disposed) setSwitchingPage(false) })
    return () => {
      disposed = true
      pageLoadGeneration.current += 1
    }
  }, [imageId, loadPage, projectId, select])

  useEffect(() => {
    if (!page || pages.length < 2) return
    const index = pages.findIndex(item => item.id === page.id)
    if (index < 0) return
    const neighbors = [pages[index - 1], pages[index + 1]].filter((item): item is ImagePage => Boolean(item))
    const timer = window.setTimeout(() => {
      for (const neighbor of neighbors) {
        const sources=immersiveReading ? [neighbor.clean_url || neighbor.original_url] : visiblePageSources(neighbor, view, layers)
        for (const source of sources) void preloadImage(source).catch(() => undefined)
      }
    }, 80)
    return () => window.clearTimeout(timer)
  }, [immersiveReading, layers, page?.id, pages, view])

  useEffect(() => {
    if (typeof FontFace === 'undefined' || !document.fonts) return
    let disposed = false
    const registered: FontFace[] = []
    for (const font of fontResources) {
      const source = font.url || `/media/${font.path.split('/').map(encodeURIComponent).join('/')}`
      const face = new FontFace(font.name, `url("${source}")`)
      void face.load().then(loaded => {
        if (disposed) return
        document.fonts.add(loaded)
        registered.push(loaded)
        notifyFontsChanged()
      }).catch(() => undefined)
    }
    return () => {
      disposed = true
      registered.forEach(font => document.fonts.delete(font))
      if (registered.length) notifyFontsChanged()
    }
  }, [fontResources])

  useEffect(() => () => pending.current.forEach(change => window.clearTimeout(change.timer)), [])

  useEffect(() => {
    if (editorBusy || editQueue.failed || (!error && !notice)) return
    const timer = window.setTimeout(() => {
      setError('')
      setNotice('')
    }, 3000)
    return () => window.clearTimeout(timer)
  }, [editorBusy, error, notice,editQueue.failed])

  useEffect(() => {
    if (!pageContextMenu) return
    const dismiss = (event: PointerEvent) => {
      if (pageContextMenuRef.current?.contains(event.target as Node)) return
      setPageContextMenu(null)
    }
    const escape = (event: KeyboardEvent) => {
      const menu = pageContextMenuRef.current
      if (event.key === 'Escape') {
        event.preventDefault(); setPageContextMenu(null)
        pageListRef.current?.querySelector<HTMLButtonElement>(`[data-page-id="${pageContextMenu.pageId}"]`)?.focus({preventScroll:true})
      } else if (menu?.contains(event.target as Node) && ['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
        event.preventDefault()
        const items = [...menu.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')]
        const index = items.indexOf(document.activeElement as HTMLButtonElement)
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length-1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
        items[next]?.focus()
      } else if (event.key === 'Tab') setPageContextMenu(null)
    }
    const close = (event: Event) => {
      if (event.target instanceof Node && pageContextMenuRef.current?.contains(event.target)) return
      setPageContextMenu(null)
    }
    window.addEventListener('pointerdown', dismiss)
    window.addEventListener('keydown', escape)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('keydown', escape)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [pageContextMenu])

  useEffect(() => {
    setPageContextMenu(current => current && current.pageId !== page?.id ? null : current)
  }, [page?.id])

  useLayoutEffect(() => {
    if (!pageContextMenu || !pageContextMenuRef.current) return
    const menu = pageContextMenuRef.current
    // Clamp the actual height, including every workflow action, before paint.
    const bounds = menu.getBoundingClientRect()
    menu.style.left = `${Math.max(8, Math.min(pageContextMenu.x, window.innerWidth-bounds.width-8))}px`
    menu.style.top = `${Math.max(8, Math.min(pageContextMenu.y, window.innerHeight-bounds.height-8))}px`
    if (!editorBusy && page?.id === pageContextMenu.pageId) menu.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus({preventScroll:true})
  }, [pageContextMenu, editorBusy, page?.id])

  useEffect(() => {
    if (!exportMenuOpen) return
    const dismiss = (event: PointerEvent) => {
      if (!exportMenuRef.current?.contains(event.target as Node)) setExportMenuOpen(false)
    }
    const escape = (event: KeyboardEvent) => {if (event.key === 'Escape') setExportMenuOpen(false)}
    const close = () => setExportMenuOpen(false)
    window.addEventListener('pointerdown', dismiss)
    window.addEventListener('keydown', escape)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('keydown', escape)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [exportMenuOpen])

  const commitPendingRegion = async (id: string, change: PendingChange) => {
    if (pending.current.get(id) !== change) return regionsRef.current.find(region => region.id === id)
    window.clearTimeout(change.timer)
    pending.current.delete(id)
    const save = saveRegionCommand(change.preview,async () => {
        const updated = await api.regions.update(id, change.patch)
        await acceptCommandHistory(change.preview,updated.editor_history)
        const nextPage=rememberPageEdit(change.preview,'polygon' in change.patch || 'bbox' in change.patch)
        const currentPageMatches = currentPageId.current === change.pageId
        if (currentPageMatches && nextPage) await adoptDecodedPage(nextPage,change.preview.sequence)
        if (currentPageMatches && (regionEditVersions.current.get(id) || 0) === change.version) {
          setRegionState(regionsRef.current.map(region => region.id === id ? updated : region))
          cacheScene()
        }
        return updated
    })
    setHasPendingEdits(!!pending.current.size)
    return save
  }

  const updateRegion = async (id: string, patch: Partial<TextRegion>, discrete = false) => {
    if (historyBusy.current || editorBusyRef.current || editQueue.failed || recoveringEditsRef.current) return
    const current = regionsRef.current.find(region => region.id === id)
    const pageId = currentPageId.current
    if (!current || !pageId) return
    let existing = pending.current.get(id)
    if (discrete || existing && editQueue.last!==existing.preview) {enqueuePendingRegions();existing=undefined}
    else for (const [otherId,change] of pending.current) if (otherId!==id) void commitPendingRegion(otherId,change).catch(()=>undefined)
    if (existing) window.clearTimeout(existing.timer)
    const before = existing?.before || current
    const mergedPatch = {...existing?.patch, ...patch}
    const preview:RegionCommand=existing?.preview || {pageId,ids:[id],before:[before],after:[],sequence:++editSequence.current,label:'编辑文字区域',beforePage:pageRef.current||undefined}
    preview.after=[{...current,...mergedPatch}]
    if (!existing) editQueue.record(preview)
    const version = (regionEditVersions.current.get(id) || 0) + 1
    regionEditVersions.current.set(id, version)
    setHasPendingEdits(true)
    // Commit the gesture locally before releasing its canvas preview. Versions
    // above keep an earlier save response from overwriting a newer gesture.
    setRegionState(regionsRef.current.map(region => region.id === id ? {...region, ...patch} : region))
    if (pageRef.current) {
      const next=invalidateHistoryPreview(pageRef.current)
      if ('polygon' in patch || 'bbox' in patch) next.clean_url=null
      preview.afterPage=next;syncPageState(next)
    }
    cacheScene()
    const change: PendingChange = {pageId, before, patch: mergedPatch, timer: 0, version,preview}
    change.timer = window.setTimeout(() => {void commitPendingRegion(id, change).catch(() => undefined)}, 320)
    pending.current.set(id, change)
    if (discrete) void commitPendingRegion(id,change).catch(() => undefined)
  }

  const updateManyRegions = async (changes: Array<{id:string,patch:Partial<TextRegion>}>) => {
    if (historyBusy.current || editorBusyRef.current || editQueue.failed || recoveringEditsRef.current || !changes.length || !currentPageId.current) return
    enqueuePendingRegions()
    const pageId = currentPageId.current
    const patches = new Map(changes.map(change => [change.id,change.patch]))
    const preview:RegionCommand={pageId,ids:changes.map(change=>change.id),label:'批量编辑文字区域',beforePage:pageRef.current||undefined,
      before:regionsRef.current.filter(region=>patches.has(region.id)),
      after:regionsRef.current.filter(region=>patches.has(region.id)).map(region=>({...region,...patches.get(region.id)})),sequence:++editSequence.current}
    editQueue.record(preview)
    const versions = nextEditVersions(preview.ids)
    setRegionState(regionsRef.current.map(region => patches.has(region.id) ? {...region,...patches.get(region.id)} : region))
    if (pageRef.current) {
      const next=invalidateHistoryPreview(pageRef.current)
      if (changes.some(({patch})=>'polygon' in patch || 'bbox' in patch)) next.clean_url=null
      preview.afterPage=next;syncPageState(next)
    }
    cacheScene()
    const save = saveRegionCommand(preview,async () => {
        const updated = await api.regions.updateMany(changes)
        await acceptCommandHistory(preview,updated[0]?.editor_history)
        const nextPage=rememberPageEdit(preview,changes.some(({patch})=>'polygon' in patch || 'bbox' in patch))
        if (currentPageId.current === pageId) {
          if (nextPage) await adoptDecodedPage(nextPage,preview.sequence)
          const byId = new Map(updated.map(region => [region.id,region]))
          setRegionState(regionsRef.current.map(region => byId.has(region.id) && regionEditVersions.current.get(region.id) === versions.get(region.id) ? byId.get(region.id)! : region))
          cacheScene()
        }
        return updated
    })
    void save.catch(() => undefined)
  }

  const flushPendingRegions = async () => {
    enqueuePendingRegions()
    await editQueue.flush()
  }
  flushEditsRef.current = flushPendingRegions
  leaveGuardRef.current=event=>{
    if (maskSession || event.defaultPrevented || event.button!==0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !hasPendingEdits && !editQueue.pending) return
    const anchor=(event.target as Element)?.closest<HTMLAnchorElement>('a[href]')
    if (!anchor || anchor.target==='_blank') return
    const target=new URL(anchor.href,window.location.href)
    if (target.origin!==window.location.origin || target.pathname===window.location.pathname && target.search===window.location.search) return
    event.preventDefault();event.stopPropagation()
    if (historyBusy.current) return
    historyBusy.current=true;setWaitingForEdits(true)
    void flushPendingRegions().then(()=>navigate(`${target.pathname}${target.search}${target.hash}`))
      .catch(reason=>setError(reason instanceof Error?reason.message:String(reason)))
      .finally(()=>{historyBusy.current=false;setWaitingForEdits(false)})
  }

  const openMaskEditor = async () => {
    if (!page || editorBusy || activePageTask) return
    const requestedPage = page, id = selectedIds[0]
    setOpeningMask(true); setError('')
    try {
      // Geometry autosaves can invalidate the old mask. Read a fresh snapshot
      // after they finish instead of editing a cached, stale mask image.
      await flushPendingRegions()
      const fresh = await api.images.regions(requestedPage.id)
      const region = fresh.find(item => item.id === id) || fresh.find(item => !item.locked) || fresh[0]
      const generation = pageLoadGeneration.current
      const prepared = region ? await prepareMaskScene(requestedPage,region,fresh) : undefined
      if (generation !== pageLoadGeneration.current) return
      if (currentPageId.current !== requestedPage.id) return
      setRegionState(fresh)
      setMaskState({undo: false, redo: false, dirty: false, busy: false})
      setMaskSession({page: requestedPage, region, regions: fresh, prepared})
      setView('mask')
      useEditorStore.getState().setTool('brush')
    } catch (reason) {setError(reason instanceof Error ? reason.message : String(reason))}
    finally {setOpeningMask(false)}
  }

  const changeView = (nextView: ViewMode) => {
    if (nextView === view) return
    if (nextView === 'mask') {void openMaskEditor(); return}
    const leave = () => {setMaskSession(null); setMaskState({undo: false, redo: false, dirty: false, busy: false}); setView(nextView)}
    if (maskSession) requestMaskLeaveRef.current(leave,nextView)
    else leave()
  }

  const selectMaskRegion = async (id: string) => {
    const session = maskSessionRef.current, region = session?.regions.find(item => item.id === id)
    if (!session || !region || region.id === session.region?.id || openingMask) return
    const generation = pageLoadGeneration.current
    setOpeningMask(true); setError('')
    try {
      const prepared = await prepareMaskScene(session.page,region,session.regions)
      if (generation !== pageLoadGeneration.current || useEditorStore.getState().view !== 'mask') return
      setMaskState({undo:false,redo:false,dirty:false,busy:false})
      setMaskSession({...session,region,prepared})
    } catch (reason) {setError(reason instanceof Error ? reason.message : String(reason))}
    finally {setOpeningMask(false)}
  }

  const saveMask = async (region: TextRegion, blob: Blob, bounds?: {x:number,y:number}) => {
    const session=maskSessionRef.current
    if (!session) throw new Error('蒙版编辑会话已结束')
    const {page: editedPage} = session
    const updated = bounds ? await api.regions.mask(region.id, blob, region.updated_at, bounds) : await api.regions.mask(region.id, blob, region.updated_at)
    if (currentPageId.current === editedPage.id) {
      editSequence.current++
      setRegionState(regionsRef.current.map(item => item.id === updated.id ? updated : item))
      // Keep the previous clean background until exit applies the saved masks
      // to only the pending regions. Cached translated composites are stale.
      if (editedPage.rendered_url || editedPage.text_layer_url) {
        const invalidated={...editedPage, rendered_url: null, text_layer_url: null, text_layer_region_ids: null}
        syncPageState(invalidated)
        setMaskSession(current => current?.page.id === editedPage.id ? {...current,page:invalidated} : current)
      }
      if (updated.editor_history) editorHistory.accept(updated.editor_history)
      else void historyRefreshRef.current()
      setMaskSession(current => current?.page.id === editedPage.id ? ({...current, region: current.region?.id === updated.id ? updated : current.region, regions: current.regions.map(item => item.id === updated.id ? updated : item)}) : current)
    }
    return updated
  }

  const applyHistory = async (direction: 'undo' | 'redo') => {
    if (historyBusy.current || editQueue.failed || loading || blockingBusy || projectHasTasks || switchingPage || openingMask || maskState.busy) return
    const requestedPageId = currentPageId.current
    enqueuePendingRegions()
    // Restore the authoritative journal for every normal edit. Do not move a
    // local history cursor or show a successful undo before the server replies.
    historyBusy.current = true
    setHistoryDirection(direction)
    try {
      if (maskSession) await maskEditorRef.current?.flush()
      await flushPendingRegions()
      const state = await api.history.state(projectId)
      if (state.pending) {editorHistory.accept(state); setError('请等待项目后台任务完成或取消后再撤销或重做'); return}
      const entryId = direction === 'undo' ? state.undo_id : state.redo_id
      if (entryId === null) {editorHistory.accept(state); return}
      const result = await api.history.apply(projectId,direction,entryId,requestedPageId)
      if (loadedProjectId.current !== projectId) return
      const nextPage = result.page
      const nextView = nextPage ? safePageView(nextPage,useEditorStore.getState().view) : 'original'
      if (nextPage) await Promise.all(visiblePageSources(nextPage,nextView,useEditorStore.getState().layers).map(source => preloadImage(source).catch(() => undefined)))
      pageLoadGeneration.current++
      pagesRef.current = result.pages
      setPages(result.pages)
      setProject(current => current ? {...current, page_count: result.pages.length} : current)
      currentPageId.current = nextPage?.id || null
      if (nextPage) syncPageState(nextPage); else {pageRef.current=null;setPage(null)}
      // History is authoritative for BOTH frames, not just translated layout.
      setRegionState(result.regions)
      editorHistory.accept(result.history)
      serverHistory.current=result.history;editQueue.reset();cachedScenes.current.clear()
      persistedPages.current.clear()
      const ids = result.region_ids.filter(id => result.regions.some(region => region.id === id))
      useEditorStore.getState().selectMany(ids)
      if (maskSession && nextPage) {
        const region = result.regions.find(item => item.id === ids[0]) || result.regions[0]
        const prepared = region ? await prepareMaskScene(nextPage,region,result.regions) : undefined
        setMaskState({undo:false,redo:false,dirty:false,busy:false})
        setMaskSession({page:nextPage,regions:result.regions,region,prepared,version:++maskHistoryVersion.current})
      } else {
        setMaskSession(null)
        if (nextView !== useEditorStore.getState().view) setView(nextView)
      }
      if (requestedPageId !== nextPage?.id) navigateRef.current(`/projects/${projectId}/editor${nextPage ? `/${nextPage.id}` : ''}`,{replace:true})
      setNotice(`已${direction === 'undo' ? '撤销' : '重做'}：${result.action}`)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      void historyRefreshRef.current()
    } finally {
      historyBusy.current = false
      setHistoryDirection(null)
    }
  }

  const syncMaskHistory = async (direction:'undo'|'redo',region:TextRegion,operationId:string) => {
    const session=maskSessionRef.current
    const state=region.editor_history
    const entryId=direction === 'undo' ? state?.undo_id : state?.redo_id
    if (!session || entryId == null) throw new Error('蒙版历史尚未同步，请重试')
    const result=await api.history.apply(projectId,direction,entryId,session.page.id,operationId)
    const updated=result.regions.find(item => item.id === region.id)
    if (!updated || result.page?.id !== session.page.id) throw new Error('蒙版历史范围已变化，请重新打开蒙版')
    if (maskSessionRef.current?.page.id !== session.page.id) throw new Error('蒙版编辑会话已结束')
    setRegionState(result.regions)
    syncPageState(result.page)
    editorHistory.accept(result.history)
    setMaskSession(current => current?.page.id === session.page.id ? {...current,page:result.page!,regions:result.regions,
      region:current.region?.id === updated.id ? updated : current.region} : current)
    return {...updated,editor_history:result.history}
  }

  const createRegion = async (polygon: number[][], bbox: number[]): Promise<boolean> => {
    if (!page || editorBusyRef.current || editQueue.failed) return false
    const requestedPageId = page.id
    setError('')
    setRunningRegionAction('create')
    enqueuePendingRegions()
    const draft = draftRegion(requestedPageId,polygon,bbox,regionsRef.current)
    const preview:RegionCommand={pageId:requestedPageId,ids:[draft.id],before:[],after:[draft],sequence:++editSequence.current,label:'创建文字区域',beforePage:pageRef.current||undefined}
    editQueue.record(preview)
    try {
      await saveRegionCommand(preview,async () => {
        const created = await api.regions.create(requestedPageId,{id:draft.id,polygon,bbox,reading_order:draft.reading_order,orientation:draft.orientation})
        await acceptCommandHistory(preview,created.editor_history)
        const nextPage=rememberPageEdit(preview)
        if (currentPageId.current === requestedPageId) {
          if (nextPage) await adoptDecodedPage(nextPage,preview.sequence)
          setRegionState([...regionsRef.current,created]); select(created.id); setTool('select')
          const store = useEditorStore.getState()
          if (!store.layers.detection) store.toggleLayer('detection')
          cacheScene()
        }
        return created
      })
      return true
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return false
    } finally {setRunningRegionAction(null)}
  }
  const reloadCurrent = useCallback(async (preserveGeometry = true) => {
    if (!page || page.id !== currentPageId.current || routeImageIdRef.current && routeImageIdRef.current !== page.id) return
    const selectedId = useEditorStore.getState().selectedIds[0] || null
    const [nextRegions] = await Promise.all([loadPage(page.id, selectedId, preserveGeometry), refreshPages()])
    return nextRegions.find(region => region.id === selectedId)
  }, [page?.id, loadPage, refreshPages])
  const reloadCurrentRef = useRef(reloadCurrent)
  reloadCurrentRef.current = reloadCurrent
  useEffect(() => {
    if (!page?.id || !pendingRecoveredRefresh.current.delete(page.id)) return
    void reloadCurrentRef.current().catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
  }, [page?.id])

  const syncTaskProgress = useCallback(async (task: ProcessingTask) => {
    if (task.image_id === currentPageId.current) setRunningPageTask(task)
    // Checkpoint batches publish once. Intermediate private stage output does
    // not change this page, so avoid fetching its full regions on every stage.
    if (task.batch_id && task.status !== 'COMPLETED') return
    if (!currentPageId.current || task.image_id !== currentPageId.current || routeImageIdRef.current && routeImageIdRef.current !== task.image_id || task.progress <= 0) return
    // OCR may report once per region. The overlay needs every progress value,
    // but page geometry/artifact snapshots only need refreshing at stage
    // boundaries; the terminal path performs one final authoritative reload.
    const version = `${task.id}:${task.current_stage || ''}`
    if (taskSyncVersion.current === version) return
    taskSyncVersion.current = version
    const monitorGeneration = taskMonitorGeneration.current
    const revisionBeforeRequest = regionStateRevision.current
    const requestedPageId = currentPageId.current
    const selectedId = useEditorStore.getState().selectedIds[0] || null
    const [nextPage, nextRegions] = await Promise.all([api.images.get(requestedPageId), api.images.regions(requestedPageId)])
    if (monitorGeneration !== taskMonitorGeneration.current || currentPageId.current !== requestedPageId || routeImageIdRef.current && routeImageIdRef.current !== requestedPageId || nextPage.id !== requestedPageId) return
    syncPageState(nextPage)
    // A region may be created or edited while this refresh is in flight. Do
    // not let an older response replace that newer local state; the next task
    // update (or final reload) will fetch an authoritative list again.
    if (revisionBeforeRequest === regionStateRevision.current) {
      const resolvedRegions = preserveLocalRegionGeometry(nextRegions, regionsRef.current)
      setRegionState(resolvedRegions)
      if (selectedId && !resolvedRegions.some(region => region.id === selectedId)) select(null)
    }
  }, [select, syncPageState, setRunningPageTask, taskMonitorGeneration])

  const refreshAfterTask = async (taskId: string, onUpdate?: TaskUpdateHandler) => {
    const task = await waitForTask(taskId, onUpdate)
    if (!task) return null
    if (task.image_id !== currentPageId.current || routeImageIdRef.current && routeImageIdRef.current !== task.image_id) {
      await Promise.all([refreshPages(), historyRefreshRef.current()])
      if (task.status === 'FAILED') setError(task.error_message || '处理任务失败')
      return null
    }
    const region = await reloadCurrentRef.current()
    if (task.image_id !== currentPageId.current || routeImageIdRef.current && routeImageIdRef.current !== task.image_id) return null
    if (task.status === 'FAILED') setError(task.error_message || '处理任务失败')
    return {task, region, regions: [...regionsRef.current]}
  }
  recoveredTaskUpdate.current = async task => {
    if (['COMPLETED','FAILED','CANCELLED'].includes(task.status)) {
      if (task.image_id && task.image_id === routeImageIdRef.current && page?.id !== task.image_id) {
        // Recovery can finish before the first scene has loaded. Revalidate
        // once that scene exists instead of leaving its in-flight OCR stale.
        pendingRecoveredRefresh.current.add(task.image_id)
      }
      setSyncingTaskResult(true)
      try {
        if (task.image_id === currentPageId.current) await reloadCurrentRef.current()
        if (task.status === 'FAILED') setError(task.error_message || '后台任务失败')
      } finally {setSyncingTaskResult(false)}
    } else await syncTaskProgress(task)
  }
  recoveredTasksSettled.current = () => Promise.all([refreshPages(), historyRefreshRef.current()])

  const repairMasksBeforeLeave = async (leave: () => void, nextView?: ViewMode) => {
    if (maskRepairBusy.current) return
    const session = maskSessionRef.current
    const changed = session?.regions.filter(region => {
      const generation = region.layout_data.mask_generation as Record<string, unknown> | undefined
      return generation?.repair_pending === true
    }) || []
    if (!session || !changed.length) {leave();return}
    maskRepairBusy.current = true
    editSequence.current++
    taskActivity.begin([])
    setRunningRegionAction('maskRepair');setError('');setNotice('')
    setRegionProcessing({ids:changed.map(region => region.id),keys:changed.map(region => region.region_key),filename:session.page.filename})
    try {
      // requestLeave has already flushed every stroke. Start at inpainting,
      // not mask generation, so the exact hand-painted masks are retained.
      const task = await api.images.process(session.page.id,{
        start_stage:'inpainting',end_stage:'inpainting',force:true,
        options:{region_ids:changed.map(region => region.id),repair_geometry:'source'},
      })
      setRunningPageTask(task)
      const completed = await waitForTask(task.id,setRunningPageTask)
      if (!completed || completed.status !== 'COMPLETED') throw new Error(completed?.error_message || '蒙版背景修复未完成，请重试退出蒙版')
      const [nextPage,nextRegions,state] = await Promise.all([
        api.images.get(session.page.id),api.images.regions(session.page.id),api.history.state(projectId),
      ])
      if (maskSessionRef.current?.page.id !== session.page.id) return
      syncPageState(nextPage);setRegionState(nextRegions)
      persistedPages.current.set(nextPage.id,nextPage)
      editorHistory.accept(state)
      setMaskSession(current => current?.page.id === nextPage.id ? {...current,page:nextPage,regions:nextRegions,
        region:nextRegions.find(region => region.id === current.region?.id) || nextRegions[0]} : current)
      const targetView = nextView || useEditorStore.getState().view
      await Promise.all(visiblePageSources(nextPage,targetView,viewSourceLayers(targetView)).map(source => preloadImage(source)))
      setNotice(`已按修改后的蒙版修复 ${changed.length} 个区域。`)
      leave()
    } catch (reason) {
      // Stay in the mask editor on failure; saved masks and prior clean pixels
      // remain intact and the pending flag allows another exit to retry.
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      maskRepairBusy.current = false
      setRunningRegionAction(null);setRunningPageTask(null);setRegionProcessing(null)
    }
  }
  requestMaskLeaveRef.current = (leave,nextView) => {
    if (maskRepairBusy.current || historyBusy.current || recoveringEditsRef.current) return
    const complete = () => {void repairMasksBeforeLeave(leave,nextView)}
    if (maskEditorRef.current) maskEditorRef.current.requestLeave(complete)
    else complete()
  }

  const deleteSelectedRegion = async (ids=useEditorStore.getState().selectedIds) => {
    if (deleteBusy.current || editorBusyRef.current || editQueue.failed || !pageRef.current) return
    const requestedPageId=pageRef.current.id
    const idSet=new Set(ids)
    const selected = regionsRef.current.filter(region => idSet.has(region.id))
    if (!selected.length) return
    const selectedRegionIds = selected.map(region => region.id)
    const multiple = selected.length > 1

    deleteBusy.current = true
    let started = false
    try {
      const accepted = await confirmDialog({
        title: multiple ? `删除 ${selected.length} 个文字区域？` : '删除文字区域？',
        message: `${multiple ? selected.map(region => region.region_key).join('、') : selected[0].region_key} 将被删除；可通过撤销恢复文字、蒙版和排版设置。系统会复用现有修复结果，恢复被删除区域的原图，不会重新处理整页。`,
        tone: 'danger',
        confirmLabel: multiple ? '删除所选' : '删除区域',
      })
      if (!accepted || editorBusyRef.current || currentPageId.current!==requestedPageId || !pageRef.current) return
      started = true
      setRunningRegionAction('delete');setError('')
      enqueuePendingRegions()
      const before=regionsRef.current.filter(region=>idSet.has(region.id))
      const preview:RegionCommand={pageId:requestedPageId,ids:selectedRegionIds,before,after:[],label:multiple?'批量删除文字区域':'删除文字区域',
        sequence:++editSequence.current,beforePage:pageRef.current}
      editQueue.record(preview);nextEditVersions(selectedRegionIds)
      await saveRegionCommand(preview,async()=>{
        const deleted = multiple
        ? await api.regions.removeMany(selectedRegionIds)
        : await api.regions.remove(selectedRegionIds[0])
        let nextPage=deleted.page
        if (deleted.rebuild_task) {
          // Damaged legacy cache: actual inference is unavoidable. Keep the
          // durable queue ordered and show the established waiting dialog.
          setRunningRegionAction('delete');setRunningPageTask(deleted.rebuild_task)
          try {
            const task=await waitForTask(deleted.rebuild_task.id,setRunningPageTask)
            if (!task || task.status!=='COMPLETED') throw new Error(task?.error_message || '删除后的背景重建未完成')
            nextPage=await api.images.get(requestedPageId)
          } finally {setRunningPageTask(null)}
        }
        await acceptCommandHistory(preview,deleted.rebuild_task?undefined:nextPage?.editor_history || deleted.editor_history)
        if (nextPage) preview.afterPage=nextPage
        if (nextPage) rememberPageEdit(preview,false,nextPage)
        if (currentPageId.current===requestedPageId) {
          if (nextPage) await adoptDecodedPage(nextPage,preview.sequence)
          setRegionState(regionsRef.current.filter(region=>!idSet.has(region.id)).map(region => deleted.region_keys?.[region.id] ? {...region,region_key:deleted.region_keys[region.id]} : region))
          useEditorStore.getState().selectMany(useEditorStore.getState().selectedIds.filter(id=>!idSet.has(id)))
          cacheScene()
        }
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      deleteBusy.current = false
      if (started) setRunningRegionAction(null)
    }
  }

  const action = async (name: RegionCommandAction, options: Record<string, unknown> = {}, targetRegionIds?: string[]) => {
    if (editorBusyRef.current || !['ocr','translate','inpaint','merge','delete'].includes(name)) return
    const requestedIds = targetRegionIds ?? useEditorStore.getState().selectedIds
    const stageOptions = name === 'inpaint'
      ? {...options, repair_geometry: useEditorStore.getState().view === 'translated' ? 'translated' : 'source'}
      : options
    const regionIds = [...new Set(requestedIds)].filter(id => regionsRef.current.some(region => region.id === id))
    const selected = regionsRef.current.find(region => region.id === regionIds[0])
    try {
      if (regionIds.length > 1 && name !== 'merge' && name !== 'delete') {
        if (!page || regionActionBusy.current) return
        const targetRegions = regionsRef.current.filter(region => regionIds.includes(region.id))
        const stage = ({
          ocr: {start_stage: 'ocr', end_stage: 'ocr'},
          translate: {start_stage: 'translation', end_stage: 'translation'},
          inpaint: {start_stage: 'mask', end_stage: 'inpainting'},
        } satisfies Record<RegionStageAction, {start_stage: string, end_stage: string}>)[name]
        const actionLabel = ({ocr: 'OCR', translate: '翻译', inpaint: '修复'} satisfies Record<RegionStageAction, string>)[name]
        regionActionBusy.current = true
        taskActivity.begin([])
        setRegionProcessing({ids:regionIds, keys:targetRegions.map(region => region.region_key), filename:page.filename})
        blurActiveControl()
        setRunningRegionAction(name); setError(''); setNotice(`正在为 ${regionIds.length} 个所选区域重新${actionLabel}…`)
        try {
          await flushPendingRegions()
          const task = await api.images.process(page.id, {
            ...stage,
            force: true,
            options: {...stageOptions, ...(name === 'ocr' ? {crop_padding: 4} : {}), region_ids: regionIds},
          })
          setRunningPageTask(task)
          const result = await refreshAfterTask(task.id, syncTaskProgress)
          if (!result || result.task.status !== 'COMPLETED') {
            setNotice('')
            return
          }
          useEditorStore.getState().selectMany(regionIds.filter(id => regionsRef.current.some(region => region.id === id)))
          if (name === 'translate' && regionsRef.current.some(region => regionIds.includes(region.id) && region.layout_data.translation_fallback)) {
            setNotice('')
            setError('重新翻译已执行，但当前默认翻译 Provider 是 Passthrough 占位模式。请先在“设置”中配置真实翻译模型。')
          } else {
            setNotice(`${regionIds.length} 个所选区域已完成重新${actionLabel}。`)
          }
        } finally {
          regionActionBusy.current = false
          setRunningRegionAction(null)
          setRunningPageTask(null)
          setRegionProcessing(null)
        }
        return
      }
      if (name === 'merge') {
        if (regionIds.length < 2) return
        setRunningRegionAction('merge')
        try {
          await flushPendingRegions()
          const mergedResult = await api.regions.merge(regionIds)
          const merged = mergedResult.region
          setRegionState([...regionsRef.current.filter(region => !regionIds.includes(region.id)).map(region => mergedResult.region_keys?.[region.id] ? {...region,region_key:mergedResult.region_keys[region.id]} : region), merged])
          select(merged.id)
          void historyRefreshRef.current()
          if (mergedResult.rebuild_task) {
            setNotice('区域已合并，正在重新生成 Mask、净图和译文…')
            setRunningPageTask(mergedResult.rebuild_task)
            const result = await refreshAfterTask(mergedResult.rebuild_task.id, syncTaskProgress)
            if (result?.task.status === 'COMPLETED') setNotice(`${regionIds.length} 个区域已合并，净图和译文已重新生成。`)
            else setNotice('')
          } else {
            setNotice(`${regionIds.length} 个区域已合并。`)
          }
        } finally {
          setRunningRegionAction(null)
          setRunningPageTask(null)
        }
        return
      }
      if (!selected) return
      const selectedId = selected.id
      if (name === 'delete') await deleteSelectedRegion(regionIds)
      else {
        if (regionActionBusy.current) return
        regionActionBusy.current = true
        taskActivity.begin([])
        const runningMessage = ({ocr: '正在重新识别当前区域…', translate: '正在重新翻译当前区域…', inpaint: '正在重新生成 Mask 并修复背景…'} satisfies Record<RegionStageAction, string>)[name]
        setRegionProcessing({ids:[selectedId], keys:[selected.region_key], filename:page?.filename || ''})
        setRunningPageTask(null)
        blurActiveControl()
        setRunningRegionAction(name); setNotice(runningMessage || '正在处理当前区域…'); setError('')
        try {
          await flushPendingRegions()
          const activeRegion = regionsRef.current.find(region => region.id === selectedId) || selected
          const before = activeRegion
          const task = await api.regions.stage(selectedId, name, stageOptions)
          setRunningPageTask(task)
          const result = await refreshAfterTask(task.id, syncTaskProgress)
          if (!result || result.task.status !== 'COMPLETED') return
          const updated = result.region
          if (name === 'translate' && updated?.layout_data.translation_fallback) {
            setNotice('')
            setError('重新翻译已执行，但当前默认翻译 Provider 是 Passthrough 占位模式，不会改变原文。请先在“设置”中配置真实翻译模型。')
          } else if (name === 'ocr') {
            setNotice(updated?.source_text === before.source_text ? '重新 OCR 已完成，识别结果与之前一致。' : '重新 OCR 已完成，原文已更新。')
          } else if (name === 'translate') {
            setNotice(updated?.translated_text === before.translated_text ? '重新翻译已完成，翻译结果与之前一致。' : '重新翻译已完成，译文已更新。')
          } else if (name === 'inpaint') {
            setNotice('当前区域的文字 Mask 与修复背景已重新生成。')
          }
        } finally {
          regionActionBusy.current = false
          setRunningRegionAction(null)
          setRunningPageTask(null)
          setRegionProcessing(null)
        }
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  const openImagePicker = () => {
    if (editorBusy || !fileRef.current) return
    setPageContextMenu(null)
    fileRef.current.value = ''
    fileRef.current.click()
  }

  const navigatePage = useCallback((pageId: string) => {
    if (pageNavigationBusyRef.current || (pageId === currentPageId.current && pageId === routeImageIdRef.current)) return
    const leave = () => {
      pageNavigationBusyRef.current = true
      setSwitchingPage(true)
      setNotice(''); setError('')
      navigateRef.current(`/projects/${projectId}/editor/${pageId}`)
    }
    if (useEditorStore.getState().view === 'mask') requestMaskLeaveRef.current(leave)
    else if (editQueue.pending || pending.current.size) {
      pageNavigationBusyRef.current = true
      historyBusy.current = true
      setWaitingForEdits(true)
      void flushEditsRef.current().then(leave)
        .catch(reason => {
          pageNavigationBusyRef.current = false
          setError(reason instanceof Error ? reason.message : String(reason))
        })
        .finally(() => {historyBusy.current=false;setWaitingForEdits(false)})
    } else leave()
  }, [projectId, editQueue])
  const activatePage = useCallback((event: ReactMouseEvent<HTMLButtonElement>, pageId: string) => {
    if (suppressPageClick.current) {
      event.preventDefault()
      event.stopPropagation()
      return
    }
    navigatePage(pageId)
  }, [navigatePage])

  const openPageContextMenu = useCallback((event: ReactMouseEvent, item: ImagePage) => {
    event.preventDefault(); event.stopPropagation()
    if (editorBusyRef.current || pagePressRef.current?.active) return
    const bounds = event.currentTarget instanceof HTMLElement ? event.currentTarget.getBoundingClientRect() : null
    const keyboard = event.clientX === 0 && event.clientY === 0
    navigatePage(item.id)
    setPageContextMenu({
      x: keyboard && bounds ? bounds.left + 16 : event.clientX,
      y: keyboard && bounds ? bounds.top + 24 : event.clientY,
      pageId: item.id,
    })
  }, [navigatePage])

  const openCanvasPageMenu = useCallback((position: {x: number, y: number}) => {
    if (editorBusyRef.current || !currentPageId.current) return
    setPageContextMenu({...position, pageId:currentPageId.current, source:'canvas'})
  }, [])

  const openImagePickerFromBlank = (event: ReactMouseEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest('[data-page-id]')) return
    event.preventDefault()
    if (!editorBusy) openImagePicker()
  }

  const deletePage = async (pageId: string) => {
    if (editorBusy) return
    const targetIndex = pages.findIndex(item => item.id === pageId)
    const target = pages[targetIndex]
    if (!target) return
    setPageContextMenu(null)
    if (!await confirmDialog({
      title: '删除图片？',
      message: `将移除“${target.filename}”及其文字区域、蒙版和处理结果。可通过撤销完整恢复这张图片。`,
      tone: 'danger',
      confirmLabel: '删除图片',
    })) return
    setDeletingPageId(pageId); setError(''); setNotice('正在删除图片…')
    try {
      await flushPendingRegions()
      await api.images.remove(pageId)
      void historyRefreshRef.current()
      const remaining = await api.projects.images(projectId)
      setPages(remaining)
      setProject(current => current ? {...current, page_count: remaining.length} : current)
      if (page?.id === pageId) {
        pending.current.forEach(change => window.clearTimeout(change.timer))
        pending.current.clear()
        currentPageId.current = null
        select(null); setRegionState([]); setPage(null)
        const next = remaining[Math.min(targetIndex, remaining.length - 1)]
        navigate(next ? `/projects/${projectId}/editor/${next.id}` : `/projects/${projectId}/editor`, {replace: true})
      }
      setNotice('图片已删除。')
    } catch (reason) {
      setNotice('')
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setDeletingPageId(null)
    }
  }

  const togglePageOcrExempt = async (pageId: string) => {
    if (editorBusy) return
    const target = pages.find(item => item.id === pageId)
    if (!target) return
    const exempt = !target.ocr_exempt
    setPageContextMenu(null)
    setUpdatingPageRecognitionId(pageId)
    setError('')
    try {
      await flushPendingRegions()
      const updated = await api.images.setOcrExempt(pageId, exempt)
      void historyRefreshRef.current()
      setPages(current => current.map(item => item.id === pageId ? updated : item))
      if (page?.id === pageId) setPage(updated)
      setNotice(exempt ? '该图片已标记为已翻译，批量识别时会自动跳过。' : '已取消已翻译标记，该图片会重新进入批量识别范围。')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setUpdatingPageRecognitionId(null)
    }
  }

  const reorderPage = async (pageId: string, fromIndex: number, toIndex: number) => {
    if (editorBusy || fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= pages.length || toIndex >= pages.length || pages[fromIndex].id !== pageId) return
    const previousPages = [...pages]
    const activePageId = imageId || page?.id
    const selectedBefore = useEditorStore.getState().selectedIds
    const nextPages = [...pages]
    const [moved] = nextPages.splice(fromIndex, 1)
    nextPages.splice(toIndex, 0, moved)
    setPages(nextPages)
    setReorderingPages(true); setReorderProgress(0); setError(''); setNotice('正在保存页面顺序…')
    try {
      await flushPendingRegions()
      await api.projects.reorderImages(projectId,nextPages.map(item => item.id))
      setReorderProgress(1)
      void historyRefreshRef.current()
      const refreshedPages = await refreshPages()
      if (activePageId) {
        const activePage = refreshedPages.find(item => item.id === activePageId)
        if (activePage) {
          const activeRegions = await api.images.regions(activePageId)
          setPage(activePage)
          setRegionState(activeRegions)
          useEditorStore.getState().selectMany(selectedBefore.filter(id => activeRegions.some(region => region.id === id)))
        }
      }
      setNotice('页面顺序已更新。')
    } catch (reason) {
      setPages(previousPages)
      setNotice('')
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setReorderingPages(false)
      setReorderProgress(null)
    }
  }

  const beginPagePress = useCallback((event: ReactPointerEvent<HTMLButtonElement>, item: ImagePage, index: number) => {
    if (editorBusyRef.current || !event.isPrimary || event.button !== 0 || event.pointerType === 'touch') return
    setPageContextMenu(null)
    pagePressRef.current = {
      pageId: item.id,
      fromIndex: index,
      overIndex: index,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      active: false,
    }
  }, [])

  useEffect(() => {
    const finish = (event?: PointerEvent) => {
      const press = pagePressRef.current
      if (!press || (event && event.pointerId !== press.pointerId)) return
      pagePressRef.current = null
      document.body.style.userSelect = ''
      setPageDrag(null)
      if (!press.active) return
      suppressPageClick.current = true
      window.setTimeout(() => { suppressPageClick.current = false }, 0)
      if (press.fromIndex !== press.overIndex) void reorderPage(press.pageId, press.fromIndex, press.overIndex)
    }
    const move = (event: PointerEvent) => {
      const press = pagePressRef.current
      if (!press || event.pointerId !== press.pointerId) return
      if (!press.active) {
        if (Math.hypot(event.clientX - press.startX, event.clientY - press.startY) < PAGE_DRAG_START_THRESHOLD) return
        press.active = true
        suppressPageClick.current = true
        document.body.style.userSelect = 'none'
        setPageDrag({pageId: press.pageId, fromIndex: press.fromIndex, overIndex: press.overIndex})
      }
      event.preventDefault()
      const list = pageListRef.current
      if (list) {
        const bounds = list.getBoundingClientRect()
        if (event.clientY < bounds.top + 36) list.scrollTop -= 12
        else if (event.clientY > bounds.bottom - 36) list.scrollTop += 12
      }
      const target = (document.elementFromPoint(event.clientX, event.clientY) as HTMLElement | null)?.closest<HTMLElement>('[data-page-index]')
      const overIndex = Number(target?.dataset.pageIndex)
      if (!Number.isInteger(overIndex) || overIndex < 0 || overIndex >= pages.length || overIndex === press.overIndex) return
      press.overIndex = overIndex
      setPageDrag({pageId: press.pageId, fromIndex: press.fromIndex, overIndex})
    }
    const cancel = () => finish()
    window.addEventListener('pointermove', move, {passive: false})
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
    window.addEventListener('blur', cancel)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      window.removeEventListener('blur', cancel)
      pagePressRef.current = null
      document.body.style.userSelect = ''
    }
  }, [pages, editorBusy])

  const upload = async (files: FileList | null) => {
    const selectedFiles = files ? [...files] : []
    if (editorBusy || uploadBusy.current || !selectedFiles.length) return
    const unsupported = selectedFiles.filter(file => !/\.(jpe?g|png|webp)$/i.test(file.name))
    if (unsupported.length) {
      setError(`不支持以下文件：${unsupported.map(file => file.name).join('、')}。请选择 JPG、PNG 或 WebP 图片。`)
      if (fileRef.current) fileRef.current.value = ''
      return
    }
    uploadBusy.current = true
    const controller = new AbortController()
    uploadAbort.current = controller
    setUploadingPages(true)
    setUploadProgress(null)
    setError('')
    setNotice(`正在导入 ${selectedFiles.length} 张图片…`)
    let imported: ImagePage[] = []
    try {
      await flushPendingRegions()
      const added = await api.projects.upload(projectId, selectedFiles, setUploadProgress, controller.signal)
      imported = added
      if (controller.signal.aborted) return
      void historyRefreshRef.current()
      if (!added.length) throw new Error('服务器没有返回已导入的页面')
      await refreshPages()
      if (controller.signal.aborted) return
      setNotice(`${added.length} 张图片已导入。`)
      navigate(`/projects/${projectId}/editor/${added[0].id}`)
    } catch (reason) {
      if (controller.signal.aborted) return
      setNotice('')
      let message = reason instanceof Error ? reason.message : String(reason)
      // Earlier batches remain committed even if a later request fails. Reconcile
      // both the page list and undo history instead of hiding imported pages.
      if (reason instanceof ImageUploadError) {
        void historyRefreshRef.current()
        try {await refreshPages()} catch {message += ' 页面列表刷新失败，请刷新页面查看已导入的图片。'}
      } else if (imported.length) {
        message = `${imported.length} 张图片已导入，但页面列表刷新失败。请刷新页面查看，勿重复导入。${message}`
      }
      if (!controller.signal.aborted) setError(message)
    } finally {
      if (uploadAbort.current === controller) {
        uploadAbort.current = null
        uploadBusy.current = false
        setUploadingPages(false)
        setUploadProgress(null)
        if (fileRef.current) fileRef.current.value = ''
      }
    }
  }

  const {processPageStage, retryClassification}=createPageWorkflowCommands({
    getPage: () => page,
    getRegions: () => regionsRef.current,
    currentPageId: () => currentPageId.current,
    isBusy: () => editorBusyRef.current,
    confirmDialog,setView,setNotice,setError,setSchedulingProcess,setRunningWorkflowStage,setRunningPageTask,
    flushPendingRegions,refreshAfterTask,syncTaskProgress,
    onStart:()=>taskActivity.begin([]),
  })

  const batchRecognize = async () => {
    if (!pages.length || editorBusy || batchBusy.current) return
    const candidates = pages.filter(item => item.id !== page?.id && pageNeedsOcr(item))
    if (!candidates.length) {
      setError('')
      setNotice('其余图片都已完成 OCR，没有需要批量识别的页面。')
      return
    }
    if (!await confirmDialog({
      title: `批量识别其余 ${candidates.length} 张图片？`,
      message: '只处理尚未完成 OCR 的其他图片；已翻译页面和正在执行任务的页面会跳过。该操作不会清除文字或修改背景。',
      tone: 'info',
      confirmLabel: '开始批量识别',
    })) return
    if (editorBusyRef.current || batchBusy.current) return
    batchBusy.current = true
    taskActivity.begin([])
    blurActiveControl()
    setSchedulingBatch('ocr'); setBatchProgress(0); setError(''); setNotice(`正在批量识别其余 ${candidates.length} 页…`)
    try {
      await flushPendingRegions()
      const tasks = await api.projects.batch(projectId, {
        start_stage: 'detection',
        end_stage: 'ocr',
        force: false,
        options: {},
        image_ids: candidates.map(item => item.id),
        only_unrecognized: true,
      })
      if (!tasks.length) {
        await refreshPages()
        setNotice('其余图片都已完成 OCR，没有创建新的识别任务。')
        return
      }
      taskActivity.begin(tasks)
      let completed = 0
      const results = await waitForTasks(tasks.map(task => task.id), task => {
        if (task.status === 'COMPLETED') completed++
        setBatchProgress(completed / tasks.length)
      })
      if (!results) return
      await refreshPages()
      const failed = results.filter(task => task.status !== 'COMPLETED')
      if (failed.length) { setNotice(''); setError(`${failed.length} 个页面识别失败或取消，请检查页面状态。`) }
      else setNotice('批量检测和 OCR 已完成，请逐页核对文字区域。')
    } catch (reason) {
      setNotice(''); setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      batchBusy.current = false
      setSchedulingBatch(null)
      setBatchProgress(null)
    }
  }

  // The current page uses live regions so creating/deleting its last box updates
  // availability immediately. Other pages use the lightweight server counts.
  const batchRepairCandidates = pages.filter(item =>
    item.id === page?.id ? regions.length > 0 : (item.region_count ?? 0) > 0,
  )
  const batchRepairAndTranslate = async () => {
    if (!batchRepairCandidates.length || editorBusyRef.current || batchBusy.current) return
    const candidates = batchRepairCandidates.map(item => item.id)
    if (!await confirmDialog({
      title: `批量修复并翻译 ${candidates.length} 张图片？`,
      message: '仅处理已有文本框的页面（包含当前页），不会重新检测或重建文本框。原文为空时先执行 OCR，再重新翻译、修复背景并排版；已有译文将重新生成，手工蒙版会保留。没有文本框或正在执行任务的页面会跳过。',
      tone: 'info',
      confirmLabel: '开始批量修复并翻译',
    })) return
    if (editorBusyRef.current || batchBusy.current) return
    batchBusy.current = true
    taskActivity.begin([])
    const originatingPageId = currentPageId.current
    const originatingView = useEditorStore.getState().view
    blurActiveControl()
    setSchedulingBatch('repairTranslate'); setBatchProgress(0); setError(''); setNotice('')
    try {
      await flushPendingRegions()
      const tasks = await api.projects.batch(projectId, {
        start_stage: 'translation', end_stage: 'rendering', force: true,
        options: {rebuild_clean: true, repair_geometry: 'source'},
        image_ids: candidates, only_with_regions: true,
      })
      if (!tasks.length) {
        await refreshPages()
        setNotice('没有可处理的页面：页面需有文本框，且没有正在执行或暂停的任务。')
        return
      }
      taskActivity.begin(tasks)
      const progress = new Map(tasks.map(task => [task.id, 0]))
      let totalProgress = 0
      const results = await waitForTasks(tasks.map(task => task.id), updated => {
        const next = updated.status === 'COMPLETED' ? 1 : 0
        totalProgress += next - (progress.get(updated.id) || 0)
        progress.set(updated.id, next)
        setBatchProgress(totalProgress / tasks.length)
      })
      if (!results) return
      await reloadCurrentRef.current(false)
      await historyRefreshRef.current()
      const completed = results.filter(result => result.status === 'COMPLETED').length
      if (completed !== tasks.length) {
        setError(`批量修复并翻译：${completed}/${tasks.length} 页完成，其余页面失败、取消或无法确认结果，请检查任务状态。`)
      } else {
        if (currentPageId.current === originatingPageId && useEditorStore.getState().view === originatingView
          && tasks.some(task => task.image_id === currentPageId.current)) setView('translated')
        setNotice(`批量修复并翻译完成，共 ${completed} 页。请核对原文与译文；OCR 后仍为空的区域未送去翻译。`)
      }
    } catch (reason) {
      setNotice(''); setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      batchBusy.current = false
      setSchedulingBatch(null); setBatchProgress(null)
    }
  }

  const resetCurrentPage = async () => {
    if (!page || editorBusy) return
    if (!await confirmDialog({
      title: '重置当前页？',
      message: '当前页的文字区域、OCR、译文、蒙版、净图和排版结果都会被清除，页面将恢复到刚导入的原图状态。可以通过撤销恢复。',
      tone: 'danger',
      confirmLabel: '恢复默认',
    })) return
    blurActiveControl()
    setResettingPage(true); setError(''); setNotice('正在恢复当前页…')
    try {
      await flushPendingRegions()
      await api.images.reset(page.id)
      select(null)
      await reloadCurrent(false)
      setNotice('当前页已恢复到默认原图状态。')
    } catch (reason) {
      setNotice(''); setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setResettingPage(false)
    }
  }

  const exportProject = async (kind: ExportKind) => {
    if (editorBusy || exporting) return
    const option = EXPORT_OPTIONS.find(item => item.kind === kind)
    if (!option) return
    setExportMenuOpen(false)
    setExporting(true)
    setExportKind(kind)
    setError('')
    setNotice('')
    try {
      // Flush debounced text/style edits first so an immediate export cannot
      // race the 320 ms autosave window and package the previous rendering.
      await flushPendingRegions()
      await api.projects.export(projectId, option.formats, `${project?.name || 'mangaflow'}-${kind}.zip`)
      setNotice(`${option.label}已生成并开始下载。`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setExporting(false)
      setExportKind(null)
    }
  }

  const saveTranslationContext = async () => {
    if (savingContext) return
    setSavingContext(true)
    setContextError('')
    setError('')
    try {
      const parsed = JSON.parse(contextText)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('上下文需要是 JSON 对象')
      const updated = await api.projects.update(projectId, {translation_context: parsed,
        settings: {...project?.settings, translation_context_mode: contextMode}})
      setProject(updated)
      setShowContext(false)
    } catch (reason) {
      setContextError(reason instanceof Error ? reason.message : 'JSON 格式错误')
    } finally {
      setSavingContext(false)
    }
  }

  const selectedScope = regionProcessing ? regionProcessing.keys.length > 1 ? `${regionProcessing.keys.length} 个区域` : `区域 ${regionProcessing.keys[0]}` : '当前区域'
  const busyLabel = historyDirection ? (historyDirection === 'undo' ? '正在撤销上一步…' : '正在重做上一步…') : exporting
    ? ({project:'正在打包可编辑源项目…', translated:'正在生成并导出翻译后图片…', clean:'正在导出净图…'} as Record<ExportKind, string>)[exportKind || 'project']
    : deletingPageId
    ? '正在删除图片…'
    : updatingPageRecognitionId
      ? '正在更新图片识别状态…'
      : reorderingPages
      ? '正在保存页面顺序…'
      : uploadingPages
      ? uploadProgress ? `正在导入图片 · ${uploadProgress.completedFiles}/${uploadProgress.totalFiles} 张` : '正在准备分批导入…'
      : resettingPage
      ? '正在重置当前页…'
      : schedulingBatch
      ? schedulingBatch === 'repairTranslate' ? '正在批量修复并翻译…' : '正在批量检测并识别…'
      : schedulingProcess
        ? pageTaskBusyLabel(runningPageTask, runningWorkflowStage)
        : runningRegionAction === 'translate' && runningPageTask?.message.startsWith('翻译前自动识别')
        ? runningPageTask.message
        : ({ocr: `正在重新 OCR ${selectedScope}…`, translate: `正在重新翻译${selectedScope}…`, inpaint: `正在重新修复${selectedScope}…`, maskRepair: '正在修复修改后的蒙版…', merge: '正在合并所选区域…', delete: '正在删除所选区域…', create: '正在创建文字区域…'} as Record<string, string>)[runningRegionAction || ''] || (editQueue.pending ? '正在保存文字区域…' : '正在处理…')
  const activePageTask = observedPageTask
  const foregroundProgress = uploadingPages && uploadProgress
    ? uploadProgress.totalBytes > 0 ? uploadProgress.completedBytes / uploadProgress.totalBytes : uploadProgress.completedFiles / uploadProgress.totalFiles
    : reorderingPages
      ? reorderProgress
      : null
  const recoveredRegion = runningPageTask?.region_id ? regions.find(region => region.id === runningPageTask.region_id) : undefined
  const processingScope: ProcessingScope | undefined = regionProcessing
    ? {kind:'regions', filename:regionProcessing.filename, regionKeys:regionProcessing.keys}
    : schedulingProcess && recoveredRegion
      ? {kind:'regions', filename:page?.filename || '', regionKeys:[recoveredRegion.region_key]}
      : page && (schedulingProcess || resettingPage)
        ? {kind:'page', filename:pages.find(item => item.id === runningPageTask?.image_id)?.filename || page.filename}
        : undefined

  const headerButtonClass = `${buttonClass} !h-10 !min-h-10 px-3.5 py-0 text-[12px]`
  const workflowButtons: Array<{stage: PageWorkflowStage, shortcutId: ShortcutId, label: string, title: string, icon: typeof ScanText}> = [
    {stage: 'ocr', shortcutId:'page.workflowOcr', label: '重新检测', title: '按当前检测设置重新检测文字区域，再执行 OCR', icon: ScanText},
    {stage: 'inpainting', shortcutId:'page.workflowInpaint', label: '重新修复', title: canRunPageWorkflowStage('inpainting', regions.length) ? '刷新自动蒙版（保留手工蒙版）、修复背景并自动重新排版' : '请先创建或检测文本框', icon: Eraser},
    {stage: 'translation', shortcutId:'page.workflowTranslate', label: '重新翻译', title: canRunPageWorkflowStage('translation', regions.length) ? '重新翻译当前文本框；空原文先执行 OCR' : '请先创建或检测文本框', icon: Languages},
  ]

  shortcutHandlerRef.current = event => {
    if (event.defaultPrevented || event.isComposing || immersiveReading || dialogOpen || showContext || pageContextMenu || isEditableTarget(event.target)) return
    const shortcutId = shortcutForEvent(event, shortcuts)
    if (!shortcutId) return
    if (shortcutId === 'page.previous' || shortcutId === 'page.next') {
      event.preventDefault()
      if (event.repeat || loading || pageNavigationBusyRef.current || historyBusy.current || editQueue.failed) return
      const index = pagesRef.current.findIndex(item => item.id === currentPageId.current)
      const target = index < 0 ? undefined : pagesRef.current[index + (shortcutId === 'page.previous' ? -1 : 1)]
      if (target) navigatePage(target.id)
      return
    }
    if (maskSession) return
    event.preventDefault()
    const repeatable = shortcutId.startsWith('region.nudge') || shortcutId.startsWith('zoom.')
    const viewingShortcut = shortcutId === 'tool.pan' || shortcutId.startsWith('zoom.') || shortcutId.startsWith('view.')
    const canViewWhileProcessing = viewingShortcut && !foregroundBusy && !switchingPage && !openingMask && !editQueue.failed
    if ((event.repeat && !repeatable) || editorBusy && !canViewWhileProcessing || historyBusy.current) return

    const store = useEditorStore.getState()
    if (shortcutId === 'tool.pan') store.setTool('pan')
    else if (shortcutId === 'tool.select') store.setTool('select')
    else if (shortcutId === 'tool.rectangle') store.setTool('rectangle')
    else if (shortcutId === 'tool.polygon') store.setTool('polygon')
    else if (shortcutId === 'tool.lasso') store.setTool('lasso')
    else if (shortcutId === 'view.original') store.setView('original')
    else if (shortcutId === 'view.clean') store.setView('clean')
    else if (shortcutId === 'view.translated') store.setView('translated')
    else if (shortcutId === 'view.comparison') store.setView('comparison')
    else if (shortcutId === 'edit.undo') {
      if (editQueue.pending || hasPendingEdits || editorHistory.state?.undo_count) void applyHistory('undo')
    } else if (shortcutId === 'edit.redo') {
      if (!editQueue.pending && !hasPendingEdits && editorHistory.state?.redo_count) void applyHistory('redo')
    } else if (shortcutId === 'edit.selectAll') {
      store.selectMany(regionsRef.current.map(region => region.id))
    } else if (shortcutId === 'zoom.out') store.setZoom(store.zoom / 1.15)
    else if (shortcutId === 'zoom.in') store.setZoom(store.zoom * 1.15)
    else if (shortcutId === 'zoom.reset') store.resetView()
    else if (shortcutId === 'region.delete') {
      if (selectedIds.length) void deleteSelectedRegion()
    } else if (shortcutId === 'region.cancelSelection') store.select(null)
    else if (shortcutId === 'region.merge') {
      if (selectedIds.length > 1) void action('merge')
    } else if (shortcutId === 'region.ocr' || shortcutId === 'region.translate' || shortcutId === 'region.inpaint') {
      if (selectedIds.length) void action(({'region.ocr':'ocr', 'region.translate':'translate', 'region.inpaint':'inpaint'} as const)[shortcutId])
    } else if (shortcutId.startsWith('region.nudge')) {
      if (tool !== 'select' || view === 'comparison' || !selectedIds.length) return
      const direction = ({
        'region.nudgeLeft':[-1, 0],
        'region.nudgeRight':[1, 0],
        'region.nudgeUp':[0, -1],
        'region.nudgeDown':[0, 1],
      } as Record<string, [number, number]>)[shortcutId]
      if (!direction) return
      const distance = event.shiftKey ? 10 : 1
      const selected = new Set(selectedIds)
      const selectedRegions = regionsRef.current.filter(region => selected.has(region.id) && !region.locked)
      const delta = constrainGroupMove(selectedRegions,view === 'translated',direction[0]*distance,direction[1]*distance,page?.width || 0,page?.height || 0)
      if (!delta.x && !delta.y) return
      const changes = moveRegions(selectedRegions,view === 'translated',delta.x,delta.y)
      void updateManyRegions(changes).catch(() => undefined)
    } else if (shortcutId === 'page.workflowOcr') {
      if (page) void processPageStage('ocr')
    } else if (shortcutId === 'page.workflowTranslate') {
      if (page && canRunPageWorkflowStage('translation', regions.length)) void processPageStage('translation')
    } else if (shortcutId === 'page.workflowInpaint') {
      if (page && canRunPageWorkflowStage('inpainting', regions.length)) void processPageStage('inpainting')
    } else if (shortcutId === 'page.import') openImagePicker()
    else if (shortcutId === 'page.context') setShowContext(true)
    else if (shortcutId === 'page.export') {
      if (pages.length) setExportMenuOpen(open => !open)
    } else if (shortcutId === 'page.batchOcr') void batchRecognize()
    else if (shortcutId === 'page.reset') void resetCurrentPage()
  }

  const contextPage = pageContextMenu ? page?.id === pageContextMenu.pageId ? page : pages.find(item => item.id === pageContextMenu.pageId) : undefined
  const contextPageReady = !!contextPage && contextPage.id === page?.id && !editorBusy
  const contextMenuItemClass = 'flex min-h-9 w-full cursor-pointer items-center gap-2.5 rounded-lg border-0 bg-transparent px-3 py-2 text-left text-[14px] leading-5 text-secondary outline-none transition-colors hover:bg-hover hover:text-ink focus-visible:bg-hover focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 disabled:cursor-not-allowed disabled:opacity-40'
  const selectedPageIndex = pages.findIndex(item => item.id === selectedPageId)
  const previousPage = selectedPageIndex > 0 ? pages[selectedPageIndex - 1] : undefined
  const nextPage = selectedPageIndex >= 0 ? pages[selectedPageIndex + 1] : undefined
  if (!project && !page && !showingInitialLoading) return <div className="grid h-full place-content-center text-center"><h2>无法打开项目</h2><p className="text-muted">{error}</p><Link className="text-accent" to="/projects">返回项目</Link></div>
  return <>
    {editorTarget && createPortal(<>
      <EditorHeaderActions>
        <button className={headerButtonClass} onClick={() => setShowContext(true)} disabled={editorBusy || showingInitialLoading} title={`翻译上下文 (${formatShortcut(shortcuts['page.context'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['page.context'])}><BookOpenText size={16}/>翻译上下文</button>
        <div className="relative" ref={exportMenuRef}>
          <button className={headerButtonClass} disabled={editorBusy || showingInitialLoading || exporting} title={`导出 (${formatShortcut(shortcuts['page.export'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['page.export'])} aria-haspopup="menu" aria-expanded={exportMenuOpen} onClick={() => setExportMenuOpen(open => !open)}>{exporting ? <ButtonLoading label="导出中…"/> : <><Download size={16}/>导出<ChevronDown className={cn('transition-transform', exportMenuOpen && 'rotate-180')} size={13}/></>}</button>
          {exportMenuOpen && <div className="absolute right-0 top-[calc(100%+8px)] z-[90] w-[292px] overflow-hidden rounded-xl border border-line-strong bg-popover p-1.5 text-secondary shadow-dialog backdrop-blur-xl" role="menu" aria-label="选择导出内容">
            {EXPORT_OPTIONS.map(option => {
              const unavailable = !pages.length
              const Icon = option.kind === 'project' ? Archive : option.kind === 'translated' ? ImageIcon : Eraser
              return <button key={option.kind} type="button" role="menuitem" disabled={unavailable} className="flex min-h-[58px] w-full cursor-pointer items-center gap-3 rounded-lg border-0 bg-transparent px-3 py-2 text-left outline-none transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:opacity-35" onClick={() => void exportProject(option.kind)}>
                <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-accent/10 text-accent"><Icon size={16}/></span>
                <span className="min-w-0"><strong className="block text-[11px] font-semibold text-ink">{option.label}</strong><small className="mt-1 block text-[9px] leading-relaxed text-muted">{option.detail}</small></span>
              </button>
            })}
          </div>}
        </div>
        <button className={headerButtonClass} onClick={() => void batchRecognize()} disabled={editorBusy || showingInitialLoading} title={`只识别当前页之外尚未完成 OCR 的图片 (${formatShortcut(shortcuts['page.batchOcr'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['page.batchOcr'])}>{schedulingBatch === 'ocr' ? <ButtonLoading label="批量识别中…"/> : <><Layers3 size={16}/>批量识别</>}</button>
        <button className={headerButtonClass} onClick={() => void batchRepairAndTranslate()} disabled={editorBusy || showingInitialLoading || !batchRepairCandidates.length} title={batchRepairCandidates.length ? '修复并翻译已有文本框的页面；空原文会先执行 OCR' : '至少一张图片有文本框后可用'}>{schedulingBatch === 'repairTranslate' ? <ButtonLoading label="批量修复并翻译中…"/> : <><Languages size={16}/>批量修复并翻译</>}</button>
        <button className={headerButtonClass} disabled={editorBusy || showingInitialLoading || !page} title="只阅读翻译后的漫画；点击任意位置退出，滚轮上下翻页" onClick={() => {blurActiveControl();setExportMenuOpen(false);setPageContextMenu(null);setImmersiveReading(true)}}><Maximize size={16}/>沉浸式阅读</button>
      </EditorHeaderActions>
    </>, editorTarget)}
    {immersiveReading && page && <ImmersiveReader page={page} regions={regions} busy={pageNavigationBusy}
      error={selectedPageId !== page.id && !switchingPage ? error : undefined} onClose={closeImmersiveReader}
      onPreviousPage={!pageNavigationBusy && previousPage ? () => navigatePage(previousPage.id) : undefined}
      onNextPage={!pageNavigationBusy && nextPage ? () => navigatePage(nextPage.id) : undefined}/>}
    <div hidden={immersiveReading && !!page} className="editor-content-enter grid h-full grid-rows-[auto_minmax(0,1fr)] bg-canvas">
    <EditorToolbar
      onUndo={() => maskSession ? maskEditorRef.current?.undo() : applyHistory('undo')}
      onRedo={() => maskSession ? maskEditorRef.current?.redo() : applyHistory('redo')}
      canUndo={maskSession ? !editorHistory.state?.pending && (maskState.undo || !maskState.syncing && (hasPendingEdits || !!editorHistory.state?.undo_count)) : !editorHistory.state?.pending && (editQueue.pending || hasPendingEdits || !!editorHistory.state?.undo_count)}
      canRedo={maskSession ? !editorHistory.state?.pending && !hasPendingEdits && (maskState.redo || !maskState.syncing && !maskState.dirty && !!editorHistory.state?.redo_count) : !editorHistory.state?.pending && !editQueue.pending && !hasPendingEdits && !!editorHistory.state?.redo_count}
      disabled={foregroundBusy || editQueue.failed || switchingPage || openingMask || maskState.busy || maskRepairBusy.current}
      readOnly={blockingBusy || projectHasTasks}
      onViewChange={changeView}
      centerContent={error || !blockingBusy ? <EditorToolbarFeedback error={error} notice={notice} onDismiss={() => {setError(''); setNotice('')}}/> : null}
    />
    {showingInitialLoading ? <EditorSkeleton canvasControls={<CanvasZoomControls/>}/> : <EditorWorkspace pageId={selectedPageId} selectedCount={maskSession ? 0 : selectedIds.length} propertiesLabel={maskSession ? '蒙版' : undefined} propertiesTitle={maskSession ? '蒙版设置' : undefined} canvasPromptOpen={maskState.confirming}
      onPreviousPage={!pageNavigationBusy && previousPage ? () => navigatePage(previousPage.id) : undefined}
      onNextPage={!pageNavigationBusy && nextPage ? () => navigatePage(nextPage.id) : undefined} pages={
      <aside className="flex min-h-0 flex-col border-r border-line-subtle bg-panel">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-line-subtle px-3.5 text-[13px] font-semibold text-secondary"><span>页面</span><button className={cn(buttonClass, '!size-8 !min-h-8 !p-0 border-0 bg-transparent')} disabled={editorBusy} title={`导入图片 (${formatShortcut(shortcuts['page.import'])})`} aria-label="导入图片" aria-keyshortcuts={shortcutToAria(shortcuts['page.import'])} onClick={openImagePicker}>{uploadingPages ? <ButtonLoading compact label="正在导入图片"/> : <ImagePlus aria-hidden="true" size={16}/>}</button></div>
        <VirtualPageList items={pages} selectedPageId={selectedPageId} listRef={pageListRef} className={cn('min-h-0 flex-1 overflow-auto p-2.5', scrollbarClass)} onContextMenu={openImagePickerFromBlank}
          empty={<div className="flex min-h-full items-center justify-center"><button className={`${buttonClass} min-h-[140px] w-full flex-col border-dashed bg-transparent text-[12px] text-muted`} disabled={editorBusy} onClick={openImagePicker}><Upload size={22}/>导入漫画图片</button></div>}>{(item, index) => {
          const dropDirection = pageDrag && pageDrag.overIndex === index && pageDrag.fromIndex !== index
            ? (pageDrag.overIndex > pageDrag.fromIndex ? 'after' : 'before')
            : null
          return <PageListItem
            key={item.id}
            item={item}
            index={index}
            selected={selectedPageId === item.id}
            dragged={pageDrag?.pageId === item.id}
            dropDirection={dropDirection}
            activeTask={activePageTask?.image_id === item.id ? activePageTask : null}
            onActivate={activatePage}
            onPointerDown={beginPagePress}
            onContextMenu={openPageContextMenu}
          />
        }}</VirtualPageList>
      </aside>} canvas={
      <main className="editor-task-canvas relative min-h-0 min-w-0 workspace-background">
        <CanvasTaskList activity={taskActivity} pages={pages} onCancelTask={cancelTask} scope={processingScope} pendingLabel={syncingTaskResult ? '正在同步任务结果…' : schedulingProcess || backgroundRegionAction ? busyLabel : undefined}
          batch={schedulingBatch ? {label:schedulingBatch === 'repairTranslate' ? '批量修复并翻译' : '批量检测并识别', progress:batchProgress} : null}/>
        {page && !maskSession && <ClassificationStatus summary={page.text_classification} busy={editorBusy} onRetry={() => void retryClassification()}/>}
        {maskSession ? maskSession.region ? <MaskEditor ref={maskEditorRef} prepared={maskSession.prepared} preferences={maskPreferences} controlsTarget={maskControlsTarget} key={`${maskSession.page.id}:${maskSession.region.id}:${maskSession.version || 0}`} page={maskSession.page} region={maskSession.region} regions={maskSession.regions} tool={tool} operation={maskOperation} disabled={blockingBusy || switchingPage || openingMask} onToolChange={setTool} onSave={saveMask} onStateChange={setMaskState} onHistoryStep={syncMaskHistory} onHistoryBoundary={direction => void applyHistory(direction)} onSelectRegion={id => void selectMaskRegion(id)}/>
          : <div className="grid h-full place-items-center p-6 text-sm text-muted">当前页暂无文字区域，请切回原图识别或创建区域后编辑蒙版。</div>
          : page ? !immersiveReading && <MangaCanvas page={page} regions={regions} onCreate={createRegion} onUpdate={(id,patch) => updateRegion(id,patch,true)}
            onUpdateMany={updateManyRegions} onRegionAction={(ids, name, options) => {void action(name, options, ids)}} onPageContextMenu={openCanvasPageMenu}
            processingRegionIds={regionProcessing?.ids} runningAction={historyDirection ? 'history' : runningRegionAction || (editorBusy ? 'busy' : null)} interactionKey={`${view}:${tool}:${editorBusy}:${selectedIds.join(',')}`}/>
          : <div className="flex h-full flex-col items-center justify-center text-muted"><ImagePlus size={36}/><h3 className="mb-1 mt-3 text-base text-secondary">导入漫画页面</h3><p className="mb-4 mt-0 text-xs">支持 JPG、PNG 和 WebP，可一次选择多张。</p><button className={`${primaryButtonClass} !min-h-[34px]`} disabled={editorBusy} onClick={openImagePicker}>选择图片</button></div>}
        {!maskSession && <CanvasZoomControls disabled={foregroundBusy || switchingPage || openingMask}/>}
      </main>} properties={
      maskSession ? <div ref={setMaskControlsTarget} className="mask-inspector-host"/> : <RegionProperties page={page} regions={regions} region={selectedIds.length === 1 ? regions.find(region => region.id === selectedIds[0]) : undefined} selectedRegions={regions.filter(region => selectedIds.includes(region.id))} selectedCount={selectedIds.length} fontOptions={[...DEFAULT_FONT_FAMILIES.map(font => font.name), ...fontResources.map(font => font.name)]} busyAction={editorBusy ? historyDirection ? 'history' : runningRegionAction || 'busy' : null} onUpdate={updateRegion} onUpdateMany={changes => {void updateManyRegions(changes).catch(() => undefined)}} onAction={action}/>}/>}
    {pageContextMenu && createPortal(<div ref={pageContextMenuRef} className="page-context-menu fixed z-[110] w-[224px] max-w-[calc(100vw-16px)] max-h-[calc(100dvh-16px)] overflow-y-auto rounded-xl border border-line-strong bg-popover p-1 text-secondary shadow-dialog backdrop-blur-xl" style={{left: pageContextMenu.x, top: pageContextMenu.y}} role="menu" aria-label={pageContextMenu.source === 'canvas' ? '单页操作' : '图片操作'}>
      <div className="flex min-h-9 flex-col justify-center gap-1 border-b border-line-subtle px-3 py-2">
        {pageContextMenu.source === 'canvas' && <strong className="text-[14px] text-ink">单页操作 <span className="ml-1 text-[12px] font-normal text-muted">本页全部区域</span></strong>}
        <span className="min-w-0 truncate text-[13px] text-muted" title={contextPage?.filename}>{contextPage?.filename}</span>
      </div>
      <div className="py-1" role="group" aria-label="页面处理操作">{workflowButtons.map(({stage, shortcutId, label, title, icon: Icon}) => <button
        key={stage} type="button" role="menuitem" className={contextMenuItemClass}
        disabled={!contextPageReady || !canRunPageWorkflowStage(stage, regions.length)}
        title={title} aria-keyshortcuts={shortcutToAria(shortcuts[shortcutId])}
        onClick={() => {if (!contextPageReady || currentPageId.current !== pageContextMenu.pageId) return; setPageContextMenu(null); void processPageStage(stage)}}
      ><Icon size={15} aria-hidden="true"/><span>{label}</span></button>)}
      </div>
      <div className="border-t border-line-subtle py-1">
        {pageContextMenu.source !== 'canvas' && <button className={contextMenuItemClass} type="button" role="menuitem" disabled={!contextPageReady || (!!contextPage && !contextPage.ocr_exempt && !pageNeedsOcr(contextPage))} onClick={() => void togglePageOcrExempt(pageContextMenu.pageId)}><CheckCircle2 size={15} className="text-accent"/>{contextPage?.ocr_exempt ? '取消已翻译' : '已翻译'}</button>}
        <button className={cn(contextMenuItemClass, '!text-danger-soft-ink hover:!bg-danger/10')} type="button" role="menuitem" disabled={!contextPageReady || (contextPage?.status === 'UPLOADED' && !regions.length && !contextPage.clean_url && !contextPage.rendered_url)} onClick={() => {if (!contextPageReady || currentPageId.current !== pageContextMenu.pageId) return; setPageContextMenu(null); void resetCurrentPage()}}><RotateCcw size={15}/>重置当前页</button>
        {pageContextMenu.source !== 'canvas' && <button className={cn(contextMenuItemClass, '!text-danger-soft-ink hover:!bg-danger/10')} type="button" role="menuitem" disabled={!contextPageReady} onClick={() => void deletePage(pageContextMenu.pageId)}><Trash2 size={15}/>删除图片</button>}
      </div>
    </div>, document.body)}
    {showContext && createPortal(<div ref={contextDialogRef} data-saving={savingContext} className="fixed inset-0 z-[90] grid place-items-center bg-overlay backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="translation-context-title">
      <div className="flex max-h-[90dvh] w-[min(680px,calc(100vw-24px))] flex-col overflow-y-auto rounded-2xl border border-line-strong bg-surface shadow-dialog">
        <header className="flex items-center justify-between border-b border-line-subtle px-6 py-5"><div><span className={eyebrowClass}>PROJECT CONTEXT</span><h2 className="mb-0 mt-1.5 text-xl" id="translation-context-title">翻译上下文</h2></div><button className={cn(iconButtonClass, 'border-0 bg-transparent text-xl')} disabled={savingContext} aria-label="关闭翻译上下文" onClick={() => setShowContext(false)}>×</button></header>
        <fieldset className="mx-6 mt-5 rounded-xl border border-line p-3 text-sm" disabled={savingContext}>
          <legend>批量翻译的上下文模式</legend>
          <label className="flex items-center gap-2"><input type="radio" name="context-mode" value="snapshot" checked={contextMode === 'snapshot'} onChange={() => setContextMode('snapshot')}/>提交时快照（默认）</label>
          <p className="ml-6 mt-1 text-xs leading-5 text-muted">使用提交批次时已有的上下文，各页可以并发翻译。</p>
          <label className="flex items-center gap-2"><input type="radio" name="context-mode" value="previous_page" checked={contextMode === 'previous_page'} onChange={() => setContextMode('previous_page')}/>依赖前页翻译</label>
          <p className="mb-0 ml-6 mt-1 text-xs leading-5 text-muted">等待同一批次中紧邻前页的新译文，不等待它修复完成。连贯性更强，但翻译并行度较低；前页不在本批次时使用提交时快照。只影响新建批次。</p>
        </fieldset>
        <p className="px-6 text-[13px] leading-6 text-muted">以 JSON 保存人物名、术语、口癖、称谓和章节背景；整页翻译时会随 Region ID 一起发送。</p>
        {contextError && <p className="px-6 text-xs text-danger" role="alert">{contextError}</p>}
        <textarea aria-label="项目翻译上下文 JSON" className={cn(textareaClass, 'mx-6 mb-6 min-h-[120px] flex-1 w-[calc(100%-48px)] font-mono text-[12px] leading-relaxed')} disabled={savingContext} value={contextText} onChange={event => setContextText(event.target.value)} spellCheck={false}/>
        <footer className="flex justify-end gap-2.5 border-t border-line-subtle px-6 py-4"><button className={buttonClass} disabled={savingContext} onClick={() => setShowContext(false)}>取消</button><button className={primaryButtonClass} disabled={savingContext} onClick={() => void saveTranslationContext()}>{savingContext ? <ButtonLoading label="保存中…"/> : '保存上下文'}</button></footer>
      </div>
    </div>, document.body)}
    {(foregroundBusy || openingMask || switchingPage || maskState.busy) && <BlockingLoader label={recoveringEdits?'正在重新同步编辑状态…':waitingForEdits?'正在保存编辑并离开…':openingMask?'正在准备蒙版…':switchingPage?'正在切换页面…':maskState.busy?'正在保存蒙版并切换…':busyLabel} progress={foregroundBusy?foregroundProgress:undefined} scope={foregroundBusy?processingScope:undefined}
      detail={uploadingPages && uploadProgress ? uploadProgress.batchFiles
        ? `${uploadProgress.resizing ? '请求过大，已自动缩小批次。' : ''}当前批次 ${uploadProgress.batchFiles} 张，${(uploadProgress.batchBytes / 1024 / 1024).toFixed(1)} MiB。将自动继续下一批，请勿关闭页面。`
        : '图片已导入，正在更新页面列表…' : undefined}/>}
    {editQueue.failed && !recoveringEdits && <div className="fixed inset-0 z-[120] grid place-items-center bg-overlay backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="编辑同步失败"><div className="w-[min(420px,calc(100vw-24px))] rounded-2xl border border-line-strong bg-panel p-6 shadow-dialog"><h2 className="mt-0 text-base">编辑同步失败</h2><p className="text-xs leading-relaxed text-muted">{error}</p><button className={primaryButtonClass} onClick={()=>{void recoverEditingQueue(new Error('编辑状态已重新同步，请核对后继续。'))}}>重试同步</button></div></div>}
    <input ref={fileRef} disabled={editorBusy} hidden type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" multiple onChange={event => void upload(event.currentTarget.files)}/>
  </div>
  </>
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}
