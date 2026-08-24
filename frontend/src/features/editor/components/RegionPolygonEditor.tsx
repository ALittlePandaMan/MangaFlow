import Konva from 'konva'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Circle, Group, Line, Rect, Text } from 'react-konva'

type Point = [number, number]

interface Props {
  polygon: number[][]
  pageWidth: number
  pageHeight: number
  scale: number
  labelScale: number
  label: string
  labelColor: string
  visible: boolean
  onSelect: () => void
  onContextMenu: (event: Konva.KonvaEventObject<MouseEvent>) => void
  onPreview?: (polygon: number[][], rotationDelta?: number) => void
  onCommit: (polygon: number[][], rotationDelta?: number) => Promise<void>
}

type ReshapeDrag = {
  kind: 'corner' | 'edge'
  index: number
  start: Point[]
  winding: number
  minimumEdgeLength: number
  midpoint?: Point
  movementAxis?: 'horizontal' | 'vertical'
}

type RotationDrag = {
  start: Point[]
  center: Point
  edgeIndex: number
  startAngle: number
  lastDelta: number
  winding: number
  minimumEdgeLength: number
  stage: Konva.Stage
}

const CORNER_SIZE = 11
const EDGE_HANDLE_LENGTH = 18
const EDGE_HANDLE_THICKNESS = 6
const EDGE_HIT_THICKNESS = 14
const MIN_EDGE_LENGTH = 4
const MIN_POLYGON_AREA = 16
const GEOMETRY_EPSILON = .001
const MAX_VERTEX_HANDLES = 128
const MAX_EDGE_HANDLES = 24
const ROTATION_HANDLE_OFFSET = 30
const ROTATION_HANDLE_RADIUS = 5.5
const ROTATION_HIT_WIDTH = 16

const copyPoints = (polygon: number[][]): Point[] => {
  const points = polygon.map(point => [point[0], point[1]] as Point)
  if (points.length > 3 && Math.hypot(points[0][0] - points.at(-1)![0], points[0][1] - points.at(-1)![1]) <= GEOMETRY_EPSILON) points.pop()
  return points
}
const flatPoints = (polygon: Point[]) => polygon.flatMap(point => point)
const midpoint = (first: Point, second: Point): Point => [(first[0] + second[0]) / 2, (first[1] + second[1]) / 2]
const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value))

const polygonBounds = (polygon: Point[]) => {
  const xs = polygon.map(point => point[0])
  const ys = polygon.map(point => point[1])
  return {
    x: Math.min(...xs),
    y: Math.min(...ys),
    width: Math.max(...xs) - Math.min(...xs),
    height: Math.max(...ys) - Math.min(...ys),
  }
}

const polygonCenter = (polygon: Point[]): Point => {
  const total = polygon.reduce((sum, point) => [sum[0] + point[0], sum[1] + point[1]] as Point, [0, 0])
  return [total[0] / polygon.length, total[1] / polygon.length]
}

const rotationHandleGeometry = (polygon: Point[], scale: number, preferredEdgeIndex?: number) => {
  if (polygon.length < 3) return null
  const center = polygonCenter(polygon)
  const edges = polygon.map((point, index) => {
    const next = polygon[(index + 1) % polygon.length]
    const edgeCenter = midpoint(point, next)
    return {anchor: edgeCenter, distanceFromTop: edgeCenter[1], index}
  })
  const topEdge = preferredEdgeIndex === undefined
    ? edges.reduce((top, edge) => edge.distanceFromTop < top.distanceFromTop ? edge : top)
    : edges[preferredEdgeIndex % edges.length]
  const anchor = topEdge.anchor
  const directionX = anchor[0] - center[0]
  const directionY = anchor[1] - center[1]
  const length = Math.hypot(directionX, directionY)
  const unitX = length > GEOMETRY_EPSILON ? directionX / length : 0
  const unitY = length > GEOMETRY_EPSILON ? directionY / length : -1
  const offset = ROTATION_HANDLE_OFFSET / scale
  return {
    anchor,
    edgeIndex: topEdge.index,
    handle: [anchor[0] + unitX * offset, anchor[1] + unitY * offset] as Point,
  }
}

