import {useSyncExternalStore} from 'react'

const query = '(max-width: 1099px)'
function subscribe(callback: () => void) {
  const media = window.matchMedia?.(query)
  media?.addEventListener('change', callback)
  return () => media?.removeEventListener('change', callback)
}
const snapshot = () => window.matchMedia?.(query).matches ?? false

export function useCompactLayout() {
  return useSyncExternalStore(subscribe, snapshot, () => false)
}
