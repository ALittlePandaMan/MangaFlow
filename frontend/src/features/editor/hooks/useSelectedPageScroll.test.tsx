import {act, render, screen} from '@testing-library/react'
import {useRef} from 'react'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {useSelectedPageScroll} from './useSelectedPageScroll'

const scrollIntoView = vi.fn()
const resizeCallbacks = new Set<() => void>()
const disconnect = vi.fn()

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
    disconnect.mockReset()
    resizeCallbacks.clear()
    vi.stubGlobal('ResizeObserver',class {
      constructor(callback: () => void) {resizeCallbacks.add(callback)}
      observe() {}
      disconnect = disconnect
    })
    vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockImplementation(function (this: HTMLElement) {return this.hidden ? 0 : 200})
    vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockImplementation(function (this: HTMLElement) {return this.hidden ? 0 : 500})
    Object.defineProperty(HTMLElement.prototype,'scrollIntoView',{configurable:true,value:scrollIntoView})
  })
  afterEach(() => {
    delete (HTMLElement.prototype as {scrollIntoView?: unknown}).scrollIntoView
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
    expect(disconnect).toHaveBeenCalled()
    unmount()
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
})
