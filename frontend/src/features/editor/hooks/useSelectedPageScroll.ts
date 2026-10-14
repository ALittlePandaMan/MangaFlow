import {useLayoutEffect, useRef, type RefObject} from 'react'

/** Reveal the active thumbnail once per navigation, including a hidden mobile sheet. */
export function useSelectedPageScroll(listRef: RefObject<HTMLDivElement>, pageId: string | undefined, ready: boolean) {
  const positioned = useRef<{list: HTMLDivElement, pageId: string} | null>(null)
  useLayoutEffect(() => {
    const list = listRef.current
    if (!ready || !list || !pageId) return
    const reveal = () => {
      if (positioned.current?.list === list && positioned.current.pageId === pageId) return true
      // Hidden responsive panels have no scroll viewport. Wait until the
      // page sheet opens instead of consuming the initial positioning early.
      if (!list.clientWidth || !list.clientHeight) return false
      const active = list.querySelector<HTMLElement>('[data-page-id][aria-current="page"]')
      if (!active || active.dataset.pageId !== pageId) return false
      active.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'})
      positioned.current = {list, pageId}
      observer?.disconnect()
      return true
    }
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(reveal)
    if (reveal()) return
    observer?.observe(list)
    return () => observer?.disconnect()
  }, [listRef, pageId, ready])
}
