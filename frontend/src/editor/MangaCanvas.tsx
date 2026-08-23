import Konva from 'konva'
import { ChevronDown, Eraser, Languages, Layers3, LoaderCircle, ScanText, TextCursorInput } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text, Transformer } from 'react-konva'
import type { ImagePage, TextRegion } from '../types'
import { useEditorStore } from '../stores/editor'
import {buttonClass, cn, primaryButtonClass} from '../ui'
import { useImage } from './useImage'

export interface MaskStroke { points: number[], size: number, hardness: number, erase: boolean }

interface Props {
  page: ImagePage
  regions: TextRegion[]
  onCreate: (polygon: number[][], bbox: number[]) => Promise<void>
  onUpdate: (id: string, patch: Partial<TextRegion>) => Promise<void>
  onSaveMask: (id: string, blob: Blob) => Promise<void>
  onRegionAction: (id: string, action: string, options?: Record<string, unknown>) => void
  runningAction: string | null
  maskRevision: number
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
  '「': '﹁', '」': '﹂', '『': '﹃', '』': '﹄', '…': '︙', '—': '︱',
}

const bboxPolygon = ([x, y, width, height]: number[]) => [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]
const TRANSFORMER_HANDLE_SIZE = 8
const MIN_ZOOM = .05
const MAX_ZOOM = 6
const WHEEL_ZOOM_SPEED = .001
const MARQUEE_DRAG_THRESHOLD = 3
const COMPARISON_GAP = 32
type ViewTransform = {zoom: number, pan: {x: number, y: number}}
type MarqueePoint = [number, number]
type MarqueeSelection = {start: MarqueePoint, current: MarqueePoint}
type OrientedRegionBox = {x: number, y: number, width: number, height: number, rotation: number}
type RegionGeometry = {polygon: number[][], bbox: number[]}
const polygonBbox = (points: number[][]) => {
  const xs = points.map(point => point[0]), ys = points.map(point => point[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)]
}
const flat = (points: number[][]) => points.flatMap(point => point)
const normalizeRotation = (degrees: number) => {
  const normalized = ((degrees + 180) % 360 + 360) % 360 - 180
  return Math.abs(normalized) < .001 ? 0 : normalized
}
const inferredPolygonRotation = (points: number[][]) => {
  if (points.length !== 4) return 0
  const edges = [[points[0], points[1]], [points[3], points[2]]]
    .map(([start, end]) => {
      const x = end[0] - start[0], y = end[1] - start[1]
      const length = Math.hypot(x, y)
      return length > .001 ? [x / length, y / length] : null
    })
    .filter((edge): edge is number[] => edge !== null)
  if (!edges.length) return 0
  const direction = edges.reduce((result, edge) => [result[0] + edge[0], result[1] + edge[1]], [0, 0])
  let rotation = Math.atan2(direction[1], direction[0]) * 180 / Math.PI
  // A rectangle rotated by 180 degrees has the same geometry. Keeping the
  // angle in this range prevents reversed OCR point order from flipping it.
  if (rotation >= 90) rotation -= 180
  if (rotation < -90) rotation += 180
  return normalizeRotation(rotation)
}
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
const orientedRegionBox = (region: TextRegion, translated = false): OrientedRegionBox => {
  const geometry = regionGeometry(region, translated)
  const points = geometry.polygon
  const inferredRotation = inferredPolygonRotation(points)
  const rotation = translated && Math.abs(region.rotation) > .001 ? normalizeRotation(region.rotation) : inferredRotation
  const radians = rotation * Math.PI / 180
  const cosine = Math.cos(radians), sine = Math.sin(radians)
  const projected = points.map(point => [point[0] * cosine + point[1] * sine, -point[0] * sine + point[1] * cosine])
  const horizontal = projected.map(point => point[0]), vertical = projected.map(point => point[1])
  const left = Math.min(...horizontal), right = Math.max(...horizontal)
  const top = Math.min(...vertical), bottom = Math.max(...vertical)
  return {
    x: left * cosine - top * sine,
    y: left * sine + top * cosine,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
    rotation,
  }
}
const styleTransformerAnchor = (anchor: Konva.Rect) => {
  const name = anchor.name().split(' ')[0]
  const isCorner = ['top-left','top-right','bottom-left','bottom-right'].includes(name)
  anchor.cornerRadius(isCorner ? TRANSFORMER_HANDLE_SIZE / 2 : 1.5)
}

