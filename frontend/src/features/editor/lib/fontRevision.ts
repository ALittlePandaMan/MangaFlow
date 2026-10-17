import {useSyncExternalStore} from 'react'

let revision = 0
const listeners = new Set<() => void>()
const snapshot = () => revision
export function notifyFontsChanged() {
  revision += 1
  for (const listener of listeners) listener()
}
function subscribe(listener: () => void) {
  if (!listeners.size) document.fonts?.addEventListener('loadingdone', notifyFontsChanged)
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (!listeners.size) document.fonts?.removeEventListener('loadingdone', notifyFontsChanged)
  }
}
// One shared font listener for the page, not a font-loading effect per glyph.
export function useFontRevision() {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}
