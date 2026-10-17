import {act, cleanup, fireEvent, render, screen} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import type {ImagePage, TextRegion} from '../../../types'
import {ImmersiveReader} from './ImmersiveReader'

const {useImageMock, textMock} = vi.hoisted(() => ({useImageMock:vi.fn(), textMock:vi.fn()}))
vi.mock('../hooks/useImage', () => ({useImage:useImageMock}))
vi.mock('./MangaCanvas', () => ({TranslatedRegions: (props:unknown) => {textMock(props);return <span data-testid="reader-text"/>}}))
vi.mock('react-konva', () => ({
  Stage: ({children,width,height}:{children:ReactNode;width:number;height:number}) => <div data-testid="reader-scene" data-width={width} data-height={height}>{children}</div>,
  Layer: ({children}:{children:ReactNode}) => <>{children}</>,
  Group: ({children,x,y,scaleX}:{children:ReactNode;x:number;y:number;scaleX:number}) => <div data-testid="reader-transform" data-x={x} data-y={y} data-scale={scaleX}>{children}</div>,
  Image: () => <span data-testid="reader-artwork"/>, Rect: () => null,
}))

const page={id:'p1',filename:'page.png',width:1000,height:2000,original_url:'/original.png',clean_url:'/clean.png',rendered_url:'/old-export.png'} as ImagePage
const regions=[{id:'r1',translated_text:'当前译文'}] as TextRegion[]
let now=0
class ResizeObserverMock {observe() {} disconnect() {}}

function fixture(patch:Partial<Parameters<typeof ImmersiveReader>[0]>={}) {
  const props={page,regions,busy:false,onPreviousPage:vi.fn(),onNextPage:vi.fn(),onClose:vi.fn(),...patch}
  return {...render(<ImmersiveReader {...props}/>),props,node:screen.getByRole('dialog',{name:'沉浸式阅读'})}
}

