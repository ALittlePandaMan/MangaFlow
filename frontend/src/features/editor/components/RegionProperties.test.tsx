import {act, fireEvent, render, screen, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'

import type {ImagePage, TextRegion} from '../../../types'
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

const page: ImagePage = {
  id:'image-1', project_id:'project-1', filename:'page.png', width:1200, height:1800,
  order_index:0, status:'COMPLETED', current_stage:null, error_message:null, ocr_exempt:false,
  original_url:'/media/page.png', clean_url:'/media/clean.png', rendered_url:null, text_layer_url:null,
  created_at:'2026-08-24T00:00:00Z', updated_at:'2026-08-24T00:00:00Z',
}
const layerRegions: TextRegion[] = [region,
  {...region,id:'region-2',region_key:'R002',translated_text:'second translation'},
  {...region,id:'region-3',region_key:'R003',translated_text:'',source_text:'third source'},
]

function LayerInspectorHarness({onUpdate,onAction,busyAction}: {onUpdate:ReturnType<typeof vi.fn>;onAction:ReturnType<typeof vi.fn>;busyAction?:string}) {
  const selectedIds=useEditorStore(state => state.selectedIds)
  const selectedRegions=layerRegions.filter(item => selectedIds.includes(item.id))
  return <RegionProperties page={page} regions={layerRegions} selectedRegions={selectedRegions} selectedCount={selectedRegions.length} fontOptions={[]} onUpdate={onUpdate} onAction={onAction} busyAction={busyAction}/>
}

describe('RegionProperties inspector', () => {
  beforeEach(() => useEditorStore.setState({view: 'original', selectedIds: [], layers: {original:true, detection:true, clean:false, translated:true, mask:false}}))

  it('allows selecting layers but not mutating their fields during background inference', () => {
    const update=vi.fn()
    render(<RegionProperties page={page} regions={layerRegions} region={region} selectedRegions={[region]} selectedCount={1}
      fontOptions={[]} readOnly onUpdate={update} onAction={vi.fn()}/>)
    const select=screen.getByRole('button',{name:/选择 R002/})
    expect(select).toBeEnabled()
    fireEvent.click(select)
    expect(useEditorStore.getState().selectedIds).toEqual(['region-2'])
    expect(screen.getByRole('button',{name:'锁定区域'})).toBeDisabled()
    expect(screen.getByPlaceholderText('OCR 原文')).toBeDisabled()
    expect(update).not.toHaveBeenCalled()
  })

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

  it('decreases mixed font sizes as one batch while clamping each region independently', () => {
    const targets=[{...region,font_size:1,auto_font_size:false},{...region,id:'region-2',font_size:40,auto_font_size:false}]
    const onUpdateMany=vi.fn(), onUpdate=vi.fn()
    render(<RegionProperties selectedRegions={targets} selectedCount={2} fontOptions={[]} onUpdate={onUpdate} onUpdateMany={onUpdateMany} onAction={vi.fn()}/>)
    expect(screen.getByRole('textbox',{name:'批量字号'})).toHaveValue('')
    fireEvent.click(screen.getByRole('button',{name:'批量字号减小'}))
    expect(onUpdateMany).toHaveBeenCalledExactlyOnceWith([
      {id:region.id,patch:{font_size:1,auto_font_size:false}},
      {id:'region-2',patch:{font_size:39,auto_font_size:false}},
    ])
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it.each([null,'region-2'])('commits the old selection before canvas mousedown selects %s',async target => {
    const user=userEvent.setup(), onUpdate=vi.fn()
    useEditorStore.getState().select(region.id)
    render(<><LayerInspectorHarness onUpdate={onUpdate} onAction={vi.fn()}/><canvas data-testid="canvas" onMouseDown={() => useEditorStore.getState().select(target)}/></>)
    const input=screen.getByRole('textbox',{name:'字号'})
    await user.click(input)
    await user.clear(input)
    await user.type(input,'36')
    expect(onUpdate).not.toHaveBeenCalled()
    await user.click(screen.getByTestId('canvas'))
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith(region.id,{font_size:36,auto_font_size:false})
    expect(useEditorStore.getState().selectedIds).toEqual(target ? [target] : [])
  })

  it.each(['original','translated'] as const)('omits the batch action panel for multi-selection in %s view without changing selection',view => {
    useEditorStore.setState({view,selectedIds:['region-1','region-2']})
    const onUpdate=vi.fn(),onAction=vi.fn()
    render(<LayerInspectorHarness onUpdate={onUpdate} onAction={onAction}/>)
    expect(screen.getByText('已选择 2 个区域')).toBeInTheDocument()
    expect(screen.queryByRole('region',{name:'批量区域操作'})).not.toBeInTheDocument()
    expect(screen.queryByText(/批量操作/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'所选文本框靠左'})).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'重新 OCR'})).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'合并区域'})).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'删除所选'})).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'取消选择'})).not.toBeInTheDocument()
    expect(useEditorStore.getState().selectedIds).toEqual(['region-1','region-2'])
    expect(onUpdate).not.toHaveBeenCalled()
    expect(onAction).not.toHaveBeenCalled()
  })

  it('does not display the internal OCR confidence', () => {
    renderProperties()

    expect(screen.queryByText('99%')).not.toBeInTheDocument()
  })

  it('uses the reference typesetting rows while preserving advanced controls', () => {
    const onUpdate = renderProperties()
    const styles = screen.getByRole('region', {name: '排版设置'})
    const direction = within(styles).getByRole('button', {name: '文字方向'})
    const row = direction.closest('label')!.parentElement!
    expect(row).toHaveClass('inspector-alignment-row')
    expect(within(row).getByRole('button', {name: '文字左对齐'})).toBeInTheDocument()
    expect(within(row).getByRole('button', {name: '文字居中'})).toHaveAttribute('aria-pressed', 'true')
    expect(within(row).getByRole('button', {name: '文字右对齐'})).toBeInTheDocument()
    expect(within(styles).getByRole('button', {name: '字体'}).closest('label')!.parentElement).toHaveClass('inspector-font-row')
    expect(within(styles).getByRole('textbox', {name: '字号'}).closest('label')!.parentElement).toHaveClass('inspector-size-row')
    expect(within(styles).getByRole('textbox', {name: '描边宽度'}).closest('label')!.parentElement).toHaveClass('inspector-stroke-row')
    const style=within(styles).getByRole('button',{name:'排版样式'})
    expect(style.closest('label')!.parentElement).toHaveClass('inspector-stroke-row')
    expect(styles.querySelector('.inspector-size-row')!.children).toHaveLength(2)
    expect(styles.querySelector('.inspector-stroke-row')!.children).toHaveLength(3)
    expect(Array.from(styles.querySelector('.inspector-stroke-row')!.children, label => label.firstChild?.textContent)).toEqual(['样式', '宽度', '颜色'])
    expect(screen.queryByText(/背景修复/)).not.toBeInTheDocument()
    expect(screen.queryByText(/画布像素/)).not.toBeInTheDocument()
    expect(screen.queryByText(/方向键微调/)).not.toBeInTheDocument()

    fireEvent.click(direction)
    fireEvent.click(screen.getByRole('option', {name: '竖排'}))
    expect(onUpdate).toHaveBeenCalledWith(region.id, {orientation: 'vertical'})
    expect(screen.queryByText('更多排版')).not.toBeInTheDocument()
    expect(within(styles).getByRole('textbox', {name:'行距'})).toBeVisible()
    expect(within(styles).getByRole('textbox', {name:'字距'})).toBeVisible()
    expect(within(styles).queryByText('译文旋转')).not.toBeInTheDocument()
    expect(within(styles).getByRole('textbox', {name:'译文 X 坐标（像素）'})).toHaveValue('10')
    expect(within(styles).getByRole('textbox', {name:'译文 Y 坐标（像素）'})).toHaveValue('10')
    expect(within(styles).getByRole('textbox', {name:'译文 Z 旋转角度（度）'})).toBeInTheDocument()
  })

  it('keeps standard and perspective styles editable after moving the selector next to outline width',() => {
    const onUpdate=renderProperties()
    const style=screen.getByRole('button',{name:'排版样式'})
    fireEvent.click(style)
    fireEvent.click(screen.getByRole('option',{name:'随形透视'}))
    expect(onUpdate).toHaveBeenLastCalledWith(region.id,{perspective_warp:true})
    fireEvent.click(style)
    fireEvent.click(screen.getByRole('option',{name:'常规'}))
    expect(onUpdate).toHaveBeenLastCalledWith(region.id,{perspective_warp:false})
  })

  it('warns that changing the source frame requires OCR rather than implying repair recognizes text', () => {
    const stale = {...region,layout_data:{ocr_geometry_stale:true}}
    render(<RegionProperties region={stale} selectedRegions={[stale]} selectedCount={1} fontOptions={[]} onUpdate={vi.fn()} onAction={vi.fn()}/>)
    expect(screen.getByRole('note')).toHaveTextContent('请按新框重新 OCR')
    expect(screen.getByRole('note')).toHaveTextContent('修复只同步框的位置，不会重新识字')
  })

  it('expands the selected text inside its list row below the typesetting section', () => {
    renderProperties()
    expect(screen.getByRole('heading', {name:'排版'})).toHaveClass('inspector-section-heading')
    expect(screen.getByRole('heading', {name:/图层/})).toHaveClass('inspector-layer-heading')
    const selected = screen.getByRole('button', {name:'选择 R001：translated'}).closest('article')!
    expect(selected).toHaveClass('is-selected')
    expect(within(selected).getByRole('textbox', {name:'原文'})).toHaveValue('source')
    expect(within(selected).getByRole('textbox', {name:'译文'})).toHaveValue('translated')
    expect(within(selected).getByRole('slider', {name:'译文不透明度'})).toHaveValue('100')
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

  it.each(['original','translated','comparison'] as const)('keeps X/Y/Z only in typesetting, not in layer details in %s view',view => {
    useEditorStore.setState({view})
    renderProperties()
    const layer=screen.getByRole('button',{name:'选择 R001：translated'}).closest('article')!
    expect(layer.querySelector('details')).not.toBeInTheDocument()
    expect(within(layer).queryByRole('textbox',{name:/坐标|旋转/})).not.toBeInTheDocument()
    expect(within(layer).getAllByRole('textbox')).toHaveLength(2)
    expect(within(layer).getByRole('textbox',{name:'原文'})).toBeVisible()
    expect(within(layer).getByRole('textbox',{name:'译文'})).toBeVisible()
    expect(within(screen.getByRole('group',{name:'译文位置与旋转'})).getAllByRole('textbox')).toHaveLength(3)
  })

  it.each([false,true])('retains the layer lock toggle when locked is %s',locked => {
    const target={...region,locked},onUpdate=vi.fn()
    render(<RegionProperties region={target} selectedRegions={[target]} selectedCount={1} fontOptions={[]} onUpdate={onUpdate} onAction={vi.fn()}/>)
    const layer=screen.getByRole('button',{name:'选择 R001：translated'}).closest('article')!
    fireEvent.click(within(layer).getByRole('button',{name:locked ? '解除锁定' : '锁定区域'}))
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith(region.id,{locked:!locked})
  })

  it.each([1, 2])('commits a complete line spacing once for %i selected regions', count => {
    const selectedRegions=layerRegions.slice(0,count), onUpdateMany=vi.fn(), onUpdate=vi.fn()
    render(<RegionProperties selectedRegions={selectedRegions} selectedCount={count} fontOptions={[]} onUpdate={onUpdate} onUpdateMany={onUpdateMany} onAction={vi.fn()}/>)
    const input=screen.getByRole('textbox',{name:'行距'})
    fireEvent.focus(input)
    for(const value of ['0','0.','0.9']) fireEvent.change(input,{target:{value}})
    expect(onUpdateMany).not.toHaveBeenCalled()
    fireEvent.keyDown(input,{key:'Enter'})
    fireEvent.blur(input)
    expect(onUpdateMany).toHaveBeenCalledExactlyOnceWith(selectedRegions.map(item=>({id:item.id,patch:{line_spacing:0.9}})))
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it('keeps explicit typography rotation separate from frame geometry', () => {
    useEditorStore.setState({view:'translated'})
    const onUpdate=renderProperties()
    const input=screen.getByRole('textbox',{name:'译文 Z 旋转角度（度）'})
    fireEvent.focus(input)
    fireEvent.change(input,{target:{value:'27'}})
    fireEvent.blur(input)
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith(region.id,{rotation:27})
  })

  it('publishes an intentional zero rotation even when the region is already upright', () => {
    const onUpdate=vi.fn(), upright={...region,rotation:0}
    render(<RegionProperties region={upright} selectedRegions={[upright]} selectedCount={1} fontOptions={[]} onUpdate={onUpdate} onAction={vi.fn()}/>)
    const input=screen.getByRole('textbox',{name:'译文 Z 旋转角度（度）'})
    fireEvent.focus(input)
    fireEvent.change(input,{target:{value:'0.0'}})
    fireEvent.keyDown(input,{key:'Enter'})
    fireEvent.blur(input)
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith(region.id,{rotation:0})
  })

  it('shows only additional rotation in perspective while retaining the automatic standard angle', () => {
    const onUpdate=vi.fn(), automatic={...region,rotation:-28,perspective_warp:true,
      layout_data:{text_rotation:{version:1,source:'detector',role:'free_text',angle:-28}}}
    const props={region:automatic,selectedRegions:[automatic],selectedCount:1,fontOptions:[],onUpdate,onAction:vi.fn()}
    const {rerender}=render(<RegionProperties {...props}/>)
    expect(screen.getByRole('textbox',{name:'译文 Z 旋转角度（度）'})).toHaveValue('0')
    const standard={...automatic,perspective_warp:false}
    rerender(<RegionProperties {...props} region={standard} selectedRegions={[standard]}/>)
    expect(screen.getByRole('textbox',{name:'译文 Z 旋转角度（度）'})).toHaveValue('-28')
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it.each(['original','translated','comparison'] as const)('moves only translated geometry with X/Y controls in %s view',view => {
    useEditorStore.setState({view})
    const onUpdate=renderProperties()
    const x=screen.getByRole('textbox',{name:'译文 X 坐标（像素）'})
    fireEvent.focus(x)
    fireEvent.change(x,{target:{value:'-15'}})
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.keyDown(x,{key:'Enter'})
    fireEvent.blur(x)
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith(region.id,{
      translated_bbox:[-15,10,80,40],
      translated_polygon:[[-15,10],[65,10],[65,50],[-15,50]],
    })
    onUpdate.mockClear()
    fireEvent.click(screen.getByRole('button',{name:'译文 Y 坐标（像素）增大'}))
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith(region.id,{
      translated_bbox:[10,11,80,40],
      translated_polygon:[[10,11],[90,11],[90,51],[10,51]],
    })
  })

  it('moves a selection from its top-left position without collapsing the spacing between regions',() => {
    const second={...region,id:'second',translated_bbox:[50,30,80,40],translated_polygon:[[50,30],[130,30],[130,70],[50,70]]}
    const onUpdateMany=vi.fn()
    render(<RegionProperties selectedRegions={[region,second]} selectedCount={2} fontOptions={[]} onUpdate={vi.fn()} onUpdateMany={onUpdateMany} onAction={vi.fn()}/>)
    expect(screen.getByRole('textbox',{name:'译文 X 坐标（像素）'})).toHaveValue('10')
    fireEvent.click(screen.getByRole('button',{name:'译文 X 坐标（像素）增大'}))
    expect(onUpdateMany).toHaveBeenCalledExactlyOnceWith([
      {id:region.id,patch:{translated_bbox:[11,10,80,40],translated_polygon:[[11,10],[91,10],[91,50],[11,50]]}},
      {id:second.id,patch:{translated_bbox:[51,30,80,40],translated_polygon:[[51,30],[131,30],[131,70],[51,70]]}},
    ])
  })

  it('steps mixed Z angles independently and clamps each angle',() => {
    const onUpdateMany=vi.fn(), targets=[{...region,rotation:180},{...region,id:'second',rotation:-20}]
    render(<RegionProperties selectedRegions={targets} selectedCount={2} fontOptions={[]} onUpdate={vi.fn()} onUpdateMany={onUpdateMany} onAction={vi.fn()}/>)
    expect(screen.getByRole('textbox',{name:'译文 Z 旋转角度（度）'})).toHaveValue('')
    fireEvent.click(screen.getByRole('button',{name:'译文 Z 旋转角度（度）增大'}))
    expect(onUpdateMany).toHaveBeenCalledExactlyOnceWith([{id:region.id,patch:{rotation:180}},{id:'second',patch:{rotation:-19}}])
  })

  it('excludes locked regions from X/Y/Z transformations',() => {
    const onUpdateMany=vi.fn(),locked={...region,id:'locked',locked:true,translated_bbox:[0,0,80,40],rotation:40}
    render(<RegionProperties selectedRegions={[locked,region]} selectedCount={2} fontOptions={[]} onUpdate={vi.fn()} onUpdateMany={onUpdateMany} onAction={vi.fn()}/>)
    expect(screen.getByRole('textbox',{name:'译文 X 坐标（像素）'})).toHaveValue('10')
    fireEvent.click(screen.getByRole('button',{name:'译文 X 坐标（像素）增大'}))
    expect(onUpdateMany.mock.calls[0][0].map((change:{id:string}) => change.id)).toEqual([region.id])
    fireEvent.click(screen.getByRole('button',{name:'译文 Z 旋转角度（度）增大'}))
    expect(onUpdateMany).toHaveBeenLastCalledWith([{id:region.id,patch:{rotation:13}}])
  })

  it.each(['empty','locked','busy'] as const)('disables X/Y/Z controls for %s selection',state => {
    const targets=state === 'empty' ? [] : [{...region,locked:state === 'locked'}]
    const onUpdateMany=vi.fn()
    render(<RegionProperties selectedRegions={targets} selectedCount={targets.length} fontOptions={[]} onUpdate={vi.fn()} onUpdateMany={onUpdateMany} onAction={vi.fn()} busyAction={state === 'busy' ? 'translate' : null}/>)
    const transform=screen.getByRole('group',{name:'译文位置与旋转'})
    within(transform).getAllByRole('textbox').forEach(input => expect(input).toBeDisabled())
    within(transform).getAllByRole('button').forEach(button => expect(button).toBeDisabled())
    expect(onUpdateMany).not.toHaveBeenCalled()
  })

  it('reflects and toggles the translated group visibility in comparison view', () => {
    useEditorStore.getState().setView('comparison')
    renderProperties()
    fireEvent.click(screen.getByRole('button',{name:'隐藏文本组'}))
    expect(useEditorStore.getState().layers.translated).toBe(false)
    fireEvent.click(screen.getByRole('button',{name:'显示文本组'}))
    expect(useEditorStore.getState().layers.translated).toBe(true)
  })

  it('collapses only text rows without hiding canvas text, clearing selection or hiding the Mask layer', () => {
    useEditorStore.getState().select('region-1')
    const onUpdate=vi.fn(),onAction=vi.fn()
    render(<LayerInspectorHarness onUpdate={onUpdate} onAction={onAction}/>)
    const toggle=screen.getByRole('button',{name:'折叠文本组'})
    const list=document.getElementById(toggle.getAttribute('aria-controls')!)!
    const row=screen.getByRole('button',{name:'选择 R001：translated'})
    expect(toggle).toHaveAttribute('aria-expanded','true')
    fireEvent.click(toggle)
    expect(screen.getByRole('button',{name:'展开文本组'})).toHaveAttribute('aria-expanded','false')
    expect(list).not.toBeVisible()
    expect(row).toBeInTheDocument()
    expect(screen.queryByRole('button',{name:/^选择 R00/})).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'显示 Mask 图层'})).toBeVisible()
    expect(screen.getByRole('region',{name:'排版设置'})).toBeVisible()
    expect(useEditorStore.getState().layers.translated).toBe(true)
    expect(useEditorStore.getState().selectedIds).toEqual(['region-1'])
    expect(onUpdate).not.toHaveBeenCalled()
    expect(onAction).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button',{name:'展开文本组'}))
    expect(screen.getByRole('button',{name:'选择 R001：translated'})).toBe(row)
    expect(screen.getByRole('textbox',{name:'译文'})).toHaveValue('translated')
  })

  it('keeps group visibility and collapse independent in either direction', () => {
    render(<LayerInspectorHarness onUpdate={vi.fn()} onAction={vi.fn()}/>)
    fireEvent.click(screen.getByRole('button',{name:'隐藏文本组'}))
    expect(screen.getByRole('button',{name:'折叠文本组'})).toHaveAttribute('aria-expanded','true')
    expect(screen.getAllByRole('button',{name:/^选择 R00/})).toHaveLength(3)
    fireEvent.click(screen.getByRole('button',{name:'折叠文本组'}))
    fireEvent.click(screen.getByRole('button',{name:'显示文本组'}))
    expect(useEditorStore.getState().layers.translated).toBe(true)
    expect(screen.getByRole('button',{name:'展开文本组'})).toHaveAttribute('aria-expanded','false')
    expect(screen.queryByRole('button',{name:/^选择 R00/})).not.toBeInTheDocument()
  })

  it('preserves the mounted text editors and lock toggle when folding text rows', () => {
    useEditorStore.getState().select('region-1')
    const onUpdate=vi.fn()
    render(<LayerInspectorHarness onUpdate={onUpdate} onAction={vi.fn()}/>)
    const source=screen.getByRole('textbox',{name:'原文'}),translation=screen.getByRole('textbox',{name:'译文'})
    const lock=screen.getByRole('button',{name:'锁定区域'})
    fireEvent.click(screen.getByRole('button',{name:'折叠文本组'}))
    fireEvent.click(screen.getByRole('button',{name:'展开文本组'}))
    expect(screen.getByRole('textbox',{name:'原文'})).toBe(source)
    expect(screen.getByRole('textbox',{name:'译文'})).toBe(translation)
    expect(screen.getByRole('button',{name:'锁定区域'})).toBe(lock)
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it('supports keyboard folding while edits are busy and remembers it across the outer layer fold', async () => {
    const user=userEvent.setup()
    render(<LayerInspectorHarness onUpdate={vi.fn()} onAction={vi.fn()} busyAction="ocr"/>)
    const toggle=screen.getByRole('button',{name:'折叠文本组'})
    expect(toggle).toBeEnabled()
    toggle.focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('button',{name:'展开文本组'})).toHaveAttribute('aria-expanded','false')
    fireEvent.click(screen.getByRole('button',{name:/^图层/}))
    fireEvent.click(screen.getByRole('button',{name:/^图层/}))
    expect(screen.getByRole('button',{name:'展开文本组'})).toHaveAttribute('aria-expanded','false')
    screen.getByRole('button',{name:'展开文本组'}).focus()
    await user.keyboard(' ')
    expect(screen.getAllByRole('button',{name:/^选择 R00/})).toHaveLength(3)
  })

  it('does not scroll to hidden selected rows and reveals the selection when the group reopens', () => {
    const {container}=render(<LayerInspectorHarness onUpdate={vi.fn()} onAction={vi.fn()}/>)
    const list=container.querySelector<HTMLElement>('.inspector-layer-list')!
    list.style.overflowY='auto'
    const row=screen.getByRole('button',{name:'选择 R003：third source'}).closest('article')!
    vi.spyOn(list,'getBoundingClientRect').mockReturnValue(new DOMRect(0,100,280,300))
    const bounds=vi.spyOn(row,'getBoundingClientRect').mockReturnValue(new DOMRect(0,450,280,180))
    fireEvent.click(screen.getByRole('button',{name:'折叠文本组'}))
    // Selection can change via the canvas while the list is folded.
    act(() => useEditorStore.getState().select('region-3'))
    expect(bounds).not.toHaveBeenCalled()
    expect(list.scrollTop).toBe(0)
    fireEvent.click(screen.getByRole('button',{name:'展开文本组'}))
    expect(bounds).toHaveBeenCalled()
    expect(list.scrollTop).toBe(230)
  })

  it('shows all text rows without a selection and expands only the clicked row', () => {
    render(<LayerInspectorHarness onUpdate={vi.fn()} onAction={vi.fn()}/>)
    expect(screen.getByRole('button',{name:'字体'})).toBeDisabled()
    expect(screen.getAllByRole('button',{name:/^选择 R00/})).toHaveLength(3)
    expect(screen.queryByRole('textbox',{name:'原文'})).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button',{name:'选择 R002：second translation'}))
    expect(useEditorStore.getState().selectedIds).toEqual(['region-2'])
    expect(screen.getByRole('textbox',{name:'译文'})).toHaveValue('second translation')
    expect(screen.getByRole('button',{name:'字体'})).toBeEnabled()
    fireEvent.click(screen.getByRole('button',{name:'选择 R003：third source'}))
    expect(screen.getByRole('textbox',{name:'原文'})).toHaveValue('third source')
    expect(screen.getAllByRole('textbox',{name:'译文'})).toHaveLength(1)
  })

  it.each(['auto','visible'] as const)('reveals the selected row in the active scroll container when list overflow is %s', overflow => {
    const {container}=render(<LayerInspectorHarness onUpdate={vi.fn()} onAction={vi.fn()}/>)
    const list=container.querySelector<HTMLElement>('.inspector-layer-list')!
    const inspector=container.querySelector<HTMLElement>('.region-inspector')!
    list.style.overflowY=overflow
    const scroller=overflow === 'visible' ? inspector : list
    const inactive=overflow === 'visible' ? list : inspector
    const button=screen.getByRole('button',{name:'选择 R003：third source'})
    const row=button.closest('article')!
    vi.spyOn(scroller,'getBoundingClientRect').mockReturnValue(new DOMRect(0,100,280,300))
    vi.spyOn(inactive,'getBoundingClientRect').mockReturnValue(new DOMRect(0,0,280,1000))
    vi.spyOn(row,'getBoundingClientRect').mockReturnValue(new DOMRect(0,450,280,180))

    fireEvent.click(button)

    expect(scroller.scrollTop).toBe(230)
    expect(inactive.scrollTop).toBe(0)
  })

  it.each([
    {top:50,height:180,delta:-50},
    {top:450,height:500,delta:350},
    {top:150,height:180,delta:0},
  ])('keeps a row at $top with height $height reachable in the whole-panel scroller', ({top,height,delta}) => {
    const {container}=render(<LayerInspectorHarness onUpdate={vi.fn()} onAction={vi.fn()}/>)
    const list=container.querySelector<HTMLElement>('.inspector-layer-list')!
    const inspector=container.querySelector<HTMLElement>('.region-inspector')!
    list.style.overflowY='visible'
    inspector.scrollTop=100
    const button=screen.getByRole('button',{name:'选择 R003：third source'})
    vi.spyOn(inspector,'getBoundingClientRect').mockReturnValue(new DOMRect(0,100,280,300))
    vi.spyOn(button.closest('article')!,'getBoundingClientRect').mockReturnValue(new DOMRect(0,top,280,height))

    fireEvent.click(button)

    expect(inspector.scrollTop).toBe(100+delta)
    expect(list.scrollTop).toBe(0)
  })

  it('supports range and additive selection from the text list without the removed batch panel', () => {
    render(<LayerInspectorHarness onUpdate={vi.fn()} onAction={vi.fn()}/>)
    fireEvent.click(screen.getByRole('button',{name:'选择 R001：translated'}))
    fireEvent.click(screen.getByRole('button',{name:'选择 R003：third source'}),{shiftKey:true})
    expect(useEditorStore.getState().selectedIds).toEqual(['region-1','region-2','region-3'])
    expect(screen.getByText('已选择 3 个区域')).toBeInTheDocument()
    expect(screen.queryByRole('textbox',{name:'译文'})).not.toBeInTheDocument()
    expect(screen.queryByRole('region',{name:'批量区域操作'})).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'所选文字左对齐'})).toBeEnabled()
    fireEvent.click(screen.getByRole('button',{name:'选择 R002：second translation'}),{ctrlKey:true})
    expect(useEditorStore.getState().selectedIds).toEqual(['region-1','region-3'])
    fireEvent.click(screen.getByRole('button',{name:'选择 R003：third source'}),{metaKey:true})
    expect(useEditorStore.getState().selectedIds).toEqual(['region-1'])
    expect(screen.getByRole('textbox',{name:'译文'})).toHaveValue('translated')
  })

  it('targets the row eye independently and keeps opacity and text edits on the selected region', () => {
    const onUpdate=vi.fn(), onAction=vi.fn()
    render(<LayerInspectorHarness onUpdate={onUpdate} onAction={onAction}/>)
    fireEvent.click(screen.getByRole('button',{name:'选择 R001：translated'}))
    fireEvent.click(screen.getByRole('button',{name:'R002 隐藏译文'}))
    expect(onUpdate).toHaveBeenLastCalledWith('region-2',{visible:false})
    expect(useEditorStore.getState().selectedIds).toEqual(['region-1'])
    fireEvent.change(screen.getByRole('slider',{name:'译文不透明度'}),{target:{value:'35'}})
    expect(onUpdate).toHaveBeenLastCalledWith('region-1',{opacity:.35})
    fireEvent.change(screen.getByRole('textbox',{name:'译文'}),{target:{value:'new translation'}})
    expect(onUpdate).toHaveBeenLastCalledWith('region-1',{translated_text:'new translation'})
    fireEvent.click(screen.getByRole('button',{name:'删除当前区域'}))
    expect(onAction).toHaveBeenCalledWith('delete')
  })

  it('offers a default-off read-only Mask layer instead of cleanup and original rows', () => {
    const onUpdate=vi.fn()
    render(<LayerInspectorHarness onUpdate={onUpdate} onAction={vi.fn()}/>)
    fireEvent.click(screen.getByRole('button',{name:'隐藏文本组'}))
    expect(useEditorStore.getState().layers.translated).toBe(false)
    expect(screen.queryByText('修复背景')).not.toBeInTheDocument()
    expect(screen.queryByText('原始图稿')).not.toBeInTheDocument()
    const mask=screen.getByRole('button',{name:'显示 Mask 图层'})
    expect(mask).toHaveAttribute('aria-pressed','false')
    fireEvent.click(mask)
    expect(useEditorStore.getState().layers.mask).toBe(true)
    expect(screen.getByRole('button',{name:'隐藏 Mask 图层'})).toHaveAttribute('aria-pressed','true')
    fireEvent.click(screen.getByRole('button',{name:'隐藏 Mask 图层'}))
    expect(useEditorStore.getState().layers.mask).toBe(false)
    expect(screen.queryByRole('button',{name:/原图/})).not.toBeInTheDocument()
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it('sorts text rows naturally by name without mutating reading order, and range-selects in that display order', () => {
    const items=[{...region,id:'ten',region_key:'R010',reading_order:1},{...region,id:'two',region_key:'R002',reading_order:3},{...region,id:'one',region_key:'R001',reading_order:2}]
    render(<RegionProperties page={page} regions={items} selectedRegions={[]} selectedCount={0} fontOptions={[]} onUpdate={vi.fn()} onAction={vi.fn()}/>)
    const buttons=screen.getAllByRole('button',{name:/^选择 R/})
    expect(buttons.map(button => button.getAttribute('aria-label'))).toEqual(['选择 R001：translated','选择 R002：translated','选择 R010：translated'])
    expect(items.map(item => item.id)).toEqual(['ten','two','one'])
    expect(items.map(item => item.reading_order)).toEqual([1,3,2])
    fireEvent.click(buttons[0]);fireEvent.click(buttons[2],{shiftKey:true})
    expect(useEditorStore.getState().selectedIds).toEqual(['one','two','ten'])
  })

  it('disables edits and list selection during an exclusive processing operation', () => {
    const onUpdate=vi.fn()
    render(<LayerInspectorHarness onUpdate={onUpdate} onAction={vi.fn()} busyAction="ocr"/>)
    expect(screen.getByRole('button',{name:'选择 R002：second translation'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'R002 隐藏译文'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'隐藏文本组'})).toBeDisabled()
    fireEvent.click(screen.getByRole('button',{name:'R002 隐藏译文'}))
    expect(onUpdate).not.toHaveBeenCalled()
    expect(useEditorStore.getState().selectedIds).toEqual([])
  })
})
