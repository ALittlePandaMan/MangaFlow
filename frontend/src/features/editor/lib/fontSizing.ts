import type {TextRegion} from '../../../types'
import {PERSPECTIVE_RENDER_OUTPUT_VERSION} from './perspectiveText'
import {translationTextFrame} from './textFrame'
import {effectiveTextRotation} from './textRotation'
import {fitInk, type GlyphMetrics, type InkGlyph} from './inkLayout'
import {WORD_FIT_REFERENCE_SIZE, fittingChunkCount, wordCount} from './lineBreaking'

export function usesAutomaticFontSize(region: TextRegion): boolean {
  if (typeof region.auto_font_size === 'boolean') return region.auto_font_size
  if (typeof region.layout_data?.auto_font_size === 'boolean') return region.layout_data.auto_font_size
  const snapshot = region.layout_data?.render_input as {font_size?: unknown} | undefined
  const previous = snapshot?.font_size ?? region.layout_data?.font_size
  return !(typeof previous === 'number' && Number.isFinite(previous) && Math.abs(previous - region.font_size) > .01)
}

export function hasCurrentComputedFont(region: TextRegion): boolean {
  if (region.layout_data?.render_output_version !== PERSPECTIVE_RENDER_OUTPUT_VERSION) return false
  const snapshot=region.layout_data.render_input as Record<string,unknown> | undefined
  const inputs=['bbox','polygon','translated_bbox','translated_polygon','translated_text','font_size','font_family',
    'font_weight','orientation','line_spacing','character_spacing','alignment','stroke_width','rotation','perspective_warp'] as const
  return !!snapshot && snapshot.auto_font_size === usesAutomaticFontSize(region) &&
    JSON.stringify(snapshot.text_rotation ?? null) === JSON.stringify(region.layout_data.text_rotation ?? null) &&
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
  glyphs?: InkGlyph[]
}

type Measure = (character: string, size: number) => number | GlyphMetrics
let measurementContext: CanvasRenderingContext2D | null | undefined

/** Track user adjustments internally; the UI has one editable, computed size. */
export function sizeText(region: TextRegion, rect?: {width: number, height: number}, measure?: Measure): TextSizing {
  const textFrame = rect ? null : translationTextFrame(region)
  const box = textFrame ?? (region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox)
  const auto = usesAutomaticFontSize(region)
  const rotation = effectiveTextRotation(region)
  const padding = auto ? 1 + Math.ceil(Math.max(0, region.stroke_width)) : 0
  const width = Math.max(1, (rect?.width ?? box[2]) - padding * 2)
  const height = Math.max(1, (rect?.height ?? box[3]) - padding * 2)
  if (!measure) {
    if (measurementContext === undefined) measurementContext = document.createElement('canvas').getContext('2d')
    const context = measurementContext
    let measuredSize = 0
    measure = (character, size): GlyphMetrics | number => {
      if (!context) return size // non-browser/test fallback, no extra canvas per candidate
      if (measuredSize !== size) {
        context.font = `${region.font_weight >= 600 ? 'bold' : 'normal'} ${size}px "${region.font_family}"`
        context.textBaseline = 'alphabetic'
        context.textAlign = 'left'
        measuredSize = size
      }
      const ink = context.measureText(character)
      if (![ink.actualBoundingBoxLeft, ink.actualBoundingBoxRight, ink.actualBoundingBoxAscent, ink.actualBoundingBoxDescent].every(Number.isFinite)) return ink.width
      return {advance: ink.width, left: -ink.actualBoundingBoxLeft, right: ink.actualBoundingBoxRight,
        top: -ink.actualBoundingBoxAscent, bottom: ink.actualBoundingBoxDescent}
    }
  }
  const candidate = (fontSize: number): TextSizing => {
      const result = fitInk(region.translated_text, width, height, fontSize, region.orientation === 'vertical',
        region.line_spacing, region.character_spacing, region.alignment, character => {
          const ink = measure!(character, fontSize)
          return typeof ink === 'number' ? {advance: ink, left: 0, top: -fontSize, right: ink, bottom: 0} : ink
        })
      if (auto && ((rect?.width ?? box[2]) <= padding*2 || (rect?.height ?? box[3]) <= padding*2)) result.overflow = true
      if (auto && rotation && !textFrame && !result.overflow) {
        const frameWidth = rect?.width ?? box[2], frameHeight = rect?.height ?? box[3]
        const angle = rotation*Math.PI/180, cosine = Math.cos(angle), sine = Math.sin(angle)
        for (const glyph of result.glyphs) {
          const x = (frameWidth-result.coordinateWidth)/2+glyph.x, y = (frameHeight-result.height)/2+glyph.y
          const w = glyph.rotate ? glyph.bottom-glyph.top : glyph.right-glyph.left
          const h = glyph.rotate ? glyph.right-glyph.left : glyph.bottom-glyph.top
          if ([[x,y],[x+w,y],[x+w,y+h],[x,y+h]].some(([px,py]) => {
            const dx = px-frameWidth/2, dy = py-frameHeight/2
            const rx = frameWidth/2+dx*cosine-dy*sine, ry = frameHeight/2+dx*sine+dy*cosine
            return rx < padding || rx > frameWidth-padding || ry < padding || ry > frameHeight-padding
          })) {result.overflow = true; break}
        }
      }
      return result
  }
  if (!auto) return candidate(Math.max(1, Math.min(512, Math.round(region.font_size))))
  if (hasCurrentComputedFont(region)) {
    const saved=candidate(Math.max(1,Math.min(512,Math.round(region.font_size))))
    if (!saved.overflow) return saved
  }
  const maximum = 512
  let best = candidate(1)
  const words = region.orientation === 'vertical' ? 0 : wordCount(region.translated_text)
  const referenceLines = words ? candidate(WORD_FIT_REFERENCE_SIZE).lines.join('\n') : ''
  const preserveWords = words > 0 && wordCount(referenceLines) <= words
  const chunks = words ? fittingChunkCount(region.translated_text) : 0
  const preserveChunks = words > 0 && fittingChunkCount(referenceLines) <= chunks
  const fits = (result: TextSizing) => !result.overflow && (!preserveWords || wordCount(result.lines.join('\n')) <= words)
    && (!preserveChunks || fittingChunkCount(result.lines.join('\n')) <= chunks)
  if (region.character_spacing < 0 || (rotation && !textFrame)) {
    for (let size = maximum; size > 0; size -= 1) {const result = candidate(size); if (fits(result)) return result}
  } else {
    let low = 1, high = maximum
    while (low <= high) {
      const size = Math.floor((low + high) / 2), result = candidate(size)
      if (!fits(result)) high = size - 1
      else {best = result; low = size + 1}
    }
  }
  return best
}
