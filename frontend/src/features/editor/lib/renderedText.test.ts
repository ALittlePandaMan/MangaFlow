import {describe, expect, it} from 'vitest'
import type {ImagePage, TextRegion} from '../../../types'
import {currentTextLayer} from './renderedText'

function fixture() {
  const region = {id: 'r1', bbox: [0, 0, 100, 100], polygon: [[0, 0], [100, 0], [100, 100]],
    translated_bbox: [0, 0, 100, 100], translated_polygon: [[0, 0], [100, 0], [100, 100]],
    translated_text: 'ABC!?', orientation: 'vertical', font_size: 24, font_family: 'sans-serif',
    font_weight: 400, text_color: '#111111', stroke_color: '#ffffff', stroke_width: 1,
    alignment: 'center', line_spacing: 1.15, character_spacing: 0, rotation: 0,
    perspective_warp: false, opacity: 1, visible: true, locked: false, reading_order: 1,
    layout_data: {}} as TextRegion
  region.layout_data = {render_input: {...region, custom_font_path: null}}
  const page = {text_layer_url: '/text.png?v=1', text_layer_region_ids: ['r1']} as ImagePage
  return {region, page}
}

describe('export-identical text layer selection', () => {
  it('uses the layer only for matching visual inputs and region set', () => {
    const {region, page} = fixture()
    expect(currentTextLayer(page, [region])).toBe('/text.png?v=1')
    expect(currentTextLayer(page, [{...region, locked: true, needs_review: true}])).toBe('/text.png?v=1')
    expect(currentTextLayer(page, [])).toBeNull()
    expect(currentTextLayer(page, [{...region, id: 'new'}])).toBeNull()
    expect(currentTextLayer({...page, text_layer_region_ids: undefined}, [region])).toBeNull()
  })
  it.each([{translated_text: 'new'}, {font_size: 30}, {stroke_width: 3}, {rotation: 5},
    {visible: false}, {reading_order: 2}, {translated_bbox: [10, 10, 80, 80]}, {orientation: 'horizontal' as const}])('invalidates optimistic edits immediately: %j', patch => {
    const {region, page} = fixture()
    expect(currentTextLayer(page, [{...region, ...patch}])).toBeNull()
  })
  it('invalidates font replacement, missing snapshots and removed artifacts', () => {
    const {region, page} = fixture()
    expect(currentTextLayer(page, [{...region, layout_data: {...region.layout_data, custom_font_path: 'new.ttf'}}])).toBeNull()
    expect(currentTextLayer(page, [{...region, layout_data: {}}])).toBeNull()
    expect(currentTextLayer({...page, text_layer_url: null}, [region])).toBeNull()
  })
})
