import {useLayoutEffect, useRef, type RefObject} from 'react'

const layers: HTMLElement[] = []
const inertLocks = new Map<Element, {count:number, previous:string | null}>()
let scrollLocks = 0
let previousOverflow = ''
const focusableSelector = 'button, input:not([type="hidden"]), textarea, select, a[href], [tabindex]'

/** Shared modal isolation, including popovers rendered outside the dialog. */
export function useModalFocus(ref: RefObject<HTMLElement>, {enabled = true, focusKey, initialFocus, onEscape}: {
  enabled?: boolean
  focusKey?: number
  initialFocus?: () => HTMLElement | null | undefined
  onEscape?: () => void
}) {
  const callbacks = useRef({initialFocus, onEscape})
  callbacks.current = {initialFocus, onEscape}
  useLayoutEffect(() => {
    const dialog = ref.current
    if (!enabled || !dialog) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const background = new Set<Element>()
    layers.push(dialog)
    // Two portals can mount in the same commit. A lower layer may have
    // already isolated this portal before its own layout effect runs.
    for (let ancestor: Element | null = dialog; ancestor && ancestor !== document.body; ancestor = ancestor.parentElement) {
      if (inertLocks.has(ancestor)) ancestor.removeAttribute('inert')
    }
    const isTop = () => layers.at(-1) === dialog
    const portals = () => [...document.querySelectorAll<HTMLElement>('[data-modal-owner]')].filter(node => node.dataset.modalOwner === dialog.id)
    const modalRoots = () => [dialog, ...portals()]
    const roots = () => [...modalRoots(), ...document.querySelectorAll<HTMLElement>('.global-alerts')]
    const controls = () => modalRoots().flatMap(root => [...root.querySelectorAll<HTMLElement>(focusableSelector)])
      .filter(node => node.tabIndex >= 0 && !node.matches(':disabled') && !node.closest('[inert], [hidden], [aria-hidden="true"]')
        && getComputedStyle(node).display !== 'none' && getComputedStyle(node).visibility !== 'hidden')
    const focus = () => (callbacks.current.initialFocus?.() || controls()[0] || dialog).focus({preventScroll:true})
    const isolate = () => {
      if (!isTop()) return
      let branch: Element = dialog
      while (branch.parentElement) {
        for (const sibling of branch.parentElement.children) {
          if (sibling === branch || sibling.matches('.global-alerts') || (sibling instanceof HTMLElement && sibling.dataset.modalOwner === dialog.id)) continue
          if (!background.has(sibling)) {
            background.add(sibling)
            const lock = inertLocks.get(sibling)
            if (lock) lock.count++
            else inertLocks.set(sibling, {count:1, previous:sibling.getAttribute('inert')})
            sibling.setAttribute('inert', '')
          }
        }
        if (branch.parentElement === document.body) break
        branch = branch.parentElement
      }
    }
    const keydown = (event: KeyboardEvent) => {
      if (!isTop() || event.defaultPrevented) return
      if (event.key === 'Escape') {
        if (event.isComposing) return
        event.preventDefault()
        callbacks.current.onEscape?.()
      }
      if (event.key !== 'Tab') return
      const items = controls()
      const first = items[0], last = items.at(-1)
      const inside = items.includes(document.activeElement as HTMLElement)
      if (!first || !last) {event.preventDefault(); dialog.focus(); return}
      if (event.shiftKey && (!inside || document.activeElement === first)) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && (!inside || document.activeElement === last)) {
        event.preventDefault(); first.focus()
      }
    }
    const focusin = (event: FocusEvent) => {
      if (isTop() && !roots().some(root => root.contains(event.target as Node))) focus()
    }
    isolate()
    if (scrollLocks++ === 0) previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const observer = new MutationObserver(isolate)
    observer.observe(document.body, {childList:true})
    document.addEventListener('keydown', keydown)
    document.addEventListener('focusin', focusin)
    return () => {
      observer.disconnect()
      document.removeEventListener('keydown', keydown)
      document.removeEventListener('focusin', focusin)
      const restoreFocus = isTop()
      layers.splice(layers.indexOf(dialog), 1)
      if (--scrollLocks === 0) document.body.style.overflow = previousOverflow
      background.forEach(node => {
        const lock = inertLocks.get(node)!
        if (--lock.count > 0) return
        if (lock.previous === null) node.removeAttribute('inert')
        else node.setAttribute('inert', lock.previous)
        inertLocks.delete(node)
      })
      if (restoreFocus && previous?.isConnected) previous.focus({preventScroll:true})
    }
  }, [enabled, ref])

  useLayoutEffect(() => {
    if (!enabled) return
    const dialog = ref.current
    if (!dialog || layers.at(-1) !== dialog) return
    const target = callbacks.current.initialFocus?.() || dialog.querySelector<HTMLElement>(focusableSelector) || dialog
    target.focus({preventScroll:true})
  }, [enabled, focusKey, ref])
}
