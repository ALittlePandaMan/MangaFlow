import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import {StrictMode, useRef, useState} from 'react'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import type {ImagePage, TextRegion} from '../../../types'
import {MaskEditor, type MaskEditorHandle, type MaskEditorState} from './MaskEditor'
import {EditorToolbar} from './EditorToolbar'
import {useEditorStore} from '../store'
import {polygonDraftStyle} from '../lib/canvasPalette'
import {loadMaskHistory} from '../lib/maskHistory'
import {MaskBitmap} from '../lib/maskBitmap'

vi.mock('../lib/maskHistory',() => ({loadMaskHistory:vi.fn(async () => {throw new Error('not cached')})}))

const page = {id: 'page', width: 100, height: 120, original_url: '/original.png'} as ImagePage
const region = {id: 'region', region_key: 'R001', bbox: [20,20,40,80], polygon: [[20,20],[60,20],[60,100],[20,100]], mask_url: null} as TextRegion
function Harness({onSave, onClose, onSelectRegion = () => {}, locked = false,onHistoryStep,onHistoryBoundary}: {onSave: (region: TextRegion, blob: Blob) => Promise<TextRegion>, onClose: () => void, onSelectRegion?: (id: string) => void, locked?: boolean,
  onHistoryStep?:(direction:'undo'|'redo',region:TextRegion,operationId:string) => Promise<TextRegion>,onHistoryBoundary?:(direction:'undo'|'redo') => void}) {
  const ref = useRef<MaskEditorHandle>(null)
  const [state, setState] = useState<MaskEditorState>({undo:false,redo:false,dirty:false,busy:false})
  const tool = useEditorStore(value => value.tool)
  const operation = useEditorStore(value => value.maskOperation)
  const [target, setTarget] = useState<HTMLDivElement | null>(null)
  const neighbor = {...region,id:'neighbor',region_key:'R002'}
  return <><EditorToolbar onUndo={() => ref.current?.undo()} onRedo={() => ref.current?.redo()} canUndo={state.undo} canRedo={state.redo} onViewChange={() => ref.current?.requestLeave(onClose)}/><MaskEditor ref={ref} controlsTarget={target} page={page} region={{...region,locked}} regions={[region,neighbor]} tool={tool} operation={operation} onToolChange={useEditorStore.getState().setTool} onSave={onSave} onSelectRegion={onSelectRegion} onStateChange={setState} onHistoryStep={onHistoryStep} onHistoryBoundary={onHistoryBoundary}/><div data-testid="mask-properties" ref={setTarget}/></>
}

