import {RegionLabel} from './RegionLabel'
import Konva from 'konva'
import { ChevronDown, Eraser, Languages, Layers3, ScanText, TextCursorInput } from 'lucide-react'
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text } from 'react-konva'
import type { ImagePage, TextRegion } from '../../../types'
import {cn} from '../../../ui'
import {ButtonLoading} from '../../../components/LoadingUI'
import {formatShortcut, shortcutToAria, useShortcutStore} from '../../shortcuts/store'
import { useEditorStore } from '../store'
import { RegionPolygonEditor } from './RegionPolygonEditor'
import { useImage } from '../hooks/useImage'
import {useCanvasTouch} from '../hooks/useCanvasTouch'
import {canvasPalette, polygonDraftStyle} from '../lib/canvasPalette'
import {shapePreviewRegion} from '../lib/shapeText'
import {currentTextLayer} from '../lib/renderedText'
import {extendedPerspectiveQuad, isPerspectiveQuad, perspectiveQuadSize, warpCanvasToQuad, type WarpedCanvas} from '../lib/perspectiveText'
import {sizeText, usesAutomaticFontSize} from '../lib/fontSizing'
import {constrainGroupMove, moveRegions, type RegionChange} from '../lib/regionEditing'

interface Props {
  page: ImagePage
  regions: TextRegion[]
  onCreate: (polygon: number[][], bbox: number[]) => Promise<boolean>
  onUpdate: (id: string, patch: Partial<TextRegion>) => Promise<void>
  onUpdateMany?: (changes: RegionChange[]) => Promise<void>
  onRegionAction: (ids: string[], action: string, options?: Record<string, unknown>) => void
  onPageContextMenu?: (position: {x: number, y: number}) => void
  processingRegionIds?: string[]
  runningAction: string | null
  interactionKey: string
}

type RenderStyle = {textColor: string, strokeColor: string, strokeWidth: number}

const VERTICAL_FORMS: Record<string, string> = {
  '（': '︵', '）': '︶', '(': '︵', ')': '︶', '【': '︻', '】': '︼',
  '「': '﹁', '」': '﹂', '『': '﹃', '』': '﹄',
  ',': '︐', '，': '︐', '、': '︑', '.': '︒', '．': '︒', '。': '︒',
  ':': '︓', '：': '︓', ';': '︔', '；': '︔', '!': '︕', '！': '︕', '?': '︖', '？': '︖',
  '…': '︙', '—': '︱', 'ー': '︱', '～': '︴', '〜': '︴',
}

const bboxPolygon = ([x, y, width, height]: number[]) => [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]
const MIN_ZOOM = .05
const MAX_ZOOM = 6
const WHEEL_ZOOM_SPEED = .001
const MARQUEE_DRAG_THRESHOLD = 3
const POLYGON_CLOSE_DISTANCE = polygonDraftStyle.closeDistance
const COMPARISON_GAP = 32
const PERSPECTIVE_PREVIEW_PIXEL_BUDGET = 8_000_000
const PERSPECTIVE_PREVIEW_MAX_RATIO = 6
type ViewTransform = {zoom: number, pan: {x: number, y: number}}
type MarqueePoint = [number, number]
type MarqueeSelection = {start: MarqueePoint, current: MarqueePoint}
type RegionGeometry = {polygon: number[][], bbox: number[]}
type TranslatedGeometryPreview = {regionId: string, polygon: number[][], rotation: number}
const polygonBbox = (points: number[][]) => {
  const xs = points.map(point => point[0]), ys = points.map(point => point[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)]
}
const normalizedRotation = (degrees: number) => {
  const value = ((degrees + 180) % 360 + 360) % 360 - 180
  return Math.abs(value) < .001 ? 0 : value
}
const flat = (points: number[][]) => points.flatMap(point => point)
const regionGeometry = (region: TextRegion, translated = false): RegionGeometry => {
  if (!translated) return {
    polygon: region.polygon.length >= 3 ? region.polygon : bboxPolygon(region.bbox),
    bbox: region.bbox,
  }
  const bbox = region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox
  return {
    polygon: region.translated_polygon?.length >= 3 ? region.translated_polygon : bboxPolygon(bbox),
    bbox,
  }
}
const pointToSegmentDistance = (point: MarqueePoint, start: number[], end: number[]) => {
  const dx = end[0] - start[0], dy = end[1] - start[1]
  const lengthSquared = dx * dx + dy * dy
  if (lengthSquared <= Number.EPSILON) return Math.hypot(point[0] - start[0], point[1] - start[1])
  const ratio = Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / lengthSquared))
  return Math.hypot(point[0] - (start[0] + ratio * dx), point[1] - (start[1] + ratio * dy))
}
const polygonContainsPoint = (polygon: number[][], point: MarqueePoint, edgeTolerance: number) => {
  if (polygon.length < 3) return false
  let inside = false
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const start = polygon[previous], end = polygon[index]
    if (pointToSegmentDistance(point, start, end) <= edgeTolerance) return true
    const crossesRay = (start[1] > point[1]) !== (end[1] > point[1]) &&
      point[0] < (end[0] - start[0]) * (point[1] - start[1]) / (end[1] - start[1]) + start[0]
    if (crossesRay) inside = !inside
  }
  return inside
}
function ComparisonLabel({x, scale, text}: {x: number, scale: number, text: string}) {
  return <Group x={x} y={12 * scale} scaleX={scale} scaleY={scale} listening={false}>
    <Rect width={48} height={24} cornerRadius={6} fill="rgba(24,24,24,.86)" stroke="rgba(255,255,255,.18)" strokeWidth={1}/>
    <Text width={48} height={24} text={text} align="center" verticalAlign="middle" fontFamily="Noto Sans CJK SC, sans-serif" fontSize={10} fontStyle="bold" fill="#f2f2f2"/>
  </Group>
}

