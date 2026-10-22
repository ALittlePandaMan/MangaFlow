import {act, render, screen, fireEvent, within} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterAll, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import type {ImagePage, TextRegion} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'

const {stageContext} = vi.hoisted(() => ({stageContext:{current:(_event:unknown) => {}}}))
vi.mock('konva', () => ({default:{}}))
vi.mock('react-konva', () => {
  const Container = ({children}: {children?:ReactNode}) => <>{children}</>
  const Empty = () => null
  return {Stage:({children,onContextMenu}:{children?:ReactNode,onContextMenu:(event:unknown) => void}) => {
    stageContext.current = onContextMenu; return <div>{children}</div>
  }, Layer:Container,Group:Container,Circle:Empty,Image:Empty,
  Line:({name}:{name?:string}) => name ? <span data-testid={name}/> : null,Rect:Empty,Text:Empty}
})
vi.mock('../hooks/useImage', () => ({useImage:() => ({image:null,loading:false,error:false})}))
vi.mock('./RegionPolygonEditor', () => ({RegionPolygonEditor:() => null}))

const page = {id:'page',width:800,height:1000,original_url:'/original.png'} as ImagePage
const region = {id:'region',region_key:'R001',bbox:[0,0,800,1000],polygon:[[0,0],[800,0],[800,1000],[0,1000]],translated_bbox:[0,0,800,1000],translated_polygon:[[0,0],[800,0],[800,1000],[0,1000]],visible:true,locked:false,orientation:'vertical',layout_data:{},translated_text:''} as TextRegion
function callbacks() {return {page,regions:[] as TextRegion[],onCreate:vi.fn(async()=>true),onUpdate:vi.fn(async()=>undefined),onRegionAction:vi.fn(),onPageContextMenu:vi.fn(),runningAction:null,interactionKey:'context'}}
function rightClick(x=400,y=350) {
  const evt = new MouseEvent('contextmenu',{clientX:x,clientY:y})
  const stage = {getPointerPosition:()=>({x,y})}
  act(() => stageContext.current({evt,target:{getStage:()=>stage},cancelBubble:false}))
}