describe('mask editor session', () => {
  beforeEach(() => {
    useEditorStore.setState({maskOperation:'brush'})
    vi.stubGlobal('ResizeObserver', class {observe() {} disconnect() {}})
    vi.stubGlobal('Image', class {onload?: () => void; crossOrigin = ''; naturalWidth = 100; naturalHeight = 120; set src(_value: string) {queueMicrotask(() => this.onload?.())}})
    vi.stubGlobal('ImageData', class {constructor(public data: Uint8ClampedArray, public width: number, public height: number) {}})
    vi.stubGlobal('PointerEvent', class extends MouseEvent {
      pointerId: number
      constructor(type: string, options: PointerEventInit) {super(type, options); this.pointerId = options.pointerId || 1}
    })
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(600)
    vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockReturnValue({left:0, top:0, width:800, height:600} as DOMRect)
    const noop = () => {}
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({strokeRect:noop, fillRect:noop, clearRect:noop, setTransform:noop, save:noop, restore:noop, translate:noop, scale:noop, drawImage:noop, setLineDash:noop, beginPath:noop, moveTo:noop, lineTo:noop, closePath:noop, stroke:noop, fill:noop, fillText:noop, arc:noop, putImageData:noop, createImageData:(w:number,h:number) => ({data:new Uint8ClampedArray(w*h*4)})} as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(callback => callback(new Blob(['mask'], {type:'image/png'})))
    HTMLCanvasElement.prototype.setPointerCapture = noop
    HTMLCanvasElement.prototype.releasePointerCapture = noop
    HTMLCanvasElement.prototype.hasPointerCapture = () => false
  })
  afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})
  const setup = async (onSave = vi.fn(async (value: TextRegion, _blob: Blob) => value), locked = false) => {
    const onClose = vi.fn()
    const onSelectRegion = vi.fn()
    useEditorStore.setState({view:'mask', tool:'brush'})
    render(<StrictMode><Harness onSave={onSave} onClose={onClose} onSelectRegion={onSelectRegion} locked={locked}/></StrictMode>)
    await waitFor(() => expect(screen.getByRole('checkbox', {name:'原图参考'})).toBeEnabled())
    const canvas = screen.getByLabelText('蒙版编辑画布')
    const down = (id = 1) => fireEvent.pointerDown(canvas, {pointerId:id, clientX:400, clientY:300, button:0})
    const up = (id = 1) => fireEvent.pointerUp(canvas, {pointerId:id, clientX:400, clientY:300, button:0})
    return {onClose, onSave, onSelectRegion, canvas, down, up}
  }

  it('defaults the original reference on at 30%, removes save/help UI and never saves display preferences', async () => {
    const {onSave}=await setup()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', {name:'原图参考'})).toBeChecked()
    const slider = screen.getByRole('slider', {name:'原图参考不透明度'})
    expect(slider).toHaveValue('30')
    expect(slider).toBeEnabled()
    fireEvent.click(screen.getByRole('checkbox', {name:'原图参考'}))
    expect(slider).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', {name:'原图参考'}))
    expect(slider).toBeEnabled()
    fireEvent.change(slider,{target:{value:'80'}})
    expect(slider).toHaveValue('80')
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    expect(screen.queryByText('操作提示')).not.toBeInTheDocument()
    expect(screen.queryByText('仅编辑蒙版')).not.toBeInTheDocument()
    expect(screen.queryByText('蒙版只决定修复范围，原图始终保留。')).not.toBeInTheDocument()
    expect(screen.queryByText('自动同步')).not.toBeInTheDocument()
    expect(screen.queryByText('同步中')).not.toBeInTheDocument()
    expect(onSave).not.toHaveBeenCalled()
  })
  it('repaints the theme in the mutation microtask even when animation frames are suspended', async () => {
    document.documentElement.dataset.theme = 'light'
    await setup()
    const context = vi.mocked(HTMLCanvasElement.prototype.getContext).mock.results[0].value as CanvasRenderingContext2D
    const paint = vi.spyOn(context,'fillRect')
    vi.spyOn(window,'requestAnimationFrame').mockReturnValue(1)
    document.documentElement.dataset.theme = 'dark'
    await Promise.resolve()
    expect(paint).toHaveBeenCalled()
    expect(context.fillStyle).toBe('#24272d')
    expect(screen.queryByRole('button',{name:'保存蒙版'})).not.toBeInTheDocument()
    delete document.documentElement.dataset.theme
  })
  it('delegates undo and redo to persistent project history even while the last stroke is syncing', async () => {
    const boundary = vi.fn()
    useEditorStore.setState({view:'mask',tool:'brush'})
    const saved = vi.fn(async (value:TextRegion) => value)
    render(<MaskEditor page={page} region={region} regions={[region]} tool="brush" onToolChange={() => {}} onSave={saved}
      onSelectRegion={() => {}} onStateChange={() => {}} onHistoryBoundary={boundary}/>)
    await waitFor(() => expect(screen.getByRole('checkbox',{name:'原图参考'})).toBeEnabled())
    fireEvent.keyDown(document,{key:'z',ctrlKey:true})
    expect(boundary).toHaveBeenCalledWith('undo')
    boundary.mockClear()
    const canvas = screen.getByLabelText('蒙版编辑画布')
    fireEvent.pointerDown(canvas,{pointerId:1,clientX:400,clientY:300,button:0})
    fireEvent.pointerUp(canvas,{pointerId:1,clientX:400,clientY:300,button:0})
    await waitFor(() => expect(saved).toHaveBeenCalledOnce())
    fireEvent.keyDown(document,{key:'z',ctrlKey:true})
    expect(boundary).toHaveBeenCalledWith('undo')
    fireEvent.keyDown(document,{key:'z',ctrlKey:true,shiftKey:true})
    expect(boundary).toHaveBeenCalledWith('redo')
  })

  it('undos and redoes pixels immediately during slow saving without a boundary reload, while syncing real history in order', async () => {
    useEditorStore.setState({view:'mask',tool:'brush'})
    let saveDone!:(value:TextRegion) => void,undoDone!:(value:TextRegion) => void
    const saved=vi.fn(() => new Promise<TextRegion>(resolve => {saveDone=resolve}))
    const restored=vi.fn((direction:'undo'|'redo',value:TextRegion,_operationId:string) => direction === 'undo'
      ? new Promise<TextRegion>(resolve => {undoDone=resolve}) : Promise.resolve(value))
    const boundary=vi.fn()
    render(<Harness onSave={saved} onClose={() => {}} onHistoryStep={restored} onHistoryBoundary={boundary}/>)
    await waitFor(() => expect(screen.getByRole('checkbox',{name:'原图参考'})).toBeEnabled())
    const canvas=screen.getByLabelText('蒙版编辑画布')
    fireEvent.pointerDown(canvas,{pointerId:1,clientX:400,clientY:300,button:0})
    fireEvent.pointerUp(canvas,{pointerId:1,clientX:400,clientY:300,button:0})
    await waitFor(() => expect(saved).toHaveBeenCalledOnce())
    fireEvent.keyDown(document,{key:'z',ctrlKey:true})
    expect(screen.getByRole('button',{name:'重做'})).toBeEnabled()
    expect(screen.getByRole('button',{name:'撤销'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'蒙版画笔'})).toBeEnabled()
    expect(restored).not.toHaveBeenCalled()
    expect(boundary).not.toHaveBeenCalled()
    fireEvent.keyDown(document,{key:'y',ctrlKey:true})
    expect(screen.getByRole('button',{name:'撤销'})).toBeEnabled()
    expect(screen.getByRole('button',{name:'重做'})).toBeDisabled()
    saveDone(region)
    await waitFor(() => expect(restored).toHaveBeenCalledTimes(1))
    expect(restored.mock.calls[0][0]).toBe('undo')
    expect(screen.queryByText('任务完成前暂时不能进行其他操作')).not.toBeInTheDocument()
    undoDone(region)
    await waitFor(() => expect(restored).toHaveBeenCalledTimes(2))
    expect(restored.mock.calls.map(item => item[0])).toEqual(['undo','redo'])
    expect(saved).toHaveBeenCalledOnce()
  })

  it('keeps an optimistic undo visible on sync failure and retries the same operation identity', async () => {
    useEditorStore.setState({view:'mask',tool:'brush'})
    const restored=vi.fn(async (_direction:'undo'|'redo',value:TextRegion,_operationId:string) => value)
    restored.mockRejectedValueOnce(new Error('history offline'))
    render(<Harness onSave={async value => value} onClose={() => {}} onHistoryStep={restored}/>)
    await waitFor(() => expect(screen.getByRole('checkbox',{name:'原图参考'})).toBeEnabled())
    const canvas=screen.getByLabelText('蒙版编辑画布')
    fireEvent.pointerDown(canvas,{pointerId:1,clientX:400,clientY:300,button:0})
    fireEvent.pointerUp(canvas,{pointerId:1,clientX:400,clientY:300,button:0})
    fireEvent.keyDown(document,{key:'z',ctrlKey:true})
    await screen.findByText(/history offline/)
    expect(screen.getByRole('button',{name:'重做'})).toBeEnabled()
    expect(screen.getByRole('button',{name:'蒙版画笔'})).toBeEnabled()
    fireEvent.click(screen.getByRole('button',{name:'重试同步'}))
    await waitFor(() => expect(restored).toHaveBeenCalledTimes(2))
    expect(restored.mock.calls[0][2]).toBe(restored.mock.calls[1][2])
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('warms sparse saved checkpoints after reopening and undoes them without rebuilding the scene', async () => {
    const version='2026-10-13T15:00:00',current={...region,updated_at:version}
    const coverage=new Uint8Array(page.width*page.height);coverage[50*page.width+50]=255
    const before={x:50,y:50,width:1,height:1,pixels:new Uint8Array([0])}
    const after={...before,pixels:new Uint8Array([255])}
    vi.mocked(loadMaskHistory).mockResolvedValueOnce({region_updated_at:version,history:{project_id:'project',undo_id:1,redo_id:null,undo_count:1,redo_count:0,undo_label:'编辑蒙版',redo_label:null,pending:false},undo:[{before,after}],redo:[]})
    const restored=vi.fn(async () => current),boundary=vi.fn(),state=vi.fn()
    const mask=new MaskBitmap(page.width,page.height,coverage)
    render(<MaskEditor page={page} region={current} regions={[current]} prepared={{source:new Image(),mask,others:document.createElement('canvas')}} tool="brush"
      onToolChange={() => {}} onSave={vi.fn()} onSelectRegion={() => {}} onStateChange={state} onHistoryStep={restored} onHistoryBoundary={boundary}/>)
    await waitFor(() => expect(state.mock.lastCall![0].undo).toBe(true))
    fireEvent.keyDown(document,{key:'z',ctrlKey:true})
    expect(mask.pixels[50*page.width+50]).toBe(0)
    expect(boundary).not.toHaveBeenCalled()
    await waitFor(() => expect(restored).toHaveBeenCalledOnce())
  })
  it('handles wheel zoom without changing mask coverage or scrolling the page', async () => {
    const {canvas} = await setup()
    expect(fireEvent.wheel(canvas, {deltaY:-100,clientX:400,clientY:300,cancelable:true})).toBe(false)
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
  })
  it('pans from the mask toolbar without painting or selecting a region', async () => {
    const {canvas,down,onSelectRegion} = await setup()
    fireEvent.click(screen.getByRole('button', {name:'拖拽蒙版画布'}))
    expect(screen.getByRole('button', {name:'拖拽蒙版画布'})).toHaveAttribute('aria-pressed','true')
    expect(canvas).toHaveClass('cursor-grab')
    const translate = vi.spyOn((canvas as HTMLCanvasElement).getContext('2d')!, 'translate')
    await waitFor(() => expect(translate).toHaveBeenCalled())
    const [x,y] = translate.mock.lastCall!
    translate.mockClear()
    down()
    expect(canvas).toHaveStyle({cursor:'grabbing'})
    fireEvent.pointerMove(canvas,{pointerId:1,clientX:460,clientY:330})
    fireEvent.pointerUp(canvas,{pointerId:1,clientX:460,clientY:330})
    await waitFor(() => {
      expect(translate).toHaveBeenCalled()
      expect(translate.mock.lastCall![0]).toBeCloseTo(x+60)
      expect(translate.mock.lastCall![1]).toBeCloseTo(y+30)
    })
    expect((canvas as HTMLCanvasElement).style.cursor).toBe('')
    expect(onSelectRegion).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name:'撤销'})).toBeDisabled()
  })
  it('matches the original polygon preview with a yellow dashed edge and no extra cursor dot', async () => {
    const {canvas} = await setup()
    fireEvent.click(screen.getByRole('button', {name:'多边形补画蒙版'}))
    const context = (canvas as HTMLCanvasElement).getContext('2d')!
    const arc = vi.spyOn(context,'arc'), line = vi.spyOn(context,'lineTo'), dash = vi.spyOn(context,'setLineDash')
    const strokes: {color:typeof context.strokeStyle,width:number}[] = []
    const fills: typeof context.fillStyle[] = []
    vi.spyOn(context,'stroke').mockImplementation(() => {strokes.push({color:context.strokeStyle,width:context.lineWidth})})
    vi.spyOn(context,'fill').mockImplementation(() => {fills.push(context.fillStyle)})
    fireEvent.pointerDown(canvas,{pointerId:1,clientX:360,clientY:260,button:0})
    fireEvent.pointerUp(canvas,{pointerId:1,clientX:360,clientY:260,button:0})
    fireEvent.pointerMove(canvas,{pointerId:1,clientX:420,clientY:310})
    const scale = 456/140, x = 400-50*scale, y = 292-60*scale
    const px = (420-x)/scale, py = (310-y)/scale
    await waitFor(() => expect(line).toHaveBeenCalledWith(px,py))
    expect(arc).not.toHaveBeenCalledWith(px,py,3.5/scale,0,Math.PI*2)
    expect(strokes).toContainEqual({color:polygonDraftStyle.stroke,width:2/scale})
    expect(strokes).toContainEqual({color:polygonDraftStyle.stroke,width:1.5/scale})
    expect(strokes).toContainEqual({color:polygonDraftStyle.firstPointBorder,width:1.5/scale})
    expect(fills).toContain(polygonDraftStyle.pointFill)
    expect(dash).toHaveBeenCalledWith([6/scale,4/scale])
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    const clear = vi.spyOn(context,'clearRect'); line.mockClear()
    fireEvent.pointerLeave(canvas)
    await waitFor(() => expect(clear).toHaveBeenCalled())
    expect(line).not.toHaveBeenCalledWith(px,py)
  })
  it.each([false,true])('highlights and closes at the same 12px snap target after zoom=%s', async zoomed => {
    const {canvas} = await setup()
    fireEvent.click(screen.getByRole('button', {name:'多边形补画蒙版'}))
    const context = (canvas as HTMLCanvasElement).getContext('2d')!
    const hint = vi.spyOn(context,'fillText')
    const arc = vi.spyOn(context,'arc')
    const fills: typeof context.fillStyle[] = []
    vi.spyOn(context,'fill').mockImplementation(() => {fills.push(context.fillStyle)})
    for (const [clientX,clientY] of [[360,260],[420,260],[420,330]]) {
      fireEvent.pointerDown(canvas,{pointerId:1,clientX,clientY,button:0})
      fireEvent.pointerUp(canvas,{pointerId:1,clientX,clientY,button:0})
    }
    if (zoomed) fireEvent.click(screen.getByRole('button', {name:'放大蒙版画布'}))
    const first = zoomed ? [350,250] : [360,260]
    // Inside the fixed target at both zoom levels, but not exactly on the vertex.
    const event = {pointerId:1,clientX:first[0]+11,clientY:first[1],button:0}
    fireEvent.pointerMove(canvas,event)
    await waitFor(() => expect(fills).toContain(polygonDraftStyle.selected))
    const scale = 456/140 * (zoomed ? 1.25 : 1)
    expect(arc.mock.calls.every(call => call[2] === 5/scale || call[2] === 3.5/scale)).toBe(true)
    expect(hint).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    fireEvent.pointerDown(canvas,event); fireEvent.pointerUp(canvas,event)
    expect(screen.getByRole('button', {name:'撤销'})).toBeEnabled()
    hint.mockClear()
    const clear = vi.spyOn(context,'clearRect')
    fireEvent.pointerMove(canvas,{pointerId:1,clientX:400,clientY:300})
    await waitFor(() => expect(clear).toHaveBeenCalled())
    expect(hint).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', {name:'撤销'}))
    expect(screen.getByRole('button', {name:'重做'})).toBeEnabled()
  })
  it('does not close incomplete polygons or count duplicate clicks as vertices', async () => {
    const {canvas} = await setup()
    fireEvent.click(screen.getByRole('button', {name:'多边形补画蒙版'}))
    const context = (canvas as HTMLCanvasElement).getContext('2d')!
    const fills: typeof context.fillStyle[] = []
    vi.spyOn(context,'fill').mockImplementation(() => {fills.push(context.fillStyle)})
    for (const [clientX,clientY] of [[360,260],[420,260],[420,260]]) {
      fireEvent.pointerDown(canvas,{pointerId:1,clientX,clientY,button:0})
      fireEvent.pointerUp(canvas,{pointerId:1,clientX,clientY,button:0})
    }
    fireEvent.keyDown(document,{key:'Enter'})
    fireEvent.pointerMove(canvas,{pointerId:1,clientX:360,clientY:260})
    const clear = vi.spyOn((canvas as HTMLCanvasElement).getContext('2d')!,'clearRect')
    await waitFor(() => expect(clear).toHaveBeenCalled())
    expect(fills).not.toContain(polygonDraftStyle.selected)
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    // Enter with fewer than three vertices preserves the draft so drawing can continue.
    fireEvent.pointerDown(canvas,{pointerId:1,clientX:420,clientY:330,button:0})
    fireEvent.pointerUp(canvas,{pointerId:1,clientX:420,clientY:330,button:0})
    fireEvent.pointerMove(canvas,{pointerId:1,clientX:360,clientY:260})
    await waitFor(() => expect(fills).toContain(polygonDraftStyle.selected))
    fireEvent.keyDown(document,{key:'Escape'})
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    fills.length = 0; clear.mockClear()
    await waitFor(() => expect(clear).toHaveBeenCalled())
    expect(fills).toHaveLength(0)
  })
  it('applies the opacity slider only to the original reference, not the masks', async () => {
    const {canvas} = await setup()
    const context = (canvas as HTMLCanvasElement).getContext('2d')!
    const draws: {args:number,alpha:number}[] = []
    vi.spyOn(context, 'drawImage').mockImplementation(function (this: CanvasRenderingContext2D, ...args: unknown[]) {draws.push({args:args.length,alpha:this.globalAlpha})})
    fireEvent.click(screen.getByRole('checkbox', {name:'原图参考'}))
    fireEvent.click(screen.getByRole('checkbox', {name:'原图参考'}))
    await waitFor(() => expect(draws).toContainEqual({args:5,alpha:.3}))
    draws.length = 0
    fireEvent.change(screen.getByRole('slider', {name:'原图参考不透明度'}),{target:{value:'80'}})
    await waitFor(() => expect(draws).toContainEqual({args:5,alpha:.8}))
    expect(draws.filter(draw => draw.args === 3).map(draw => draw.alpha)).toEqual([.75,.45])
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
  })
  it('keeps strokes on autosave failure and retries without a bottom save or discard tray', async () => {
    const persist=vi.fn(async (value:TextRegion) => value)
    persist.mockRejectedValueOnce(new Error('保存失败，请重试'))
    const {down,up,onSave,onClose} = await setup(persist)
    down(); up()
    expect(await screen.findByRole('alert')).toHaveTextContent('保存失败，请重试')
    expect(onSave).toHaveBeenCalledOnce(); expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('button', {name:'撤销'})).toBeEnabled()
    fireEvent.click(screen.getByRole('button', {name:'重试同步'}))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.queryByText('放弃尚未保存的蒙版修改？')).not.toBeInTheDocument()
  })
  it('cancels the initial brush mark when a second pointer starts a pinch', async () => {
    const {down,up} = await setup()
    down(1); down(2); up(2); up(1)
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name:'撤销'})).toBeDisabled()
  })
  it('automatically persists each completed stroke, local undo and redo without a save button', async () => {
    const {down,up,onSave,onClose} = await setup()
    down(); up()
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce())
    fireEvent.click(screen.getByRole('button', {name:'撤销'}))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2))
    fireEvent.click(screen.getByRole('button', {name:'重做'}))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(3))
    expect(onClose).not.toHaveBeenCalled()
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({id:region.id}), expect.any(Blob),expect.objectContaining({x:expect.any(Number),y:expect.any(Number)}))
    expect(useEditorStore.getState().view).toBe('mask')
  })
  it('draws and erases using the workbench tools without modifying the source', async () => {
    const {down,up} = await setup()
    down(); up()
    fireEvent.click(screen.getByRole('button', {name:'蒙版橡皮擦'}))
    down(); up()
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name:'蒙版橡皮擦'})).toHaveAttribute('aria-pressed','true')
  })
  it('fills a rectangle with the existing geometry tool and supports undo', async () => {
    const {canvas,down,up} = await setup()
    fireEvent.click(screen.getByRole('button', {name:'矩形补画蒙版'}))
    down(); fireEvent.pointerMove(canvas,{pointerId:1,clientX:430,clientY:350}); up()
    expect(screen.getByRole('button', {name:'撤销'})).toBeEnabled()
    fireEvent.click(screen.getByRole('button', {name:'撤销'}))
    expect(screen.getByRole('button', {name:'重做'})).toBeEnabled()
  })
  it('does not paint a locked region', async () => {
    const {down,up} = await setup(undefined,true)
    down(); up()
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name:'撤销'})).toBeDisabled()
  })
  it.each(['多边形','套索'])('fills and undoes a %s mask shape', async name => {
    const {canvas} = await setup()
    fireEvent.click(screen.getByRole('button', {name:`${name}补画蒙版`}))
    const points = [[360,260],[420,260],[420,330],[360,330]]
    points.forEach(([clientX,clientY], index) => {
      const event = {pointerId:1,clientX,clientY,button:0}
      if (name === '多边形') {fireEvent.pointerDown(canvas,event); fireEvent.pointerUp(canvas,event)}
      else if (!index) fireEvent.pointerDown(canvas,event)
      else fireEvent.pointerMove(canvas,event)
    })
    if (name === '多边形') fireEvent.keyDown(document,{key:'Enter'})
    else fireEvent.pointerUp(canvas,{pointerId:1,clientX:360,clientY:330,button:0})
    expect(screen.getByRole('button', {name:'撤销'})).toBeEnabled()
    fireEvent.click(screen.getByRole('button', {name:'撤销'}))
    expect(screen.getByRole('button', {name:'重做'})).toBeEnabled()
  })
  it('keeps a single control panel in the inspector and presets do not change mask pixels', async () => {
    await setup()
    const panel = screen.getByTestId('mask-properties')
    const sliders = screen.getAllByRole('slider')
    expect(sliders).toHaveLength(2)
    sliders.forEach(slider => expect(panel).toContainElement(slider))
    fireEvent.click(screen.getByRole('button', {name:'精细'}))
    expect(screen.getByRole('slider', {name:'蒙版笔刷大小'})).toHaveValue('8')
    expect(screen.getByRole('button', {name:'精细'})).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('checkbox', {name:'原图参考'}))
    expect(screen.getByRole('checkbox', {name:'原图参考'})).not.toBeChecked()
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
  })
  it('switches tool-specific settings without discarding strokes', async () => {
    const {down,up} = await setup()
    down(); up()
    fireEvent.click(screen.getByRole('button', {name:'矩形补画蒙版'}))
    expect(screen.queryByRole('slider', {name:'蒙版笔刷大小'})).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name:'撤销'})).toBeEnabled()
    fireEvent.click(screen.getByRole('button', {name:'蒙版橡皮擦'}))
    expect(screen.getByRole('button', {name:'矩形擦除蒙版'})).toHaveAttribute('aria-pressed','true')
    fireEvent.click(screen.getByRole('button', {name:'自由画笔'}))
    expect(screen.getByRole('slider', {name:'蒙版笔刷大小'})).toHaveValue('24')
    expect(screen.getByRole('button', {name:'撤销'})).toBeEnabled()
  })

  it.each(['矩形','多边形','套索'])('keeps add/erase independent of the %s shape and restores exact pixels on undo/redo', async name => {
    const fill = vi.spyOn(MaskBitmap.prototype,'fillPolygon')
    const {canvas,onSave} = await setup()
    const draw = () => {
      if (name === '矩形') {
        fireEvent.pointerDown(canvas,{pointerId:1,clientX:360,clientY:260,button:0})
        fireEvent.pointerMove(canvas,{pointerId:1,clientX:420,clientY:330})
        fireEvent.pointerUp(canvas,{pointerId:1,clientX:420,clientY:330,button:0})
        return
      }
      for (const [index,[clientX,clientY]] of [[360,260],[420,260],[420,330],[360,330]].entries()) {
        const event={pointerId:1,clientX,clientY,button:0}
        if (name === '多边形') {fireEvent.pointerDown(canvas,event);fireEvent.pointerUp(canvas,event)}
        else if (!index) fireEvent.pointerDown(canvas,event)
        else fireEvent.pointerMove(canvas,event)
      }
      if (name === '多边形') fireEvent.keyDown(document,{key:'Enter'})
      else fireEvent.pointerUp(canvas,{pointerId:1,clientX:360,clientY:330,button:0})
    }
    // The reported sequence: eraser first, then a shape. Empty masks must
    // remain empty rather than accidentally filling the selected shape.
    fireEvent.click(screen.getByRole('button',{name:'蒙版橡皮擦'}))
    fireEvent.click(screen.getByRole('button',{name:`${name}擦除蒙版`}))
    draw()
    expect(fill.mock.lastCall![1]).toBe('eraser')
    const bitmap=fill.mock.contexts.at(-1) as MaskBitmap
    expect(bitmap.pixels.some(Boolean)).toBe(false)
    expect(screen.getByRole('button',{name:'撤销'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'蒙版橡皮擦'})).toHaveAttribute('aria-pressed','true')
    // Switching the operation preserves the shape, with actual add/erase
    // pixels following that operation instead of just changing the icon.
    fireEvent.click(screen.getByRole('button',{name:'蒙版画笔'}))
    expect(screen.getByRole('button',{name:`${name}补画蒙版`})).toHaveAttribute('aria-pressed','true')
    draw()
    const painted=bitmap.pixels.slice()
    expect(painted.some(Boolean)).toBe(true)
    fireEvent.click(screen.getByRole('button',{name:'蒙版橡皮擦'}))
    draw()
    expect(bitmap.pixels.some(Boolean)).toBe(false)
    fireEvent.click(screen.getByRole('button',{name:'撤销'}))
    expect(bitmap.pixels).toEqual(painted)
    fireEvent.click(screen.getByRole('button',{name:'重做'}))
    expect(bitmap.pixels.some(Boolean)).toBe(false)
    fireEvent.click(screen.getByRole('button',{name:'自由画笔'}))
    expect(useEditorStore.getState().tool).toBe('eraser')
    expect(useEditorStore.getState().maskOperation).toBe('eraser')
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(4))
  })

  it('cancels an unfinished polygon when changing add/erase instead of applying a draft in the wrong mode', async () => {
    const fill=vi.spyOn(MaskBitmap.prototype,'fillPolygon')
    const {canvas}=await setup()
    fireEvent.click(screen.getByRole('button',{name:'多边形补画蒙版'}))
    for (const [clientX,clientY] of [[360,260],[420,260],[420,330]]) {
      fireEvent.pointerDown(canvas,{pointerId:1,clientX,clientY,button:0})
      fireEvent.pointerUp(canvas,{pointerId:1,clientX,clientY,button:0})
    }
    fireEvent.click(screen.getByRole('button',{name:'蒙版橡皮擦'}))
    fireEvent.keyDown(document,{key:'Enter'})
    expect(fill).not.toHaveBeenCalled()
    expect(screen.getByRole('button',{name:'多边形擦除蒙版'})).toHaveAttribute('aria-pressed','true')
  })

  it('shows only mask tools and selects regions without painting', async () => {
    const {down,up,onSelectRegion} = await setup()
    expect(screen.queryByRole('button', {name:'选择 / 变换'})).not.toBeInTheDocument()
    expect(screen.queryByRole('button', {name:'矩形区域'})).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', {name:'选择蒙版区域'}))
    down(); up()
    expect(onSelectRegion).toHaveBeenCalledWith('neighbor')
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
  })

  it('waits for autosave before switching regions instead of asking to discard', async () => {
    let finish!: (value:TextRegion) => void
    const pending=new Promise<TextRegion>(resolve => {finish=resolve})
    const {down,up,onSelectRegion,onSave} = await setup(vi.fn(() => pending))
    down(); up()
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce())
    fireEvent.click(screen.getByRole('button', {name:'选择蒙版区域'}))
    down(); up()
    expect(onSelectRegion).not.toHaveBeenCalled()
    expect(screen.queryByText('放弃尚未保存的蒙版修改？')).not.toBeInTheDocument()
    finish(region)
    await waitFor(() => expect(onSelectRegion).toHaveBeenCalledWith('neighbor'))
  })
  it('saves separate snapshots for rapid strokes and chains the returned region version', async () => {
    let finish!: (value:TextRegion) => void
    const first=new Promise<TextRegion>(resolve => {finish=resolve})
    const savedRegion={...region,updated_at:'new-version',mask_url:'/saved.png'}
    const save=vi.fn(async (value:TextRegion,_blob:Blob) => value)
    save.mockReturnValueOnce(first)
    const {down,up,canvas}=await setup(save)
    down(); up()
    await waitFor(() => expect(save).toHaveBeenCalledOnce())
    fireEvent.pointerDown(canvas,{pointerId:1,clientX:430,clientY:340,button:0})
    fireEvent.pointerUp(canvas,{pointerId:1,clientX:430,clientY:340,button:0})
    expect(save).toHaveBeenCalledOnce()
    finish(savedRegion)
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    expect(save.mock.calls[1][0].updated_at).toBe('new-version')
    expect(save.mock.calls[1][0].mask_url).toBe('/saved.png')
    expect(screen.queryByRole('button',{name:'保存蒙版'})).not.toBeInTheDocument()
  })
  it('does not leave a failed autosave behind when changing view', async () => {
    const save=vi.fn(async () => {throw new Error('offline')})
    const {down,up,onClose}=await setup(save)
    down(); up()
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button',{name:'原图'}))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('修改仍保留')
  })
  it('retains reference and brush preferences across keyed region sessions', async () => {
    const preferences = {current:{diameter:24,referenceOpacity:30,showOriginal:false}}
    const props = {page,region,regions:[region],tool:'brush' as const,onToolChange:vi.fn(),onSave:vi.fn(),onSelectRegion:vi.fn(),onStateChange:vi.fn(),preferences}
    const {rerender} = render(<MaskEditor key="first" {...props}/>)
    await waitFor(() => expect(screen.getByRole('checkbox', {name:'原图参考'})).toBeEnabled())
    fireEvent.click(screen.getByRole('checkbox', {name:'原图参考'}))
    fireEvent.change(screen.getByRole('slider', {name:'原图参考不透明度'}),{target:{value:'80'}})
    fireEvent.click(screen.getByRole('button', {name:'精细'}))
    rerender(<MaskEditor key="second" {...props}/>)
    await waitFor(() => expect(screen.getByRole('checkbox', {name:'原图参考'})).toBeEnabled())
    expect(screen.getByRole('checkbox', {name:'原图参考'})).toBeChecked()
    expect(screen.getByRole('slider', {name:'原图参考不透明度'})).toHaveValue('80')
    expect(screen.getByRole('slider', {name:'蒙版笔刷大小'})).toHaveValue('8')
    expect(screen.queryByRole('button', {name:'保存蒙版'})).not.toBeInTheDocument()
  })
})
