import type {TextRegion} from '../../../types'

export function translatedTextBox(region: TextRegion): number[] {
  return region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox
}

/** Move only the translation; source detection and repair geometry stay intact. */
export function translateText(region: TextRegion, dx: number, dy: number): Partial<TextRegion> {
  const [x,y,width,height] = translatedTextBox(region)
  const polygon = region.translated_polygon?.length >= 3 ? region.translated_polygon
    : [[x,y],[x+width,y],[x+width,y+height],[x,y+height]]
  return {
    translated_bbox:[x+dx,y+dy,width,height],
    translated_polygon:polygon.map(([px,py]) => [px+dx,py+dy]),
  }
}
