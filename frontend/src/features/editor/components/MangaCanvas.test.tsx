import {act, render, screen} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'

import type {ImagePage, TextRegion} from '../../../types'
import {useEditorStore} from '../store'
import {notifyFontsChanged} from '../lib/fontRevision'
import {PERSPECTIVE_RENDER_OUTPUT_VERSION} from '../lib/perspectiveText'
import {MangaCanvas} from './MangaCanvas'

type MockNodeProps = {children?: ReactNode; x?: number; y?: number; offsetX?: number; offsetY?: number; rotation?: number}

const {stageRenderMock, useImageMock, konvaImageMock, liveTextMock, useMaskOverlayMock, tileCanvasMock, tileGroupMock} = vi.hoisted(() => ({
  tileCanvasMock:vi.fn(), tileGroupMock:vi.fn(),
  stageRenderMock: vi.fn(),
  useImageMock: vi.fn((_source: string | null | undefined):{image:HTMLImageElement|null;loading:boolean;error:boolean} => ({image: null, loading: true, error: false})),
  konvaImageMock: vi.fn((_props:Record<string,unknown>):ReactNode => null),
  liveTextMock: vi.fn((_props:Record<string,unknown>):ReactNode => null),
  useMaskOverlayMock: vi.fn((_page:unknown,_regions:unknown,_enabled:boolean):{image:HTMLCanvasElement|null;loading:boolean;error:string|null} => ({image:null,loading:false,error:null})),
}))

vi.mock('konva', () => {
  class Group {
    constructor(props:unknown) {tileGroupMock(props)}
    add() {}
    destroy() {}
    toCanvas(props:unknown) {tileCanvasMock(props); return document.createElement('canvas')}
  }

  class Text {}

  return {default: {Group, Text, Shape: Text}}
})

vi.mock('react-konva', () => {
  const Container = ({children,x,y,offsetX,offsetY,rotation}: MockNodeProps) => <div data-group-x={x}
    data-group-y={y} data-offset-x={offsetX} data-offset-y={offsetY} data-rotation={rotation}>{children}</div>
  const EmptyNode = () => null

  return {
    Stage: ({children}: MockNodeProps) => { stageRenderMock(); return <div data-testid="konva-stage">{children}</div> },
    Layer: Container,
    Group: Container,
    Circle: EmptyNode,
    Image: konvaImageMock,
    Line: EmptyNode,
    Rect: EmptyNode,
    Text: EmptyNode,
    Shape: liveTextMock,
  }
})

vi.mock('../hooks/useImage', () => ({
  useImage: useImageMock,
}))
vi.mock('../hooks/useMaskOverlay', () => ({useMaskOverlay:useMaskOverlayMock}))

const page: ImagePage = {
  id: 'image-1',
  project_id: 'project-1',
  filename: 'page.png',
  width: 1200,
  height: 1800,
  order_index: 0,
  status: 'UPLOADED',
  current_stage: null,
  error_message: null,
  ocr_exempt: false,
  original_url: '/media/page.png',
  clean_url: '/media/page-clean.png',
  rendered_url: null,
  text_layer_url: null,
  created_at: '2026-08-24T00:00:00Z',
  updated_at: '2026-08-24T00:00:00Z',
}

function translatedRegion(id: string, orientation: TextRegion['orientation'] = 'horizontal'): TextRegion {
  const region = {id, region_key:id, visible:true, locked:false, reading_order:1,
    bbox:[10,10,100,100], polygon:[[10,10],[110,10],[110,110],[10,110]],
    translated_bbox:[10,10,100,100], translated_polygon:[[10,10],[110,10],[110,110],[10,110]],
    translated_text:'清晰的小字', orientation, font_size:10, auto_font_size:false,
    font_family:'sans-serif', font_weight:400, text_color:'#111111', stroke_color:'#ffffff', stroke_width:0,
    alignment:'center', line_spacing:1.15, character_spacing:0, rotation:0,
    perspective_warp:false, opacity:1, layout_data:{}} as TextRegion
  region.layout_data = {render_output_version:PERSPECTIVE_RENDER_OUTPUT_VERSION,
    render_input:{...region, custom_font_path:null}}
  return region
}

class ResizeObserverMock {
  observe() {}
  disconnect() {}
}

