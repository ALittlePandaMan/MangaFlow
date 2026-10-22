import {t, useLocale} from '../../../i18n'
import {Layers3} from 'lucide-react'
import {forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type MutableRefObject, type PointerEvent as ReactPointerEvent} from 'react'
import {createPortal} from 'react-dom'
import type {ImagePage, TextRegion, Tool} from '../../../types'
import {buttonClass, cn} from '../../../ui'
import {MaskBitmap, type MaskBounds, type MaskPatch, type MaskPoint, type MaskTool} from '../lib/maskBitmap'
import {MaskEncoder} from '../lib/maskEncoder'
import {loadMaskHistory} from '../lib/maskHistory'
import {prepareMaskScene, type PreparedMaskScene} from '../lib/prepareMaskScene'
import {polygonDraftStyle} from '../lib/canvasPalette'
import {MaskControls} from './MaskControls'
import {MaskAutosaveQueue, type MaskSaveState} from '../lib/maskAutosave'

interface Props {
  page: ImagePage
  region: TextRegion
  regions: TextRegion[]
  tool: Tool
  operation?: MaskTool
  disabled?: boolean
  onToolChange: (tool: Tool) => void
  onSave: (region: TextRegion, blob: Blob, bounds?: MaskBounds) => Promise<TextRegion>
  onSelectRegion: (id: string) => void
  onStateChange: (state: MaskEditorState) => void
  onHistoryBoundary?: (direction: 'undo' | 'redo') => void
  onHistoryStep?: (direction:'undo'|'redo',region:TextRegion,operationId:string) => Promise<TextRegion>
  controlsTarget?: HTMLElement | null
  preferences?: MutableRefObject<MaskEditorPreferences>
  prepared?: PreparedMaskScene
}
export interface MaskEditorPreferences {diameter: number, referenceOpacity: number, showOriginal: boolean}
export interface MaskEditorState {undo: boolean, redo: boolean, dirty: boolean, busy: boolean, localHistory?:boolean, syncing?:boolean, confirming?: boolean}
export interface MaskEditorHandle {undo: () => void, redo: () => void, flush: () => Promise<void>, requestLeave: (leave: () => void) => void}
type Transform = {scale: number, x: number, y: number}
type Bounds = {left: number, top: number, right: number, bottom: number}
// Match the normal polygon tool: a fixed screen-space target at every zoom.
const POLYGON_CLOSE_DISTANCE = polygonDraftStyle.closeDistance