const signedArea = (polygon: Point[]) => polygon.reduce((area, point, index) => {
  const next = polygon[(index + 1) % polygon.length]
  return area + point[0] * next[1] - next[0] * point[1]
}, 0) / 2

const cross = (first: Point, second: Point, third: Point) =>
  (second[0] - first[0]) * (third[1] - second[1]) - (second[1] - first[1]) * (third[0] - second[0])

const pointOnSegment = (point: Point, start: Point, end: Point) =>
  point[0] >= Math.min(start[0], end[0]) - GEOMETRY_EPSILON && point[0] <= Math.max(start[0], end[0]) + GEOMETRY_EPSILON &&
  point[1] >= Math.min(start[1], end[1]) - GEOMETRY_EPSILON && point[1] <= Math.max(start[1], end[1]) + GEOMETRY_EPSILON

const segmentsIntersect = (firstStart: Point, firstEnd: Point, secondStart: Point, secondEnd: Point) => {
  const firstToSecondStart = cross(firstStart, firstEnd, secondStart)
  const firstToSecondEnd = cross(firstStart, firstEnd, secondEnd)
  const secondToFirstStart = cross(secondStart, secondEnd, firstStart)
  const secondToFirstEnd = cross(secondStart, secondEnd, firstEnd)
  const crossesFirst = (firstToSecondStart > GEOMETRY_EPSILON && firstToSecondEnd < -GEOMETRY_EPSILON) || (firstToSecondStart < -GEOMETRY_EPSILON && firstToSecondEnd > GEOMETRY_EPSILON)
  const crossesSecond = (secondToFirstStart > GEOMETRY_EPSILON && secondToFirstEnd < -GEOMETRY_EPSILON) || (secondToFirstStart < -GEOMETRY_EPSILON && secondToFirstEnd > GEOMETRY_EPSILON)
  if (crossesFirst && crossesSecond) return true
  if (Math.abs(firstToSecondStart) <= GEOMETRY_EPSILON && pointOnSegment(secondStart, firstStart, firstEnd)) return true
  if (Math.abs(firstToSecondEnd) <= GEOMETRY_EPSILON && pointOnSegment(secondEnd, firstStart, firstEnd)) return true
  if (Math.abs(secondToFirstStart) <= GEOMETRY_EPSILON && pointOnSegment(firstStart, secondStart, secondEnd)) return true
  return Math.abs(secondToFirstEnd) <= GEOMETRY_EPSILON && pointOnSegment(firstEnd, secondStart, secondEnd)
}

const hasSelfIntersection = (polygon: Point[]) => {
  for (let first = 0; first < polygon.length; first += 1) {
    const firstEnd = (first + 1) % polygon.length
    for (let second = first + 1; second < polygon.length; second += 1) {
      const secondEnd = (second + 1) % polygon.length
      if (first === second || firstEnd === second || secondEnd === first) continue
      if (segmentsIntersect(polygon[first], polygon[firstEnd], polygon[second], polygon[secondEnd])) return true
    }
  }
  return false
}

const minimumEdgeConstraint = (polygon: Point[]) => polygon.every((point, index) => {
  const next = polygon[(index + 1) % polygon.length]
  return Math.hypot(next[0] - point[0], next[1] - point[1]) >= MIN_EDGE_LENGTH
}) ? MIN_EDGE_LENGTH : 0

const isValidPolygon = (polygon: Point[], pageWidth: number, pageHeight: number, winding: number, minimumEdgeLength: number) => {
  if (polygon.length < 3 || polygon.some(point => !Number.isFinite(point[0]) || !Number.isFinite(point[1]) || point[0] < 0 || point[0] > pageWidth || point[1] < 0 || point[1] > pageHeight)) return false
  const area = signedArea(polygon)
  if (Math.abs(area) < MIN_POLYGON_AREA || (winding && Math.sign(area) !== winding) || hasSelfIntersection(polygon)) return false
  for (let index = 0; index < polygon.length; index += 1) {
    const current = polygon[index]
    const next = polygon[(index + 1) % polygon.length]
    if (Math.hypot(next[0] - current[0], next[1] - current[1]) < Math.max(GEOMETRY_EPSILON, minimumEdgeLength)) return false
  }
  return true
}

