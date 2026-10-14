import {useCallback, useSyncExternalStore} from 'react'

const serverSnapshot = () => false

/** Side panels and header actions have different space requirements. */
export function useCompactLayout(desktopWidth = 960) {
  const query = `(width < ${desktopWidth}px)`
  const subscribe = useCallback((callback: () => void) => {
    const media = window.matchMedia?.(query)
    media?.addEventListener('change', callback)
    return () => media?.removeEventListener('change', callback)
  }, [query])
  const snapshot = useCallback(() => window.matchMedia?.(query).matches ?? false, [query])
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot)
}
