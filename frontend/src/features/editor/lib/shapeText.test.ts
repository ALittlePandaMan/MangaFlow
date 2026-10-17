import {describe, expect, it} from 'vitest'
import type {TextRegion} from '../../../types'
import {shapePreviewRegion} from './shapeText'

const bbox = [0, 0, 100, 150]
const polygon = [[0, 0], [100, 0], [100, 150], [0, 150]]
function region(): TextRegion {
  return {id: 'r1', image_id: 'p1', region_key: 'R001', mask_url: null, source_text: '原文', translated_text: '译文',
    confidence: .9, orientation: 'vertical', reading_order: 1, panel_id: null, bubble_id: null, region_type: 'background_complex',
    font_size: 20, font_family: 'sans-serif', font_weight: 400, text_color: '#111111', stroke_color: '#ffffff', stroke_width: 1,
    alignment: 'center', line_spacing: 1.15, character_spacing: 0, opacity: 1, visible: true, needs_review: false,
    review_reasons: [], layout_warning: false, updated_at: '',
    bbox, polygon, translated_bbox: bbox, translated_polygon: polygon, rotation: 0, locked: false, perspective_warp: false,
    layout_data: {shape_layout_applied: true, effective_text_bbox: [20, 15, 60, 120], shape_layout_source: {bbox, polygon}}}
}

describe('visible-frame text preview', () => {
  it('ignores old hidden safe bounds without mutating stored geometry', () => {
    const original = region()
    expect(shapePreviewRegion(original)?.translated_bbox).toEqual(bbox)
    expect(original.translated_bbox).toBe(bbox)
  })
  it.each([{rotation: 10}, {perspective_warp: true}, {bbox: [1, 0, 100, 150]},
    {translated_bbox: [2, 2, 80, 130]}, {translated_polygon: [[1, 1], [90, 2], [80, 140]]}])('respects manual changes: %j', patch => {
    const original = {...region(), ...patch}
    expect(shapePreviewRegion(original)).toBe(original)
  })
  it('locking preserves the current safe box', () => {
    const original = region()
    expect(shapePreviewRegion({...original, locked: true})?.translated_bbox).toEqual(shapePreviewRegion(original)?.translated_bbox)
  })
  it('does not hide text because an old bubble mask had no safe area', () => {
    const original = region()
    original.layout_data = {...original.layout_data, shape_layout_applied: false, render_skipped: true}
    expect(shapePreviewRegion(original)).toBe(original)
    expect(shapePreviewRegion({...original, rotation: 5})).not.toBeNull()
  })
  it('ignores stale or malformed layout metadata', () => {
    const original = region()
    original.layout_data = {...original.layout_data, effective_text_bbox: [0, 0, NaN, 10]}
    expect(shapePreviewRegion(original)).toBe(original)
    original.layout_data = {shape_layout_applied: true}
    expect(shapePreviewRegion(original)).toBe(original)
  })
})
