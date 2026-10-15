import {act, render, screen} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'

import type {ImagePage} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'

type MockNodeProps = {children?: ReactNode}

const {stageRenderMock, useImageMock, konvaImageMock, useMaskOverlayMock} = vi.hoisted(() => ({
  stageRenderMock: vi.fn(),
  useImageMock: vi.fn((_source: string | null | undefined) => ({image: null, loading: true, error: false})),
  konvaImageMock: vi.fn((_props:Record<string,unknown>) => null),
  useMaskOverlayMock: vi.fn((_page:unknown,_regions:unknown,_enabled:boolean):{image:HTMLCanvasElement|null;loading:boolean;error:string|null} => ({image:null,loading:false,error:null})),
}))

vi.mock('konva', () => {
  class Group {
    add() {}
    destroy() {}
    toCanvas() { return document.createElement('canvas') }
  }

  class Text {}

  return {default: {Group, Text}}
})

vi.mock('react-konva', () => {
  const Container = ({children}: MockNodeProps) => <>{children}</>
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
    useImageMock.mockClear()
    konvaImageMock.mockClear()
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
    expect(useImageMock.mock.calls.slice(-3).map(([source]) => source)).toEqual([null, '/media/page-clean.png', null])
  })

  it('loads both page artifacts only for comparison view', () => {
    useEditorStore.setState({
      view: 'comparison',
      layers: {original: false, detection: false, clean: false, translated: false, mask:false},
    })

    render(<MangaCanvas
      page={page}
      regions={[]}
      onCreate={vi.fn(async () => true)}
      onUpdate={vi.fn(async () => undefined)}
      onRegionAction={vi.fn()}
      runningAction={null}
      interactionKey="comparison:select:false:"
    />)

    expect(useImageMock.mock.calls.slice(-3).map(([source]) => source)).toEqual([
      '/media/page.png',
      '/media/page-clean.png',
      null,
    ])
  })

  it('loads the export text layer only while its region snapshot matches', () => {
    const props = {page: {...page, text_layer_url: '/media/text.png?v=2', text_layer_region_ids: []},
      regions: [], onCreate: vi.fn(async () => true), onUpdate: vi.fn(async () => undefined),
      onRegionAction: vi.fn(), runningAction: null, interactionKey: 'translated:select:false:'}
    const {rerender} = render(<MangaCanvas {...props}/>)
    expect(useImageMock.mock.calls.at(-1)?.[0]).toBe('/media/text.png?v=2')
    rerender(<MangaCanvas {...props} page={{...props.page, text_layer_region_ids: ['deleted-region']}}/>)
    expect(useImageMock.mock.calls.at(-1)?.[0]).toBeNull()
  })

  it('removes the floating layer panel while honoring inspector layer visibility', () => {
    render(<MangaCanvas page={page} regions={[]} onCreate={vi.fn(async () => true)} onUpdate={vi.fn(async () => undefined)} onRegionAction={vi.fn()} runningAction={null} interactionKey="translated:select:false:"/>)
    expect(screen.queryByLabelText('画布图层')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', {name:'图层'})).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', {name:'透明棋盘格'})).not.toBeInTheDocument()
    act(() => useEditorStore.getState().toggleLayer('original'))
    expect(useImageMock.mock.calls.slice(-3).map(([source]) => source)).toEqual(['/media/page.png', '/media/page-clean.png', null])
    act(() => useEditorStore.getState().toggleLayer('clean'))
    expect(useImageMock.mock.calls.slice(-3).map(([source]) => source)).toEqual(['/media/page.png', null, null])
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
