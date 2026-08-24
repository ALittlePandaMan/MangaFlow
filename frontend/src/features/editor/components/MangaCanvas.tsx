import Konva from 'konva'
import { ChevronDown, Eraser, Languages, Layers3, ScanText, TextCursorInput } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text } from 'react-konva'
import type { ImagePage, TextRegion } from '../../../types'
import {cn} from '../../../ui'
import {ButtonLoading} from '../../../components/LoadingUI'
import {formatShortcut, shortcutToAria, useShortcutStore} from '../../shortcuts/store'
import { useEditorStore } from '../store'
import { RegionPolygonEditor } from './RegionPolygonEditor'
import { useImage, versionedImageSource } from '../hooks/useImage'
import {isPerspectiveQuad, perspectiveQuadSize, warpCanvasToQuad, type WarpedCanvas} from '../lib/perspectiveText'

interface Props {
  page: ImagePage
  regions: TextRegion[]
  onCreate: (polygon: number[][], bbox: number[]) => Promise<boolean>
  onUpdate: (id: string, patch: Partial<TextRegion>) => Promise<void>
  onRegionAction: (id: string, action: string, options?: Record<string, unknown>) => void
  runningAction: string | null
}

type RenderStyle = {textColor: string, strokeColor: string, strokeWidth: number}
type VerticalPreviewLayout = {
  fontSize: number
  cellHeight: number
  columnWidth: number
  columns: string[]
  width: number
  height: number
}

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
const POLYGON_CLOSE_DISTANCE = 12
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
    <Rect width={48} height={24} cornerRadius={6} fill="rgba(8,11,9,.82)" stroke="rgba(255,255,255,.18)" strokeWidth={1}/>
    <Text width={48} height={24} text={text} align="center" verticalAlign="middle" fontFamily="Noto Sans CJK SC, sans-serif" fontSize={10} fontStyle="bold" fill="#dffbf3"/>
  </Group>
}

