import {act,render} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterAll,beforeAll,beforeEach,describe,expect,it,vi} from 'vitest'
import type {ImagePage,TextRegion} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'

const {drag} = vi.hoisted(()=>({drag:{start:(_e:unknown)=>{},move:(_e:unknown)=>{},end:(_e:unknown)=>{}}}))
vi.mock('konva',()=>({default:{}}))
vi.mock('react-konva',()=>{
  const Container=({children,name,onDragStart,onDragMove,onDragEnd}:{children?:ReactNode,name?:string,onDragStart:typeof drag.start,onDragMove:typeof drag.move,onDragEnd:typeof drag.end})=>{
    if(name==='selected-region-group') {drag.start=onDragStart;drag.move=onDragMove;drag.end=onDragEnd}
    return <div data-name={name}>{children}</div>
  }
  const Empty=()=>null
  return {Stage:Container,Layer:Container,Group:Container,Circle:Empty,Image:Empty,Line:Empty,Rect:Empty,Text:Empty}
})
vi.mock('../hooks/useImage',()=>({useImage:()=>({image:null,loading:false,error:false})}))
vi.mock('./RegionPolygonEditor',()=>({RegionPolygonEditor:()=>null}))
const page={id:'page',width:300,height:300,original_url:'/page.png'} as ImagePage
const region=(id:string,x:number,y:number,locked=false)=>({id,region_key:id,locked,visible:true,bbox:[x,y,40,60],polygon:[[x,y],[x+40,y],[x+40,y+60],[x,y+60]],
  translated_bbox:[x+10,y+10,30,50],translated_polygon:[[x+10,y+10],[x+40,y+10],[x+40,y+60],[x+10,y+60]],translated_text:'',layout_data:{},rotation:0} as TextRegion)

describe('multi-region drag gestures',()=>{
  beforeAll(()=>vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}}))
  afterAll(()=>vi.unstubAllGlobals())
  beforeEach(()=>useEditorStore.setState({view:'original',tool:'select',zoom:1,selectedIds:['R001','R002','R003'],layers:{original:true,detection:true,clean:false,translated:false}}))
  it.each([false,true])('moves the whole active frame but saves only once at drag end (%s)',translated=>{
    useEditorStore.setState({view:translated?'translated':'original'})
    const regions=[region('R001',20,20),region('R002',100,130),region('R003',0,0,true)]
    const onUpdateMany=vi.fn<(_changes:Array<{id:string,patch:Partial<TextRegion>}>)=>Promise<void>>(async()=>undefined),onUpdate=vi.fn(async()=>undefined)
    render(<MangaCanvas page={page} regions={regions} onCreate={vi.fn(async()=>true)} onUpdate={onUpdate} onUpdateMany={onUpdateMany} onRegionAction={vi.fn()} runningAction={null} interactionKey="group"/>)
    let x=0,y=0
    const target={x:()=>x,y:()=>y,position:(point:{x:number,y:number})=>{x=point.x;y=point.y}}
    const event={target,currentTarget:target,cancelBubble:false}
    act(()=>drag.start(event))
    for(let i=1;i<=10;i++) {x=i*2;y=i;act(()=>drag.move(event))}
    expect(onUpdateMany).not.toHaveBeenCalled()
    act(()=>drag.end(event))
    expect(onUpdateMany).toHaveBeenCalledTimes(1)
    expect(onUpdate).not.toHaveBeenCalled()
    const changes=onUpdateMany.mock.calls[0][0] as unknown as Array<{id:string,patch:Partial<TextRegion>}>
    expect(changes.map(change=>change.id)).toEqual(['R001','R002'])
    expect(changes[0].patch).toEqual(translated?{translated_bbox:[50,40,30,50],translated_polygon:[[50,40],[80,40],[80,90],[50,90]]}
      : {bbox:[40,30,40,60],polygon:[[40,30],[80,30],[80,90],[40,90]]})
    expect({x,y}).toEqual({x:0,y:0})
    expect(useEditorStore.getState().selectedIds).toEqual(['R001','R002','R003'])
  })
})
