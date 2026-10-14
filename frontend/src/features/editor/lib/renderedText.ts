import type {ImagePage, TextRegion} from '../../../types'
import {PERSPECTIVE_RENDER_OUTPUT_VERSION} from './perspectiveText'
import {usesAutomaticFontSize} from './fontSizing'

const visualFields = ['bbox', 'polygon', 'translated_bbox', 'translated_polygon', 'translated_text',
  'orientation', 'font_size', 'font_family', 'font_weight', 'text_color', 'stroke_color', 'stroke_width',
  'alignment', 'line_spacing', 'character_spacing', 'rotation', 'perspective_warp', 'opacity', 'visible', 'reading_order'] as const

export function currentTextLayer(page: ImagePage, regions: TextRegion[]): string | null {
  const ids = page.text_layer_region_ids
  if (!page.text_layer_url || !ids || ids.length !== regions.length) return null
  const expected = new Set(ids)
  if (expected.size !== ids.length) return null
  for (const region of regions) {
    if (!expected.delete(region.id)) return null
    // Old text layers can match all inputs yet contain the incorrect corner mapping.
    if (region.visible && region.translated_text.trim() && region.layout_data.render_output_version !== PERSPECTIVE_RENDER_OUTPUT_VERSION) return null
    const snapshot = region.layout_data.render_input as Record<string, unknown> | undefined
    if (!snapshot || visualFields.some(field => JSON.stringify(snapshot[field]) !== JSON.stringify(region[field]))) return null
    if (snapshot.auto_font_size !== usesAutomaticFontSize(region)) return null
    if ((snapshot.custom_font_path ?? null) !== (region.layout_data.custom_font_path ?? null)) return null
  }
  return expected.size === 0 ? page.text_layer_url : null
}