describe('immersive reading', () => {
  beforeEach(() => {
    now=0
    vi.spyOn(performance,'now').mockImplementation(() => now)
    vi.stubGlobal('ResizeObserver',ResizeObserverMock)
    useImageMock.mockReset().mockReturnValue({image:document.createElement('img'),loading:false,error:false})
    textMock.mockClear()
  })
  afterEach(() => {cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()})

  it('uses clean artwork and current editor text, not a stale export, fitted without cropping', () => {
    fixture()
    expect(useImageMock).toHaveBeenCalledWith('/clean.png')
    expect(useImageMock).not.toHaveBeenCalledWith('/old-export.png')
    expect(textMock).toHaveBeenCalledWith({regions,displayScale:Math.min(window.innerWidth/1000,window.innerHeight/2000)})
    expect(screen.getByTestId('reader-artwork')).toBeInTheDocument()
    expect(screen.getByTestId('reader-transform')).toHaveAttribute('data-y','0')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
  it('falls back to the original only when no clean background exists', () => {
    fixture({page:{...page,clean_url:null}})
    expect(useImageMock).toHaveBeenCalledWith('/original.png')
    expect(screen.getByTestId('reader-text')).toBeInTheDocument()
  })
  it.each(['background','artwork'])('exits when clicking the %s', target => {
    const {props,node}=fixture()
    fireEvent.click(target === 'background' ? node : screen.getByTestId('reader-artwork'))
    expect(props.onClose).toHaveBeenCalledOnce()
  })
  it('keeps the background inert and restores focus, scroll and previous inert values on exit', () => {
    const background=document.createElement('div'), trigger=document.createElement('button')
    background.inert=false; background.append(trigger);document.body.append(background)
    trigger.focus();document.body.style.overflow='auto'
    const {node,unmount}=fixture()
    expect(background.inert).toBe(true)
    expect(document.activeElement).toBe(node)
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.keyDown(node,{key:'Tab'})
    expect(document.activeElement).toBe(node)
    unmount()
    expect(background.inert).toBe(false)
    expect(document.body.style.overflow).toBe('auto')
    expect(document.activeElement).toBe(trigger)
    background.remove();document.body.style.overflow=''
  })
  it('turns one page per wheel gesture, including after the page callbacks change', () => {
    const {props,node,rerender}=fixture()
    const event=new WheelEvent('wheel',{deltaY:120,bubbles:true,cancelable:true})
    fireEvent(node,event)
    expect(event.defaultPrevented).toBe(true)
    expect(props.onNextPage).toHaveBeenCalledOnce()
    const next=vi.fn()
    rerender(<ImmersiveReader {...props} page={{...page,id:'p2'}} onNextPage={next}/>)
    for (now=40;now<=360;now+=40) fireEvent.wheel(node,{deltaY:80})
    expect(next).not.toHaveBeenCalled() // Inertia is not a fresh gesture.
    now=600;fireEvent.wheel(node,{deltaY:120})
    expect(next).toHaveBeenCalledOnce()
    now=1000;fireEvent.wheel(node,{deltaY:-120})
    expect(props.onPreviousPage).toHaveBeenCalledOnce()
  })
  it('accumulates trackpad movement, normalizes wheel units and ignores horizontal or pinch events', () => {
    const {props,node}=fixture()
    fireEvent.wheel(node,{deltaY:10,deltaX:80})
    fireEvent.wheel(node,{deltaY:120,ctrlKey:true})
    expect(props.onNextPage).not.toHaveBeenCalled()
    for (now=0;now<40;now+=10) fireEvent.wheel(node,{deltaY:12})
    expect(props.onNextPage).toHaveBeenCalledOnce()
    now=500;fireEvent.wheel(node,{deltaY:-3,deltaMode:1})
    expect(props.onPreviousPage).toHaveBeenCalledOnce()
    now=900;fireEvent.wheel(node,{deltaY:1,deltaMode:2})
    expect(props.onNextPage).toHaveBeenCalledTimes(2)
  })
  it('does not queue page flips while loading or wrap past a project boundary', () => {
    const {props,node,rerender}=fixture({busy:true})
    fireEvent.wheel(node,{deltaY:120})
    rerender(<ImmersiveReader {...props} busy={false}/>)
    now=100;fireEvent.wheel(node,{deltaY:120})
    expect(props.onNextPage).not.toHaveBeenCalled()
    now=500;fireEvent.wheel(node,{deltaY:120})
    expect(props.onNextPage).toHaveBeenCalledOnce()
    rerender(<ImmersiveReader {...props} busy={false} onNextPage={undefined}/>)
    now=1000;fireEvent.wheel(node,{deltaY:120})
    expect(props.onNextPage).toHaveBeenCalledOnce()
  })
  it('prevents hidden editor shortcuts and supports keyboard navigation/exit', () => {
    const backgroundKey=vi.fn();window.addEventListener('keydown',backgroundKey)
    const {props,node}=fixture()
    for (const key of ['Delete','Backspace','z']) fireEvent.keyDown(node,{key})
    expect(backgroundKey).not.toHaveBeenCalled()
    expect(props.onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(node,{key:'ArrowDown'})
    fireEvent.keyDown(node,{key:'ArrowDown',repeat:true})
    fireEvent.keyDown(node,{key:'PageUp'})
    expect(props.onNextPage).toHaveBeenCalledOnce()
    expect(props.onPreviousPage).toHaveBeenCalledOnce()
    fireEvent.keyDown(node,{key:'Escape'})
    expect(props.onClose).toHaveBeenCalledOnce()
    window.removeEventListener('keydown',backgroundKey)
  })
  it('keeps click-to-exit available when an image or navigation request fails', () => {
    useImageMock.mockReturnValue({image:null,loading:false,error:true})
    const {props}=fixture()
    expect(screen.getByRole('alert')).toHaveTextContent('图片加载失败')
    fireEvent.click(screen.getByRole('alert'))
    expect(props.onClose).toHaveBeenCalledOnce()
  })
  it('does not flip away while the current artwork is still decoding', () => {
    useImageMock.mockReturnValue({image:null,loading:true,error:false})
    const {node,props}=fixture()
    fireEvent.wheel(node,{deltaY:120})
    fireEvent.keyDown(node,{key:'ArrowDown'})
    expect(props.onNextPage).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载漫画')
    fireEvent.click(node)
    expect(props.onClose).toHaveBeenCalledOnce()
  })
  it('continues reading if native fullscreen is denied', async () => {
    const original=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'requestFullscreen')
    const request=vi.fn().mockRejectedValue(new Error('not allowed'))
    Object.defineProperty(HTMLElement.prototype,'requestFullscreen',{configurable:true,value:request})
    try {
      const {node}=fixture()
      await act(async () => {await Promise.resolve()})
      expect(request).toHaveBeenCalledOnce()
      expect(node).toBeInTheDocument()
      expect(screen.getByTestId('reader-text')).toBeInTheDocument()
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype,'requestFullscreen',original)
      else Reflect.deleteProperty(HTMLElement.prototype,'requestFullscreen')
    }
  })
  it('exits a late native-fullscreen request if the reader was already closed', async () => {
    const originalRequest=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'requestFullscreen')
    const originalExit=Object.getOwnPropertyDescriptor(document,'exitFullscreen')
    const originalElement=Object.getOwnPropertyDescriptor(document,'fullscreenElement')
    let active:Element|null=null, finish!:() => void
    const pending=new Promise<void>(resolve => {finish=resolve})
    const exit=vi.fn(async () => {active=null})
    Object.defineProperty(HTMLElement.prototype,'requestFullscreen',{configurable:true,value:() => pending})
    Object.defineProperty(document,'fullscreenElement',{configurable:true,get:() => active})
    Object.defineProperty(document,'exitFullscreen',{configurable:true,value:exit})
    try {
      const {node,unmount}=fixture()
      unmount()
      active=node
      await act(async () => {finish();await pending})
      expect(exit).toHaveBeenCalledOnce()
    } finally {
      if (originalRequest) Object.defineProperty(HTMLElement.prototype,'requestFullscreen',originalRequest)
      else Reflect.deleteProperty(HTMLElement.prototype,'requestFullscreen')
      if (originalElement) Object.defineProperty(document,'fullscreenElement',originalElement)
      else Reflect.deleteProperty(document,'fullscreenElement')
      if (originalExit) Object.defineProperty(document,'exitFullscreen',originalExit)
      else Reflect.deleteProperty(document,'exitFullscreen')
    }
  })
})