function MangaCanvasComponent({ page, regions, onCreate, onUpdate, onUpdateMany, onRegionAction, onPageContextMenu, processingRegionIds, runningAction }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState({width: 800, height: 700})
  const [pan, setPan] = useState({x: 0, y: 0})
  const [draft, setDraft] = useState<number[][]>([])
  const [polygonPreview, setPolygonPreview] = useState<MarqueePoint | null>(null)
  const [drawing, setDrawing] = useState(false)
  const [touchNavigating, setTouchNavigating] = useState(false)
  const [middlePanningActive, setMiddlePanningActive] = useState(false)
  const [layersCollapsed, setLayersCollapsed] = useState(() => {
    try { return localStorage.getItem('mangaflow.layers-collapsed.v1') !== 'false' } catch { return true }
  })
  const [hoveredRegionId, setHoveredRegionId] = useState<string | null>(null)
  const [checkerboard, setCheckerboard] = useState(false)
  useEffect(() => {
    try { localStorage.setItem('mangaflow.layers-collapsed.v1', String(layersCollapsed)) } catch { /* Storage may be disabled. */ }
  }, [layersCollapsed])
  const [contextMenu, setContextMenu] = useState<{x: number, y: number} | null>(null)
  const [marquee, setMarquee] = useState<MarqueeSelection | null>(null)
  const [translatedGeometryPreview, setTranslatedGeometryPreview] = useState<TranslatedGeometryPreview | null>(null)
  const middlePanning = useRef(false)
  const middlePointer = useRef({x: 0, y: 0})
  const marqueeCandidate = useRef<MarqueeSelection | null>(null)
  const marqueeActive = useRef(false)
  const polygonSubmitting = useRef(false)
  const view = useEditorStore(state => state.view)
  const tool = useEditorStore(state => state.tool)
  const zoom = useEditorStore(state => state.zoom)
  const viewReset = useEditorStore(state => state.viewReset)
  const setZoom = useEditorStore(state => state.setZoom)
  const selectedIds = useEditorStore(state => state.selectedIds)
  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds])
  const processingIdSet = useMemo(() => new Set(processingRegionIds), [processingRegionIds])
  const existingRegionIds = useMemo(() => new Set(regions.map(region => region.id)), [regions])
  const contextRegionIds = contextMenu ? selectedIds.filter(id => existingRegionIds.has(id)) : []
  const contextRegion = contextRegionIds.length === 1 ? regions.find(region => region.id === contextRegionIds[0]) : undefined
  const select = useEditorStore(state => state.select)
  const selectMany = useEditorStore(state => state.selectMany)
  const layers = useEditorStore(state => state.layers)
  const toggleLayer = useEditorStore(state => state.toggleLayer)
  const comparison = view === 'comparison'
  const cleanSource = page.clean_url || page.original_url
  const originalState = useImage(comparison || layers.original ? page.original_url : null)
  const cleanState = useImage(comparison || layers.clean ? cleanSource : null)
  const original = originalState.image
  const clean = cleanState.image
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const currentView = useRef<ViewTransform>({zoom, pan})
  const targetView = useRef<ViewTransform>({zoom, pan})
  const wheelFrame = useRef<number | null>(null)
  const wheelFrameTime = useRef<number | null>(null)
  const selected = regions.find(region => region.id === selectedIds[0])
  const singleSelection = selectedIds.length === 1
  const editableRegionId = singleSelection && selected && !selected.locked && !runningAction && !touchNavigating && !comparison && tool === 'select'
    ? selected.id : null
  // Konva hit-testing follows paint order, including transparent polygon fills.
  // Raise the active editor and all its handles together, without mutating OCR
  // reading order or the separately rendered translation layer.
  const interactionRegions = useMemo(() => {
    const index = editableRegionId ? regions.findIndex(region => region.id === editableRegionId) : -1
    if (index < 0 || index === regions.length - 1) return regions
    return [...regions.slice(0, index), ...regions.slice(index + 1), regions[index]]
  }, [regions, editableRegionId])
  const editingTranslatedGeometry = view === 'translated'
  const groupSelection = useMemo(() => selectedIds.length > 1 && tool === 'select' && !comparison && !runningAction
    ? regions.filter(region => selectedIdSet.has(region.id) && !region.locked) : [], [regions,selectedIds,selectedIdSet,tool,comparison,runningAction])
  const groupSelectionIds = useMemo(() => new Set(groupSelection.length > 1 ? groupSelection.map(region => region.id) : []), [groupSelection])
  const groupDrag = useRef<TextRegion[] | null>(null)
  const beginGroupDrag = (event: Konva.KonvaEventObject<DragEvent>) => {
    if (event.target !== event.currentTarget) return
    event.cancelBubble = true
    groupDrag.current = groupSelection
  }
  const moveGroupDrag = (event: Konva.KonvaEventObject<DragEvent>) => {
    if (event.target !== event.currentTarget || !groupDrag.current) return
    event.cancelBubble = true
    const delta = constrainGroupMove(groupDrag.current,editingTranslatedGeometry,event.target.x(),event.target.y(),page.width,page.height)
    // Move one Konva group, not React region state or perspective raster tiles
    // for every pointer event. Save the entire gesture once on pointer release.
    event.target.position(delta)
  }
  const endGroupDrag = (event: Konva.KonvaEventObject<DragEvent>) => {
    if (event.target !== event.currentTarget || !groupDrag.current) return
    event.cancelBubble = true
    const delta = constrainGroupMove(groupDrag.current,editingTranslatedGeometry,event.target.x(),event.target.y(),page.width,page.height)
    const changes = moveRegions(groupDrag.current,editingTranslatedGeometry,delta.x,delta.y)
    groupDrag.current = null
    event.target.position({x:0,y:0})
    if (Math.abs(delta.x)+Math.abs(delta.y) < .001) return
    if (onUpdateMany) void onUpdateMany(changes).catch(() => undefined)
    else void Promise.all(changes.map(({id,patch}) => onUpdate(id,patch))).catch(() => undefined)
  }
  const translatedRenderRegions = useMemo(() => translatedGeometryPreview
    ? regions.map(region => region.id === translatedGeometryPreview.regionId ? {
      ...region,
      translated_polygon: translatedGeometryPreview.polygon,
      translated_bbox: polygonBbox(translatedGeometryPreview.polygon),
      rotation: translatedGeometryPreview.rotation,
    } : region)
    : regions, [regions, translatedGeometryPreview])
  const comparisonWidth = page.width * 2 + COMPARISON_GAP
  const contentWidth = view === 'comparison' ? comparisonWidth : page.width
  const margin = viewport.width < 640 ? 32 : 72
  const fitScale = Math.max(.01, Math.min((viewport.width - margin) / contentWidth, (viewport.height - margin) / page.height))
  const scale = Math.max(0.01, fitScale * zoom)
  const regionLabelScale = 1 / scale
  const origin = {x: (viewport.width - contentWidth * scale) / 2 + pan.x, y: (viewport.height - page.height * scale) / 2 + pan.y}
  useLayoutEffect(() => {
    if (wheelFrame.current !== null) cancelAnimationFrame(wheelFrame.current)
    wheelFrame.current = null
    wheelFrameTime.current = null
    const resetPan = {x: 0, y: 0}
    const currentZoom = useEditorStore.getState().zoom
    currentView.current = {zoom: currentZoom, pan: resetPan}
    targetView.current = {zoom: currentZoom, pan: resetPan}
    middlePanning.current = false
    marqueeCandidate.current = null
    marqueeActive.current = false
    setPan(resetPan)
    setDraft([])
    setPolygonPreview(null)
    setDrawing(false)
    setMiddlePanningActive(false)
    setContextMenu(null)
    setMarquee(null)
    setTranslatedGeometryPreview(null)
  }, [page.id, viewReset])

  const stopWheelAnimation = useCallback(() => {
    if (wheelFrame.current !== null) cancelAnimationFrame(wheelFrame.current)
    wheelFrame.current = null
    wheelFrameTime.current = null
    targetView.current = currentView.current
  }, [])

  const cancelMarquee = useCallback(() => {
    marqueeCandidate.current = null
    marqueeActive.current = false
    setMarquee(null)
  }, [])

  useCanvasTouch(containerRef, {
    singleFingerPan: tool === 'pan',
    zoom, pan, viewport, content: {width: contentWidth, height: page.height}, fitScale,
    onStart: () => {
      stopWheelAnimation(); cancelMarquee(); setDraft([]); setDrawing(false)
      setPolygonPreview(null); setTranslatedGeometryPreview(null); setTouchNavigating(true)
    },
    onEnd: () => setTouchNavigating(false),
    onChange: next => {
      currentView.current = next; targetView.current = next
      setPan(next.pan); setZoom(next.zoom)
    },
  })

  const beginMarquee = useCallback((point: MarqueePoint) => {
    cancelMarquee()
    marqueeCandidate.current = {start: point, current: point}
  }, [cancelMarquee])

  const finishMarquee = useCallback(() => {
    const candidate = marqueeCandidate.current
    const active = marqueeActive.current
    if (!candidate) return false
    const left = Math.min(candidate.start[0], candidate.current[0])
    const top = Math.min(candidate.start[1], candidate.current[1])
    const right = Math.max(candidate.start[0], candidate.current[0])
    const bottom = Math.max(candidate.start[1], candidate.current[1])
    const largeEnough = Math.hypot(right - left, bottom - top) * scale >= 4
    const matches = active && largeEnough && layers.detection
      ? regions.filter(region => {
        const [x, y, width, height] = regionGeometry(region, view === 'translated').bbox
        return x < right && x + width > left && y < bottom && y + height > top
      }).map(region => region.id)
      : []
    cancelMarquee()
    if (active) selectMany(matches)
    return active
  }, [cancelMarquee, layers.detection, regions, scale, selectMany, view])

  const animateWheelZoom = useCallback((time: number) => {
    const current = currentView.current
    const target = targetView.current
    const elapsed = wheelFrameTime.current === null ? 16.7 : Math.min(34, time - wheelFrameTime.current)
    const blend = 1 - Math.exp(-elapsed / 45)
    const zoomDistance = Math.abs(target.zoom - current.zoom)
    const panDistance = Math.hypot(target.pan.x - current.pan.x, target.pan.y - current.pan.y)
    const finished = zoomDistance < .0005 && panDistance < .08
    const next = finished ? target : {
      zoom: current.zoom + (target.zoom - current.zoom) * blend,
      pan: {
        x: current.pan.x + (target.pan.x - current.pan.x) * blend,
        y: current.pan.y + (target.pan.y - current.pan.y) * blend,
      },
    }
    currentView.current = next
    wheelFrameTime.current = time
    setPan(next.pan)
    setZoom(next.zoom)
    if (finished) {
      wheelFrame.current = null
      wheelFrameTime.current = null
    } else {
      wheelFrame.current = requestAnimationFrame(animateWheelZoom)
    }
  }, [setZoom])

  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const box = entries[0]?.contentRect
      if (box) setViewport({width: box.width, height: box.height})
    })
    if (containerRef.current) observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (wheelFrame.current !== null) return
    const view = {zoom, pan}
    currentView.current = view
    targetView.current = view
  }, [zoom, pan])

  useEffect(() => () => stopWheelAnimation(), [stopWheelAnimation])
  useEffect(() => {
    const finish = () => { finishMarquee() }
    window.addEventListener('mouseup', finish)
    window.addEventListener('touchend', finish)
    window.addEventListener('blur', cancelMarquee)
    return () => {
      window.removeEventListener('mouseup', finish)
      window.removeEventListener('touchend', finish)
      window.removeEventListener('blur', cancelMarquee)
      cancelMarquee()
    }
  }, [cancelMarquee, finishMarquee])

  useEffect(() => {
    const move = (event: MouseEvent) => {
      if (!middlePanning.current) return
      event.preventDefault()
      const dx = event.clientX - middlePointer.current.x
      const dy = event.clientY - middlePointer.current.y
      middlePointer.current = {x: event.clientX, y: event.clientY}
      setPan(value => ({x: value.x + dx, y: value.y + dy}))
    }
    const stop = (event: MouseEvent) => {
      if (event.button !== 1 && event.button !== 0) return
      middlePanning.current = false
      setMiddlePanningActive(false)
    }
    const cancel = () => {
      middlePanning.current = false
      setMiddlePanningActive(false)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', stop)
    window.addEventListener('blur', cancel)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', stop)
      window.removeEventListener('blur', cancel)
    }
  }, [])

  useEffect(() => {
    if (!contextMenu) return
    const dismiss = (event: PointerEvent) => {
      if (contextMenuRef.current?.contains(event.target as Node)) return
      setContextMenu(null)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setContextMenu(null) }
    const resize = () => setContextMenu(null)
    window.addEventListener('pointerdown', dismiss)
    window.addEventListener('keydown', escape)
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('keydown', escape)
      window.removeEventListener('resize', resize)
    }
  }, [contextMenu])

  useEffect(() => setContextMenu(null), [page.id, view])
  useEffect(() => setTranslatedGeometryPreview(null), [page.id, selected?.id, view])
  useEffect(() => {
    setDraft([])
    setPolygonPreview(null)
    setDrawing(false)
    middlePanning.current = false
    setMiddlePanningActive(false)
  }, [page.id, tool])
  useEffect(() => {
    if (view !== 'comparison') return
    cancelMarquee()
    setDrawing(false)
    setDraft([])
    setPolygonPreview(null)
  }, [cancelMarquee, view])

  const openRegionContextMenu = (event: Konva.KonvaEventObject<MouseEvent>, region: TextRegion) => {
    event.evt.preventDefault()
    event.evt.stopPropagation()
    event.cancelBubble = true
    if (runningAction) return
    // Konva synthesizes `click` for every mouse button. Read the live store so
    // this event cannot target a selection made stale by that event sequence.
    if (!useEditorStore.getState().selectedIds.includes(region.id)) select(region.id)
    const bounds = containerRef.current?.getBoundingClientRect()
    if (!bounds) return
    const width = 178
    const height = 190
    setContextMenu({
      x: Math.max(8, Math.min(event.evt.clientX - bounds.left, bounds.width - width - 8)),
      y: Math.max(8, Math.min(event.evt.clientY - bounds.top, bounds.height - height - 8)),
    })
  }

  const runRegionContextAction = (action: string) => {
    if (!contextMenu || runningAction) return
    // Ctrl+A can change the selection while this menu is already open. Read
    // that current selection, then freeze only the accepted command's scope.
    const regionIds = useEditorStore.getState().selectedIds.filter(id => existingRegionIds.has(id))
    if (!regionIds.length) {setContextMenu(null); return}
    const region = regionIds.length === 1 ? regions.find(item => item.id === regionIds[0]) : undefined
    // A mixed-orientation selection must retain each region's own direction.
    const options = action === 'ocr' ? {crop_padding: 4, ...(region ? {orientation: region.orientation} : {})} : {}
    // Close the menu as soon as the command is accepted; task scope and
    // progress are then presented by the workbench's loading indicator.
    setContextMenu(null)
    onRegionAction(regionIds, action, options)
  }

  const pointer = useCallback((event: Konva.KonvaEventObject<Event>) => {
    const stage = event.target.getStage()
    const position = stage?.getPointerPosition()
    if (!position) return null
    return [(position.x - origin.x) / scale, (position.y - origin.y) / scale] as [number, number]
  }, [origin.x, origin.y, scale])

  const handleStageContextMenu = (event: Konva.KonvaEventObject<MouseEvent>) => {
    event.evt.preventDefault()
    event.evt.stopPropagation()
    if (runningAction) return
    if (view === 'comparison') {
      setContextMenu(null); onPageContextMenu?.({x:event.evt.clientX,y:event.evt.clientY}); return
    }
    const point = pointer(event)
    // Translated text deliberately does not listen for Konva events. When the
    // detection overlay is hidden (or a child handle misses the event), resolve
    // the visually topmost region from its geometry instead.
    const candidates = layers.detection ? interactionRegions : layers.translated ? regions.filter(region => region.visible) : []
    const region = point && [...candidates].reverse().find(candidate =>
      polygonContainsPoint(regionGeometry(candidate, view === 'translated').polygon, point, 6 / scale))
    if (region) openRegionContextMenu(event, region)
    else {setContextMenu(null); onPageContextMenu?.({x:event.evt.clientX,y:event.evt.clientY})}
  }

  const handleDown = (event: Konva.KonvaEventObject<MouseEvent | TouchEvent>) => {
    if (runningAction) return
    stopWheelAnimation()
    cancelMarquee()
    if (event.evt instanceof MouseEvent) {
      if (event.evt.button === 1 || (event.evt.button === 0 && tool === 'pan')) {
        event.evt.preventDefault()
        event.cancelBubble = true
        middlePanning.current = true
        setContextMenu(null)
        middlePointer.current = {x: event.evt.clientX, y: event.evt.clientY}
        setMiddlePanningActive(true)
        return
      }
      if (event.evt.button !== 0) return
    }
    if (tool === 'pan') return
    if (view === 'comparison') return
    const point = pointer(event)
    if (!point) return
    if (tool === 'rectangle' || tool === 'lasso') { setDraft([point]); setDrawing(true); select(null) }
    const clickedBlankCanvas = event.target === event.target.getStage() || event.target.name() === 'canvas-background'
    if (clickedBlankCanvas && tool === 'select') {
      setContextMenu(null)
      select(null)
      beginMarquee(point)
    }
  }

  const handleMove = (event: Konva.KonvaEventObject<MouseEvent | TouchEvent>) => {
    const point = pointer(event)
    if (!point) return
    if (view === 'comparison') return
    if (tool === 'polygon' && draft.length) {
      const insidePage = point[0] >= 0 && point[0] <= page.width && point[1] >= 0 && point[1] <= page.height
      setPolygonPreview(insidePage ? point : null)
    }
    const candidate = marqueeCandidate.current
    if (candidate) {
      candidate.current = point
      const dragDistance = Math.hypot(point[0] - candidate.start[0], point[1] - candidate.start[1]) * scale
      if (!marqueeActive.current && dragDistance >= MARQUEE_DRAG_THRESHOLD) marqueeActive.current = true
      if (marqueeActive.current) {
        event.evt.preventDefault()
        setMarquee({...candidate})
      }
      return
    }
    if (!drawing) return
    if (tool === 'rectangle') setDraft(value => [value[0], point])
    if (tool === 'lasso') setDraft(value => [...value, point])
  }

  const handleUp = async () => {
    if (runningAction) return
    if (view === 'comparison') return
    if (marqueeCandidate.current) {
      finishMarquee()
      return
    }
    if (!drawing) return
    setDrawing(false)
    if (tool === 'rectangle' && draft.length === 2) {
      const [first, second] = draft
      const bbox = [Math.min(first[0], second[0]), Math.min(first[1], second[1]), Math.abs(second[0] - first[0]), Math.abs(second[1] - first[1])]
      if (bbox[2] > 5 && bbox[3] > 5) {
        if (await onCreate(bboxPolygon(bbox), bbox)) setDraft([])
      } else setDraft([])
    }
    if (tool === 'lasso' && draft.length > 3) {
      const submittedPoints = draft.map(point => [...point])
      if (await onCreate(submittedPoints, polygonBbox(submittedPoints))) setDraft([])
    } else if (tool === 'lasso') {
      setDraft([])
    }
  }

  const handleWheel = (event: Konva.KonvaEventObject<WheelEvent>) => {
    event.evt.preventDefault()
    const position = event.target.getStage()?.getPointerPosition()
    if (!position) return
    const rawDelta = event.evt.deltaY * (event.evt.deltaMode === 1 ? 16 : event.evt.deltaMode === 2 ? viewport.height : 1)
    const delta = Math.max(-160, Math.min(160, rawDelta))
    if (!delta) return
    const target = targetView.current
    const targetScale = Math.max(.01, fitScale * target.zoom)
    const targetOrigin = {
      x: (viewport.width - contentWidth * targetScale) / 2 + target.pan.x,
      y: (viewport.height - page.height * targetScale) / 2 + target.pan.y,
    }
    const nextZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, target.zoom * Math.exp(-delta * WHEEL_ZOOM_SPEED)))
    const nextScale = fitScale * nextZoom
    const imagePoint = {x: (position.x - targetOrigin.x) / targetScale, y: (position.y - targetOrigin.y) / targetScale}
    const centeredOrigin = {x: (viewport.width - contentWidth * nextScale) / 2, y: (viewport.height - page.height * nextScale) / 2}
    targetView.current = {
      zoom: nextZoom,
      pan: {
        x: position.x - imagePoint.x * nextScale - centeredOrigin.x,
        y: position.y - imagePoint.y * nextScale - centeredOrigin.y,
      },
    }
    if (wheelFrame.current === null) {
      wheelFrameTime.current = null
      wheelFrame.current = requestAnimationFrame(animateWheelZoom)
    }
  }

  const finishPolygon = async (points = draft) => {
    if (view === 'comparison' || tool !== 'polygon' || points.length < 3 || polygonSubmitting.current) return
    // Keep the draft visible until the server has durably created the region.
    // Clearing it first made a rejected or skipped request look as though the
    // first polygon had randomly disappeared, with no way to retry it.
    const submittedPoints = points.map(point => [...point])
    polygonSubmitting.current = true
    try {
      if (await onCreate(submittedPoints, polygonBbox(submittedPoints))) {
        setDraft([])
        setPolygonPreview(null)
      }
    } finally {
      polygonSubmitting.current = false
    }
  }

  const polygonClick = (event: Konva.KonvaEventObject<Event>) => {
    if (runningAction || view === 'comparison' || tool !== 'polygon' || polygonSubmitting.current) return
    const point = pointer(event)
    if (!point || point[0] < 0 || point[0] > page.width || point[1] < 0 || point[1] > page.height) return
    if (draft.length >= 3 && Math.hypot(point[0] - draft[0][0], point[1] - draft[0][1]) * scale <= POLYGON_CLOSE_DISTANCE) {
      void finishPolygon()
      return
    }
    const previous = draft.at(-1)
    if (previous && Math.hypot(point[0] - previous[0], point[1] - previous[1]) * scale < 2) return
    if (!draft.length) select(null)
    setDraft(value => [...value, point])
    setPolygonPreview(point)
  }

  const draftRect = tool === 'rectangle' && draft.length === 2
    ? [Math.min(draft[0][0], draft[1][0]), Math.min(draft[0][1], draft[1][1]), Math.abs(draft[1][0] - draft[0][0]), Math.abs(draft[1][1] - draft[0][1])]
    : null
  const contextButtonClass = 'flex h-9 min-h-9 w-full cursor-pointer items-center justify-start gap-2 rounded-lg border-0 bg-transparent px-3 text-[11px] text-secondary outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-wait disabled:opacity-50 [&_svg]:text-muted hover:[&_svg]:text-accent'

  const polygonCanClose = tool === 'polygon' && draft.length >= 3 && polygonPreview
    ? Math.hypot(polygonPreview[0] - draft[0][0], polygonPreview[1] - draft[0][1]) * scale <= POLYGON_CLOSE_DISTANCE
    : false

  const renderInteractionRegion = (region: TextRegion) => {
            const selectedNow = selectedIdSet.has(region.id)
            const geometry = regionGeometry(region, editingTranslatedGeometry)
            const editable = region.id === editableRegionId
            const geometryLabel = selectedNow ? `  · ${editingTranslatedGeometry ? '译文框' : '原文框'}` : ''
            const label = `${region.region_key}${geometryLabel}${region.visible ? '' : '  · 已关闭'}${region.locked ? '  🔒' : ''}`
            const labelColor = selectedNow ? canvasPalette.selectedLabel : region.visible ? canvasPalette.detected : canvasPalette.hidden
            const drawingToolActive = tool === 'rectangle' || tool === 'polygon' || tool === 'lasso'
            return <Group key={region.id} name={`region-${region.id}`} onMouseEnter={() => setHoveredRegionId(region.id)} onMouseLeave={() => setHoveredRegionId(null)} onContextMenu={event => openRegionContextMenu(event, region)}>
              {editable ? <RegionPolygonEditor
                key={`${region.id}-${editingTranslatedGeometry ? 'translated' : 'source'}`}
                polygon={geometry.polygon}
                pageWidth={page.width} pageHeight={page.height}
                scale={scale} labelScale={regionLabelScale}
                label={label} labelColor={labelColor} visible={region.visible}
                onSelect={() => select(region.id, true)}
                onContextMenu={event => openRegionContextMenu(event, region)}
                onPreview={editingTranslatedGeometry ? (polygon, rotationDelta = 0) => setTranslatedGeometryPreview({
                  regionId: region.id,
                  polygon,
                  rotation: normalizedRotation(region.rotation + rotationDelta),
                }) : undefined}
                onCommit={async (polygon, rotationDelta = 0) => {
                  const rotation = normalizedRotation(region.rotation + rotationDelta)
                  // Keep the preview until the editor adopts the geometry locally.
                  try {await onUpdate(region.id, editingTranslatedGeometry
                      ? {translated_polygon: polygon, translated_bbox: polygonBbox(polygon), rotation}
                      : {polygon, bbox: polygonBbox(polygon)})}
                  finally {if (editingTranslatedGeometry) setTranslatedGeometryPreview(null)}
                }}
              /> : <>
                <Line
                  points={flat(geometry.polygon)} closed
                  stroke={selectedNow ? canvasPalette.selected : region.visible ? canvasPalette.detected : canvasPalette.hidden}
                  strokeWidth={(selectedNow ? 3 : 1.5) / scale} hitStrokeWidth={12 / scale}
                  dash={region.visible ? undefined : [6 / scale, 4 / scale]}
                  fill={selectedNow ? canvasPalette.selectionFill : 'rgba(0,0,0,.001)'}
                  opacity={region.visible ? 1 : .7}
                  listening={!runningAction && !drawingToolActive && tool !== 'pan'}
                  onClick={event => {
                    event.cancelBubble = true
                    if (event.evt.button !== 0) return
                    select(region.id, tool === 'select')
                  }}
                  onTap={event => { event.cancelBubble = true; select(region.id, tool === 'select') }}
                  onContextMenu={event => openRegionContextMenu(event, region)}
                />
                {(selectedNow || hoveredRegionId === region.id) && <RegionLabel x={geometry.bbox[0]} y={geometry.bbox[1]} text={label} color={labelColor} scale={regionLabelScale}/>}
              </>}
            </Group>
  }

  return <div className={cn('relative size-full overflow-hidden touch-none', checkerboard && 'workspace-checkerboard', tool === 'pan' && !middlePanningActive && 'cursor-grab [&_canvas]:!cursor-grab', middlePanningActive && 'cursor-grabbing [&_canvas]:!cursor-grabbing', (marquee || tool === 'polygon') && 'cursor-crosshair [&_canvas]:!cursor-crosshair')} ref={containerRef} onAuxClick={event => event.preventDefault()} onContextMenu={event => {
    if (event.defaultPrevented || runningAction || (event.target as Element).closest('button, input, [role="menu"]')) return
    event.preventDefault(); setContextMenu(null); onPageContextMenu?.({x:event.clientX,y:event.clientY})
  }}>
    <Stage
      width={viewport.width} height={viewport.height}
      onMouseDown={handleDown} onTouchStart={handleDown}
      onMouseMove={handleMove} onTouchMove={handleMove}
      onMouseUp={handleUp} onTouchEnd={handleUp}
      onTap={event => {setContextMenu(null); polygonClick(event)}}
      onMouseLeave={() => { if (tool === 'polygon') setPolygonPreview(null) }}
      onClick={event => {
        if (event.evt.button !== 0) return
        setContextMenu(null)
        polygonClick(event)
      }}
      onContextMenu={handleStageContextMenu}
      onWheel={handleWheel}
    >
      <Layer>
        <Group x={origin.x} y={origin.y} scaleX={scale} scaleY={scale}>
          <Rect name="canvas-background" width={page.width} height={page.height} fill="#ffffff" shadowColor="#000000" shadowOpacity={.12} shadowBlur={18 / scale} shadowOffsetY={4 / scale} />
          {view === 'comparison' && <>
            <KonvaImage image={original ?? undefined} width={page.width} height={page.height} listening={false}/>
            <Rect x={page.width + COMPARISON_GAP} width={page.width} height={page.height} fill="#ffffff" shadowColor="#000000" shadowOpacity={.12} shadowBlur={18 / scale} shadowOffsetY={4 / scale} listening={false}/>
            <Group x={page.width + COMPARISON_GAP} clipX={0} clipY={0} clipWidth={page.width} clipHeight={page.height} listening={false}>
              <KonvaImage image={clean ?? undefined} width={page.width} height={page.height}/>
              <TranslatedPageLayer page={page} regions={translatedRenderRegions} displayScale={scale}/>
            </Group>
            <ComparisonLabel x={12 / scale} scale={1 / scale} text="原图"/>
            <ComparisonLabel x={page.width + COMPARISON_GAP + 12 / scale} scale={1 / scale} text="译文"/>
          </>}
          {layers.original && <KonvaImage image={original ?? undefined} width={page.width} height={page.height} listening={false} />}
          {layers.clean && <KonvaImage image={clean ?? undefined} width={page.width} height={page.height} listening={false} />}
          {layers.translated && (editingTranslatedGeometry && groupSelectionIds.size > 1
            ? <TranslatedRegions regions={translatedRenderRegions.filter(region => !groupSelectionIds.has(region.id))} displayScale={scale}/>
            : <TranslatedPageLayer page={page} regions={translatedRenderRegions} displayScale={scale}/>)}
          {layers.detection && interactionRegions.filter(region => !groupSelectionIds.has(region.id)).map(renderInteractionRegion)}
          {groupSelectionIds.size > 1 && <Group name="selected-region-group" draggable onDragStart={beginGroupDrag} onDragMove={moveGroupDrag} onDragEnd={endGroupDrag}>
            {editingTranslatedGeometry && layers.translated && <TranslatedRegions regions={groupSelection} displayScale={scale}/>}
            {layers.detection && groupSelection.map(renderInteractionRegion)}
          </Group>}
          {view !== 'comparison' && regions.filter(region => processingIdSet.has(region.id)).map(region => {
            const geometry = regionGeometry(region, view === 'translated')
            return <Group key={`processing-${region.id}`} listening={false}>
              <Line name={`processing-${region.id}`} points={flat(geometry.polygon)} closed stroke={canvasPalette.selected} strokeWidth={3 / scale} dash={[6 / scale,4 / scale]} fill={canvasPalette.marqueeFill}/>
              <RegionLabel x={geometry.bbox[0]} y={geometry.bbox[1]} text={`${region.region_key} · 处理中`} color={canvasPalette.selectedLabel} scale={regionLabelScale}/>
            </Group>
          })}
          {view !== 'comparison' && marquee && <Rect
            x={Math.min(marquee.start[0], marquee.current[0])}
            y={Math.min(marquee.start[1], marquee.current[1])}
            width={Math.abs(marquee.current[0] - marquee.start[0])}
            height={Math.abs(marquee.current[1] - marquee.start[1])}
            fill={canvasPalette.marqueeFill} stroke={canvasPalette.selected} strokeWidth={1.5 / scale}
            dash={[6 / scale, 4 / scale]} listening={false}
          />}
          {view !== 'comparison' && draftRect && <Rect x={draftRect[0]} y={draftRect[1]} width={draftRect[2]} height={draftRect[3]} stroke="#ffcb45" dash={[8 / scale, 5 / scale]} strokeWidth={2 / scale} />}
          {view !== 'comparison' && tool === 'lasso' && draft.length > 0 && <Line points={flat(draft)} closed stroke="#ffcb45" fill="rgba(255,203,69,.08)" strokeWidth={2 / scale} />}
          {view !== 'comparison' && tool === 'polygon' && draft.length > 0 && <>
            <Line points={flat(draft)} stroke={polygonDraftStyle.stroke} strokeWidth={polygonDraftStyle.lineWidth / scale} lineCap="round" lineJoin="round" listening={false}/>
            {polygonPreview && <Line
              points={[...draft.at(-1)!, ...(polygonCanClose ? draft[0] : polygonPreview)]}
              stroke={polygonCanClose ? polygonDraftStyle.selected : polygonDraftStyle.stroke} strokeWidth={polygonDraftStyle.previewWidth / scale}
              dash={polygonDraftStyle.dash.map(value => value / scale)} lineCap="round" listening={false}
            />}
            {draft.map((point, index) => <Circle
              key={`polygon-point-${index}`} x={point[0]} y={point[1]}
              radius={(index === 0 ? polygonDraftStyle.firstPointRadius : polygonDraftStyle.pointRadius) / scale}
              fill={index === 0 && polygonCanClose ? polygonDraftStyle.selected : polygonDraftStyle.pointFill}
              stroke={index === 0 ? polygonDraftStyle.firstPointBorder : polygonDraftStyle.pointBorder} strokeWidth={polygonDraftStyle.pointBorderWidth / scale}
              hitStrokeWidth={Math.max(POLYGON_CLOSE_DISTANCE * 2 / scale, 12 / scale)}
              onClick={event => {
                event.cancelBubble = true
                if (index === 0 && draft.length >= 3) void finishPolygon()
              }}
              onTap={event => {
                event.cancelBubble = true
                if (index === 0 && draft.length >= 3) void finishPolygon()
              }}
            />)}
          </>}
        </Group>
      </Layer>
    </Stage>
    {contextMenu && contextRegionIds.length > 0 && <div ref={contextMenuRef} className="absolute z-[80] w-[178px] rounded-xl border border-line-strong bg-popover p-1 text-secondary shadow-panel backdrop-blur-xl" style={{left: contextMenu.x, top: contextMenu.y}} role="menu" aria-label={contextRegionIds.length > 1 ? `所选 ${contextRegionIds.length} 个区域` : `${contextRegion?.region_key} 区域操作`}>
      <div className="mb-1 flex h-[38px] items-center gap-2 border-b border-line-subtle px-2"><span className="font-mono text-[11px] font-semibold text-accent">{contextRegionIds.length > 1 ? `所选 ${contextRegionIds.length} 个区域` : contextRegion?.region_key}</span><small className="text-[9px] text-muted">{contextRegionIds.length > 1 ? '多选操作' : '区域操作'}</small></div>
      <button className={cn(contextButtonClass, runningAction === 'ocr' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} title={`重新 OCR (${formatShortcut(shortcuts['region.ocr'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.ocr'])} onClick={() => runRegionContextAction('ocr')}>{runningAction === 'ocr' ? <ButtonLoading label="OCR 处理中…"/> : <><ScanText size={15}/>重新 OCR</>}</button>
      <button className={cn(contextButtonClass, runningAction === 'translate' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} title={`重新翻译 (${formatShortcut(shortcuts['region.translate'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.translate'])} onClick={() => runRegionContextAction('translate')}>{runningAction === 'translate' ? <ButtonLoading label="翻译处理中…"/> : <><Languages size={15}/>重新翻译</>}</button>
      <button className={cn(contextButtonClass, runningAction === 'inpaint' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} title={`重新修复 (${formatShortcut(shortcuts['region.inpaint'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.inpaint'])} onClick={() => runRegionContextAction('inpaint')}>{runningAction === 'inpaint' ? <ButtonLoading label="背景修复中…"/> : <><Eraser size={15}/>重新修复</>}</button>
      <button className={cn(contextButtonClass, runningAction === 'render' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} title={`重新排版 (${formatShortcut(shortcuts['region.render'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.render'])} onClick={() => runRegionContextAction('render')}>{runningAction === 'render' ? <ButtonLoading label="排版处理中…"/> : <><TextCursorInput size={15}/>重新排版</>}</button>
    </div>}
    {view !== 'comparison' && <div
      className={cn('absolute right-3.5 top-3.5 z-10 select-none text-secondary', layersCollapsed ? 'w-24' : 'w-40')}
      style={{filter: layersCollapsed ? undefined : 'drop-shadow(var(--shadow-soft))'}}
      aria-label="画布图层"
    >
      <button
        className={cn('relative z-10 flex h-[36px] min-h-[36px] w-full cursor-pointer items-center justify-start gap-2 border border-line-strong bg-popover px-3 text-xs font-medium leading-none text-secondary outline-none transition-colors hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-accent/30 [&>svg:first-child]:text-accent', layersCollapsed ? 'rounded-xl shadow-soft' : 'rounded-t-xl rounded-b-none')}
        style={{borderBottomColor: layersCollapsed ? 'var(--color-line-strong)' : 'var(--color-line)'}}
        type="button" aria-expanded={!layersCollapsed} onClick={() => setLayersCollapsed(value => !value)}
      >
        <Layers3 className="shrink-0" size={15}/><span className="flex h-full items-center leading-none">图层</span><ChevronDown className={cn('ml-auto text-muted transition-transform duration-200 motion-reduce:transition-none', layersCollapsed && '-rotate-90')} size={15}/>
      </button>
      <div
        className={cn('absolute right-0 top-full w-full overflow-hidden rounded-b-xl border-x border-b border-line-strong bg-popover transition-[clip-path] duration-200 ease-out motion-reduce:transition-none', layersCollapsed && 'pointer-events-none')}
        style={{clipPath: layersCollapsed ? 'inset(0 0 100% 0)' : 'inset(0 0 0 0)'}}
        aria-hidden={layersCollapsed}
      ><div className="flex flex-col gap-0.5 p-1">{Object.entries({...layers, checkerboard}).map(([name, visible]) => <label className="relative flex h-[30px] shrink-0 cursor-pointer items-center gap-2 rounded-lg px-2 text-[11px] leading-none text-secondary transition-colors hover:bg-hover hover:text-ink" key={name}>
          <input className="peer sr-only" type="checkbox" checked={visible} tabIndex={layersCollapsed ? -1 : 0} onChange={() => name === 'checkerboard' ? setCheckerboard(value => !value) : toggleLayer(name as keyof typeof layers)} />
          <span aria-hidden="true" className={cn('size-3 shrink-0 rounded-full border border-line-strong transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-accent/50 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-panel', visible && 'border-accent bg-accent')} />
          <span className="flex h-full items-center leading-none">{({original:'原始图像',detection:'检测区域',clean:'修复背景',translated:'翻译文字',checkerboard:'透明棋盘格'} as Record<string,string>)[name]}</span>
        </label>)}</div></div>
    </div>}
    <span className="canvas-touch-hint pointer-events-none absolute bottom-3 left-3 rounded-md bg-panel/90 px-2 py-1 text-[11px] text-muted">双指缩放 / 移动</span>
    <div className="pointer-events-none absolute bottom-3 right-3 min-w-12 rounded-lg border border-line-strong bg-canvas/90 px-2.5 py-1.5 text-center font-mono text-[11px] font-semibold text-ink shadow-soft backdrop-blur-md">{Math.round(zoom * 100)}%</div>
  </div>
}

export const MangaCanvas = memo(MangaCanvasComponent, (previous, next) =>
  previous.page === next.page &&
  previous.regions === next.regions &&
  previous.runningAction === next.runningAction &&
  previous.onPageContextMenu === next.onPageContextMenu &&
  previous.processingRegionIds === next.processingRegionIds &&
  previous.interactionKey === next.interactionKey
)

function TranslatedPageLayer({page, regions, displayScale}: {page: ImagePage, regions: TextRegion[], displayScale: number}) {
  // Reuse the existing bounded/deduplicated image cache. Editing any visual
  // input invalidates this artifact immediately, before the autosave response.
  const source = useMemo(() => currentTextLayer(page, regions), [page, regions])
  const {image} = useImage(source)
  return image && source
    ? <KonvaImage image={image} width={page.width} height={page.height} listening={false}/>
    : <TranslatedRegions regions={regions} displayScale={displayScale}/>
}

function TranslatedRegions({regions, displayScale}: {regions: TextRegion[], displayScale: number}) {
  const rasterScale = Math.max(1, Math.ceil(displayScale * 2) / 2)
  return <>{regions.map(region => <TranslatedRegion key={region.id} original={region}
    rasterScale={region.perspective_warp ? rasterScale : 1}/>)}</>
}

// Pointer/zoom/selection renders must not lay out every unchanged text region.
// Keep original references at this boundary, before shapePreviewRegion can
// create derived geometry. Perspective tiles update only at raster thresholds.
const TranslatedRegion = memo(function TranslatedRegion({original, rasterScale}: {original: TextRegion, rasterScale: number}) {
    if (!original.visible) return null
    const region = shapePreviewRegion(original)
    if (!region) return null
    const renderStyle = resolvedRenderStyle(region)
    const geometry = regionGeometry(region, true)
    if (region.perspective_warp && isPerspectiveQuad(region.translated_polygon)) return <PerspectiveTranslatedText key={`text-${region.id}`} region={region} style={renderStyle} displayScale={rasterScale}/>
    const text = region.orientation === 'vertical'
      ? <VerticalTranslatedText key={`text-${region.id}`} region={region} style={renderStyle}/>
      : <HorizontalTranslatedText key={`text-${region.id}`} region={region} style={renderStyle}/>
    return region === original ? text : <Group key={`text-${region.id}`} listening={false}
      clipX={geometry.bbox[0]} clipY={geometry.bbox[1]} clipWidth={geometry.bbox[2]} clipHeight={geometry.bbox[3]}>{text}</Group>
})

function PerspectiveTranslatedText({region, style, displayScale}: {region: TextRegion, style: RenderStyle, displayScale: number}) {
  const [fontRevision, setFontRevision] = useState(0)
  useEffect(() => {
    if (!document.fonts) return
    let active = true
    void document.fonts.load(`${region.font_weight} ${Math.max(1, region.font_size)}px "${region.font_family}"`, region.translated_text).then(() => {
      if (active) setFontRevision(value => value + 1)
    })
    return () => {active = false}
  }, [region.font_family, region.font_size, region.font_weight, region.translated_text])
  // Quantizing the screen scale avoids rebuilding every raster tile on each
  // wheel-animation frame while still refreshing before an enlarged preview
  // can outgrow its backing canvas resolution.
  const rasterScale = Math.max(1, Math.ceil(displayScale * 2) / 2)
  const preview = useMemo(() => {
    // A newly loaded font invalidates the raster even with unchanged text/style.
    void fontRevision
    return createPerspectivePreview(region, {strokeColor:style.strokeColor, strokeWidth:style.strokeWidth, textColor:style.textColor}, rasterScale)
  }, [fontRevision, rasterScale, region, style.strokeColor, style.strokeWidth, style.textColor])
  if (!preview) return null
  return <KonvaImage
    image={preview.image} x={preview.x} y={preview.y} width={preview.width} height={preview.height}
    opacity={region.opacity} listening={false} perfectDrawEnabled={false}
  />
}

function createPerspectivePreview(region: TextRegion, style: RenderStyle, displayScale: number): WarpedCanvas | null {
  if (!region.translated_text || !isPerspectiveQuad(region.translated_polygon)) return null
  const tileSize = perspectiveQuadSize(region.translated_polygon)
  if (!tileSize) return null
  const xs = region.translated_polygon.map(point => point[0])
  const ys = region.translated_polygon.map(point => point[1])
  const outputArea = Math.max(1, (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys)))
  const largestArea = Math.max(outputArea, tileSize.width * tileSize.height)
  const deviceRatio = Math.min(3, Math.max(1, window.devicePixelRatio || 1))
  // Perspective mode has to rasterize glyphs before warping them. Keep at
  // least a 2x source for the transform itself and increase the backing
  // resolution when the user zooms in. The area cap prevents pathological
  // polygons from allocating an unbounded canvas.
  const requestedRatio = Math.max(2, deviceRatio * displayScale)
  const budgetRatio = Math.sqrt(PERSPECTIVE_PREVIEW_PIXEL_BUDGET / largestArea)
  const pixelRatio = Math.max(1, Math.min(PERSPECTIVE_PREVIEW_MAX_RATIO, requestedRatio, budgetRatio))
  const tile = createTextTile(region, style, tileSize.width, tileSize.height, pixelRatio)
  if (!tile) return null
  const quad = usesAutomaticFontSize(region) ? region.translated_polygon : extendedPerspectiveQuad(region.translated_polygon, tileSize, tile)
  return quad ? warpCanvasToQuad(tile.canvas, quad, pixelRatio) : null
}

function createTextTile(region: TextRegion, style: RenderStyle, width: number, height: number, pixelRatio: number) {
  const auto = usesAutomaticFontSize(region)
  const layout = sizeText(region, {width, height})
  if (auto && layout.overflow) return null
  const root = new Konva.Group(auto ? {clipX: 0, clipY: 0, clipWidth: width, clipHeight: height} : {})
  const content = new Konva.Group({
    x: width / 2,
    y: height / 2,
    offsetX: width / 2,
    offsetY: height / 2,
    rotation: region.rotation,
  })
  root.add(content)
  if (region.orientation === 'vertical') {
    const offsetX = (width - layout.width) / 2
    const offsetY = (height - layout.height) / 2
    layout.lines.forEach((column, index) => {
      const x = offsetX + layout.width - layout.fontSize - index * layout.columnWidth
      content.add(new Konva.Text({
        x,
        y: offsetY,
        width: layout.fontSize,
        text: [...column].map(character => VERTICAL_FORMS[character] || character).join('\n'),
        fontSize: layout.fontSize,
        fontFamily: region.font_family,
        fontStyle: region.font_weight >= 600 ? 'bold' : 'normal',
        fill: style.textColor,
        stroke: style.strokeColor,
        strokeWidth: style.strokeWidth,
        align: 'center',
        lineHeight: layout.cellHeight / layout.fontSize,
        listening: false,
      }))
    })
  } else {
    layout.lines.forEach((line,index) => content.add(new Konva.Text({
      x: horizontalLineX(region, layout.lineWidths[index], width, layout.coordinateWidth),
      y: (height-layout.height)/2 + index*layout.columnWidth,
      text: line,
      wrap: 'none',
      fontSize: layout.fontSize,
      fontFamily: region.font_family,
      fontStyle: region.font_weight >= 600 ? 'bold' : 'normal',
      fill: style.textColor,
      stroke: style.strokeColor,
      strokeWidth: style.strokeWidth,
      letterSpacing: region.character_spacing,
      listening: false,
    })))
  }
  const bounds = auto ? {x:0,y:0,width,height} : root.getClientRect()
  // Raster allocation stays bounded; lowering raster density does not change
  // manual font size or map oversized text back into the original frame.
  const ratio = Math.max(.1, Math.min(pixelRatio, Math.sqrt(PERSPECTIVE_PREVIEW_PIXEL_BUDGET / Math.max(1,bounds.width*bounds.height))))
  const canvas = root.toCanvas({...bounds, pixelRatio:ratio, imageSmoothingEnabled: true})
  root.destroy()
  return {canvas,...bounds}
}

function VerticalTranslatedText({region, style}: {region: TextRegion, style: RenderStyle}) {
  const layout = sizeText(region)
  if (!region.translated_text.trim() || (usesAutomaticFontSize(region) && layout.overflow)) return null
  const geometry = regionGeometry(region, true)
  const offsetX = (geometry.bbox[2] - layout.width) / 2
  const offsetY = (geometry.bbox[3] - layout.height) / 2
  return <Group
    x={geometry.bbox[0]+geometry.bbox[2]/2} y={geometry.bbox[1]+geometry.bbox[3]/2}
    offsetX={geometry.bbox[2]/2} offsetY={geometry.bbox[3]/2} rotation={region.rotation}
    opacity={region.opacity} listening={false}
  >
    {layout.lines.map((column, index) => {
      // Japanese/Chinese vertical text reads top-to-bottom, with subsequent
      // columns placed to the left of the first column.
      const x = offsetX + layout.width - layout.fontSize - index * layout.columnWidth
      return <Text
        key={`${index}-${column}`}
        x={x} y={offsetY} width={layout.fontSize}
        text={[...column].map(character => VERTICAL_FORMS[character] || character).join('\n')}
        fontSize={layout.fontSize} fontFamily={region.font_family}
        fontStyle={region.font_weight >= 600 ? 'bold' : 'normal'}
        fill={style.textColor} stroke={style.strokeColor} strokeWidth={style.strokeWidth}
        align="center" lineHeight={layout.cellHeight / layout.fontSize}
        listening={false}
      />
    })}
  </Group>
}

function horizontalLineX(region: TextRegion, lineWidth: number, width: number, availableWidth: number) {
  const offset = (width-availableWidth)/2
  return offset + (region.alignment === 'left' ? 0 : region.alignment === 'right' ? availableWidth-lineWidth : (availableWidth-lineWidth)/2)
}

function HorizontalTranslatedText({region, style}: {region: TextRegion, style: RenderStyle}) {
  const layout = sizeText(region)
  if (!region.translated_text.trim() || (usesAutomaticFontSize(region) && layout.overflow)) return null
  const {bbox} = regionGeometry(region,true)
  return <Group x={bbox[0]+bbox[2]/2} y={bbox[1]+bbox[3]/2} offsetX={bbox[2]/2} offsetY={bbox[3]/2}
    rotation={region.rotation} opacity={region.opacity} listening={false}>
    {layout.lines.map((line,index) => <Text key={index}
      x={horizontalLineX(region,layout.lineWidths[index],bbox[2],layout.coordinateWidth)} y={(bbox[3]-layout.height)/2+index*layout.columnWidth}
      text={line} wrap="none" fontSize={layout.fontSize} fontFamily={region.font_family} fontStyle={region.font_weight >= 600 ? 'bold' : 'normal'}
      fill={style.textColor} stroke={style.strokeColor} strokeWidth={style.strokeWidth} letterSpacing={region.character_spacing} listening={false}/>) }
  </Group>
}

function resolvedRenderStyle(region: TextRegion): RenderStyle {
  return {textColor: region.text_color, strokeColor: region.stroke_color, strokeWidth: region.stroke_width}
}
