import { BookOpenText, Download, ImagePlus, Layers3, LoaderCircle, Play, RotateCcw, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { EditorToolbar } from '../components/EditorToolbar'
import { Loading, useAppHeaderSlots } from '../components/AppShell'
import { useGlobalDialog } from '../components/GlobalDialog'
import { RegionProperties } from '../components/RegionProperties'
import { DEFAULT_FONT_FAMILIES } from '../constants/fonts'
import { MangaCanvas } from '../editor/MangaCanvas'
import { ApiError, api } from '../services/api'
import { useEditorStore } from '../stores/editor'
import type { FontResource, ImagePage, ProcessingTask, Project, TextRegion, Tool, ViewMode } from '../types'
import {buttonClass, cn, dangerButtonClass, eyebrowClass, iconButtonClass, primaryButtonClass, textareaClass} from '../ui'

type HistoryEntry = {id: string, before: TextRegion, after: TextRegion}
type PendingChange = {before: TextRegion, patch: Partial<TextRegion>, timer: number}
type TaskUpdateHandler = (task: ProcessingTask) => void | Promise<void>
type PageContextMenu = {x: number, y: number, pageId: string}
type PagePress = {pageId: string, fromIndex: number, overIndex: number, pointerId: number, startX: number, startY: number, active: boolean}
type PageDrag = {pageId: string, fromIndex: number, overIndex: number}

const PAGE_DRAG_START_THRESHOLD = 3

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
  const pending = useRef<Map<string, PendingChange>>(new Map())
  const [undoStack, setUndoStack] = useState<HistoryEntry[]>([])
  const [redoStack, setRedoStack] = useState<HistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [runningRegionAction, setRunningRegionAction] = useState<string | null>(null)
  const [maskRevision, setMaskRevision] = useState(0)
  const [showContext, setShowContext] = useState(false)
  const [contextText, setContextText] = useState('{}')
  const [schedulingProcess, setSchedulingProcess] = useState(false)
  const [runningPageTask, setRunningPageTask] = useState<ProcessingTask | null>(null)
  const [schedulingBatch, setSchedulingBatch] = useState(false)
  const [resettingPage, setResettingPage] = useState(false)
  const [uploadingPages, setUploadingPages] = useState(false)
  const [deletingPageId, setDeletingPageId] = useState<string | null>(null)
  const [reorderingPages, setReorderingPages] = useState(false)
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
  const {selectedIds, select, setTool, tool, view} = useEditorStore()
  const {confirm: confirmDialog, isOpen: dialogOpen} = useGlobalDialog()
  const {editorTarget} = useAppHeaderSlots()
  const editorBusy = schedulingProcess || schedulingBatch || resettingPage || uploadingPages || !!deletingPageId || reorderingPages || !!runningRegionAction

  const setRegionState = (next: TextRegion[]) => { regionsRef.current = next; setRegions(next) }
  const refreshPages = useCallback(async () => {
    const next = await api.projects.images(projectId)
    setPages(next)
    if (page) setPage(next.find(item => item.id === page.id) || page)
    return next
  }, [projectId, page?.id])
  const loadPage = useCallback(async (id: string, preservedSelection?: string | null) => {
    const [nextPage, nextRegions] = await Promise.all([api.images.get(id), api.images.regions(id)])
    setPage(nextPage); setRegionState(nextRegions); setUndoStack([]); setRedoStack([])
    const target = preservedSelection === undefined ? searchParams.get('region') : preservedSelection
    select(target && nextRegions.some(region => region.id === target) ? target : null)
    return nextRegions
  }, [searchParams, select])

  useEffect(() => {
    const load = async () => {
      try {
        const [nextProject, nextPages, globalFonts, projectFonts] = await Promise.all([
          api.projects.get(projectId),
          api.projects.images(projectId),
          api.fonts.list().catch(() => []),
          api.projects.fonts(projectId).catch(() => []),
        ])
        setProject(nextProject); setPages(nextPages); setContextText(JSON.stringify(nextProject.translation_context || {}, null, 2))
        setFontResources([...new Map([...projectFonts, ...globalFonts].map(font => [font.name, font])).values()])
        const target = imageId || nextPages[0]?.id
        if (target) {
          if (!imageId) navigate(`/projects/${projectId}/editor/${target}`, {replace: true})
          await loadPage(target)
        }
      } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
      finally { setLoading(false) }
    }
    void load()
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

  const commitPendingRegion = async (id: string, change: PendingChange) => {
    if (pending.current.get(id) !== change) return regionsRef.current.find(region => region.id === id)
    window.clearTimeout(change.timer)
    pending.current.delete(id)
    try {
      const updated = await api.regions.update(id, change.patch)
      setRegionState(regionsRef.current.map(region => region.id === id ? updated : region))
      setUndoStack(stack => [...stack.slice(-99), {id, before: change.before, after: updated}]); setRedoStack([])
      return updated
    } catch (reason) {
      setRegionState(regionsRef.current.map(region => region.id === id ? change.before : region))
      setError(reason instanceof Error ? reason.message : String(reason))
      throw reason
    }
  }

  const updateRegion = async (id: string, patch: Partial<TextRegion>) => {
    const current = regionsRef.current.find(region => region.id === id)
    if (!current) return
    const existing = pending.current.get(id)
    if (existing) window.clearTimeout(existing.timer)
    const before = existing?.before || current
    const mergedPatch = {...existing?.patch, ...patch}
    setRegionState(regionsRef.current.map(region => region.id === id ? {...region, ...patch} : region))
    const change: PendingChange = {before, patch: mergedPatch, timer: 0}
    change.timer = window.setTimeout(() => {void commitPendingRegion(id, change).catch(() => undefined)}, 320)
    pending.current.set(id, change)
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

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.altKey) return
      if (dialogOpen || showContext || editorBusy || isEditableTarget(event.target)) return

      const commandKey = event.ctrlKey || event.metaKey
      const key = event.key.toLowerCase()
      if (commandKey) {
        if (key === 'a' && !event.shiftKey) {
          event.preventDefault()
          useEditorStore.getState().selectMany(regionsRef.current.map(region => region.id))
        } else if (key === 'z') {
          const direction = event.shiftKey ? 'redo' : 'undo'
          if (direction === 'undo' ? !undoStack.length : !redoStack.length) return
          event.preventDefault()
          void applyHistory(direction)
        } else if (key === 'y' && !event.shiftKey && redoStack.length) {
          event.preventDefault()
          void applyHistory('redo')
        }
        return
      }

      const tool = TOOL_SHORTCUTS[event.code]
      if (tool && !event.shiftKey) {
        event.preventDefault()
        useEditorStore.getState().setTool(tool)
        return
      }
      const view = VIEW_SHORTCUTS[event.code]
      if (view && !event.shiftKey) {
        event.preventDefault()
        useEditorStore.getState().setView(view)
        return
      }
      const store = useEditorStore.getState()
      if (event.code === 'Minus' || event.code === 'NumpadSubtract') {
        event.preventDefault()
        store.setZoom(store.zoom / 1.15)
      } else if (event.code === 'Equal' || event.code === 'NumpadAdd') {
        event.preventDefault()
        store.setZoom(store.zoom * 1.15)
      } else if ((event.code === 'Digit0' || event.code === 'Numpad0') && !event.shiftKey) {
        event.preventDefault()
        store.setZoom(1)
      }
    }
    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  }, [applyHistory, dialogOpen, editorBusy, redoStack.length, showContext, undoStack.length])

  const createRegion = async (polygon: number[][], bbox: number[]) => {
    if (!page) return
    const created = await api.regions.create(page.id, {polygon, bbox, reading_order: regions.length + 1, orientation: bbox[3] > bbox[2] * 1.2 ? 'vertical' : 'horizontal'})
    setRegionState([...regionsRef.current, created]); select(created.id); setTool('select')
  }
  const saveMask = async (id: string, blob: Blob) => {
    const updated = await api.regions.mask(id, blob)
    setRegionState(regionsRef.current.map(region => region.id === id ? updated : region)); setMaskRevision(value => value + 1)
  }
  const reloadCurrent = useCallback(async () => {
    if (!page) return
    const selectedId = useEditorStore.getState().selectedIds[0] || null
    const [nextRegions] = await Promise.all([loadPage(page.id, selectedId), refreshPages()])
    setMaskRevision(value => value + 1)
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
    const selectedId = useEditorStore.getState().selectedIds[0] || null
    const [nextPage, nextRegions] = await Promise.all([api.images.get(page.id), api.images.regions(page.id)])
    setPage(nextPage)
    setPages(current => current.map(item => item.id === nextPage.id ? nextPage : item))
    setRegionState(nextRegions)
    if (selectedId && !nextRegions.some(region => region.id === selectedId)) select(null)
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
        const result = await refreshAfterTask(deleted.rebuild_task.id)
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
  }, [confirmDialog, editorBusy, refreshAfterTask, selectedIds, select])

  useEffect(() => {
    const handleDelete = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.repeat || event.key !== 'Delete') return
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || showContext || dialogOpen) return
      if (isEditableTarget(event.target) || !selectedIds.length) return
      event.preventDefault()
      void deleteSelectedRegion()
    }
    window.addEventListener('keydown', handleDelete)
    return () => window.removeEventListener('keydown', handleDelete)
  }, [deleteSelectedRegion, dialogOpen, selectedIds.length, showContext])

  useEffect(() => {
    const handleNudge = (event: KeyboardEvent) => {
      const directions: Record<string, [number, number]> = {
        ArrowLeft: [-1, 0],
        ArrowRight: [1, 0],
        ArrowUp: [0, -1],
        ArrowDown: [0, 1],
      }
      const direction = directions[event.key]
      if (!direction || event.defaultPrevented || event.isComposing) return
      if (event.ctrlKey || event.metaKey || event.altKey || showContext || dialogOpen || editorBusy) return
      if (tool !== 'select' || view === 'comparison' || isEditableTarget(event.target) || !selectedIds.length) return
      event.preventDefault()
      const distance = event.shiftKey ? 10 : 1
      const dx = direction[0] * distance
      const dy = direction[1] * distance
      const selected = new Set(selectedIds)
      for (const region of regionsRef.current) {
        if (!selected.has(region.id) || region.locked) continue
        if (view === 'translated') {
          const bbox = region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox
          const polygon = region.translated_polygon?.length >= 3 ? region.translated_polygon : region.polygon
          void updateRegion(region.id, {
            translated_bbox: [bbox[0] + dx, bbox[1] + dy, bbox[2], bbox[3]],
            translated_polygon: polygon.map(point => [point[0] + dx, point[1] + dy]),
          })
        } else {
          void updateRegion(region.id, {
            bbox: [region.bbox[0] + dx, region.bbox[1] + dy, region.bbox[2], region.bbox[3]],
            polygon: region.polygon.map(point => [point[0] + dx, point[1] + dy]),
          })
        }
      }
    }
    window.addEventListener('keydown', handleNudge)
    return () => window.removeEventListener('keydown', handleNudge)
  }, [dialogOpen, editorBusy, selectedIds, showContext, tool, view])

  const action = async (name: string, options: Record<string, unknown> = {}, targetRegionId?: string) => {
    if (editorBusy) return
    const selected = regionsRef.current.find(region => region.id === (targetRegionId || selectedIds[0]))
    try {
      if (!targetRegionId && selectedIds.length > 1 && ['ocr','translate','inpaint','render'].includes(name)) {
        if (!page || regionActionBusy.current) return
        const regionIds = selectedIds.filter(id => regionsRef.current.some(region => region.id === id))
        if (regionIds.length < 2) return
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
          for (const id of regionIds) {
            const change = pending.current.get(id)
            if (change) await commitPendingRegion(id, change)
          }
          const task = await api.images.process(page.id, {
            ...stage,
            force: true,
            options: {...options, ...(name === 'ocr' ? {crop_padding: 4} : {}), region_ids: regionIds},
          })
          const result = await refreshAfterTask(task.id)
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
            const result = await refreshAfterTask(mergedResult.rebuild_task.id)
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
          let activeRegion = selected
          const pendingChange = pending.current.get(selectedId)
          if (pendingChange) activeRegion = await commitPendingRegion(selectedId, pendingChange) || activeRegion
          const updated = await api.regions.update(selectedId, {visible: !activeRegion.visible})
          setRegionState(regionsRef.current.map(region => region.id === selectedId ? updated : region))
          if (page?.rendered_url || page?.text_layer_url) {
            setNotice(updated.visible ? '正在恢复当前区域的译文显示…' : '正在从译图中隐藏当前区域…')
            const task = await api.regions.stage(selectedId, 'render')
            const result = await refreshAfterTask(task.id)
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
        regionActionBusy.current = true
        const runningMessage = ({ocr: '正在重新识别当前区域…', translate: '正在重新翻译当前区域…', inpaint: '正在重新生成 Mask 并修复背景…', render: '正在重新生成排版…'} as Record<string, string>)[name]
        blurActiveControl()
        setRunningRegionAction(name); setNotice(runningMessage || '正在处理当前区域…'); setError('')
        try {
          let activeRegion = selected
          const pendingChange = pending.current.get(selectedId)
          if (pendingChange) activeRegion = await commitPendingRegion(selectedId, pendingChange) || activeRegion
          const before = activeRegion
          const task = await api.regions.stage(selectedId, name, options)
          const result = await refreshAfterTask(task.id)
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
      else if (['dilate','erode','clear-mask'].includes(name)) {
        const operation = name === 'clear-mask' ? 'clear' : name
        const updated = await api.regions.maskOperation(selectedId, operation, 3)
        setRegionState(regionsRef.current.map(region => region.id === selectedId ? updated : region)); setMaskRevision(value => value + 1)
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
      y: Math.max(8, Math.min(event.clientY, window.innerHeight - 92)),
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

  const reorderPage = async (pageId: string, fromIndex: number, toIndex: number) => {
    if (editorBusy || fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= pages.length || toIndex >= pages.length) return
    const previousPages = [...pages]
    const activePageId = imageId || page?.id
    const selectedBefore = useEditorStore.getState().selectedIds
    const nextPages = [...pages]
    const [moved] = nextPages.splice(fromIndex, 1)
    nextPages.splice(toIndex, 0, moved)
    setPages(nextPages)
    setReorderingPages(true); setError(''); setNotice('正在保存页面顺序…')
    try {
      const direction = toIndex > fromIndex ? 1 : -1
      for (let index = fromIndex + direction; direction > 0 ? index <= toIndex : index >= toIndex; index += direction) {
        await api.images.reorder(pageId, previousPages[index].order_index)
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

  const flushPendingRegions = async () => {
    const changes = [...pending.current.entries()]
    await Promise.all(changes.map(([id, change]) => commitPendingRegion(id, change)))
  }

  // Only a completed OCR stage is ready for manual confirmation. Detection
  // boxes can already exist while OCR is still running (or after an interrupted
  // task), and treating those partial results as reviewed would skip OCR.
  const awaitingManualReview = page?.status === 'OCR_DONE'

  const processCurrentPage = async () => {
    if (!page || editorBusy) return
    const continueAfterReview = awaitingManualReview
    const replacingExisting = regionsRef.current.length > 0
    if (continueAfterReview && !await confirmDialog({
      title: '确认区域并继续处理？',
      message: '请确认当前仅保留需要清除和翻译的文字区域。继续后系统才会生成 Mask、清除原文字、修复背景并重新排版。',
      tone: 'warning',
      confirmLabel: '确认并继续',
    })) return
    if (!continueAfterReview && replacingExisting && !await confirmDialog({
      title: '重新检测当前页？',
      message: '系统将重新检测文字区域并执行 OCR，但会在清除文字和修复背景前停止，等待人工核对。已锁定区域会保留。',
      tone: 'warning',
      confirmLabel: '重新检测',
    })) return
    blurActiveControl()
    setSchedulingProcess(true)
    setRunningPageTask(null)
    setNotice(continueAfterReview ? '正在根据已确认的区域生成净图和译文…' : '正在检测文字区域并执行 OCR…')
    try {
      await flushPendingRegions()
      const task = await api.images.process(page.id, {
        start_stage: continueAfterReview ? 'translation' : 'detection',
        end_stage: continueAfterReview ? 'rendering' : 'ocr',
        force: continueAfterReview || replacingExisting,
        options: {},
      })
      setRunningPageTask(task)
      const result = await refreshAfterTask(task.id, syncTaskProgress)
      if (result?.task.status === 'COMPLETED') {
        setNotice(continueAfterReview ? '净图和译文已生成。' : '检测和 OCR 已完成，请核对文字区域，删除或调整后再点击“确认区域并继续”。')
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSchedulingProcess(false)
      setRunningPageTask(null)
    }
  }

  const batchRecognize = async () => {
    if (!pages.length || editorBusy) return
    if (!await confirmDialog({
      title: '批量检测与识别？',
      message: '所有页面只执行文字检测和 OCR，不会清除文字或修改背景。完成后需要逐页人工核对并确认继续。',
      tone: 'info',
      confirmLabel: '开始批量识别',
    })) return
    blurActiveControl()
    setSchedulingBatch(true); setError(''); setNotice(`正在批量识别 ${pages.length} 页…`)
    try {
      const tasks = await api.projects.batch(projectId, {start_stage: 'detection', end_stage: 'ocr', force: false, options: {}})
      const results = await Promise.all(tasks.map(task => waitForTask(task.id)))
      await refreshPages()
      const failed = results.filter(task => task?.status === 'FAILED')
      if (failed.length) { setNotice(''); setError(`${failed.length} 个页面识别失败，请检查页面状态。`) }
      else setNotice('批量检测和 OCR 已完成，请逐页核对文字区域。')
    } catch (reason) {
      setNotice(''); setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSchedulingBatch(false)
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
      await reloadCurrent()
      setNotice('当前页已恢复到默认原图状态。')
    } catch (reason) {
      setNotice(''); setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setResettingPage(false)
    }
  }

  const selectedScope = selectedIds.length > 1 ? `${selectedIds.length} 个所选区域` : '当前区域'
  const busyLabel = deletingPageId
    ? '正在删除图片…'
    : reorderingPages
      ? '正在保存页面顺序…'
      : uploadingPages
      ? '正在导入图片…'
      : resettingPage
      ? '正在重置当前页…'
      : schedulingBatch
      ? '正在批量检测并识别…'
      : schedulingProcess
        ? pageTaskBusyLabel(runningPageTask, awaitingManualReview)
        : ({ocr: `正在重新 OCR ${selectedScope}…`, translate: `正在重新翻译${selectedScope}…`, inpaint: `正在重新修复${selectedScope}…`, render: `正在重新排版${selectedScope}…`, merge: '正在合并所选区域…', delete: '正在删除所选区域…', visibility: '正在更新区域显示…'} as Record<string, string>)[runningRegionAction || ''] || '正在处理…'

  if (loading) return <Loading label="正在打开 MangaFlow 工作台…" />
  const headerButtonClass = `${buttonClass} !h-8 !min-h-8 px-3 py-0 text-[11px]`
  if (!project) return <div className="grid h-full place-content-center text-center"><h2>无法打开项目</h2><p className="text-muted">{error}</p><Link className="text-accent" to="/projects">返回项目</Link></div>
  return <>
    {editorTarget && createPortal(<>
      <div className="ml-auto flex items-center gap-2">
        <button className={headerButtonClass} onClick={() => setShowContext(true)} disabled={editorBusy}><BookOpenText size={16}/>翻译上下文</button>
        <button className={`${dangerButtonClass} !h-8 !min-h-8 px-3 py-0 text-[11px]`} onClick={() => void resetCurrentPage()} disabled={!page || editorBusy || (page.status === 'UPLOADED' && !regions.length && !page.clean_url && !page.rendered_url)}>{resettingPage ? <LoaderCircle className="animate-spin" size={16}/> : <RotateCcw size={16}/>}<span>{resettingPage ? '恢复中…' : '重置当前页'}</span></button>
        <button className={headerButtonClass} disabled={editorBusy} onClick={() => api.projects.export(projectId, ['translated','clean','text_layer','json','masks','project'])}><Download size={16}/>导出</button>
        <button className={`${primaryButtonClass} !h-8 !min-h-8 px-3 py-0 text-[11px]`} onClick={processCurrentPage} disabled={!page || editorBusy} title={awaitingManualReview ? '确认保留的区域并继续生成净图和译文' : '仅执行文字检测和 OCR'}>{schedulingProcess ? <LoaderCircle className="animate-spin" size={16}/> : <Play size={16}/>}<span>{schedulingProcess ? '处理中…' : awaitingManualReview ? '确认区域并继续' : regions.length ? '重新检测并识别' : '检测并识别'}</span></button>
        <button className={headerButtonClass} onClick={() => void batchRecognize()} disabled={editorBusy}>{schedulingBatch ? <LoaderCircle className="animate-spin" size={16}/> : <Layers3 size={16}/>}<span>{schedulingBatch ? '批量识别中…' : '批量识别'}</span></button>
      </div>
    </>, editorTarget)}
    <div className="grid h-full grid-rows-[44px_minmax(0,1fr)] bg-canvas">
    <EditorToolbar onUndo={() => applyHistory('undo')} onRedo={() => applyHistory('redo')} canUndo={!!undoStack.length} canRedo={!!redoStack.length} disabled={editorBusy}/>
    <div className="grid min-h-0 grid-cols-[192px_minmax(0,1fr)_320px] max-[1200px]:grid-cols-[176px_minmax(0,1fr)_300px]">
      <aside className="flex min-h-0 flex-col border-r border-line-subtle bg-panel">
        <div className="flex h-[38px] shrink-0 items-center justify-between border-b border-line-subtle px-3 font-mono text-[10px] uppercase text-muted"><span>页面</span><button className={cn(iconButtonClass, 'border-0 bg-transparent')} disabled={editorBusy} title="导入图片" aria-label="导入图片" onClick={openImagePicker}>{uploadingPages ? <LoaderCircle className="animate-spin" size={16}/> : <ImagePlus size={16}/>}</button></div>
        <div ref={pageListRef} className="min-h-0 flex-1 overflow-auto p-2 [scrollbar-color:#44443e_transparent] [scrollbar-width:thin]" onContextMenu={openImagePickerFromBlank}>{pages.map((item, index) => <button
          key={item.id}
          data-page-id={item.id}
          data-page-index={index}
          className={cn(
            'relative mb-2 block w-full cursor-grab touch-pan-y rounded-lg border border-transparent bg-transparent p-2 text-left outline-none transition-[background-color,opacity,transform,box-shadow] hover:bg-raised active:cursor-grabbing',
            page?.id === item.id && 'bg-raised',
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
          <div className="relative flex aspect-[3/4] w-full justify-center overflow-hidden bg-[#0a0a09]"><img className="pointer-events-none size-full select-none object-contain" draggable={false} src={item.rendered_url || item.original_url} alt={item.filename}/><span className="absolute bottom-1 left-1 bg-canvas px-1 py-0.5 font-mono text-[9px]">{String(index + 1).padStart(2,'0')}</span></div>
          <small className="mt-2 block truncate text-[10px] text-secondary" title={item.filename}>{item.filename}</small><i className={cn('mt-1 block truncate font-mono text-[8px] not-italic', pageState(item).className)}>{pageState(item).label}</i>
        </button>)}
        {!pages.length && <div className="flex min-h-full items-center justify-center"><button className={`${buttonClass} min-h-[140px] w-full flex-col border-dashed bg-transparent text-muted`} disabled={editorBusy} onClick={openImagePicker}><Upload size={22}/>导入漫画图片</button></div>}
        </div>
      </aside>
      <main className="relative min-h-0 min-w-0 bg-canvas bg-[linear-gradient(45deg,var(--color-panel)_25%,transparent_25%),linear-gradient(-45deg,var(--color-panel)_25%,transparent_25%),linear-gradient(45deg,transparent_75%,var(--color-panel)_75%),linear-gradient(-45deg,transparent_75%,var(--color-panel)_75%)] [background-position:0_0,0_10px,10px_-10px,-10px_0] [background-size:20px_20px]">{page ? <MangaCanvas key={page.id} page={page} regions={regions} onCreate={createRegion} onUpdate={updateRegion} onSaveMask={saveMask} onRegionAction={(id, name, options) => {void action(name, options, id)}} runningAction={runningRegionAction} maskRevision={maskRevision}/> : <div className="flex h-full flex-col items-center justify-center text-muted"><ImagePlus size={36}/><h3 className="mb-1 mt-3 text-base text-secondary">导入漫画页面</h3><p className="mb-4 mt-0 text-xs">支持 JPG、PNG 和 WebP，可一次选择多张。</p><button className={`${primaryButtonClass} !min-h-[34px]`} disabled={editorBusy} onClick={openImagePicker}>选择图片</button></div>}</main>
      <RegionProperties region={selectedIds.length === 1 ? regions.find(region => region.id === selectedIds[0]) : undefined} selectedCount={selectedIds.length} fontOptions={[...DEFAULT_FONT_FAMILIES.map(font => font.name), ...fontResources.map(font => font.name)]} busyAction={runningRegionAction} onUpdate={updateRegion} onAction={action}/>
    </div>
    {pageContextMenu && createPortal(<div ref={pageContextMenuRef} className="fixed z-[110] w-[182px] overflow-hidden rounded-xl border border-line-strong bg-[rgb(24_27_23/.98)] p-1 text-secondary shadow-dialog backdrop-blur-xl" style={{left: pageContextMenu.x, top: pageContextMenu.y}} role="menu" aria-label="图片操作">
      <div className="flex h-9 items-center border-b border-line-subtle px-2"><span className="min-w-0 truncate text-[10px] text-muted" title={pages.find(item => item.id === pageContextMenu.pageId)?.filename}>{pages.find(item => item.id === pageContextMenu.pageId)?.filename}</span></div>
      <button className="mt-1 flex h-9 w-full cursor-pointer items-center gap-2 rounded-lg border-0 bg-transparent px-3 text-left text-[11px] text-[#d9a09a] outline-none transition-colors hover:bg-danger/20 hover:text-white" type="button" role="menuitem" onClick={() => void deletePage(pageContextMenu.pageId)}><Trash2 size={15}/>删除图片</button>
    </div>, document.body)}
    {showContext && <div className="fixed inset-0 z-[90] grid place-items-center bg-black/60 backdrop-blur-sm"><div className="w-[min(650px,70vw)] overflow-hidden rounded-xl border border-line-strong bg-surface shadow-dialog"><header className="flex items-center justify-between border-b border-line-subtle px-5 py-4"><div><span className={eyebrowClass}>PROJECT CONTEXT</span><h2 className="mb-0 mt-1 text-xl">翻译上下文</h2></div><button className={cn(iconButtonClass, 'border-0 bg-transparent text-xl')} onClick={() => setShowContext(false)}>×</button></header><p className="px-5 text-xs text-muted">以 JSON 保存人物名、术语、口癖、称谓和章节背景；整页翻译时会随 Region ID 一起发送。</p><textarea className={cn(textareaClass, 'mx-5 mb-5 min-h-[310px] w-[calc(100%-40px)] font-mono text-[11px] leading-relaxed')} value={contextText} onChange={event => setContextText(event.target.value)} spellCheck={false}/><footer className="flex justify-end gap-2 border-t border-line-subtle px-5 py-4"><button className={`${buttonClass} !min-h-[34px]`} onClick={() => setShowContext(false)}>取消</button><button className={`${primaryButtonClass} !min-h-[34px]`} onClick={async () => {try {const parsed = JSON.parse(contextText); const updated = await api.projects.update(projectId, {translation_context: parsed}); setProject(updated); setShowContext(false)} catch (reason) {setError(reason instanceof Error ? reason.message : 'JSON 格式错误')}}}>保存上下文</button></footer></div></div>}
    {editorBusy && <div className="fixed inset-0 z-[75] flex cursor-wait items-center justify-center bg-canvas/50 backdrop-blur-[2px]" role="status" aria-live="assertive" aria-label={busyLabel}><div className="flex min-w-[260px] items-center gap-3 rounded-xl bg-surface px-5 py-4 text-ink shadow-panel"><LoaderCircle className="shrink-0 animate-spin text-accent" size={24}/><span className="flex flex-col gap-1"><strong className="text-xs">{busyLabel}</strong><small className="text-[10px] text-muted">任务完成前暂时不能进行其他操作</small></span></div></div>}
    {!editorBusy && (error ? <div className="pointer-events-none fixed left-1/2 top-[72px] z-[120] flex w-max max-w-[min(600px,calc(100vw-36px))] -translate-x-1/2 items-center gap-3 rounded-lg bg-danger/25 py-3 pl-4 pr-2 text-[#ffe3df] shadow-panel" role="alert"><span className="min-w-0 text-[11px] leading-relaxed">{error}</span><button className="pointer-events-auto grid size-7 cursor-pointer place-items-center rounded-md border-0 bg-transparent p-0 text-lg text-current opacity-70 hover:bg-white/10 hover:opacity-100" type="button" aria-label="关闭提示" onClick={() => {setError(''); setNotice('')}}>×</button></div> : notice && <div className="pointer-events-none fixed left-1/2 top-[72px] z-[120] flex w-max max-w-[min(600px,calc(100vw-36px))] -translate-x-1/2 items-center gap-3 rounded-lg bg-success/20 py-3 pl-4 pr-2 text-[#c7f7e9] shadow-panel" role="status"><span className="min-w-0 text-[11px] leading-relaxed">{notice}</span><button className="pointer-events-auto grid size-7 cursor-pointer place-items-center rounded-md border-0 bg-transparent p-0 text-lg text-current opacity-70 hover:bg-white/10 hover:opacity-100" type="button" aria-label="关闭提示" onClick={() => {setNotice(''); setError('')}}>×</button></div>)}
    <input ref={fileRef} disabled={editorBusy} hidden type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" multiple onChange={event => void upload(event.currentTarget.files)}/>
  </div>
  </>
}

function editable(region: TextRegion): Partial<TextRegion> {
  const keys: (keyof TextRegion)[] = ['polygon','bbox','translated_polygon','translated_bbox','source_text','translated_text','confidence','orientation','reading_order','panel_id','bubble_id','region_type','font_size','font_family','font_weight','text_color','stroke_color','stroke_width','alignment','line_spacing','character_spacing','rotation','opacity','locked','visible']
  return Object.fromEntries(keys.map(key => [key, region[key]])) as Partial<TextRegion>
}

const TOOL_SHORTCUTS: Record<string, Tool> = {
  KeyV: 'select',
  KeyR: 'rectangle',
  KeyP: 'polygon',
  KeyL: 'lasso',
  KeyB: 'mask-brush',
  KeyE: 'mask-eraser',
}

const VIEW_SHORTCUTS: Record<string, ViewMode> = {
  Digit1: 'original',
  Numpad1: 'original',
  Digit2: 'clean',
  Numpad2: 'clean',
  Digit3: 'translated',
  Numpad3: 'translated',
  Digit4: 'comparison',
  Numpad4: 'comparison',
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

function blurActiveControl() {
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
}

function pageTaskBusyLabel(task: ProcessingTask | null, continueAfterReview: boolean): string {
  if (!task?.current_stage) return continueAfterReview ? '正在生成净图和译文…' : '正在检测并识别当前页…'
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

function pageState(page: ImagePage): {className: string, label: string} {
  const status = page.status === 'NEEDS_REVIEW' ? 'COMPLETED' : page.status
  const labels: Record<string, string> = {
    UPLOADED: '未识别', DETECTING: '检测中', DETECTED: '待识别', OCR_RUNNING: '识别中', OCR_DONE: '待确认',
    TRANSLATING: '翻译中', TRANSLATED: '已翻译', MASK_GENERATING: '生成 Mask', INPAINTING: '修复中',
    INPAINTED: '已修复', LAYOUTING: '排版中', RENDERING: '渲染中', COMPLETED: '已完成', FAILED: '失败',
  }
  const className = status === 'FAILED' ? 'text-danger' : status === 'COMPLETED' ? 'text-accent' : 'text-muted'
  return {className, label: labels[status] || status}
}