export function MangaCanvas({ page, regions, onCreate, onUpdate, onRegionAction, runningAction }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState({width: 800, height: 700})
  const [pan, setPan] = useState({x: 0, y: 0})
  const [draft, setDraft] = useState<number[][]>([])
  const [polygonPreview, setPolygonPreview] = useState<MarqueePoint | null>(null)
  const [drawing, setDrawing] = useState(false)
  const [middlePanningActive, setMiddlePanningActive] = useState(false)
  const [layersCollapsed, setLayersCollapsed] = useState(false)
  const [contextMenu, setContextMenu] = useState<{x: number, y: number, regionId: string, regionKey: string} | null>(null)
  const [marquee, setMarquee] = useState<MarqueeSelection | null>(null)
  const [translatedGeometryPreview, setTranslatedGeometryPreview] = useState<TranslatedGeometryPreview | null>(null)
  const middlePanning = useRef(false)
  const middlePointer = useRef({x: 0, y: 0})
  const marqueeCandidate = useRef<MarqueeSelection | null>(null)
  const marqueeActive = useRef(false)
  const polygonSubmitting = useRef(false)
  const originalState = useImage(page.original_url)
  const cleanSource = page.clean_url ? versionedImageSource(page.clean_url, page.updated_at) : page.original_url
  const cleanState = useImage(cleanSource)
  const original = originalState.image
  const clean = cleanState.image
  const { view, tool, zoom, setZoom, selectedIds, select, selectMany, layers, toggleLayer } = useEditorStore()
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const currentView = useRef<ViewTransform>({zoom, pan})
  const targetView = useRef<ViewTransform>({zoom, pan})
  const wheelFrame = useRef<number | null>(null)
  const wheelFrameTime = useRef<number | null>(null)
  const selected = regions.find(region => region.id === selectedIds[0])
  const singleSelection = selectedIds.length === 1
  const editingTranslatedGeometry = view === 'translated'
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
  const fitScale = Math.min((viewport.width - 72) / contentWidth, (viewport.height - 72) / page.height)
  const scale = Math.max(0.01, fitScale * zoom)
  const regionLabelScale = 1 / Math.max(.01, fitScale)
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
  }, [page.id])

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
      if (event.button !== 1) return
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
      regionId: region.id,
      regionKey: region.region_key,
    })
  }

  const runRegionContextAction = (action: string) => {
    if (!contextMenu || runningAction) return
    const {regionId} = contextMenu
    const region = regions.find(item => item.id === regionId)
    const options = action === 'ocr' ? {crop_padding: 4, orientation: region?.orientation} : {}
    // The task state is presented by the centered loading overlay. Close the
    // transient context menu as soon as its command has been accepted.
    setContextMenu(null)
    onRegionAction(regionId, action, options)
  }

  const pointer = useCallback((event: Konva.KonvaEventObject<Event>) => {
    const stage = event.target.getStage()
    const position = stage?.getPointerPosition()
    if (!position) return null
    return [(position.x - origin.x) / scale, (position.y - origin.y) / scale] as [number, number]
  }, [origin.x, origin.y, scale])

  const handleStageContextMenu = (event: Konva.KonvaEventObject<MouseEvent>) => {
    event.evt.preventDefault()
    if (view === 'comparison' || runningAction) return
    const point = pointer(event)
    if (!point) return
    // Translated text deliberately does not listen for Konva events. When the
    // detection overlay is hidden (or a child handle misses the event), resolve
    // the visually topmost region from its geometry instead.
    const candidates = layers.detection ? regions : layers.translated ? regions.filter(region => region.visible) : []
    const region = [...candidates].reverse().find(candidate =>
      polygonContainsPoint(regionGeometry(candidate, view === 'translated').polygon, point, 6 / scale))
    if (region) openRegionContextMenu(event, region)
  }

  const handleDown = (event: Konva.KonvaEventObject<MouseEvent | TouchEvent>) => {
    stopWheelAnimation()
    cancelMarquee()
    if (event.evt instanceof MouseEvent) {
      if (event.evt.button === 1) {
        event.evt.preventDefault()
        event.cancelBubble = true
        middlePanning.current = true
        middlePointer.current = {x: event.evt.clientX, y: event.evt.clientY}
        setMiddlePanningActive(true)
        return
      }
      if (event.evt.button !== 0) return
    }
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

  const polygonClick = (event: Konva.KonvaEventObject<MouseEvent>) => {
    if (view === 'comparison' || tool !== 'polygon' || polygonSubmitting.current) return
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

  return <div className={cn('relative size-full overflow-hidden', middlePanningActive && 'cursor-grabbing [&_canvas]:!cursor-grabbing', (marquee || tool === 'polygon') && 'cursor-crosshair [&_canvas]:!cursor-crosshair')} ref={containerRef} onAuxClick={event => event.preventDefault()}>
    <Stage
      width={viewport.width} height={viewport.height}
      onMouseDown={handleDown} onTouchStart={handleDown}
      onMouseMove={handleMove} onTouchMove={handleMove}
      onMouseUp={handleUp} onTouchEnd={handleUp}
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
          <Rect name="canvas-background" width={page.width} height={page.height} fill="#f4f0e8" shadowColor="#172019" shadowOpacity={.16} shadowBlur={18 / scale} shadowOffsetY={4 / scale} />
          {view === 'comparison' && <>
            <KonvaImage image={original ?? undefined} width={page.width} height={page.height} listening={false}/>
            <Rect x={page.width + COMPARISON_GAP} width={page.width} height={page.height} fill="#f4f0e8" shadowColor="#172019" shadowOpacity={.16} shadowBlur={18 / scale} shadowOffsetY={4 / scale} listening={false}/>
            <Group x={page.width + COMPARISON_GAP} clipX={0} clipY={0} clipWidth={page.width} clipHeight={page.height} listening={false}>
              <KonvaImage image={clean ?? undefined} width={page.width} height={page.height}/>
              <TranslatedRegions regions={translatedRenderRegions} displayScale={scale}/>
            </Group>
            <ComparisonLabel x={12 / scale} scale={1 / scale} text="原图"/>
            <ComparisonLabel x={page.width + COMPARISON_GAP + 12 / scale} scale={1 / scale} text="译文"/>
          </>}
          {layers.original && <KonvaImage image={original ?? undefined} width={page.width} height={page.height} listening={false} />}
          {layers.clean && <KonvaImage image={clean ?? undefined} width={page.width} height={page.height} listening={false} />}
          {layers.translated && <TranslatedRegions regions={translatedRenderRegions} displayScale={scale}/>}
          {layers.detection && regions.map(region => {
            const selectedNow = selectedIds.includes(region.id)
            const geometry = regionGeometry(region, editingTranslatedGeometry)
            const editable = view !== 'comparison' && singleSelection && selectedNow && tool === 'select' && !region.locked
            const geometryLabel = selectedNow ? `  · ${editingTranslatedGeometry ? '译文框' : '原文框'}` : ''
            const label = `${region.region_key}${geometryLabel}${region.visible ? '' : '  · 已关闭'}${region.locked ? '  🔒' : ''}`
            const labelColor = selectedNow ? '#65ddc5' : region.visible ? '#4aa8ff' : '#888880'
            const drawingToolActive = tool === 'rectangle' || tool === 'polygon' || tool === 'lasso'
            return <Group key={region.id} onContextMenu={event => openRegionContextMenu(event, region)}>
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
                  if (editingTranslatedGeometry) setTranslatedGeometryPreview({regionId: region.id, polygon, rotation})
                  try {
                    await onUpdate(region.id, editingTranslatedGeometry
                      ? {translated_polygon: polygon, translated_bbox: polygonBbox(polygon), rotation}
                      : {polygon, bbox: polygonBbox(polygon)})
                  } finally {
                    if (editingTranslatedGeometry) setTranslatedGeometryPreview(current => current?.regionId === region.id ? null : current)
                  }
                }}
              /> : <>
                <Line
                  points={flat(geometry.polygon)} closed
                  stroke={selectedNow ? '#00d7aa' : region.visible ? '#4aa8ff' : '#77776f'}
                  strokeWidth={(selectedNow ? 3 : 1.5) / scale} hitStrokeWidth={12 / scale}
                  dash={region.visible ? undefined : [6 / scale, 4 / scale]}
                  fill={selectedNow ? 'rgba(0,215,170,.08)' : 'rgba(0,0,0,.001)'}
                  opacity={region.visible ? 1 : .7}
                  listening={!drawingToolActive}
                  onClick={event => {
                    event.cancelBubble = true
                    if (event.evt.button !== 0) return
                    select(region.id, tool === 'select')
                  }}
                  onTap={event => { event.cancelBubble = true; select(region.id, tool === 'select') }}
                  onContextMenu={event => openRegionContextMenu(event, region)}
                />
                <Text x={geometry.bbox[0]} y={geometry.bbox[1] - 17 * regionLabelScale} text={label} fill={labelColor} fontSize={12 * regionLabelScale} listening={false}/>
              </>}
            </Group>
          })}
          {view !== 'comparison' && marquee && <Rect
            x={Math.min(marquee.start[0], marquee.current[0])}
            y={Math.min(marquee.start[1], marquee.current[1])}
            width={Math.abs(marquee.current[0] - marquee.start[0])}
            height={Math.abs(marquee.current[1] - marquee.start[1])}
            fill="rgba(0,215,170,.1)" stroke="#00d7aa" strokeWidth={1.5 / scale}
            dash={[6 / scale, 4 / scale]} listening={false}
          />}
          {view !== 'comparison' && draftRect && <Rect x={draftRect[0]} y={draftRect[1]} width={draftRect[2]} height={draftRect[3]} stroke="#ffcb45" dash={[8 / scale, 5 / scale]} strokeWidth={2 / scale} />}
          {view !== 'comparison' && tool === 'lasso' && draft.length > 0 && <Line points={flat(draft)} closed stroke="#ffcb45" fill="rgba(255,203,69,.08)" strokeWidth={2 / scale} />}
          {view !== 'comparison' && tool === 'polygon' && draft.length > 0 && <>
            <Line points={flat(draft)} stroke="#ffcb45" strokeWidth={2 / scale} lineCap="round" lineJoin="round" listening={false}/>
            {polygonPreview && <Line
              points={[...draft.at(-1)!, ...(polygonCanClose ? draft[0] : polygonPreview)]}
              stroke={polygonCanClose ? '#00d7aa' : '#ffcb45'} strokeWidth={1.5 / scale}
              dash={[6 / scale, 4 / scale]} lineCap="round" listening={false}
            />}
            {draft.map((point, index) => <Circle
              key={`polygon-point-${index}`} x={point[0]} y={point[1]}
              radius={(index === 0 ? 5 : 3.5) / scale}
              fill={index === 0 && polygonCanClose ? '#00d7aa' : '#fff7d6'}
              stroke={index === 0 ? '#00b991' : '#d8a91d'} strokeWidth={1.5 / scale}
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
    {contextMenu && <div ref={contextMenuRef} className="absolute z-[80] w-[178px] rounded-xl border border-line-strong bg-popover p-1 text-secondary shadow-panel backdrop-blur-xl" style={{left: contextMenu.x, top: contextMenu.y}} role="menu" aria-label={`${contextMenu.regionKey} 区域操作`}>
      <div className="mb-1 flex h-[38px] items-center gap-2 border-b border-line-subtle px-2"><span className="font-mono text-[11px] font-semibold text-accent">{contextMenu.regionKey}</span><small className="text-[9px] text-muted">区域操作</small></div>
      <button className={cn(contextButtonClass, runningAction === 'ocr' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} title={`重新 OCR (${formatShortcut(shortcuts['region.ocr'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.ocr'])} onClick={() => runRegionContextAction('ocr')}>{runningAction === 'ocr' ? <ButtonLoading label="OCR 处理中…"/> : <><ScanText size={15}/>重新 OCR</>}</button>
      <button className={cn(contextButtonClass, runningAction === 'translate' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} title={`重新翻译 (${formatShortcut(shortcuts['region.translate'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.translate'])} onClick={() => runRegionContextAction('translate')}>{runningAction === 'translate' ? <ButtonLoading label="翻译处理中…"/> : <><Languages size={15}/>重新翻译</>}</button>
      <button className={cn(contextButtonClass, runningAction === 'inpaint' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} title={`重新修复 (${formatShortcut(shortcuts['region.inpaint'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.inpaint'])} onClick={() => runRegionContextAction('inpaint')}>{runningAction === 'inpaint' ? <ButtonLoading label="背景修复中…"/> : <><Eraser size={15}/>重新修复</>}</button>
      <button className={cn(contextButtonClass, runningAction === 'render' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} title={`重新排版 (${formatShortcut(shortcuts['region.render'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.render'])} onClick={() => runRegionContextAction('render')}>{runningAction === 'render' ? <ButtonLoading label="排版处理中…"/> : <><TextCursorInput size={15}/>重新排版</>}</button>
    </div>}
    {view !== 'comparison' && <div
      className="absolute right-3.5 top-3.5 z-10 w-44 select-none text-secondary"
      style={{filter: layersCollapsed ? undefined : 'drop-shadow(var(--shadow-soft))'}}
      aria-label="画布图层"
    >
      <button
        className={cn('relative z-10 flex h-[36px] min-h-[36px] w-full cursor-pointer items-center justify-start gap-2 border border-line-strong bg-popover px-3 font-mono text-xs font-medium uppercase leading-none tracking-[1px] text-secondary outline-none transition-colors hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-accent/30 [&>svg:first-child]:text-accent', layersCollapsed ? 'rounded-xl shadow-soft' : 'rounded-t-xl rounded-b-none')}
        style={{borderBottomColor: layersCollapsed ? 'var(--color-line-strong)' : 'var(--color-line)'}}
        type="button" aria-expanded={!layersCollapsed} onClick={() => setLayersCollapsed(value => !value)}
      >
        <Layers3 className="shrink-0" size={15}/><span className="flex h-full items-center leading-none">图层</span><ChevronDown className={cn('ml-auto text-muted transition-transform duration-200 motion-reduce:transition-none', layersCollapsed && '-rotate-90')} size={15}/>
      </button>
      <div
        className={cn('absolute right-0 top-full w-full overflow-hidden rounded-b-xl border-x border-b border-line-strong bg-popover transition-[clip-path] duration-200 ease-out motion-reduce:transition-none', layersCollapsed && 'pointer-events-none')}
        style={{clipPath: layersCollapsed ? 'inset(0 0 100% 0)' : 'inset(0 0 0 0)'}}
        aria-hidden={layersCollapsed}
      ><div className="flex flex-col gap-1 p-1">{Object.entries(layers).map(([name, visible]) => <label className="relative flex h-[34px] shrink-0 cursor-pointer items-center gap-2 rounded-lg px-2 text-[11px] leading-none text-secondary transition-colors hover:bg-hover hover:text-ink" key={name}>
          <input className="sr-only" type="checkbox" checked={visible} tabIndex={layersCollapsed ? -1 : 0} onChange={() => toggleLayer(name as keyof typeof layers)} />
          <span className={cn('size-3 shrink-0 rounded-full border border-accent/50 transition', visible && 'border-accent bg-accent shadow-[0_0_8px_rgb(16_211_163/.25)]')} />
          <span className="flex h-full items-center leading-none">{({original:'原始图像',detection:'检测区域',clean:'修复背景',translated:'翻译文字'} as Record<string,string>)[name]}</span>
        </label>)}</div></div>
    </div>}
    <div className="pointer-events-none absolute bottom-3 right-3 min-w-12 rounded-lg border border-line-strong bg-canvas/90 px-2.5 py-1.5 text-center font-mono text-[11px] font-semibold text-ink shadow-soft backdrop-blur-md">{Math.round(zoom * 100)}%</div>
  </div>
}

function TranslatedRegions({regions, displayScale}: {regions: TextRegion[], displayScale: number}) {
  return <>{regions.filter(region => region.visible).map(region => {
    const renderStyle = resolvedRenderStyle(region)
    const geometry = regionGeometry(region, true)
    if (region.perspective_warp && isPerspectiveQuad(region.translated_polygon)) return <PerspectiveTranslatedText key={`text-${region.id}`} region={region} style={renderStyle} displayScale={displayScale}/>
    if (region.orientation === 'vertical') return <VerticalTranslatedText key={`text-${region.id}`} region={region} style={renderStyle}/>
    return <Text
      key={`text-${region.id}`} x={geometry.bbox[0]} y={geometry.bbox[1]} width={geometry.bbox[2]} height={geometry.bbox[3]}
      text={region.translated_text}
      fontSize={region.font_size} fontFamily={region.font_family} fontStyle={region.font_weight >= 600 ? 'bold' : 'normal'}
      fill={renderStyle.textColor} stroke={renderStyle.strokeColor} strokeWidth={renderStyle.strokeWidth}
      opacity={region.opacity} rotation={region.rotation} align={region.alignment}
      lineHeight={region.line_spacing} letterSpacing={region.character_spacing} verticalAlign="middle" listening={false}
    />
  })}</>
}

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
  const preview = useMemo(() => createPerspectivePreview(region, style, rasterScale), [fontRevision, rasterScale, region, style.strokeColor, style.strokeWidth, style.textColor])
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
  return warpCanvasToQuad(tile, region.translated_polygon, pixelRatio)
}

function createTextTile(region: TextRegion, style: RenderStyle, width: number, height: number, pixelRatio: number): HTMLCanvasElement {
  const root = new Konva.Group({clipX: 0, clipY: 0, clipWidth: width, clipHeight: height})
  const content = new Konva.Group({
    x: width / 2,
    y: height / 2,
    offsetX: width / 2,
    offsetY: height / 2,
    rotation: region.rotation,
  })
  root.add(content)
  if (region.orientation === 'vertical') {
    const layout = verticalPreviewLayout(region, {width, height})
    const offsetX = Math.max(0, (width - layout.width) / 2)
    const offsetY = Math.max(0, (height - layout.height) / 2)
    layout.columns.forEach((column, index) => {
      const x = offsetX + layout.width - (index + 1) * layout.columnWidth + Math.max(0, (layout.columnWidth - layout.fontSize) / 2)
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
    content.add(new Konva.Text({
      x: 0,
      y: 0,
      width,
      height,
      text: region.translated_text,
      fontSize: region.font_size,
      fontFamily: region.font_family,
      fontStyle: region.font_weight >= 600 ? 'bold' : 'normal',
      fill: style.textColor,
      stroke: style.strokeColor,
      strokeWidth: style.strokeWidth,
      align: region.alignment,
      lineHeight: region.line_spacing,
      letterSpacing: region.character_spacing,
      verticalAlign: 'middle',
      listening: false,
    }))
  }
  const canvas = root.toCanvas({x: 0, y: 0, width, height, pixelRatio, imageSmoothingEnabled: true})
  root.destroy()
  return canvas
}

function VerticalTranslatedText({region, style}: {region: TextRegion, style: RenderStyle}) {
  const layout = verticalPreviewLayout(region)
  if (!layout.columns.length) return null
  const geometry = regionGeometry(region, true)
  const offsetX = Math.max(0, (geometry.bbox[2] - layout.width) / 2)
  const offsetY = Math.max(0, (geometry.bbox[3] - layout.height) / 2)
  return <Group
    x={geometry.bbox[0]} y={geometry.bbox[1]} rotation={region.rotation}
    opacity={region.opacity} listening={false}
  >
    {layout.columns.map((column, index) => {
      // Japanese/Chinese vertical text reads top-to-bottom, with subsequent
      // columns placed to the left of the first column.
      const x = offsetX + layout.width - (index + 1) * layout.columnWidth + Math.max(0, (layout.columnWidth - layout.fontSize) / 2)
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

function verticalPreviewLayout(region: TextRegion, rectifiedSize?: {width: number, height: number}): VerticalPreviewLayout {
  if (!region.translated_text.trim()) {
    return {fontSize: Math.max(1, Math.round(region.font_size)), cellHeight: 0, columnWidth: 0, columns: [], width: 0, height: 0}
  }
  const geometry = regionGeometry(region, true)
  const polygonFactor = rectifiedSize ? 1 : previewPolygonFactor(geometry.polygon, geometry.bbox)
  const availableWidth = Math.max(1, (rectifiedSize?.width ?? geometry.bbox[2]) * .9 * polygonFactor)
  const availableHeight = Math.max(1, (rectifiedSize?.height ?? geometry.bbox[3]) * .9 * polygonFactor)
  const preferredSize = Math.max(10, Math.round(region.font_size))
  let fallback: VerticalPreviewLayout | null = null
  for (let fontSize = preferredSize; fontSize >= 10; fontSize -= 1) {
    const cellHeight = Math.max(1, fontSize + region.character_spacing)
    const capacity = Math.max(1, Math.floor(Math.max(1, availableHeight + region.character_spacing) / cellHeight))
    const columns: string[] = []
    for (const paragraph of region.translated_text.replace(/\r/g, '').split('\n')) {
      if (!paragraph) {
        columns.push('')
        continue
      }
      const characters = [...paragraph]
      for (let index = 0; index < characters.length; index += capacity) {
        columns.push(characters.slice(index, index + capacity).join(''))
      }
    }
    const columnWidth = fontSize * region.line_spacing
    const width = Math.max(fontSize, columns.length * columnWidth)
    const height = Math.min(capacity, Math.max(0, ...columns.map(column => [...column].length))) * cellHeight
    const candidate = {fontSize, cellHeight, columnWidth, columns, width, height}
    fallback = candidate
    if (width <= availableWidth + .5 && height <= availableHeight + .5) return candidate
  }
  return fallback!
}

function previewPolygonFactor(polygon: number[][], bbox: number[]): number {
  if (polygon.length < 3 || bbox[2] <= 0 || bbox[3] <= 0) return 1
  let area = 0
  for (let index = 0; index < polygon.length; index += 1) {
    const current = polygon[index], next = polygon[(index + 1) % polygon.length]
    area += current[0] * next[1] - next[0] * current[1]
  }
  const coverage = Math.max(.2, Math.min(1, Math.abs(area) / 2 / (bbox[2] * bbox[3])))
  return Math.sqrt(coverage)
}

function resolvedRenderStyle(region: TextRegion): RenderStyle {
  return {textColor: region.text_color, strokeColor: region.stroke_color, strokeWidth: region.stroke_width}
}
