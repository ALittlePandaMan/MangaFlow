export type PerspectivePoint = [number, number]

export interface WarpedCanvas {
  image: HTMLCanvasElement
  x: number
  y: number
  width: number
  height: number
}

/** Return a clockwise convex quad while preserving the persisted semantic first corner. */
export function orderPerspectiveQuad(points: number[][]): PerspectivePoint[] | null {
  if (points.length !== 4 || points.some(point => point.length < 2 || !Number.isFinite(point[0]) || !Number.isFinite(point[1]))) return null
  const normalized = points.map(point => [point[0], point[1]] as PerspectivePoint)
  if (new Set(normalized.map(point => `${point[0]}:${point[1]}`)).size !== 4) return null
  const crosses = (quad: PerspectivePoint[]) => quad.map((current, index) => {
    const previous = quad[(index + 3) % 4]
    const following = quad[(index + 1) % 4]
    return (current[0] - previous[0]) * (following[1] - current[1]) - (current[1] - previous[1]) * (following[0] - current[0])
  })
  const suppliedCrosses = crosses(normalized)
  let ordered: PerspectivePoint[]
  if (Math.min(...suppliedCrosses) > 1e-6) {
    // Preserve the semantic corner indices persisted by RegionPolygonEditor.
    ordered = normalized
  } else if (Math.max(...suppliedCrosses) < -1e-6) {
    ordered = [normalized[0], normalized[3], normalized[2], normalized[1]]
  } else {
    const centerX = normalized.reduce((sum, point) => sum + point[0], 0) / 4
    const centerY = normalized.reduce((sum, point) => sum + point[1], 0) / 4
    ordered = [...normalized].sort((first, second) => Math.atan2(first[1] - centerY, first[0] - centerX) - Math.atan2(second[1] - centerY, second[0] - centerX))
    const start = ordered.reduce((best, point, index) => {
      const candidate = [point[0] + point[1], point[1], point[0]]
      const current = [ordered[best][0] + ordered[best][1], ordered[best][1], ordered[best][0]]
      return candidate[0] < current[0] || (candidate[0] === current[0] && (candidate[1] < current[1] || (candidate[1] === current[1] && candidate[2] < current[2]))) ? index : best
    }, 0)
    ordered = [...ordered.slice(start), ...ordered.slice(0, start)]
  }
  const crossValues = crosses(ordered)
  if (Math.min(...crossValues) <= 1e-6) return null
  const edgeLengths = ordered.map((point, index) => Math.hypot(ordered[(index + 1) % 4][0] - point[0], ordered[(index + 1) % 4][1] - point[1]))
  if (Math.min(...edgeLengths) < 2) return null
  const area = Math.abs(ordered.reduce((sum, point, index) => sum + point[0] * ordered[(index + 1) % 4][1] - ordered[(index + 1) % 4][0] * point[1], 0)) / 2
  const xs = ordered.map(point => point[0])
  const ys = ordered.map(point => point[1])
  const bboxArea = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys))
  const normalizedCrosses = crossValues.map((cross, index) => cross / (edgeLengths[(index + 3) % 4] * edgeLengths[index]))
  if (area < 4 || bboxArea <= 0 || area / bboxArea < .01 || Math.min(...normalizedCrosses) < 1e-3) return null
  return ordered
}

export function isPerspectiveQuad(points: number[][]): boolean {
  return orderPerspectiveQuad(points) !== null
}

export function perspectiveQuadSize(points: number[][]): {width: number, height: number} | null {
  const quad = orderPerspectiveQuad(points)
  if (!quad || !isPerspectiveQuad(quad)) return null
  const distance = (start: PerspectivePoint, end: PerspectivePoint) => Math.hypot(end[0] - start[0], end[1] - start[1])
  return {
    width: Math.max(2, distance(quad[0], quad[1]), distance(quad[3], quad[2])),
    height: Math.max(2, distance(quad[0], quad[3]), distance(quad[1], quad[2])),
  }
}

/**
 * Project a raster text tile into a convex quadrilateral. Canvas 2D has no
 * projective transform, so the homography is sampled as a small triangle mesh.
 * The same unit-square-to-quad equation can also be used by the export renderer.
 */