const interpolatePolygon = (from: Point[], to: Point[], ratio: number): Point[] => from.map((point, index) => [
  point[0] + (to[index][0] - point[0]) * ratio,
  point[1] + (to[index][1] - point[1]) * ratio,
])

const normalizeRadians = (radians: number) => {
  let normalized = radians
  while (normalized > Math.PI) normalized -= Math.PI * 2
  while (normalized < -Math.PI) normalized += Math.PI * 2
  return normalized
}

const rotatePolygon = (polygon: Point[], center: Point, radians: number): Point[] => {
  const cosine = Math.cos(radians)
  const sine = Math.sin(radians)
  return polygon.map(point => {
    const x = point[0] - center[0]
    const y = point[1] - center[1]
    return [center[0] + x * cosine - y * sine, center[1] + x * sine + y * cosine]
  })
}

/** Keep a handle at its furthest valid position instead of snapping back. */
const constrainPolygon = (start: Point[], candidate: Point[], pageWidth: number, pageHeight: number, winding: number, minimumEdgeLength: number) => {
  if (isValidPolygon(candidate, pageWidth, pageHeight, winding, minimumEdgeLength)) return candidate
  let lower = 0
  let upper = 1
  let best = start
  for (let iteration = 0; iteration < 14; iteration += 1) {
    const ratio = (lower + upper) / 2
    const attempt = interpolatePolygon(start, candidate, ratio)
    if (isValidPolygon(attempt, pageWidth, pageHeight, winding, minimumEdgeLength)) {
      best = attempt
      lower = ratio
    } else upper = ratio
  }
  return best
}

const samePolygon = (first: Point[], second: Point[]) => first.length === second.length && first.every((point, index) =>
  Math.abs(point[0] - second[index][0]) < GEOMETRY_EPSILON && Math.abs(point[1] - second[index][1]) < GEOMETRY_EPSILON)

const setCursor = (event: Konva.KonvaEventObject<Event>, cursor: string) => {
  const stage = event.target.getStage()
  if (stage) stage.container().style.cursor = cursor
}

/**
 * Every polygon vertex has an independent handle. A mostly horizontal edge
 * moves vertically and a mostly vertical edge moves horizontally; both of its
 * endpoints receive the exact same offset so the original slope is preserved.
 * This covers OCR quadrilaterals and manually drawn regions without flattening
 * either shape back into a rectangle.
 */
