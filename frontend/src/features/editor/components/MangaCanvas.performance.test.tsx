import {act,render} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterAll,beforeAll,beforeEach,describe,expect,it,vi} from 'vitest'
import type {ImagePage,TextRegion} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'
import {sizeText} from '../lib/fontSizing'

vi.mock('konva',()=>({default:{}}))
vi.mock('react-konva',()=>{
  const Container=({children}:{children?:ReactNode})=><>{children}</>
  const Empty=()=>null
  return {Stage:Container,Layer:Container,Group:Container,Circle:Empty,Image:Empty,Line:Empty,Rect:Empty,Text:Empty,Shape:Empty}
})
vi.mock('../hooks/useImage',()=>({useImage:()=>({image:null,loading:false,error:false})}))
vi.mock('./RegionPolygonEditor',()=>({RegionPolygonEditor:()=>null}))
vi.mock('../lib/fontSizing',async importOriginal=>{
  const original=await importOriginal<typeof import('../lib/fontSizing')>()
  return {...original,sizeText:vi.fn(original.sizeText)}
})

const page={id:'page',width:1200,height:1800,original_url:'/page.png',clean_url:'/clean.png'} as ImagePage
const region=(index:number)=>({id:`r${index}`,region_key:`R${index}`,visible:true,locked:false,
  bbox:[10,10,100,180],polygon:[[10,10],[110,10],[110,190],[10,190]],
  translated_bbox:[10,10,100,180],translated_polygon:[[10,10],[110,10],[110,190],[10,190]],
  translated_text:'漫画翻译的预览',orientation:'vertical',font_size:24,font_family:'sans-serif',font_weight:400,
  text_color:'#111111',stroke_color:'#ffffff',stroke_width:0,alignment:'center',line_spacing:1.15,
  character_spacing:0,rotation:0,perspective_warp:false,opacity:1,layout_data:{}} as TextRegion)
const callbacks={onCreate:vi.fn(async()=>true),onUpdate:vi.fn(async()=>undefined),onRegionAction:vi.fn(),runningAction:null,interactionKey:'perf'}

describe('dense-page preview work',()=>{
  beforeAll(()=>{
    vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}})
  })
  afterAll(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
  beforeEach(()=>{
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
    vi.mocked(sizeText).mockClear()
    useEditorStore.setState({view:'translated',tool:'select',zoom:1,selectedIds:[],layers:{original:false,detection:true,clean:true,translated:true,mask:false}})
  })
  it('does not redo layout for unchanged regions on 60 zoom frames',()=>{
    const regions=Array.from({length:100},(_,index)=>region(index))
    render(<MangaCanvas {...callbacks} page={page} regions={regions}/>)
    for(let frame=1;frame<=60;frame++) act(()=>useEditorStore.getState().setZoom(1+frame/100))
    expect(sizeText).toHaveBeenCalledTimes(100)
  })
  it('recomputes only the edited region',()=>{
    const regions=Array.from({length:100},(_,index)=>region(index))
    const {rerender}=render(<MangaCanvas {...callbacks} page={page} regions={regions}/>)
    const edited=regions.map((item,index)=>index===42?{...item,translated_text:'新译文'}:item)
    rerender(<MangaCanvas {...callbacks} page={page} regions={edited}/>)
    expect(sizeText).toHaveBeenCalledTimes(101)
  })
  it.each([
    {shape_layout_applied:true,effective_text_bbox:[20,15,60,120]},
    {shape_layout_applied:false,render_skipped:true},
    {shape_layout_applied:true,effective_text_bbox:[0,0,NaN,10]},
  ])('passes visible geometry to real text sizing despite stale legacy metadata: %j',layout_data=>{
    const original={...region(1),layout_data}
    const {rerender}=render(<MangaCanvas {...callbacks} page={page} regions={[original]}/>)
    expect(sizeText).toHaveBeenCalledExactlyOnceWith(original)
    expect(vi.mocked(sizeText).mock.calls[0][0]).toBe(original)
    expect(original.translated_bbox).toEqual([10,10,100,180])
    const locked={...original,locked:true,translated_bbox:[20,30,90,160]}
    rerender(<MangaCanvas {...callbacks} page={page} regions={[locked]}/>)
    expect(vi.mocked(sizeText).mock.lastCall?.[0]).toBe(locked)
  })
  it('does not size or paint a hidden region',()=>{
    render(<MangaCanvas {...callbacks} page={page} regions={[{...region(1),visible:false}]}/>)
    expect(sizeText).not.toHaveBeenCalled()
  })
})