export function warpCanvasToQuad(source: HTMLCanvasElement, points: number[][], pixelRatio = 1): WarpedCanvas | null {
  const quad = orderPerspectiveQuad(points)
  if (!quad || !isPerspectiveQuad(quad) || source.width < 1 || source.height < 1) return null
  const xs = quad.map(point => point[0])
  const ys = quad.map(point => point[1])
  const x = Math.min(...xs)
  const y = Math.min(...ys)
  const width = Math.max(1, Math.max(...xs) - x)
  const height = Math.max(1, Math.max(...ys) - y)
  const ratio = Math.max(.25, pixelRatio)
  const output = document.createElement('canvas')
  output.width = Math.max(1, Math.ceil(width * ratio))
  output.height = Math.max(1, Math.ceil(height * ratio))
  const context = output.getContext('2d')
  if (!context) return null
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'

  const localQuad = quad.map(point => [(point[0] - x) * ratio, (point[1] - y) * ratio] as PerspectivePoint)
  const projection = squareToQuad(localQuad)
  if (!projection) return null
  const longestRasterSide = Math.max(width, height) * ratio
  // Keep each affine patch small in physical pixels. The previous fixed
  // twelve-patch ceiling stretched large glyph fragments and softened their
  // edges, especially on zoomed or high-DPI canvases.
  const divisions = Math.max(6, Math.min(24, Math.ceil(longestRasterSide / 64)))

  // Triangle expansion hides sub-pixel mesh seams, while this outer clip
  // prevents boundary triangles from bleeding beyond the selected region.
  context.save()
  context.beginPath()
  context.moveTo(localQuad[0][0], localQuad[0][1])
  for (const point of localQuad.slice(1)) context.lineTo(point[0], point[1])
  context.closePath()
  context.clip()
  for (let row = 0; row < divisions; row += 1) {
    const v0 = row / divisions
    const v1 = (row + 1) / divisions
    const sy0 = source.height * v0
    const sy1 = source.height * v1
    for (let column = 0; column < divisions; column += 1) {
      const u0 = column / divisions
      const u1 = (column + 1) / divisions
      const sx0 = source.width * u0
      const sx1 = source.width * u1
      const topLeft = projection(u0, v0)
      const topRight = projection(u1, v0)
      const bottomRight = projection(u1, v1)
      const bottomLeft = projection(u0, v1)
      drawTriangle(context, source, [[sx0, sy0], [sx1, sy0], [sx1, sy1]], [topLeft, topRight, bottomRight])
      drawTriangle(context, source, [[sx0, sy0], [sx1, sy1], [sx0, sy1]], [topLeft, bottomRight, bottomLeft])
    }
  }
  context.restore()
  return {image: output, x, y, width, height}
}

function squareToQuad(quad: PerspectivePoint[]): ((u: number, v: number) => PerspectivePoint) | null {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = quad
  const dx1 = x1 - x2
  const dx2 = x3 - x2
  const dx3 = x0 - x1 + x2 - x3
  const dy1 = y1 - y2
  const dy2 = y3 - y2
  const dy3 = y0 - y1 + y2 - y3
  let a11: number, a12: number, a21: number, a22: number, a31: number, a32: number
  if (Math.abs(dx3) < 1e-7 && Math.abs(dy3) < 1e-7) {
    a11 = x1 - x0
    a12 = x3 - x0
    a21 = y1 - y0
    a22 = y3 - y0
    a31 = 0
    a32 = 0
  } else {
    const determinant = dx1 * dy2 - dx2 * dy1
    if (Math.abs(determinant) < 1e-7) return null
    a31 = (dx3 * dy2 - dx2 * dy3) / determinant
    a32 = (dx1 * dy3 - dx3 * dy1) / determinant
    a11 = x1 - x0 + a31 * x1
    a12 = x3 - x0 + a32 * x3
    a21 = y1 - y0 + a31 * y1
    a22 = y3 - y0 + a32 * y3
  }
  return (u, v) => {
    const denominator = a31 * u + a32 * v + 1
    return [(a11 * u + a12 * v + x0) / denominator, (a21 * u + a22 * v + y0) / denominator]
  }
}

function drawTriangle(context: CanvasRenderingContext2D, source: HTMLCanvasElement, sourcePoints: number[][], destinationPoints: PerspectivePoint[]) {
  const matrix = affineFromTriangles(sourcePoints, destinationPoints)
  if (!matrix) return
  const [a, b, c, d, e, f] = matrix
  const expanded = expandTriangle(destinationPoints, .65)
  context.save()
  context.beginPath()
  context.moveTo(expanded[0][0], expanded[0][1])
  context.lineTo(expanded[1][0], expanded[1][1])
  context.lineTo(expanded[2][0], expanded[2][1])
  context.closePath()
  context.clip()
  context.setTransform(a, b, c, d, e, f)
  context.drawImage(source, 0, 0)
  context.restore()
}

function affineFromTriangles(source: number[][], destination: PerspectivePoint[]): [number, number, number, number, number, number] | null {
  const [[x0, y0], [x1, y1], [x2, y2]] = source
  const [[targetX0, targetY0], [targetX1, targetY1], [targetX2, targetY2]] = destination
  const determinant = x0 * (y1 - y2) + x1 * (y2 - y0) + x2 * (y0 - y1)
  if (Math.abs(determinant) < 1e-7) return null
  const a = (targetX0 * (y1 - y2) + targetX1 * (y2 - y0) + targetX2 * (y0 - y1)) / determinant
  const c = (targetX0 * (x2 - x1) + targetX1 * (x0 - x2) + targetX2 * (x1 - x0)) / determinant
  const e = (targetX0 * (x1 * y2 - x2 * y1) + targetX1 * (x2 * y0 - x0 * y2) + targetX2 * (x0 * y1 - x1 * y0)) / determinant
  const b = (targetY0 * (y1 - y2) + targetY1 * (y2 - y0) + targetY2 * (y0 - y1)) / determinant
  const d = (targetY0 * (x2 - x1) + targetY1 * (x0 - x2) + targetY2 * (x1 - x0)) / determinant
  const f = (targetY0 * (x1 * y2 - x2 * y1) + targetY1 * (x2 * y0 - x0 * y2) + targetY2 * (x0 * y1 - x1 * y0)) / determinant
  return [a, b, c, d, e, f]
}

function expandTriangle(points: PerspectivePoint[], amount: number): PerspectivePoint[] {
  const centerX = (points[0][0] + points[1][0] + points[2][0]) / 3
  const centerY = (points[0][1] + points[1][1] + points[2][1]) / 3
  return points.map(point => {
    const dx = point[0] - centerX
    const dy = point[1] - centerY
    const length = Math.max(.001, Math.hypot(dx, dy))
    return [point[0] + dx / length * amount, point[1] + dy / length * amount]
  })
}