describe('canvas context-menu scopes', () => {
  beforeAll(() => vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}}))
  afterAll(() => vi.unstubAllGlobals())
  beforeEach(() => useEditorStore.setState({view:'original',tool:'select',zoom:1,selectedIds:[],layers:{original:true,detection:true,clean:false,translated:false,mask:false}}))
  it('opens the page menu for blank image space and surrounding canvas', () => {
    const props = callbacks()
    render(<MangaCanvas {...props}/>)
    rightClick(); rightClick(20,20)
    expect(props.onPageContextMenu.mock.calls).toEqual([[{x:400,y:350}],[{x:20,y:20}]])
    expect(props.onRegionAction).not.toHaveBeenCalled()
  })
  it('keeps text-box right clicks scoped to the region and does not open both menus', () => {
    const props = {...callbacks(),regions:[region]}
    render(<MangaCanvas {...props}/>)
    rightClick()
    expect(screen.getByRole('menu',{name:'R001 区域操作'})).toBeInTheDocument()
    expect(props.onPageContextMenu).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('menuitem',{name:'重新 OCR'}))
    expect(props.onRegionAction).toHaveBeenCalledWith([region.id],'ocr',{crop_padding:4,orientation:'vertical'})
  })
  it.each([false,true])('shows only OCR, repair and translation in that order (multi-selection: %s)', multiple => {
    const other={...region,id:'other',region_key:'R002'}
    const props={...callbacks(),regions:multiple ? [region,other] : [region]}
    if (multiple) useEditorStore.getState().selectMany([region.id,other.id])
    render(<MangaCanvas {...props}/>)
    rightClick()
    const menu=screen.getByRole('menu',{name:multiple ? '所选 2 个区域' : 'R001 区域操作'})
    expect(within(menu).getAllByRole('menuitem').map(item=>item.textContent)).toEqual(['重新 OCR','重新修复','重新翻译'])
    expect(within(menu).queryByRole('menuitem',{name:'重新排版'})).not.toBeInTheDocument()
  })
  it.each([
    ['重新 OCR','ocr',{crop_padding:4}],
    ['重新修复','inpaint',{}],
    ['重新翻译','translate',{}],
  ])('keeps %s scoped to every selected region when right-clicking a selected box', (label,action,options) => {
    const other = {...region,id:'other',region_key:'R002',orientation:'horizontal'} as TextRegion
    const props = {...callbacks(),regions:[region,other]}
    useEditorStore.getState().selectMany([region.id,other.id])
    render(<MangaCanvas {...props}/>)
    rightClick()
    expect(screen.getByRole('menu',{name:'所选 2 个区域'})).toHaveTextContent('多选操作')
    expect(useEditorStore.getState().selectedIds).toEqual([region.id,other.id])
    fireEvent.click(screen.getByRole('menuitem',{name:label}))
    expect(props.onRegionAction).toHaveBeenCalledWith([region.id,other.id],action,options)
    expect(props.onPageContextMenu).not.toHaveBeenCalled()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
  it('replaces multi-selection when right-clicking an unselected region', () => {
    const other = {...region,id:'other',region_key:'R002'}
    const third = {...region,id:'third',region_key:'R003'}
    const props = {...callbacks(),regions:[region,other,third]}
    useEditorStore.getState().selectMany([region.id,other.id])
    render(<MangaCanvas {...props}/>)
    rightClick()
    expect(screen.getByRole('menu',{name:'R003 区域操作'})).toBeInTheDocument()
    expect(useEditorStore.getState().selectedIds).toEqual([third.id])
    fireEvent.click(screen.getByRole('menuitem',{name:'重新翻译'}))
    expect(props.onRegionAction).toHaveBeenCalledWith([third.id],'translate',{})
  })
  it('updates an open menu when the selection changes and excludes missing boxes', () => {
    const other = {...region,id:'other',region_key:'R002'}
    const props = {...callbacks(),regions:[region,other]}
    useEditorStore.getState().selectMany([region.id,other.id,'removed'])
    render(<MangaCanvas {...props}/>)
    rightClick()
    expect(screen.getByRole('menu',{name:'所选 2 个区域'})).toBeInTheDocument()
    act(() => useEditorStore.getState().select(region.id))
    expect(screen.getByRole('menu',{name:'R001 区域操作'})).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem',{name:'重新 OCR'}))
    expect(props.onRegionAction).toHaveBeenCalledWith([region.id],'ocr',{crop_padding:4,orientation:'vertical'})
  })
  it.each([
    ['重新 OCR','ocr',{crop_padding:4}],
    ['重新修复','inpaint',{}],
    ['重新翻译','translate',{}],
  ])('updates a previously single-region menu after select-all and runs %s for the entire selection', (label,action,options) => {
    const other = {...region,id:'other',region_key:'R002',orientation:'horizontal'} as TextRegion
    const third = {...region,id:'third',region_key:'R003'}
    const props = {...callbacks(),regions:[region,other,third]}
    render(<MangaCanvas {...props}/>)
    rightClick()
    expect(screen.getByRole('menu',{name:'R003 区域操作'})).toBeInTheDocument()
    // This is the store update performed by the workbench's Ctrl+A handler.
    act(() => useEditorStore.getState().selectMany([region.id,other.id,third.id]))
    expect(screen.getByRole('menu',{name:'所选 3 个区域'})).toHaveTextContent('多选操作')
    expect(screen.queryByRole('menu',{name:'R003 区域操作'})).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem',{name:label}))
    expect(props.onRegionAction).toHaveBeenCalledWith([region.id,other.id,third.id],action,options)
  })
  it('does not leave a single-region command available after selection is cleared', () => {
    const props = {...callbacks(),regions:[region]}
    render(<MangaCanvas {...props}/>)
    rightClick()
    act(() => useEditorStore.getState().select(null))
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(props.onRegionAction).not.toHaveBeenCalled()
  })
  it('opens only the page menu in comparison mode and blocks menus while processing', () => {
    const props = callbacks()
    useEditorStore.setState({view:'comparison'})
    const {rerender} = render(<MangaCanvas {...props}/>)
    rightClick()
    expect(props.onPageContextMenu).toHaveBeenCalledOnce()
    rerender(<MangaCanvas {...props} runningAction="ocr"/>)
    rightClick()
    expect(props.onPageContextMenu).toHaveBeenCalledOnce()
  })
  it('keeps context actions and selection available while background tasks lock geometry', () => {
    const other={...region,id:'other',region_key:'R002'}
    const props={...callbacks(),regions:[region,other]}
    render(<MangaCanvas {...props} readOnly processingRegionIds={[region.id]}/>)
    rightClick()
    expect(useEditorStore.getState().selectedIds).toEqual([other.id])
    fireEvent.click(screen.getByRole('menuitem',{name:'重新修复'}))
    expect(props.onRegionAction).toHaveBeenCalledWith([other.id],'inpaint',{})
    rightClick()
    fireEvent.click(screen.getByRole('menuitem',{name:'重新翻译'}))
    expect(props.onRegionAction).toHaveBeenLastCalledWith([other.id],'translate',{})
    expect(props.onUpdate).not.toHaveBeenCalled()
  })
  it('highlights only the regions captured for the active task', () => {
    const props = {...callbacks(),regions:[region,{...region,id:'other',region_key:'R002'}]}
    const {rerender} = render(<MangaCanvas {...props} processingRegionIds={['region']}/>)
    expect(screen.getByTestId('processing-region')).toBeInTheDocument()
    expect(screen.queryByTestId('processing-other')).not.toBeInTheDocument()
    rerender(<MangaCanvas {...props}/>)
    expect(screen.queryByTestId('processing-region')).not.toBeInTheDocument()
  })
})
