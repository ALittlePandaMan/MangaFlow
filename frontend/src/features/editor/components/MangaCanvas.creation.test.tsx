import {act, fireEvent, render, screen} from '@testing-library/react'
import {forwardRef, type ReactNode} from 'react'
import type Konva from 'konva'
import {afterAll, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import type {ImagePage} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'

const {stageProps, frameProps} = vi.hoisted(() => ({stageProps:vi.fn(),frameProps:vi.fn()}))
type CanvasEvent = Konva.KonvaEventObject<MouseEvent | TouchEvent>
type Handlers = {
  onMouseDown:(event:CanvasEvent) => void
  onMouseMove:(event:CanvasEvent) => void
  onMouseUp:(event?:CanvasEvent) => Promise<void>
}
vi.mock('konva', () => ({default:{}}))
vi.mock('react-konva', () => {
  const Group=forwardRef(function MockGroup(props:{children?:ReactNode,x?:number,y?:number,scaleX?:number,scaleY?:number},_ref) {
    if (props.scaleX) frameProps(props)
    return <div>{props.children}</div>
  })
  const Stage=(props:Handlers & {children?:ReactNode}) => {
    stageProps(props)
    return <div data-testid="stage">{props.children}</div>
  }
  const Container=({children}:{children?:ReactNode}) => <div>{children}</div>
  const Empty=() => null
  const Rect=({stroke}:{stroke?:string})=>stroke==='#ffcb45'?<div data-testid="rectangle-draft"/>:null
  return {Stage,Group,Layer:Container,Circle:Empty,Image:Empty,Line:Empty,Rect,Text:Empty}
})
vi.mock('../hooks/useImage',()=>({useImage:()=>({image:null,loading:false,error:false})}))
vi.mock('./RegionPolygonEditor',()=>({RegionPolygonEditor:()=>null}))

const page={id:'page',width:300,height:300,original_url:'/page.png'} as ImagePage
const callbacks=()=>({page,regions:[],onCreate:vi.fn(async (_polygon:number[][],_bbox:number[])=>true),
  onUpdate:vi.fn(async()=>undefined),onRegionAction:vi.fn(),runningAction:null,interactionKey:'creation'})
const handlers=()=>stageProps.mock.lastCall![0] as Handlers
function pointer(x:number,y:number,type:string,buttons=1):CanvasEvent {
  const frame=frameProps.mock.lastCall![0] as {x:number,y:number,scaleX:number,scaleY:number}
  const stage={getPointerPosition:()=>({x:frame.x+x*frame.scaleX,y:frame.y+y*frame.scaleY})}
  const target={getStage:()=>stage,name:()=> 'canvas-background'}
  return {target,evt:new MouseEvent(type,{button:0,buttons}),cancelBubble:false} as unknown as CanvasEvent
}
function begin(x=20,y=20) {act(()=>handlers().onMouseDown(pointer(x,y,'mousedown')))}
function move(x:number,y:number,buttons=1) {act(()=>handlers().onMouseMove(pointer(x,y,'mousemove',buttons)))}
async function finish() {await act(async()=>handlers().onMouseUp())}
function expectCoordinates(actual:number[],expected:number[]) {
  expect(actual).toHaveLength(expected.length)
  actual.forEach((value,index)=>expect(value).toBeCloseTo(expected[index],8))
}

describe('bounded text-box creation gestures',()=>{
  beforeAll(()=>vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}}))
  afterAll(()=>vi.unstubAllGlobals())
  beforeEach(()=>{
    stageProps.mockClear();frameProps.mockClear()
    useEditorStore.setState({view:'original',tool:'rectangle',zoom:1,selectedIds:[],
      layers:{original:true,detection:true,clean:false,translated:false,mask:false}})
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
