import type {TextRegion} from '../../../types'

/** Koharu-style local rectangle, without coupling subsequent box edits to text rotation. */
export function alignedTextFrame(polygon: number[][], angle: number): number[] | null {
  if (!Number.isFinite(angle) || Math.abs(angle) <= .01 || polygon.length !== 4) return null
  if (polygon.some(point => point.length < 2 || !point.slice(0, 2).every(Number.isFinite))) return null
  const cx = polygon.reduce((sum, point) => sum + point[0], 0) / 4
  const cy = polygon.reduce((sum, point) => sum + point[1], 0) / 4
  const cosine = Math.cos(angle*Math.PI/180), sine = Math.sin(angle*Math.PI/180)
  const points = polygon.map(([x,y]) => [(x-cx)*cosine+(y-cy)*sine, -(x-cx)*sine+(y-cy)*cosine])
  const left = Math.min(...points.map(point => point[0])), right = Math.max(...points.map(point => point[0]))
  const top = Math.min(...points.map(point => point[1])), bottom = Math.max(...points.map(point => point[1]))
  const width = right-left, height = bottom-top
  if (Math.min(width,height) < 2) return null
  const tolerance = Math.max(width,height)*1e-6, corners = new Set<string>()
  for (const [x,y] of points) {
    if (Math.min(Math.abs(x-left),Math.abs(x-right)) > tolerance || Math.min(Math.abs(y-top),Math.abs(y-bottom)) > tolerance) return null
    corners.add(`${Math.abs(x-right) < Math.abs(x-left)}:${Math.abs(y-bottom) < Math.abs(y-top)}`)
  }
  if (corners.size !== 4) return null
  return [cx-width/2,cy-height/2,width,height].map(value => Math.round(value*1e6)/1e6)
}

export function translationTextFrame(region: TextRegion): number[] | null {
  if (region.perspective_warp) return null // Perspective already maps its own local tile.
  const polygon = region.translated_polygon?.length >= 3 ? region.translated_polygon : region.polygon
  return alignedTextFrame(polygon, region.rotation)
}
