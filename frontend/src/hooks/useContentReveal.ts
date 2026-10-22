import {useLayoutEffect, useRef} from 'react'

/** Fade a replaced view without remounting its forms or delaying interaction. */
export function useContentReveal<T extends HTMLElement>(view: string | number, {
  animateOnMount = false, duration, fromOpacity = .45,
}: {animateOnMount?: boolean, duration?: number, fromOpacity?: number} = {}) {
  const ref = useRef<T>(null)
  const previous = useRef<string | number | undefined>(animateOnMount ? undefined : view)
  useLayoutEffect(() => {
    if (previous.current === view) return
    const previousView = previous.current
    previous.current = view
    const element = ref.current
    if (!element?.animate) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (reduced.matches) return
    const tokens = getComputedStyle(element)
    const animation = element.animate([{opacity:fromOpacity}, {opacity:1}], {
      duration: duration ?? (parseFloat(tokens.getPropertyValue('--motion-standard')) || 160),
      easing: tokens.getPropertyValue('--motion-ease').trim() || 'ease-out',
    })
    const cancel = () => animation.cancel()
    reduced.addEventListener('change', cancel)
    return () => {
      cancel()
      reduced.removeEventListener('change', cancel)
      // Restore the setup state so StrictMode's effect replay can reveal entry.
      previous.current = previousView
    }
  }, [view, duration, fromOpacity])
  return ref
}
