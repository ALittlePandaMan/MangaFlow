import {useLayoutEffect, useRef, type RefObject} from 'react'

/** Reveal the active thumbnail and the following page once per navigation. */
export function useSelectedPageScroll(listRef: RefObject<HTMLDivElement>, pageId: string | undefined, ready: boolean) {
  const positioned = useRef<{list: HTMLDivElement, pageId: string, width: number, height: number} | null>(null)
  useLayoutEffect(() => {
    const list = listRef.current
    if (!ready || !list || !pageId) return
    const reveal = () => {
      // Hidden responsive panels have no scroll viewport. Wait until the
      // page sheet opens instead of consuming the initial positioning early.
      const width = list.clientWidth, height = list.clientHeight
      if (!width || !height) return false
      if (positioned.current?.list === list && positioned.current.pageId === pageId
        && positioned.current.width === width && positioned.current.height === height) return true
      const active = list.querySelector<HTMLElement>('[data-page-id][aria-current="page"]')
      if (!active || active.dataset.pageId !== pageId) return false
      active.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'})
      const next = active.nextElementSibling
      if (next instanceof HTMLElement && next.hasAttribute('data-page-id')) {
        // Keep a one-page lookahead when advancing with the keyboard. Only
        // scroll this list, and never chase task/progress updates or wheel input.
        const style = getComputedStyle(list)
        const paddingTop = parseFloat(style.paddingTop) || 0
        const paddingBottom = parseFloat(style.paddingBottom) || 0
        const top = list.getBoundingClientRect().top + list.clientTop + paddingTop
        const bottom = top + list.clientHeight - paddingTop - paddingBottom
        const activeBounds = active.getBoundingClientRect()
        const nextBounds = next.getBoundingClientRect()
        const pairBottom = Math.max(activeBounds.bottom, nextBounds.bottom)
        // On short screens two complete thumbnails may not fit. Keep the
        // current page at the top and expose as much of the next as possible.
        const delta = pairBottom - activeBounds.top > bottom - top
          ? activeBounds.top - top
          : pairBottom > bottom ? pairBottom - bottom
            : activeBounds.top < top ? activeBounds.top - top : 0
        if (delta) list.scrollTo({top: list.scrollTop + delta, behavior: 'instant'})
      }
      positioned.current = {list, pageId, width, height}
      return true
    }
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(reveal)
    reveal()
    // A responsive grid has different row offsets than the desktop list.
    // Reposition on viewport resize, never on an ordinary scroll or rerender.
    observer?.observe(list)
    return () => observer?.disconnect()
  }, [listRef, pageId, ready])
}
