import {act, fireEvent, render, screen} from '@testing-library/react'
import {forwardRef, type ReactNode} from 'react'
import type Konva from 'konva'
import {afterAll, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import type {ImagePage, TextRegion} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'

const {stageProps, frameProps, outlineProps} = vi.hoisted(() => ({stageProps:vi.fn(),frameProps:vi.fn(),outlineProps:vi.fn()}))
type CanvasEvent = Konva.KonvaEventObject<MouseEvent | TouchEvent>
type Handlers = {
  onMouseDown:(event:CanvasEvent) => void
  onMouseMove:(event:CanvasEvent) => void
  onMouseUp:(event?:CanvasEvent) => Promise<void>
  onClick:(event:CanvasEvent) => void
  onTouchStart:(event:CanvasEvent) => void
  onTouchEnd:(event?:CanvasEvent) => Promise<void>
  onTap:(event:CanvasEvent) => void
}
type OutlineHandlers = {
  listening:boolean
  onMouseDown?:(event:CanvasEvent) => void
  onTouchStart?:(event:CanvasEvent) => void
  onClick:(event:CanvasEvent) => void
  onTap:(event:CanvasEvent) => void
}
vi.mock('konva', () => ({default:{}}))
vi.mock('react-konva', () => {
  const Group=forwardRef(function MockGroup(props:{children?:ReactNode,x?:number,y?:number,scaleX?:number,scaleY?:number,listening?:boolean},_ref) {
    if (props.scaleX && props.listening!==false) frameProps(props)
    return <div>{props.children}</div>
  })
  const Stage=(props:Handlers & {children?:ReactNode}) => {
    stageProps(props)
    return <div data-testid="stage">{props.children}</div>
  }
  const Container=({children}:{children?:ReactNode}) => <div>{children}</div>
  const Empty=() => null
  const Rect=({stroke}:{stroke?:string})=>stroke==='#ffcb45'?<div data-testid="rectangle-draft"/>:null
  const Line=(props:Partial<OutlineHandlers> & {closed?:boolean})=>{
    if(props.closed && props.onClick) outlineProps(props)
    return !props.closed ? <div data-testid="polygon-draft"/> : null
  }
  return {Stage,Group,Layer:Container,Circle:Empty,Image:Empty,Line,Rect,Text:Empty}
})
vi.mock('../hooks/useImage',()=>({useImage:()=>({image:null,loading:false,error:false})}))
vi.mock('./RegionPolygonEditor',()=>({RegionPolygonEditor:()=>null}))

const page={id:'page',width:300,height:300,original_url:'/page.png'} as ImagePage
const existingRegion={id:'existing',region_key:'R001',bbox:[40,40,80,80],polygon:[[40,40],[120,40],[120,120],[40,120]],
  translated_bbox:[140,140,80,80],translated_polygon:[[140,140],[220,140],[220,220],[140,220]],visible:true,locked:false,translated_text:'',layout_data:{}} as TextRegion
const callbacks=()=>({page,regions:[] as TextRegion[],onCreate:vi.fn(async (_polygon:number[][],_bbox:number[])=>true),
  onUpdate:vi.fn(async()=>undefined),onRegionAction:vi.fn(),runningAction:null,interactionKey:'creation'})
const handlers=()=>stageProps.mock.lastCall![0] as Handlers
const outline=()=>outlineProps.mock.lastCall![0] as OutlineHandlers
function pointer(x:number,y:number,type:string,buttons=1,targetName='canvas-background'):CanvasEvent {
  const frame=frameProps.mock.lastCall![0] as {x:number,y:number,scaleX:number,scaleY:number}
  const stage={getPointerPosition:()=>({x:frame.x+x*frame.scaleX,y:frame.y+y*frame.scaleY})}
  const target={getStage:()=>stage,name:()=>targetName}
  return {target,evt:new MouseEvent(type,{button:0,buttons}),cancelBubble:false} as unknown as CanvasEvent
}
function begin(x=20,y=20) {act(()=>handlers().onMouseDown(pointer(x,y,'mousedown')))}
function move(x:number,y:number,buttons=1) {act(()=>handlers().onMouseMove(pointer(x,y,'mousemove',buttons)))}
async function finish() {await act(async()=>handlers().onMouseUp())}
function expectCoordinates(actual:number[],expected:number[]) {
  expect(actual).toHaveLength(expected.length)
  actual.forEach((value,index)=>expect(value).toBeCloseTo(expected[index],8))
}
async function clickExisting(touch=false,button=0) {
  const down={...pointer(60,60,'mousedown',1,'region-outline'),evt:touch ? new Event('touchstart') as TouchEvent : new MouseEvent('mousedown',{button})}
  act(()=>{
    if(touch) outline().onTouchStart?.(down)
    else outline().onMouseDown?.(down)
    if(!down.cancelBubble) (touch ? handlers().onTouchStart : handlers().onMouseDown)(down)
  })
  await act(async()=>{
    const up={...pointer(60,60,'mouseup',0,'region-outline'),evt:touch ? new Event('touchend') as TouchEvent : new MouseEvent('mouseup',{button})}
    await (touch ? handlers().onTouchEnd : handlers().onMouseUp)(up)
  })
  const click={...pointer(60,60,'click',0,'region-outline'),evt:touch ? new Event('touchend') as TouchEvent : new MouseEvent('click',{button,altKey:true})}
  act(()=>{
    (touch ? outline().onTap : outline().onClick)(click)
    if(!click.cancelBubble) (touch ? handlers().onTap : handlers().onClick)(click)
  })
  return down
}

describe('bounded text-box creation gestures',()=>{
  beforeAll(()=>vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}}))
  afterAll(()=>vi.unstubAllGlobals())
  beforeEach(()=>{
    stageProps.mockClear();frameProps.mockClear();outlineProps.mockClear()
    useEditorStore.setState({view:'original',tool:'rectangle',zoom:1,selectedIds:[],
      layers:{original:true,detection:true,clean:false,translated:false,mask:false}})
  })

  it.each(['rectangle','polygon','lasso'] as const)('switches %s to single selection when clicking an existing region',async tool=>{
    useEditorStore.setState({tool,selectedIds:['existing','other']})
    const props=callbacks();props.regions=[existingRegion]
    render(<MangaCanvas {...props}/>)
    expect(outline().listening).toBe(true)
    const down=await clickExisting()
    expect(down.cancelBubble).toBe(true)
    expect(useEditorStore.getState().tool).toBe('select')
    expect(useEditorStore.getState().selectedIds).toEqual(['existing'])
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onUpdate).not.toHaveBeenCalled()
  })

  it.each(['rectangle','polygon','lasso'] as const)('switches %s to selection with a touch tap too',async tool=>{
    useEditorStore.setState({tool})
    const props=callbacks();props.regions=[existingRegion]
    render(<MangaCanvas {...props}/>)
    expect(outline().listening).toBe(true)
    const down=await clickExisting(true)
    expect(down.cancelBubble).toBe(true)
    expect(useEditorStore.getState().tool).toBe('select')
    expect(useEditorStore.getState().selectedIds).toEqual(['existing'])
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it.each([
    {view:'original',locked:false,readOnly:false},
    {view:'translated',locked:false,readOnly:false},
    {view:'original',locked:true,readOnly:false},
    {view:'original',locked:false,readOnly:true},
  ] as const)('can select a previously unselected region in creation mode (%j)',async({view,locked,readOnly})=>{
    useEditorStore.setState({tool:'polygon',view,selectedIds:['other']})
    const props=callbacks();props.regions=[{...existingRegion,locked}]
    render(<MangaCanvas {...props} readOnly={readOnly}/>)
    await clickExisting()
    expect(useEditorStore.getState().tool).toBe('select')
    expect(useEditorStore.getState().selectedIds).toEqual(['existing'])
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onUpdate).not.toHaveBeenCalled()
  })

  it('discards a polygon draft when selecting a region and starts fresh if polygon is chosen again',async()=>{
    useEditorStore.setState({tool:'polygon'})
    const props=callbacks();props.regions=[existingRegion]
    render(<MangaCanvas {...props}/>)
    for(const [x,y] of [[180,40],[240,40],[240,120]]) act(()=>handlers().onClick(pointer(x,y,'click')))
    expect(screen.queryAllByTestId('polygon-draft')).not.toHaveLength(0)
    await clickExisting()
    expect(useEditorStore.getState().tool).toBe('select')
    expect(screen.queryAllByTestId('polygon-draft')).toHaveLength(0)
    expect(props.onCreate).not.toHaveBeenCalled()
    act(()=>useEditorStore.getState().setTool('polygon'))
    for(const [x,y] of [[180,160],[240,160],[240,240],[180,160]]) {
      await act(async()=>handlers().onClick(pointer(x,y,'click')))
    }
    expect(props.onCreate).toHaveBeenCalledOnce()
    expectCoordinates(props.onCreate.mock.calls[0][0].flat(),[180,160,240,160,240,240])
    expect(useEditorStore.getState().tool).toBe('polygon')
  })

  it('does not start a creation drag from an existing region',async()=>{
    const props=callbacks();props.regions=[existingRegion]
    render(<MangaCanvas {...props}/>)
    const down=pointer(60,60,'mousedown',1,'region-outline')
    act(()=>{
      outline().onMouseDown?.(down)
      if(!down.cancelBubble) handlers().onMouseDown(down)
    })
    move(100,110);await finish()
    expect(down.cancelBubble).toBe(true)
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it('still creates across existing regions when the drag starts on blank canvas',async()=>{
    const props=callbacks();props.regions=[existingRegion]
    render(<MangaCanvas {...props}/>)
    begin(20,20);move(100,110);await finish()
    expect(props.onCreate).toHaveBeenCalledOnce()
    expectCoordinates(props.onCreate.mock.calls[0][1],[20,20,80,90])
    expect(useEditorStore.getState().tool).toBe('rectangle')
  })

  it.each([1,2])('does not switch tools on a non-primary region click (button=%s)',async button=>{
    useEditorStore.setState({tool:'polygon'})
    const props=callbacks();props.regions=[existingRegion]
    render(<MangaCanvas {...props}/>)
    await clickExisting(false,button)
    expect(useEditorStore.getState().tool).toBe('polygon')
    expect(useEditorStore.getState().selectedIds).toEqual([])
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it('does not create frames in read-only mode even if a drawing tool was already active',async()=>{
    const props=callbacks();const {rerender}=render(<MangaCanvas {...props}/>)
    begin();move(80,100)
    rerender(<MangaCanvas {...props} readOnly/>)
    await finish()
    begin();move(90,110);await finish()
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it.each(['rectangle','lasso'] as const)('allows repeated %s creation without choosing the tool again',async tool=>{
    useEditorStore.setState({tool})
    const props=callbacks();render(<MangaCanvas {...props}/>)
    for(const start of [20,140]) {
      begin(start,start);move(start+50,start);move(start+50,start+80)
      if(tool==='lasso')move(start,start+80)
      await finish()
      expect(useEditorStore.getState().tool).toBe(tool)
    }
    expect(props.onCreate).toHaveBeenCalledTimes(2)
    expectCoordinates(props.onCreate.mock.calls[0][1],[20,20,50,80])
    expectCoordinates(props.onCreate.mock.calls[1][1],[140,140,50,80])
  })

  it('closes consecutive polygons and starts a fresh draft with the same tool',async()=>{
    useEditorStore.setState({tool:'polygon'})
    const props=callbacks();render(<MangaCanvas {...props}/>)
    for(const start of [20,140]) {
      for(const [x,y] of [[start,start],[start+50,start],[start+50,start+80],[start,start]]) {
        await act(async()=>handlers().onClick(pointer(x,y,'click')))
      }
      expect(useEditorStore.getState().tool).toBe('polygon')
    }
    expect(props.onCreate).toHaveBeenCalledTimes(2)
    expectCoordinates(props.onCreate.mock.calls[0][0].flat(),[20,20,70,20,70,100])
    expectCoordinates(props.onCreate.mock.calls[1][0].flat(),[140,140,190,140,190,220])
  })

  it.each(['rectangle','lasso'] as const)('ignores %s gestures starting in any gray margin',async tool=>{
    useEditorStore.setState({tool})
    const props=callbacks();render(<MangaCanvas {...props}/>)
    for (const [x,y] of [[-20,20],[320,20],[20,-20],[20,320]]) {
      begin(x,y);move(60,20);move(60,80);move(20,80);await finish()
    }
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it.each([[-40,-40,0,0],[340,-40,150,0],[-40,340,0,150],[340,340,150,150]])(
    'clamps a rectangle dragged toward (%s, %s) to the image',async(x,y,left,top)=>{
      const props=callbacks();render(<MangaCanvas {...props}/>)
      begin(150,150);move(x,y);await finish()
      expect(props.onCreate).toHaveBeenCalledOnce()
      const [polygon,bbox]=props.onCreate.mock.calls[0]
      bbox.forEach((value,index)=>expect(value).toBeCloseTo([left,top,150,150][index]))
      polygon.flat().forEach(value=>{expect(value).toBeGreaterThanOrEqual(0);expect(value).toBeLessThanOrEqual(300)})
    })

  it('clamps lasso vertices and keeps its box consistent with the submitted polygon',async()=>{
    useEditorStore.setState({tool:'lasso'})
    const props=callbacks();render(<MangaCanvas {...props}/>)
    begin();move(340,20);move(340,340);move(-40,340);move(-40,-40);await finish()
    expect(props.onCreate).toHaveBeenCalledOnce()
    expectCoordinates(props.onCreate.mock.calls[0][0].flat(),[20,20,300,20,300,300,0,300,0,0])
    expectCoordinates(props.onCreate.mock.calls[0][1],[0,0,300,300])
  })

  it('does not submit a lasso collapsed to an image edge',async()=>{
    useEditorStore.setState({tool:'lasso'})
    const props=callbacks();render(<MangaCanvas {...props}/>)
    begin(0,20);move(-30,40);move(-30,60);move(-30,80);await finish()
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it.each(['rectangle','lasso'] as const)('cancels %s on an outside mouse release without a ghost gesture',async tool=>{
    useEditorStore.setState({tool})
    const props=callbacks();render(<MangaCanvas {...props}/>)
    begin();move(60,20);move(60,80);move(20,80)
    fireEvent.mouseUp(window,{button:0})
    move(150,160,0);await finish()
    expect(props.onCreate).not.toHaveBeenCalled()
    begin();move(60,20);move(60,80)
    if (tool === 'lasso') move(20,80)
    await finish()
    expect(props.onCreate).toHaveBeenCalledOnce()
  })

  it.each(['blur','touchcancel','pointercancel'])('cancels an unfinished gesture on %s',async type=>{
    const props=callbacks();render(<MangaCanvas {...props}/>)
    begin();move(60,80)
    fireEvent(window,new Event(type))
    move(100,120);await finish()
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it('cancels a touch gesture released outside the canvas',async()=>{
    const props=callbacks();render(<MangaCanvas {...props}/>)
    const touch=(x:number,y:number,type:string)=>({...pointer(x,y,type),evt:new Event(type) as TouchEvent})
    act(()=>handlers().onMouseDown(touch(20,20,'touchstart')))
    act(()=>handlers().onMouseMove(touch(60,80,'touchmove')))
    fireEvent.touchEnd(window)
    act(()=>handlers().onMouseMove(touch(100,120,'touchmove')))
    await finish()
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it.each(['tool','page','comparison'] as const)('discards an unfinished gesture after changing %s',async change=>{
    const props=callbacks();const {rerender}=render(<MangaCanvas {...props}/>)
    begin();move(60,80)
    if (change==='page') rerender(<MangaCanvas {...props} page={{...page,id:'other-page'}}/>)
    else if (change==='tool') act(()=>useEditorStore.setState({tool:'lasso'}))
    else act(()=>useEditorStore.setState({view:'comparison'}))
    await finish()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(screen.queryByTestId('rectangle-draft')).not.toBeInTheDocument()
  })

  it('cancels drawing when the mouse returns without its left button held',async()=>{
    const props=callbacks();render(<MangaCanvas {...props}/>)
    begin();move(60,80);move(100,120,0);await finish()
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it('submits only once and keeps its draft while a successful creation is pending',async()=>{
    const props=callbacks()
    let resolve!:(saved:boolean)=>void
    props.onCreate.mockImplementation(()=>new Promise<boolean>(done=>{resolve=done}))
    render(<MangaCanvas {...props}/>)
    begin();move(60,80)
    let saving!:Promise<void>
    act(()=>{saving=handlers().onMouseUp()})
    fireEvent.mouseUp(screen.getByTestId('stage'),{button:0})
    fireEvent.mouseUp(window,{button:0})
    await finish()
    expect(props.onCreate).toHaveBeenCalledOnce()
    expectCoordinates(props.onCreate.mock.calls[0][1],[20,20,40,60])
    expect(screen.getByTestId('rectangle-draft')).toBeInTheDocument()
    await act(async()=>{resolve(true);await saving})
    expect(screen.queryByTestId('rectangle-draft')).not.toBeInTheDocument()
  })

  it('does not finish a left-button gesture when the right button is released',async()=>{
    const props=callbacks();render(<MangaCanvas {...props}/>)
    begin();move(60,80)
    const event={...pointer(60,80,'mouseup'),evt:new MouseEvent('mouseup',{button:2})}
    await act(async()=>handlers().onMouseUp(event))
    fireEvent.mouseUp(window,{button:2})
    expect(props.onCreate).not.toHaveBeenCalled()
    await finish()
    expect(props.onCreate).toHaveBeenCalledOnce()
  })

  it('uses the latest move even when pointer events share a React batch',async()=>{
    const props=callbacks();render(<MangaCanvas {...props}/>)
    await act(async()=>{
      handlers().onMouseDown(pointer(20,20,'mousedown'))
      handlers().onMouseMove(pointer(60,80,'mousemove'))
      await handlers().onMouseUp()
    })
    expect(props.onCreate).toHaveBeenCalledOnce()
    expectCoordinates(props.onCreate.mock.calls[0][0].flat(),[20,20,60,20,60,80,20,80])
    expectCoordinates(props.onCreate.mock.calls[0][1],[20,20,40,60])
  })
})