/** Isolated editing session: pointer movement never re-renders the workbench. */
export const MaskEditor = forwardRef<MaskEditorHandle, Props>(function MaskEditor({page, region, regions, tool, operation = tool === 'eraser' ? 'eraser' : 'brush', disabled = false, onToolChange, onSave, onSelectRegion, onStateChange, onHistoryBoundary, onHistoryStep, controlsTarget, preferences, prepared}, ref) {
  useLocale()
  const viewport = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const source = useRef<HTMLImageElement | null>(null)
  const overlay = useRef<HTMLCanvasElement | null>(null)
  const others = useRef<HTMLCanvasElement | null>(null)
  const savedRegion = useRef(region)
  const palette = useRef({paper: '#fff', border: '#dfe5ed', stroke: '#2864ae', color: [40,100,174], shadow: 'rgba(20,35,55,.06)'})
  const bitmap = useRef<MaskBitmap | null>(null)
  const transform = useRef<Transform>({scale: 1, x: 0, y: 0})
  const fitted = useRef(true)
  const size = useRef({width: 1, height: 1})
  const frame = useRef(0)
  const redraw = useRef(() => {})
  const dirtyBounds = useRef<Bounds | null>(null)
  const cursor = useRef<MaskPoint | null>(null)
  const pointers = useRef(new Map<number, MaskPoint>())
  const stroke = useRef<{id: number, last: MaskPoint, tool: MaskTool, diameter: number} | null>(null)
  const gesture = useRef<{center: MaskPoint, distance: number, transform: Transform} | null>(null)
  const panning = useRef<{point: MaskPoint, transform: Transform} | null>(null)
  const shape = useRef<{tool: Tool, operation: MaskTool, points: MaskPoint[]} | null>(null)
  const [showOriginal, setShowOriginal] = useState(() => preferences?.current.showOriginal ?? true)
  const [diameter, setDiameter] = useState(() => preferences?.current.diameter ?? 24)
  const [referenceOpacity, setReferenceOpacity] = useState(() => preferences?.current.referenceOpacity ?? 30)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [leaving, setLeaving] = useState(false)
  const leavingRef = useRef(false)
  const mounted = useRef(true)
  const [saveState, setSaveState] = useState<MaskSaveState>({pending:false,blocked:false,error:null})
  const [history, setHistory] = useState({undo: false, redo: false, dirty: false,localHistory:false})
  const encoder = useRef<MaskEncoder | null>(null)
  const savePixels = useRef((_patch: MaskPatch): Promise<void> => Promise.resolve())
  const savedPixels = useRef((_patch: MaskPatch) => {})
  const autosave = useRef<MaskAutosaveQueue | null>(null)
  if (!autosave.current) autosave.current = new MaskAutosaveQueue(
    patch => savePixels.current(patch),patch => savedPixels.current(patch),state => {
      if (mounted.current) setSaveState(previous => previous.pending === state.pending && previous.blocked === state.blocked && previous.error === state.error ? previous : state)
    })
  const actions = useRef({cancel: () => {}, undo: () => {}, redo: () => {}, complete: () => {}})
  const wheelZoom = useRef((_event: WheelEvent) => {})
  const schedule = () => { if (!frame.current) frame.current = requestAnimationFrame(() => {frame.current = 0; redraw.current()}) }
  const syncHistory = () => {
    const mask = bitmap.current
    setHistory({undo: !!mask?.canUndo, redo: !!mask?.canRedo, dirty: !!mask?.dirty,localHistory:!!mask?.hasHistory})
  }
  const invalidate = (bounds?: Bounds) => {
    const next = bounds || {left: 0, top: 0, right: page.width, bottom: page.height}
    const old = dirtyBounds.current
    dirtyBounds.current = old ? {left: Math.min(old.left, next.left), top: Math.min(old.top, next.top), right: Math.max(old.right, next.right), bottom: Math.max(old.bottom, next.bottom)} : next
    schedule()
  }
  const fit = (selected = false) => {
    fitted.current = !selected
    const [x,y,w,h] = selected ? region.bbox : [0,0,page.width,page.height]
    // Keep the fitted page clear of the top controls; no bottom save tray.
    const scale = Math.max(.02, Math.min(4, (size.current.width - 48) / (w + 60), (size.current.height - 144) / (h + 20)))
    transform.current = {scale, x: size.current.width / 2 - (x + w / 2) * scale, y: (size.current.height - 16) / 2 - (y + h / 2) * scale}
    schedule()
  }
  const zoomAt = (factor: number, anchor: MaskPoint = {x: size.current.width / 2, y: size.current.height / 2}) => {
    if (stroke.current) return
    fitted.current = false
    const old = transform.current, scale = Math.max(.02, Math.min(12, old.scale * factor))
    transform.current = {scale, x: anchor.x - (anchor.x - old.x) * scale / old.scale, y: anchor.y - (anchor.y - old.y) * scale / old.scale}
    schedule()
  }
  wheelZoom.current = event => {
    event.preventDefault()
    if (disabled || !ready || leavingRef.current || !canvas.current) return
    const rect = canvas.current.getBoundingClientRect()
    zoomAt(Math.exp(-event.deltaY * .001), {x: event.clientX - rect.left, y: event.clientY - rect.top})
  }
  redraw.current = () => {
    const context = canvas.current?.getContext('2d'), mask = bitmap.current, image = overlay.current
    if (!context || !canvas.current || !mask || !image) return
    const bounds = dirtyBounds.current
    if (bounds) {
      const left = Math.max(0, Math.floor(bounds.left)), top = Math.max(0, Math.floor(bounds.top))
      const width = Math.max(0, Math.min(page.width, Math.ceil(bounds.right)) - left)
      const height = Math.max(0, Math.min(page.height, Math.ceil(bounds.bottom)) - top)
      const maskContext = image.getContext('2d')
      if (width && height && maskContext) {
        const data = maskContext.createImageData(width, height)
        for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
          const index = (y * width + x) * 4
          data.data[index] = palette.current.color[0]; data.data[index + 1] = palette.current.color[1]; data.data[index + 2] = palette.current.color[2]
          data.data[index + 3] = mask.pixels[(y + top) * page.width + x + left]
        }
        maskContext.putImageData(data, left, top)
      }
      dirtyBounds.current = null
    }
    const ratio = canvas.current.width / size.current.width
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, size.current.width, size.current.height)
    const {x,y,scale} = transform.current
    context.save(); context.translate(x,y); context.scale(scale,scale)
    // The original is a 30% reference by default, never the editable bitmap.
    context.shadowColor = palette.current.shadow; context.shadowBlur = 12; context.shadowOffsetY = 3
    context.fillStyle = palette.current.paper; context.fillRect(0, 0, page.width, page.height)
    context.shadowBlur = 0; context.shadowOffsetY = 0
    context.globalAlpha = referenceOpacity / 100
    if (showOriginal && source.current) context.drawImage(source.current, 0, 0, page.width, page.height)
    context.globalAlpha = .75
    if (others.current) context.drawImage(others.current, 0, 0)
    context.globalAlpha = .45
    context.drawImage(image, 0, 0)
    context.globalAlpha = 1
    context.strokeStyle = palette.current.border; context.lineWidth = 1 / scale; context.strokeRect(0,0,page.width,page.height)
    context.strokeStyle = palette.current.stroke; context.setLineDash([4 / scale, 4 / scale])
    const polygon = region.polygon.length >= 3 ? region.polygon : [[region.bbox[0],region.bbox[1]], [region.bbox[0]+region.bbox[2],region.bbox[1]], [region.bbox[0]+region.bbox[2],region.bbox[1]+region.bbox[3]], [region.bbox[0],region.bbox[1]+region.bbox[3]]]
    context.beginPath(); polygon.forEach(([px,py], index) => index ? context.lineTo(px,py) : context.moveTo(px,py)); context.closePath(); context.stroke()
    if (shape.current?.points.length && shape.current.tool === 'polygon') {
      const points = shape.current.points, first = points[0]
      const hovered = cursor.current && !panning.current && !gesture.current ? pagePoint(cursor.current) : null
      const preview = hovered && hovered.x >= 0 && hovered.y >= 0 && hovered.x < page.width && hovered.y < page.height ? hovered : null
      const canClose = preview && points.length >= 3 && Math.hypot(preview.x-first.x, preview.y-first.y) * scale <= POLYGON_CLOSE_DISTANCE
      context.setLineDash([]); context.lineWidth = polygonDraftStyle.lineWidth / scale
      context.lineCap = 'round'; context.lineJoin = 'round'
      context.strokeStyle = polygonDraftStyle.stroke
      context.beginPath(); points.forEach((point,index) => index ? context.lineTo(point.x,point.y) : context.moveTo(point.x,point.y)); context.stroke()
      if (preview) {
        const end = canClose ? first : preview, last = points.at(-1)!
        context.setLineDash(polygonDraftStyle.dash.map(value => value / scale)); context.lineWidth = polygonDraftStyle.previewWidth / scale
        context.strokeStyle = canClose ? polygonDraftStyle.selected : polygonDraftStyle.stroke
        context.beginPath(); context.moveTo(last.x,last.y); context.lineTo(end.x,end.y); context.stroke()
      }
      context.setLineDash([]); context.lineWidth = polygonDraftStyle.pointBorderWidth / scale
      points.forEach((point,index) => {
        context.beginPath(); context.arc(point.x,point.y,(index === 0 ? polygonDraftStyle.firstPointRadius : polygonDraftStyle.pointRadius) / scale,0,Math.PI * 2)
        context.fillStyle = index === 0 && canClose ? polygonDraftStyle.selected : polygonDraftStyle.pointFill
        context.strokeStyle = index === 0 ? polygonDraftStyle.firstPointBorder : polygonDraftStyle.pointBorder
        context.fill(); context.stroke()
      })
    } else if (shape.current?.points.length) {
      context.strokeStyle = palette.current.stroke
      context.beginPath(); shape.current.points.forEach((point,index) => index ? context.lineTo(point.x,point.y) : context.moveTo(point.x,point.y))
      context.closePath(); context.stroke()
    }
    context.restore()
    if (cursor.current && (tool === 'brush' || tool === 'eraser') && !gesture.current) {
      context.beginPath(); context.arc(cursor.current.x, cursor.current.y, diameter * scale / 2, 0, Math.PI * 2)
      context.strokeStyle = '#fff'; context.lineWidth = 3; context.stroke()
      context.strokeStyle = '#1f2937'; context.lineWidth = 1; context.stroke()
    }
  }

  useLayoutEffect(() => {
    // Canvas colors follow the same theme as the surrounding workbench.
    const update = () => {
      palette.current = document.documentElement.dataset.theme === 'light'
        ? {paper:'#fff', border:'#dfe5ed', stroke:'#2864ae', color:[40,100,174], shadow:'rgba(20,35,55,.06)'}
        : {paper:'#24272d', border:'#414955', stroke:'#8dbdff', color:[105,167,245], shadow:'rgba(0,0,0,.2)'}
      invalidate()
      // A theme snapshot suspends RAF while committing its DOM update. Paint
      // now so the incoming snapshot includes the same palette as the panels.
      redraw.current()
    }
    const observer = new MutationObserver(update)
    observer.observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']})
    update()
    return () => observer.disconnect()
    // invalidate only reads refs and the fixed page dimensions in this session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useLayoutEffect(() => {
    const element = viewport.current
    if (!element) return
    const resize = () => {
      if (element.clientWidth < 2 || element.clientHeight < 2) return
      const previous = size.current
      size.current = {width: element.clientWidth, height: element.clientHeight}
      if (bitmap.current && fitted.current) fit()
      else if (bitmap.current && previous.width > 1 && previous.height > 1) {
        const old = transform.current, factor = Math.min(size.current.width / previous.width, size.current.height / previous.height)
        transform.current = {scale: old.scale * factor, x: size.current.width/2 - (previous.width/2-old.x)*factor, y: size.current.height/2 - (previous.height/2-old.y)*factor}
      }
      const ratio = Math.min(2, window.devicePixelRatio || 1)
      if (canvas.current) {canvas.current.width = Math.round(size.current.width * ratio); canvas.current.height = Math.round(size.current.height * ratio)}
      schedule()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(element); resize()
    return () => observer.disconnect()
    // Page geometry is fixed for this keyed editing session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useLayoutEffect(() => {
    let cancelled = false
    const install = (scene: PreparedMaskScene) => {
      if (cancelled) return
      const image = document.createElement('canvas'); image.width = page.width; image.height = page.height
      bitmap.current = scene.mask; source.current = scene.source; overlay.current = image; others.current = scene.others
      invalidate(); fit(); redraw.current(); setReady(true)
    }
    if (prepared) install(prepared)
    else void prepareMaskScene(page,region,regions).then(install).catch(reason => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason))
    })
    return () => {cancelled = true; cancelAnimationFrame(frame.current); frame.current = 0}
    // The parent keys this session by page/region. It must not reload over strokes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {actions.current.cancel()}, [tool, operation])
  useEffect(() => {schedule()}, [referenceOpacity, diameter, showOriginal])
  useEffect(() => {
    if (preferences) preferences.current = {referenceOpacity, diameter, showOriginal}
  }, [preferences, referenceOpacity, diameter, showOriginal])
  useEffect(() => {onStateChange({...history, busy: leaving,syncing:saveState.pending})}, [history, leaving, onStateChange,saveState.pending])
  useEffect(() => {
    if (!ready || !onHistoryStep) return
    let cancelled=false
    void loadMaskHistory(region.id,page.width,page.height).then(cached => {
      if (cancelled || stroke.current || shape.current || autosave.current?.pending || bitmap.current?.hasHistory || cached.region_updated_at !== savedRegion.current.updated_at) return
      if (bitmap.current?.seedHistory(cached.undo,cached.redo)) {
        savedRegion.current={...savedRegion.current,editor_history:cached.history}
        syncHistory()
      }
    }).catch(() => {})
    return () => {cancelled=true}
    // Warm once for this keyed region, never replace a live edit with a fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[ready])
  useEffect(() => {mounted.current=true; return () => {mounted.current=false;encoder.current?.dispose();encoder.current=null}}, [])
  useEffect(() => {
    const element = canvas.current
    const wheel = (event: WheelEvent) => wheelZoom.current(event)
    // React's delegated wheel event is passive in Chromium. A local listener
    // keeps browser/page scrolling from competing with canvas zoom.
    element?.addEventListener('wheel', wheel, {passive: false})
    return () => element?.removeEventListener('wheel', wheel)
  }, [])
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable="true"]')) return
      if (event.key === 'Escape' && (shape.current || stroke.current)) {event.preventDefault(); actions.current.cancel()}
      if (event.key === 'Enter' && shape.current?.tool === 'polygon') {event.preventDefault(); actions.current.complete()}
      if ((event.ctrlKey || event.metaKey) && ['z','y'].includes(event.key.toLowerCase())) {
        event.preventDefault(); event.stopPropagation()
        if (event.key.toLowerCase() === 'y' || event.shiftKey) actions.current.redo(); else actions.current.undo()
      }
    }
    const beforeUnload = (event: BeforeUnloadEvent) => {if (bitmap.current?.dirty || autosave.current?.pending) {event.preventDefault(); event.returnValue = ''}}
    document.addEventListener('keydown', keyboard, true); window.addEventListener('beforeunload', beforeUnload)
    return () => {document.removeEventListener('keydown', keyboard, true); window.removeEventListener('beforeunload', beforeUnload)}
  }, [])

  const cancelStroke = () => {shape.current = null; if (stroke.current) {bitmap.current?.cancel(); stroke.current = null; invalidate(); syncHistory()} schedule()}
  const historyAction = (direction: 'undo' | 'redo') => {
    if (disabled || stroke.current || leavingRef.current) return
    if (autosave.current?.blocked && !onHistoryBoundary) return
    cancelStroke()
    const mask = bitmap.current
    if (mask && onHistoryStep && (direction === 'undo' ? mask.canUndo : mask.canRedo)) {
      mask[direction]()
      const patch=mask.patch()
      const operationId=globalThis.crypto?.randomUUID?.() || `mask-${Date.now()}-${Math.random().toString(36).slice(2)}`
      invalidate({left:patch.x,top:patch.y,right:patch.x+patch.width,bottom:patch.y+patch.height});syncHistory()
      autosave.current?.enqueue(patch,async () => {savedRegion.current=await onHistoryStep(direction,savedRegion.current,operationId)})
      return
    }
    if (onHistoryBoundary) {
      if (onHistoryStep && autosave.current?.pending) return
      onHistoryBoundary(direction); return
    }
    if (!mask || !(direction === 'undo' ? mask.canUndo : mask.canRedo)) return
    mask[direction](); invalidate(); syncHistory(); autosave.current?.enqueue(mask.patch())
  }
  const requestLeave = (leave: () => void) => {
    if (leavingRef.current) return
    cancelStroke()
    if (!autosave.current?.pending) {leave(); return}
    leavingRef.current=true; setLeaving(true)
    void autosave.current.flush().then(leave).catch(() => {}).finally(() => {
      leavingRef.current=false
      if (mounted.current) setLeaving(false)
    })
  }
  const completeShape = () => {
    if (disabled || !shape.current || (shape.current.tool === 'polygon' && shape.current.points.length < 3) || region.locked || leavingRef.current) return
    bitmap.current?.fillPolygon(shape.current.points, shape.current.operation)
    const changed=bitmap.current?.commit()
    shape.current = null; invalidate(); syncHistory()
    if (changed && bitmap.current) autosave.current?.enqueue(bitmap.current.patch())
  }
  actions.current = {cancel: cancelStroke, undo: () => historyAction('undo'), redo: () => historyAction('redo'), complete: completeShape}
  useImperativeHandle(ref, () => ({undo: actions.current.undo, redo: actions.current.redo, requestLeave, flush:async () => {cancelStroke(); await autosave.current?.flush()}}))

  const localPoint = (event: ReactPointerEvent) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return {x: event.clientX - rect.left, y: event.clientY - rect.top}
  }
  const pagePoint = (point: MaskPoint) => ({x: (point.x - transform.current.x) / transform.current.scale, y: (point.y - transform.current.y) / transform.current.scale})
  const paint = (from: MaskPoint, to: MaskPoint, brush: MaskTool, width: number) => {
    bitmap.current?.paint(from, to, width, brush)
    invalidate({left: Math.min(from.x,to.x) - width, top: Math.min(from.y,to.y) - width, right: Math.max(from.x,to.x) + width, bottom: Math.max(from.y,to.y) + width})
  }
  const pair = () => {
    const [a,b] = [...pointers.current.values()]
    return {center: {x: (a.x+b.x)/2, y: (a.y+b.y)/2}, distance: Math.max(1, Math.hypot(a.x-b.x, a.y-b.y))}
  }
  const down = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (disabled || !ready || leavingRef.current || ![0,1].includes(event.button)) return
    if (autosave.current?.blocked && tool !== 'pan' && tool !== 'select' && event.button !== 1) return
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId)
    const point = localPoint(event); pointers.current.set(event.pointerId,point); cursor.current = point
    if (pointers.current.size === 2) {cancelStroke(); panning.current = null; gesture.current = {...pair(), transform: {...transform.current}}; return}
    if (pointers.current.size > 1 || gesture.current) return
    if (tool === 'pan' || event.button === 1) {panning.current = {point, transform: {...transform.current}}; event.currentTarget.style.cursor = 'grabbing'; schedule(); return}
    const at = pagePoint(point)
    if (at.x < 0 || at.y < 0 || at.x >= page.width || at.y >= page.height) return
    if (tool === 'select') {
      const hit = [...regions].reverse().find(item => at.x >= item.bbox[0] && at.x <= item.bbox[0]+item.bbox[2] && at.y >= item.bbox[1] && at.y <= item.bbox[1]+item.bbox[3])
      if (hit && hit.id !== region.id) requestLeave(() => onSelectRegion(hit.id))
      return
    }
    if (region.locked) return
    if (tool === 'polygon') {
      const draft = shape.current
      if (draft && draft.points.length >= 3 && Math.hypot(at.x-draft.points[0].x, at.y-draft.points[0].y) * transform.current.scale <= POLYGON_CLOSE_DISTANCE) completeShape()
      else {
        const last = draft?.points.at(-1)
        if (last && Math.hypot(at.x-last.x,at.y-last.y) * transform.current.scale < 2) return
        shape.current = {tool, operation, points: [...(draft?.points || []), at]}; schedule()
      }
      return
    }
    if (tool === 'rectangle' || tool === 'lasso') {shape.current = {tool, operation, points: [at]}; return}
    stroke.current = {id: event.pointerId, last: at, tool: operation, diameter}
    paint(at, at, operation, diameter)
  }
  const move = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const point = localPoint(event); cursor.current = point
    if (pointers.current.has(event.pointerId)) pointers.current.set(event.pointerId,point)
    if (gesture.current) {
      if (pointers.current.size >= 2) {
        fitted.current = false
        const current = pair(), start = gesture.current, old = start.transform
        const scale = Math.max(.02, Math.min(12, old.scale * current.distance / start.distance))
        transform.current = {scale, x: current.center.x - (start.center.x-old.x) * scale/old.scale, y: current.center.y - (start.center.y-old.y) * scale/old.scale}
      }
    } else if (panning.current) {
      fitted.current = false
      const start = panning.current
      transform.current = {...start.transform, x: start.transform.x + point.x-start.point.x, y: start.transform.y + point.y-start.point.y}
    } else if (shape.current && pointers.current.has(event.pointerId) && shape.current.tool !== 'polygon') {
      const draft = shape.current, at = pagePoint(point), first = draft.points[0]
      if (draft.tool === 'rectangle') draft.points = [first, {x:at.x,y:first.y}, at, {x:first.x,y:at.y}]
      else if (Math.hypot(at.x-draft.points.at(-1)!.x,at.y-draft.points.at(-1)!.y) * transform.current.scale >= 2) draft.points.push(at)
    } else if (stroke.current?.id === event.pointerId) {
      const current = stroke.current, at = pagePoint(point)
      paint(current.last, at, current.tool, current.diameter); current.last = at
    }
    schedule()
  }
  const up = (event: ReactPointerEvent<HTMLCanvasElement>, cancelled = false) => {
    if (shape.current && shape.current.tool !== 'polygon') {if (cancelled) cancelStroke(); else completeShape()}
    if (stroke.current?.id === event.pointerId) {
      if (cancelled) cancelStroke()
      else {
        const changed=bitmap.current?.commit()
        stroke.current = null; syncHistory()
        if (changed && bitmap.current) autosave.current?.enqueue(bitmap.current.patch())
      }
    }
    pointers.current.delete(event.pointerId); panning.current = null; event.currentTarget.style.cursor = ''
    if (!pointers.current.size) gesture.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    schedule()
  }
  savePixels.current = async patch => {
      encoder.current ??= new MaskEncoder()
      const blob = await encoder.current.encode(patch)
      savedRegion.current = await onSave(savedRegion.current, blob, {x:patch.x,y:patch.y,width:patch.width,height:patch.height})
  }
  savedPixels.current = patch => {bitmap.current?.markPatchSaved(patch); if (mounted.current) syncHistory()}
  const controlsDisabled = disabled || !ready || leaving
  const saveError=saveState.error instanceof Error ? saveState.error.message : saveState.error ? String(saveState.error) : ''
  const controls = <MaskControls region={region} regions={regions} tool={tool} operation={operation} onToolChange={onToolChange} diameter={diameter} referenceOpacity={referenceOpacity} showOriginal={showOriginal} disabled={controlsDisabled} onRegionChange={id => {if (id !== region.id) requestLeave(() => onSelectRegion(id))}} onDiameterChange={setDiameter} onReferenceOpacityChange={setReferenceOpacity} onReferenceChange={setShowOriginal}/>
  return <section aria-label={t("蒙版模式")} className="absolute inset-0 flex min-h-0 flex-col text-ink">
    {controlsTarget ? createPortal(controls, controlsTarget) : controlsTarget === undefined ? controls : null}
    {error && <p role="alert" className="m-0 shrink-0 bg-danger/10 px-4 py-2 text-sm text-danger-soft-ink">{error}</p>}
    {saveError && <div role="alert" className="flex shrink-0 items-center gap-3 bg-danger/10 px-4 py-2 text-xs text-danger-soft-ink"><span className="min-w-0 flex-1">{t("蒙版自动保存失败，修改仍保留：")}{saveError}</span><button className={buttonClass} disabled={leaving} onClick={() => {void autosave.current?.flush().catch(() => {})}}>{t("重试同步")}</button></div>}
    {region.locked ? <p className="m-0 shrink-0 bg-accent-soft px-4 py-2 text-xs text-accent-soft-ink">{t("此区域已锁定，仅可查看蒙版。")}</p> : !region.mask_url && <p className="m-0 shrink-0 bg-accent-soft px-4 py-2 text-xs text-accent-soft-ink">{t("此区域暂无蒙版，可用顶部工具补画，或切回原图执行“重新修复”生成。")}</p>}
    <div ref={viewport} className="relative min-h-0 flex-1 overflow-hidden bg-canvas">
      <canvas ref={canvas} aria-label={t("蒙版编辑画布")} className={cn('block size-full touch-none', disabled ? 'pointer-events-none' : tool === 'pan' ? 'cursor-grab' : tool === 'select' ? 'cursor-pointer' : 'cursor-crosshair')} onPointerDown={down} onPointerMove={move} onPointerUp={event => up(event)} onPointerCancel={event => up(event,true)} onLostPointerCapture={event => {panning.current = null; event.currentTarget.style.cursor = ''; if (stroke.current?.id === event.pointerId) cancelStroke()}} onPointerLeave={() => {cursor.current = null; schedule()}} onContextMenu={event => event.preventDefault()}/>
      <div className="mask-active-region"><Layers3 size={14} aria-hidden="true"/><span>{region.region_key}</span>{region.locked && <span className="mask-caption">{t("已锁定")}</span>}</div>
    </div>
  </section>
})
