export type ResizePoint = [number, number]

export interface ParallelEdgeResize {
  start: ResizePoint[]
  normal: ResizePoint
  velocities: ResizePoint[]
}

const EPSILON = 1e-6
const subtract = (first: ResizePoint, second: ResizePoint): ResizePoint => [first[0] - second[0], first[1] - second[1]]
const dot = (first: ResizePoint, second: ResizePoint) => first[0] * second[0] + first[1] * second[1]

/** Offset one edge's line, keeping its neighbours' original directions.
 * Collinear vertices share the same boundary rather than becoming new kinks.
 * Compute once at drag start; every preview uses this immutable baseline.
 */
export function createParallelEdgeResize(polygon: ResizePoint[], edgeIndex: number): ParallelEdgeResize | null {
  const count = polygon.length
  if (count < 3 || !Number.isInteger(edgeIndex) || edgeIndex < 0 || edgeIndex >= count ||
    polygon.some(point => !Number.isFinite(point[0]) || !Number.isFinite(point[1]))) return null
  const start = polygon.map(point => [...point] as ResizePoint)
  const edge = subtract(start[(edgeIndex + 1) % count], start[edgeIndex])
  const length = Math.hypot(...edge)
  if (length < EPSILON) return null
  const normal: ResizePoint = [-edge[1] / length, edge[0] / length]
  const alongEdge = (vector: ResizePoint) => Math.abs(dot(vector, normal)) <= EPSILON * Math.hypot(...vector) && dot(vector, edge) > 0
  let first = edgeIndex, last = (edgeIndex + 1) % count
  // Extend the moving boundary across redundant collinear polygon vertices.
  for (let visited = 0; visited < count - 2; visited++) {
    const previous = (first + count - 1) % count
    if (!alongEdge(subtract(start[first], start[previous]))) break
    first = previous
  }
  for (let visited = 0; visited < count - 2; visited++) {
    const following = (last + 1) % count
    if (following === first || !alongEdge(subtract(start[following], start[last]))) break
    last = following
  }
  const before = subtract(start[first], start[(first + count - 1) % count])
  const after = subtract(start[(last + 1) % count], start[last])
  const beforeProjection = dot(before, normal), afterProjection = dot(after, normal)
  if (Math.abs(beforeProjection) <= EPSILON * Math.hypot(...before) ||
    Math.abs(afterProjection) <= EPSILON * Math.hypot(...after)) return null
  const firstVelocity: ResizePoint = [before[0] / beforeProjection, before[1] / beforeProjection]
  const lastVelocity: ResizePoint = [after[0] / afterProjection, after[1] / afterProjection]
  const span = dot(subtract(start[last], start[first]), edge)
  if (span <= EPSILON) return null
  const velocities: ResizePoint[] = start.map(() => [0, 0])
  let index = first
  for (let visited = 0; visited < count; visited++) {
    const ratio = dot(subtract(start[index], start[first]), edge) / span
    velocities[index] = [
      firstVelocity[0] + (lastVelocity[0] - firstVelocity[0]) * ratio,
      firstVelocity[1] + (lastVelocity[1] - firstVelocity[1]) * ratio,
    ]
    if (index === last) break
    index = (index + 1) % count
  }
  return {start, normal, velocities}
}

export function resizeParallelEdge(drag: ParallelEdgeResize, delta: ResizePoint): ResizePoint[] {
  const distance = dot(delta, drag.normal)
  return drag.start.map((point, index) => [
    point[0] + distance * drag.velocities[index][0],
    point[1] + distance * drag.velocities[index][1],
  ])
}

/** Resize cursors follow the edge normal, including rotated rectangles. */
export function parallelEdgeCursor(first: ResizePoint, second: ResizePoint): string {
  const normalAngle = Math.atan2(second[0] - first[0], first[1] - second[1]) * 180 / Math.PI
  const sector = Math.round(((normalAngle % 180 + 180) % 180) / 45) % 4
  return ['ew-resize', 'nwse-resize', 'ns-resize', 'nesw-resize'][sector]
}