export function RegionPolygonEditor({polygon, pageWidth, pageHeight, scale, labelScale, label, labelColor, visible, onSelect, onContextMenu, onPreview, onCommit}: Props) {
  const editorRef = useRef<Konva.Group>(null)
  const [points, setPoints] = useState<Point[]>(() => copyPoints(polygon))
  const pointsRef = useRef(points)
  const reshapeRef = useRef<ReshapeDrag | null>(null)
  const rotationRef = useRef<RotationDrag | null>(null)
  const wholeDragStart = useRef<Point[] | null>(null)
  const editingRef = useRef(false)

  const preview = (next: Point[], notify = true, rotationDelta = 0) => {
    pointsRef.current = next
    setPoints(next)
    if (notify) onPreview?.(next, rotationDelta)
  }

  useEffect(() => {
    if (!editingRef.current) preview(copyPoints(polygon), false)
  }, [polygon])

  const bounds = useMemo(() => polygonBounds(points), [points])
  const supportsPolygonReshape = points.length >= 3 && points.length <= MAX_VERTEX_HANDLES && points.every(point => Number.isFinite(point[0]) && Number.isFinite(point[1]))
  const supportsEdgeReshape = supportsPolygonReshape && points.length <= MAX_EDGE_HANDLES
  const rotationControl = useMemo(() => rotationHandleGeometry(points, scale, rotationRef.current?.edgeIndex), [points, scale])

  const finishEdit = (before: Point[], rotationDelta = 0) => {
    const next = copyPoints(pointsRef.current)
    reshapeRef.current = null
    rotationRef.current = null
    wholeDragStart.current = null
    editingRef.current = false
    if (!samePolygon(before, next) || Math.abs(rotationDelta) > GEOMETRY_EPSILON) void onCommit(next, rotationDelta)
  }

  const pointerInEditor = (stage: Konva.Stage): Point | null => {
    const pointer = stage.getPointerPosition()
    const editor = editorRef.current
    if (!pointer || !editor) return null
    const local = editor.getAbsoluteTransform().copy().invert().point(pointer)
    return [local.x, local.y]
  }

  const stopRotationListeners = (stage: Konva.Stage) => stage.off('.regionRotation')

  const moveRotation = (event: Konva.KonvaEventObject<Event>) => {
    const drag = rotationRef.current
    if (!drag) return
    const pointer = pointerInEditor(drag.stage)
    if (!pointer) return
    event.evt.preventDefault()
    let delta = normalizeRadians(Math.atan2(pointer[1] - drag.center[1], pointer[0] - drag.center[0]) - drag.startAngle)
    if (event.evt instanceof MouseEvent && event.evt.shiftKey) {
      const snap = Math.PI / 12
      delta = Math.round(delta / snap) * snap
    }
    const candidate = rotatePolygon(drag.start, drag.center, delta)
    if (!isValidPolygon(candidate, pageWidth, pageHeight, drag.winding, drag.minimumEdgeLength)) return
    drag.lastDelta = delta
    preview(candidate, true, delta * 180 / Math.PI)
  }

  const endRotation = (event: Konva.KonvaEventObject<Event>) => {
    const drag = rotationRef.current
    if (!drag) return
    event.evt.preventDefault()
    stopRotationListeners(drag.stage)
    finishEdit(drag.start, drag.lastDelta * 180 / Math.PI)
    setCursor(event, 'grab')
  }

  const beginRotation = (event: Konva.KonvaEventObject<MouseEvent | TouchEvent>) => {
    event.cancelBubble = true
    event.evt.preventDefault()
    const stage = event.target.getStage()
    if (!stage) return
    const pointer = pointerInEditor(stage)
    if (!pointer) return
    const start = copyPoints(pointsRef.current)
    const center = polygonCenter(start)
    if (Math.hypot(pointer[0] - center[0], pointer[1] - center[1]) <= GEOMETRY_EPSILON) return
    editingRef.current = true
    rotationRef.current = {
      start,
      center,
      edgeIndex: rotationControl?.edgeIndex ?? 0,
      startAngle: Math.atan2(pointer[1] - center[1], pointer[0] - center[0]),
      lastDelta: 0,
      winding: Math.sign(signedArea(start)),
      minimumEdgeLength: minimumEdgeConstraint(start),
      stage,
    }
    stopRotationListeners(stage)
    stage.on('mousemove.regionRotation touchmove.regionRotation', moveRotation)
    stage.on('mouseup.regionRotation touchend.regionRotation mouseleave.regionRotation', endRotation)
    setCursor(event, 'grabbing')
  }

  useEffect(() => () => {
    const stage = rotationRef.current?.stage
    if (stage) stopRotationListeners(stage)
  }, [])

  const beginCornerDrag = (event: Konva.KonvaEventObject<DragEvent>, index: number) => {
    event.cancelBubble = true
    const start = copyPoints(pointsRef.current)
    editingRef.current = true
    reshapeRef.current = {kind: 'corner', index, start, winding: Math.sign(signedArea(start)), minimumEdgeLength: minimumEdgeConstraint(start)}
    setCursor(event, 'crosshair')
  }

  const moveCorner = (event: Konva.KonvaEventObject<DragEvent>) => {
    event.cancelBubble = true
    const drag = reshapeRef.current
    if (!drag || drag.kind !== 'corner') return
    const node = event.target
    const candidate = copyPoints(drag.start)
    candidate[drag.index] = [clamp(node.x(), 0, pageWidth), clamp(node.y(), 0, pageHeight)]
    const next = constrainPolygon(drag.start, candidate, pageWidth, pageHeight, drag.winding, drag.minimumEdgeLength)
    preview(next)
    node.position({x: next[drag.index][0], y: next[drag.index][1]})
  }

  const endCornerDrag = (event: Konva.KonvaEventObject<DragEvent>) => {
    event.cancelBubble = true
    const drag = reshapeRef.current
    if (!drag || drag.kind !== 'corner') return
    const next = pointsRef.current[drag.index]
    event.target.position({x: next[0], y: next[1]})
    finishEdit(drag.start)
    setCursor(event, 'crosshair')
  }

  const beginEdgeDrag = (event: Konva.KonvaEventObject<DragEvent>, index: number) => {
    event.cancelBubble = true
    const start = copyPoints(pointsRef.current)
    const nextIndex = (index + 1) % start.length
    const edgeX = start[nextIndex][0] - start[index][0]
    const edgeY = start[nextIndex][1] - start[index][1]
    editingRef.current = true
    reshapeRef.current = {
      kind: 'edge', index, start, winding: Math.sign(signedArea(start)), minimumEdgeLength: minimumEdgeConstraint(start),
      midpoint: midpoint(start[index], start[nextIndex]),
      movementAxis: Math.abs(edgeX) >= Math.abs(edgeY) ? 'vertical' : 'horizontal',
    }
  }

  const moveEdge = (event: Konva.KonvaEventObject<DragEvent>) => {
    event.cancelBubble = true
    const drag = reshapeRef.current
    if (!drag || drag.kind !== 'edge' || !drag.midpoint || !drag.movementAxis) return
    const deltaX = event.target.x() - drag.midpoint[0]
    const deltaY = event.target.y() - drag.midpoint[1]
    const offset: Point = drag.movementAxis === 'vertical' ? [0, deltaY] : [deltaX, 0]
    const candidate = copyPoints(drag.start)
    const nextIndex = (drag.index + 1) % candidate.length
    candidate[drag.index] = [candidate[drag.index][0] + offset[0], candidate[drag.index][1] + offset[1]]
    candidate[nextIndex] = [candidate[nextIndex][0] + offset[0], candidate[nextIndex][1] + offset[1]]
    const next = constrainPolygon(drag.start, candidate, pageWidth, pageHeight, drag.winding, drag.minimumEdgeLength)
    preview(next)
    const nextMidpoint = midpoint(next[drag.index], next[nextIndex])
    event.target.position({x: nextMidpoint[0], y: nextMidpoint[1]})
  }

  const endEdgeDrag = (event: Konva.KonvaEventObject<DragEvent>) => {
    event.cancelBubble = true
    const drag = reshapeRef.current
    if (!drag || drag.kind !== 'edge') return
    const nextIndex = (drag.index + 1) % pointsRef.current.length
    const nextMidpoint = midpoint(pointsRef.current[drag.index], pointsRef.current[nextIndex])
    event.target.position({x: nextMidpoint[0], y: nextMidpoint[1]})
    finishEdit(drag.start)
  }

  const beginWholeDrag = (event: Konva.KonvaEventObject<DragEvent>) => {
    if (event.target !== event.currentTarget) return
    event.cancelBubble = true
    editingRef.current = true
    wholeDragStart.current = copyPoints(pointsRef.current)
    setCursor(event, 'grabbing')
  }

  const moveWholeRegion = (event: Konva.KonvaEventObject<DragEvent>) => {
    if (event.target !== event.currentTarget || !wholeDragStart.current) return
    event.cancelBubble = true
    const startBounds = polygonBounds(wholeDragStart.current)
    const x = clamp(event.target.x(), -startBounds.x, pageWidth - startBounds.x - startBounds.width)
    const y = clamp(event.target.y(), -startBounds.y, pageHeight - startBounds.y - startBounds.height)
    event.target.position({x, y})
    onPreview?.(wholeDragStart.current.map(point => [point[0] + x, point[1] + y]))
  }

  const endWholeDrag = (event: Konva.KonvaEventObject<DragEvent>) => {
    if (event.target !== event.currentTarget || !wholeDragStart.current) return
    event.cancelBubble = true
    const start = wholeDragStart.current
    const next = start.map(point => [point[0] + event.target.x(), point[1] + event.target.y()] as Point)
    event.target.position({x: 0, y: 0})
    preview(next)
    finishEdit(start)
    setCursor(event, 'grab')
  }

  return <Group ref={editorRef} draggable onDragStart={beginWholeDrag} onDragMove={moveWholeRegion} onDragEnd={endWholeDrag} onContextMenu={onContextMenu}>
    <Line
      points={flatPoints(points)} closed fill="rgba(0,215,170,.08)" stroke="#00d7aa"
      strokeWidth={2 / scale} hitStrokeWidth={12 / scale}
      dash={visible ? undefined : [6 / scale, 4 / scale]} opacity={visible ? 1 : .7}
      onClick={event => {
        event.cancelBubble = true
        if (event.evt.button !== 0) return
        onSelect()
      }}
      onTap={event => { event.cancelBubble = true; onSelect() }}
      onContextMenu={onContextMenu}
      onMouseEnter={event => setCursor(event, 'grab')}
      onMouseLeave={event => setCursor(event, 'default')}
    />
    <Text x={bounds.x} y={bounds.y - 17 * labelScale} text={label} fill={labelColor} fontSize={12 * labelScale} listening={false}/>
    {supportsPolygonReshape && rotationControl && <Group>
      <Line
        points={[...rotationControl.anchor, ...rotationControl.handle]}
        stroke="#00d7aa" strokeWidth={2 / scale} hitStrokeWidth={ROTATION_HIT_WIDTH / scale}
        lineCap="round"
        onMouseDown={beginRotation} onTouchStart={beginRotation}
        onContextMenu={onContextMenu}
        onMouseEnter={event => setCursor(event, 'grab')} onMouseLeave={event => setCursor(event, 'default')}
      />
      <Circle
        x={rotationControl.handle[0]} y={rotationControl.handle[1]}
        radius={ROTATION_HANDLE_RADIUS / scale}
        fill="#00d7aa" stroke="#052f27" strokeWidth={1.5 / scale}
        onMouseDown={beginRotation} onTouchStart={beginRotation}
        onContextMenu={onContextMenu}
        onMouseEnter={event => setCursor(event, 'grab')} onMouseLeave={event => setCursor(event, 'default')}
      />
    </Group>}
    {supportsEdgeReshape && points.map((point, index) => {
      const next = points[(index + 1) % points.length]
      const center = midpoint(point, next)
      const edgeLength = Math.hypot(next[0] - point[0], next[1] - point[1])
      const angle = Math.atan2(next[1] - point[1], next[0] - point[0]) * 180 / Math.PI
      const horizontalEdge = Math.abs(next[0] - point[0]) >= Math.abs(next[1] - point[1])
      const hitLength = Math.max(EDGE_HANDLE_LENGTH / scale, edgeLength - CORNER_SIZE * 1.5 / scale)
      return <Group key={`edge-${index}`}>
        <Rect
          x={center[0]} y={center[1]}
          width={hitLength} height={EDGE_HIT_THICKNESS / scale}
          offsetX={hitLength / 2} offsetY={EDGE_HIT_THICKNESS / scale / 2}
          rotation={angle} fill="rgba(0,215,170,.001)"
          draggable
          onContextMenu={onContextMenu}
          onDragStart={event => beginEdgeDrag(event, index)} onDragMove={moveEdge} onDragEnd={endEdgeDrag}
          onMouseEnter={event => setCursor(event, horizontalEdge ? 'ns-resize' : 'ew-resize')} onMouseLeave={event => setCursor(event, 'default')}
        />
        <Rect
          x={center[0]} y={center[1]}
          width={EDGE_HANDLE_LENGTH / scale} height={EDGE_HANDLE_THICKNESS / scale}
          offsetX={EDGE_HANDLE_LENGTH / scale / 2} offsetY={EDGE_HANDLE_THICKNESS / scale / 2}
          rotation={angle} cornerRadius={EDGE_HANDLE_THICKNESS / scale / 2}
          fill="#00d7aa" stroke="#052f27" strokeWidth={1 / scale} listening={false}
        />
      </Group>
    })}
    {supportsPolygonReshape && points.map((point, index) => <Rect
      key={`corner-${index}`} x={point[0]} y={point[1]}
      width={CORNER_SIZE / scale} height={CORNER_SIZE / scale}
      offsetX={CORNER_SIZE / scale / 2} offsetY={CORNER_SIZE / scale / 2}
      cornerRadius={CORNER_SIZE / scale / 2} fill="#f8fffc" stroke="#00b991" strokeWidth={1.5 / scale}
      draggable
      onContextMenu={onContextMenu}
      onDragStart={event => beginCornerDrag(event, index)} onDragMove={moveCorner} onDragEnd={endCornerDrag}
      onMouseEnter={event => setCursor(event, 'crosshair')} onMouseLeave={event => setCursor(event, 'default')}
    />)}
  </Group>
}
