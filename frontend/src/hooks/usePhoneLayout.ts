import {useSyncExternalStore} from 'react'

const query = '(max-width: 639px)'
function subscribe(callback: () => void) {
  const media = window.matchMedia?.(query)
  media?.addEventListener('change', callback)
  return () => media?.removeEventListener('change', callback)
}
const snapshot = () => window.matchMedia?.(query).matches ?? false

export function usePhoneLayout() {
  return useSyncExternalStore(subscribe, snapshot, () => false)
}