function MaskImage({ source, width, height }: {source: string, width: number, height: number}) {
  const image = useImage(source)
  return <KonvaImage image={image ?? undefined} width={width} height={height} opacity={0.32} globalCompositeOperation="source-over" filters={[Konva.Filters.RGBA]} red={255} green={50} blue={45} alpha={0.9} listening={false} />
}

function ComparisonLabel({x, scale, text}: {x: number, scale: number, text: string}) {
  return <Group x={x} y={12 * scale} scaleX={scale} scaleY={scale} listening={false}>
    <Rect width={48} height={24} cornerRadius={6} fill="rgba(8,11,9,.82)" stroke="rgba(255,255,255,.18)" strokeWidth={1}/>
    <Text width={48} height={24} text={text} align="center" verticalAlign="middle" fontFamily="Noto Sans CJK SC, sans-serif" fontSize={10} fontStyle="bold" fill="#dffbf3"/>
  </Group>
}

export function MangaCanvas({ page, regions, onCreate, onUpdate, onSaveMask, onRegionAction, runningAction, maskRevision }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)
  const transformerRef = useRef<Konva.Transformer>(null)
  const shapeRefs = useRef<Record<string, Konva.Rect | null>>({})
  const [viewport, setViewport] = useState({width: 800, height: 700})
  const [pan, setPan] = useState({x: 0, y: 0})
  const [draft, setDraft] = useState<number[][]>([])
  const [drawing, setDrawing] = useState(false)
  const [middlePanningActive, setMiddlePanningActive] = useState(false)
  const [layersCollapsed, setLayersCollapsed] = useState(false)
  const [contextMenu, setContextMenu] = useState<{x: number, y: number, regionId: string, regionKey: string} | null>(null)
  const [marquee, setMarquee] = useState<MarqueeSelection | null>(null)
  const [strokes, setStrokes] = useState<MaskStroke[]>([])
  const [strokeRedo, setStrokeRedo] = useState<MaskStroke[]>([])
  const middlePanning = useRef(false)
  const middlePointer = useRef({x: 0, y: 0})
  const marqueeCandidate = useRef<MarqueeSelection | null>(null)
  const marqueeActive = useRef(false)
  const original = useImage(page.original_url)
  const clean = useImage(page.clean_url || page.original_url)
  const { view, tool, zoom, setZoom, selectedIds, select, selectMany, layers, toggleLayer, brushSize, brushHardness } = useEditorStore()
  const currentView = useRef<ViewTransform>({zoom, pan})
  const targetView = useRef<ViewTransform>({zoom, pan})
  const wheelFrame = useRef<number | null>(null)
  const wheelFrameTime = useRef<number | null>(null)
  const selected = regions.find(region => region.id === selectedIds[0])
  const singleSelection = selectedIds.length === 1
  const editingTranslatedGeometry = view === 'translated'
  const comparisonWidth = page.width * 2 + COMPARISON_GAP
  const contentWidth = view === 'comparison' ? comparisonWidth : page.width
  const fitScale = Math.min((viewport.width - 72) / contentWidth, (viewport.height - 72) / page.height)
  const scale = Math.max(0.01, fitScale * zoom)
  const regionLabelScale = 1 / Math.max(.01, fitScale)
  const origin = {x: (viewport.width - contentWidth * scale) / 2 + pan.x, y: (viewport.height - page.height * scale) / 2 + pan.y}

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
    const node = view !== 'comparison' && singleSelection && selected && !selected.locked ? shapeRefs.current[selected.id] : null
    if (transformerRef.current) {
      transformerRef.current.nodes(node && tool === 'select' ? [node] : [])
      transformerRef.current.getLayer()?.batchDraw()
    }
  }, [selected, singleSelection, tool, regions, view])

  useEffect(() => { setStrokes([]); setStrokeRedo([]) }, [page.id, selected?.id, maskRevision])

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
  useEffect(() => {
    if (view !== 'comparison') return
    cancelMarquee()
    setDrawing(false)
    setDraft([])
  }, [cancelMarquee, view])

  const openRegionContextMenu = (event: Konva.KonvaEventObject<MouseEvent>, region: TextRegion) => {
    event.evt.preventDefault()
    event.cancelBubble = true
    if (runningAction) return
    if (!selectedIds.includes(region.id)) select(region.id)
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
    if ((tool === 'mask-brush' || tool === 'mask-eraser') && selected) {
      setStrokes(value => [...value, {points: point, size: brushSize, hardness: brushHardness, erase: tool === 'mask-eraser'}])
      setStrokeRedo([])
      setDrawing(true)
    }
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
    if ((tool === 'mask-brush' || tool === 'mask-eraser') && selected) {
      setStrokes(value => value.map((stroke, index) => index === value.length - 1 ? {...stroke, points: [...stroke.points, ...point]} : stroke))
    }
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
      if (bbox[2] > 5 && bbox[3] > 5) await onCreate(bboxPolygon(bbox), bbox)
      setDraft([])
    }
    if (tool === 'lasso' && draft.length > 3) {
      await onCreate(draft, polygonBbox(draft)); setDraft([])
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

  const polygonClick = (event: Konva.KonvaEventObject<MouseEvent>) => {
    if (view === 'comparison' || tool !== 'polygon') return
    const point = pointer(event)
    if (point) setDraft(value => [...value, point])
  }
  const finishPolygon = async () => {
    if (view !== 'comparison' && tool === 'polygon' && draft.length >= 3) { await onCreate(draft, polygonBbox(draft)); setDraft([]) }
  }

  const updateBox = async (region: TextRegion, node: Konva.Rect) => {
    const previous = orientedRegionBox(region, editingTranslatedGeometry)
    const x = node.x(), y = node.y()
    const width = Math.max(4, node.width() * Math.abs(node.scaleX()))
    const height = Math.max(4, node.height() * Math.abs(node.scaleY()))
    const rotation = normalizeRotation(node.rotation())
    const previousRadians = previous.rotation * Math.PI / 180
    const nextRadians = rotation * Math.PI / 180
    const previousCosine = Math.cos(previousRadians), previousSine = Math.sin(previousRadians)
    const nextCosine = Math.cos(nextRadians), nextSine = Math.sin(nextRadians)
    const sourcePolygon = regionGeometry(region, editingTranslatedGeometry).polygon
    const transformedPolygon = sourcePolygon.map(point => {
      const offsetX = point[0] - previous.x, offsetY = point[1] - previous.y
      const localX = (offsetX * previousCosine + offsetY * previousSine) * width / previous.width
      const localY = (-offsetX * previousSine + offsetY * previousCosine) * height / previous.height
      return [x + localX * nextCosine - localY * nextSine, y + localX * nextSine + localY * nextCosine]
    })
    node.width(width); node.height(height); node.scaleX(1); node.scaleY(1); node.rotation(rotation)
    await onUpdate(region.id, editingTranslatedGeometry
      ? {translated_bbox: polygonBbox(transformedPolygon), translated_polygon: transformedPolygon, rotation}
      : {bbox: polygonBbox(transformedPolygon), polygon: transformedPolygon})
  }

  const saveMask = async () => {
    if (!selected) return
    const canvas = document.createElement('canvas')
    canvas.width = page.width; canvas.height = page.height
    const context = canvas.getContext('2d')!
    context.fillStyle = 'black'; context.fillRect(0, 0, page.width, page.height)
    if (selected.mask_url) {
      const image = await loadImage(`${selected.mask_url}?v=${maskRevision}`)
      context.drawImage(image, 0, 0, page.width, page.height)
    }
    for (const stroke of strokes) {
      context.save()
      context.globalCompositeOperation = stroke.erase ? 'destination-out' : 'source-over'
      context.strokeStyle = `rgba(255,255,255,${Math.max(.15, stroke.hardness)})`
      context.lineWidth = stroke.size
      context.lineCap = 'round'; context.lineJoin = 'round'
      context.shadowColor = stroke.erase ? 'transparent' : 'white'
      context.shadowBlur = (1 - stroke.hardness) * stroke.size * .55
      context.beginPath()
      for (let index = 0; index < stroke.points.length; index += 2) {
        if (index === 0) context.moveTo(stroke.points[index], stroke.points[index + 1])
        else context.lineTo(stroke.points[index], stroke.points[index + 1])
      }
      if (stroke.points.length === 2) context.lineTo(stroke.points[0] + .01, stroke.points[1] + .01)
      context.stroke(); context.restore()
    }
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(item => item ? resolve(item) : reject(new Error('Mask encoding failed')), 'image/png'))
    await onSaveMask(selected.id, blob)
    setStrokes([]); setStrokeRedo([])
  }

  const draftRect = draft.length === 2 ? [Math.min(draft[0][0], draft[1][0]), Math.min(draft[0][1], draft[1][1]), Math.abs(draft[1][0] - draft[0][0]), Math.abs(draft[1][1] - draft[0][1])] : null
  const contextButtonClass = 'flex h-9 min-h-9 w-full cursor-pointer items-center justify-start gap-2 rounded-lg border-0 bg-transparent px-3 text-[11px] text-secondary outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-wait disabled:opacity-50 [&_svg]:text-muted hover:[&_svg]:text-accent'

  return <div className={cn('relative size-full overflow-hidden', middlePanningActive && 'cursor-grabbing [&_canvas]:!cursor-grabbing', marquee && 'cursor-crosshair [&_canvas]:!cursor-crosshair')} ref={containerRef} onAuxClick={event => event.preventDefault()}>
    <Stage
      width={viewport.width} height={viewport.height}
      onMouseDown={handleDown} onTouchStart={handleDown}
      onMouseMove={handleMove} onTouchMove={handleMove}
      onMouseUp={handleUp} onTouchEnd={handleUp}
      onClick={event => {setContextMenu(null); polygonClick(event)}} onDblClick={finishPolygon}
      onWheel={handleWheel}
    >
      <Layer>
        <Group x={origin.x} y={origin.y} scaleX={scale} scaleY={scale}>
          <Rect name="canvas-background" width={page.width} height={page.height} fill="#f4f0e8" shadowColor="black" shadowOpacity={.38} shadowBlur={28 / scale} />
          {view === 'comparison' && <>
            <KonvaImage image={original ?? undefined} width={page.width} height={page.height} listening={false}/>
            <Rect x={page.width + COMPARISON_GAP} width={page.width} height={page.height} fill="#f4f0e8" shadowColor="black" shadowOpacity={.38} shadowBlur={28 / scale} listening={false}/>
            <Group x={page.width + COMPARISON_GAP} clipX={0} clipY={0} clipWidth={page.width} clipHeight={page.height} listening={false}>
              <KonvaImage image={clean ?? undefined} width={page.width} height={page.height}/>
              <TranslatedRegions regions={regions}/>
            </Group>
            <ComparisonLabel x={12 / scale} scale={1 / scale} text="原图"/>
            <ComparisonLabel x={page.width + COMPARISON_GAP + 12 / scale} scale={1 / scale} text="译文"/>
          </>}
          {layers.original && <KonvaImage image={original ?? undefined} width={page.width} height={page.height} listening={false} />}
          {layers.clean && <KonvaImage image={clean ?? undefined} width={page.width} height={page.height} listening={false} />}
          {layers.masks && regions.filter(region => region.mask_url).map(region => <MaskImage key={`${region.id}-${maskRevision}`} source={`${region.mask_url}?v=${maskRevision}`} width={page.width} height={page.height} />)}
          {layers.translated && <TranslatedRegions regions={regions}/>}
          {layers.detection && regions.map(region => {
            const selectedNow = selectedIds.includes(region.id)
            const geometry = regionGeometry(region, editingTranslatedGeometry)
            const interactionBox = orientedRegionBox(region, editingTranslatedGeometry)
            return <Group key={region.id} onContextMenu={event => openRegionContextMenu(event, region)}>
              {!(singleSelection && selectedNow && tool === 'select' && !region.locked) && (editingTranslatedGeometry
                ? <Rect x={interactionBox.x} y={interactionBox.y} width={interactionBox.width} height={interactionBox.height} rotation={interactionBox.rotation} stroke={selectedNow ? '#00d7aa' : region.visible ? '#4aa8ff' : '#77776f'} strokeWidth={(selectedNow ? 3 : 1.5) / scale} dash={region.visible ? undefined : [6 / scale, 4 / scale]} fill={selectedNow ? 'rgba(0,215,170,.08)' : undefined} opacity={region.visible ? 1 : .7} listening={false}/>
                : <Line points={flat(geometry.polygon)} closed stroke={selectedNow ? '#00d7aa' : region.visible ? '#4aa8ff' : '#77776f'} strokeWidth={(selectedNow ? 3 : 1.5) / scale} dash={region.visible ? undefined : [6 / scale, 4 / scale]} fill={selectedNow ? 'rgba(0,215,170,.08)' : undefined} opacity={region.visible ? 1 : .7} listening={false}/>)}
              <Rect
                ref={node => { shapeRefs.current[region.id] = node }} x={interactionBox.x} y={interactionBox.y} width={interactionBox.width} height={interactionBox.height}
                rotation={interactionBox.rotation} fill="rgba(0,0,0,.001)" draggable={tool === 'select' && selectedIds.length < 2 && !region.locked}
                onClick={event => { event.cancelBubble = true; select(region.id, tool === 'select') }}
                onTap={() => select(region.id, tool === 'select')}
                onDragMove={() => transformerRef.current?.forceUpdate()}
                onTransform={() => transformerRef.current?.forceUpdate()}
                onDragEnd={event => updateBox(region, event.target as Konva.Rect)}
                onTransformEnd={event => updateBox(region, event.target as Konva.Rect)}
              />
              <Text x={geometry.bbox[0]} y={geometry.bbox[1] - 17 * regionLabelScale} text={`${region.region_key}${region.visible ? '' : '  · 已关闭'}${region.locked ? '  🔒' : ''}`} fill={selectedNow ? '#65ddc5' : region.visible ? '#4aa8ff' : '#888880'} fontSize={12 * regionLabelScale} listening={false} />
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
          {view !== 'comparison' && (tool === 'polygon' || tool === 'lasso') && draft.length > 0 && <Line points={flat(draft)} closed={tool === 'lasso'} stroke="#ffcb45" fill="rgba(255,203,69,.08)" strokeWidth={2 / scale} />}
          {view !== 'comparison' && strokes.map((stroke, index) => <Line key={index} points={stroke.points} stroke={stroke.erase ? '#1d1d1b' : '#ff4940'} opacity={stroke.erase ? .7 : .55 + stroke.hardness * .3} strokeWidth={stroke.size} lineCap="round" lineJoin="round" />)}
          <Transformer
            ref={transformerRef}
            rotateEnabled
            flipEnabled={false}
            rotateAnchorOffset={26}
            enabledAnchors={['top-left','top-right','bottom-left','bottom-right','middle-left','middle-right','top-center','bottom-center']}
            borderStroke="#00d7aa"
            borderStrokeWidth={1.25}
            anchorFill="#fff"
            anchorStroke="#00b991"
            anchorStrokeWidth={1.25}
            anchorCornerRadius={1.5}
            anchorSize={TRANSFORMER_HANDLE_SIZE}
            anchorStyleFunc={styleTransformerAnchor}
          />
        </Group>
      </Layer>
    </Stage>
    {contextMenu && <div ref={contextMenuRef} className="absolute z-[80] w-[178px] rounded-xl border border-line-strong bg-[rgb(24_27_23/.97)] p-1 text-secondary shadow-panel backdrop-blur-xl" style={{left: contextMenu.x, top: contextMenu.y}} role="menu" aria-label={`${contextMenu.regionKey} 区域操作`}>
      <div className="mb-1 flex h-[38px] items-center gap-2 border-b border-line-subtle px-2"><span className="font-mono text-[11px] font-semibold text-accent">{contextMenu.regionKey}</span><small className="text-[9px] text-muted">区域操作</small></div>
      <button className={cn(contextButtonClass, runningAction === 'ocr' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} onClick={() => runRegionContextAction('ocr')}>{runningAction === 'ocr' ? <LoaderCircle className="animate-spin text-accent" size={15}/> : <ScanText size={15}/>}<span>{runningAction === 'ocr' ? 'OCR 处理中…' : '重新 OCR'}</span></button>
      <button className={cn(contextButtonClass, runningAction === 'translate' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} onClick={() => runRegionContextAction('translate')}>{runningAction === 'translate' ? <LoaderCircle className="animate-spin text-accent" size={15}/> : <Languages size={15}/>}<span>{runningAction === 'translate' ? '翻译处理中…' : '重新翻译'}</span></button>
      <button className={cn(contextButtonClass, runningAction === 'inpaint' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} onClick={() => runRegionContextAction('inpaint')}>{runningAction === 'inpaint' ? <LoaderCircle className="animate-spin text-accent" size={15}/> : <Eraser size={15}/>}<span>{runningAction === 'inpaint' ? '背景修复中…' : '重新修复'}</span></button>
      <button className={cn(contextButtonClass, runningAction === 'render' && 'bg-accent/10 text-ink')} role="menuitem" disabled={!!runningAction} onClick={() => runRegionContextAction('render')}>{runningAction === 'render' ? <LoaderCircle className="animate-spin text-accent" size={15}/> : <TextCursorInput size={15}/>}<span>{runningAction === 'render' ? '排版处理中…' : '重新排版'}</span></button>
    </div>}
    {view !== 'comparison' && <div className="absolute right-3.5 top-3.5 z-10 w-44 select-none overflow-hidden rounded-xl border border-line-strong bg-[rgb(24_27_23/.9)] text-secondary shadow-panel backdrop-blur-xl" aria-label="画布图层">
      <button className={cn('flex h-[34px] min-h-[34px] w-full cursor-pointer items-center justify-start gap-2 border-0 bg-transparent px-3 font-mono text-[10px] font-medium uppercase leading-none tracking-[1px] text-secondary outline-none transition-colors hover:bg-white/[.04] hover:text-ink [&>svg:first-child]:text-accent', !layersCollapsed && 'border-b border-line')} type="button" aria-expanded={!layersCollapsed} onClick={() => setLayersCollapsed(value => !value)}>
        <Layers3 className="shrink-0" size={14}/><span className="flex h-full items-center leading-none">图层</span><ChevronDown className={cn('ml-auto text-muted transition-transform duration-200', layersCollapsed && '-rotate-90')} size={14}/>
      </button>
      <div className={cn('flex max-h-[194px] flex-col gap-1 overflow-hidden p-1 transition-all duration-200', layersCollapsed && 'pointer-events-none max-h-0 py-0 opacity-0')}>{Object.entries(layers).map(([name, visible]) => <label className="relative flex h-[34px] shrink-0 cursor-pointer items-center gap-2 rounded-lg px-2 text-[11px] leading-none text-secondary transition-colors hover:bg-white/[.05] hover:text-ink" key={name}>
        <input className="sr-only" type="checkbox" checked={visible} onChange={() => toggleLayer(name as keyof typeof layers)} />
        <span className={cn('size-3 shrink-0 rounded-full border border-accent/50 transition', visible && 'border-accent bg-accent shadow-[0_0_8px_rgb(16_211_163/.25)]')} />
        <span className="flex h-full items-center leading-none">{({original:'原始图像',detection:'检测区域',masks:'文字 Mask',clean:'修复背景',translated:'翻译文字'} as Record<string,string>)[name]}</span>
      </label>)}</div>
    </div>}
    {view !== 'comparison' && (tool === 'mask-brush' || tool === 'mask-eraser') && <div className="absolute bottom-4 left-1/2 flex -translate-x-1/2 gap-1 rounded-xl border border-line-strong bg-[rgb(24_27_23/.94)] p-2 shadow-panel backdrop-blur-xl">
      <button className={`${buttonClass} !min-h-[34px] px-3 text-[11px]`} disabled={!strokes.length} onClick={() => { const last = strokes.at(-1); if (last) {setStrokes(value => value.slice(0, -1)); setStrokeRedo(value => [...value, last])} }}>撤销笔画</button>
      <button className={`${buttonClass} !min-h-[34px] px-3 text-[11px]`} disabled={!strokeRedo.length} onClick={() => { const last = strokeRedo.at(-1); if (last) {setStrokeRedo(value => value.slice(0, -1)); setStrokes(value => [...value, last])} }}>重做</button>
      <button className={`${primaryButtonClass} !min-h-[34px] px-3 text-[11px]`} disabled={!selected || !strokes.length} onClick={saveMask}>保存 Mask</button>
    </div>}
    <div className="pointer-events-none absolute bottom-2.5 right-3 rounded-md border border-line bg-canvas/80 px-2 py-1 font-mono text-[8px] text-secondary">{Math.round(zoom * 100)}%</div>
  </div>
}

function loadImage(source: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image(); image.crossOrigin = 'anonymous'; image.onload = () => resolve(image); image.onerror = reject; image.src = source
  })
}

function TranslatedRegions({regions}: {regions: TextRegion[]}) {
  return <>{regions.filter(region => region.visible).map(region => {
    const renderStyle = resolvedRenderStyle(region)
    const geometry = regionGeometry(region, true)
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

function verticalPreviewLayout(region: TextRegion): VerticalPreviewLayout {
  if (!region.translated_text.trim()) {
    return {fontSize: Math.max(1, Math.round(region.font_size)), cellHeight: 0, columnWidth: 0, columns: [], width: 0, height: 0}
  }
  const geometry = regionGeometry(region, true)
  const polygonFactor = previewPolygonFactor(geometry.polygon, geometry.bbox)
  const availableWidth = Math.max(1, geometry.bbox[2] * .9 * polygonFactor)
  const availableHeight = Math.max(1, geometry.bbox[3] * .9 * polygonFactor)
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