describe('MangaCanvas image loading', () => {
  beforeAll(() => vi.stubGlobal('ResizeObserver', ResizeObserverMock))
  afterAll(() => vi.unstubAllGlobals())

  beforeEach(() => {
    vi.useFakeTimers()
    stageRenderMock.mockClear()
    useImageMock.mockReset().mockReturnValue({image:null,loading:true,error:false})
    konvaImageMock.mockReset().mockReturnValue(null)
    liveTextMock.mockReset().mockImplementation(() => <span data-testid="live-text"/>)
    tileCanvasMock.mockClear()
    tileGroupMock.mockClear()
    useMaskOverlayMock.mockReset().mockReturnValue({image:null,loading:false,error:null})
    useEditorStore.setState({
      view: 'translated',
      tool: 'select',
      zoom: 1,
      selectedIds: [],
      layers: {original: false, detection: true, clean: true, translated: true, mask:false},
    })
  })
  afterEach(() => vi.useRealTimers())

  it('keeps the canvas visible without showing an image-loading overlay', () => {
    render(<MangaCanvas
      page={page}
      regions={[]}
      onCreate={vi.fn(async () => true)}
      onUpdate={vi.fn(async () => undefined)}
      onRegionAction={vi.fn()}
      runningAction={null}
      interactionKey="translated:select:false:"
    />)

    act(() => vi.advanceTimersByTime(250))

    expect(screen.getByTestId('konva-stage')).toBeInTheDocument()
    expect(screen.queryByText('正在载入画布图片')).not.toBeInTheDocument()
    expect(screen.queryByRole('status', {name: '正在载入画布图片'})).not.toBeInTheDocument()
    expect(useImageMock.mock.calls.slice(-2).map(([source]) => source)).toEqual([null, '/media/page-clean.png'])
  })

  it('loads both page artifacts only for comparison view', () => {
    useEditorStore.getState().setView('comparison')

    render(<MangaCanvas
      page={page}
      regions={[]}
      onCreate={vi.fn(async () => true)}
      onUpdate={vi.fn(async () => undefined)}
      onRegionAction={vi.fn()}
      runningAction={null}
      interactionKey="comparison:select:false:"
    />)

    expect(useImageMock.mock.calls.slice(-2).map(([source]) => source)).toEqual([
      '/media/page.png',
      '/media/page-clean.png',
    ])
  })

  it('toggles comparison text only on the right without overlaying the original', () => {
    const context = vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
    useEditorStore.getState().setView('comparison')
    const region = translatedRegion('r1')
    render(<MangaCanvas page={{...page,text_layer_url:'/media/text.png',text_layer_region_ids:[region.id]}} regions={[region]}
      onCreate={vi.fn(async()=>true)} onUpdate={vi.fn(async()=>undefined)} onRegionAction={vi.fn()} runningAction={null} interactionKey="comparison"/>)
    const expectRightOnly=()=>{
      const text=screen.getAllByTestId('live-text')
      expect(text).toHaveLength(1)
      expect(text[0].closest(`[data-group-x="${page.width+32}"]`)).not.toBeNull()
    }
    expectRightOnly()
    act(()=>useEditorStore.getState().toggleLayer('translated'))
    expect(screen.queryByTestId('live-text')).not.toBeInTheDocument()
    act(()=>useEditorStore.getState().toggleLayer('translated'))
    expectRightOnly()
    context.mockRestore()
  })

  it.each(['translated','comparison'] as const)('keeps %s text live before edits, after size round-trips and after artifact refresh', view => {
    const context = vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
    useEditorStore.getState().setView(view)
    const artifact = document.createElement('img')
    useImageMock.mockImplementation(source => ({image:source?.startsWith('/media/text.png') ? artifact : null,loading:false,error:false}))
    const regions = [translatedRegion('r1'), translatedRegion('r2','vertical')]
    const props = {page: {...page, text_layer_url: '/media/text.png?v=2', text_layer_region_ids: regions.map(region=>region.id)},
      regions, onCreate: vi.fn(async () => true), onUpdate: vi.fn(async () => undefined),
      onRegionAction: vi.fn(), runningAction: null, interactionKey: 'translated:select:false:'}
    const {rerender} = render(<MangaCanvas {...props}/>)
    const originalNodes = screen.getAllByTestId('live-text')
    expect(originalNodes).toHaveLength(2)
    expect(liveTextMock).toHaveBeenCalledTimes(2)
    rerender(<MangaCanvas {...props} regions={[{...regions[0],font_size:20},regions[1]]}/>)
    rerender(<MangaCanvas {...props} regions={[{...regions[0],font_size:10},regions[1]]}/>)
    screen.getAllByTestId('live-text').forEach((node,index)=>expect(node).toBe(originalNodes[index]))
    expect(liveTextMock).toHaveBeenCalledTimes(4) // Other small text never switches renderers.
    rerender(<MangaCanvas {...props} page={{...props.page,text_layer_url:'/media/text.png?v=3'}}/>)
    screen.getAllByTestId('live-text').forEach((node,index)=>expect(node).toBe(originalNodes[index]))
    expect(useImageMock.mock.calls.some(([source])=>source?.startsWith('/media/text.png'))).toBe(false)
    expect(konvaImageMock.mock.calls.some(([props])=>props.image===artifact)).toBe(false)
    expect(props.onUpdate).not.toHaveBeenCalled()
    context.mockRestore()
  })

  it('redraws horizontal and vertical text when fonts finish loading without requiring an edit', () => {
    const context = vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
    const onUpdate = vi.fn(async()=>undefined)
    render(<MangaCanvas page={page} regions={[translatedRegion('r1'),translatedRegion('r2','vertical')]}
      onCreate={vi.fn(async()=>true)} onUpdate={onUpdate} onRegionAction={vi.fn()} runningAction={null} interactionKey="fonts"/>)
    expect(liveTextMock).toHaveBeenCalledTimes(2)
    act(()=>notifyFontsChanged())
    expect(liveTextMock).toHaveBeenCalledTimes(4)
    expect(onUpdate).not.toHaveBeenCalled()
    context.mockRestore()
  })

  it.each(['horizontal','vertical'] as const)('previews %s text in local axes and rotates it only once', orientation => {
    const context = vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
    const angle = 12*Math.PI/180
    const polygon = [[-70,-90],[70,-90],[70,90],[-70,90]].map(([x,y]) =>
      [180+x*Math.cos(angle)-y*Math.sin(angle),180+x*Math.sin(angle)+y*Math.cos(angle)])
    const xs = polygon.map(p => p[0]), ys = polygon.map(p => p[1])
    const bbox = [Math.min(...xs),Math.min(...ys),Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys)]
    const region = {id:'slanted',visible:true,polygon,bbox,translated_polygon:polygon,translated_bbox:bbox,
      translated_text:'MANGA TEXT',orientation,rotation:12,font_size:12,auto_font_size:true,font_family:'sans-serif',font_weight:400,
      stroke_width:0,line_spacing:1.15,character_spacing:0,alignment:'center',opacity:1,layout_data:{}} as TextRegion
    const {container} = render(<MangaCanvas page={page} regions={[region]} onCreate={vi.fn(async()=>true)}
      onUpdate={vi.fn(async()=>undefined)} onRegionAction={vi.fn()} runningAction={null} interactionKey="slanted"/>)
    const rotated = container.querySelectorAll('[data-rotation="12"]')
    expect(rotated).toHaveLength(1)
    expect(rotated[0]).toHaveAttribute('data-group-x','180')
    expect(rotated[0]).toHaveAttribute('data-group-y','180')
    expect(rotated[0]).toHaveAttribute('data-offset-x','70')
    expect(rotated[0]).toHaveAttribute('data-offset-y','90')
    context.mockRestore()
  })

  it('removes the floating layer panel while honoring inspector layer visibility', () => {
    render(<MangaCanvas page={page} regions={[]} onCreate={vi.fn(async () => true)} onUpdate={vi.fn(async () => undefined)} onRegionAction={vi.fn()} runningAction={null} interactionKey="translated:select:false:"/>)
    expect(screen.queryByLabelText('画布图层')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', {name:'图层'})).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', {name:'透明棋盘格'})).not.toBeInTheDocument()
    act(() => useEditorStore.getState().toggleLayer('original'))
    expect(useImageMock.mock.calls.slice(-2).map(([source]) => source)).toEqual(['/media/page.png', '/media/page-clean.png'])
    act(() => useEditorStore.getState().toggleLayer('clean'))
    expect(useImageMock.mock.calls.slice(-2).map(([source]) => source)).toEqual(['/media/page.png', null])
  })

  it.each([true,false])('rasterizes the actual oversized perspective ink without clipping; automatic angle: %s', automatic => {
    const context=vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
    const region={...translatedRegion('oversized'),translated_text:'字',font_size:120,stroke_width:3,
      translated_bbox:[10,10,30,40],translated_polygon:[[10,10],[40,10],[40,50],[10,50]],
      rotation:-28,perspective_warp:true,layout_data:automatic ? {
        text_rotation:{source:'detector',role:'free_text',angle:-28}} : {}}
    render(<MangaCanvas page={page} regions={[region]} onCreate={vi.fn(async()=>true)}
      onUpdate={vi.fn(async()=>undefined)} onRegionAction={vi.fn()} runningAction={null} interactionKey="oversized"/>)
    expect(tileCanvasMock).toHaveBeenCalled()
    const bounds=tileCanvasMock.mock.calls[0][0] as {x:number;y:number;width:number;height:number}
    expect(bounds.width).toBeGreaterThan(120)
    expect(bounds.height).toBeGreaterThan(120)
    expect(bounds.x).toBeLessThan(0)
    expect(bounds.y).toBeLessThan(0)
    expect(tileGroupMock).toHaveBeenCalledWith(expect.objectContaining({rotation:automatic ? 0 : -28}))
    context.mockRestore()
  })

  it('skips expensive canvas reconciliation for unrelated parent updates', () => {
    const props = {
      page,
      regions: [],
      onCreate: vi.fn(async () => true),
      onUpdate: vi.fn(async () => undefined),
      onRegionAction: vi.fn(),
      runningAction: null,
      interactionKey: 'translated:select:false:',
    }
    const {rerender} = render(<MangaCanvas {...props}/>)
    const rendered = stageRenderMock.mock.calls.length

    rerender(<MangaCanvas {...props} onCreate={vi.fn(async () => true)} onUpdate={vi.fn(async () => undefined)}/>)
    expect(stageRenderMock).toHaveBeenCalledTimes(rendered)

    rerender(<MangaCanvas {...props} interactionKey="translated:select:true:"/>)
    expect(stageRenderMock.mock.calls.length).toBeGreaterThan(rendered)
  })

  it('renders a read-only mask overlay only when its inspector layer is enabled', () => {
    const image=document.createElement('canvas')
    useMaskOverlayMock.mockImplementation((_page,_regions,enabled) => ({image:enabled?image:null,loading:false,error:null}))
    render(<MangaCanvas page={page} regions={[]} onCreate={vi.fn(async () => true)} onUpdate={vi.fn(async () => undefined)} onRegionAction={vi.fn()} runningAction={null} interactionKey="translated:select:false:"/>)
    expect(konvaImageMock.mock.calls.some(([props]) => props.name==='mask-overlay')).toBe(false)
    act(() => useEditorStore.getState().toggleLayer('mask'))
    expect(konvaImageMock.mock.calls.map(([props]) => props)).toContainEqual(expect.objectContaining({name:'mask-overlay',image,width:1200,height:1800,opacity:0.3,listening:false}))
    expect(useMaskOverlayMock.mock.calls.at(-1)?.[2]).toBe(true)
    act(() => useEditorStore.getState().toggleLayer('mask'))
    expect(useMaskOverlayMock.mock.calls.at(-1)?.[2]).toBe(false)
  })

  it('overlays both sides of comparison with the same mask without intercepting input', () => {
    const image=document.createElement('canvas')
    useEditorStore.setState({view:'comparison',layers:{original:false,detection:false,clean:false,translated:false,mask:true}})
    useMaskOverlayMock.mockReturnValue({image,loading:false,error:null})
    render(<MangaCanvas page={page} regions={[]} onCreate={vi.fn(async () => true)} onUpdate={vi.fn(async () => undefined)} onRegionAction={vi.fn()} runningAction={null} interactionKey="comparison:select:false:"/>)
    const overlays=konvaImageMock.mock.calls.map(([props]) => props).filter(props => String(props.name).startsWith('mask-overlay')).slice(-2)
    expect(overlays).toHaveLength(2)
    expect(overlays).toEqual(expect.arrayContaining([
      expect.objectContaining({name:'mask-overlay-original',image,opacity:0.3,listening:false}),
      expect.objectContaining({name:'mask-overlay-translated',image,opacity:0.3,listening:false}),
    ]))
  })
})
