import {act, render, screen} from '@testing-library/react'
import {useRef} from 'react'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {useSelectedPageScroll} from './useSelectedPageScroll'

const scrollIntoView = vi.fn()
const scrollTo = vi.fn()
const resizeCallbacks = new Set<() => void>()
const disconnect = vi.fn()
let activeBounds = {top: 250, bottom: 450}
let nextBounds = {top: 460, bottom: 660}

function Fixture({pageId = 'page-30', ready = true, hidden = false, version = 0}: {pageId?: string, ready?: boolean, hidden?: boolean, version?: number}) {
  const list = useRef<HTMLDivElement>(null)
  useSelectedPageScroll(list, pageId, ready)
  return <div ref={list} data-page-list hidden={hidden} data-version={version}>
    {Array.from({length:40},(_,index) => <button key={index} data-page-id={`page-${index+1}`} aria-current={pageId === `page-${index+1}` ? 'page' : undefined}>第 {index+1} 页</button>)}
  </div>
}

describe('selected page list positioning', () => {
  beforeEach(() => {
    scrollIntoView.mockReset()
    scrollTo.mockReset()
    activeBounds = {top: 250, bottom: 450}
    nextBounds = {top: 460, bottom: 660}
    disconnect.mockReset()
    resizeCallbacks.clear()
    vi.stubGlobal('ResizeObserver',class {
      constructor(callback: () => void) {resizeCallbacks.add(callback)}
      observe() {}
      disconnect = disconnect
    })
    vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockImplementation(function (this: HTMLElement) {return this.hidden ? 0 : 200})
    vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockImplementation(function (this: HTMLElement) {return this.hidden ? 0 : 500})
    vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const bounds = this.hasAttribute('data-page-list') ? {top:0,bottom:500}
        : this.getAttribute('aria-current') === 'page' ? activeBounds : nextBounds
      return {...bounds,x:0,y:bounds.top,left:0,right:200,width:200,height:bounds.bottom-bounds.top,toJSON() {return {}}}
    })
    Object.defineProperty(HTMLElement.prototype,'scrollIntoView',{configurable:true,value:scrollIntoView})
    Object.defineProperty(HTMLElement.prototype,'scrollTo',{configurable:true,value:scrollTo})
  })
  afterEach(() => {
    delete (HTMLElement.prototype as {scrollIntoView?: unknown}).scrollIntoView
    delete (HTMLElement.prototype as {scrollTo?: unknown}).scrollTo
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })
  it('positions page 30 on initial load and again after a full refresh/remount', () => {
    const first = render(<Fixture/>)
    expect(scrollIntoView).toHaveBeenCalledWith({block:'nearest',inline:'nearest',behavior:'instant'})
    expect(scrollIntoView.mock.contexts[0]).toBe(screen.getByRole('button',{name:'第 30 页'}))
    first.unmount()
    render(<Fixture/>)
    expect(scrollIntoView).toHaveBeenCalledTimes(2)
    expect(scrollIntoView.mock.contexts[1]).toBe(screen.getByRole('button',{name:'第 30 页'}))
  })
  it('waits for the loaded workspace rather than positioning during the skeleton', () => {
    const {rerender} = render(<Fixture ready={false}/>)
    expect(scrollIntoView).not.toHaveBeenCalled()
    rerender(<Fixture ready/>)
    expect(scrollIntoView).toHaveBeenCalledOnce()
  })
  it('follows navigation without snapping back during metadata/progress rerenders', () => {
    const {rerender} = render(<Fixture/>)
    rerender(<Fixture version={1}/>)
    expect(scrollIntoView).toHaveBeenCalledOnce()
    rerender(<Fixture pageId="page-31" version={2}/>)
    expect(scrollIntoView).toHaveBeenCalledTimes(2)
    expect(scrollIntoView.mock.contexts[1]).toBe(screen.getByRole('button',{name:'第 31 页'}))
    rerender(<Fixture pageId="page-31" ready={false}/>)
    rerender(<Fixture pageId="page-31" ready/>)
    expect(scrollIntoView).toHaveBeenCalledTimes(2)
    expect(scrollTo).toHaveBeenCalledTimes(2)
  })
  it('leaves the next page fully visible below the selected page', () => {
    render(<Fixture/>)
    expect(scrollTo).toHaveBeenCalledOnce()
    expect(scrollTo).toHaveBeenCalledWith({top:160,behavior:'instant'})
    expect(scrollTo.mock.contexts[0]).toBe(screen.getByRole('button',{name:'第 30 页'}).parentElement)
  })
  it('does not move the list when the current and next page already fit', () => {
    activeBounds = {top:50,bottom:250}
    nextBounds = {top:260,bottom:460}
    render(<Fixture/>)
    expect(scrollTo).not.toHaveBeenCalled()
  })
  it('prioritizes the current thumbnail when two pages cannot fit', () => {
    activeBounds = {top:50,bottom:450}
    nextBounds = {top:460,bottom:860}
    render(<Fixture/>)
    expect(scrollTo).toHaveBeenCalledWith({top:50,behavior:'instant'})
  })
  it('uses the current scroll offset and the list padding for lookahead', () => {
    vi.spyOn(HTMLElement.prototype,'scrollTop','get').mockReturnValue(1000)
    vi.stubGlobal('getComputedStyle', () => ({paddingTop:'10px',paddingBottom:'10px'}))
    render(<Fixture/>)
    expect(scrollTo).toHaveBeenCalledWith({top:1170,behavior:'instant'})
  })
  it('only reveals the selected page at the end of the project', () => {
    render(<Fixture pageId="page-40"/>)
    expect(scrollIntoView.mock.contexts[0]).toBe(screen.getByRole('button',{name:'第 40 页'}))
    expect(scrollTo).not.toHaveBeenCalled()
  })
  it('does not scroll an extra row when mobile thumbnails share a row', () => {
    activeBounds = {top:200,bottom:450}
    nextBounds = {top:200,bottom:450}
    render(<Fixture/>)
    expect(scrollTo).not.toHaveBeenCalled()
  })
  it('waits for the mobile page sheet to become visible and then positions only once', () => {
    const {rerender,unmount} = render(<Fixture hidden/>)
    expect(scrollIntoView).not.toHaveBeenCalled()
    rerender(<Fixture hidden={false}/>)
    act(() => resizeCallbacks.forEach(callback => callback()))
    expect(scrollIntoView).toHaveBeenCalledOnce()
    expect(scrollIntoView.mock.contexts[0]).toBe(screen.getByRole('button',{name:'第 30 页'}))
    act(() => resizeCallbacks.forEach(callback => callback()))
    expect(scrollIntoView).toHaveBeenCalledOnce()
    unmount()
    expect(disconnect).toHaveBeenCalled()
  })
  it('cancels a hidden-panel observer when navigating before that panel opens', () => {
    const {rerender} = render(<Fixture hidden/>)
    rerender(<Fixture hidden pageId="page-31"/>)
    expect(disconnect).toHaveBeenCalledOnce()
    rerender(<Fixture pageId="page-31"/>)
    act(() => resizeCallbacks.forEach(callback => callback()))
    expect(scrollIntoView).toHaveBeenCalledOnce()
    expect(scrollIntoView.mock.contexts[0]).toBe(screen.getByRole('button',{name:'第 31 页'}))
  })
  it('repositions after switching between the desktop list and mobile grid', () => {
    render(<Fixture/>)
    vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(800)
    act(() => resizeCallbacks.forEach(callback => callback()))
    expect(scrollIntoView).toHaveBeenCalledTimes(2)
    act(() => resizeCallbacks.forEach(callback => callback()))
    expect(scrollIntoView).toHaveBeenCalledTimes(2)
    vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(320)
    act(() => resizeCallbacks.forEach(callback => callback()))
    expect(scrollIntoView).toHaveBeenCalledTimes(3)
  })
})
