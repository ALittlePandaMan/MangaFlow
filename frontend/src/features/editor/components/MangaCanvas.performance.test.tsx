import {act,render} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterAll,beforeAll,beforeEach,describe,expect,it,vi} from 'vitest'
import type {ImagePage,TextRegion} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'
import {shapePreviewRegion} from '../lib/shapeText'
import {currentTextLayer} from '../lib/renderedText'

vi.mock('konva',()=>({default:{}}))
vi.mock('react-konva',()=>{
  const Container=({children}:{children?:ReactNode})=><>{children}</>
  const Empty=()=>null
  return {Stage:Container,Layer:Container,Group:Container,Circle:Empty,Image:Empty,Line:Empty,Rect:Empty,Text:Empty}
})
vi.mock('../hooks/useImage',()=>({useImage:()=>({image:null,loading:false,error:false})}))
vi.mock('./RegionPolygonEditor',()=>({RegionPolygonEditor:()=>null}))
vi.mock('../lib/shapeText',async importOriginal=>({
  ...await importOriginal<typeof import('../lib/shapeText')>(),
  shapePreviewRegion:vi.fn((region:TextRegion)=>region),
}))
vi.mock('../lib/renderedText',async importOriginal=>({
  ...await importOriginal<typeof import('../lib/renderedText')>(),
  currentTextLayer:vi.fn(()=>null),
}))

const page={id:'page',width:1200,height:1800,original_url:'/page.png',clean_url:'/clean.png'} as ImagePage
const region=(index:number)=>({id:`r${index}`,region_key:`R${index}`,visible:true,locked:false,
  bbox:[10,10,100,180],polygon:[[10,10],[110,10],[110,190],[10,190]],
  translated_bbox:[10,10,100,180],translated_polygon:[[10,10],[110,10],[110,190],[10,190]],
  translated_text:'漫画翻译的预览',orientation:'vertical',font_size:24,font_family:'sans-serif',font_weight:400,
  text_color:'#111111',stroke_color:'#ffffff',stroke_width:0,alignment:'center',line_spacing:1.15,
  character_spacing:0,rotation:0,perspective_warp:false,opacity:1,layout_data:{}} as TextRegion)
const callbacks={onCreate:vi.fn(async()=>true),onUpdate:vi.fn(async()=>undefined),onRegionAction:vi.fn(),runningAction:null,interactionKey:'perf'}

describe('dense-page preview work',()=>{
  beforeAll(()=>vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}}))
  afterAll(()=>vi.unstubAllGlobals())
  beforeEach(()=>{
    vi.mocked(shapePreviewRegion).mockClear();vi.mocked(currentTextLayer).mockClear()
    useEditorStore.setState({view:'translated',tool:'select',zoom:1,selectedIds:[],layers:{original:false,detection:true,clean:true,translated:true,mask:false}})
  })
  it('does not redo layout or text-layer validation for unchanged regions on 60 zoom frames',()=>{
    const regions=Array.from({length:100},(_,index)=>region(index))
    render(<MangaCanvas {...callbacks} page={page} regions={regions}/>)
    for(let frame=1;frame<=60;frame++) act(()=>useEditorStore.getState().setZoom(1+frame/100))
    expect(shapePreviewRegion).toHaveBeenCalledTimes(100)
    expect(currentTextLayer).toHaveBeenCalledOnce()
  })
  it('recomputes only the edited region and invalidates the old text-layer snapshot',()=>{
    const regions=Array.from({length:100},(_,index)=>region(index))
    const {rerender}=render(<MangaCanvas {...callbacks} page={page} regions={regions}/>)
    const edited=regions.map((item,index)=>index===42?{...item,translated_text:'新译文'}:item)
    rerender(<MangaCanvas {...callbacks} page={page} regions={edited}/>)
    expect(shapePreviewRegion).toHaveBeenCalledTimes(101)
    expect(currentTextLayer).toHaveBeenCalledTimes(2)
  })
})
