import type {TextRegion} from '../../../types'

// Derive the preview from saved geometry. Never persist another copy of editor
// state, and immediately yield to user-authored target geometry or transforms.
export function shapePreviewRegion(region: TextRegion): TextRegion | null {
  const layout = region.layout_data
  if (!layout || (!layout.shape_layout_applied && !layout.render_skipped)) return region
  if (region.rotation || region.perspective_warp) return region
  const source = layout.shape_layout_source as {bbox?: unknown, polygon?: unknown} | null
  if (!source || JSON.stringify(source.bbox) !== JSON.stringify(region.bbox) ||
      JSON.stringify(source.polygon) !== JSON.stringify(region.polygon)) return region
  if ((region.translated_bbox?.length && JSON.stringify(region.translated_bbox) !== JSON.stringify(region.bbox)) ||
      (region.translated_polygon?.length && JSON.stringify(region.translated_polygon) !== JSON.stringify(region.polygon))) return region
  if (layout.render_skipped === true) return null
  const box = layout.effective_text_bbox
  if (!Array.isArray(box) || box.length !== 4 || !box.every(value => typeof value === 'number' && Number.isFinite(value)) ||
      box[2] <= 0 || box[3] <= 0) return region
  const [x, y, width, height] = box as number[]
  return {...region, translated_bbox: box, translated_polygon: [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]}
}
