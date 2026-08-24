import { Archive, BookOpenText, CheckCircle2, ChevronDown, Download, Eraser, Image as ImageIcon, ImagePlus, Languages, Layers3, RotateCcw, ScanText, TextCursorInput, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAppHeaderSlots } from '../components/AppShell'
import { useGlobalDialog } from '../components/GlobalDialog'
import { BlockingLoader, ButtonLoading, CircularProgress, EditorSkeleton, useMinimumLoadingTime } from '../components/LoadingUI'
import { DEFAULT_FONT_FAMILIES } from '../constants/fonts'
import { CanvasZoomControls, EditorToolbar } from '../features/editor/components/EditorToolbar'
import { MangaCanvas } from '../features/editor/components/MangaCanvas'
import { RegionProperties } from '../features/editor/components/RegionProperties'
import {preloadImage, versionedImageSource} from '../features/editor/hooks/useImage'
import {buildPageWorkflowRequest, pageWorkflowTargetView, type PageWorkflowStage} from '../features/editor/pageWorkflow'
import { useEditorStore } from '../features/editor/store'
import {formatShortcut, shortcutForEvent, shortcutToAria, type ShortcutId, useShortcutStore} from '../features/shortcuts/store'
import { ApiError, api } from '../services/api'
import type { FontResource, ImagePage, ProcessingTask, Project, TextRegion } from '../types'
import {buttonClass, cn, dangerButtonClass, eyebrowClass, iconButtonClass, primaryButtonClass, scrollbarClass, textareaClass} from '../ui'

type HistoryEntry = {id: string, before: TextRegion, after: TextRegion}
type PendingChange = {before: TextRegion, patch: Partial<TextRegion>, timer: number, version: number}
type TaskUpdateHandler = (task: ProcessingTask) => void | Promise<void>
type PageContextMenu = {x: number, y: number, pageId: string}
type PagePress = {pageId: string, fromIndex: number, overIndex: number, pointerId: number, startX: number, startY: number, active: boolean}
type PageDrag = {pageId: string, fromIndex: number, overIndex: number}
type ExportKind = 'project' | 'translated' | 'clean'

const EXPORT_OPTIONS: Array<{kind: ExportKind, label: string, detail: string, formats: string[]}> = [
  {kind: 'project', label: '导出源项目', detail: '完整工程包，可再次导入 MangaFlow 继续编辑', formats: ['project']},
  {kind: 'translated', label: '导出翻译后图片', detail: '仅打包所有页面的最终译图', formats: ['translated']},
  {kind: 'clean', label: '导出净图', detail: '未修复的页面会自动使用原图', formats: ['clean']},
]

const PAGE_DRAG_START_THRESHOLD = 3

function preserveLocalRegionGeometry(nextRegions: TextRegion[], currentRegions: TextRegion[]) {
  if (!currentRegions.length) return nextRegions
  const currentById = new Map(currentRegions.map(region => [region.id, region]))
  return nextRegions.map(region => {
    const current = currentById.get(region.id)
    if (!current) return region
    return {
      ...region,
      bbox: [...current.bbox],
      polygon: current.polygon.map(point => [...point]),
      translated_bbox: [...current.translated_bbox],
      translated_polygon: current.translated_polygon.map(point => [...point]),
      rotation: current.rotation,
      perspective_warp: current.perspective_warp,
    }
  })
}

/**
 * A same-page response can have been requested before a local create/delete
 * finished. In that case the current ID set is newer than the response: merge
 * fresh server fields into matching regions without resurrecting or dropping
 * regions from the newer local list.
 */
function reconcileRegionSnapshot(nextRegions: TextRegion[], currentRegions: TextRegion[]) {
  const nextById = new Map(nextRegions.map(region => [region.id, region]))
  return currentRegions.map(current => {
    const next = nextById.get(current.id)
    return next ? preserveLocalRegionGeometry([next], [current])[0] : current
  })
}

function samePolygon(first: number[][], second: number[][]) {
  return first.length === second.length && first.every((point, index) => {
    const other = second[index]
    return point.length >= 2 && other?.length >= 2 &&
      Math.abs(point[0] - other[0]) < .001 && Math.abs(point[1] - other[1]) < .001
  })
}

