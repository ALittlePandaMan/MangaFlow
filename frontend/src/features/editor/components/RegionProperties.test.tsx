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

  it('shows a computed editable font size without mode controls and preserves user adjustments', () => {
    const onUpdate=renderProperties()
    expect(screen.queryByRole('button',{name:'字号模式'})).not.toBeInTheDocument()
    expect(screen.queryByText('自动适配所选字号')).not.toBeInTheDocument()
    const size=screen.getByRole('textbox',{name:'字号'}) as HTMLInputElement
    const computed=Number(size.value)
    expect(computed).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button',{name:'字号增大'}))
    expect(onUpdate).toHaveBeenCalledWith(region.id,{font_size:computed+1,auto_font_size:false})
  })

  it('shows the exact user font size even when it exceeds the frame', () => {
    const adjusted={...region,font_size:120,auto_font_size:false}
    render(<RegionProperties region={adjusted} selectedRegions={[adjusted]} selectedCount={1} fontOptions={[]} onUpdate={vi.fn()} onAction={vi.fn()}/>)
    expect(screen.getByRole('textbox',{name:'字号'})).toHaveValue('120')
  })

  it('batches text alignment as one edit without changing boxes',() => {
    const second={...region,id:'region-2',alignment:'right' as const}
    const onUpdateMany=vi.fn(),onUpdate=vi.fn()
    render(<RegionProperties selectedRegions={[region,second]} selectedCount={2} fontOptions={[]} onUpdate={onUpdate} onUpdateMany={onUpdateMany} onAction={vi.fn()}/>)
    fireEvent.click(screen.getByRole('button',{name:'所选文字左对齐'}))
    expect(onUpdateMany).toHaveBeenCalledWith([{id:region.id,patch:{alignment:'left'}},{id:second.id,patch:{alignment:'left'}}])
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it('aligns only translated boxes in translated view in one batch',() => {
    useEditorStore.setState({view:'translated'})
    const second={...region,id:'region-2',translated_bbox:[40,10,80,40],translated_polygon:[[40,10],[120,10],[120,50],[40,50]]}
    const onUpdateMany=vi.fn()
    render(<RegionProperties selectedRegions={[region,second]} selectedCount={2} fontOptions={[]} onUpdate={vi.fn()} onUpdateMany={onUpdateMany} onAction={vi.fn()}/>)
    fireEvent.click(screen.getByRole('button',{name:'所选文本框靠左'}))
    expect(onUpdateMany).toHaveBeenCalledWith([{id:second.id,patch:{translated_bbox:region.translated_bbox,translated_polygon:region.translated_polygon}}])
  })

  it('does not display the internal OCR confidence', () => {
    renderProperties()

    expect(screen.queryByText('99%')).not.toBeInTheDocument()
  })

  it('groups direction with alignment and translated rotation without repair or canvas hints', () => {
    const onUpdate = renderProperties()
    const styles = screen.getByRole('heading', {name: '文字样式'}).closest('section')!
    const content = screen.getByRole('heading', {name: '文本内容'}).closest('section')!
    const direction = within(styles).getByRole('button', {name: '文字方向'})
    const row = direction.closest('label')!.parentElement!

    expect(within(content).queryByRole('button', {name: '文字方向'})).not.toBeInTheDocument()
    expect(within(row).getByRole('button', {name: '文字对齐'})).toBeInTheDocument()
    expect(within(row).getByRole('textbox', {name: '译文旋转角度'})).toBeInTheDocument()
    expect(screen.queryByText(/背景修复/)).not.toBeInTheDocument()
    expect(screen.queryByText(/画布像素/)).not.toBeInTheDocument()
    expect(screen.queryByText(/方向键微调/)).not.toBeInTheDocument()

    fireEvent.click(direction)
    fireEvent.click(screen.getByRole('option', {name: '竖排'}))
    expect(onUpdate).toHaveBeenCalledWith(region.id, {orientation: 'vertical'})
  })

  it('warns that changing the source frame requires OCR rather than implying repair recognizes text', () => {
    const stale = {...region,layout_data:{ocr_geometry_stale:true}}
    render(<RegionProperties region={stale} selectedRegions={[stale]} selectedCount={1} fontOptions={[]} onUpdate={vi.fn()} onAction={vi.fn()}/>)
    expect(screen.getByRole('note')).toHaveTextContent('请按新框重新 OCR')
    expect(screen.getByRole('note')).toHaveTextContent('修复只同步框的位置，不会重新识字')
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

    fireEvent.click(screen.getByText('位置与旋转 · 原文框坐标'))
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

    fireEvent.click(screen.getByText('位置与旋转 · 译文框坐标'))
    fireEvent.click(screen.getByRole('button', {name: '译文框旋转角度（度）增大'}))

    expect(onUpdate).toHaveBeenCalledOnce()
    const [, patch] = onUpdate.mock.calls[0] as [string, Partial<TextRegion>]
    expect(patch.rotation).toBe(13)
    expect(patch.translated_polygon).not.toEqual(region.translated_polygon)
    expect(patch.translated_bbox).toHaveLength(4)
  })
})
