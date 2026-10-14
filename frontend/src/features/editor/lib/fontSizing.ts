import type {TextRegion} from '../../../types'

export function usesAutomaticFontSize(region: TextRegion): boolean {
  if (typeof region.auto_font_size === 'boolean') return region.auto_font_size
  if (typeof region.layout_data?.auto_font_size === 'boolean') return region.layout_data.auto_font_size
  const snapshot = region.layout_data?.render_input as {font_size?: unknown} | undefined
  const previous = snapshot?.font_size ?? region.layout_data?.font_size
  return !(typeof previous === 'number' && Number.isFinite(previous) && Math.abs(previous - region.font_size) > .01)
}

export function hasCurrentComputedFont(region: TextRegion): boolean {
  if (region.layout_data?.render_output_version !== 6) return false
  const snapshot=region.layout_data.render_input as Record<string,unknown> | undefined
  const inputs=['bbox','polygon','translated_bbox','translated_polygon','translated_text','font_size','font_family',
    'font_weight','orientation','line_spacing','character_spacing','alignment','stroke_width','rotation','perspective_warp'] as const
  return !!snapshot && snapshot.auto_font_size === usesAutomaticFontSize(region) &&
    (snapshot.custom_font_path ?? null) === (region.layout_data.custom_font_path ?? null) &&
    inputs.every(field=>JSON.stringify(snapshot[field]) === JSON.stringify(region[field]))
}

export interface TextSizing {
  fontSize: number
  lines: string[]
  lineWidths: number[]
  width: number
  height: number
  coordinateWidth: number
  columnWidth: number
  cellHeight: number
  overflow: boolean
}

type Measure = (character: string, size: number) => number
let measurementContext: CanvasRenderingContext2D | null | undefined

/** Track user adjustments internally; the UI has one editable, computed size. */
export function sizeText(region: TextRegion, rect?: {width: number, height: number}, measure?: Measure): TextSizing {
  const box = region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox
  const polygon = region.translated_polygon?.length >= 3 ? region.translated_polygon : region.polygon
  const auto = usesAutomaticFontSize(region)
  const factor = !auto || rect ? 1 : polygonFactor(polygon, box)
  const padding = auto ? 2 + Math.max(0, region.stroke_width) : 0
  const width = Math.max(1, (rect?.width ?? box[2]) * factor - padding * 2)
  const height = Math.max(1, (rect?.height ?? box[3]) * factor - padding * 2)
  if (!measure && region.orientation !== 'vertical') {
    if (measurementContext === undefined) measurementContext = document.createElement('canvas').getContext('2d')
    const context = measurementContext
    measure = (character, size) => {
      if (!context) return size // non-browser/test fallback, no extra canvas per candidate
      context.font = `${region.font_weight >= 600 ? 'bold' : 'normal'} ${size}px "${region.font_family}"`
      return context.measureText(character).width
    }
  }
  const candidate = (fontSize: number): TextSizing => {
    const lines: string[] = []
    const lineWidths: number[] = []
    const cellHeight = Math.max(1, fontSize + region.character_spacing)
    const columnWidth = fontSize * region.line_spacing
    const vertical = region.orientation === 'vertical'
    const capacity = Math.max(1, Math.floor(Math.max(1, height + region.character_spacing) / cellHeight))
    const advances = new Map<string, number>()
    for (const paragraph of region.translated_text.replace(/\r/g, '').split('\n')) {
      const characters = [...paragraph]
      if (vertical) {
        if (!characters.length) lines.push('')
        for (let start = 0; start < characters.length; start += capacity) lines.push(characters.slice(start, start + capacity).join(''))
      } else {
        let line = '', length = 0, count = 0
        for (const character of characters) {
          let advance = advances.get(character)
          if (advance === undefined) {advance = measure!(character, fontSize); advances.set(character, advance)}
          const next = length + advance + (count ? region.character_spacing : 0)
          if (count && next > width) {lines.push(line); lineWidths.push(length); line = character; length = advance; count = 1}
          else {line += character; length = next; count += 1}
        }
        lines.push(line); lineWidths.push(length)
      }
    }
    const rows = Math.max(0, ...lines.map(line => [...line].length))
    const usedWidth = vertical ? Math.max(fontSize, (lines.length - 1) * columnWidth + fontSize) : Math.max(0, ...lineWidths)
    const usedHeight = vertical ? (rows ? (rows - 1) * cellHeight + fontSize : 0) : (lines.length - 1) * columnWidth + fontSize
    let overflow = usedWidth > width + .5 || usedHeight > height + .5 || (auto && ((rect?.width ?? box[2])*factor <= padding*2 || (rect?.height ?? box[3])*factor <= padding*2))
    if (auto && region.rotation && !overflow) {
      const frameWidth=rect?.width ?? box[2],frameHeight=rect?.height ?? box[3]
      const angle=region.rotation*Math.PI/180, cosine=Math.cos(angle),sine=Math.sin(angle)
      for (let index=0;index<lines.length;index++) {
        const lineWidth=vertical ? fontSize : lineWidths[index]
        const lineHeight=vertical ? Math.max(0,([...lines[index]].length-1)*cellHeight+fontSize) : fontSize
        const x=vertical ? (frameWidth-usedWidth)/2+usedWidth-fontSize-index*columnWidth
          : (frameWidth-width)/2+(region.alignment === 'left' ? 0 : region.alignment === 'right' ? width-lineWidth : (width-lineWidth)/2)
        const y=(frameHeight-usedHeight)/2+(vertical ? 0 : index*columnWidth)
        if ([[x,y],[x+lineWidth,y],[x+lineWidth,y+lineHeight],[x,y+lineHeight]].some(([px,py])=>{
          const dx=px-frameWidth/2,dy=py-frameHeight/2
          const rx=frameWidth/2+dx*cosine-dy*sine,ry=frameHeight/2+dx*sine+dy*cosine
          return rx<padding || rx>frameWidth-padding || ry<padding || ry>frameHeight-padding
        })) {overflow=true;break}
      }
    }
    return {fontSize, lines, lineWidths, width: usedWidth, height: usedHeight, coordinateWidth: width,columnWidth, cellHeight,overflow}
  }
  if (!auto) return candidate(Math.max(1, Math.min(512, Math.round(region.font_size))))
  if (hasCurrentComputedFont(region)) {
    const saved=candidate(Math.max(1,Math.min(512,Math.round(region.font_size))))
    if (!saved.overflow) return saved
  }
  const maximum = Math.max(1, Math.min(512, Math.floor(region.orientation === 'vertical' ? Math.min(width, height) : height)))
  let best = candidate(1)
  if (region.character_spacing < 0) {
    for (let size = maximum; size > 0; size -= 1) {const result = candidate(size); if (!result.overflow) return result}
  } else {
    let low = 1, high = maximum
    while (low <= high) {
      const size = Math.floor((low + high) / 2), result = candidate(size)
      if (result.overflow) high = size - 1
      else {best = result; low = size + 1}
    }
  }
  return best
}

function polygonFactor(polygon: number[][], bbox: number[]): number {
  if (polygon.length < 3 || bbox[2] <= 0 || bbox[3] <= 0) return 1
  let area = 0
  for (let index = 0; index < polygon.length; index += 1) {
    const current = polygon[index], next = polygon[(index + 1) % polygon.length]
    area += current[0] * next[1] - next[0] * current[1]
  }
  return Math.sqrt(Math.max(.2, Math.min(1, Math.abs(area) / 2 / (bbox[2] * bbox[3]))))
}
