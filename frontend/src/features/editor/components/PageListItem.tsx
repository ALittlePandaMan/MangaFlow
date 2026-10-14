import {memo, useEffect, useRef} from 'react'
import type {MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent} from 'react'
import type {ImagePage, ProcessingTask} from '../../../types'
import {cn} from '../../../ui'
import {CircularProgress} from '../../../components/LoadingUI'
import {pageState} from '../lib/pagePresentation'

type ImageFetchPriorityAttributes = {
  // React 18 passes standards-compliant lowercase custom attributes through
  // without the unknown camelCase prop warning emitted for `fetchPriority`.
  fetchpriority: 'high' | 'low'
}

type PageListItemProps = {
  item: ImagePage
  index: number
  selected: boolean
  dragged: boolean
  dropDirection: 'before' | 'after' | null
  activeTask: ProcessingTask | null
  onActivate: (event: ReactMouseEvent<HTMLButtonElement>, pageId: string) => void
  onPointerDown: (event: ReactPointerEvent<HTMLButtonElement>, item: ImagePage, index: number) => void
  onContextMenu: (event: ReactMouseEvent<HTMLButtonElement>, item: ImagePage) => void
}

export const PageListItem = memo(function PageListItem({
  item,
  index,
  selected,
  dragged,
  dropDirection,
  activeTask,
  onActivate,
  onPointerDown,
  onContextMenu,
}: PageListItemProps) {
  const touchPress = useRef<{timer: number, x: number, y: number} | null>(null)
  const suppressTouchClick = useRef(false)
  const cancelTouchPress = () => {
    if (touchPress.current) window.clearTimeout(touchPress.current.timer)
    touchPress.current = null
  }
  useEffect(() => () => {if (touchPress.current) window.clearTimeout(touchPress.current.timer)}, [])
  const state = pageState(item)
  const fetchPriorityAttributes = {
    fetchpriority: selected ? 'high' : 'low',
  } satisfies ImageFetchPriorityAttributes
  return <button
    data-page-id={item.id}
    data-page-index={index}
    aria-haspopup="menu"
    aria-current={selected ? 'page' : undefined}
    className={cn(
      'relative mb-2.5 block w-full cursor-grab touch-pan-y rounded-xl border p-2.5 text-left outline-none transition-[background-color,border-color,opacity,transform,box-shadow,filter] [content-visibility:auto] [contain-intrinsic-size:auto_280px] focus-visible:border-accent/70 focus-visible:ring-2 focus-visible:ring-accent/25 active:cursor-grabbing',
      selected
        ? 'border-accent-line !bg-accent-soft !text-accent-soft-ink hover:!bg-accent-soft-hover'
        : 'border-transparent bg-transparent hover:bg-raised',
      dragged && 'z-10 scale-[1.015] cursor-grabbing bg-raised opacity-70 shadow-panel',
      dropDirection === 'after' && 'after:absolute after:-bottom-1 after:inset-x-1 after:h-0.5 after:rounded-full after:bg-accent',
      dropDirection === 'before' && 'before:absolute before:-top-1 before:inset-x-1 before:h-0.5 before:rounded-full before:bg-accent',
    )}
    onPointerDown={event => {
      cancelTouchPress(); suppressTouchClick.current = false
      onPointerDown(event, item, index)
      if (event.pointerType === 'touch' && event.isPrimary) {
        touchPress.current = {x:event.clientX, y:event.clientY, timer:window.setTimeout(() => {
          touchPress.current = null; suppressTouchClick.current = true
          onContextMenu(event, item)
        }, 500)}
      }
    }}
    onPointerMove={event => {const press = touchPress.current; if (press && Math.hypot(event.clientX-press.x,event.clientY-press.y) > 8) cancelTouchPress()}}
    onPointerUp={cancelTouchPress}
    onPointerCancel={cancelTouchPress}
    onPointerLeave={cancelTouchPress}
    onContextMenu={event => {cancelTouchPress(); onContextMenu(event, item)}}
    onClick={event => {
      if (suppressTouchClick.current) {suppressTouchClick.current = false; event.preventDefault(); return}
      onActivate(event, item.id)
    }}
  >
    <div className={cn('relative flex aspect-[3/4] w-full justify-center overflow-hidden bg-transparent transition-shadow', selected && 'ring-1 ring-inset ring-accent-line')} style={item.width > 0 && item.height > 0 ? {aspectRatio: `${item.width} / ${item.height}`} : undefined}>
      <img
        className="pointer-events-none block size-full select-none object-contain"
        draggable={false}
        width={item.width}
        height={item.height}
        src={item.thumbnail_url || item.rendered_url || item.original_url}
        srcSet={item.thumbnail_url && item.thumbnail_2x_url ? `${item.thumbnail_url} 1x, ${item.thumbnail_2x_url} 2x` : undefined}
        onError={event => {
          const fallback = item.rendered_url || item.original_url
          event.currentTarget.removeAttribute('srcset')
          if (event.currentTarget.getAttribute('src') !== fallback) event.currentTarget.src = fallback
        }}
        alt={item.filename}
        loading={selected ? 'eager' : 'lazy'}
        decoding="async"
        {...fetchPriorityAttributes}
      />
      <span className={cn('absolute bottom-1 left-1 bg-canvas px-1 py-0.5 font-mono text-[12px]', selected && 'text-accent')}>{String(index + 1).padStart(2,'0')}</span>
      {activeTask && <span className="absolute right-1 top-1 grid rounded-full bg-canvas/90 p-1 shadow-panel"><CircularProgress value={activeTask.progress} size={34} label={`${item.filename} 处理进度`}/></span>}
    </div>
    <small className={cn('mt-2 block truncate text-[12px]', selected ? 'font-semibold text-accent-soft-ink' : 'text-secondary')} title={item.filename}>{item.filename}</small>
    <i className={cn('mt-1 block truncate font-mono text-[12px] not-italic', state.emphasized && 'font-semibold', selected ? 'text-accent-soft-ink' : state.className)}>{state.label}</i>
  </button>
})
