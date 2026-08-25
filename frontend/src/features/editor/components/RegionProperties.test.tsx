import {fireEvent, render, screen, within} from '@testing-library/react'
import {beforeEach, describe, expect, it, vi} from 'vitest'

import type {TextRegion} from '../../../types'
import {useEditorStore} from '../store'
import {RegionProperties} from './RegionProperties'

const region: TextRegion = {
  id: 'region-1',
  image_id: 'image-1',
  region_key: 'R001',
  polygon: [[0, 0], [100, 0], [100, 50], [0, 50]],
  bbox: [0, 0, 100, 50],
  translated_polygon: [[10, 10], [90, 10], [90, 50], [10, 50]],
  translated_bbox: [10, 10, 80, 40],
  mask_url: null,
  source_text: 'source',
  translated_text: 'translated',
  confidence: .99,
  orientation: 'horizontal',
  reading_order: 1,
  panel_id: null,
  bubble_id: null,
  region_type: 'bubble_simple',
  font_size: 24,
  font_family: 'sans-serif',
  font_weight: 400,
  text_color: '#111111',
  stroke_color: '#ffffff',
  stroke_width: 0,
  alignment: 'center',
  line_spacing: 1.2,
  character_spacing: 0,
  rotation: 12,
  perspective_warp: false,
  opacity: 1,
  locked: false,
  visible: true,
  needs_review: false,
  review_reasons: [],
  layout_warning: false,
  layout_data: {},
  updated_at: '2026-08-24T00:00:00Z',
}

function renderProperties(onUpdate = vi.fn()) {
  render(<RegionProperties
    region={region}
    selectedRegions={[region]}
    selectedCount={1}
    fontOptions={[]}
    onUpdate={onUpdate}
    onAction={vi.fn()}
  />)
  return onUpdate
}

describe('RegionProperties coordinate rotation', () => {
  beforeEach(() => useEditorStore.setState({view: 'original'}))

  it('does not display the internal OCR confidence', () => {
    renderProperties()

    expect(screen.queryByText('99%')).not.toBeInTheDocument()
  })

  it('uses the same 48px header height as the page list', () => {
    renderProperties()

    expect(screen.getByText(region.region_key).closest('section')).toHaveClass('h-12')
  })

  it('shows color swatches without inline hex text and still updates both colors', () => {
    const onUpdate = renderProperties()
    const textColor = screen.getByRole('button', {name: `文字颜色，当前 ${region.text_color.toUpperCase()}`})
    const strokeColor = screen.getByRole('button', {name: `描边颜色，当前 ${region.stroke_color.toUpperCase()}`})

    expect(textColor.querySelector('span[style]')).toHaveStyle({background: region.text_color})
    expect(strokeColor.querySelector('span[style]')).toHaveStyle({background: region.stroke_color})
    expect(within(textColor).queryByText(region.text_color.toUpperCase())).not.toBeInTheDocument()
    expect(within(strokeColor).queryByText(region.stroke_color.toUpperCase())).not.toBeInTheDocument()

    fireEvent.click(textColor)
    fireEvent.click(within(screen.getByRole('dialog', {name: '文字颜色'})).getByRole('button', {name: '#ff6258'}))
    fireEvent.click(textColor)
    fireEvent.click(strokeColor)
    fireEvent.click(within(screen.getByRole('dialog', {name: '描边颜色'})).getByRole('button', {name: '#3a9cff'}))

    expect(onUpdate).toHaveBeenNthCalledWith(1, region.id, {text_color: '#ff6258'})
    expect(onUpdate).toHaveBeenNthCalledWith(2, region.id, {stroke_color: '#3a9cff'})
  })

  it('rotates the source polygon without changing translated text rotation', () => {
    const onUpdate = renderProperties()

    fireEvent.click(screen.getByRole('button', {name: '原文框旋转角度（度）增大'}))

    expect(onUpdate).toHaveBeenCalledOnce()
    const [id, patch] = onUpdate.mock.calls[0] as [string, Partial<TextRegion>]
    expect(id).toBe(region.id)
    expect(patch).not.toHaveProperty('rotation')
    expect(patch.polygon).not.toEqual(region.polygon)
    expect(patch.bbox).toHaveLength(4)
  })

  it('rotates translated geometry and keeps translated text aligned', () => {
    useEditorStore.setState({view: 'translated'})
    const onUpdate = renderProperties()

    fireEvent.click(screen.getByRole('button', {name: '译文框旋转角度（度）增大'}))

    expect(onUpdate).toHaveBeenCalledOnce()
    const [, patch] = onUpdate.mock.calls[0] as [string, Partial<TextRegion>]
    expect(patch.rotation).toBe(13)
    expect(patch.translated_polygon).not.toEqual(region.translated_polygon)
    expect(patch.translated_bbox).toHaveLength(4)
  })
})
