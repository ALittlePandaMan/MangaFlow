import {act,fireEvent,render,screen} from '@testing-library/react'
import {type ReactNode} from 'react'
import {afterAll,beforeAll,beforeEach,describe,expect,it,vi} from 'vitest'
import type Konva from 'konva'
import type {ImagePage,TextRegion} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'

vi.mock('konva',()=>({default:{}}))
vi.mock('react-konva',()=>{
  const Container=({children}:{children?:ReactNode})=><div>{children}</div>
  const Empty=()=>null
  type LineProps={points:number[],closed?:boolean,listening?:boolean,onClick?:(event:Konva.KonvaEventObject<MouseEvent>)=>void,onTap?:(event:Konva.KonvaEventObject<TouchEvent>)=>void}
  const Line=({points,closed,listening,onClick,onTap}:LineProps)=>closed && onClick ? <button data-testid={`outline-${points[0]}`} disabled={listening===false}
    onClick={event=>onClick({evt:event.nativeEvent,cancelBubble:false} as Konva.KonvaEventObject<MouseEvent>)}
    onTouchEnd={event=>onTap?.({evt:event.nativeEvent,cancelBubble:false} as Konva.KonvaEventObject<TouchEvent>)}/> : null
  return {Stage:Container,Layer:Container,Group:Container,Circle:Empty,Image:Empty,Line,Rect:Empty,Text:Empty}
})
vi.mock('../hooks/useImage',()=>({useImage:()=>({image:null,loading:false,error:false})}))
vi.mock('./RegionPolygonEditor',()=>({
  RegionPolygonEditor:({polygon,onSelect}:{polygon:number[][],onSelect:(additive:boolean)=>void})=><button data-testid={`outline-${polygon[0][0]}`}
    onClick={event=>onSelect(event.altKey)} onTouchEnd={()=>onSelect(false)}/>,
}))

const page={id:'page',width:500,height:500,original_url:'/page.png'} as ImagePage
const regions=[20,120,220].map((x,index)=>({id:`region-${index+1}`,region_key:`R00${index+1}`,bbox:[x,20,50,100],polygon:[[x,20],[x+50,20],[x+50,120],[x,120]],
  translated_bbox:[x,20,50,100],translated_polygon:[[x,20],[x+50,20],[x+50,120],[x,120]],visible:true,locked:false,translated_text:'',layout_data:{}} as TextRegion))
const callbacks=()=>({page,regions,onCreate:vi.fn(async()=>true),onUpdate:vi.fn(async()=>undefined),onRegionAction:vi.fn(),runningAction:null,interactionKey:'selection'})
const selected=()=>useEditorStore.getState().selectedIds
const click=(x:number,options:MouseEventInit={})=>fireEvent.click(screen.getByTestId(`outline-${x}`),options)

describe('canvas region click selection',()=>{
  beforeAll(()=>vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}}))
  afterAll(()=>vi.unstubAllGlobals())
  beforeEach(()=>useEditorStore.setState({view:'original',tool:'select',zoom:1,selectedIds:[],layers:{original:true,detection:true,clean:false,translated:false,mask:false}}))

  it.each(['original','translated'] as const)('switches a plain click to one region in %s view and keeps repeated clicks selected',view=>{
    useEditorStore.setState({view})
    render(<MangaCanvas {...callbacks()}/>)
    click(20);expect(selected()).toEqual(['region-1'])
    click(120);expect(selected()).toEqual(['region-2'])
    click(120);expect(selected()).toEqual(['region-2'])
  })

  it.each([false,true])('uses Alt to add/remove regions, then plain click collapses the group (readOnly=%s)',readOnly=>{
    const props=callbacks()
    render(<MangaCanvas {...props} readOnly={readOnly}/>)
    click(20);click(120,{altKey:true});click(220,{altKey:true})
    expect(selected()).toEqual(['region-1','region-2','region-3'])
    click(120,{altKey:true});expect(selected()).toEqual(['region-1','region-3'])
    click(20);expect(selected()).toEqual(['region-1'])
    click(20,{altKey:true});expect(selected()).toEqual([])
    expect(props.onUpdate).not.toHaveBeenCalled()
  })

  it.each([{ctrlKey:true},{metaKey:true},{shiftKey:true}])('does not treat other modifiers as additive selection (%j)',modifier=>{
    render(<MangaCanvas {...callbacks()}/>)
    click(20);click(120,modifier)
    expect(selected()).toEqual(['region-2'])
  })

  it('taps switch selection instead of accumulating it, including an existing multi-selection',()=>{
    render(<MangaCanvas {...callbacks()}/>)
    fireEvent.touchEnd(screen.getByTestId('outline-20'))
    expect(selected()).toEqual(['region-1'])
    fireEvent.touchEnd(screen.getByTestId('outline-120'))
    expect(selected()).toEqual(['region-2'])
    fireEvent.touchEnd(screen.getByTestId('outline-120'))
    expect(selected()).toEqual(['region-2'])
    act(()=>useEditorStore.getState().selectMany(['region-1','region-3']))
    fireEvent.touchEnd(screen.getByTestId('outline-220'))
    expect(selected()).toEqual(['region-3'])
  })

  it('ignores non-primary clicks on an unselected outline',()=>{
    render(<MangaCanvas {...callbacks()}/>)
    click(20)
    click(120,{button:2,altKey:true})
    expect(selected()).toEqual(['region-1'])
  })
})
