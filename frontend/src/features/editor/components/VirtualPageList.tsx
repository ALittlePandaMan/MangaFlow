import {useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode, type RefObject} from 'react'
import type {ImagePage} from '../../../types'
import {useCompactLayout} from '../../../hooks/useCompactLayout'

const OVERSCAN_ROWS = 2
export const VIRTUAL_PAGE_THRESHOLD = 40

function rowAt(offsets: number[], offset: number) {
  let low = 0, high = offsets.length - 1
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (offsets[middle] <= offset) low = middle
    else high = middle - 1
  }
  return low
}

/** Known image dimensions reserve exact thumbnail space without decoding offscreen pages. */
export function VirtualPageList({items, selectedPageId, listRef, className, onContextMenu, children, empty}: {
  items: ImagePage[]
  selectedPageId?: string
  listRef: RefObject<HTMLDivElement>
  className: string
  onContextMenu: (event: MouseEvent<HTMLDivElement>) => void
  children: (item: ImagePage, index: number) => ReactNode
  empty: ReactNode
}) {
  const compact = useCompactLayout()
  const virtual = items.length > VIRTUAL_PAGE_THRESHOLD
  const [viewport, setViewport] = useState({width: 0, height: 0, top: 0})
  const positioned = useRef('')
  const layout = useMemo(() => {
    const width = Math.max(1, (viewport.width || 280) - 20)
    const columns = compact ? Math.max(1, Math.floor((width + 8) / 148)) : 1
    const cardWidth = compact ? Math.min(240, (width - (columns - 1) * 8) / columns) : width
    const offsets = [0]
    for (let index = 0; index < items.length; index += columns) {
      let height = 0
      for (const item of items.slice(index, index + columns)) {
        const ratio = item.width > 0 && item.height > 0 ? item.height / item.width : 4 / 3
        height = Math.max(height, (cardWidth - 22) * ratio + 80)
      }
      offsets.push(offsets.at(-1)! + height + (compact ? 8 : 0))
    }
    return {offsets, columns}
  }, [compact, items, viewport.width])

  useLayoutEffect(() => {
    if (!virtual) return
    const list = listRef.current
    if (!list) return
    let frame = 0
    const measure = () => {
      frame = 0
      setViewport(previous => {
        const next = {width: list.clientWidth, height: list.clientHeight, top: list.scrollTop}
        return previous.width === next.width && previous.height === next.height && previous.top === next.top ? previous : next
      })
    }
    const scroll = () => {if (!frame) frame = requestAnimationFrame(measure)}
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(list)
    list.addEventListener('scroll', scroll, {passive: true})
    return () => {
      observer?.disconnect()
      list.removeEventListener('scroll', scroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [listRef, virtual])

  useLayoutEffect(() => {
    if (!virtual || !viewport.width || !viewport.height || !selectedPageId) return
    const index = items.findIndex(item => item.id === selectedPageId)
    const list = listRef.current
    if (!list || index < 0) return
    const row = Math.floor(index / layout.columns)
    const nextRow = Math.floor(Math.min(items.length - 1, index + 1) / layout.columns)
    const top = layout.offsets[row], bottom = layout.offsets[nextRow + 1]
    const key = `${selectedPageId}:${viewport.width}:${viewport.height}:${top}:${bottom}`
    if (positioned.current === key) return
    positioned.current = key
    const available = viewport.height - 20
    const nextTop = bottom - top > available ? top
      : bottom > list.scrollTop + available ? bottom - available
        : top < list.scrollTop ? top : list.scrollTop
    list.scrollTop = Math.max(0, nextTop)
    setViewport(previous => previous.top === list.scrollTop ? previous : {...previous, top: list.scrollTop})
  }, [items, layout, listRef, selectedPageId, viewport.width, viewport.height, virtual])

  const rowCount = layout.offsets.length - 1
  const start = Math.max(0, rowAt(layout.offsets, viewport.top) - OVERSCAN_ROWS)
  const end = Math.min(rowCount, rowAt(layout.offsets, viewport.top + (viewport.height || 600)) + OVERSCAN_ROWS + 1)
  return <div ref={listRef} data-page-list data-virtual={virtual ? 'true' : undefined} className={className} onContextMenu={onContextMenu}>
    {!items.length ? empty : !virtual ? items.map(children) : <div className="virtual-page-space" style={{height: layout.offsets.at(-1)}}>
      {Array.from({length: end - start}, (_, offset) => {
        const row = start + offset
        return <div className="virtual-page-row" key={row} style={{top: layout.offsets[row], gridTemplateColumns: `repeat(${layout.columns},minmax(0,1fr))`}}>
          {items.slice(row * layout.columns, (row + 1) * layout.columns).map((item, column) => children(item, row * layout.columns + column))}
        </div>
      })}
    </div>}
  </div>
}
