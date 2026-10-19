import {act, fireEvent, render, screen} from '@testing-library/react'
import {useRef} from 'react'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import type {ImagePage} from '../../../types'
import {VirtualPageList} from './VirtualPageList'

let width = 300, height = 600, compact = false
const observers = new Set<() => void>()
const pages = Array.from({length:350}, (_, i) => ({id:`page-${i+1}`,width:1000,height:i%3 ? 1400 : 2200} as ImagePage))
function Fixture({selected='page-300',items=pages}: {selected?:string,items?:ImagePage[]}) {
  const ref = useRef<HTMLDivElement>(null)
  return <VirtualPageList listRef={ref} items={items} selectedPageId={selected} className="" onContextMenu={() => {}} empty={<span>empty</span>}>
    {(item,index) => <button key={item.id} data-page-id={item.id} data-page-index={index}>{item.id}</button>}
  </VirtualPageList>
}

describe('large page lists', () => {
  beforeEach(() => {
    width = 300; height = 600; compact = false; observers.clear()
    vi.stubGlobal('matchMedia', () => ({matches:compact,addEventListener() {},removeEventListener() {}}))
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function(this:HTMLElement) {return this.hasAttribute('data-page-list') ? width : 0})
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function(this:HTMLElement) {return this.hasAttribute('data-page-list') ? height : 0})
    vi.stubGlobal('ResizeObserver', class {constructor(callback: () => void) {observers.add(callback)} observe() {} disconnect() {}})
  })
  afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals()})

  it('mounts a bounded window around a restored page 300 with the next page present', () => {
    render(<Fixture/>)
    expect(screen.getByRole('button',{name:'page-300'})).toBeInTheDocument()
    expect(screen.getByRole('button',{name:'page-301'})).toBeInTheDocument()
    expect(screen.getAllByRole('button').length).toBeLessThan(15)
    expect(document.querySelector('[data-page-list]')!.scrollTop).toBeGreaterThan(100000)
  })
  it('follows navigation without snapping back on progress rerenders or ordinary scrolling', async () => {
    const view = render(<Fixture/>)
    const list = document.querySelector('[data-page-list]')!
    view.rerender(<Fixture selected="page-301"/>)
    expect(screen.getByRole('button',{name:'page-301'})).toBeInTheDocument()
    list.scrollTop = 5000
    fireEvent.scroll(list)
    await act(async () => {await new Promise(resolve => requestAnimationFrame(resolve))})
    view.rerender(<Fixture selected="page-301" items={[...pages]}/>)
    expect(list.scrollTop).toBe(5000)
    expect(screen.getAllByRole('button').length).toBeLessThan(15)
    expect(screen.queryByRole('button',{name:'page-301'})).not.toBeInTheDocument()
  })
  it('handles the final page and backwards navigation without an empty window', () => {
    const view = render(<Fixture selected="page-350"/>)
    expect(screen.getByRole('button',{name:'page-350'})).toBeInTheDocument()
    view.rerender(<Fixture selected="page-1"/>)
    expect(screen.getByRole('button',{name:'page-1'})).toBeInTheDocument()
    expect(screen.getByRole('button',{name:'page-2'})).toBeInTheDocument()
  })
  it('waits for a hidden mobile panel and then restores its multi-column scroll position', () => {
    compact = true; width = 0; height = 0
    render(<Fixture/>)
    const list = document.querySelector('[data-page-list]')!
    expect(list.scrollTop).toBe(0)
    width = 800; height = 500
    act(() => observers.forEach(callback => callback()))
    expect(list.scrollTop).toBeGreaterThan(1000)
    expect(screen.getByRole('button',{name:'page-300'})).toBeInTheDocument()
    expect(screen.getByRole('button',{name:'page-301'})).toBeInTheDocument()
    expect(screen.getAllByRole('button').length).toBeLessThan(60)
  })
  it('preserves all small-project cards and original page indexes for sorting', () => {
    render(<Fixture items={pages.slice(0,40)} selected="page-30"/>)
    expect(screen.getAllByRole('button')).toHaveLength(40)
    expect(screen.getByRole('button',{name:'page-30'})).toHaveAttribute('data-page-index','29')
    expect(document.querySelector('[data-virtual="true"]')).toBeNull()
  })
})