export function EditorPage() {
  const {projectId = '', imageId} = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [project, setProject] = useState<Project | null>(null)
  const [pages, setPages] = useState<ImagePage[]>([])
  const [page, setPage] = useState<ImagePage | null>(null)
  const [regions, setRegions] = useState<TextRegion[]>([])
  const [fontResources, setFontResources] = useState<FontResource[]>([])
  const regionsRef = useRef<TextRegion[]>([])
  const regionStateRevision = useRef(0)
  const pageLoadGeneration = useRef(0)
  const currentPageId = useRef<string | null>(null)
  const loadedProjectId = useRef<string | null>(null)
  const pending = useRef<Map<string, PendingChange>>(new Map())
  const regionEditVersions = useRef<Map<string, number>>(new Map())
  const regionSaveQueues = useRef<Map<string, Promise<TextRegion | undefined>>>(new Map())
  const [undoStack, setUndoStack] = useState<HistoryEntry[]>([])
  const [redoStack, setRedoStack] = useState<HistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [switchingPage, setSwitchingPage] = useState(false)
  const showingInitialLoading = useMinimumLoadingTime(loading, 420)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [runningRegionAction, setRunningRegionAction] = useState<string | null>(null)
  const [showContext, setShowContext] = useState(false)
  const [contextText, setContextText] = useState('{}')
  const [schedulingProcess, setSchedulingProcess] = useState(false)
  const [runningWorkflowStage, setRunningWorkflowStage] = useState<PageWorkflowStage | null>(null)
  const [runningPageTask, setRunningPageTask] = useState<ProcessingTask | null>(null)
  const [schedulingBatch, setSchedulingBatch] = useState(false)
  const [batchProgress, setBatchProgress] = useState<number | null>(null)
  const [resettingPage, setResettingPage] = useState(false)
  const [uploadingPages, setUploadingPages] = useState(false)
  const [deletingPageId, setDeletingPageId] = useState<string | null>(null)
  const [updatingPageRecognitionId, setUpdatingPageRecognitionId] = useState<string | null>(null)
  const [reorderingPages, setReorderingPages] = useState(false)
  const [reorderProgress, setReorderProgress] = useState<number | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportKind, setExportKind] = useState<ExportKind | null>(null)
  const [exportMenuOpen, setExportMenuOpen] = useState(false)
  const exportMenuRef = useRef<HTMLDivElement>(null)
  const [savingContext, setSavingContext] = useState(false)
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
  const taskMonitorGeneration = useRef(0)
  const recoveredTaskId = useRef<string | null>(null)
  const taskSyncVersion = useRef('')
  const shortcutHandlerRef = useRef<(event: KeyboardEvent) => void>(() => undefined)
  const {selectedIds, select, setTool, setView, tool, view} = useEditorStore()
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const {confirm: confirmDialog, isOpen: dialogOpen} = useGlobalDialog()
  const {editorTarget} = useAppHeaderSlots()
  const blockingBusy = schedulingProcess || schedulingBatch || resettingPage || uploadingPages || !!deletingPageId || !!updatingPageRecognitionId || reorderingPages || exporting || !!runningRegionAction
  const editorBusy = blockingBusy || switchingPage

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => shortcutHandlerRef.current(event)
    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  }, [])

  const setRegionState = useCallback((next: TextRegion[]) => {
    regionStateRevision.current += 1
    regionsRef.current = next
    setRegions(next)
  }, [])
  const refreshPages = useCallback(async () => {
    const next = await api.projects.images(projectId)
    setPages(next)
    if (page) setPage(next.find(item => item.id === page.id) || page)
    return next
  }, [projectId, page?.id])
  const loadPage = useCallback(async (id: string, preservedSelection?: string | null, preserveGeometry = false) => {
    const generation = ++pageLoadGeneration.current
    const revisionBeforeRequest = regionStateRevision.current
    const samePageBeforeRequest = currentPageId.current === id
    const [nextPage, nextRegions] = await Promise.all([api.images.get(id), api.images.regions(id)])
    const editorStore = useEditorStore.getState()
    let initialView = editorStore.view
    // Before the first repair, users are reviewing detector/OCR geometry.
    // Open that workflow on the source frame so dragging a box cannot be
    // mistaken for editing the independent translated layout frame.
    if (!nextPage.clean_url && initialView === 'translated') {
      initialView = 'original'
      editorStore.setView(initialView)
    }
    const cleanSource = nextPage.clean_url ? versionedImageSource(nextPage.clean_url, nextPage.updated_at) : nextPage.original_url
    const initialSources = initialView === 'comparison'
      ? [nextPage.original_url, cleanSource]
      : [initialView === 'original' ? nextPage.original_url : cleanSource]
    await Promise.all(initialSources.map(source => preloadImage(source).catch(() => undefined)))
    if (generation !== pageLoadGeneration.current) return nextRegions
    const localStateChanged = samePageBeforeRequest && currentPageId.current === id && revisionBeforeRequest !== regionStateRevision.current
    const resolvedRegions = localStateChanged
      ? reconcileRegionSnapshot(nextRegions, regionsRef.current)
      : preserveGeometry ? preserveLocalRegionGeometry(nextRegions, regionsRef.current) : nextRegions
    currentPageId.current = id
    setPage(nextPage); setRegionState(resolvedRegions); setUndoStack([]); setRedoStack([])
    const target = preservedSelection === undefined ? searchParams.get('region') : preservedSelection
    select(target && resolvedRegions.some(region => region.id === target) ? target : null)
    return resolvedRegions
  }, [searchParams, select])

  useEffect(() => {
    let disposed = false
    const initialProjectLoad = loadedProjectId.current !== projectId
    if (initialProjectLoad) {
      setLoading(true)
      setSwitchingPage(false)
    } else {
      setSwitchingPage(true)
      select(null)
    }
    const load = async () => {
      try {
        const [nextProject, nextPages, globalFonts, projectFonts] = await Promise.all([
          api.projects.get(projectId),
          api.projects.images(projectId),
          api.fonts.list().catch(() => []),
          api.projects.fonts(projectId).catch(() => []),
        ])
        if (disposed) return
        setProject(nextProject); setPages(nextPages); setContextText(JSON.stringify(nextProject.translation_context || {}, null, 2))
        setFontResources([...new Map([...projectFonts, ...globalFonts].map(font => [font.name, font])).values()])
        const target = imageId || nextPages[0]?.id
        if (target) {
          if (!imageId) navigate(`/projects/${projectId}/editor/${target}`, {replace: true})
          await loadPage(target)
        }
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (!disposed) {
          loadedProjectId.current = projectId
          if (initialProjectLoad) setLoading(false)
          else setSwitchingPage(false)
        }
      }
    }
    void load()
    return () => {
      disposed = true
      pageLoadGeneration.current += 1
    }
  }, [projectId, imageId])

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
      }).catch(() => undefined)
    }
    return () => {
      disposed = true
      registered.forEach(font => document.fonts.delete(font))
    }
  }, [fontResources])

  useEffect(() => () => pending.current.forEach(change => window.clearTimeout(change.timer)), [])

  useEffect(() => {
    if (editorBusy || (!error && !notice)) return
    const timer = window.setTimeout(() => {
      setError('')
      setNotice('')
    }, 3000)
    return () => window.clearTimeout(timer)
  }, [editorBusy, error, notice])

  useEffect(() => {
    if (!pageContextMenu) return
    const dismiss = (event: PointerEvent) => {
      if (pageContextMenuRef.current?.contains(event.target as Node)) return
      setPageContextMenu(null)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setPageContextMenu(null) }
    const close = () => setPageContextMenu(null)
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

  useEffect(() => setPageContextMenu(null), [page?.id])

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
    const previousSave = regionSaveQueues.current.get(id)
    const save = (previousSave ? previousSave.catch(() => undefined) : Promise.resolve()).then(async () => {
      try {
        const updated = await api.regions.update(id, change.patch)
        // A slower save must not paint its response over a newer drag that is
        // already visible locally. The queued newer save will apply next.
        if ((regionEditVersions.current.get(id) || 0) === change.version) {
          setRegionState(regionsRef.current.map(region => region.id === id ? updated : region))
        }
        setUndoStack(stack => [...stack.slice(-99), {id, before: change.before, after: updated}]); setRedoStack([])
        return updated
      } catch (reason) {
        if ((regionEditVersions.current.get(id) || 0) === change.version) {
          setRegionState(regionsRef.current.map(region => region.id === id ? change.before : region))
        }
        setError(reason instanceof Error ? reason.message : String(reason))
        throw reason
      }
    })
    regionSaveQueues.current.set(id, save)
    void save.then(
      () => { if (regionSaveQueues.current.get(id) === save) regionSaveQueues.current.delete(id) },
      () => { if (regionSaveQueues.current.get(id) === save) regionSaveQueues.current.delete(id) },
    )
    return save
  }

  const updateRegion = async (id: string, patch: Partial<TextRegion>) => {
    const current = regionsRef.current.find(region => region.id === id)
    if (!current) return
    const existing = pending.current.get(id)
    if (existing) window.clearTimeout(existing.timer)
    const before = existing?.before || current
    const mergedPatch = {...existing?.patch, ...patch}
    const version = (regionEditVersions.current.get(id) || 0) + 1
    regionEditVersions.current.set(id, version)
    setRegionState(regionsRef.current.map(region => region.id === id ? {...region, ...patch} : region))
    const change: PendingChange = {before, patch: mergedPatch, timer: 0, version}
    change.timer = window.setTimeout(() => {void commitPendingRegion(id, change).catch(() => undefined)}, 320)
    pending.current.set(id, change)
  }

  const flushPendingRegions = async (regionIds?: string[]) => {
    const requestedIds = regionIds ? new Set(regionIds) : null
    while (true) {
      const changes = [...pending.current.entries()].filter(([id]) => !requestedIds || requestedIds.has(id))
      if (changes.length) await Promise.all(changes.map(([id, change]) => commitPendingRegion(id, change)))
      const saves = [...regionSaveQueues.current.entries()]
        .filter(([id]) => !requestedIds || requestedIds.has(id))
        .map(([, save]) => save)
      if (saves.length) await Promise.all(saves)
      const hasPending = [...pending.current.keys()].some(id => !requestedIds || requestedIds.has(id))
      const hasSaving = [...regionSaveQueues.current.keys()].some(id => !requestedIds || requestedIds.has(id))
      if (!hasPending && !hasSaving) return
    }
  }

  const requireSourceGeometryForFirstRepair = (targetRegions: TextRegion[]) => {
    const hasUnreviewedSeparateGeometry = view === 'translated' && targetRegions.some(region =>
      !region.mask_url && !samePolygon(region.polygon, region.translated_polygon))
    if (!hasUnreviewedSeparateGeometry) return false
    setView('original')
    setError('')
    setNotice('首次修复使用原文框生成文字 Mask。已切换到原图，请核对原文框后再次点击重新修复；刚才调整的译文框位置已保留。')
    return true
  }

  const applyHistory = useCallback(async (direction: 'undo' | 'redo') => {
    if (historyBusy.current || editorBusy) return
    const source = direction === 'undo' ? undoStack : redoStack
    const entry = source.at(-1)
    if (!entry) return
    historyBusy.current = true
    try {
      const target = direction === 'undo' ? entry.before : entry.after
      const updated = await api.regions.update(entry.id, editable(target))
      setRegionState(regionsRef.current.map(region => region.id === entry.id ? updated : region))
      if (direction === 'undo') {setUndoStack(value => value.slice(0, -1)); setRedoStack(value => [...value, entry])}
      else {setRedoStack(value => value.slice(0, -1)); setUndoStack(value => [...value, entry])}
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      historyBusy.current = false
    }
  }, [undoStack, redoStack, editorBusy])

  const createRegion = async (polygon: number[][], bbox: number[]): Promise<boolean> => {
    if (!page || editorBusy) return false
    const requestedPageId = page.id
    setRunningRegionAction('create'); setError('')
    try {
      const nextReadingOrder = regionsRef.current.reduce((maximum, region) => Math.max(maximum, region.reading_order), 0) + 1
      const created = await api.regions.create(requestedPageId, {polygon, bbox, reading_order: nextReadingOrder, orientation: bbox[3] > bbox[2] * 1.2 ? 'vertical' : 'horizontal'})
      if (currentPageId.current !== requestedPageId) return false
      const store = useEditorStore.getState()
      if (!store.layers.detection) store.toggleLayer('detection')
      setRegionState([...regionsRef.current.filter(region => region.id !== created.id), created]); select(created.id); setTool('select')
      return true
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return false
    } finally {
      setRunningRegionAction(null)
    }
  }
  const reloadCurrent = useCallback(async (preserveGeometry = true) => {
    if (!page) return
    const selectedId = useEditorStore.getState().selectedIds[0] || null
    const [nextRegions] = await Promise.all([loadPage(page.id, selectedId, preserveGeometry), refreshPages()])
    return nextRegions.find(region => region.id === selectedId)
  }, [page?.id, loadPage, refreshPages])

  useEffect(() => {
    taskMonitorGeneration.current += 1
    return () => { taskMonitorGeneration.current += 1 }
  }, [page?.id])

  const waitForTask = useCallback(async (taskId: string, onUpdate?: TaskUpdateHandler) => {
    const generation = taskMonitorGeneration.current
    let transientFailures = 0
    let lastVersion = ''
    while (generation === taskMonitorGeneration.current) {
      try {
        const task = await api.tasks.get(taskId)
        transientFailures = 0
        const version = `${task.status}:${task.current_stage || ''}:${task.progress}:${task.message}:${task.updated_at}`
        if (version !== lastVersion) {
          await onUpdate?.(task)
          lastVersion = version
        }
        if (['COMPLETED','FAILED','CANCELLED'].includes(task.status)) return task
        await new Promise(resolve => window.setTimeout(resolve, 800))
      } catch (reason) {
        const transientGatewayError = reason instanceof ApiError && [502, 503, 504].includes(reason.status)
        const transientNetworkError = reason instanceof TypeError
        if ((!transientGatewayError && !transientNetworkError) || transientFailures >= 20) throw reason
        transientFailures += 1
        await new Promise(resolve => window.setTimeout(resolve, Math.min(3000, 800 + transientFailures * 200)))
      }
    }
    return null
  }, [])

  const syncTaskProgress = useCallback(async (task: ProcessingTask) => {
    setRunningPageTask(task)
    if (!page?.id || task.image_id !== page.id || task.progress <= 0) return
    const version = `${task.id}:${task.current_stage || ''}:${task.progress}:${task.updated_at}`
    if (taskSyncVersion.current === version) return
    taskSyncVersion.current = version
    const monitorGeneration = taskMonitorGeneration.current
    const revisionBeforeRequest = regionStateRevision.current
    const requestedPageId = page.id
    const selectedId = useEditorStore.getState().selectedIds[0] || null
    const [nextPage, nextRegions] = await Promise.all([api.images.get(requestedPageId), api.images.regions(requestedPageId)])
    if (monitorGeneration !== taskMonitorGeneration.current || nextPage.id !== requestedPageId) return
    setPage(nextPage)
    setPages(current => current.map(item => item.id === nextPage.id ? nextPage : item))
    // A region may be created or edited while this refresh is in flight. Do
    // not let an older response replace that newer local state; the next task
    // update (or final reload) will fetch an authoritative list again.
    if (revisionBeforeRequest === regionStateRevision.current) {
      const resolvedRegions = preserveLocalRegionGeometry(nextRegions, regionsRef.current)
      setRegionState(resolvedRegions)
      if (selectedId && !resolvedRegions.some(region => region.id === selectedId)) select(null)
    }
  }, [page?.id, select])

  const refreshAfterTask = async (taskId: string, onUpdate?: TaskUpdateHandler) => {
    const task = await waitForTask(taskId, onUpdate)
    if (!task) return null
    if (task.status === 'COMPLETED') return {task, region: await reloadCurrent()}
    else if (task.status === 'FAILED') setError(task.error_message || '处理任务失败')
    return {task, region: undefined}
  }

  useEffect(() => {
    if (loading || !page?.id) return
    let disposed = false
    let claimedTaskId: string | null = null

    const recoverActiveTask = async () => {
      try {
        const tasks = await api.tasks.list(projectId)
        if (disposed) return
        const active = tasks.find(task => task.image_id === page.id && ['QUEUED', 'RUNNING'].includes(task.status))
        if (!active || recoveredTaskId.current === active.id) return
        recoveredTaskId.current = active.id
        claimedTaskId = active.id
        setSchedulingProcess(true)
        setRunningPageTask(active)
        setNotice('已恢复当前页面的后台任务监控。')
        const terminal = await waitForTask(active.id, task => disposed ? undefined : syncTaskProgress(task))
        if (disposed || !terminal) return
        await reloadCurrent()
        if (terminal.status === 'COMPLETED') setNotice('后台任务已完成，页面结果已同步。')
        else if (terminal.status === 'FAILED') setError(terminal.error_message || '后台任务失败')
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (!disposed) {
          setSchedulingProcess(false)
          setRunningPageTask(null)
        }
        if (claimedTaskId && recoveredTaskId.current === claimedTaskId) recoveredTaskId.current = null
      }
    }

    void recoverActiveTask()
    return () => {
      disposed = true
      setSchedulingProcess(false)
      setRunningPageTask(null)
      if (claimedTaskId && recoveredTaskId.current === claimedTaskId) recoveredTaskId.current = null
    }
  }, [loading, page?.id, projectId, reloadCurrent, syncTaskProgress, waitForTask])

  const deleteSelectedRegion = useCallback(async () => {
    if (deleteBusy.current || editorBusy) return
    const selected = regionsRef.current.filter(region => selectedIds.includes(region.id))
    if (!selected.length) return
    const selectedRegionIds = selected.map(region => region.id)
    const multiple = selected.length > 1

    deleteBusy.current = true
    try {
      const accepted = await confirmDialog({
        title: multiple ? `删除 ${selected.length} 个文字区域？` : '删除文字区域？',
        message: `${multiple ? selected.map(region => region.region_key).join('、') : selected[0].region_key} 的识别文本、翻译、Mask 和排版设置将被删除。如果当前页已有净图或译文，系统会用剩余区域统一重建一次。此操作无法撤销。`,
        tone: 'danger',
        confirmLabel: multiple ? '删除所选' : '删除区域',
      })
      if (!accepted) return
      setRunningRegionAction('delete')
      for (const id of selectedRegionIds) {
        const pendingChange = pending.current.get(id)
        if (pendingChange) window.clearTimeout(pendingChange.timer)
        pending.current.delete(id)
      }
      const deleted = multiple
        ? await api.regions.removeMany(selectedRegionIds)
        : await api.regions.remove(selectedRegionIds[0])
      setRegionState(regionsRef.current.filter(region => !selectedRegionIds.includes(region.id)))
      select(null)
      if (deleted.rebuild_task) {
        setNotice('正在恢复净图并重新生成译文图层…')
        setRunningPageTask(deleted.rebuild_task)
        const result = await refreshAfterTask(deleted.rebuild_task.id, syncTaskProgress)
        if (result?.task.status === 'COMPLETED') setNotice(`${multiple ? '所选区域' : '区域'}已删除，净图和译文已重新生成。`)
        else setNotice('')
      } else {
        setNotice(`${multiple ? '所选区域' : '区域'}已删除；请继续核对剩余区域。`)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      deleteBusy.current = false
      setRunningRegionAction(null)
    }
  }, [confirmDialog, editorBusy, refreshAfterTask, selectedIds, select, syncTaskProgress])

  const action = async (name: string, options: Record<string, unknown> = {}, targetRegionId?: string) => {
    if (editorBusy) return
    const selected = regionsRef.current.find(region => region.id === (targetRegionId || selectedIds[0]))
    try {
      if (!targetRegionId && selectedIds.length > 1 && ['ocr','translate','inpaint','render'].includes(name)) {
        if (!page || regionActionBusy.current) return
        const regionIds = selectedIds.filter(id => regionsRef.current.some(region => region.id === id))
        if (regionIds.length < 2) return
        const targetRegions = regionsRef.current.filter(region => regionIds.includes(region.id))
        if (name === 'inpaint' && requireSourceGeometryForFirstRepair(targetRegions)) return
        const stage = ({
          ocr: {start_stage: 'ocr', end_stage: 'ocr'},
          translate: {start_stage: 'translation', end_stage: 'translation'},
          inpaint: {start_stage: 'mask', end_stage: 'inpainting'},
          render: {start_stage: 'rendering', end_stage: 'rendering'},
        } as Record<string, {start_stage: string, end_stage: string}>)[name]
        const actionLabel = ({ocr: 'OCR', translate: '翻译', inpaint: '修复', render: '排版'} as Record<string, string>)[name]
        regionActionBusy.current = true
        blurActiveControl()
        setRunningRegionAction(name); setError(''); setNotice(`正在为 ${regionIds.length} 个所选区域重新${actionLabel}…`)
        try {
          await flushPendingRegions(regionIds)
          const task = await api.images.process(page.id, {
            ...stage,
            force: true,
            options: {...options, ...(name === 'ocr' ? {crop_padding: 4} : {}), region_ids: regionIds},
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
        }
        return
      }
      if (name === 'merge') {
        if (selectedIds.length < 2) return
        setRunningRegionAction('merge')
        try {
          for (const id of selectedIds) {
            const change = pending.current.get(id)
            if (change) await commitPendingRegion(id, change)
          }
          const mergedResult = await api.regions.merge(selectedIds)
          const merged = mergedResult.region
          setRegionState([...regionsRef.current.filter(region => !selectedIds.includes(region.id)), merged])
          select(merged.id)
          if (mergedResult.rebuild_task) {
            setNotice('区域已合并，正在重新生成 Mask、净图和译文…')
            setRunningPageTask(mergedResult.rebuild_task)
            const result = await refreshAfterTask(mergedResult.rebuild_task.id, syncTaskProgress)
            if (result?.task.status === 'COMPLETED') setNotice(`${selectedIds.length} 个区域已合并，净图和译文已重新生成。`)
            else setNotice('')
          } else {
            setNotice(`${selectedIds.length} 个区域已合并。`)
          }
        } finally {
          setRunningRegionAction(null)
        }
        return
      }
      if (!selected) return
      const selectedId = selected.id
      if (name === 'delete') await deleteSelectedRegion()
      else if (name === 'copy') {const copy = await api.regions.copy(selectedId); setRegionState([...regionsRef.current, copy]); select(copy.id)}
      else if (name === 'split') {const split = await api.regions.split(selectedId); setRegionState([...regionsRef.current.filter(region => region.id !== selectedId), ...split]); select(split[0].id)}
      else if (name === 'visibility') {
        if (regionActionBusy.current) return
        regionActionBusy.current = true
        setRunningRegionAction('visibility'); setError(''); blurActiveControl()
        try {
          await flushPendingRegions([selectedId])
          const activeRegion = regionsRef.current.find(region => region.id === selectedId) || selected
          const updated = await api.regions.update(selectedId, {visible: !activeRegion.visible})
          setRegionState(regionsRef.current.map(region => region.id === selectedId ? updated : region))
          if (page?.rendered_url || page?.text_layer_url) {
            setNotice(updated.visible ? '正在恢复当前区域的译文显示…' : '正在从译图中隐藏当前区域…')
            const task = await api.regions.stage(selectedId, 'render')
            setRunningPageTask(task)
            const result = await refreshAfterTask(task.id, syncTaskProgress)
            if (!result || result.task.status !== 'COMPLETED') return
          }
          setNotice(updated.visible ? '当前区域已打开，预览和导出将显示译文。' : '当前区域已关闭，预览和导出将不显示译文。')
        } finally {
          regionActionBusy.current = false
          setRunningRegionAction(null)
        }
      }
      else if (['ocr','translate','inpaint','render'].includes(name)) {
        if (regionActionBusy.current) return
        if (name === 'inpaint' && requireSourceGeometryForFirstRepair([selected])) return
        regionActionBusy.current = true
        const runningMessage = ({ocr: '正在重新识别当前区域…', translate: '正在重新翻译当前区域…', inpaint: '正在重新生成 Mask 并修复背景…', render: '正在重新生成排版…'} as Record<string, string>)[name]
        blurActiveControl()
        setRunningRegionAction(name); setNotice(runningMessage || '正在处理当前区域…'); setError('')
        try {
          await flushPendingRegions([selectedId])
          const activeRegion = regionsRef.current.find(region => region.id === selectedId) || selected
          const before = activeRegion
          const task = await api.regions.stage(selectedId, name, options)
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
          } else {
            setNotice('排版与翻译文字图层已重新生成。')
          }
        } finally {
          regionActionBusy.current = false
          setRunningRegionAction(null)
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

  const openPageContextMenu = (event: ReactMouseEvent, item: ImagePage) => {
    event.preventDefault()
    event.stopPropagation()
    if (editorBusy || pagePressRef.current?.active) return
    setPageContextMenu({
      x: Math.max(8, Math.min(event.clientX, window.innerWidth - 190)),
      y: Math.max(8, Math.min(event.clientY, window.innerHeight - 140)),
      pageId: item.id,
    })
  }

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
      message: `“${target.filename}”及其全部文字区域、Mask、净图和译文结果都会被永久删除。此操作无法撤销。`,
      tone: 'danger',
      confirmLabel: '删除图片',
    })) return
    setDeletingPageId(pageId); setError(''); setNotice('正在删除图片…')
    try {
      await api.images.remove(pageId)
      const remaining = await api.projects.images(projectId)
      setPages(remaining)
      setProject(current => current ? {...current, page_count: remaining.length} : current)
      if (page?.id === pageId) {
        pending.current.forEach(change => window.clearTimeout(change.timer))
        pending.current.clear()
        currentPageId.current = null
        select(null); setRegionState([]); setPage(null); setUndoStack([]); setRedoStack([])
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
      const updated = await api.images.setOcrExempt(pageId, exempt)
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
    if (editorBusy || fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= pages.length || toIndex >= pages.length) return
    const previousPages = [...pages]
    const activePageId = imageId || page?.id
    const selectedBefore = useEditorStore.getState().selectedIds
    const nextPages = [...pages]
    const [moved] = nextPages.splice(fromIndex, 1)
    nextPages.splice(toIndex, 0, moved)
    setPages(nextPages)
    setReorderingPages(true); setReorderProgress(0); setError(''); setNotice('正在保存页面顺序…')
    try {
      const direction = toIndex > fromIndex ? 1 : -1
      const moveCount = Math.abs(toIndex - fromIndex)
      let completed = 0
      for (let index = fromIndex + direction; direction > 0 ? index <= toIndex : index >= toIndex; index += direction) {
        await api.images.reorder(pageId, previousPages[index].order_index)
        completed += 1
        setReorderProgress(completed / moveCount)
      }
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

  const beginPagePress = (event: ReactPointerEvent<HTMLButtonElement>, item: ImagePage, index: number) => {
    if (editorBusy || !event.isPrimary || event.button !== 0) return
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
  }

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
    if (editorBusy || !selectedFiles.length) return
    const unsupported = selectedFiles.filter(file => !/\.(jpe?g|png|webp)$/i.test(file.name))
    if (unsupported.length) {
      setError(`不支持以下文件：${unsupported.map(file => file.name).join('、')}。请选择 JPG、PNG 或 WebP 图片。`)
      if (fileRef.current) fileRef.current.value = ''
      return
    }
    setUploadingPages(true)
    setError('')
    setNotice(`正在导入 ${selectedFiles.length} 张图片…`)
    try {
      const added = await api.projects.upload(projectId, selectedFiles)
      if (!added.length) throw new Error('服务器没有返回已导入的页面')
      await refreshPages()
      setNotice(`${added.length} 张图片已导入。`)
      navigate(`/projects/${projectId}/editor/${added[0].id}`)
    } catch (reason) {
      setNotice('')
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setUploadingPages(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const processPageStage = async (stage: PageWorkflowStage) => {
    if (!page || editorBusy) return
    if (stage === 'inpainting' && requireSourceGeometryForFirstRepair(regionsRef.current)) return
    const rank = pageWorkflowRank(page, regionsRef.current)
    const requiredRank: Record<PageWorkflowStage, number> = {ocr: 0, translation: 1, inpainting: 2, rendering: 3}
    if (rank < requiredRank[stage]) return
    const currentRegions = regionsRef.current
    const startsWithDetection = stage === 'ocr' && currentRegions.length === 0
    const targetView = pageWorkflowTargetView(stage)
    if (targetView) setView(targetView)
    const request = buildPageWorkflowRequest(stage, startsWithDetection)
    const startingNotices: Record<PageWorkflowStage, string> = {
      ocr: startsWithDetection ? '正在检测文字区域并执行 OCR…' : '正在重新 OCR 当前页的文字区域…',
      translation: '正在重新翻译当前页，完成后才能执行修复…',
      inpainting: '正在重新生成文字 Mask、修复背景并自动重新排版…',
      rendering: '正在根据当前译文重新排版…',
    }
    const completedNotices: Record<PageWorkflowStage, string> = {
      ocr: 'OCR 已完成，请核对文字区域和原文；确认无误后再执行重新翻译。',
      translation: '翻译已完成，可以继续执行重新修复。',
      inpainting: '背景修复与重新排版已完成。',
      rendering: '当前页重新排版已完成。',
    }
    blurActiveControl()
    setSchedulingProcess(true)
    setRunningWorkflowStage(stage)
    setRunningPageTask(null)
    setNotice(startingNotices[stage])
    try {
      await flushPendingRegions()
      const task = await api.images.process(page.id, request)
      setRunningPageTask(task)
      const result = await refreshAfterTask(task.id, syncTaskProgress)
      if (result?.task.status === 'COMPLETED') {
        if (targetView) setView(targetView)
        setNotice(completedNotices[stage])
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSchedulingProcess(false)
      setRunningWorkflowStage(null)
      setRunningPageTask(null)
    }
  }

  const batchRecognize = async () => {
    if (!pages.length || editorBusy) return
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
    blurActiveControl()
    setSchedulingBatch(true); setBatchProgress(0); setError(''); setNotice(`正在批量识别其余 ${candidates.length} 页…`)
    try {
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
      let completed = 0
      const results = await Promise.all(tasks.map(async task => {
        const result = await waitForTask(task.id)
        completed += 1
        setBatchProgress(completed / Math.max(1, tasks.length))
        return result
      }))
      await refreshPages()
      const failed = results.filter(task => task?.status === 'FAILED')
      if (failed.length) { setNotice(''); setError(`${failed.length} 个页面识别失败，请检查页面状态。`) }
      else setNotice('批量检测和 OCR 已完成，请逐页核对文字区域。')
    } catch (reason) {
      setNotice(''); setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSchedulingBatch(false)
      setBatchProgress(null)
    }
  }

  const resetCurrentPage = async () => {
    if (!page || editorBusy) return
    if (!await confirmDialog({
      title: '重置当前页？',
      message: '当前页的文字区域、OCR、译文、Mask、净图和排版结果都会被清除，页面将恢复到刚导入的原图状态。此操作无法撤销。',
      tone: 'danger',
      confirmLabel: '恢复默认',
    })) return
    blurActiveControl()
    setResettingPage(true); setError(''); setNotice('正在恢复当前页…')
    try {
      pending.current.forEach(change => window.clearTimeout(change.timer))
      pending.current.clear()
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
    setError('')
    try {
      const parsed = JSON.parse(contextText)
      const updated = await api.projects.update(projectId, {translation_context: parsed})
      setProject(updated)
      setShowContext(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'JSON 格式错误')
    } finally {
      setSavingContext(false)
    }
  }

  const selectedScope = selectedIds.length > 1 ? `${selectedIds.length} 个所选区域` : '当前区域'
  const busyLabel = exporting
    ? ({project:'正在打包可编辑源项目…', translated:'正在生成并导出翻译后图片…', clean:'正在导出净图…'} as Record<ExportKind, string>)[exportKind || 'project']
    : deletingPageId
    ? '正在删除图片…'
    : updatingPageRecognitionId
      ? '正在更新图片识别状态…'
      : reorderingPages
      ? '正在保存页面顺序…'
      : uploadingPages
      ? '正在导入图片…'
      : resettingPage
      ? '正在重置当前页…'
      : schedulingBatch
      ? '正在批量检测并识别…'
      : schedulingProcess
        ? pageTaskBusyLabel(runningPageTask, runningWorkflowStage)
        : ({ocr: `正在重新 OCR ${selectedScope}…`, translate: `正在重新翻译${selectedScope}…`, inpaint: `正在重新修复${selectedScope}…`, render: `正在重新排版${selectedScope}…`, merge: '正在合并所选区域…', delete: '正在删除所选区域…', visibility: '正在更新区域显示…', create: '正在创建文字区域…'} as Record<string, string>)[runningRegionAction || ''] || '正在处理…'
  const activePageTask = runningPageTask && ['QUEUED', 'RUNNING'].includes(runningPageTask.status.toUpperCase())
    ? runningPageTask
    : null
  const busyProgress = schedulingBatch
    ? batchProgress
    : reorderingPages
      ? reorderProgress
      : activePageTask && (schedulingProcess || !!runningRegionAction)
        ? activePageTask.progress
        : null

  const headerButtonClass = `${buttonClass} !h-10 !min-h-10 px-3.5 py-0 text-[12px]`
  const workflowRank = page ? pageWorkflowRank(page, regions) : 0
  const nextWorkflowStage: PageWorkflowStage | null = workflowRank < 1
    ? 'ocr'
    : workflowRank < 2
      ? 'translation'
      : workflowRank < 3
        ? 'inpainting'
        : workflowRank < 4
          ? 'rendering'
          : null
  const workflowButtons: Array<{stage: PageWorkflowStage, shortcutId: ShortcutId, label: string, requiredRank: number, title: string, icon: typeof ScanText}> = [
    {stage: 'ocr', shortcutId:'page.workflowOcr', label: '重新 OCR', requiredRank: 0, title: regions.length ? '重新识别当前页已有文字区域' : '当前页没有文字区域，将先自动检测再执行 OCR', icon: ScanText},
    {stage: 'translation', shortcutId:'page.workflowTranslate', label: '重新翻译', requiredRank: 1, title: workflowRank >= 1 ? '使用最新 OCR 原文重新翻译' : '请先完成重新 OCR', icon: Languages},
    {stage: 'inpainting', shortcutId:'page.workflowInpaint', label: '重新修复', requiredRank: 2, title: workflowRank >= 2 ? '重新生成文字 Mask、修复背景并自动重新排版' : '请先依次完成重新 OCR 和重新翻译', icon: Eraser},
    {stage: 'rendering', shortcutId:'page.workflowRender', label: '重新排版', requiredRank: 3, title: workflowRank >= 3 ? '使用当前译文和样式重新排版' : '请先依次完成 OCR、翻译和修复', icon: TextCursorInput},
  ]

  shortcutHandlerRef.current = event => {
    if (event.defaultPrevented || event.isComposing || dialogOpen || showContext || isEditableTarget(event.target)) return
    const shortcutId = shortcutForEvent(event, shortcuts)
    if (!shortcutId) return
    event.preventDefault()
    const repeatable = shortcutId.startsWith('region.nudge') || shortcutId.startsWith('zoom.')
    if ((event.repeat && !repeatable) || editorBusy) return

    const store = useEditorStore.getState()
    if (shortcutId === 'tool.select') store.setTool('select')
    else if (shortcutId === 'tool.rectangle') store.setTool('rectangle')
    else if (shortcutId === 'tool.polygon') store.setTool('polygon')
    else if (shortcutId === 'tool.lasso') store.setTool('lasso')
    else if (shortcutId === 'view.original') store.setView('original')
    else if (shortcutId === 'view.clean') store.setView('clean')
    else if (shortcutId === 'view.translated') store.setView('translated')
    else if (shortcutId === 'view.comparison') store.setView('comparison')
    else if (shortcutId === 'edit.undo') {
      if (undoStack.length) void applyHistory('undo')
    } else if (shortcutId === 'edit.redo') {
      if (redoStack.length) void applyHistory('redo')
    } else if (shortcutId === 'edit.selectAll') {
      store.selectMany(regionsRef.current.map(region => region.id))
    } else if (shortcutId === 'zoom.out') store.setZoom(store.zoom / 1.15)
    else if (shortcutId === 'zoom.in') store.setZoom(store.zoom * 1.15)
    else if (shortcutId === 'zoom.reset') store.setZoom(1)
    else if (shortcutId === 'region.delete') {
      if (selectedIds.length) void deleteSelectedRegion()
    } else if (shortcutId === 'region.cancelSelection') store.select(null)
    else if (shortcutId === 'region.merge') {
      if (selectedIds.length > 1) void action('merge')
    } else if (shortcutId === 'region.ocr' || shortcutId === 'region.translate' || shortcutId === 'region.inpaint' || shortcutId === 'region.render') {
      if (selectedIds.length) void action(({['region.ocr']:'ocr', ['region.translate']:'translate', ['region.inpaint']:'inpaint', ['region.render']:'render'} as Record<string, string>)[shortcutId])
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
      const dx = direction[0] * distance
      const dy = direction[1] * distance
      const selected = new Set(selectedIds)
      regionsRef.current.forEach(region => {
        if (!selected.has(region.id) || region.locked) return
        if (view === 'translated') {
          const bbox = region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox
          const polygon = region.translated_polygon?.length >= 3 ? region.translated_polygon : region.polygon
          void updateRegion(region.id, {
            translated_bbox:[bbox[0] + dx, bbox[1] + dy, bbox[2], bbox[3]],
            translated_polygon:polygon.map(point => [point[0] + dx, point[1] + dy]),
          })
        } else {
          void updateRegion(region.id, {
            bbox:[region.bbox[0] + dx, region.bbox[1] + dy, region.bbox[2], region.bbox[3]],
            polygon:region.polygon.map(point => [point[0] + dx, point[1] + dy]),
          })
        }
      })
    } else if (shortcutId === 'page.workflowOcr') {
      if (page) void processPageStage('ocr')
    } else if (shortcutId === 'page.workflowTranslate') {
      if (page && workflowRank >= 1) void processPageStage('translation')
    } else if (shortcutId === 'page.workflowInpaint') {
      if (page && workflowRank >= 2) void processPageStage('inpainting')
    } else if (shortcutId === 'page.workflowRender') {
      if (page && workflowRank >= 3) void processPageStage('rendering')
    } else if (shortcutId === 'page.import') openImagePicker()
    else if (shortcutId === 'page.context') setShowContext(true)
    else if (shortcutId === 'page.export') {
      if (pages.length) setExportMenuOpen(open => !open)
    } else if (shortcutId === 'page.batchOcr') void batchRecognize()
    else if (shortcutId === 'page.reset') void resetCurrentPage()
  }

  const contextPage = pageContextMenu ? pages.find(item => item.id === pageContextMenu.pageId) : undefined
  const selectedPageId = imageId || page?.id
  if (!project && !showingInitialLoading) return <div className="grid h-full place-content-center text-center"><h2>无法打开项目</h2><p className="text-muted">{error}</p><Link className="text-accent" to="/projects">返回项目</Link></div>
  return <>
    {editorTarget && createPortal(<>
      <div className="ml-auto flex items-center gap-2">
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
        <button className={headerButtonClass} onClick={() => void batchRecognize()} disabled={editorBusy || showingInitialLoading} title={`只识别当前页之外尚未完成 OCR 的图片 (${formatShortcut(shortcuts['page.batchOcr'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['page.batchOcr'])}>{schedulingBatch ? <ButtonLoading label="批量识别中…"/> : <><Layers3 size={16}/>批量识别</>}</button>
      </div>
    </>, editorTarget)}
    <div className="editor-content-enter grid h-full grid-rows-[48px_minmax(0,1fr)] bg-canvas">
    <EditorToolbar
      onUndo={() => applyHistory('undo')}
      onRedo={() => applyHistory('redo')}
      canUndo={!!undoStack.length}
      canRedo={!!redoStack.length}
      disabled={editorBusy}
      rightActions={<div className="flex items-center gap-1" aria-label="当前页处理流程">
        {workflowButtons.map(({stage, shortcutId, label, requiredRank, title, icon: Icon}) => <button
          key={stage}
          className={cn(nextWorkflowStage === stage ? primaryButtonClass : buttonClass, '!h-8 !min-h-8 px-2.5 py-0 text-[11px]')}
          onClick={() => void processPageStage(stage)}
          disabled={!page || editorBusy || workflowRank < requiredRank}
          title={`${title} (${formatShortcut(shortcuts[shortcutId])})`}
          aria-keyshortcuts={shortcutToAria(shortcuts[shortcutId])}
        >{schedulingProcess && runningWorkflowStage === stage ? <ButtonLoading label={`${label.replace('重新 ', '')} 中…`}/> : <><Icon size={14}/>{label}</>}</button>)}
        <button
          className={`${dangerButtonClass} !h-8 !min-h-8 px-2.5 py-0 text-[11px]`}
          onClick={() => void resetCurrentPage()}
          disabled={!page || editorBusy || (page.status === 'UPLOADED' && !regions.length && !page.clean_url && !page.rendered_url)}
          title={`重置当前页 (${formatShortcut(shortcuts['page.reset'])})`}
          aria-keyshortcuts={shortcutToAria(shortcuts['page.reset'])}
        >{resettingPage ? <ButtonLoading label="恢复中…"/> : <><RotateCcw size={14}/>重置当前页</>}</button>
      </div>}
    />
    {showingInitialLoading ? <EditorSkeleton canvasControls={<CanvasZoomControls/>}/> : <div className="grid min-h-0 grid-cols-[200px_minmax(0,1fr)_320px]">
      <aside className="flex min-h-0 flex-col border-r border-line-subtle bg-panel">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-line-subtle px-3.5 font-mono text-[12px] font-semibold uppercase tracking-[.8px] text-muted"><span>页面</span><button className={cn(buttonClass, '!size-8 !min-h-8 !p-0 border-0 bg-transparent')} disabled={editorBusy} title={`导入图片 (${formatShortcut(shortcuts['page.import'])})`} aria-label="导入图片" aria-keyshortcuts={shortcutToAria(shortcuts['page.import'])} onClick={openImagePicker}>{uploadingPages ? <ButtonLoading compact label="正在导入图片"/> : <ImagePlus aria-hidden="true" size={16}/>}</button></div>
        <div ref={pageListRef} className={cn('min-h-0 flex-1 overflow-auto p-2.5', scrollbarClass)} onContextMenu={openImagePickerFromBlank}>{pages.map((item, index) => <button
          key={item.id}
          data-page-id={item.id}
          data-page-index={index}
          aria-current={selectedPageId === item.id ? 'page' : undefined}
          className={cn(
            'relative mb-2.5 block w-full cursor-grab touch-pan-y rounded-xl border p-2.5 text-left outline-none transition-[background-color,border-color,opacity,transform,box-shadow,filter] focus-visible:border-accent/70 focus-visible:ring-2 focus-visible:ring-accent/25 active:cursor-grabbing',
            selectedPageId === item.id
              ? 'border-accent !bg-accent !text-accent-ink [box-shadow:var(--shadow-card-hover)] hover:brightness-105'
              : 'border-transparent bg-transparent hover:bg-raised',
            pageDrag?.pageId === item.id && 'z-10 scale-[1.015] cursor-grabbing bg-raised opacity-70 shadow-panel',
            pageDrag && pageDrag.overIndex === index && pageDrag.fromIndex !== index && (pageDrag.overIndex > pageDrag.fromIndex
              ? 'after:absolute after:-bottom-1 after:inset-x-1 after:h-0.5 after:rounded-full after:bg-accent after:shadow-[0_0_8px_var(--color-accent)]'
              : 'before:absolute before:-top-1 before:inset-x-1 before:h-0.5 before:rounded-full before:bg-accent before:shadow-[0_0_8px_var(--color-accent)]'),
          )}
          onPointerDown={event => beginPagePress(event, item, index)}
          onContextMenu={event => openPageContextMenu(event, item)}
          onClick={event => {
            if (suppressPageClick.current) {event.preventDefault(); event.stopPropagation(); return}
            if (!editorBusy) navigate(`/projects/${projectId}/editor/${item.id}`)
          }}
        >
          <div className={cn('relative flex aspect-[3/4] w-full justify-center overflow-hidden bg-transparent transition-shadow', selectedPageId === item.id && 'ring-1 ring-inset ring-canvas/50')} style={item.width > 0 && item.height > 0 ? {aspectRatio: `${item.width} / ${item.height}`} : undefined}><img className="pointer-events-none block size-full select-none object-contain" draggable={false} width={item.width} height={item.height} src={item.rendered_url || item.original_url} alt={item.filename}/><span className={cn('absolute bottom-1 left-1 bg-canvas px-1 py-0.5 font-mono text-[12px]', selectedPageId === item.id && 'text-accent')}>{String(index + 1).padStart(2,'0')}</span>{activePageTask?.image_id === item.id && <span className="absolute right-1 top-1 grid rounded-full bg-canvas/90 p-1 shadow-panel"><CircularProgress value={activePageTask.progress} size={34} label={`${item.filename} 处理进度`}/></span>}</div>
          <small className={cn('mt-2 block truncate text-[12px]', selectedPageId === item.id ? 'font-semibold text-accent-ink' : 'text-secondary')} title={item.filename}>{item.filename}</small><i className={cn('mt-1 block truncate font-mono text-[12px] not-italic', pageState(item).emphasized && 'font-semibold', selectedPageId === item.id ? 'text-accent-ink' : pageState(item).className)}>{pageState(item).label}</i>
        </button>)}
        {!pages.length && <div className="flex min-h-full items-center justify-center"><button className={`${buttonClass} min-h-[140px] w-full flex-col border-dashed bg-transparent text-[12px] text-muted`} disabled={editorBusy} onClick={openImagePicker}><Upload size={22}/>导入漫画图片</button></div>}
        </div>
      </aside>
      <main className="relative min-h-0 min-w-0 bg-canvas bg-[linear-gradient(45deg,var(--color-panel)_25%,transparent_25%),linear-gradient(-45deg,var(--color-panel)_25%,transparent_25%),linear-gradient(45deg,transparent_75%,var(--color-panel)_75%),linear-gradient(-45deg,transparent_75%,var(--color-panel)_75%)] [background-position:0_0,0_10px,10px_-10px,-10px_0] [background-size:20px_20px]">{page ? <MangaCanvas page={page} regions={regions} onCreate={createRegion} onUpdate={updateRegion} onRegionAction={(id, name, options) => {void action(name, options, id)}} runningAction={runningRegionAction}/> : <div className="flex h-full flex-col items-center justify-center text-muted"><ImagePlus size={36}/><h3 className="mb-1 mt-3 text-base text-secondary">导入漫画页面</h3><p className="mb-4 mt-0 text-xs">支持 JPG、PNG 和 WebP，可一次选择多张。</p><button className={`${primaryButtonClass} !min-h-[34px]`} disabled={editorBusy} onClick={openImagePicker}>选择图片</button></div>}<CanvasZoomControls disabled={editorBusy}/></main>
      <RegionProperties region={selectedIds.length === 1 ? regions.find(region => region.id === selectedIds[0]) : undefined} selectedRegions={regions.filter(region => selectedIds.includes(region.id))} selectedCount={selectedIds.length} fontOptions={[...DEFAULT_FONT_FAMILIES.map(font => font.name), ...fontResources.map(font => font.name)]} busyAction={runningRegionAction} onUpdate={updateRegion} onAction={action}/>
    </div>}
    {pageContextMenu && createPortal(<div ref={pageContextMenuRef} className="fixed z-[110] w-[182px] overflow-hidden rounded-xl border border-line-strong bg-popover p-1 text-secondary shadow-dialog backdrop-blur-xl" style={{left: pageContextMenu.x, top: pageContextMenu.y}} role="menu" aria-label="图片操作">
      <div className="flex h-9 items-center border-b border-line-subtle px-2"><span className="min-w-0 truncate text-[10px] text-muted" title={pages.find(item => item.id === pageContextMenu.pageId)?.filename}>{pages.find(item => item.id === pageContextMenu.pageId)?.filename}</span></div>
      <button className="mt-1 flex h-9 w-full cursor-pointer items-center gap-2 rounded-lg border-0 bg-transparent px-3 text-left text-[11px] text-secondary outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-default disabled:opacity-60" type="button" role="menuitem" disabled={!!contextPage && !contextPage.ocr_exempt && !pageNeedsOcr(contextPage)} onClick={() => void togglePageOcrExempt(pageContextMenu.pageId)}><CheckCircle2 size={15} className="text-accent"/>{contextPage?.ocr_exempt ? '取消已翻译' : '已翻译'}</button>
      <button className="mt-1 flex h-9 w-full cursor-pointer items-center gap-2 rounded-lg border-0 bg-transparent px-3 text-left text-[11px] text-danger-soft-ink outline-none transition-colors hover:bg-danger/15 hover:text-danger-soft-ink" type="button" role="menuitem" onClick={() => void deletePage(pageContextMenu.pageId)}><Trash2 size={15}/>删除图片</button>
    </div>, document.body)}
    {showContext && <div className="fixed inset-0 z-[90] grid place-items-center bg-overlay backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="translation-context-title"><div className="w-[min(680px,72vw)] overflow-hidden rounded-2xl border border-line-strong bg-surface shadow-dialog"><header className="flex items-center justify-between border-b border-line-subtle px-6 py-5"><div><span className={eyebrowClass}>PROJECT CONTEXT</span><h2 className="mb-0 mt-1.5 text-xl" id="translation-context-title">翻译上下文</h2></div><button className={cn(iconButtonClass, 'border-0 bg-transparent text-xl')} disabled={savingContext} aria-label="关闭翻译上下文" onClick={() => setShowContext(false)}>×</button></header><p className="px-6 text-[13px] leading-6 text-muted">以 JSON 保存人物名、术语、口癖、称谓和章节背景；整页翻译时会随 Region ID 一起发送。</p><textarea className={cn(textareaClass, 'mx-6 mb-6 min-h-[320px] w-[calc(100%-48px)] font-mono text-[12px] leading-relaxed')} disabled={savingContext} value={contextText} onChange={event => setContextText(event.target.value)} spellCheck={false}/><footer className="flex justify-end gap-2.5 border-t border-line-subtle px-6 py-4"><button className={buttonClass} disabled={savingContext} onClick={() => setShowContext(false)}>取消</button><button className={primaryButtonClass} disabled={savingContext} onClick={() => void saveTranslationContext()}>{savingContext ? <ButtonLoading label="保存中…"/> : '保存上下文'}</button></footer></div></div>}
    {blockingBusy && <BlockingLoader label={busyLabel} progress={busyProgress}/>}
    {!blockingBusy && (error ? <div className="pointer-events-none fixed left-1/2 top-20 z-[120] flex w-max max-w-[min(600px,calc(100vw-36px))] -translate-x-1/2 items-center gap-3 rounded-[10px] border border-danger/20 bg-danger/20 py-3 pl-4 pr-2 text-danger-soft-ink shadow-panel backdrop-blur-xl" role="alert"><span className="min-w-0 text-[12px] leading-relaxed">{error}</span><button className="pointer-events-auto grid size-7 cursor-pointer place-items-center rounded-md border-0 bg-transparent p-0 text-lg text-current opacity-70 hover:bg-ink/10 hover:opacity-100" type="button" aria-label="关闭提示" onClick={() => {setError(''); setNotice('')}}>×</button></div> : notice && <div className="pointer-events-none fixed left-1/2 top-20 z-[120] flex w-max max-w-[min(600px,calc(100vw-36px))] -translate-x-1/2 items-center gap-3 rounded-[10px] border border-success/20 bg-success/15 py-3 pl-4 pr-2 text-success-soft-ink shadow-panel backdrop-blur-xl" role="status"><span className="min-w-0 text-[12px] leading-relaxed">{notice}</span><button className="pointer-events-auto grid size-7 cursor-pointer place-items-center rounded-md border-0 bg-transparent p-0 text-lg text-current opacity-70 hover:bg-ink/10 hover:opacity-100" type="button" aria-label="关闭提示" onClick={() => {setNotice(''); setError('')}}>×</button></div>)}
    <input ref={fileRef} disabled={editorBusy} hidden type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" multiple onChange={event => void upload(event.currentTarget.files)}/>
  </div>
  </>
}

function editable(region: TextRegion): Partial<TextRegion> {
  const keys: (keyof TextRegion)[] = ['polygon','bbox','translated_polygon','translated_bbox','source_text','translated_text','confidence','orientation','reading_order','panel_id','bubble_id','region_type','font_size','font_family','font_weight','text_color','stroke_color','stroke_width','alignment','line_spacing','character_spacing','rotation','perspective_warp','opacity','locked','visible']
  return Object.fromEntries(keys.map(key => [key, region[key]])) as Partial<TextRegion>
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

function blurActiveControl() {
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
}

function pageTaskBusyLabel(task: ProcessingTask | null, requestedStage: PageWorkflowStage | null): string {
  if (!task?.current_stage) return ({
    ocr: '正在重新 OCR 当前页…',
    translation: '正在重新翻译当前页…',
    inpainting: '正在重新修复当前页…',
    rendering: '正在重新排版当前页…',
  } as Record<PageWorkflowStage, string>)[requestedStage || 'ocr']
  if (task.current_stage === 'detection') return '正在检测文字区域…'
  if (task.current_stage === 'ocr') {
    if (task.message.startsWith('Loading OCR model')) return '检测完成，正在加载 OCR 模型…'
    const progress = task.message.match(/OCR\s+(\d+)\/(\d+)/)
    return progress ? `正在 OCR 识别（${progress[1]}/${progress[2]}）…` : '检测完成，正在 OCR 识别…'
  }
  return ({
    translation: '正在翻译已确认区域…',
    mask: '正在生成文字 Mask…',
    inpainting: '正在清除文字并修复背景…',
    rendering: '正在生成译文排版…',
  } as Record<string, string>)[task.current_stage] || '正在处理当前页…'
}

function pageWorkflowRank(page: ImagePage, regions: TextRegion[]): number {
  const ranks: Record<string, number> = {
    UPLOADED: 0, DETECTING: 0, DETECTED: 0, OCR_RUNNING: 0,
    OCR_DONE: 1, TRANSLATING: 1,
    TRANSLATED: 2, MASK_GENERATING: 2, INPAINTING: 2,
    INPAINTED: 3, LAYOUTING: 3, RENDERING: 3,
    COMPLETED: 4, NEEDS_REVIEW: 4,
  }
  if (page.status !== 'FAILED') return ranks[page.status] ?? 0
  if (page.rendered_url || page.text_layer_url) return 4
  if (page.clean_url) return 3
  const recognized = regions.filter(region => region.source_text.trim())
  if (recognized.length && recognized.every(region => region.translated_text.trim())) return 2
  if (recognized.length) return 1
  return 0
}

function pageNeedsOcr(page: ImagePage): boolean {
  return !page.ocr_exempt && ['UPLOADED', 'DETECTED', 'FAILED'].includes(page.status)
}

function pageState(page: ImagePage): {className: string, label: string, emphasized?: boolean} {
  if (page.ocr_exempt) return {className: 'text-success-soft-ink', label: '已翻译', emphasized: true}
  const status = page.status === 'NEEDS_REVIEW' ? 'COMPLETED' : page.status
  const labels: Record<string, string> = {
    UPLOADED: '未识别', DETECTING: '检测中', DETECTED: '待识别', OCR_RUNNING: '识别中', OCR_DONE: '待确认',
    TRANSLATING: '翻译中', TRANSLATED: '已翻译', MASK_GENERATING: '生成 Mask', INPAINTING: '修复中',
    INPAINTED: '已修复', LAYOUTING: '排版中', RENDERING: '渲染中', COMPLETED: '已完成', FAILED: '失败',
  }
  const translated = status === 'TRANSLATED'
  const className = status === 'FAILED' ? 'text-danger' : status === 'COMPLETED' ? 'text-accent' : translated ? 'text-success-soft-ink' : 'text-muted'
  return {className, label: labels[status] || status, emphasized: translated}
}
